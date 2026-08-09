"""Tests for the autonomy ledger: runs, steps, decisions, blockers, kill switch.

Pure stdlib (unittest). No network, no writes to the real home directory.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import os
import tempfile
import unittest
from pathlib import Path

# Redirect all stores into a temp dir BEFORE importing the package.
_TMP = tempfile.mkdtemp(prefix="gigabite-ledger-test-")
os.environ["GIGABITE_CORE_DIR"] = str(Path(_TMP) / "core")
os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(Path(_TMP) / "knowledge")

from gigabite import cli, config  # noqa: E402
from gigabite.features import ledger as L  # noqa: E402


class LedgerTestCase(unittest.TestCase):
    """Each test gets its own knowledge root, so the STOP file can't leak."""

    def setUp(self):
        # Restored in tearDown: these are module globals, and leaving them
        # pointed at a temp dir breaks every test module that runs after this
        # one under `unittest discover`.
        self._saved = (
            os.environ.get("GIGABITE_KNOWLEDGE_DIR"),
            config.KNOWLEDGE_DIR,
            config.MACHINE_DIR,
        )
        self.root = Path(tempfile.mkdtemp(prefix="gb-ledger-"))
        os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(self.root)
        config.KNOWLEDGE_DIR = self.root
        config.MACHINE_DIR = config.machine_dir()
        self.led = L.Ledger.open(self.root / ".gigabite" / "index" / "ledger.db")

    def tearDown(self):
        self.led.close()
        L.release_stop()
        env, knowledge, machine = self._saved
        if env is None:
            os.environ.pop("GIGABITE_KNOWLEDGE_DIR", None)
        else:
            os.environ["GIGABITE_KNOWLEDGE_DIR"] = env
        config.KNOWLEDGE_DIR = knowledge
        config.MACHINE_DIR = machine


class TestRuns(LedgerTestCase):
    def test_start_run_records_the_mission(self):
        run = self.led.start_run(
            "scan the Turkish market",
            done_definition="a one-pager with three named competitors",
            authority="supervised",
            baseline_minutes=180,
        )
        self.assertTrue(run.run_id.startswith("run_"))
        self.assertEqual(run.status, "running")
        self.assertEqual(run.version, 1)
        self.assertTrue(run.started_utc)

    def test_run_ids_are_unique(self):
        ids = {self.led.start_run(f"goal {i}").run_id for i in range(25)}
        self.assertEqual(len(ids), 25)

    def test_goal_is_required(self):
        with self.assertRaises(L.LedgerError):
            self.led.start_run("   ")

    def test_unknown_authority_is_refused(self):
        with self.assertRaises(L.LedgerError):
            self.led.start_run("x", authority="god-mode")

    def test_unknown_run_raises(self):
        with self.assertRaises(L.UnknownRun):
            self.led.summary("run_nope")

    def test_survives_a_new_connection(self):
        """The whole point: state outlives the process that made it."""
        rid = self.led.start_run("outlive me", baseline_minutes=60).run_id
        self.led.add_step(rid, "research", summary="find sources")
        self.led.close()

        reopened = L.Ledger.open(self.root / ".gigabite" / "index" / "ledger.db")
        self.addCleanup(reopened.close)
        run = reopened.get_run(rid)
        self.assertIsNotNone(run)
        self.assertEqual(run.goal, "outlive me")
        self.assertEqual(len(reopened.steps(rid)), 1)


