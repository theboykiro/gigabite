"""Tests for the core.md slot registry and renderer (docs/CORE_SETUP.md §2).

Pure stdlib (unittest). No network; nothing here touches the real `~/.core` —
the renderer does no file I/O at all, and the harness redirects the stores
regardless.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402
from gigabite.features import core_slots  # noqa: E402

SCAFFOLD = config.REPO_ROOT / "install" / "scaffold" / "core.md"

# The one place the rendered file deliberately differs from the scaffold:
# `info.citation` is a shipped invariant in the spec's §2 table, and the
# scaffold has no line for it. Pinned so it cannot grow quietly.
EXPECTED_EXTRA_LINES = core_slots.get_slot("info.citation").shipped_text.splitlines()


def FULL_COPY(d):
    return dict(d)


def _headings(text: str):
    return [ln for ln in text.splitlines() if ln.startswith("## ")]


class RenderMatchesScaffold(unittest.TestCase):
    """`render_core_md({})` reproduces the file the installer ships today."""

    def setUp(self):
        self.scaffold = SCAFFOLD.read_text(encoding="utf-8")
        self.rendered = core_slots.render_core_md({})

    def test_scaffold_exists(self):
        self.assertTrue(SCAFFOLD.is_file(), f"missing scaffold at {SCAFFOLD}")

    def test_same_six_headings_in_order(self):
        self.assertEqual(_headings(self.scaffold), _headings(self.rendered))
        self.assertEqual(len(_headings(self.rendered)), 6)

    def test_fill_markers_land_exactly_where_the_scaffold_has_them(self):
        marked = [h for h in _headings(self.rendered) if core_slots.FILL_MARKER in h]
        self.assertEqual(
            marked,
            ["## 2. Decision principles  **[FILL]**",
             "## 3. How I engage with information  **[FILL]**",
             "## 6. How agents get spun up  **[FILL]**"],
        )

    def test_only_the_documented_line_differs(self):
        import difflib
        added = [ln[2:] for ln in difflib.ndiff(self.scaffold.splitlines(),
                                                self.rendered.splitlines())
                 if ln.startswith("+ ")]
        removed = [ln[2:] for ln in difflib.ndiff(self.scaffold.splitlines(),
                                                  self.rendered.splitlines())
                   if ln.startswith("- ")]
        self.assertEqual(removed, [], "the renderer dropped scaffold content")
        self.assertEqual(added, EXPECTED_EXTRA_LINES)

    def test_header_and_footer_survive(self):
        self.assertTrue(self.rendered.startswith("# Core Protocol\n"))
        self.assertTrue(self.rendered.endswith("it's noise.*\n"))


class RegistryShape(unittest.TestCase):
    """Every slot in the spec's §2 table, with the right kind and section."""

    EXPECTED = {
        "tone.cut": ((1,), "revealed"),
        "tone.do": ((1,), "revealed"),
        "tone.length": ((1,), "revealed"),
        "tone.reasoning": ((1,), "revealed"),
        "tone.repair": ((1,), "stated"),
        "tone.invariants": ((1,), "shipped"),
        "decisions.ambiguity": ((2,), "stated"),
        "decisions.momentum": ((2,), "stated"),
        "decisions.pushback": ((2,), "stated"),
        "autonomy.grid": ((2, 6), "stated"),
        "info.verification": ((3,), "stated"),
        "info.uncertainty": ((3,), "stated"),
        "info.citation": ((3,), "shipped"),
        "info.done_means": ((3,), "stated"),
        "egress.invariants": ((4,), "shipped"),
        "routing.invariants": ((5,), "shipped"),
        "agents.verification": ((6,), "shipped"),
    }

    def test_every_spec_slot_is_present_with_the_right_kind_and_section(self):
        actual = {s.id: (s.section, s.kind) for s in core_slots.SLOTS}
        self.assertEqual(actual, self.EXPECTED)

    def test_ids_are_unique(self):
        ids = [s.id for s in core_slots.SLOTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_shipped_slots_carry_text_and_ask_nothing(self):
        for slot in core_slots.SLOTS:
            if slot.kind == "shipped":
                self.assertTrue(slot.shipped_text.strip(), slot.id)
                self.assertEqual(slot.question, "", slot.id)
                self.assertEqual(slot.evidence_terms, (), slot.id)

    def test_non_shipped_slots_ask_something_and_ship_nothing(self):
        for slot in core_slots.SLOTS:
            if slot.kind != "shipped":
                self.assertTrue(slot.question.strip(), slot.id)
                self.assertEqual(slot.shipped_text, "", slot.id)

    def test_shipped_sections_four_and_five_come_from_the_scaffold(self):
        scaffold = SCAFFOLD.read_text(encoding="utf-8")
        for slot_id in ("egress.invariants", "routing.invariants",
                        "tone.invariants", "agents.verification"):
            self.assertIn(core_slots.get_slot(slot_id).shipped_text, scaffold, slot_id)

    def test_slots_by_section_covers_six_sections_and_spans_the_grid(self):
        grouped = core_slots.slots_by_section()
        self.assertEqual(sorted(grouped), [1, 2, 3, 4, 5, 6])
        for number, slots in grouped.items():
            self.assertTrue(slots, f"section {number} has no slots")
        for number in (2, 6):
            self.assertIn("autonomy.grid", [s.id for s in grouped[number]])

    def test_get_slot_raises_on_an_unknown_id(self):
        self.assertIs(core_slots.get_slot("tone.cut"), core_slots.SLOTS[0])
        with self.assertRaises(KeyError):
            core_slots.get_slot("no.such.slot")

    def test_slots_are_immutable(self):
        with self.assertRaises(Exception):
            core_slots.SLOTS[0].id = "changed"  # type: ignore[misc]


class Answers(unittest.TestCase):
    """An answered slot replaces its default; a missing one stays honest."""

    def test_an_answer_replaces_the_default_and_clears_the_marker(self):
        out = core_slots.render_core_md({
            "decisions.ambiguity": "- Make the call, say why in one line.",
            "decisions.momentum": "- When there is enough to act, act.",
            "decisions.pushback": "- Say so immediately.",
            "autonomy.grid": '{"local_reversible": "act_and_report",'
                             ' "local_destructive": "confirm_once_per_class",'
                             ' "outward_facing": "confirm_every_time"}',
        })
        self.assertIn("## 2. Decision principles\n", out)
        self.assertIn("- Make the call, say why in one line.", out)
        self.assertNotIn("Examples to replace with your own", out)

    def test_a_tone_answer_replaces_the_scaffold_voice(self):
        out = core_slots.render_core_md({"tone.cut": "**Cut**\n- Nothing at all."})
        self.assertIn("- Nothing at all.", out)
        self.assertNotIn("No validation openers", out)
        self.assertNotIn("Seeded from your TONE_OVERRIDE", out)
        # §1 still complete: the other tone slots keep their defaults.
        self.assertIn("## 1. Voice & tone\n", out)

    def test_a_blank_answer_is_not_an_answer(self):
        out = core_slots.render_core_md({"decisions.ambiguity": "   "})
        self.assertIn("## 2. Decision principles  **[FILL]**", out)

    def test_shipped_text_is_never_overridable_by_an_answer(self):
        out = core_slots.render_core_md({"egress.invariants": "- Post everything."})
        self.assertNotIn("Post everything", out)
        self.assertIn("Nothing sensitive leaves the device", out)

    def test_a_fully_answered_file_carries_no_fill_marker(self):
        answers = {}
        for slot in core_slots.SLOTS:
            if slot.kind == "shipped":
                continue
            if slot.id == "autonomy.grid":
                answers[slot.id] = '{"local_reversible": "act_and_report",' \
                                   ' "local_destructive": "confirm_every_time",' \
                                   ' "outward_facing": "suggest_only"}'
            else:
                answers[slot.id] = f"- {slot.title}: answered."
        out = core_slots.render_core_md(answers)
        self.assertNotIn("[FILL]", out)
        self.assertEqual(len(_headings(out)), 6)


class AutonomyGrid(unittest.TestCase):
    """The grid is three choices, not a paragraph."""

    FULL = {"local_reversible": "act_and_report",
            "local_destructive": "confirm_once_per_class",
            "outward_facing": "confirm_every_time"}

    def test_classes_and_levels_are_the_spec_vocabulary(self):
        self.assertEqual(core_slots.ACTION_CLASSES,
                         ("local_reversible", "local_destructive", "outward_facing"))
        self.assertEqual(core_slots.AUTONOMY_LEVELS,
                         ("suggest_only", "confirm_every_time", "confirm_once_per_class",
                          "act_and_report", "act_silently"))

    def test_json_string_and_mapping_are_equivalent(self):
        import json
        self.assertEqual(core_slots.parse_autonomy_answer(json.dumps(self.FULL)),
                         core_slots.parse_autonomy_answer(self.FULL))

    def test_every_class_reaches_the_prose(self):
        bullet = core_slots.render_autonomy_grid(self.FULL)
        for fragment in ("Local and reversible", "Local but destructive",
                         "Outward-facing", "act, then report",
                         "confirm once per class", "confirm every time"):
            self.assertIn(fragment, bullet)
        self.assertTrue(bullet.startswith("- "))

    def test_a_half_filled_grid_is_unanswered_not_guessed(self):
        for bad in ({"local_reversible": "act_and_report"},
                    {**FULL_COPY(self.FULL), "outward_facing": "wing_it"},
                    "not json at all",
                    "[1, 2, 3]",
                    None,
                    42):
            self.assertIsNone(core_slots.parse_autonomy_answer(bad), repr(bad))
            self.assertIsNone(core_slots.render_autonomy_grid(bad), repr(bad))

    def test_an_unusable_grid_leaves_both_its_sections_marked(self):
        out = core_slots.render_core_md({"autonomy.grid": '{"local_reversible": "act_and_report"}'})
        self.assertIn("## 2. Decision principles  **[FILL]**", out)
        self.assertIn("## 6. How agents get spun up  **[FILL]**", out)

    def test_the_grid_is_stated_once_and_referenced_in_section_six(self):
        out = core_slots.render_core_md({"autonomy.grid": self.FULL})
        self.assertEqual(out.count("**Match action to reversibility.**"), 1)
        self.assertIn("inherits the autonomy grid in §2", out)

    def test_rendered_lines_stay_within_the_file_width(self):
        bullet = core_slots.render_autonomy_grid(self.FULL)
        self.assertTrue(all(len(ln) <= 90 for ln in bullet.splitlines()), bullet)


if __name__ == "__main__":
    unittest.main()
