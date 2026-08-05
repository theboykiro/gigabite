"""Materializing indexed documents into readable files, and intake without a drop box.

The load-bearing property here is that a conversation which exists both as a raw
import and as a readable file is **one** document, not two. Everything else in this
module is in service of that: if it ever indexes twice, every search silently
returns each conversation a second time, which is the kind of failure that looks
like a ranking problem for months.

Written against a temp knowledge base, never the real one.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="gigabite-mat-")
os.environ.setdefault("GIGABITE_CORE_DIR", str(Path(_TMP) / "core"))
os.environ.setdefault("GIGABITE_KNOWLEDGE_DIR", str(Path(_TMP) / "knowledge"))

from gigabite import config  # noqa: E402
from gigabite.features import intake, materialize, save  # noqa: E402
from gigabite.sources import notes  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402


def _chat(native_id="c1", title="Loyalty pricing chat", project="",
          created="2026-07-02T10:00:00+00:00", source=None):
    """A claude.ai-shaped document that exists only in the index."""
    return Document(
        source=source or config.SOURCE_CLAUDE_AI,
        native_id=native_id,
        title=title,
        project=project,
        created_utc=created,
        updated_utc=created,
        ref=str(config.SOURCES_CLAUDE_AI / "conversations.json"),
        messages=[
            Message(seq=0, role="user", text="What did we decide about widget pricing?",
                    ts_utc=created),
            Message(seq=1, role="assistant",
                    text="The widget anchor was set at the kickoff and never revisited.",
                    ts_utc=created),
        ],
    )


class _Base(unittest.TestCase):
    """Each test gets its own knowledge base and its own index."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.knowledge = Path(self.tmp.name) / "knowledge"
        self.knowledge.mkdir(parents=True)

        self._orig = config.KNOWLEDGE_DIR
        config.KNOWLEDGE_DIR = self.knowledge
        # The machinery constants are bound at import; re-point the ones this
        # module's code paths read so nothing reaches the real store.
        self._orig_machine = (config.MACHINE_DIR, config.SOURCES_DIR,
                              config.SOURCES_CLAUDE_AI, config.ORIGINALS_DIR)
        config.MACHINE_DIR = config.machine_dir()
        config.SOURCES_DIR = config.MACHINE_DIR / "imports"
        config.SOURCES_CLAUDE_AI = config.SOURCES_DIR / "claude_ai"
        config.ORIGINALS_DIR = config.MACHINE_DIR / "originals"
        self.addCleanup(self._restore)

        save.ensure_project("acme", keywords=["widget", "acme"])
        self.db = Path(self.tmp.name) / "index.db"
        self.store = Store(connect(self.db))

    def _restore(self):
        config.KNOWLEDGE_DIR = self._orig
        (config.MACHINE_DIR, config.SOURCES_DIR,
         config.SOURCES_CLAUDE_AI, config.ORIGINALS_DIR) = self._orig_machine

    def fresh_store(self) -> Store:
        """A second Store on the same db, as a later `gigabite ingest` would open."""
        return Store(connect(self.db))

    def files(self, pattern="*.md"):
        return sorted(p.relative_to(self.knowledge).as_posix()
                      for p in self.knowledge.rglob(pattern)
                      if config.MACHINE_DIRNAME not in p.parts)


