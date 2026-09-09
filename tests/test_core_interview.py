"""Tests for the interview's retrieval half and its resume behaviour.

Pure stdlib (unittest). The harness repoints `~/.core` and `~/Knowledge` into a
temp dir before `gigabite` is imported, so nothing here can reach the user's real
protocol — which is the one file in this feature that must never be touched by a
test run.

    python3 -m unittest discover -s tests        (from the repo root)

The two loads-bearing behaviours: **nothing is written that the caller did not
approve**, and **a half-finished pass resumes rather than restarts**. The rest is
shape.
"""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
from _harness import TempRoot  # noqa: E402  (must precede any gigabite import)

from gigabite import cli, config  # noqa: E402
from gigabite.features import core_interview, core_slots  # noqa: E402


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


GRID = {
    "local_reversible": "act_and_report",
    "local_destructive": "confirm_once_per_class",
    "outward_facing": "confirm_every_time",
}


class ThePlan(TempRoot):
    """What the slash command reads before it asks anything."""

    def test_a_fresh_machine_has_every_stated_section_outstanding(self):
        plan = core_interview.plan()
        self.assertFalse(plan["core_exists"])
        # The `[FILL]` sections of the scaffold, and only those.
        self.assertEqual(
            set(plan["ask_next"]),
            {"decisions.ambiguity", "decisions.momentum", "decisions.pushback",
             "autonomy.grid", "info.verification", "info.uncertainty",
             "info.done_means"},
        )
        self.assertEqual(plan["remaining_required"], 7)

    def test_shipped_slots_are_never_asked_about(self):
        plan = core_interview.plan()
        shipped = {s.id for s in core_slots.SLOTS if s.kind == "shipped"}
        asked = set(plan["ask_next"]) | set(plan["ask_optional"])
        self.assertFalse(shipped & asked)
        for entry in plan["slots"]:
            if entry["id"] in shipped:
                self.assertEqual(entry["status"], "shipped")

    def test_every_required_slot_carries_something_to_offer(self):
        """A default the user can accept, not an open question (§8)."""
        plan = core_interview.plan()
        by_id = {s["id"]: s for s in plan["slots"]}
        for slot_id in plan["ask_next"]:
            entry = by_id[slot_id]
            self.assertTrue(entry["question"], slot_id)
            if entry["answer_shape"] == "grid":
                self.assertEqual(len(entry["action_classes"]), 3)
                for cls in entry["action_classes"]:
                    self.assertIn(cls["suggested"], core_slots.AUTONOMY_LEVELS)
                    self.assertTrue(cls["ask"])
            else:
                self.assertGreaterEqual(len(entry["options"]), 2, slot_id)
                for option in entry["options"]:
                    self.assertTrue(option["label"] and option["body"])

    def test_the_autonomy_grid_is_three_choices_not_an_essay(self):
        plan = core_interview.plan()
        grid = next(s for s in plan["slots"] if s["id"] == "autonomy.grid")
        self.assertEqual([c["action_class"] for c in grid["action_classes"]],
                         list(core_slots.ACTION_CLASSES))
        self.assertEqual(set(grid["levels"]), set(core_slots.AUTONOMY_LEVELS))

    def test_reading_the_plan_writes_nothing(self):
        core_interview.plan()
        self.assertFalse(config.CORE_FILE.exists())
        self.assertFalse(core_interview.answers_path().exists())