class TestVersionGuard(LedgerTestCase):
    def test_update_bumps_version(self):
        rid = self.led.start_run("g").run_id
        run = self.led.update_run(rid, status="blocked")
        self.assertEqual(run.version, 2)
        self.assertEqual(run.status, "blocked")

    def test_stale_version_is_refused(self):
        rid = self.led.start_run("g").run_id
        self.led.update_run(rid, status="blocked")          # now at v2
        with self.assertRaises(L.StaleVersion):
            self.led.update_run(rid, expected_version=1, status="done")
    def test_unknown_field_is_refused(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(L.LedgerError):
            self.led.update_run(rid, sneaky="value")

    def test_unknown_status_is_refused(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(L.LedgerError):
            self.led.update_run(rid, status="vibing")


class TestSteps(LedgerTestCase):
    def test_steps_are_numbered_and_ordered(self):
        rid = self.led.start_run("g").run_id
        self.assertEqual(self.led.add_step(rid, "search"), 1)
        self.assertEqual(self.led.add_step(rid, "read"), 2)
        self.assertEqual([s["kind"] for s in self.led.steps(rid)], ["search", "read"])

    def test_step_lifecycle(self):
        rid = self.led.start_run("g").run_id
        seq = self.led.add_step(rid, "search", contract={"returns": "list[url]"})
        self.led.start_step(rid, seq)
        self.led.finish_step(rid, seq, output={"urls": ["a", "b"]})
        step = self.led.steps(rid)[0]
        self.assertEqual(step["status"], "done")
        self.assertEqual(step["attempts"], 1)
        self.assertIn("urls", step["output_json"])

    def test_attempts_accumulate_across_retries(self):
        rid = self.led.start_run("g").run_id
        seq = self.led.add_step(rid, "fetch")
        self.led.start_step(rid, seq)
        self.assertEqual(self.led.fail_step(rid, seq, "timeout"), 1)
        self.led.start_step(rid, seq)
        self.assertEqual(self.led.fail_step(rid, seq, "timeout again"), 2)

    def test_step_counts_in_summary(self):
        rid = self.led.start_run("g").run_id
        a = self.led.add_step(rid, "one")
        self.led.add_step(rid, "two")
        self.led.start_step(rid, a)
        self.led.finish_step(rid, a)
        counts = self.led.summary(rid)["step_counts"]
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["pending"], 1)


class TestDecisions(LedgerTestCase):
    def test_decision_records_the_road_not_taken(self):
        rid = self.led.start_run("g").run_id
        self.led.record_decision(
            rid,
            question="which market first",
            chosen="Turkey",
            why="largest addressable base of the three, and we have a local partner",
            rejected=["Poland", "Greece"],
        )
        d = self.led.decisions(rid)[0]
        self.assertEqual(d["chosen"], "Turkey")
        self.assertIn("Poland", d["rejected_json"])

    def test_why_is_mandatory(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(L.LedgerError):
            self.led.record_decision(rid, "q", "a", "   ")


class TestBlockers(LedgerTestCase):
    def test_blocker_must_say_what_would_unblock_it(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(L.LedgerError):
            self.led.open_blocker(rid, "paywall", "report is behind a paywall", "  ")

    def test_open_and_resolve(self):
        rid = self.led.start_run("g").run_id
        bid = self.led.open_blocker(
            rid, "paywall",
            "the Statista report on Turkish e-commerce is paywalled",
            "a Statista seat, or an equivalent free source",
        )
        self.assertEqual(len(self.led.blockers()), 1)
        self.led.resolve_blocker(bid)
        self.assertEqual(len(self.led.blockers()), 0)
        self.assertEqual(len(self.led.blockers(status="")), 1)

    def test_blocker_does_not_stop_the_run(self):
        """Park and continue — a blocker is not a halt (docs/AUTONOMY.md §6)."""
        rid = self.led.start_run("g").run_id
        self.led.open_blocker(rid, "auth", "no Jira token", "authenticate Jira once")
        self.assertEqual(self.led.get_run(rid).status, "running")
        self.assertEqual(self.led.add_step(rid, "carry on"), 1)


class TestKillSwitch(LedgerTestCase):
    def test_stop_halts_everything_in_flight(self):
        a = self.led.start_run("a").run_id
        b = self.led.start_run("b").run_id
        halted = self.led.halt_all("hands off")
        self.assertCountEqual(halted, [a, b])
        self.assertEqual(self.led.get_run(a).status, "halted")
        self.assertTrue(L.halted())

    def test_nothing_new_starts_while_engaged(self):
        L.engage_stop("no")
        with self.assertRaises(L.Halted):
            self.led.start_run("sneaky")
        rid_free = None
        L.release_stop()
        rid_free = self.led.start_run("fine").run_id
        self.assertIsNotNone(rid_free)

    def test_engaged_switch_blocks_steps_and_decisions(self):
        rid = self.led.start_run("g").run_id
        seq = self.led.add_step(rid, "s")
        L.engage_stop()
        with self.assertRaises(L.Halted):
            self.led.add_step(rid, "another")
        with self.assertRaises(L.Halted):
            self.led.start_step(rid, seq)
        with self.assertRaises(L.Halted):
            self.led.record_decision(rid, "q", "a", "because")

    def test_halting_works_while_the_switch_is_engaged(self):
        """Halting must not itself be blocked by the halt."""
        rid = self.led.start_run("g").run_id
        L.engage_stop()
        self.assertEqual(self.led.halt_run(rid, "stop").status, "halted")

    def test_release_does_not_restart_halted_runs(self):
        rid = self.led.start_run("g").run_id
        self.led.halt_all("stop")
        self.assertTrue(L.release_stop())
        self.assertEqual(self.led.get_run(rid).status, "halted")
        self.assertFalse(L.release_stop())

    def test_audit_still_writes_while_halted(self):
        rid = self.led.start_run("g").run_id
        L.engage_stop()
        self.led.audit(rid, "code", "push", "refused")
        self.assertTrue(any(r["action"] == "push" for r in self.led.audit_trail()))


class TestCostAccounting(LedgerTestCase):
    def test_hours_saved_subtracts_attention_not_wall_time(self):
        rid = self.led.start_run("g", baseline_minutes=180).run_id
        self.led.add_human_time(rid, 15 * 60)          # 15 minutes in gates
        s = self.led.summary(rid)
        self.assertEqual(s["human_touch_minutes"], 15.0)
        self.assertAlmostEqual(s["hours_saved"], 2.75, places=3)

    def test_attention_accumulates(self):
        rid = self.led.start_run("g", baseline_minutes=60).run_id
        self.led.add_human_time(rid, 120)
        self.led.add_human_time(rid, 180)
        self.assertEqual(self.led.summary(rid)["human_touch_minutes"], 5.0)

    def test_no_baseline_means_unmeasured_not_zero(self):
        rid = self.led.start_run("g").run_id
        self.assertIsNone(self.led.summary(rid)["hours_saved"])

    def test_negative_attention_is_refused(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(L.LedgerError):
            self.led.add_human_time(rid, -1)


class TestAuditTrail(LedgerTestCase):
    def test_run_lifecycle_is_audited(self):
        rid = self.led.start_run("g").run_id
        self.led.finish_run(rid)
        actions = [r["action"] for r in self.led.audit_trail(run_id=rid)]
        self.assertIn("start", actions)
        self.assertIn("finish", actions)

    def test_trail_is_filterable_by_run(self):
        a = self.led.start_run("a").run_id
        self.led.start_run("b")
        self.assertTrue(all(r["run_id"] == a for r in self.led.audit_trail(run_id=a)))


class TestCli(LedgerTestCase):
    def test_run_start_list_and_show(self):
        self.assertEqual(cli.main(["run", "start", "ship it", "--baseline", "90"]), 0)
        self.assertEqual(cli.main(["run", "list"]), 0)
        rid = self.led.list_runs()[0].run_id
        self.assertEqual(cli.main(["run", "show", rid]), 0)
        self.assertEqual(cli.main(["run", "show", rid, "--json"]), 0)

    def test_show_unknown_run_exits_nonzero(self):
        self.assertEqual(cli.main(["run", "show", "run_nope"]), 1)

    def test_stop_and_resume(self):
        cli.main(["run", "start", "a"])
        self.assertEqual(cli.main(["run", "stop", "--reason", "testing"]), 0)
        self.assertTrue(L.halted())
        self.assertEqual(cli.main(["run", "start", "b"]), 1)   # refused while engaged
        self.assertEqual(cli.main(["run", "resume"]), 0)
        self.assertEqual(cli.main(["run", "start", "b"]), 0)

    def test_touch_and_finish(self):
        cli.main(["run", "start", "a", "--baseline", "60"])
        rid = self.led.list_runs()[0].run_id
        self.assertEqual(cli.main(["run", "touch", rid, "--minutes", "5"]), 0)
        self.assertEqual(cli.main(["run", "finish", rid]), 0)
        self.assertEqual(self.led.get_run(rid).status, "done")

    def test_blockers_command(self):
        cli.main(["run", "start", "a"])
        rid = self.led.list_runs()[0].run_id
        self.led.open_blocker(rid, "paywall", "gated report", "a seat, or a free equivalent")
        self.assertEqual(cli.main(["run", "blockers"]), 0)
        self.assertEqual(cli.main(["audit"]), 0)

    def test_bare_run_prints_help_without_crashing(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["run"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