class TestNoDoubleIndexing(_Base):
    """The whole point: one conversation, one document, however many files."""

    def test_materialized_chat_is_not_indexed_a_second_time(self):
        self.store.upsert_document(_chat())
        self.store.commit()
        before = len(self.store.iter_documents())

        materialize.run(self.store, retire=False)
        notes.ingest(self.fresh_store(), root=self.knowledge)

        after = len(self.fresh_store().iter_documents())
        self.assertEqual(after, before,
                         "materializing must not add a document to the index")

    def test_search_returns_one_hit_not_two(self):
        self.store.upsert_document(_chat())
        self.store.commit()
        materialize.run(self.store, retire=False)
        notes.ingest(self.fresh_store(), root=self.knowledge)

        hits = self.fresh_store().search("widget anchor kickoff")
        doc_ids = {h["doc_id"] for h in hits}
        self.assertEqual(len(doc_ids), 1,
                         f"a materialized chat surfaced as {len(doc_ids)} documents")

    def test_the_written_file_declares_the_document_it_renders(self):
        doc = _chat()
        self.store.upsert_document(doc)
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        written = plan.actionable[0]
        body = written.path.read_text(encoding="utf-8")
        self.assertIn(f"doc_id: {doc.doc_id}", body)
        self.assertIn(f"source: {config.SOURCE_CLAUDE_AI}", body)

    def test_a_note_without_a_doc_id_is_still_its_own_document(self):
        """The guard must not swallow ordinary notes."""
        save.save_note("An ordinary written note about widget.", project="acme",
                       title="Ordinary", ts="2026-07-03")
        rep = notes.ingest(self.store, root=self.knowledge)
        self.assertEqual(rep.changed, 1)

    def test_an_orphaned_rendering_carries_its_own_content(self):
        """If the raw import is gone, the readable file *is* the document."""
        doc = _chat()
        self.store.upsert_document(doc)
        self.store.commit()
        materialize.run(self.store, retire=False)

        # Rebuild the index from files alone, as `reindex` would after the export
        # was deleted: nothing else claims this doc_id.
        fresh_db = Path(self.tmp.name) / "rebuilt.db"
        rebuilt = Store(connect(fresh_db))
        notes.ingest(rebuilt, root=self.knowledge)

        found = rebuilt.get_document(doc.doc_id)
        self.assertIsNotNone(found, "the rendering must adopt the document id")
        self.assertEqual(found["source"], config.SOURCE_CLAUDE_AI,
                         "it should still report which source it came from")
        self.assertTrue(rebuilt.search("widget anchor"))

    def test_editing_a_materialized_file_still_reaches_the_index(self):
        doc = _chat()
        self.store.upsert_document(doc)
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        path = plan.actionable[0].path

        # The export is retired, so the file owns the document from here on.
        self.store.set_document_ref(doc.doc_id, str(path))
        path.write_text(path.read_text(encoding="utf-8") + "\nA later addition: quibble.\n",
                        encoding="utf-8")
        notes.ingest(self.store, root=self.knowledge, force=True)
        self.assertTrue(self.fresh_store().search("quibble"))


class TestIdempotency(_Base):
    def test_second_run_writes_nothing(self):
        self.store.upsert_document(_chat())
        self.store.commit()
        first, _ = materialize.run(self.store, retire=False)
        self.assertEqual(len(first.actionable), 1)
        files_after_first = self.files()

        second, _ = materialize.run(self.store, retire=False)
        self.assertEqual(second.actionable, [], "a second run must do nothing")
        self.assertEqual(self.files(), files_after_first)

    def test_idempotent_even_after_the_index_is_rebuilt(self):
        """The files are the record, so a fresh index must not re-materialize."""
        self.store.upsert_document(_chat())
        self.store.commit()
        materialize.run(self.store, retire=False)
        count = len(self.files())

        rebuilt = Store(connect(Path(self.tmp.name) / "rebuilt.db"))
        rebuilt.upsert_document(_chat())
        rebuilt.commit()
        plan, _ = materialize.run(rebuilt, retire=False)
        self.assertEqual(plan.actionable, [])
        self.assertEqual(len(self.files()), count)

    def test_a_document_that_already_lives_in_the_store_is_never_rewritten(self):
        """Ownership is settled by where the file is, not by the source label.

        A document adopted from a materialized rendering keeps reporting its
        original source, so the `source != note` test alone would let it be
        materialized a second time.
        """
        doc = _chat()
        self.store.upsert_document(doc)
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        path = plan.actionable[0].path
        self.store.set_document_ref(doc.doc_id, str(path))

        # Even with the on-disk stamps ignored, the ref alone must stop it.
        row = next(d for d in self.store.iter_documents() if d["doc_id"] == doc.doc_id)
        self.assertTrue(materialize._is_already_a_file(row))
        second, _ = materialize.run(self.store, retire=False)
        self.assertEqual(second.actionable, [])

    def test_a_raw_import_is_not_mistaken_for_a_stored_file(self):
        path = config.SOURCES_DIR / "granola" / "m9.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("widget notes", encoding="utf-8")
        self.assertFalse(materialize._is_already_a_file({"ref": str(path)}),
                         "a file inside .gigabite/ is machinery, not stored content")

    def test_rendering_is_deterministic(self):
        doc = _chat()
        self.store.upsert_document(doc)
        self.store.commit()
        full = self.store.get_document(doc.doc_id)
        self.assertEqual(materialize.render(full), materialize.render(full))

    def test_dry_run_writes_nothing(self):
        self.store.upsert_document(_chat())
        self.store.commit()
        before = self.files()
        plan, retired = materialize.run(self.store, dry_run=True)
        self.assertEqual(len(plan.actionable), 1, "it should still report the work")
        self.assertEqual(self.files(), before)
        self.assertIsNone(plan.actionable[0].path)


