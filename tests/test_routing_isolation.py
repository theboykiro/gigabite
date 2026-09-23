"""Recall isolation: one project's material must never reach another's session.

Recall is injected into every prompt on the machine and presented as the user's
own memory. On a laptop that holds work for more than one client, a hit from the
wrong project is not a relevance miss — it is that client's material appearing,
unasked, in someone else's session.

Two behaviours are pinned here, and both were previously the opposite:

  A. `route` scoped the search to the detected project and then, whenever that
     returned fewer than `limit` hits, re-ran the same query **unscoped** and
     padded the results. Correctly detecting the project was what triggered the
     leak, on exactly the projects with the least of their own to say.
  B. An unresolved prompt fell back to searching everything, which is the one
     query that cannot be safe.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402
from gigabite.features import routing  # noqa: E402

# Long enough, and past-referring, so the register router calls these `brief`
# rather than `spar` — the register axis is tested in test_register.py.
ALPHA_PROMPT = "what did we decide about the widget pricing schedule"
NEUTRAL_PROMPT = "what did we decide about the pricing schedule"


class _TwoProjects(_harness.TempRoot):
    """Two registered projects. `beta` holds plenty; `alpha` holds one document."""

    def setUp(self):
        super().setUp()
        for name, keywords in (("alpha", "widget"), ("beta", "gadget")):
            d = config.KNOWLEDGE_DIR / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "_project.md").write_text(
                f"---\nproject: {name}\nkeywords: {keywords}\nlayers: \n---\n",
                encoding="utf-8")

        self.st = Store(connect(self.db))
        self.st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="a1", title="Alpha pricing",
            project="alpha",
            messages=[Message(0, "note", "the widget pricing schedule was agreed")]))
        for i in range(6):
            self.st.upsert_document(Document(
                source=config.SOURCE_NOTE, native_id=f"b{i}", title=f"Beta pricing {i}",
                project="beta",
                messages=[Message(0, "note", "the pricing schedule was agreed here too")]))
        self.st.commit()

    def code_dir(self, name):
        """A checkout path outside the knowledge base, named `name`."""
        return str(self.root.parent / "code" / name)

    def projects_in(self, result):
        return {h["project"] for h in result["hits"]}


class TestScopedMeansScoped(_TwoProjects):
    def test_a_thin_project_is_not_padded_from_another_project(self):
        """The leak path: fewer hits than `limit` used to trigger an unscoped re-run."""
        out = routing.route(self.st, ALPHA_PROMPT, limit=6, cwd="")
        self.assertEqual(out["context"]["project"], "alpha")
        self.assertTrue(out["hits"], "the project's own material should still return")
        self.assertLess(len(out["hits"]), 6, "this is the under-limit case that leaked")
        self.assertEqual(self.projects_in(out), {"alpha"})

    def test_the_other_project_would_otherwise_have_matched(self):
        """Guards the test above: beta really is reachable for this query."""
        rows = self.st.search(NEUTRAL_PROMPT, limit=6, record=False)
        self.assertIn("beta", {r["project"] for r in rows})


class TestUnresolvedInjectsNothing(_TwoProjects):
    def test_an_ambiguous_prompt_returns_no_hits(self):
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6, cwd="")
        self.assertEqual(out["context"]["confidence"], "ambiguous")
        self.assertIsNone(out["context"]["project"])
        self.assertEqual(out["hits"], [])

    def test_an_unknown_marker_does_not_scope_to_itself_and_returns_nothing(self):
        out = routing.route(self.st, "janedoe@Janes-MacBook-Pro pricing schedule notes",
                            limit=6, cwd="")
        self.assertNotEqual(out["context"]["project"], "Janes-MacBook-Pro")
        self.assertEqual(out["hits"], [])


class TestADirectoryNameIsNeverAProject(_TwoProjects):
    """Finding 4: the basename used to select a project all by itself.

    `clientB/docs/alpha` is a folder *about* alpha inside somebody else's checkout,
    and it resolved to the alpha project and injected alpha's material — with no
    binding anywhere, and nothing the user ever said. Only an explicit binding may
    scope a directory.
    """

    def workspace(self, *parts):
        path = self.root.parent.joinpath("code", *parts)
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").mkdir(exist_ok=True)
        return str(path)

    def test_a_directory_named_after_a_real_project_does_not_scope_the_search(self):
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6,
                            cwd=self.workspace("beta"))
        self.assertIsNone(out["context"]["project"])
        self.assertEqual(out["context"]["confidence"], "ambiguous")
        self.assertEqual(out["hits"], [])

    def test_a_folder_named_after_a_project_inside_another_checkout_leaks_nothing(self):
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6,
                            cwd=self.workspace("other-checkout", "docs", "beta"))
        self.assertIsNone(out["context"]["project"])
        self.assertEqual(out["hits"], [])

    def test_an_alias_does_not_turn_a_directory_name_into_a_project_either(self):
        config.ALIASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.ALIASES_FILE.write_text(json.dumps({"beta-app": "beta"}), encoding="utf-8")
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6,
                            cwd=self.workspace("beta-app"))
        self.assertIsNone(out["context"]["project"])
        self.assertEqual(out["hits"], [])

    def test_a_binding_is_the_only_thing_that_scopes_a_directory(self):
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6, cwd=work)
        self.assertEqual(out["context"]["project"], "beta")
        self.assertEqual(out["context"]["confidence"], "binding")
        self.assertEqual(self.projects_in(out), {"beta"})

    def test_a_keyword_for_another_project_does_not_outrank_the_binding(self):
        """Finding 5: a stray keyword used to beat the binding and leak.

        In a directory the user bound to `beta`, a prompt that merely *mentions*
        something in `alpha`'s vocabulary resolved to `alpha` and injected
        `alpha`'s passages. A binding is something the user deliberately said
        about this folder; a keyword is a word in a sentence.
        """
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        out = routing.route(self.st, ALPHA_PROMPT, limit=6, cwd=work)
        self.assertEqual(out["context"]["project"], "beta")
        self.assertEqual(out["context"]["confidence"], "binding")
        self.assertNotIn("alpha", self.projects_in(out))

    def test_the_reason_says_the_keyword_was_ignored(self):
        """The user has to be able to tell *why* their words did nothing."""
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        out = routing.route(self.st, ALPHA_PROMPT, limit=6, cwd=work)
        reason = out["context"]["reason"]
        self.assertIn("bound", reason)
        self.assertIn("alpha", reason, "name the project whose keyword was ignored")
        self.assertIn("@alpha", reason, "and how to override it for this turn")

    def test_a_plain_bound_turn_says_only_that_it_is_bound(self):
        """No keyword in the prompt, no noise about one in the reason."""
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        out = routing.route(self.st, NEUTRAL_PROMPT, limit=6, cwd=work)
        self.assertEqual(out["context"]["project"], "beta")
        self.assertNotIn("@", out["context"]["reason"])

    def test_a_marker_still_overrides_the_binding(self):
        """The one signal that outranks a binding: the user saying so, this turn."""
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        out = routing.route(self.st, "@alpha " + ALPHA_PROMPT, limit=6, cwd=work)
        self.assertEqual(out["context"]["project"], "alpha")
        self.assertEqual(out["context"]["confidence"], "explicit")
        self.assertEqual(self.projects_in(out), {"alpha"})

    def test_keywords_still_resolve_in_an_unbound_directory(self):
        """Nothing changes where the user has said nothing about the folder."""
        out = routing.route(self.st, ALPHA_PROMPT, limit=6,
                            cwd=self.workspace("unbound-checkout"))
        self.assertEqual(out["context"]["project"], "alpha")
        self.assertEqual(out["context"]["confidence"], "keyword")
        self.assertEqual(self.projects_in(out), {"alpha"})

    def test_a_stale_binding_does_not_hand_the_directory_to_a_keyword(self):
        """Bound to a project whose `_project.md` is gone: scope to nothing.

        Falling through to keywords here would reopen the same leak by another
        door — the user said this folder is someone else's work, and a missing
        metadata file is not them changing their mind.
        """
        work = self.workspace("some-checkout")
        from gigabite.features import bindings
        bindings.bind(work, "beta")
        (config.KNOWLEDGE_DIR / "beta" / "_project.md").unlink()
        out = routing.route(self.st, ALPHA_PROMPT, limit=6, cwd=work)
        self.assertIsNone(out["context"]["project"])
        self.assertEqual(out["hits"], [])

    def test_filing_never_takes_a_project_from_the_working_directory(self):
        """`resolve_context` consults cwd only when a caller passes one.

        Content being filed must not inherit whatever directory the shell was in.
        """
        ctx = routing.resolve_context("a document with no project signal in it")
        self.assertIsNone(ctx["project"])
        self.assertEqual(ctx["confidence"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
