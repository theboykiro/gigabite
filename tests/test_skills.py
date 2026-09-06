"""Tests for the shipped Claude Code skills. Pure stdlib. No home writes.

These assert properties of the files in `install/scaffold/skills/` — the source the
installer copies from — because every one of them fails *silently* in Claude Code:

* A skill lives at `<name>/SKILL.md`. A flat `<name>.md` is ignored with no warning.
* Invalid frontmatter YAML strips all metadata and turns the whole file into body, so
  the skill never triggers. The values here therefore stay flat scalars — in
  particular, no bare `": "` inside a value, which is the easiest way to break it.
* `allowed-tools` is hyphenated. `allowed_tools` fails validation.
* **A skill shadows a slash command of the same name.** gigabite ships `/meeting`,
  so a skill called `meeting` would disable it and say nothing. That is the test in
  here most worth having.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import re
import unittest
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite.util import parse_frontmatter  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_SKILLS = _REPO / "install" / "scaffold" / "skills"
_COMMANDS = _REPO / "install" / "claude-commands"

MARKER = "<!-- gigabite:managed"
REQUIRED_KEYS = ("name", "description", "when_to_use", "allowed-tools")
# Claude Code's own cap on description + when_to_use combined.
METADATA_BUDGET = 1536


def _flat(text: str) -> str:
    """Lowercased, whitespace-collapsed. These files are hard-wrapped at 88 columns,
    so a phrase asserted on may be split across two lines in the source."""
    return " ".join(text.split()).lower()


def skill_files() -> list[Path]:
    """Every shipped skill, asserting the directory has not moved out from under us.

    Globbing a renamed directory yields nothing silently, which would turn this whole
    file green while shipping no skills at all.
    """
    if not _SKILLS.is_dir():
        raise AssertionError(
            f"scaffold skills not found at {_SKILLS} — did the directory move? "
            f"Update _SKILLS in this file to match."
        )
    found = sorted(_SKILLS.glob("*/SKILL.md"))
    if not found:
        raise AssertionError(f"no <name>/SKILL.md under {_SKILLS}")
    return found


class SkillTestCase(unittest.TestCase):
    def setUp(self):
        self.skills = skill_files()

    def each(self):
        for path in self.skills:
            yield path, path.read_text(encoding="utf-8")


class TestLayout(SkillTestCase):
    def test_the_three_skills_are_shipped(self):
        self.assertEqual(
            sorted(p.parent.name for p in self.skills),
            ["decision-record", "design-critique", "meeting-prep"],
        )

    def test_a_skill_is_a_directory_containing_SKILL_md(self):
        for path in self.skills:
            self.assertEqual(path.name, "SKILL.md")
            self.assertTrue(path.parent.is_dir())

    def test_no_stray_flat_md_files_that_would_be_ignored(self):
        self.assertEqual(list(_SKILLS.glob("*.md")), [])

    def test_directory_names_are_valid_skill_names(self):
        for path in self.skills:
            self.assertRegex(path.parent.name, r"^[a-zA-Z0-9_-]+$")


class TestCollisionWithSlashCommands(SkillTestCase):
    """A skill wins over a slash command of the same name, and does so quietly.

    `/meeting` is the live case: it files a transcript from the clipboard, and a skill
    named `meeting` would take the name and leave the command unreachable.
    """

    def test_no_skill_is_named_meeting(self):
        self.assertNotIn("meeting", [p.parent.name for p in self.skills])

    def test_no_skill_shadows_any_shipped_command(self):
        commands = {p.stem for p in _COMMANDS.glob("*.md")}
        self.assertIn("meeting", commands, "the command this guards has moved")
        for path in self.skills:
            self.assertNotIn(
                path.parent.name, commands,
                f"skill {path.parent.name} would shadow /{path.parent.name}")


class TestFrontmatter(SkillTestCase):
    def test_frontmatter_opens_on_line_one(self):
        for path, text in self.each():
            self.assertTrue(text.startswith("---\n"), f"{path}: no frontmatter at line 1")

    def test_required_keys_are_present_and_non_empty(self):
        for path, text in self.each():
            meta, body = parse_frontmatter(text)
            self.assertTrue(body.strip(), f"{path}: frontmatter but no body")
            for key in REQUIRED_KEYS:
                self.assertIn(key, meta, f"{path}: missing {key}")
                self.assertTrue(meta[key].strip(), f"{path}: empty {key}")

    def test_name_matches_the_directory(self):
        for path, text in self.each():
            meta, _ = parse_frontmatter(text)
            self.assertEqual(meta["name"], path.parent.name)

    def test_allowed_tools_is_hyphenated_not_underscored(self):
        for path, text in self.each():
            block = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
            self.assertRegex(block, r"(?m)^allowed-tools:", f"{path}: no allowed-tools")
            self.assertNotRegex(
                block, r"(?m)^allowed_tools:",
                f"{path}: allowed_tools fails validation — it must be hyphenated")

    def test_every_frontmatter_line_is_a_flat_scalar(self):
        """Keep it simple and it stays parseable. A `": "` inside a value does not."""
        for path, text in self.each():
            block = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
            self.assertNotIn("\t", block, f"{path}: tab in frontmatter")
            for line in block.splitlines():
                self.assertRegex(line, r"^[a-z][a-z_-]*: \S.*$",
                                 f"{path}: not a flat `key: value` line -> {line!r}")
                _, _, value = line.partition(": ")
                self.assertNotIn(": ", value,
                                 f"{path}: `: ` in a value breaks the YAML -> {line!r}")
                self.assertNotIn(value[0], "[{*&!%@`\"'",
                                 f"{path}: value starts with a YAML sigil -> {line!r}")

    def test_description_and_when_to_use_fit_the_metadata_budget(self):
        for path, text in self.each():
            meta, _ = parse_frontmatter(text)
            total = len(meta["description"]) + len(meta["when_to_use"])
            self.assertLessEqual(total, METADATA_BUDGET, f"{path}: {total} chars")


class TestManagedMarker(SkillTestCase):
    """The marker is how the installer tells its own files from the user's.

    `install.sh` only overwrites a file that looks like ours; without the marker a
    reinstall would leave every skill at whatever version shipped first.
    """

    def test_the_marker_is_the_first_line_after_the_frontmatter(self):
        for path, text in self.each():
            _, body = parse_frontmatter(text)
            first = next(line for line in body.splitlines() if line.strip())
            self.assertTrue(first.startswith(MARKER), f"{path}: marker not first -> {first!r}")

    def test_the_marker_matches_the_commands_and_agents_verbatim(self):
        reference = next(
            line for line in (_REPO / "install" / "scaffold" / "agents" / "gg-builder.md")
            .read_text(encoding="utf-8").splitlines() if line.startswith(MARKER))
        for path, text in self.each():
            self.assertIn(reference, text, f"{path}: marker text drifted")

    def test_the_installer_recognises_a_skill_as_its_own(self):
        """`is_ours()` in install.sh is a case-insensitive grep for "gigabite"."""
        for path, text in self.each():
            self.assertIn("gigabite", text.lower())


class TestContract(SkillTestCase):
    """The behaviour every gigabite skill promises: recall first, cite, no invention."""

    def test_each_skill_searches_the_index(self):
        for path, text in self.each():
            self.assertIn("gigabite search", text, f"{path}: never consults recall")

    def test_each_skill_cites_its_sources(self):
        for path, text in self.each():
            self.assertIn("(source · title · date)", _flat(text),
                          f"{path}: no citation format")

    def test_each_skill_refuses_to_invent_history(self):
        for path, text in self.each():
            self.assertIn("never invent history", _flat(text),
                          f"{path}: no anti-fabrication rule")

    def test_each_skill_loads_the_operating_protocol(self):
        for path, text in self.each():
            self.assertIn("~/.core/core.md", text, f"{path}: does not load the protocol")


class TestDecisionRecordWritesOnlyThroughTheTool(unittest.TestCase):
    """The hard architectural rule: knowledge is persisted by the CLI, never by hand.

    A skill that writes into `~/Knowledge` itself bypasses project routing, provenance
    frontmatter and the index, so the note exists but cannot be found again.
    """

    def setUp(self):
        self.text = (_SKILLS / "decision-record" / "SKILL.md").read_text(encoding="utf-8")

    def test_it_saves_through_the_cli(self):
        self.assertIn("gigabite save", self.text)

    def test_it_forbids_writing_into_the_knowledge_base_directly(self):
        self.assertIn("never write into `~/knowledge` yourself", _flat(self.text))

    def test_it_declares_no_write_tool(self):
        meta, _ = parse_frontmatter(self.text)
        tools = meta["allowed-tools"]
        for banned in ("Write", "Edit"):
            self.assertNotIn(banned, tools,
                             f"decision-record must not be able to {banned} a file")

    def test_it_does_not_invent_a_project(self):
        self.assertIn("do not invent a project", _flat(self.text))
        self.assertIn("unfiled", self.text)


class TestConfidentiality(SkillTestCase):
    """Placeholders only. The repo is an egress boundary, examples included."""

    def test_examples_use_placeholder_names(self):
        allowed = {"acme", "contoso", "widget", "sprocket", "jane", "john", "example"}
        for path, text in self.each():
            quoted = re.findall(r"[\"“]([^\"”]{0,80})[\"”]", text)
            for phrase in quoted:
                for word in re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", phrase):
                    self.assertIn(word.lower(), allowed | {"read", "glob", "bash"},
                                  f"{path}: capitalised name in an example -> {phrase!r}")


class TestInstallerWiring(unittest.TestCase):
    """install.sh is not executed here — it registers a LaunchAgent and rewrites
    ~/.claude. So the wiring is asserted by reading it: every shipped skill has to be
    reachable by the loop, and the loop has to reuse the protection the commands get.
    """

    def setUp(self):
        self.sh = (_REPO / "install.sh").read_text(encoding="utf-8")

    def test_it_installs_from_the_scaffold_to_the_skills_directory(self):
        self.assertIn('"$REPO/install/scaffold/skills/"*/SKILL.md', self.sh)
        self.assertIn('SKILL_DIR="$HOME/.claude/skills"', self.sh)
        self.assertIn('mkdir -p "$SKILL_DIR/$skill"', self.sh)

    def test_it_reuses_install_managed_rather_than_overwriting_blindly(self):
        self.assertIn('install_managed "$s" "$SKILL_DIR/$skill/SKILL.md"', self.sh)

    def test_the_step_heading_mentions_skills(self):
        heading = next(l for l in self.sh.splitlines() if l.startswith('say "3/7'))
        self.assertIn("skills", heading)

    def test_the_summary_block_mentions_skills(self):
        head = "\n".join(self.sh.splitlines()[:8])
        self.assertIn("skills", head)


if __name__ == "__main__":
    unittest.main()