class TestProjectIsNeverGuessed(_Base):
    def test_recorded_project_is_used_when_the_folder_exists(self):
        self.store.upsert_document(_chat(project="acme"))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        item = plan.actionable[0]
        self.assertEqual(item.project, "acme")
        self.assertFalse(item.triaged)
        self.assertTrue(str(item.path).startswith(str(self.knowledge / "acme")))

    def test_a_stale_project_label_does_not_create_a_folder(self):
        self.store.upsert_document(_chat(project="ghostco", title="Nothing familiar",
                                         native_id="c-stale"))
        self.store.commit()
        materialize.run(self.store, retire=False)
        self.assertFalse((self.knowledge / "ghostco").exists())

    def test_keyword_routing_places_an_unlabelled_chat(self):
        # 'widget' is an acme keyword and appears in the body.
        self.store.upsert_document(_chat(project=""))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable[0].project, "acme")

    def _vague(self, native_id="c-vague", title="Assorted thoughts"):
        self.store.upsert_document(Document(
            source=config.SOURCE_CLAUDE_AI, native_id=native_id, title=title,
            created_utc="2026-07-04T09:00:00+00:00",
            messages=[Message(seq=0, role="user", text="Nothing identifying here.")],
        ))
        self.store.commit()

    def test_an_unroutable_chat_is_skipped_rather_than_placed(self):
        self._vague()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable, [], "it must not be written anywhere")
        self.assertEqual(plan.skipped[0].skip, materialize.UNRESOLVED)

    def test_include_unfiled_files_it_under_personal(self):
        self._vague()
        plan, _ = materialize.run(self.store, retire=False, include_unfiled=True)
        item = plan.actionable[0]
        self.assertTrue(item.triaged, "it is still a fallback, not a routed project")
        self.assertEqual(item.project, config.PERSONAL_PROJECT)
        self.assertIn("personal/conversations/", item.path.as_posix())

    def test_include_unfiled_leaves_the_knowledge_root_a_list_of_projects(self):
        self._vague()
        materialize.run(self.store, retire=False, include_unfiled=True)
        loose = [q.name for q in self.knowledge.iterdir()
                 if q.is_file() and q.suffix == ".md"]
        self.assertEqual(loose, [], "nothing may be left loose at the root")
        visible_dirs = {q.name for q in self.knowledge.iterdir()
                        if q.is_dir() and not q.name.startswith(".")}
        self.assertEqual(visible_dirs, {"acme", config.PERSONAL_PROJECT})

    def test_personal_attracts_nothing_by_keyword(self):
        """The folder must not become the drawer everything ambiguous falls into."""
        from gigabite.features import routing
        self._vague()
        materialize.run(self.store, retire=False, include_unfiled=True)
        personal = next(p for p in routing._scan_projects()
                        if p["name"] == config.PERSONAL_PROJECT)
        self.assertEqual(personal["keywords"], [],
                         "keywords here would hijack routing for real projects")
        ctx = routing.resolve_context("some personal thoughts about nothing")
        self.assertIsNone(ctx["project"])

    def test_a_stray_handle_in_a_transcript_cannot_invent_a_project(self):
        """A real defect: '@leonardo' in a chat about sunglasses made a project."""
        self.store.upsert_document(Document(
            source=config.SOURCE_CLAUDE_AI, native_id="c-handles",
            title="Sunglasses side temples explained",
            created_utc="2026-07-14T09:00:00+00:00",
            messages=[Message(seq=0, role="user",
                              text="I saw them on @leonardo and @joyoptics.")],
        ))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable, [])
        self.assertFalse((self.knowledge / "leonardo").exists(),
                         "a handle in the text must not become a project folder")

    def test_a_shell_prompt_in_a_transcript_cannot_invent_a_project(self):
        self.store.upsert_document(Document(
            source=config.SOURCE_CLAUDE_CODE, native_id="cc-host",
            title="Laptop fan troubleshooting",
            created_utc="2026-07-06T09:00:00+00:00",
            messages=[Message(seq=0, role="user",
                              text="jane@Janes-MacBook-Pro ~ % sensors")],
        ))
        self.store.commit()
        materialize.run(self.store, retire=False)
        self.assertFalse((self.knowledge / "janes-macbook-pro").exists())

    def test_an_email_address_is_not_a_project_marker(self):
        """A real defect: 'jane.doe@acme.com' filed a chat under acme."""
        from gigabite.features import routing
        ctx = routing.resolve_context("reply to jane.doe@acme.com when you can")
        self.assertNotEqual(ctx["confidence"], "explicit",
                            "an email domain must not read as @project")

    def test_a_shell_prompt_host_is_not_a_project_marker(self):
        from gigabite.features import routing
        ctx = routing.resolve_context("jane@Janes-MacBook-Pro ~ % ls")
        self.assertNotEqual(ctx["confidence"], "explicit")

    def test_a_marker_for_a_project_that_exists_is_still_honoured(self):
        self.store.upsert_document(Document(
            source=config.SOURCE_CLAUDE_AI, native_id="c-marked",
            title="Marked chat", created_utc="2026-07-14T09:00:00+00:00",
            messages=[Message(seq=0, role="user", text="Filing this under @acme.")],
        ))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable[0].project, "acme")

    def test_a_typed_prompt_may_still_name_a_brand_new_project(self):
        """The marker rule only tightens for documents, not for what you type."""
        from gigabite.features import routing
        ctx = routing.resolve_context("@brandnew let's start this")
        self.assertEqual(ctx["project"], "brandnew")

    def test_routing_over_the_whole_document_finds_a_late_mention(self):
        """A transcript can name its client well past the old 2,000-char probe."""
        filler = "General discussion of the agenda. " * 200
        self.store.upsert_document(Document(
            source=config.SOURCE_GRANOLA, native_id="m-late.md",
            title="Weekly catch up", created_utc="2026-07-20T09:00:00+00:00",
            messages=[Message(seq=0, role="note",
                              text=filler + " Finally: the widget rollout.")],
        ))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable[0].project, "acme")

    def test_a_forced_project_places_what_routing_could_not(self):
        self._vague()
        plan, _ = materialize.run(self.store, project="acme", retire=False)
        item = plan.actionable[0]
        self.assertFalse(item.triaged)
        self.assertEqual(item.project, "acme")
        self.assertIn("acme/conversations/", item.path.as_posix())

    def test_an_unfiled_file_records_personal_as_its_project(self):
        self.store.upsert_document(Document(
            source=config.SOURCE_CLAUDE_AI, native_id="c-vague2",
            title="Assorted thoughts two", created_utc="2026-07-04T09:00:00+00:00",
            messages=[Message(seq=0, role="user", text="Still nothing identifying.")],
        ))
        self.store.commit()
        materialize.run(self.store, retire=False, include_unfiled=True)
        notes.ingest(self.fresh_store(), root=self.knowledge)
        hit = next(h for h in self.fresh_store().search("identifying"))
        self.assertEqual(hit["project"] or "", config.PERSONAL_PROJECT,
                         "it is filed under personal, and says so in the index")


