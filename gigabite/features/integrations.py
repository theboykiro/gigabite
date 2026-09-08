"""The "AI brain" integrations menu: one registry, one interactive driver.

Today the only entry is Granola. More are coming, and the point of this module
is that a second one is a new `INTEGRATIONS` entry, not new branching logic:
adding a connector means naming its module (which must expose
`read_token()` / `store_token_interactive()` / `delete_token()`) and, if it has
a scheduled pull, its plist template and bin script — nothing here changes.

Reached two ways, on purpose (docs/MEETINGS.md background: a prior build made
the user run `gigabite granola-login` and then hand-type a `launchctl
bootstrap` one-liner, which he rejected):
    * `install.sh` step 6/8, for a first install.
    * `gigabite integrations`, any time after, for someone already installed
      who wants to turn one on (or add a new one once it ships) without a
      reinstall.

The secure keychain prompt itself is not duplicated here. `run_secure_prompt`
is the one place that drives it, and `cli.cmd_granola_login` calls into it too
rather than keeping its own copy — see that function for why the output has
to stay byte-identical for anyone already using `gigabite granola-login`
directly.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---- tiny ANSI helpers, matching cli.py's look -----------------------------
# Not imported from cli.py: cli.py imports from gigabite.features, so the
# reverse import would be circular. Same codes, same auto-disable-when-piped
# rule, so output looks identical either way it's driven.
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def cyan(s): return _c("36", s)
def yellow(s): return _c("33", s)
def green(s): return _c("32", s)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

INTEGRATIONS = [
    {
        "id": "granola",
        "label": "Granola — meeting transcripts + AI summaries",
        "module": "gigabite.sources.granola_live",
        "help": "Get it: Granola desktop app -> Settings -> Connectors -> API keys "
                "(requires Business plan).",
        "schedule": {
            "plist_label": "com.gigabite.granola-pull",
            "plist_template": "install/launchd/com.gigabite.granola-pull.plist",
            "bin": "bin/gigabite-granola-pull",
            "log_name": "gigabite-granola-pull.log",
            "description": "daily meeting pull at 19:00",
        },
    },
]


# ---------------------------------------------------------------------------
# the shared secure-prompt flow
# ---------------------------------------------------------------------------

def run_secure_prompt(module, noun: str = "key") -> Optional[int]:
    """Show the secure `security` prompt, then call `module.store_token_interactive()`.

    Returns that call's exit status, or `None` if the user cancelled (Ctrl-C /
    EOF) before it ran — the two are kept distinct so a caller can tell "the
    prompt failed" apart from "nothing was attempted".
    """
    print("When you press Enter, macOS's " + bold("security") + " tool will open a prompt called:")
    print(cyan("    password data for new item:"))
    print(dim("Ignore the word \"password\" — that's just macOS's generic label for anything "
              f"stored in the keychain. ") + bold(f"Paste your {noun} there") +
          dim(" (you won't see characters as you paste — that's expected).\n"
              "Press Enter, then paste the same ") + bold(noun) + dim(" again at ") +
          cyan("retype password for new item:") + dim(".\n"))
    try:
        input("Press Enter to open the secure prompt (Ctrl-C to cancel)… ")
    except (EOFError, KeyboardInterrupt):
        print(yellow("\ncancelled."))
        return None
    return module.store_token_interactive()


def _ask_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{prompt} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(yellow("\ncancelled."))
        return False
    if not ans:
        return default
    return ans in ("y", "yes")


# ---------------------------------------------------------------------------
# scheduling (launchd)
# ---------------------------------------------------------------------------

def _render_plist(template_path: Path, bin_path: Path, log_path: Path) -> str:
    """Same two placeholders install.sh substitutes for the synthesis plist —
    `__DAILY_BIN__` / `__LOG__` — done here with `str.replace`, not `sed`."""
    text = template_path.read_text(encoding="utf-8")
    return text.replace("__DAILY_BIN__", str(bin_path)).replace("__LOG__", str(log_path))


def _install_schedule(repo_root: Path, schedule: dict) -> None:
    """Render + install the plist, then load it. Reports exactly like
    install.sh's own "5/7 Scheduling" step — same tone, same fallback line.

    Calls `subprocess.run` at module level (not a bound default) so a test can
    patch `integrations.subprocess.run` and never spawn a real `launchctl`.
    """
    plist_label = schedule["plist_label"]
    template = repo_root / schedule["plist_template"]
    bin_path = repo_root / schedule["bin"]
    log_path = Path.home() / "Library" / "Logs" / schedule["log_name"]
    la_dir = Path.home() / "Library" / "LaunchAgents"
    la_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path = la_dir / f"{plist_label}.plist"

    plist_path.write_text(_render_plist(template, bin_path, log_path), encoding="utf-8")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{plist_label}"], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                            capture_output=True)
    if getattr(result, "returncode", 1) == 0:
        print(green(f"  ✓ scheduled: {schedule['description']}"))
    else:
        print(yellow(f"  ! installed the LaunchAgent plist but couldn't load it now "
                     f"({plist_path})"))


# ---------------------------------------------------------------------------
# the interactive driver
# ---------------------------------------------------------------------------

def run_interactive(repo_root: Path, *, assume_yes: bool = False) -> None:
    """Walk `INTEGRATIONS`, offering to connect (and schedule) each one.

    Safe to run any number of times: an already-connected integration is
    reported as such and left alone by default, never silently re-prompted.
    """
    if not assume_yes and not sys.stdin.isatty():
        print(dim("not an interactive terminal — run `gigabite integrations` later"))
        return

    print(bold("gigabite integrations — the \"AI brain\" you can turn on"))
    for entry in INTEGRATIONS:
        print()
        print(bold(entry["label"]))
        try:
            module = importlib.import_module(entry["module"])
        except ImportError as exc:
            print(yellow(f"  ! could not load {entry['module']}: {exc}"))
            continue

        just_stored = False
        existing = module.read_token()
        if existing:
            print(dim("  already connected."))
            if not _ask_yes_no("  Replace the stored key?", default=False):
                continue
            rc = run_secure_prompt(module)
            just_stored = rc == 0
        else:
            print(dim(f"  {entry['help']}"))
            if not _ask_yes_no(f"  Enable {entry['label'].split(' — ')[0]}?", default=False):
                continue
            rc = run_secure_prompt(module)
            just_stored = rc == 0

        if just_stored:
            print(green("  ✓ key saved to keychain."))
        elif rc is not None:
            print(yellow("  key was not saved (prompt cancelled or failed)."))

        schedule = entry.get("schedule")
        if just_stored and schedule:
            if _ask_yes_no(f"  Schedule the daily pull ({schedule['description']})?",
                           default=True):
                _install_schedule(repo_root, schedule)

    print()
    print(dim("More integrations are coming. Run `gigabite integrations` again any time."))


__all__ = ["INTEGRATIONS", "run_secure_prompt", "run_interactive"]
