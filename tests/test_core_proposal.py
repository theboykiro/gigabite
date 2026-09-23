"""Tests for the core-setup applier and its write gate.

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
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402

# The real sibling modules, always. An earlier version of this file installed
# stubs for `core_slots` and `core_coverage` when they failed to import, which
# was defensible while the three modules were being built in parallel and is not
# any more: they all exist, and a fallback that fires on ImportError can only
# hide breakage. Masking `core_slots` used to leave the suite green; it must now
# fail loudly. Where a test needs a controlled slot set, it builds one visibly
# in the test rather than through an import-time fallback.
from gigabite.features import core_proposal, core_slots  # noqa: E402


class TestTheWriteGate(_harness.TempRoot):
    """The gate. Do not delete or weaken these without reading CORE_SETUP §5.

    `core.md` is the constitutional layer, loaded in full into every session. The
    whole design rests on one rule: nothing generated reaches that file without
    the user approving it, per slot. The only function permitted to write it is
    `apply_proposal`, and only when a caller hands it an explicit mapping of
    approved slots. If this test is failing, the feature is unsafe to ship — fix
    the code, not the test.
    """

    def test_no_unallowed_filesystem_mutation_in_the_core_setup_modules(self):
        """A static check, so a future writer path fails here rather than in the wild.

        This must not be weakened back into a grep. A `write_text`-only text
        search over one file was blind to `open(..., 'w')`, `shutil.*`,
        `os.replace` and `Path.replace` — and in fact could not see the
        `core_path.replace(kept)` in `set_aside`, which mutates `core.md`. It is
        the AST of the `core_*.py` modules that establishes the invariant,
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

        # The only call sites that may touch disk, and why each is safe:
        #   set_aside       — moves the existing core.md to a dated copy
        #   apply_proposal  — the sole writer of core.md, gated on `approved`
        ALLOWED = {
            ("core_proposal", "set_aside", "replace"),
            ("core_proposal", "apply_proposal", "write_text"),
        }

        found = set()
        for module in ("core_slots", "core_proposal"):
            path = Path(core_proposal.__file__).with_name(f"{module}.py")
            self.assertTrue(path.exists(), path)
            found.update(mutations(path, module))

        self.assertEqual(found - ALLOWED, set(), "unallowed mutation call site")
        # and the known-good ones are still there, so the check cannot pass by
        # having silently stopped finding anything
        self.assertEqual(found, ALLOWED)


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
        self.assertEqual(params, {"approved", "core_path", "sections"}, params)

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