class TestLayers(_Base):
    def test_meetings_join_the_existing_meetings_layer(self):
        self.store.upsert_document(Document(
            source=config.SOURCE_GRANOLA, native_id="m1.md", title="Widget sync",
            project="acme", created_utc="2026-07-05T09:00:00+00:00",
            ref=str(config.SOURCES_DIR / "granola" / "m1.md"),
            messages=[Message(seq=0, role="note", text="Widget rollout discussed.")],
        ))
        self.store.commit()
        plan, _ = materialize.run(self.store, retire=False)
        self.assertEqual(plan.actionable[0].layer, "meetings")
        self.assertIn("acme/meetings/", plan.actionable[0].path.as_posix())

    def test_layer_can_be_overridden(self):
        self.store.upsert_document(_chat(project="acme"))
        self.store.commit()
        plan, _ = materialize.run(self.store, layer="transcripts", retire=False)
        self.assertEqual(plan.actionable[0].layer, "transcripts")


class TestRetiringRawImports(_Base):
    def _granola_export(self, name="m1.md", body="Widget rollout discussed at length."):
        path = config.SOURCES_DIR / "granola" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: Widget sync\ndate: 2026-07-05\n---\n\n{body}\n",
                        encoding="utf-8")
        doc = Document(
            source=config.SOURCE_GRANOLA, native_id=name, title="Widget sync",
            project="acme", created_utc="2026-07-05T09:00:00+00:00", ref=str(path),
            messages=[Message(seq=0, role="note", text=body)],
        )
        self.store.upsert_document(doc)
        self.store.commit()
        return path, doc

    def test_original_is_moved_not_deleted(self):
        path, doc = self._granola_export()
        materialize.run(self.store)
        self.assertFalse(path.exists(), "it should no longer be a raw import")
        kept = config.ORIGINALS_DIR / "imports" / "granola" / "m1.md"
        self.assertTrue(kept.exists(), "the original must be preserved")

    def test_the_document_is_repointed_at_the_readable_file(self):
        _path, doc = self._granola_export()
        plan, _ = materialize.run(self.store)
        self.assertEqual(self.store.document_ref(doc.doc_id),
                         str(plan.actionable[0].path))

    def test_retiring_leaves_one_document_and_it_is_searchable(self):
        _path, doc = self._granola_export()
        materialize.run(self.store)
        notes.ingest(self.fresh_store(), root=self.knowledge)
        st = self.fresh_store()
        self.assertEqual(len(st.iter_documents()), 1)
        hits = st.search("widget rollout")
        self.assertEqual(len({h["doc_id"] for h in hits}), 1)

    def test_a_shared_import_is_left_alone(self):
        """conversations.json backs many documents; retiring it would lose them."""
        shared = config.SOURCES_CLAUDE_AI / "export.md"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text("two chats in one file", encoding="utf-8")
        for n in ("a", "b"):
            doc = _chat(native_id=n, project="acme")
            doc.ref = str(shared)
            self.store.upsert_document(doc)
        self.store.commit()
        materialize.run(self.store)
        self.assertTrue(shared.exists(), "a multi-document import must stay")

    def test_keep_sources_leaves_everything_in_place(self):
        path, _doc = self._granola_export()
        materialize.run(self.store, retire=False)
        self.assertTrue(path.exists())


