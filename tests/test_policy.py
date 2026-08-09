"""Tests for the policy engine: action classes, verdicts, grants, hard refusals.

The negative cases carry the weight here. A guard that lets the right things
through proves very little; a guard that cannot be talked into letting the wrong
things through is the whole product.

Pure stdlib (unittest). No network, no writes to the real home directory.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="gigabite-policy-test-")
os.environ["GIGABITE_CORE_DIR"] = str(Path(_TMP) / "core")
os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(Path(_TMP) / "knowledge")

from gigabite import cli, config  # noqa: E402
from gigabite.features import ledger as L  # noqa: E402
from gigabite.features import policy as P  # noqa: E402


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = (
            os.environ.get("GIGABITE_KNOWLEDGE_DIR"),
            os.environ.get("GIGABITE_CORE_DIR"),
            config.KNOWLEDGE_DIR,
            config.MACHINE_DIR,
            config.CORE_DIR,
        )
        self.root = Path(tempfile.mkdtemp(prefix="gb-policy-"))
        self.core = self.root / "core"
        self.core.mkdir(parents=True, exist_ok=True)
        os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(self.root)
        os.environ["GIGABITE_CORE_DIR"] = str(self.core)
        config.KNOWLEDGE_DIR = self.root
        config.MACHINE_DIR = config.machine_dir()
        config.CORE_DIR = self.core
        self.led = L.Ledger.open(self.root / ".gigabite" / "index" / "ledger.db")

    def tearDown(self):
        self.led.close()
        L.release_stop()
        env_k, env_c, knowledge, machine, core = self._saved
        for var, val in (("GIGABITE_KNOWLEDGE_DIR", env_k), ("GIGABITE_CORE_DIR", env_c)):
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        config.KNOWLEDGE_DIR = knowledge
        config.MACHINE_DIR = machine
        config.CORE_DIR = core

    def write_policy(self, rules: dict) -> Path:
        path = P.policy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "rules": rules}), encoding="utf-8")
        return path


class TestDefaults(PolicyTestCase):
    def test_shipped_defaults_match_the_design_table(self):
        self.assertEqual(P.decide("read").verdict, P.ALLOW)
        self.assertEqual(P.decide("local-write").verdict, P.ALLOW)
        self.assertEqual(P.decide("code-working").verdict, P.ALLOW)
        self.assertEqual(P.decide("code-publishing").verdict, P.APPROVE)
        self.assertEqual(P.decide("third-party-create").verdict, P.APPROVE)
        self.assertEqual(P.decide("outbound-comms").verdict, P.APPROVE)
        self.assertEqual(P.decide("spend").verdict, P.APPROVE)
        self.assertEqual(P.decide("credentials").verdict, P.USER_ONLY)
        self.assertEqual(P.decide("infra-security").verdict, P.NEVER)

    def test_every_class_carries_a_reason(self):
        for name in P.ACTION_CLASSES:
            self.assertTrue(P.decide(name).why.strip(), name)

    def test_missing_policy_file_is_not_an_error(self):
        self.assertFalse(P.policy_path().exists())
        self.assertEqual(P.decide("read").verdict, P.ALLOW)


class TestFailClosed(PolicyTestCase):
    def test_unknown_action_class_requires_approval_not_allow(self):
        d = P.decide("launch-the-missiles")
        self.assertEqual(d.verdict, P.APPROVE)
        self.assertEqual(d.source, "unknown-class")

    def test_unknown_class_is_never_silently_allowed_even_with_a_grant(self):
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "launch-the-missiles")
        d = P.authorize("launch-the-missiles", run_id=rid, led=self.led)
        self.assertNotEqual(d.verdict, P.ALLOW)

    def test_malformed_policy_file_raises_rather_than_falling_back(self):
        P.policy_path().parent.mkdir(parents=True, exist_ok=True)
        P.policy_path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(P.PolicyError):
            P.load()

    def test_unknown_class_in_the_file_is_rejected(self):
        self.write_policy({"nonsense": {"verdict": "allow"}})
        with self.assertRaises(P.PolicyError):
            P.load()

    def test_bad_verdict_in_the_file_is_rejected(self):
        self.write_policy({"spend": {"verdict": "sure why not"}})
        with self.assertRaises(P.PolicyError):
            P.load()


class TestHardRefusal(PolicyTestCase):
    def test_infra_security_is_refused_by_default(self):
        self.assertEqual(P.decide("infra-security").verdict, P.NEVER)

    def test_the_policy_file_cannot_relax_it(self):
        self.write_policy({"infra-security": {"verdict": "allow"}})
        with self.assertRaises(P.PolicyError):
            P.load()

    def test_a_forged_grant_cannot_relax_it(self):
        """Grants are storage, not permission — the decision ignores them here."""
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "infra-security", "I really need this")
        d = P.authorize("infra-security", "open port 22", run_id=rid, led=self.led)
        self.assertEqual(d.verdict, P.NEVER)
        self.assertEqual(d.source, "code")

    def test_full_authority_cannot_relax_it(self):
        rid = self.led.start_run("g", authority="full").run_id
        d = P.authorize("infra-security", run_id=rid, led=self.led)
        self.assertEqual(d.verdict, P.NEVER)

    def test_guard_raises_refused(self):
        with self.assertRaises(P.Refused):
            P.guard("infra-security", "disable the firewall")


class TestUserConfiguration(PolicyTestCase):
    def test_a_user_can_narrow_a_class(self):
        self.write_policy({"code-working": {"verdict": "approve", "why": "not on this box"}})
        d = P.decide("code-working", rules=P.load())
        self.assertEqual(d.verdict, P.APPROVE)
        self.assertEqual(d.why, "not on this box")

    def test_a_user_can_widen_a_class(self):
        self.write_policy({"third-party-create": {"verdict": "allow", "why": "my own board"}})
        self.assertEqual(P.decide("third-party-create", rules=P.load()).verdict, P.ALLOW)

    def test_unlisted_classes_keep_their_defaults(self):
        self.write_policy({"spend": {"verdict": "never"}})
        rules = P.load()
        self.assertEqual(P.decide("spend", rules=rules).verdict, P.NEVER)
        self.assertEqual(P.decide("read", rules=rules).verdict, P.ALLOW)

    def test_write_default_does_not_clobber_an_existing_file(self):
        path = self.write_policy({"spend": {"verdict": "never"}})
        P.write_default()
        self.assertNotIn("_comment", json.loads(path.read_text()))
        P.write_default(overwrite=True)
        self.assertIn("_comment", json.loads(path.read_text()))

    def test_the_written_default_round_trips(self):
        P.write_default()
        rules = P.load()
        for name, (verdict, _why) in P.DEFAULT_RULES.items():
            self.assertEqual(rules[name]["verdict"], verdict, name)


class TestGrants(PolicyTestCase):
    def test_a_grant_satisfies_an_approve(self):
        rid = self.led.start_run("g").run_id
        self.assertEqual(P.authorize("third-party-create", "TR-1", run_id=rid,
                                     led=self.led).verdict, P.APPROVE)
        self.led.grant(rid, "third-party-create", "the Turkey scan tickets")
        d = P.authorize("third-party-create", "TR-1", run_id=rid, led=self.led)
        self.assertEqual(d.verdict, P.ALLOW)
        self.assertEqual(d.source, "grant")

    def test_one_grant_covers_the_whole_class_for_the_run(self):
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "third-party-create")
        for n in range(5):
            self.assertTrue(P.authorize("third-party-create", f"TR-{n}",
                                        run_id=rid, led=self.led).allowed)

    def test_a_grant_does_not_leak_to_another_run(self):
        a = self.led.start_run("a").run_id
        b = self.led.start_run("b").run_id
        self.led.grant(a, "third-party-create")
        self.assertTrue(P.authorize("third-party-create", run_id=a, led=self.led).allowed)
        self.assertFalse(P.authorize("third-party-create", run_id=b, led=self.led).allowed)

    def test_a_grant_does_not_leak_to_another_class(self):
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "third-party-create")
        self.assertFalse(P.authorize("outbound-comms", run_id=rid, led=self.led).allowed)

    def test_revoking_takes_effect_immediately(self):
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "spend")
        self.assertTrue(P.authorize("spend", run_id=rid, led=self.led).allowed)
        self.led.revoke_grant(rid, "spend")
        self.assertFalse(P.authorize("spend", run_id=rid, led=self.led).allowed)

    def test_a_grant_cannot_upgrade_user_only(self):
        """Batching approval must not become 'the agent may handle credentials'."""
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "credentials")
        d = P.authorize("credentials", "jira oauth", run_id=rid, led=self.led)
        self.assertEqual(d.verdict, P.USER_ONLY)


class TestAuthorityCeiling(PolicyTestCase):
    def test_passive_permits_only_reads(self):
        rid = self.led.start_run("g", authority="passive").run_id
        self.assertTrue(P.authorize("read", run_id=rid, led=self.led).allowed)
        self.assertEqual(P.authorize("local-write", run_id=rid, led=self.led).verdict, P.NEVER)

    def test_advisory_permits_reads_and_local_writes(self):
        rid = self.led.start_run("g", authority="advisory").run_id
        self.assertTrue(P.authorize("local-write", run_id=rid, led=self.led).allowed)
        self.assertEqual(P.authorize("code-working", run_id=rid, led=self.led).verdict, P.NEVER)

    def test_the_ceiling_beats_a_grant(self):
        rid = self.led.start_run("g", authority="passive").run_id
        self.led.grant(rid, "code-working")
        self.assertEqual(P.authorize("code-working", run_id=rid, led=self.led).verdict, P.NEVER)

    def test_supervised_imposes_nothing_extra(self):
        rid = self.led.start_run("g", authority="supervised").run_id
        self.assertTrue(P.authorize("code-working", run_id=rid, led=self.led).allowed)


class TestAuditTrail(PolicyTestCase):
    def test_a_refusal_is_audited(self):
        """The done-condition: it cannot proceed without a trace either way."""
        rid = self.led.start_run("g").run_id
        P.authorize("third-party-create", "create TR-1", run_id=rid, led=self.led)
        trail = self.led.audit_trail(run_id=rid)
        entry = next(r for r in trail if r["action"] == "create TR-1")
        self.assertEqual(entry["disposition"], P.APPROVE)
        self.assertEqual(entry["action_class"], "third-party-create")

    def test_an_allow_is_audited_too(self):
        rid = self.led.start_run("g").run_id
        P.authorize("read", "search the corpus", run_id=rid, led=self.led)
        entry = next(r for r in self.led.audit_trail(run_id=rid)
                     if r["action"] == "search the corpus")
        self.assertEqual(entry["disposition"], P.ALLOW)

    def test_the_reason_is_recorded_not_just_the_verdict(self):
        rid = self.led.start_run("g").run_id
        P.authorize("infra-security", "open a port", run_id=rid, led=self.led)
        entry = next(r for r in self.led.audit_trail(run_id=rid) if r["action"] == "open a port")
        self.assertIn("why", json.loads(entry["detail_json"]))

    def test_context_is_carried_into_the_trail(self):
        rid = self.led.start_run("g").run_id
        P.authorize("spend", "buy a seat", run_id=rid, led=self.led, context={"amount": "€99"})
        entry = next(r for r in self.led.audit_trail(run_id=rid) if r["action"] == "buy a seat")
        self.assertEqual(json.loads(entry["detail_json"])["context"]["amount"], "€99")


class TestGuard(PolicyTestCase):
    def test_guard_allows_an_allow(self):
        self.assertTrue(P.guard("read", "search").allowed)

    def test_guard_raises_approval_required(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(P.ApprovalRequired) as ctx:
            P.guard("outbound-comms", "email the vendor", run_id=rid, led=self.led)
        self.assertIn("gigabite policy grant", ctx.exception.decision.explain())

    def test_guard_passes_once_granted(self):
        rid = self.led.start_run("g").run_id
        self.led.grant(rid, "outbound-comms")
        self.assertTrue(P.guard("outbound-comms", "email", run_id=rid, led=self.led).allowed)

    def test_guard_refuses_user_only(self):
        with self.assertRaises(P.Refused):
            P.guard("credentials", "type the token")


class TestLedgerSchema(PolicyTestCase):
    def test_grants_table_is_additive_on_an_existing_ledger(self):
        db = self.root / ".gigabite" / "index" / "ledger.db"
        second = L.Ledger.open(db)
        self.addCleanup(second.close)
        rid = second.start_run("g").run_id
        second.grant(rid, "spend")
        self.assertTrue(second.has_grant(rid, "spend"))

    def test_a_newer_schema_is_refused_rather_than_guessed_at(self):
        db = self.root / "future.db"
        led = L.Ledger.open(db)
        led.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        led.conn.commit()
        led.close()
        with self.assertRaises(L.LedgerError):
            L.Ledger.open(db)


class TestCli(PolicyTestCase):
    def test_show_and_init(self):
        self.assertEqual(cli.main(["policy", "show"]), 0)
        self.assertEqual(cli.main(["policy", "init"]), 0)
        self.assertTrue(P.policy_path().exists())
        self.assertEqual(cli.main(["policy", "show"]), 0)

    def test_check_exit_code_reflects_the_verdict(self):
        self.assertEqual(cli.main(["policy", "check", "read"]), 0)
        self.assertEqual(cli.main(["policy", "check", "infra-security"]), 1)
        self.assertEqual(cli.main(["policy", "check", "third-party-create"]), 1)

    def test_grant_then_check_passes(self):
        cli.main(["run", "start", "g"])
        rid = self.led.list_runs()[0].run_id
        self.assertEqual(cli.main(["policy", "check", "spend", "--run", rid]), 1)
        self.assertEqual(cli.main(["policy", "grant", rid, "spend"]), 0)
        self.assertEqual(cli.main(["policy", "check", "spend", "--run", rid]), 0)
        self.assertEqual(cli.main(["policy", "grants", rid]), 0)
        self.assertEqual(cli.main(["policy", "revoke", rid, "spend"]), 0)
        self.assertEqual(cli.main(["policy", "check", "spend", "--run", rid]), 1)

    def test_granting_a_hard_refused_class_is_rejected_at_the_cli(self):
        cli.main(["run", "start", "g"])
        rid = self.led.list_runs()[0].run_id
        self.assertEqual(cli.main(["policy", "grant", rid, "infra-security"]), 1)

    def test_granting_an_unknown_class_is_rejected(self):
        cli.main(["run", "start", "g"])
        rid = self.led.list_runs()[0].run_id
        self.assertEqual(cli.main(["policy", "grant", rid, "nonsense"]), 1)

    def test_bare_policy_prints_help(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["policy"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
