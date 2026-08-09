"""Tests for the capability registry and credential broker.

Nothing here touches the real keychain: every credential test runs against
`MemoryBackend`. The one test that would have to shell out to `security` asserts
the argument shape instead, because the property that matters is that the secret is
never an argument.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
from _harness import TempRoot  # noqa: F401  (must precede any gigabite import)

from gigabite import cli  # noqa: E402
from gigabite.features import capability as C  # noqa: E402
from gigabite.features import ledger as L  # noqa: E402
from gigabite.features import policy as P  # noqa: E402


class CapabilityTestCase(TempRoot):
    def setUp(self):
        super().setUp()
        self.led = L.Ledger.open()
        self.backend = C.MemoryBackend()
        self.addCleanup(self.led.close)
        self.addCleanup(L.release_stop)

    def write_manifest(self, name: str, spec: dict):
        d = C.connectors_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path


READONLY_MANIFEST = {
    "name": "statista",
    "title": "Statista",
    "auth": "keychain",
    "keychain_service": "gigabite:statista",
    "how_to_connect": "Create an API token in your Statista account, then run `gigabite connect add statista`.",
    "rate_limit_per_minute": 20,
    "operations": {
        "search": {"action_class": "read", "summary": "search the report catalogue"},
        "fetch-report": {"action_class": "read", "summary": "one report"},
    },
}


class TestTheDoneCondition(CapabilityTestCase):
    """Adding a read-only connector is data, and nothing else changes."""

    def test_a_new_connector_is_one_json_file(self):
        self.assertNotIn("statista", C.load_all())
        self.write_manifest("statista", READONLY_MANIFEST)
        registry = C.load_all()
        self.assertIn("statista", registry)
        self.assertEqual(sorted(registry["statista"].operations), ["fetch-report", "search"])

    def test_it_authorizes_without_touching_the_policy_engine(self):
        self.write_manifest("statista", READONLY_MANIFEST)
        d = C.authorize("statista", "search")
        self.assertEqual(d.verdict, P.ALLOW)
        self.assertEqual(d.action_class, "read")

    def test_a_write_operation_on_a_new_connector_is_gated_by_the_same_rules(self):
        spec = dict(READONLY_MANIFEST)
        spec["operations"] = dict(spec["operations"])
        spec["operations"]["create-alert"] = {"action_class": "third-party-create"}
        self.write_manifest("statista", spec)
        rid = self.led.start_run("g").run_id
        self.assertEqual(
            C.authorize("statista", "create-alert", run_id=rid, led=self.led).verdict,
            P.APPROVE,
        )
        self.led.grant(rid, "third-party-create")
        self.assertTrue(
            C.authorize("statista", "create-alert", run_id=rid, led=self.led).allowed)


class TestManifestValidation(CapabilityTestCase):
    def test_an_unknown_action_class_is_refused_at_load(self):
        spec = dict(READONLY_MANIFEST)
        spec["operations"] = {"search": {"action_class": "whatever"}}
        self.write_manifest("bad", spec)
        with self.assertRaises(C.CapabilityError):
            C.load_all()

    def test_a_connector_with_no_operations_is_refused(self):
        self.write_manifest("bad", {"name": "bad", "auth": "none", "operations": {}})
        with self.assertRaises(C.CapabilityError):
            C.load_all()

    def test_keychain_auth_needs_a_service(self):
        self.write_manifest("bad", {
            "name": "bad", "auth": "keychain",
            "operations": {"x": {"action_class": "read"}},
        })
        with self.assertRaises(C.CapabilityError):
            C.load_all()

    def test_an_unknown_auth_kind_is_refused(self):
        self.write_manifest("bad", {
            "name": "bad", "auth": "magic",
            "operations": {"x": {"action_class": "read"}},
        })
        with self.assertRaises(C.CapabilityError):
            C.load_all()

    def test_malformed_json_raises_rather_than_being_skipped(self):
        d = C.connectors_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "broken.json").write_text("{nope", encoding="utf-8")
        with self.assertRaises(C.CapabilityError):
            C.load_all()

    def test_a_user_manifest_replaces_a_shipped_one_outright(self):
        self.write_manifest("claude_ai", {
            "name": "claude_ai", "auth": "none",
            "operations": {"only-this": {"action_class": "read"}},
        })
        conn = C.get("claude_ai")
        self.assertEqual(list(conn.operations), ["only-this"])
        self.assertNotEqual(conn.source, "shipped")

    def test_unknown_connector_and_operation_are_named_clearly(self):
        with self.assertRaises(C.CapabilityError):
            C.get("nope")
        with self.assertRaises(C.CapabilityError):
            C.get("claude_ai").operation("nope")


class TestShippedRegistry(CapabilityTestCase):
    def test_the_shipped_manifest_matches_the_live_module(self):
        from gigabite.sources import claude_ai_live
        self.assertEqual(C.get("claude_ai").keychain_service,
                         claude_ai_live.KEYCHAIN_SERVICE)

    def test_every_shipped_operation_names_a_known_action_class(self):
        for conn in C.load_all().values():
            for op in conn.operations.values():
                self.assertIn(op.action_class, P.ACTION_CLASSES)


class TestCredentialHandle(CapabilityTestCase):
    def test_a_credential_is_absent_until_connected(self):
        cred = C.get("claude_ai").credential(self.backend)
        self.assertFalse(cred.exists())
        with self.assertRaises(C.NotConnected):
            cred.value()

    def test_connect_stores_and_forget_removes(self):
        self.assertEqual(C.connect("claude_ai", self.backend), 0)
        cred = C.get("claude_ai").credential(self.backend)
        self.assertTrue(cred.exists())
        C.forget("claude_ai", self.backend)
        self.assertFalse(cred.exists())

    def test_repr_and_str_never_contain_the_secret(self):
        self.backend.preload("gigabite:claude_ai", C.default_account(), "sk-super-secret")
        cred = C.get("claude_ai").credential(self.backend)
        for rendered in (repr(cred), str(cred), f"{cred}", "{}".format(cred)):
            self.assertNotIn("sk-super-secret", rendered)
            self.assertIn("redacted", rendered)
    def test_a_secret_does_not_leak_into_an_audit_detail(self):
        self.backend.preload("gigabite:claude_ai", C.default_account(), "sk-super-secret")
        rid = self.led.start_run("g").run_id
        cred = C.get("claude_ai").credential(self.backend)
        C.authorize("claude_ai", "list-conversations", run_id=rid, led=self.led,
                    context={"credential": cred})
        for row in self.led.audit_trail(run_id=rid):
            self.assertNotIn("sk-super-secret", json.dumps(dict(row), default=str))

    def test_the_os_prompt_never_receives_the_secret_as_an_argument(self):
        """`-w` goes last with no value, so macOS asks and argv stays clean.

        This patches `subprocess.call` rather than subclassing the backend, so the
        argv asserted on is the one `KeychainBackend.prompt` really builds. An
        earlier version of this test had a fake backend construct the list it then
        asserted against, which meant the real implementation could have passed
        `-w "$SECRET"` and the test would still have gone green.
        """
        import subprocess as sp
        captured = {}
        real_call, real_run = sp.call, sp.run

        def fake_call(args, *a, **kw):
            captured["args"] = list(args)
            return 0

        sp.call, sp.run = fake_call, lambda *a, **kw: real_run(["true"], capture_output=True)
        try:
            C.connect("claude_ai", C.KeychainBackend())
        finally:
            sp.call, sp.run = real_call, real_run

        args = captured["args"]
        self.assertEqual(args[0], "security")
        self.assertEqual(args[-1], "-w", "`-w` must be last and valueless")
        # Every argument is a flag or a value we chose. None is a secret, and
        # there is nothing after -w for a secret to hide in.
        self.assertEqual(len(args), args.index("-w") + 1)
        self.assertIn("gigabite:claude_ai", args)

    def test_connecting_a_connector_with_nothing_to_store_is_refused(self):
        self.write_manifest("open_thing", {
            "name": "open_thing", "auth": "none",
            "operations": {"x": {"action_class": "read"}},
        })
        with self.assertRaises(C.CapabilityError):
            C.connect("open_thing", self.backend)


class TestRequireParksABlocker(CapabilityTestCase):
    def test_a_missing_connection_is_a_blocker_not_an_error(self):
        rid = self.led.start_run("g").run_id
        with self.assertRaises(C.NotConnected) as ctx:
            C.require("claude_ai", run_id=rid, led=self.led, backend=self.backend)
        self.assertIsNotNone(ctx.exception.blocker_id)
        blocker = self.led.blockers(run_id=rid)[0]
        self.assertEqual(blocker["kind"], "not-connected")
        self.assertTrue(blocker["what_would_unblock"].strip())
        # Parked, not failed: the rest of the mission still runs.
        self.assertEqual(self.led.get_run(rid).status, "running")
    def test_require_returns_the_connector_once_connected(self):
        C.connect("claude_ai", self.backend)
        rid = self.led.start_run("g").run_id
        conn = C.require("claude_ai", run_id=rid, led=self.led, backend=self.backend)
        self.assertEqual(conn.name, "claude_ai")
        self.assertEqual(self.led.blockers(run_id=rid), [])

    def test_auth_none_is_always_connected(self):
        self.write_manifest("open_thing", {
            "name": "open_thing", "auth": "none",
            "operations": {"x": {"action_class": "read"}},
        })
        self.assertTrue(C.get("open_thing").connected(self.backend))


class TestAuthorizationJoin(CapabilityTestCase):
    def test_narrowing_the_policy_narrows_every_connector_at_once(self):
        self.write_manifest("statista", READONLY_MANIFEST)
        (P.policy_path()).write_text(
            json.dumps({"rules": {"read": {"verdict": "approve", "why": "offline week"}}}),
            encoding="utf-8")
        self.assertEqual(C.authorize("statista", "search").verdict, P.APPROVE)
        self.assertEqual(C.authorize("claude_ai", "list-conversations").verdict, P.APPROVE)

    def test_a_hard_refused_class_cannot_be_reached_through_a_connector(self):
        spec = dict(READONLY_MANIFEST)
        spec["operations"] = {"open-port": {"action_class": "infra-security"}}
        self.write_manifest("statista", spec)
        rid = self.led.start_run("g", authority="full").run_id
        self.led.grant(rid, "infra-security")
        self.assertEqual(
            C.authorize("statista", "open-port", run_id=rid, led=self.led).verdict, P.NEVER)

    def test_the_operation_is_recorded_in_the_audit_trail(self):
        rid = self.led.start_run("g").run_id
        C.authorize("claude_ai", "list-conversations", run_id=rid, led=self.led)
        entry = next(r for r in self.led.audit_trail(run_id=rid)
                     if r["action"] == "claude_ai/list-conversations")
        self.assertEqual(entry["disposition"], P.ALLOW)
        self.assertEqual(json.loads(entry["detail_json"])["context"]["connector"], "claude_ai")

    def test_run_authority_still_applies_through_a_connector(self):
        self.write_manifest("statista", READONLY_MANIFEST)
        rid = self.led.start_run("g", authority="passive").run_id
        self.assertTrue(C.authorize("statista", "search", run_id=rid, led=self.led).allowed)
        spec = dict(READONLY_MANIFEST)
        spec["operations"] = {"write-note": {"action_class": "local-write"}}
        self.write_manifest("statista", spec)
        self.assertEqual(
            C.authorize("statista", "write-note", run_id=rid, led=self.led).verdict, P.NEVER)


class TestCli(CapabilityTestCase):
    def test_list(self):
        self.assertEqual(cli.main(["connect", "list"]), 0)
        self.write_manifest("statista", READONLY_MANIFEST)
        self.assertEqual(cli.main(["connect", "list"]), 0)

    def test_list_reports_a_broken_manifest_rather_than_hiding_it(self):
        d = C.connectors_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "broken.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(cli.main(["connect", "list"]), 1)

    def test_check_reflects_the_verdict_and_the_connection(self):
        self.write_manifest("statista", READONLY_MANIFEST)
        # allowed by policy, but not connected
        self.assertEqual(cli.main(["connect", "check", "statista", "search"]), 1)

    def test_check_on_an_unknown_operation_exits_nonzero(self):
        self.assertEqual(cli.main(["connect", "check", "claude_ai", "nope"]), 1)

    def test_forget_on_an_unknown_connector_exits_nonzero(self):
        self.assertEqual(cli.main(["connect", "forget", "nope"]), 1)

    def test_bare_connect_prints_help(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["connect"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