class TestBinariesAreKept(_Base):
    def _png(self, name="screenshot.png") -> Path:
        src = Path(self.tmp.name) / name
        # A minimal real PNG header — not text, and not decodable as UTF-8.
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\xff" * 64)
        return src

    def test_a_screenshot_is_stored_not_refused(self):
        path, project, unfiled = intake.place_file(self._png("acme-widget-chart.png"))
        self.assertTrue(path.exists())
        self.assertEqual(project, "acme")
        self.assertFalse(unfiled)

    def test_the_original_is_copied_by_default(self):
        src = self._png("acme-widget-chart.png")
        intake.place_file(src)
        self.assertTrue(src.exists(), "content from outside must be copied, not moved")

    def test_a_stored_binary_is_indexed_by_its_metadata(self):
        intake.place_file(self._png("acme-widget-chart.png"))
        notes.ingest(self.store, root=self.knowledge)
        hits = self.store.search("widget chart")
        self.assertTrue(hits, "a screenshot must be findable by its filename")
        doc = self.store.get_document(hits[0]["doc_id"])
        body = doc["messages"][0]["text"]
        self.assertIn("contents have not been read", body)
        self.assertIn(".png", body)

    def test_nothing_is_invented_about_the_image(self):
        intake.place_file(self._png("acme-widget-chart.png"))
        notes.ingest(self.store, root=self.knowledge)
        hits = self.store.search("widget chart")
        body = self.store.get_document(hits[0]["doc_id"])["messages"][0]["text"]
        # Only facts: name, type, size, date, path, and the disclaimer.
        for claim in ("shows", "depicts", "appears to", "image of"):
            self.assertNotIn(claim, body.lower())

    def test_an_unroutable_binary_is_still_kept(self):
        path, _project, unfiled = intake.place_file(self._png("img-8842.png"))
        self.assertTrue(unfiled)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.knowledge)

    def test_a_binary_never_overwrites_one_already_there(self):
        first, _p, _t = intake.place_file(self._png("acme-widget-chart.png"))
        second, _p, _t = intake.place_file(self._png("acme-widget-chart.png"))
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_a_companion_note_keeps_its_attachment_reference(self):
        img, _p, _t = intake.place_file(self._png("acme-widget-chart.png"))
        save.save_note("Screenshot pasted during the widget review.", project="acme",
                       title="Widget chart screenshot", ts="2026-07-27",
                       meta={"attachment": img.name})
        notes.ingest(self.store, root=self.knowledge)
        hits = self.store.search("screenshot pasted widget review")
        doc = self.store.get_document(hits[0]["doc_id"])
        extra = json.loads(doc["extra_json"])
        self.assertEqual(extra.get("attachment"), img.name)