class ApplyingAnswers(TempRoot):

    def test_a_fresh_user_reaches_a_personal_protocol_with_no_hand_editing(self):
        answers = {
            "decisions.ambiguity": "- **Make the call.**",
            "decisions.momentum": "- **Act.**",
            "decisions.pushback": "- **Say it immediately.**",
            "autonomy.grid": GRID,
            "info.verification": "- **Verify first.**",
            "info.uncertainty": "- **Flag it once.**",
            "info.done_means": "- **Observed working.**",
        }
        result = core_interview.apply_answers(answers)
        text = config.CORE_FILE.read_text(encoding="utf-8")
        self.assertNotIn(core_slots.FILL_MARKER, text)
        self.assertFalse(result["has_fill_markers"])
        self.assertEqual(result["remaining_required"], [])
        self.assertIn("**Make the call.**", text)
        # The grid renders as prose, not as the JSON it was answered with.
        self.assertNotIn("local_reversible", text)
        self.assertIn("act, then report", text)

    def test_stopping_halfway_leaves_an_honest_file(self):
        core_interview.apply_answers({"decisions.momentum": "- **Act.**"})
        text = config.CORE_FILE.read_text(encoding="utf-8")
        self.assertIn("- **Act.**", text)
        self.assertIn(core_slots.FILL_MARKER, text)
        # Answered sections are not marked; unanswered ones are.
        self.assertIn("## 3. How I engage with information  " + core_slots.FILL_MARKER,
                      text)

    def test_a_second_run_resumes_rather_than_restarting(self):
        core_interview.apply_answers({"decisions.momentum": "- **Act.**"})
        plan = core_interview.plan()
        self.assertNotIn("decisions.momentum", plan["ask_next"])
        self.assertEqual(plan["remaining_required"], 6)

        core_interview.apply_answers({"decisions.ambiguity": "- **Make the call.**"})
        text = config.CORE_FILE.read_text(encoding="utf-8")
        # The earlier answer survived a re-render it was not resent for.
        self.assertIn("- **Act.**", text)
        self.assertIn("- **Make the call.**", text)

    def test_a_declined_slot_is_not_asked_again_and_stays_fill(self):
        core_interview.apply_answers({"decisions.momentum": "- **Act.**"},
                                     declined=["info.done_means"])
        plan = core_interview.plan()
        self.assertNotIn("info.done_means", plan["ask_next"])
        self.assertIn("info.done_means", plan["declined"])
        self.assertIn(core_slots.FILL_MARKER,
                      config.CORE_FILE.read_text(encoding="utf-8"))

    def test_approving_nothing_writes_nothing(self):
        result = core_interview.apply_answers({})
        self.assertFalse(result["written"])
        self.assertFalse(config.CORE_FILE.exists())

    def test_an_existing_protocol_is_set_aside_never_overwritten(self):
        config.CORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CORE_FILE.write_text("# hand-tuned\nmy own rules\n", encoding="utf-8")
        core_interview.apply_answers({"decisions.momentum": "- **Act.**"})
        kept = list(config.ORIGINALS_DIR.glob("replaced-*-core.md"))
        self.assertEqual(len(kept), 1)
        self.assertIn("my own rules", kept[0].read_text(encoding="utf-8"))

    def test_an_unknown_slot_is_reported_and_ignored(self):
        result = core_interview.apply_answers({"not.a.slot": "- nope",
                                               "decisions.momentum": "- **Act.**"})
        self.assertEqual(result["unknown_slots"], ["not.a.slot"])
        self.assertNotIn("nope", config.CORE_FILE.read_text(encoding="utf-8"))

    def test_a_corrupt_answers_record_degrades_to_asking_again(self):
        core_interview.answers_path().parent.mkdir(parents=True, exist_ok=True)
        core_interview.answers_path().write_text("{not json", encoding="utf-8")
        plan = core_interview.plan()
        self.assertEqual(plan["remaining_required"], 7)


class TheCli(TempRoot):

    def test_interview_json_is_what_the_command_consumes(self):
        code, out = run("core", "interview", "--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertIn("ask_next", plan)
        self.assertIn("slots", plan)

    def test_interview_plain_output_names_the_command_to_run(self):
        code, out = run("core", "interview")
        self.assertEqual(code, 0)
        self.assertIn("/core-setup", out)

    def test_apply_reads_approved_answers_from_a_file(self):
        payload = self.core / "approved.json"
        payload.write_text(json.dumps({
            "answers": {"decisions.momentum": "- **Act.**", "autonomy.grid": GRID},
        }), encoding="utf-8")
        code, out = run("core", "apply", "--file", str(payload))
        self.assertEqual(code, 0)
        self.assertIn("- **Act.**", config.CORE_FILE.read_text(encoding="utf-8"))
        self.assertIn("resumes", out)

    def test_apply_refuses_malformed_input_without_writing(self):
        payload = self.core / "bad.json"
        payload.write_text("{{{", encoding="utf-8")
        code, _ = run("core", "apply", "--file", str(payload))
        self.assertEqual(code, 1)
        self.assertFalse(config.CORE_FILE.exists())

    def test_apply_with_no_answers_is_a_no_op(self):
        payload = self.core / "empty.json"
        payload.write_text(json.dumps({"answers": {}}), encoding="utf-8")
        code, out = run("core", "apply", "--file", str(payload))
        self.assertEqual(code, 0)
        self.assertIn("nothing was written", out)
        self.assertFalse(config.CORE_FILE.exists())


class TheSlashCommand(unittest.TestCase):
    """The installed command file itself — it is the interview's actual text."""

    PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "install", "claude-commands", "core-setup.md")

    def setUp(self):
        with open(self.PATH, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_it_is_installed_like_every_other_command(self):
        self.assertIn("__GIGABITE_BIN__", self.text)
        self.assertIn("gigabite:managed", self.text)
        self.assertTrue(self.text.startswith("---\ndescription:"))

    def test_it_writes_only_through_the_gated_command(self):
        self.assertIn("core apply", self.text)
        for forbidden in ("Write(", "Edit(", "> ~/.core/core.md"):
            self.assertNotIn(forbidden, self.text)

    def test_it_is_asked_one_question_at_a_time(self):
        self.assertIn("one question per message", self.text)


if __name__ == "__main__":
    unittest.main()
