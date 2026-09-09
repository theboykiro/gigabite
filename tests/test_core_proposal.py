"""Tests for the core-setup proposal writer and its write gate.

Pure stdlib (unittest). No network, no writes to the real home directory — the
harness repoints `~/.core` and `~/Knowledge` into a temp dir before `gigabite`
is imported.

    python3 -m unittest discover -s tests        (from the repo root)

`TestTheWriteGate` is the load-bearing test in this feature. Read its docstring
before touching it.
"""

import ast
import inspect
import sys
import os
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402

# The real sibling modules, always. An earlier version of this file installed
# stubs for `core_slots` and `core_coverage` when they failed to import, which
# was defensible while the three modules were being built in parallel and is not
# any more: all three exist, and a fallback that fires on ImportError can only
# hide breakage. Masking `core_slots` used to leave the suite green; it must now
# fail loudly. Where a test needs a controlled slot set, it builds one visibly
# in the test rather than through an import-time fallback.
from gigabite.features import core_proposal, core_slots  # noqa: E402


# ---------------------------------------------------------------------------
# local fixtures for build_proposal
#
# `build_proposal` reads its input by attribute, so these plain objects exercise
# it without depending on the sibling module's exact types.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FakeEvidence:
    doc_id: str = "claude_code:abc123"
    source: str = "claude_code"
    title: str = "A past session"
    created_utc: str = "2026-01-02T10:00:00+00:00"
    snippet: str = "stop summarising at the end, just give me the answer"
    why: str = "a correction: an explicit instruction about assistant behaviour"


@dataclass(frozen=True)
class FakeCoverage:
    slot_id: str
    state: str
    evidence: tuple = field(default=())


class TestBuildProposal(unittest.TestCase):
    def test_every_slot_gets_its_own_approval_checkbox(self):
        coverages = [
            FakeCoverage("tone.cut", "evidenced", (FakeEvidence(),)),
            FakeCoverage("decisions.ambiguity", "empty"),
            FakeCoverage("tone.invariants", "shipped"),
        ]
        text = core_proposal.build_proposal(coverages)
        for slot_id in ("tone.cut", "decisions.ambiguity", "tone.invariants"):
            self.assertIn(f"- [ ] Approve `{slot_id}`", text)
        # per slot, not all-or-nothing
        self.assertEqual(text.count("- [ ] Approve `"), 3)
        self.assertIn("GATED", text)

    def test_evidenced_slot_shows_the_draft_beside_its_evidence(self):
        ev = FakeEvidence()
        coverages = [FakeCoverage("tone.cut", "evidenced", (ev,))]
        text = core_proposal.build_proposal(
            coverages, drafts={"tone.cut": "- No closing summary."})
        self.assertIn("- No closing summary.", text)
        self.assertIn("Evidence", text)
        self.assertIn(ev.snippet, text)
        self.assertIn(ev.doc_id, text)
        self.assertIn(ev.why, text)

    def test_evidenced_slot_without_a_draft_never_invents_one(self):
        text = core_proposal.build_proposal(
            [FakeCoverage("tone.cut", "evidenced", (FakeEvidence(),))])
        self.assertIn(core_proposal.DRAFT_PLACEHOLDER, text)

    def test_thin_and_empty_slots_show_the_question_not_a_draft(self):
        coverages = [
            FakeCoverage("decisions.ambiguity", "empty"),
            FakeCoverage("decisions.pushback", "thin", (FakeEvidence(),)),
        ]
        text = core_proposal.build_proposal(coverages)
        self.assertIn("Question", text)
        self.assertIn(core_slots.get_slot("decisions.ambiguity").question, text)
        self.assertIn(core_slots.get_slot("decisions.pushback").question, text)
        self.assertNotIn("**Proposed**", text)

    def test_existing_core_md_is_shown_as_a_change(self):
        # Headings taken from the registry, so this fixture cannot drift out of
        # step with the real slot titles and quietly stop exercising the lookup.
        cut = core_slots.get_slot("tone.cut").title
        existing = (
            f"# Core Protocol\n\n## {cut}\n\n- No validation openers.\n\n"
            "## Push-back\n\n[FILL]\n"
        )
        text = core_proposal.build_proposal(
            [FakeCoverage("tone.cut", "evidenced", (FakeEvidence(),)),
             FakeCoverage("info.uncertainty", "empty")],
            existing_core_md=existing,
            drafts={"tone.cut": "- No validation openers. No closing summary."},
        )
        self.assertIn("**Currently**", text)
        self.assertIn("> - No validation openers.", text)
        # a slot with nothing in the current file is reported as an addition,
        # not silently presented as if it replaced something
        self.assertIn("Not present — this would be added.", text)

    def test_accepts_a_generator(self):
        gen = (FakeCoverage(sid, "empty")
               for sid in ("tone.cut", "decisions.pushback"))
        text = core_proposal.build_proposal(gen)
        self.assertIn("Slots: 2.", text)
        self.assertIn("- [ ] Approve `decisions.pushback`", text)


