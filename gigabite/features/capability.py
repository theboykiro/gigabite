"""The capability registry: what the system can reach, and on whose authority.

A connector is **declared, not coded** (docs/AUTONOMY.md §2, ROADMAP item 5). A
manifest names the integration, how a human connects it, and — the part that
matters — which action class each of its operations falls into. That last field is
what stops the registry being a list of powers: every operation resolves through
`features.policy` before it happens, so adding a connector adds capability and its
constraint in the same breath.

Adding a read-only integration is therefore a JSON file in `~/.core/connectors/`
and nothing else. No change to the policy engine, the ledger, or the executor.

**Credentials.** The broker holds nothing. `connect` runs macOS `security` with
`-w` last, which drops into the operating system's own hidden prompt — the value is
typed by the user into a system dialog and never passes through argv, this process,
shell history, or the conversation. Afterwards a `Credential` is a lazy handle:
`exists()` answers whether a connection is there, and `value()` reads the keychain
at the moment of use. Its `repr` and `str` are redacted, so a secret cannot leak by
being logged, put in an audit detail, or included in an exception.

That is the pattern `sources/claude_ai_live` already uses and this generalises. The
thing it deliberately does *not* generalise is that module's other half: reaching
undocumented internal endpoints with a session cookie. A connector manifest points
at a documented API or it does not ship.

**A missing connection is a blocker, not an error.** `require` parks it on the run
with the sentence that would resolve it, so a mission that needs Jira at step 4
finishes steps 5 and 6 and asks once, at the end (docs/AUTONOMY.md §6).

**Scope.** This module declares and authorises. It does not perform requests —
there is no transport here, and `rate_limit_per_minute` is carried on the manifest
for the executor (ROADMAP item 6) to enforce, not enforced here. Building a limiter
with nothing to limit would be the same mistake as a guardrail nothing passes
through.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from . import ledger as ledger_mod
from . import policy as policy_mod

AUTH_KINDS = ("none", "keychain", "external")


class CapabilityError(Exception):
    """A malformed manifest, or an unknown connector or operation."""


class NotConnected(Exception):
    """The connector has no credential. Carries the blocker id when parked."""

    def __init__(self, message: str, *, connector: str = "", blocker_id: Optional[int] = None):
        super().__init__(message)
        self.connector = connector
        self.blocker_id = blocker_id


# ---------------------------------------------------------------------------
# credential handles
# ---------------------------------------------------------------------------

class KeychainBackend:
    """macOS `security`. The only place a secret value is ever touched."""

    available = True

    def exists(self, service: str, account: str) -> bool:
        """Presence only — note the absent `-w`.

        Without it `security` reports whether the item exists without returning
        the secret, so listing connectors never pulls a value out of the keychain
        just to print the word "connected".
        """
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", account],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        return r.returncode == 0

    def read(self, service: str, account: str) -> Optional[str]:
        return self._read(service, account)

    def _read(self, service: str, account: str) -> Optional[str]:
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip() or None

    def forget(self, service: str, account: str) -> None:
        try:
            subprocess.run(
                ["security", "delete-generic-password", "-s", service, "-a", account],
                capture_output=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    def prompt(self, service: str, account: str, label: str) -> int:
        """Hand the typing to the operating system.

        `-w` goes LAST with no value, so `security` opens its own hidden prompt.
        The secret is never an argument, never reaches this process, and never
        lands in shell history. Any prior item is cleared first so re-running
        always works rather than colliding.
        """
        self.forget(service, account)
        try:
            return subprocess.call([
                "security", "add-generic-password",
                "-s", service, "-a", account, "-D", label, "-w",
            ])
        except (FileNotFoundError, OSError):
            return 1


class MemoryBackend:
    """In-process backend for tests. Never touches the real keychain."""

    available = True

    def __init__(self):
        self._store: dict = {}

    def exists(self, service: str, account: str) -> bool:
        return (service, account) in self._store

    def read(self, service: str, account: str) -> Optional[str]:
        return self._store.get((service, account))

    def forget(self, service: str, account: str) -> None:
        self._store.pop((service, account), None)

    def prompt(self, service: str, account: str, label: str) -> int:
        self._store[(service, account)] = "test-secret"
        return 0

    # Tests that need to simulate an already-connected integration.
    def preload(self, service: str, account: str, value: str = "test-secret") -> None:
        self._store[(service, account)] = value


def default_account() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return "gigabite"


@dataclass
class Credential:
    """A handle, not a value. Reads at the moment of use and redacts everywhere."""

    service: str
    account: str
    backend: object = field(default_factory=KeychainBackend)

    def exists(self) -> bool:
        return bool(self.backend.exists(self.service, self.account))

    def value(self) -> str:
        """Read the secret. Deliberately explicit — the only way to get it."""
        v = self.backend.read(self.service, self.account)
        if v is None:
            raise NotConnected(f"no credential stored for {self.service}")
        return v

    def forget(self) -> None:
        self.backend.forget(self.service, self.account)

    # A secret must not leak by being logged, formatted into a message, put in an
    # audit detail, or caught in a traceback. Redaction lives on the object so it
    # holds everywhere by default rather than at each call site by discipline.
    def __repr__(self) -> str:
        return f"<Credential {self.service} {'present' if self.exists() else 'absent'} (redacted)>"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------

@dataclass
class Operation:
    name: str
    action_class: str
    writes: bool = False
    summary: str = ""


@dataclass
class Connector:
    name: str
    title: str = ""
    auth: str = "none"
    keychain_service: str = ""
    how_to_connect: str = ""
    docs: str = ""
    rate_limit_per_minute: Optional[int] = None
    operations: dict = field(default_factory=dict)
    source: str = "shipped"

    def operation(self, name: str) -> Operation:
        op = self.operations.get(name)
        if op is None:
            raise CapabilityError(
                f"{self.name} has no operation {name!r}. Declared: "
                + (", ".join(sorted(self.operations)) or "none")
            )
        return op

    def credential(self, backend: object = None) -> Credential:
        if self.auth != "keychain":
            raise CapabilityError(f"{self.name} does not use a stored credential")
        return Credential(self.keychain_service, default_account(),
                          backend or KeychainBackend())

    def connected(self, backend: object = None) -> bool:
        """`none` needs nothing; `external` is connected elsewhere and unverifiable."""
        if self.auth == "none":
            return True
        if self.auth == "external":
            return False
        return self.credential(backend).exists()


# Shipped manifests describe integrations that actually exist today. Nothing
# aspirational: a connector listed here and not implemented would be a promise the
# registry cannot keep, which is worse than an empty registry.
SHIPPED = {
    "claude_ai": {
        "title": "claude.ai web chats",
        "auth": "keychain",
        "keychain_service": "gigabite:claude_ai",
        "how_to_connect": (
            "Copy your claude.ai `sessionKey` cookie, then run `gigabite claude-login` "
            "and paste it into the macOS prompt. It never passes through the chat."
        ),
        "docs": "docs/CLAUDE_AI.md",
        "rate_limit_per_minute": 30,
        "operations": {
            "list-conversations": {"action_class": "read",
                                   "summary": "your own chats, in and out of projects"},
            "fetch-conversation": {"action_class": "read",
                                   "summary": "one conversation in full"},
        },
    },
}


def connectors_dir() -> Path:
    """User manifests. Resolved at call time; `config.CORE_DIR` is import-time."""
    return config.CORE_DIR / "connectors"


def _parse(name: str, raw: dict, source: str) -> Connector:
    if not isinstance(raw, dict):
        raise CapabilityError(f"{source}: connector {name!r} must be an object")
    auth = raw.get("auth", "none")
    if auth not in AUTH_KINDS:
        raise CapabilityError(
            f"{source}: connector {name!r} has auth {auth!r}; expected one of "
            + ", ".join(AUTH_KINDS)
        )
    if auth == "keychain" and not raw.get("keychain_service"):
        raise CapabilityError(f"{source}: connector {name!r} needs a `keychain_service`")

    ops = {}
    declared = raw.get("operations") or {}
    if not declared:
        raise CapabilityError(
            f"{source}: connector {name!r} declares no operations. A connector with "
            "no operations is capability with no constraint on it."
        )
    for op_name, spec in declared.items():
        if not isinstance(spec, dict) or "action_class" not in spec:
            raise CapabilityError(
                f"{source}: operation {name}/{op_name} must declare an `action_class`"
            )
        action_class = spec["action_class"]
        if action_class not in policy_mod.ACTION_CLASSES:
            raise CapabilityError(
                f"{source}: operation {name}/{op_name} has action class "
                f"{action_class!r}, which the policy engine does not know. Known: "
                + ", ".join(policy_mod.ACTION_CLASSES)
            )
        ops[op_name] = Operation(
            name=op_name,
            action_class=action_class,
            writes=bool(spec.get("writes", action_class != "read")),
            summary=spec.get("summary", ""),
        )

    return Connector(
        name=name,
        title=raw.get("title", name),
        auth=auth,
        keychain_service=raw.get("keychain_service", ""),
        how_to_connect=raw.get("how_to_connect", ""),
        docs=raw.get("docs", ""),
        rate_limit_per_minute=raw.get("rate_limit_per_minute"),
        operations=ops,
        source=source,
    )


def load_all() -> dict:
    """Shipped manifests, overlaid with anything in `~/.core/connectors/*.json`.

    A user file with the name of a shipped connector replaces it outright rather
    than merging: a half-overridden operation list is the kind of state nobody can
    reason about later.
    """
    registry = {name: _parse(name, raw, "shipped") for name, raw in SHIPPED.items()}

    directory = connectors_dir()
    if not directory.is_dir():
        return registry
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CapabilityError(f"cannot read {path}: {exc}") from exc
        name = raw.get("name") or path.stem
        registry[name] = _parse(name, raw, str(path))
    return registry


def get(name: str) -> Connector:
    registry = load_all()
    if name not in registry:
        raise CapabilityError(
            f"no connector {name!r}. Known: " + (", ".join(sorted(registry)) or "none")
        )
    return registry[name]


# ---------------------------------------------------------------------------
# using a connector
# ---------------------------------------------------------------------------

def authorize(
    connector: str,
    operation: str,
    *,
    run_id: str = "",
    led: Optional[ledger_mod.Ledger] = None,
    context: object = None,
) -> policy_mod.Decision:
    """Resolve an operation to its declared action class and put it to policy.

    This is the join the whole layer exists for. A connector cannot name its own
    verdict — it names a class, and the user's policy decides what that class may
    do. Adding a connector therefore cannot widen what the system is allowed to do
    without a corresponding, visible policy entry.
    """
    conn = get(connector)
    op = conn.operation(operation)
    detail = {"connector": connector, "operation": operation}
    if context is not None:
        detail["context"] = context
    return policy_mod.authorize(
        op.action_class, f"{connector}/{operation}",
        run_id=run_id, led=led, context=detail,
    )


def require(
    connector: str,
    *,
    run_id: str = "",
    led: Optional[ledger_mod.Ledger] = None,
    backend: object = None,
    step_seq: Optional[int] = None,
) -> Connector:
    """Return the connector, or park a blocker and raise.

    Not an error, because an unconnected integration is not a failure of the run —
    it is one sentence of setup the user has not done yet. Parking it lets the rest
    of the mission finish and turns five interruptions into one list.
    """
    conn = get(connector)
    if conn.connected(backend):
        return conn

    blocker_id = None
    if led is not None and run_id:
        blocker_id = led.open_blocker(
            run_id, "not-connected",
            f"{conn.title or conn.name} is not connected",
            conn.how_to_connect or f"run `gigabite connect {conn.name}` once",
            step_seq=step_seq,
        )
    raise NotConnected(
        f"{conn.title or conn.name} is not connected — "
        + (conn.how_to_connect or f"run `gigabite connect {conn.name}` once"),
        connector=conn.name, blocker_id=blocker_id,
    )


def connect(connector: str, backend: object = None) -> int:
    """Hand the typing to the OS prompt. Returns the exit status."""
    conn = get(connector)
    if conn.auth != "keychain":
        raise CapabilityError(
            f"{conn.name} uses `{conn.auth}` auth — there is nothing to store here."
            + (f" {conn.how_to_connect}" if conn.how_to_connect else "")
        )
    back = backend or KeychainBackend()
    return back.prompt(conn.keychain_service, default_account(),
                       f"gigabite {conn.name} credential")


def forget(connector: str, backend: object = None) -> None:
    conn = get(connector)
    if conn.auth == "keychain":
        conn.credential(backend).forget()


__all__ = [
    "Connector", "Operation", "Credential", "KeychainBackend", "MemoryBackend",
    "CapabilityError", "NotConnected", "AUTH_KINDS", "SHIPPED",
    "load_all", "get", "authorize", "require", "connect", "forget",
    "connectors_dir", "default_account",
]