class TestIntakeWithoutADropBox(_Base):
    def test_pasted_text_lands_in_the_project_folder_directly(self):
        path, project, unfiled = intake.place_text(
            "Widget pricing was agreed.", title="Widget pricing", day="2026-07-06")
        self.assertEqual(project, "acme")
        self.assertFalse(unfiled)
        self.assertEqual(path.parent, self.knowledge / "acme")

    def test_unroutable_text_is_not_given_a_plausible_project(self):
        path, project, unfiled = intake.place_text(
            "Nothing identifying at all.", title="Loose thought", day="2026-07-06")
        self.assertTrue(unfiled)
        self.assertEqual(project, config.UNFILED_PROJECT)
        self.assertEqual(path.parent, self.knowledge)
        self.assertIn("project: \n", path.read_text(encoding="utf-8"))

    def test_a_forced_project_wins(self):
        path, project, _u = intake.place_text(
            "Nothing identifying at all.", title="Loose thought", day="2026-07-06",
            project="acme", layer="delivery")
        self.assertEqual(project, "acme")
        self.assertEqual(path.parent, self.knowledge / "acme" / "delivery")

    def test_provenance_is_recorded(self):
        path, _p, _u = intake.place_text(
            "Widget pricing was agreed.", title="Widget pricing", day="2026-07-06",
            source=config.SOURCE_GRANOLA, origin="pasted from the clipboard")
        body = path.read_text(encoding="utf-8")
        self.assertIn("origin: pasted from the clipboard", body)
        self.assertIn("share: private", body)
        self.assertIn(f"source: {config.SOURCE_GRANOLA}", body)

    def test_placed_text_is_indexed_where_it_was_put(self):
        intake.place_text("Widget pricing was agreed at the review.",
                          title="Widget pricing", day="2026-07-06")
        notes.ingest(self.store, root=self.knowledge)
        hits = self.store.search("widget pricing agreed")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project"], "acme")

    def test_moving_a_file_moves_its_project(self):
        """The folder is the metadata: no re-filing step, just a drag."""
        path, _p, _u = intake.place_text("Nothing identifying at all.",
                                         title="Loose thought", day="2026-07-06")
        notes.ingest(self.store, root=self.knowledge)
        moved = self.knowledge / "acme" / path.name
        path.rename(moved)
        notes.ingest(self.fresh_store(), root=self.knowledge)
        hits = self.fresh_store().search("nothing identifying")
        self.assertTrue(hits)
        self.assertIn("acme", {h["project"] for h in hits})


class TestExtraction(_Base):
    def test_vtt_cues_are_stripped(self):
        src = Path(self.tmp.name) / "acme-call.vtt"
        src.write_text("WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n"
                       "Widget rollout is on track.\n", encoding="utf-8")
        text, _meta, kind = intake.extract(src)
        self.assertEqual(kind, intake.TEXT)
        self.assertEqual(text, "Widget rollout is on track.")

    def test_json_is_rendered_as_readable_lines(self):
        src = Path(self.tmp.name) / "acme.json"
        src.write_text('{"topic": "widget", "owner": {"name": "K"}}', encoding="utf-8")
        text, _meta, kind = intake.extract(src)
        self.assertEqual(kind, intake.TEXT)
        self.assertIn("topic: widget", text)
        self.assertIn("owner.name: K", text)

    def test_an_unreadable_file_is_binary_not_an_error(self):
        src = Path(self.tmp.name) / "thing.sketch"
        src.write_bytes(b"\x00\x01binary")
        text, _meta, kind = intake.extract(src)
        self.assertEqual(kind, intake.BINARY)
        self.assertEqual(text, "")

    def test_an_empty_text_file_is_kept_as_a_file(self):
        src = Path(self.tmp.name) / "acme-empty.md"
        src.write_text("", encoding="utf-8")
        _text, _meta, kind = intake.extract(src)
        self.assertEqual(kind, intake.BINARY)
        path, _p, _u = intake.place_file(src)
        self.assertTrue(path.exists(), "an empty file is still the user's file")