class TestTheWriteGate(_harness.TempRoot):
    """The gate. Do not delete or weaken these without reading CORE_SETUP §5.

    `core.md` is the constitutional layer, loaded in full into every session. The
    whole design rests on one rule: nothing generated reaches that file without
    the user approving it, per slot. `write_proposal` runs retrieval and drafting
    and must leave `core.md` byte-identical; the only function permitted to write
    it is `apply_proposal`, and only when a caller hands it an explicit mapping of
    approved slots. If either of these tests is failing, the feature is unsafe to
    ship — fix the code, not the test.
    """

    def test_write_proposal_leaves_core_md_byte_identical(self):
        before = "# Core Protocol\n\n## Cut\n\n- The user's own words.\n"
        config.CORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CORE_FILE.write_bytes(before.encode("utf-8"))
        stat_before = config.CORE_FILE.stat().st_mtime_ns

        store = _harness.scratch_store("core_proposal_gate")
        path = core_proposal.write_proposal(store)

        self.assertTrue(path.exists())
        self.assertEqual(path.parent, config.PROPOSALS_DIR)
        self.assertEqual(config.CORE_FILE.read_bytes(), before.encode("utf-8"))
        self.assertEqual(config.CORE_FILE.stat().st_mtime_ns, stat_before)
        self.assertIn("GATED", path.read_text(encoding="utf-8"))

    def test_write_proposal_does_not_create_core_md_when_absent(self):
        self.assertFalse(config.CORE_FILE.exists())
        store = _harness.scratch_store("core_proposal_gate_absent")
        core_proposal.write_proposal(store)
        self.assertFalse(config.CORE_FILE.exists())

    def test_no_unallowed_filesystem_mutation_in_the_core_setup_modules(self):
        """A static check, so a future writer path fails here rather than in the wild.

        This must not be weakened back into a grep. A `write_text`-only text
        search over one file was blind to `open(..., 'w')`, `shutil.*`,
        `os.replace` and `Path.replace` — and in fact could not see the
        `core_path.replace(kept)` in `set_aside`, which mutates `core.md`. It is
        the AST of all three `core_*.py` modules that establishes the invariant,
        not the intentions in the docstrings.

        Every mutating primitive is collected as (module, enclosing function,
        primitive) and compared against an explicit allowlist. Additions are
        deliberately noisy: a new call site fails here and has to be justified
        into `ALLOWED` by whoever adds it. `str.replace` is caught too — the
        check does not try to infer the receiver's type, and losing a
        false-positive argument is cheaper than losing the gate.
        """
        # attribute calls that change bytes on disk
        MUTATORS = {
            "write_text", "write_bytes",
            "replace", "rename",
            "unlink", "remove", "rmdir", "rmtree",
            "copy", "copy2", "copyfile", "copytree", "copyfileobj", "move",
            "truncate", "touch",
        }
        WRITE_MODES = ("w", "a", "x", "+")

        def mutations(path: Path, module: str):
            found = []

            class Walk(ast.NodeVisitor):
                def __init__(self):
                    self.scope = ["<module>"]

                def _in_scope(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_FunctionDef = _in_scope
                visit_AsyncFunctionDef = _in_scope
                visit_ClassDef = _in_scope

                def visit_Call(self, node):
                    func = node.func
                    name = getattr(func, "attr", None) or getattr(func, "id", None)
                    if name == "open":
                        mode = ""
                        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                            mode = str(node.args[1].value)
                        for kw in node.keywords:
                            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                                mode = str(kw.value.value)
                        if any(c in mode for c in WRITE_MODES):
                            found.append((module, self.scope[-1], "open(write)"))
                    elif name in MUTATORS:
                        found.append((module, self.scope[-1], name))
                    self.generic_visit(node)

            Walk().visit(ast.parse(path.read_text(encoding="utf-8")))
            return found

        # The only three call sites that may touch disk, and why each is safe:
        #   write_proposal  — writes the proposal, never core.md
        #   set_aside       — moves the existing core.md to a dated copy
        #   apply_proposal  — the sole writer of core.md, gated on `approved`
        ALLOWED = {
            ("core_proposal", "write_proposal", "write_text"),
            ("core_proposal", "set_aside", "replace"),
            ("core_proposal", "apply_proposal", "write_text"),
        }

        found = set()
        for module in ("core_slots", "core_coverage", "core_proposal"):
            path = Path(core_proposal.__file__).with_name(f"{module}.py")
            self.assertTrue(path.exists(), path)
            found.update(mutations(path, module))

        self.assertEqual(found - ALLOWED, set(), "unallowed mutation call site")
        # and the known-good ones are still there, so the check cannot pass by
        # having silently stopped finding anything
        self.assertEqual(found, ALLOWED)


class TestWriteProposal(_harness.TempRoot):
    def test_idempotent_for_the_day(self):
        store = _harness.scratch_store("core_proposal_idem")
        p1 = core_proposal.write_proposal(store)
        p2 = core_proposal.write_proposal(store)
        self.assertEqual(p1, p2)
        self.assertEqual(len(core_proposal.list_proposals()), 1)

    def test_explicit_path_is_honoured(self):
        store = _harness.scratch_store("core_proposal_path")
        out = self.root / "elsewhere" / "proposal.md"
        path = core_proposal.write_proposal(store, path=out)
        self.assertEqual(path, out)
        self.assertTrue(out.exists())


class TestApplyProposal(_harness.TempRoot):
    def _seed_core(self, text="# Core Protocol\n\n## Cut\n\n- original line.\n"):
        config.CORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CORE_FILE.write_text(text, encoding="utf-8")
        return text

    def test_empty_mapping_is_a_no_op_and_does_not_truncate(self):
        before = self._seed_core()
        path = core_proposal.apply_proposal({})
        self.assertEqual(path, config.CORE_FILE)
        self.assertEqual(config.CORE_FILE.read_text(encoding="utf-8"), before)

    def test_empty_mapping_does_not_create_a_blank_file(self):
        self.assertFalse(config.CORE_FILE.exists())
        core_proposal.apply_proposal({})
        self.assertFalse(config.CORE_FILE.exists())

    def test_existing_core_md_is_set_aside_and_recoverable(self):
        before = self._seed_core()
        core_proposal.apply_proposal({"tone.cut": "- a new line."})

        kept = sorted(config.ORIGINALS_DIR.glob("replaced-*-core.md"))
        self.assertEqual(len(kept), 1, kept)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), before)
        self.assertIn("- a new line.", config.CORE_FILE.read_text(encoding="utf-8"))

    def test_set_aside_cannot_be_skipped_by_any_caller(self):
        """There is no opt-out, and there must not be one.

        `apply_proposal` once took `backup=False`, which deleted the user's
        protocol with nothing kept anywhere — the one branch that made the
        module's "nothing here deletes" promise false. The parameter is gone; a
        differently-named escape hatch would be the same bug.
        """
        params = set(inspect.signature(core_proposal.apply_proposal).parameters)
        self.assertEqual(params, {"approved", "core_path"}, params)

        before = self._seed_core("the user's own protocol\n")
        target = core_proposal.apply_proposal({"tone.cut": "- replaced."})

        kept = [p.read_text(encoding="utf-8")
                for p in config.ORIGINALS_DIR.glob("replaced-*core.md")]
        self.assertIn(before, kept)
        self.assertNotIn(before, target.read_text(encoding="utf-8"))

    def test_a_second_apply_never_overwrites_the_first_set_aside_copy(self):
        first = self._seed_core("first\n")
        core_proposal.apply_proposal({"tone.cut": "- one."})
        core_proposal.apply_proposal({"tone.cut": "- two."})
        kept = sorted(config.ORIGINALS_DIR.glob("replaced-*core.md"))
        self.assertEqual(len(kept), 2, kept)
        self.assertIn(first, [p.read_text(encoding="utf-8") for p in kept])

    def test_per_slot_approval_writes_only_the_approved_slots(self):
        approved = {
            "tone.cut": "- No closing summary.",
            "decisions.ambiguity": "- Make the call, say why.",
        }
        core_proposal.apply_proposal(approved)
        text = config.CORE_FILE.read_text(encoding="utf-8")

        self.assertIn("- No closing summary.", text)
        self.assertIn("- Make the call, say why.", text)
        # the three slots the user did not approve stay unfilled rather than
        # being invented
        self.assertNotIn("- Push back hard.", text)
        self.assertIn("[FILL]", text)

    def test_core_path_override_leaves_the_real_core_untouched(self):
        target = self.root / "somewhere" / "core.md"
        path = core_proposal.apply_proposal({"tone.cut": "- x."}, core_path=target)
        self.assertEqual(path, target)
        self.assertTrue(target.exists())
        self.assertFalse(config.CORE_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
