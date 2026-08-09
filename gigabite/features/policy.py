"""The policy engine: one chokepoint every side-effecting action passes through.

A guardrail written into a prompt is steering, and steering can be argued with.
The only guardrail that holds is the one in the call path, which is what this is
(docs/AUTONOMY.md §4).

Every action the system can take resolves to one of nine **action classes**, and
each class carries a verdict:

    allow      go, and log it
    approve    a human says yes first — once per class per run, not once per call
    user-only  the user performs it themselves; the agent never handles the value
    never      refused in code, and not addressable from the policy file

Which class gets which verdict is **per-user configuration**, and it lives in
`~/.core/policy.json`. Which classes exist, and the fact that `infra-security` is
`never`, are **product decisions**, and they live here.

Two things are deliberately not negotiable from the config file:

**The `never` row.** `HARD_REFUSED` is enforced before the file is consulted, so
editing the file cannot relax it, and a grant for it is ignored rather than
honoured. A rule a user can turn off is not a wall.

**Unknown classes fail closed.** An action class this build has never heard of
resolves to `approve`, never to `allow`. The permissive fallback on an unrecognised
input is the single most common way a generated guard ends up not guarding.

Run authority (`runs.authority`) is a **ceiling**, applied after the file: it can
only ever restrict. A `passive` run cannot write however the file is configured.

There are no connectors yet, so today the callers are the ledger's own gated
operations and whatever the operator drives through `gigabite policy check`. The
real call sites arrive with the capability registry (ROADMAP item 5), which is
exactly why this is built first: retrofitting a chokepoint into a call graph that
already assumes it can do anything is how it ends up with holes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import config
from . import ledger as ledger_mod

# Verdicts.
ALLOW = "allow"
APPROVE = "approve"
USER_ONLY = "user-only"
NEVER = "never"
VERDICTS = (ALLOW, APPROVE, USER_ONLY, NEVER)

# The nine action classes, in the order docs/AUTONOMY.md §4 lists them, with the
# shipped default verdict and the reason it is that and not something else. The
# reasons are data rather than comments because they are shown to the user at the
# moment a decision is made — a refusal that cannot say why is just a wall.
DEFAULT_RULES = {
    "read": (
        ALLOW,
        "Reading changes nothing. Egress from reads is a separate concern, handled "
        "at the outbound filter rather than here.",
    ),
    "local-write": (
        ALLOW,
        "Notes, drafts and files under the knowledge base. Reversible, and logged.",
    ),
    "code-working": (
        ALLOW,
        "Edits and commits on a working branch are reversible by anyone at any time.",
    ),
    "code-publishing": (
        APPROVE,
        "A push to a shared remote, a merge, a deploy, or a change to CI or secrets "
        "is outward-facing and effectively irreversible.",
    ),
    "third-party-create": (
        APPROVE,
        "A ticket or a page appears in someone else's workspace, with your name on it.",
    ),
    "outbound-comms": (
        APPROVE,
        "Anything addressed to a person cannot be recalled once sent.",
    ),
    "spend": (
        APPROVE,
        "Money. The amount is shown before the question is asked.",
    ),
    "credentials": (
        USER_ONLY,
        "First-time auth is performed by the user in the provider's own flow. The "
        "agent asks whether a connection exists; it never sees, types or stores a value.",
    ),
    "infra-security": (
        NEVER,
        "Ports, firewalls, IAM, access control — anything that changes a security "
        "posture on a system we do not own. Not a slow lane; a wall.",
    ),
}

ACTION_CLASSES = tuple(DEFAULT_RULES)

# Enforced before the policy file is read, and not overridable from it.
HARD_REFUSED = frozenset({"infra-security"})

# What each run-level authority permits. A ceiling: it restricts, never relaxes.
# `supervised` and `full` impose nothing extra on purpose — the difference between
# them is which classes have earned an `allow` in the user's policy file, which is
# an auditable edit, not a second hidden switch. Saying so beats inventing a
# distinction that would quietly widen what `full` means.
AUTHORITY_PERMITS = {
    "passive": frozenset({"read"}),
    "advisory": frozenset({"read", "local-write"}),
    "supervised": None,   # no additional restriction
    "full": None,         # no additional restriction
}


class PolicyError(Exception):
    """A malformed policy file, or a class this build does not know."""


class Refused(Exception):
    """The action is not permitted. Carries the decision that refused it."""

    def __init__(self, decision: "Decision"):
        super().__init__(decision.explain())
        self.decision = decision


class ApprovalRequired(Exception):
    """A human has to say yes first. Carries the decision that asked."""

    def __init__(self, decision: "Decision"):
        super().__init__(decision.explain())
        self.decision = decision


@dataclass
class Decision:
    verdict: str
    action_class: str
    action: str
    why: str
    source: str          # where the verdict came from, for the audit trail
    run_id: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW

    def explain(self) -> str:
        if self.verdict == ALLOW:
            return f"{self.action_class}/{self.action}: allowed"
        if self.verdict == APPROVE:
            return (f"{self.action_class}/{self.action}: needs your approval — {self.why}"
                    + (f"\n  approve for this run: gigabite policy grant {self.run_id} "
                       f"{self.action_class}" if self.run_id else ""))
        if self.verdict == USER_ONLY:
            return f"{self.action_class}/{self.action}: you do this one — {self.why}"
        return f"{self.action_class}/{self.action}: refused — {self.why}"


# ---------------------------------------------------------------------------
# the policy file
# ---------------------------------------------------------------------------

def policy_path() -> Path:
    """Resolved at call time; `config.CORE_DIR` is computed at import."""
    return config.CORE_DIR / "policy.json"


def default_policy() -> dict:
    return {
        "version": 1,
        "_comment": (
            "Which action class needs what. Edit `verdict` to change how much this "
            "machine may do unattended. `infra-security` is enforced in code and "
            "cannot be relaxed here. See docs/AUTONOMY.md §4."
        ),
        "rules": {
            name: {"verdict": verdict, "why": why}
            for name, (verdict, why) in DEFAULT_RULES.items()
        },
    }


def write_default(path: Optional[Path] = None, *, overwrite: bool = False) -> Path:
    target = Path(path) if path else policy_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return target
    target.write_text(json.dumps(default_policy(), indent=2) + "\n", encoding="utf-8")
    return target


def load(path: Optional[Path] = None) -> dict:
    """Read the effective rules: shipped defaults, overlaid with the user's file.

    A missing file is not an error — the defaults *are* the policy, and a first
    run should not have to be configured before it is safe. A malformed file IS an
    error, because the alternative is silently falling back to defaults that may be
    more permissive than what the user wrote, which is the fail-open shape this
    module exists to prevent.
    """
    rules = {name: {"verdict": v, "why": why, "source": "default"}
             for name, (v, why) in DEFAULT_RULES.items()}

    target = Path(path) if path else policy_path()
    if not target.exists():
        return rules

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"cannot read {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{target}: expected an object at the top level")

    for name, spec in (raw.get("rules") or {}).items():
        if name not in DEFAULT_RULES:
            raise PolicyError(
                f"{target}: unknown action class {name!r}. Known classes: "
                + ", ".join(ACTION_CLASSES)
            )
        if not isinstance(spec, dict) or "verdict" not in spec:
            raise PolicyError(f"{target}: rule {name!r} needs a `verdict`")
        verdict = spec["verdict"]
        if verdict not in VERDICTS:
            raise PolicyError(
                f"{target}: rule {name!r} has verdict {verdict!r}; expected one of "
                + ", ".join(VERDICTS)
            )
        if name in HARD_REFUSED and verdict != NEVER:
            raise PolicyError(
                f"{target}: {name!r} is refused in code and cannot be set to "
                f"{verdict!r}. Remove the rule."
            )
        rules[name] = {
            "verdict": verdict,
            "why": spec.get("why") or DEFAULT_RULES[name][1],
            "source": str(target),
        }
    return rules


# ---------------------------------------------------------------------------
# deciding
# ---------------------------------------------------------------------------

def decide(
    action_class: str,
    action: str = "",
    *,
    run_id: str = "",
    authority: str = "supervised",
    granted: bool = False,
    rules: Optional[dict] = None,
) -> Decision:
    """Resolve one action to a verdict. Pure: no I/O, no audit, no exceptions.

    Order matters, and it is hard-refused first on purpose: nothing downstream —
    not the config file, not a grant, not a run authority of `full` — gets an
    opportunity to turn it into an allow.
    """
    action = action or action_class

    if action_class in HARD_REFUSED:
        return Decision(NEVER, action_class, action, DEFAULT_RULES[action_class][1],
                        "code", run_id)

    if action_class not in DEFAULT_RULES:
        # Fail closed. An unrecognised class is not a permissive default; it is a
        # gap in the map, and a human decides what falls into it.
        return Decision(
            APPROVE, action_class, action,
            "this build has no rule for that action class, so it does not get a "
            "free pass — say yes explicitly or add a class",
            "unknown-class", run_id,
        )

    table = rules if rules is not None else load()
    rule = table.get(action_class, {})
    verdict = rule.get("verdict", DEFAULT_RULES[action_class][0])
    why = rule.get("why", DEFAULT_RULES[action_class][1])
    source = rule.get("source", "default")

    # A grant satisfies an `approve`, and nothing else. It cannot upgrade a
    # `user-only` (the agent still must not handle the value) and it never reaches
    # a hard refusal, which returned above.
    if verdict == APPROVE and granted:
        return Decision(ALLOW, action_class, action,
                        "approved for this run", "grant", run_id)

    # The run's authority ceiling, applied last so it can only restrict.
    permitted = AUTHORITY_PERMITS.get(authority, None)
    if permitted is not None and action_class not in permitted:
        return Decision(
            NEVER, action_class, action,
            f"this run's authority is {authority}, which permits only "
            + ", ".join(sorted(permitted)),
            "run-authority", run_id,
        )

    return Decision(verdict, action_class, action, why, source, run_id)


def authorize(
    action_class: str,
    action: str = "",
    *,
    run_id: str = "",
    led: Optional[ledger_mod.Ledger] = None,
    context: Any = None,
    rules: Optional[dict] = None,
) -> Decision:
    """Decide, and write the outcome to the audit trail either way.

    "Either way" is the point. A refusal that leaves no trace is indistinguishable
    from an action that was never attempted, and the question you want to answer
    later is what the system *tried* to do, not only what it managed.
    """
    owned = None
    if led is None and run_id:
        owned = led = ledger_mod.Ledger.open()

    authority, granted = "supervised", False
    if led is not None and run_id:
        run = led.get_run(run_id)
        if run is not None:
            authority = run.authority
            granted = led.has_grant(run_id, action_class)

    decision = decide(action_class, action, run_id=run_id, authority=authority,
                      granted=granted, rules=rules)

    if led is not None:
        detail = {"why": decision.why, "source": decision.source}
        if context is not None:
            detail["context"] = context
        led.audit(run_id or None, action_class, action or action_class,
                  decision.verdict, detail)
    if owned is not None:
        owned.close()
    return decision


def guard(action_class: str, action: str = "", **kwargs) -> Decision:
    """`authorize`, but raises rather than returning a verdict to be ignored.

    Call sites that must not proceed use this; the exception is the enforcement.
    A caller that wants to reason about the verdict calls `authorize` instead.
    """
    decision = authorize(action_class, action, **kwargs)
    if decision.verdict in (NEVER, USER_ONLY):
        raise Refused(decision)
    if decision.verdict == APPROVE:
        raise ApprovalRequired(decision)
    return decision


__all__ = [
    "ALLOW", "APPROVE", "USER_ONLY", "NEVER", "VERDICTS",
    "ACTION_CLASSES", "DEFAULT_RULES", "HARD_REFUSED", "AUTHORITY_PERMITS",
    "Decision", "PolicyError", "Refused", "ApprovalRequired",
    "decide", "authorize", "guard", "load", "default_policy", "write_default",
    "policy_path",
]