class TestCliCommands(_Base):
    """Every new command runs end to end, against the temp store."""

    def _args(self, **kw):
        import argparse
        return argparse.Namespace(**kw)

    def setUp(self):
        super().setUp()
        from gigabite import cli
        self.cli = cli
        # cli opens the real db via config.DB_PATH; point it at ours.
        self._orig_db = (config.DB_PATH, config.INDEX_DIR)
        config.INDEX_DIR = config.MACHINE_DIR / "index"
        config.DB_PATH = self.db
        self.addCleanup(self._restore_db)

    def _restore_db(self):
        config.DB_PATH, config.INDEX_DIR = self._orig_db

    def test_add_stores_a_screenshot(self):
        src = Path(self.tmp.name) / "acme-widget-board.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\xff" * 32)
        rc = self.cli.cmd_add(self._args(path=str(src), project=None, layer=None,
                                        move=False))
        self.assertEqual(rc, 0)
        stored = list((self.knowledge / "acme").glob("*.png"))
        self.assertEqual(len(stored), 1)
        self.assertTrue(self.fresh_store().search("widget board"))

    def test_add_reports_a_missing_file_instead_of_crashing(self):
        rc = self.cli.cmd_add(self._args(path=str(self.knowledge / "nope.png"),
                                         project=None, layer=None, move=False))
        self.assertEqual(rc, 1)

    def test_add_can_move_the_original(self):
        src = Path(self.tmp.name) / "acme-widget-slide.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 16)
        self.cli.cmd_add(self._args(path=str(src), project="acme", layer="attachments",
                                    move=True))
        self.assertFalse(src.exists())
        self.assertTrue(list((self.knowledge / "acme" / "attachments").glob("*.png")))

    def test_materialize_command_runs_and_indexes(self):
        self.store.upsert_document(_chat(project="acme"))
        self.store.commit()
        rc = self.cli.cmd_materialize(self._args(
            dry_run=False, source=None, project=None, layer=None, limit=None,
            keep_sources=True, include_unfiled=False))
        self.assertEqual(rc, 0)
        self.assertTrue(list((self.knowledge / "acme" / "conversations").glob("*.md")))
        self.assertEqual(len(self.fresh_store().iter_documents()), 1)

    def test_materialize_dry_run_command_writes_nothing(self):
        self.store.upsert_document(_chat(project="acme"))
        self.store.commit()
        before = self.files()
        rc = self.cli.cmd_materialize(self._args(
            dry_run=True, source=None, project=None, layer=None, limit=None,
            keep_sources=True, include_unfiled=False))
        self.assertEqual(rc, 0)
        self.assertEqual(self.files(), before)

    def test_materialize_limit_takes_a_slice(self):
        for n in ("a", "b", "c"):
            self.store.upsert_document(_chat(native_id=n, project="acme",
                                             title=f"Chat {n}"))
        self.store.commit()
        plan, _ = materialize.run(self.store, limit=2, retire=False)
        self.assertEqual(len(plan.actionable), 2)

    def test_paste_saves_and_indexes_without_a_drop_folder(self):
        transcript = ("Meeting Title: Widget standup\nDate: 2026-07-08\n\n"
                      "Transcript:\nMe: widget rollout is on track.\n")
        with mock.patch.object(sys, "stdin", io.StringIO(transcript)):
            rc = self.cli.cmd_paste(self._args(
                stdin=True, title=None, date=None, project=None,
                layer=None, source=config.SOURCE_GRANOLA))
        self.assertEqual(rc, 0)
        written = [q for q in (self.knowledge / "acme").glob("*.md")
                   if q.name != "_project.md"]
        self.assertEqual(len(written), 1, "it must land straight in the project folder")
        self.assertTrue(self.fresh_store().search("widget rollout on track"))
        self.assertFalse((self.knowledge / "Inbox").exists(),
                         "no drop folder may be created")

    def test_paste_reports_empty_input_instead_of_writing_nothing_quietly(self):
        with mock.patch.object(sys, "stdin", io.StringIO("   ")):
            rc = self.cli.cmd_paste(self._args(
                stdin=True, title=None, date=None, project=None, layer=None,
                source=config.SOURCE_GRANOLA))
        self.assertEqual(rc, 1)

    def test_paths_command_names_no_retired_folder(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.cli.cmd_paths(self._args())
        out = buf.getvalue()
        for gone in ("Inbox", "_sources", "_proposals", "_archive", "drop here"):
            self.assertNotIn(gone, out)


if __name__ == "__main__":
    unittest.main()
