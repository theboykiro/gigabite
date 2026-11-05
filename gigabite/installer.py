"""`gigabite install` and `gigabite uninstall`, once, for macOS and Windows.

On macOS, install.sh and uninstall.sh are wrappers over these: what stays in bash
is only the check that Python can start at all. On Windows, bootstrap.ps1 runs
`gigabite install` after `uv tool install --editable`, so the templates are read
from the checkout (`config.REPO_ROOT`) on both.

What differs by platform, and nothing else does:

* **PATH.** macOS links `bin/gigabite` into ~/.local/bin and adds that to the
  shell rc files. On Windows uv owns PATH; this only checks `gigabite` resolves.
* **Hooks.** macOS registers the install/hooks/*.sh wrappers by absolute path,
  as every existing install has them. Windows registers `gigabite.exe` itself in
  Claude Code's exec form (`command` + `args`, no shell), so neither Git Bash nor
  PowerShell quoting is involved.
* **Integrations, launchd, the keychain.** macOS only. Skipped on Windows with
  one line.

Every message is the one the bash scripts printed, so a macOS install reads the
same as before. The environment seams the tests use are the bash ones:
GIGABITE_BIN_DIRS, GIGABITE_LAUNCHCTL, GIGABITE_OPEN, GIGABITE_VERBOSE.
"""

from __future__ import annotations

import datetime
import filecmp
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from . import config, osutil

# Read at call time throughout, so a test can switch it to exercise the Windows
# shape on macOS.
WINDOWS = osutil.WINDOWS

START, END = "<!-- gigabite:router:start -->", "<!-- gigabite:router:end -->"
# event, matcher, hook name. Recall and refresh have no matcher, so they fire on
# every prompt / every way a session starts; the agent-model guard matches the
# agent tool under its current and older name.
HOOKS = (("UserPromptSubmit", None, "recall"),
         ("SessionStart", None, "refresh"),
         ("PreToolUse", "Agent|Task", "agent-model"))
HOOK_SCRIPTS = ("gg-recall.sh", "gg-refresh.sh", "gg-agent-model.sh")


class _Stop(Exception):
    """`die`: the message is already printed; exit with this code."""

    def __init__(self, code: int = 1):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# output: install.sh's five helpers, the same look
# ---------------------------------------------------------------------------

def _paint(code: str, text: str, stream) -> str:
    # Always on macOS, as the bash scripts were; on Windows only where the
    # console draws it.
    if WINDOWS and not osutil.ansi_ok(stream):
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def _out(line: str, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(line + "\n")
    stream.flush()


def say(text: str = "") -> None:
    _out(_paint("1", text, sys.stdout) if text else "")


def note(text: str, stream=None) -> None:
    _out("  " + _paint("2", text, stream or sys.stdout), stream)


def ok(text: str) -> None:
    _out("  %s %s" % (_paint("32", "✓", sys.stdout), text))


def warn(text: str) -> None:
    _out("  %s %s" % (_paint("33", "!", sys.stdout), text))


def die(first: str, *lines: str) -> None:
    _out("  %s %s" % (_paint("31", "✗", sys.stderr), first), sys.stderr)
    for line in lines:
        note(line, sys.stderr)
    raise _Stop(1)


def _stdin_is_tty() -> bool:
    """`[ -t 0 ]`. A seam: the test that answers the uninstall prompt needs it."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _label(path: Path) -> str:
    """Several scaffold files are called README.md, so a bare basename tells you
    nothing about which one was touched. The parent folder goes with it."""
    return "%s/%s" % (path.parent.name, path.name)


def _finder() -> str:
    return "Explorer" if WINDOWS else "Finder"


def _script(name: str) -> str:
    """How the user re-runs a step: the wrapper on macOS, the command on Windows."""
    return "gigabite %s" % name if WINDOWS else "./%s.sh" % name


# ---------------------------------------------------------------------------
# shared by both directions
# ---------------------------------------------------------------------------

# Every command, subagent and skill gigabite ships carries this line.
MARKER = b"<!-- gigabite:managed"
# sha256 of each command and subagent template shipped before the marker existed
# (17 versions, taken from the repository's history; none ships any more). An
# installed copy is compared with the launcher path put back as the placeholder,
# so an older install is still recognised and upgraded or removed.
_PRE_MARKER = frozenset("""
039f360839dc46fba2f255401aced0b1f1fc096eee0c7b6f7df100ffbd34173f
2304c425aa3a0207116c2e192bbf7a1f4949d624c8a5e51fc7bf81d9cf6c878a
2a0f076aafa11e84203d74431695fe54de56dd9a632c013d544dc99f86cb6955
39c0c14156f41e5264f1a5c89a311ca05d85c1aee00e8038d516beff247d40f5
4d4d6aea070694d60b138472f6cb78025da35f18fcf90c726f9888736e196ccf
50cb4479d473fe9a91205cf3ec4c6f10e66b12675010bbb3073218db2da7f8e0
54372e626b8e1ede2a205e4a0b26e3e41f39dad118b4d4a42e5c638d65db6ac3
57151f3d59ff1acf4d78d1a44d5cf430ca12c92f94c59947a6c17f42db8a3982
5c00da7806c5227d1a07665d5e0dece1bee3ec68b4bdc3eb91a5c6ec694d282b
7dfcc19443ed57c994199a34600d01eebfa686e3bbbb2cf6a4c6ddb5828ceed3
94c810986ccf2a9b44f8e289155a29dec46730bd360f198751cf9be17d32fe9e
98fcf703c1ea0b377c64ab951219febccb1f608f736d81197ee1c16332e9f7f2
a4638b66f37b5133e69003513f6ea36b4c1a336946ae61a41bcc0e6d8c57af9e
a7f5533584504aef776f2511fa70420aff580e022009a9d66ae79ed712836d69
aef8056f26180ca050e0a2f2a53e2e3bbb1e5750de7c5227e6a8da022d56ffd8
b34860e87d45777d1ca7102323a422443cb6dede35e6d3f3030d4fcd99cf7008
b7381420bc7d3b0130db2a2dd49d1a20f054167d35acaf73e5611b9057626ea9
""".split())
# The launcher path an older install baked in: `<clone>/bin/gigabite`.
_BAKED_LAUNCHER = re.compile(rb"/\S*?/bin/gigabite")


def is_ours(path: Path) -> bool:
    """A file gigabite wrote: it carries the marker, or it is, byte for byte, a
    version shipped before the marker. Merely mentioning gigabite is not enough:
    a command of the user's own that calls gigabite is theirs, and is neither
    overwritten by install nor deleted by uninstall."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if MARKER in data:
        return True
    template = _BAKED_LAUNCHER.sub(b"__GIGABITE_BIN__", data)
    return hashlib.sha256(template).hexdigest() in _PRE_MARKER


def _exec_name(hook) -> Optional[str]:
    """The gigabite hook an exec-form entry runs (`recall`, ...), else None."""
    if not isinstance(hook, dict):
        return None
    args, command = hook.get("args"), hook.get("command")
    if not (isinstance(args, list) and len(args) == 2 and args[0] == "hook"
            and isinstance(command, str)):
        return None
    base = re.split(r"[\\/]", command)[-1].lower()
    return args[1] if base in ("gigabite", "gigabite.exe") else None


def exec_hook_registered(settings_text: str, name: str) -> bool:
    """Is `gigabite hook <name>` registered in exec form in this settings.json?"""
    try:
        cfg = json.loads(settings_text)
    except ValueError:
        return False
    hooks = cfg.get("hooks") if isinstance(cfg, dict) else None
    for entries in (hooks.values() if isinstance(hooks, dict) else ()):
        for entry in (entries if isinstance(entries, list) else ()):
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            for hook in (inner if isinstance(inner, list) else [entry]):
                if _exec_name(hook) == name:
                    return True
    return False


def _launchctl() -> str:
    """Named, not hardcoded: booting a job out of the live launchd domain is not
    something a test may do."""
    return os.environ.get("GIGABITE_LAUNCHCTL") or "launchctl"


def install_bin_dirs(home: Path) -> List[str]:
    """~/.local/bin only. Earlier installs took the first writable of
    /opt/homebrew/bin, /usr/local/bin, ..., which wrote outside HOME;
    `uninstall_bin_dirs` still searches those, to clean up after them."""
    return (os.environ.get("GIGABITE_BIN_DIRS") or "%s/.local/bin" % home).split(":")


def uninstall_bin_dirs(home: Path) -> List[str]:
    return (os.environ.get("GIGABITE_BIN_DIRS")
            or "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s/bin" % (home, home)).split(":")


def _points_into_checkout(link: str) -> bool:
    """A `gigabite` symlink into a gigabite checkout is ours to replace or remove.
    Anything else by that name is somebody else's program."""
    if not os.path.islink(link):
        return False
    target = os.readlink(link)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link), target)
    root = os.path.dirname(os.path.dirname(target))
    return (os.path.isfile(os.path.join(root, "install.sh"))
            and os.path.isfile(os.path.join(root, "gigabite", "__init__.py")))


def _read_json(path: Path):
    # utf-8-sig: Windows PowerShell 5 writes a BOM, which json.load refuses.
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _read_md(path: Path):
    """(text with \\n line ends, the file's own line end). A CLAUDE.md written on
    Windows has CRLF; the block is spliced in and out without converting the rest."""
    with open(path, encoding="utf-8", newline="") as fh:
        raw = fh.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _write_md(path: Path, text: str, eol: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", eol))


def _write_json(path: Path, cfg) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=2)


def _run_cli(argv: list) -> int:
    """Another gigabite command, in this process: the bash installer ran each one
    as a subprocess, and on Windows there is no launcher script to run."""
    from . import cli
    try:
        return cli.main(list(argv)) or 0
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except KeyboardInterrupt:
        raise
    except Exception:
        traceback.print_exc()
        return 1


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

INSTALL_HELP = """\
gigabite installer — idempotent, additive, non-destructive.
  • creates ~/.core and ~/Knowledge layout (copies templates only if absent), and
    on a first install opens ~/Knowledge in Finder so you know it exists
  • puts `gigabite` on your PATH
  • installs the /search and /core-setup commands (user-level)
  • wires the router block and three hooks into ~/.claude: ambient recall on every
    prompt, a background index refresh when a session starts, and a guard that
    makes every agent spawn name its model
  • offers to enable the "AI brain" integrations (Granola, more soon)
  • builds the initial index
Nothing here overwrites content you already have. Re-run any time.
"""

INSTALL_HELP_WINDOWS = """\
gigabite installer: idempotent, additive, non-destructive.
  - creates ~\\.core and ~\\Knowledge (copies templates only if absent), and on a
    first install opens ~\\Knowledge in Explorer so you know it exists
  - checks `gigabite` is on your PATH (uv put it there)
  - installs the /search and /core-setup commands (user-level)
  - wires the router block and three hooks into ~\\.claude: ambient recall on every
    prompt, a background index refresh when a session starts, and a guard that
    makes every agent spawn name its model
  - builds the initial index
Nothing here overwrites content you already have. Re-run any time.

  gigabite install              install, one line per step
  gigabite install --verbose    every file it touched, one line each
"""


def wanted_hooks(cdir: Path, exe: Optional[str]) -> list:
    """(event, matcher, hook) for the three hooks, in this platform's shape."""
    out = []
    for event, matcher, name in HOOKS:
        if WINDOWS:
            hook = {"type": "command", "command": exe, "args": ["hook", name]}
        else:
            hook = {"type": "command", "command": str(cdir / "gigabite" / ("gg-%s.sh" % name))}
        out.append((event, matcher, hook))
    return out


def _present(entries: list, hook: dict) -> Optional[bool]:
    """None when *hook* is not registered in *entries*, True when it is exactly,
    False when it is but under an old path (Windows: rewritten in place)."""
    if not WINDOWS:
        # Any mention of the script counts, as install.sh had it: a user who
        # wrapped ours in a command of their own is not given a second one.
        return True if hook["command"] in json.dumps(entries, ensure_ascii=False) else None
    name = hook["args"][1]
    for entry in entries:
        inner = entry.get("hooks") if isinstance(entry, dict) else None
        for existing in (inner if isinstance(inner, list) else ()):
            if _exec_name(existing) == name:
                if all(existing.get(key) == value for key, value in hook.items()):
                    return True
                # gigabite.exe moved: repoint it, and keep every key of the
                # user's own on the entry (a "timeout", say).
                existing.update(hook)
                return False
    return None


def register_hooks(settings: Path, wanted: list) -> str:
    """Add the hooks to settings.json once. Never clobbers what it cannot parse."""
    cfg = {}
    if settings.exists():
        try:
            cfg = _read_json(settings)
        except Exception:
            # Never clobber a file we couldn't parse: back it up and bail out.
            shutil.copy2(str(settings), str(settings) + ".gigabite.bak")
            return "unparseable"
        if not isinstance(cfg, dict):
            shutil.copy2(str(settings), str(settings) + ".gigabite.bak")
            return "unparseable"
    hooks = cfg.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return "hooks-not-dict"                # leave the user's config untouched
    added = []
    for event, matcher, hook in wanted:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            return "%s-not-list" % event
        state = _present(entries, hook)
        if state is True:
            continue
        if state is None:
            # The nested form Claude Code's settings schema uses.
            entry = {"hooks": [hook]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            entries.append(entry)
        added.append(event)
    if not added:
        return "exists"
    # The same convention as CLAUDE.md: a copy of what was there before the edit.
    if settings.is_file() and settings.stat().st_size:
        shutil.copyfile(str(settings), str(settings) + ".gigabite-bak")
    _write_json(settings, cfg)
    return "added:" + ",".join(added)


def sync_router_block(path: Path, block: str) -> str:
    """Keep the marker-bounded block in CLAUDE.md in sync. Only the block moves."""
    current, eol = _read_md(path)
    i, j = current.find(START), current.find(END)
    if i == -1:
        updated = (current.rstrip("\n") + "\n\n" + block + "\n") if current.strip() else block + "\n"
        status = "added"
    elif j == -1 or j < i:
        return "corrupt"            # a start marker with no usable end: unsafe to bound
    else:
        if current[i:j + len(END)] == block:
            return "current"
        updated = current[:i] + block + current[j + len(END):]
        status = "updated"
    # A zero-byte file (the installer's own) has nothing worth a backup, and the
    # backup would be residue that outlives an uninstall.
    if current:
        shutil.copyfile(str(path), str(path) + ".gigabite-bak")
    _write_md(path, updated, eol)
    return status


def _open_folder(path: Path) -> bool:
    """Show *path* in Finder or Explorer. GIGABITE_OPEN names the opener, so a
    test run opens no windows."""
    opener = os.environ.get("GIGABITE_OPEN") or ("" if WINDOWS else "open")
    try:
        if opener:
            return subprocess.run([opener, str(path)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
        os.startfile(str(path))                                  # type: ignore[attr-defined]
        return True
    except (OSError, AttributeError):
        return False


def install(argv: list) -> int:
    verbose = os.environ.get("GIGABITE_VERBOSE", "0") == "1"
    for arg in argv:
        if arg in ("-v", "--verbose"):
            verbose = True
        elif arg in ("-h", "--help"):
            text = INSTALL_HELP_WINDOWS if WINDOWS else INSTALL_HELP
            sys.stdout.write(text + "\n")
            return 0
        else:
            # Refused rather than ignored: a mistyped --verbose that quietly
            # produced a quiet install is the failure this flag exists to prevent.
            die("unknown option: %s" % arg, "Run %s --help" % _script("install"))

    # Every per-item confirmation goes through here. On the success path the
    # installer prints one line per numbered step, because the last step is the
    # only screen written for a first-time reader. Loud in either mode: every
    # warn, anything skipped or backed up, every instruction needed to undo.
    def detail(fn, text):
        if verbose:
            fn(text)

    repo = config.REPO_ROOT
    if not (repo / "install").is_dir():
        die("gigabite install reads its templates from the gigabite repository, and "
            "there is none at %s." % repo,
            "Install it from a clone, editable, so the templates stay next to it:",
            "    uv tool install --editable <path to the clone>")
    home = Path.home()
    cdir = config.claude_dir()
    core, know = config.CORE_DIR, config.KNOWLEDGE_DIR
    bin_path = repo / "bin" / "gigabite"
    exe = osutil.gigabite_exe() if WINDOWS else None
    if WINDOWS:
        # osutil.launcher's rule: a path that needs quoting would not run in
        # PowerShell, so plain `gigabite` (on PATH through uv) instead.
        launcher = exe if exe and shlex.quote(exe) == exe else "gigabite"
    else:
        launcher = str(bin_path)

    if not WINDOWS:
        # macOS refuses launchd jobs access to these folders. The optional Granola
        # pull is one, and would fail there with only "Operation not permitted".
        homes = {str(home), str(home.resolve())}
        if any(str(repo).startswith("%s/%s/" % (h, sub)) for h in homes
               for sub in ("Desktop", "Documents", "Downloads")):
            warn("gigabite is in %s — macOS blocks background jobs there, so the" % repo.parent)
            note("optional Granola daily pull could not run. Moving the clone to ~/gigabite and")
            note("re-running ./install.sh avoids that; everything else works where it is.")

    # Claude Code, by its own traces: not ~/.claude, which this creates itself.
    # With CLAUDE_CONFIG_DIR set, Claude Code keeps its .claude.json in there.
    has_claude = bool(shutil.which("claude") or (home / ".claude.json").exists()
                      or (cdir / "projects").is_dir()
                      or (os.environ.get("CLAUDE_CONFIG_DIR") and (cdir / ".claude.json").exists()))
    # A first install is the one that creates the machinery folder.
    first_install = not config.MACHINE_DIR.is_dir()

    # -----------------------------------------------------------------------
    say("1/8  Creating the local store layout")
    config.ensure_dirs()
    ok("core: %s  ·  knowledge: %s" % (core, know))
    # Consumed by the SessionStart hook in the first Claude Code session, which
    # says once what the install made: when Claude Code runs the installer, the
    # user sees only Claude's summary of it.
    if first_install:
        (config.MACHINE_DIR / "welcome-pending").write_bytes(b"")

    def copy_if_absent(src: Path, dest: Path):
        if os.path.exists(dest):
            detail(note, "kept existing %s" % _label(dest))
        else:
            shutil.copyfile(str(src), str(dest))
            detail(ok, "seeded %s" % _label(dest))

    def refresh_doc(src: Path, dest: Path):
        # The knowledge README is instructions, not content, and a stale one sends
        # you to a folder that no longer exists. Refreshed, the old text set aside.
        if dest.exists() and filecmp.cmp(str(src), str(dest), shallow=False):
            detail(note, "up to date %s" % _label(dest))
            return
        if dest.exists():
            kept_dir = config.MACHINE_DIR / "originals"
            kept_dir.mkdir(parents=True, exist_ok=True)
            kept = kept_dir / ("replaced-%s-%s" % (datetime.date.today().isoformat(), dest.name))
            os.replace(str(dest), str(kept))
            warn("%s was out of date — refreshed (old text kept as %s)" % (_label(dest), kept.name))
        else:
            detail(ok, "seeded %s" % _label(dest))
        shutil.copyfile(str(src), str(dest))

    copy_if_absent(repo / "install" / "scaffold" / "core.md", core / "core.md")
    refresh_doc(repo / "docs" / "KNOWLEDGE.md", know / "README.md")

    # -----------------------------------------------------------------------
    say("2/8  Putting gigabite on your PATH")
    if WINDOWS:
        _windows_path_step()
    else:
        _macos_path_step(home, bin_path)

    # -----------------------------------------------------------------------
    say("3/8  Installing Claude Code commands (user-level)")
    cmd_dir = cdir / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    # A slash command by one of our names may be the user's own work, so a file
    # that shows no sign of being ours is left exactly as it is.
    wrote = 0
    for name in ("search", "core-setup"):
        src = repo / "install" / "claude-commands" / ("%s.md" % name)
        if not src.exists():
            continue
        dest = cmd_dir / ("%s.md" % name)
        if dest.exists() and not is_ours(dest):
            warn("kept your own /%s — gigabite did not write that file, so it is untouched" % name)
            continue
        dest.write_bytes(src.read_bytes().replace(b"__GIGABITE_BIN__", launcher.encode("utf-8")))
        detail(ok, "/%s" % name)
        wrote += 1
    # Commands, subagents and skills an older install wrote that no longer ship.
    # A copy left behind keeps triggering and calls commands that are gone.
    # Removed only when ours; a skill's directory goes only if that empties it.
    for name in ("recall-status", "granola", "gg", "search-status", "calendar", "meeting"):
        stale = cmd_dir / ("%s.md" % name)
        if stale.exists() and is_ours(stale):
            stale.unlink()
            note("removed /%s — it is no longer part of gigabite" % name)
    for name in ("gg-builder", "gg-researcher", "gg-reviewer"):
        stale = cdir / "agents" / ("%s.md" % name)
        if stale.exists() and is_ours(stale):
            stale.unlink()
            note("removed subagent %s — it is no longer part of gigabite" % name)
    for name in ("meeting-prep", "decision-record", "design-critique"):
        stale = cdir / "skills" / name / "SKILL.md"
        if stale.exists() and is_ours(stale):
            stale.unlink()
            note("removed skill %s — it is no longer part of gigabite" % name)
            try:
                stale.parent.rmdir()
            except OSError:
                pass
    ok("slash commands installed: %d" % wrote)

    # -----------------------------------------------------------------------
    say("4/8  Wiring the conversational layer (router protocol + recall, refresh and agent-model hooks)")
    global_claude = cdir / "CLAUDE.md"
    if not global_claude.exists():
        global_claude.write_bytes(b"")
    block = _read_md(repo / "install" / "scaffold" / "CLAUDE.md")[0].strip("\n")
    # The same placeholder the commands use, so the block names the launcher by
    # absolute path rather than trusting PATH in Claude Code's shell.
    block = block.replace("__GIGABITE_BIN__", launcher)
    claude_md_t = osutil.tilde(global_claude)
    try:
        router = sync_router_block(global_claude, block)
    except (OSError, ValueError):
        router = "error"
    if router == "added":
        detail(ok, "added router protocol to %s" % claude_md_t)
    elif router == "updated":
        ok("updated router protocol in %s (backup: CLAUDE.md.gigabite-bak)" % claude_md_t)
    elif router == "current":
        detail(note, "router protocol already up to date")
    elif router == "corrupt":
        note("router markers in %s look damaged — left untouched" % claude_md_t)
    else:
        note("could not sync router protocol in %s" % claude_md_t)

    settings = cdir / "settings.json"
    settings_t = osutil.tilde(settings)
    hook_dir = cdir / "gigabite"
    if not WINDOWS:
        # Each wrapper is installed with the launcher's absolute path baked in, and
        # registered by absolute path, so none depends on the PATH Claude Code
        # runs hooks with.
        hook_dir.mkdir(parents=True, exist_ok=True)
        for script in HOOK_SCRIPTS:
            dest = hook_dir / script
            dest.write_bytes((repo / "install" / "hooks" / script).read_bytes()
                             .replace(b"__GIGABITE_BIN__", str(bin_path).encode("utf-8")))
            dest.chmod(dest.stat().st_mode | 0o111)
    if WINDOWS and not exe:
        status = "no-exe"
    else:
        try:
            status = register_hooks(settings, wanted_hooks(cdir, exe))
        except Exception:
            status = "error"
    if status.startswith("added:"):
        backup = Path(str(settings) + ".gigabite-bak")
        detail(ok, "hooks registered (%s)%s" % (status[len("added:"):],
                                                " (backup: settings.json.gigabite-bak)"
                                                if backup.exists() else ""))
    elif status == "exists":
        detail(note, "hooks already registered")
    elif status == "unparseable":
        warn("%s isn't valid JSON — backed it up to .gigabite.bak and did NOT modify it. "
             "Add the hooks manually or fix the file and re-run." % settings_t)
    elif status == "no-exe":
        # A hook pointing at nothing would fail every prompt.
        warn("could not find gigabite.exe, so no hooks were registered. Open a new "
             "terminal and run `gigabite install` again.")
    elif WINDOWS:
        warn("could not register the hooks automatically (%s). Add them to %s by hand: "
             "%s with args [\"hook\", \"recall\"] on UserPromptSubmit, [\"hook\", \"refresh\"] "
             "on SessionStart, [\"hook\", \"agent-model\"] on PreToolUse matching Agent|Task."
             % (status, settings_t, exe))
    else:
        warn("could not register the hooks automatically (%s). The scripts are in %s; add "
             "them to settings.json manually (gg-recall.sh on UserPromptSubmit, gg-refresh.sh "
             "on SessionStart, gg-agent-model.sh on PreToolUse matching Agent|Task)."
             % (status, hook_dir))
    if not WINDOWS:
        # The daily 18:00 job earlier installs scheduled is retired: the refresh
        # hook does its one remaining job. Unloaded through the seam, and only when
        # its plist is there, so a fresh install never touches launchd at all.
        old_plist = home / "Library" / "LaunchAgents" / "com.gigabite.synthesis.plist"
        if old_plist.exists():
            try:
                subprocess.run([_launchctl(), "bootout", "gui/%d/com.gigabite.synthesis" % os.getuid()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
            for stale in (old_plist, home / "Library" / "Logs" / "gigabite-synthesis.log"):
                try:
                    stale.unlink()
                except OSError:
                    pass
            note("retired the old 18:00 daily job — the index now refreshes when a "
                 "Claude Code session starts")
    if not has_claude:
        warn("Claude Code isn't installed yet — all of this switches on once it is")
    # Hooks that run by themselves are not something to leave someone unable to
    # switch off, so the step's one line carries the way out.
    ok("router protocol + recall, refresh and agent-model hooks (remove them from "
       "%s to disable)" % settings_t)

    # -----------------------------------------------------------------------
    say('5/8  Enabling the "AI brain" integrations (Granola, more soon)')
    if WINDOWS:
        note("Granola and the other integrations are macOS-only, so this step is skipped on Windows")
    elif _stdin_is_tty():
        rc = _run_cli(["integrations"])
        if rc:
            return rc
    else:
        note("run 'gigabite integrations' later to enable Granola and future integrations")

    # -----------------------------------------------------------------------
    say("6/8  Personalising the operating protocol")
    # The interview is a conversation, so it lives in Claude Code. Deferred when
    # nobody is at a terminal: `curl ... | bash` leaves stdin a consumed pipe, and
    # anything that waits for a keystroke would hang the install.
    core_file = core / "core.md"

    def core_unfilled() -> bool:
        try:
            return "[FILL]" in core_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    if not core_unfilled():
        ok("your protocol is already filled in — left untouched")
    elif _stdin_is_tty() and has_claude:
        _run_cli(["core", "interview"])
        note("run /core-setup in Claude Code to answer these — nothing is written without you")
    else:
        note("core.md still has [FILL] sections — run /core-setup in Claude Code to fill them in")

    # -----------------------------------------------------------------------
    say("7/8  Building the initial index")
    if _run_cli(["ingest"]):
        warn("ingest reported issues (see above)")

    # -----------------------------------------------------------------------
    say("8/8  Done")
    say()
    # Reports on the user's own work, and hands them one command verified to find
    # something in it.
    if _run_cli(["welcome"]):
        warn("installed, but couldn't summarise the index — try: gigabite welcome")

    know_t = osutil.tilde(know)
    # A folder appearing on screen is harder to miss than any line of output.
    # First install only, and not over SSH, where there is no screen.
    opened = False
    if (first_install and not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
            and _open_folder(know)):
        opened = True
        note("opened %s in %s — that's where your notes and files go" % (know_t, _finder()))

    # Claude Code sets CLAUDECODE=1 in the shells it runs. Then the reader of all
    # of this is the assistant, which relays a summary, so say it to the assistant.
    if os.environ.get("CLAUDECODE") == "1":
        made = "created a folder at" if first_install else "keeps everything in"
        shown = " (now open in %s)" % _finder() if opened else ""
        say()
        say("── For the assistant running this install ──")
        for line in (
            "The person who asked for this install cannot see this output. Tell them",
            "these points in your reply, in plain words, before anything else:",
            "  • gigabite %s %s%s. It holds their notes," % (made, know_t, shown),
            "    meeting transcripts and files, one subfolder per project, and everything",
            "    in it is searchable from Claude Code. What goes where: %s"
            % os.path.join(know_t, "README.md"),
        ):
            _out(line)
        if core_unfilled():
            _out("  • Their working rules for Claude are in %s/core.md, still unfilled."
                 % osutil.tilde(core))
            _out("    /core-setup in Claude Code fills them in, one question at a time.")
        _out("  • Quit and restart Claude Code. Nothing is live until then.")
    return 0


def _windows_path_step() -> None:
    # uv owns PATH on Windows (`uv tool update-shell`); this only checks.
    found = shutil.which("gigabite")
    if found:
        ok("gigabite is on your PATH: %s" % found)
    else:
        warn("gigabite is not on PATH in this terminal yet")
        note("open a new terminal and it will be. If it still isn't, run:  uv tool update-shell")


def _macos_path_step(home: Path, bin_path: Path) -> None:
    # Whatever `gigabite` the current PATH already finds, looked up before linking.
    other = shutil.which("gigabite") or ""
    installed = ""
    for d in install_bin_dirs(home):
        if not d:
            continue
        link = "%s/gigabite" % d
        if os.path.lexists(link) and not _points_into_checkout(link):
            warn("left %s alone — it is not gigabite's launcher" % link)
            continue
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            continue
        if not os.access(d, os.W_OK):
            continue
        try:
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(str(bin_path), link)
        except OSError:
            continue
        installed = link
        break
    if other and other != installed and not _points_into_checkout(other):
        warn("another program called gigabite is at %s — left alone" % other)
    if not installed:
        warn("couldn't write to a PATH dir; use the launcher directly: %s" % bin_path)
        return
    bin_dir = os.path.dirname(installed)
    if bin_dir in os.environ.get("PATH", "").split(":"):
        ok("linked %s" % installed)
        return
    line = 'export PATH="%s:$PATH"  # added by gigabite' % bin_dir
    # zsh is the macOS login shell, so .zshrc is always written (created if need
    # be). The bash files only when they already exist: creating a .bash_profile
    # would make bash stop reading a ~/.profile the user relies on. The blank line
    # goes in only when there is something to separate the export from.
    added = False
    for rc in (home / ".zshrc", home / ".bash_profile", home / ".bashrc"):
        if rc.name != ".zshrc" and not rc.is_file():
            continue
        try:
            if "added by gigabite" in rc.read_text(encoding="utf-8", errors="replace"):
                continue
        except OSError:
            pass
        lead = "\n" if rc.is_file() and rc.stat().st_size > 0 else ""
        with open(rc, "a", encoding="utf-8") as fh:
            fh.write(lead + line + "\n")
        added = True
    if added:
        ok("linked %s — and added %s to PATH in your shell startup file" % (installed, bin_dir))
    else:
        ok("linked %s (%s is already on PATH in your shell startup file)" % (installed, bin_dir))
    # Loud in either mode: without it the command just given does not exist in
    # the shell they are standing in.
    note('open a new terminal, or run:  export PATH="%s:$PATH"' % bin_dir)


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

UNINSTALL_HELP = """\
gigabite uninstaller — reverses install.sh, and touches nothing else.
  • removes the `gigabite` launcher from your PATH and the line it added to your shell rc
  • removes the Claude Code commands (and any subagents or skills an older install wrote)
  • removes the router block from ~/.claude/CLAUDE.md and its hooks from settings.json
  • unloads and removes every com.gigabite.* launchd job, and the gigabite logs
  • removes the folders it created, once they are empty
Your knowledge base (~/Knowledge) and your operating protocol (~/.core) are never
touched, and a file that gigabite did not write is left where it is and reported.
Keys in your keychain are left too; the commands to delete them are printed.

  ./uninstall.sh              show the plan, then ask
  ./uninstall.sh --dry-run    show the plan and stop
  ./uninstall.sh --yes        skip the question (required when stdin is a pipe)
"""

UNINSTALL_HELP_WINDOWS = """\
gigabite uninstaller: reverses `gigabite install`, and touches nothing else.
  - removes the Claude Code commands (and any subagents or skills an older install wrote)
  - removes the router block from ~\\.claude\\CLAUDE.md and its hooks from settings.json
  - removes the folders it created, once they are empty
Your knowledge base (~\\Knowledge) and your operating protocol (~\\.core) are never
touched, and a file that gigabite did not write is left where it is and reported.
gigabite itself stays installed; the last line says how to remove it.

  gigabite uninstall              show the plan, then ask
  gigabite uninstall --dry-run    show the plan and stop
  gigabite uninstall --yes        skip the question (required when stdin is a pipe)
"""


def remove_hooks(cfg: dict) -> bool:
    """Drop gigabite's hooks from a parsed settings.json, in place. True if any.

    Every event, not just the three the installer uses: an older install or a
    hand edit may have put ours elsewhere. Filtered at the inner hook level: an
    entry may carry the user's own hook alongside ours under one matcher.
    """
    def ours(x) -> bool:
        return _exec_name(x) is not None or any(n in json.dumps(x) for n in HOOK_SCRIPTS)

    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept, touched = [], False
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                inner = [h for h in entry["hooks"] if not ours(h)]
                if len(inner) != len(entry["hooks"]):
                    touched = True
                    if not inner:
                        continue
                    entry = dict(entry, hooks=inner)
                kept.append(entry)
            elif ours(entry):
                touched = True
            else:
                kept.append(entry)
        if touched:
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if changed and not hooks:
        del cfg["hooks"]
    return changed


class _Uninstall:
    """Every step runs twice: once as the plan, once for real. Sharing one code
    path is what makes the plan trustworthy."""

    def __init__(self):
        self.mode = "plan"
        self.planned = 0
        self.planned_paths = set()
        self.kept: List[str] = []
        self.backups: List[str] = []
        self.home = Path.home()
        self.cdir = config.claude_dir()
        self.core, self.know = config.CORE_DIR, config.KNOWLEDGE_DIR
        self.repo = config.REPO_ROOT

    # -- the single points of decision --------------------------------------

    def plan_path(self, path) -> None:
        self.planned += 1
        self.planned_paths.add(str(path))

    def protected(self, path) -> bool:
        """The two directories holding the only things a clone cannot rebuild."""
        text = os.path.normcase(str(path))
        for root in (self.core, self.know):
            r = os.path.normcase(str(root))
            if text == r or text.startswith(r.rstrip(os.sep) + os.sep):
                return True
        return False

    def keep(self, path, reason: str) -> None:
        warn("kept %s — %s" % (path, reason))
        if self.mode == "apply":
            self.kept.append("  %s — %s" % (path, reason))

    def remove_path(self, path, desc: str) -> None:
        """Every deletion goes through here, and refuses ~/Knowledge and ~/.core."""
        path = str(path)
        if self.protected(path):
            warn("refused to remove %s — that is inside your knowledge base or ~/.core" % path)
            return
        if not os.path.lexists(path):
            note("already gone: %s" % desc)
            return
        if self.mode == "plan":
            self.plan_path(path)
            note("remove %s" % path)
            return
        try:
            os.remove(path)
        except OSError as exc:
            warn("could not remove %s (%s)" % (path, exc.strerror or exc))
            return
        ok("removed %s" % desc)

    def remove_managed(self, path: Path, desc: str) -> None:
        if not path.exists():
            note("already gone: %s" % desc)
        elif not is_ours(path):
            self.keep(path, "gigabite did not write that file")
        else:
            self.remove_path(path, desc)

    def leftover_in(self, d) -> List[str]:
        """What will still be in *d* once this run is through with it."""
        try:
            names = os.listdir(str(d))
        except OSError:
            return []
        out = []
        for name in names:
            entry = os.path.join(str(d), name)
            if self.mode == "plan" and entry in self.planned_paths:
                continue
            out.append(entry)
        return out

    def remove_dir_if_empty(self, d, desc: str) -> None:
        if not os.path.isdir(str(d)):
            note("already gone: %s" % desc)
        elif self.protected(d):
            warn("refused to remove %s — that is inside your knowledge base or ~/.core" % d)
        elif self.leftover_in(d):
            self.keep(d, "it still holds files gigabite did not write")
        elif self.mode == "plan":
            self.plan_path(d)
            note("remove %s/" % d)
        else:
            try:
                os.rmdir(str(d))
                ok("removed %s" % desc)
            except OSError:
                pass

    def tidy_dir(self, d) -> None:
        """A container the installer may have created. Removed only when empty, and
        silent otherwise: other tools' files in it are the normal case."""
        d = str(d)
        if not os.path.isdir(d) or os.path.islink(d) or self.protected(d) or self.leftover_in(d):
            return
        if self.mode == "plan":
            self.plan_path(d)
            note("remove the empty folder %s/" % d)
        else:
            try:
                os.rmdir(d)
                ok("removed the empty folder %s/" % d)
            except OSError:
                pass

    # -- steps ---------------------------------------------------------------

    def step_path(self) -> None:
        say("1/6  Taking gigabite off your PATH")
        if WINDOWS:
            note("uv keeps gigabite on your PATH; the last step says how to remove it")
            return
        dirs = uninstall_bin_dirs(self.home)
        found = False
        for d in dirs:
            if not d:
                continue
            link = os.path.join(d, "gigabite")
            if os.path.islink(link):
                # Removed only when it resolves into a gigabite checkout: an
                # unrelated binary called `gigabite` is someone else's.
                if _points_into_checkout(link):
                    self.remove_path(link, "the gigabite launcher at %s" % link)
                else:
                    self.keep(link, "it does not point into a gigabite checkout")
                found = True
            elif os.path.exists(link):
                self.keep(link, "it is not a symlink, so gigabite did not put it there")
                found = True
        if not found:
            note("already gone: the gigabite launcher (no link in %s)" % ":".join(dirs))
        # Container directories, once the link is out of them: only those in HOME.
        for d in dirs:
            if d.startswith("%s/" % self.home):
                self.tidy_dir(d)
        self.tidy_dir(self.home / ".local")
        # The PATH export the installer appended, by its tag, with the blank line
        # put in front of it. A file left with nothing in it was the installer's.
        for rc in (self.home / ".zshrc", self.home / ".bash_profile", self.home / ".bashrc"):
            if not rc.is_file():
                continue
            text = rc.read_text(encoding="utf-8", errors="replace")
            if "added by gigabite" not in text:
                note("already gone: the PATH line in %s" % rc)
            elif self.mode == "plan":
                self.planned += 1
                note("remove the '# added by gigabite' PATH line from %s" % rc)
            else:
                out = []
                for line in text.splitlines(keepends=True):
                    if "added by gigabite" in line:
                        if out and not out[-1].strip():
                            out.pop()
                        continue
                    out.append(line)
                if not "".join(out).strip():
                    rc.unlink()
                    ok("removed %s — the PATH line was all it held" % rc)
                else:
                    with open(rc, "w", encoding="utf-8") as fh:
                        fh.writelines(out)
                    ok("removed the PATH line from %s" % rc)

    def step_claude_files(self) -> None:
        say("2/6  Removing the Claude Code commands, subagents and skills")
        cmd_dir, agent_dir, skill_dir = (self.cdir / "commands", self.cdir / "agents",
                                         self.cdir / "skills")
        # The current commands, plus every one an older install may have left.
        for name in ("gg", "search", "search-status", "calendar", "meeting", "core-setup",
                     "recall-status", "granola"):
            self.remove_managed(cmd_dir / ("%s.md" % name), "/%s" % name)
        for name in ("gg-builder", "gg-researcher", "gg-reviewer"):
            self.remove_managed(agent_dir / ("%s.md" % name), "subagent %s" % name)
        for name in ("meeting-prep", "decision-record", "design-critique"):
            self.remove_managed(skill_dir / name / "SKILL.md", "skill %s" % name)
            if (skill_dir / name).is_dir():
                self.remove_dir_if_empty(skill_dir / name, "skill directory %s" % name)
        for d in (cmd_dir, agent_dir, skill_dir):
            self.tidy_dir(d)

    def _router(self, path: Path) -> str:
        if not path.exists():
            return "absent"
        current, eol = _read_md(path)
        i, j = current.find(START), current.find(END)
        if i == -1:
            # An end marker with no start is damage, not absence.
            return "corrupt" if j != -1 else "absent"
        if j == -1 or j < i:
            return "corrupt"
        before = current[:i].rstrip("\n")
        after = current[j + len(END):].lstrip("\n")
        remainder = [part for part in (before, after) if part.strip()]
        if self.mode == "plan":
            return "block" if remainder else "block-file"
        if not remainder:
            # Nothing but the block, which is ours: no backup, it would be residue.
            path.unlink()
            return "removed-file"
        shutil.copyfile(str(path), str(path) + ".gigabite-bak")
        _write_md(path, "\n\n".join(remainder).rstrip("\n") + "\n", eol)
        return "removed"

    def _settings(self, path: Path) -> str:
        if not path.exists():
            return "absent"
        try:
            cfg = _read_json(path)
            if not isinstance(cfg, dict):
                raise ValueError
        except Exception:
            # A file we cannot parse is a file we cannot edit safely.
            if self.mode != "plan":
                shutil.copy2(str(path), str(path) + ".gigabite.bak")
            return "unparseable"
        if not remove_hooks(cfg):
            return "absent"
        if self.mode == "plan":
            return "hook" if cfg else "hook-file"
        if not cfg:
            # Our hooks were all it held: the installer created it.
            path.unlink()
            return "removed-file"
        shutil.copy2(str(path), str(path) + ".gigabite.bak")
        _write_json(path, cfg)
        return "removed"

    def step_conversational(self) -> None:
        say("3/6  Unwiring the conversational layer (router protocol + recall, refresh and "
            "agent-model hooks)")
        global_claude = self.cdir / "CLAUDE.md"
        settings = self.cdir / "settings.json"
        claude_md_t, settings_t = osutil.tilde(global_claude), osutil.tilde(settings)
        # Older installs backed up the zero-byte CLAUDE.md they had just created. An
        # empty backup holds nothing of the user's; a non-empty one is theirs.
        empty_bak = Path(str(global_claude) + ".gigabite-bak")
        if empty_bak.is_file() and empty_bak.stat().st_size == 0:
            self.remove_path(empty_bak, "the empty CLAUDE.md backup an older install left")

        try:
            status = self._router(global_claude)
        except (OSError, ValueError) as exc:
            status = "error: %s" % exc
        if status == "block":
            self.planned += 1
            note("remove the router block from %s" % global_claude)
        elif status == "block-file":
            self.plan_path(global_claude)
            note("remove %s — the router block is all it holds" % global_claude)
        elif status == "removed":
            ok("removed the router block from %s (backup: CLAUDE.md.gigabite-bak)" % claude_md_t)
            self.backups.append("  %s.gigabite-bak" % global_claude)
        elif status == "removed-file":
            ok("removed %s — the router block was all it held" % claude_md_t)
        elif status == "absent":
            note("already gone: the router block in %s" % claude_md_t)
        elif status == "corrupt":
            warn("router markers in %s look damaged — left the file untouched" % claude_md_t)
            if self.mode == "apply":
                self.kept.append("  %s — its router markers are damaged; remove the block by hand"
                                 % global_claude)
        else:
            warn("could not read the router block in %s (%s) — left it untouched"
                 % (claude_md_t, status))

        try:
            status = self._settings(settings)
        except OSError as exc:
            status = "error: %s" % exc
        if status == "hook":
            self.planned += 1
            note("remove the recall, refresh and agent-model hooks from %s" % settings)
        elif status == "hook-file":
            self.plan_path(settings)
            note("remove %s — gigabite's hooks are all it holds" % settings)
        elif status == "removed":
            ok("removed the recall, refresh and agent-model hooks from %s (backup: "
               "settings.json.gigabite.bak)" % settings_t)
            self.backups.append("  %s.gigabite.bak" % settings)
        elif status == "removed-file":
            ok("removed %s — gigabite's hooks were all it held" % settings_t)
        elif status == "absent":
            note("already gone: the hooks in %s" % settings_t)
        elif status == "unparseable":
            warn("%s isn't valid JSON — backed it up to .gigabite.bak and did NOT modify it"
                 % settings_t)
            if self.mode == "apply":
                what = ("gigabite's recall, refresh and agent-model hooks" if WINDOWS else
                        "the gg-recall.sh, gg-refresh.sh and gg-agent-model.sh hooks")
                self.kept.append("  %s — it is not valid JSON; remove %s by hand" % (settings, what))
        else:
            warn("could not read %s (%s) — left it untouched" % (settings_t, status))

        hook_dir = self.cdir / "gigabite"
        if not WINDOWS or hook_dir.exists():
            for script, what in zip(HOOK_SCRIPTS, ("the ambient recall hook script",
                                                   "the index refresh hook script",
                                                   "the agent-model hook script")):
                self.remove_path(hook_dir / script, what)
            if hook_dir.is_dir():
                self.remove_dir_if_empty(hook_dir, str(hook_dir))
        self.tidy_dir(self.cdir)

    def step_launchd(self) -> None:
        """Every com.gigabite.* job, found by its plist: a job left loaded keeps
        firing at a checkout that is about to be deleted."""
        if WINDOWS:
            say("4/6  Unscheduling gigabite's background jobs")
            note("already gone: gigabite schedules nothing on Windows")
            return
        say("4/6  Unscheduling gigabite's launchd jobs")
        la_dir = self.home / "Library" / "LaunchAgents"
        plists = sorted(la_dir.glob("com.gigabite.*.plist")) if la_dir.is_dir() else []
        for plist in plists:
            label = plist.name[:-len(".plist")]
            if self.mode == "apply":
                launchctl = _launchctl()
                if shutil.which(launchctl):
                    # Not being loaded is the normal case after a reboot.
                    try:
                        rc = subprocess.run([launchctl, "bootout", "gui/%d/%s" % (os.getuid(), label)],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL).returncode
                    except OSError:
                        rc = 1
                    if rc == 0:
                        ok("unloaded %s" % label)
                    else:
                        note("%s was not loaded" % label)
                else:
                    warn("launchctl not found — skipped unloading %s" % label)
            else:
                note("unload %s" % label)
            self.remove_path(plist, "the LaunchAgent plist %s" % label)
        if not plists:
            note("already gone: no com.gigabite.* launchd jobs")
        self.tidy_dir(la_dir)                 # created by older installs; never ~/Library

    def step_log(self) -> None:
        say("5/6  Removing gigabite's logs")
        if WINDOWS:
            note("already gone: gigabite keeps no logs outside your knowledge base on Windows")
            return
        log_dir = self.home / "Library" / "Logs"
        logs = []
        if log_dir.is_dir():
            logs = sorted(log_dir.glob("gigabite-*.log")) + sorted(log_dir.glob("gigabite-*.log.trim"))
        for log in logs:
            self.remove_path(log, str(log))
        if not logs:
            note("already gone: no gigabite logs in %s" % log_dir)
        self.tidy_dir(log_dir)

    def run_steps(self) -> None:
        self.step_path()
        self.step_claude_files()
        self.step_conversational()
        self.step_launchd()
        self.step_log()

    # -- the closing notes ---------------------------------------------------

    def keychain_services(self) -> List[str]:
        """The keychain services gigabite reads, taken from the code rather than
        listed here, so a new integration cannot be forgotten."""
        found = set()
        for path in (self.repo / "gigabite").rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Split so this line is not itself a match.
            found.update(re.findall("KEYCHAIN_SERVICE" + r' = "([^"]+)"', text))
        return sorted(found)

    def kept_content_note(self) -> None:
        note("your knowledge base is untouched:  %s" % self.know)
        note("your operating protocol is untouched:  %s" % (self.core / "core.md"))
        if WINDOWS:
            return
        services = self.keychain_services()
        if services:
            # Printed, never run: deleting a secret is the user's call, and even
            # looking one up can raise a keychain prompt.
            note("any key you stored in the keychain (gigabite granola-login) stays there; "
                 "to delete it:")
            for svc in services:
                note("    security delete-generic-password -s %s" % svc)

    def the_checkout_note(self, nothing_left: bool) -> None:
        if WINDOWS:
            note("gigabite itself, and its copy at %s, are still here. To remove them:" % self.repo)
            say("    uv tool uninstall gigabite")
            say('    Remove-Item -Recurse -Force "%s"' % self.repo)
        elif nothing_left:
            note("the clone itself is still here: rm -rf %s" % self.repo)
        else:
            note("the clone is still here, because this script is running from inside it:")
            say("    rm -rf %s" % self.repo)


def uninstall(argv: list) -> int:
    assume_yes = dry_run = False
    for arg in argv:
        if arg in ("-y", "--yes"):
            assume_yes = True
        elif arg in ("-n", "--dry-run"):
            dry_run = True
        elif arg in ("-h", "--help"):
            text = UNINSTALL_HELP_WINDOWS if WINDOWS else UNINSTALL_HELP
            sys.stdout.write(text + "\n")
            return 0
        else:
            die("unknown option: %s" % arg, "Run %s --help" % _script("uninstall"))

    run = _Uninstall()
    say("gigabite uninstall — this is the plan")
    say()
    run.run_steps()
    say()
    say("Left alone, deliberately")
    run.kept_content_note()
    note("gigabite writes nothing else into either, and this script removes nothing from them")
    say()

    if run.planned == 0:
        say("Nothing left to remove — gigabite is already uninstalled")
        run.the_checkout_note(nothing_left=True)
        return 0
    if dry_run:
        say("--dry-run: %d item(s) would be removed. Nothing was touched." % run.planned)
        return 0
    if not assume_yes:
        if not _stdin_is_tty():
            die("Not running in a terminal, so there is nobody to ask.",
                "Re-run with --yes to remove the %d item(s) above, or --dry-run to just see them:"
                % run.planned,
                "    %s --yes" % _script("uninstall"))
        sys.stdout.write("Remove the %d item(s) above? [y/N] " % run.planned)
        sys.stdout.flush()
        reply = sys.stdin.readline().strip()
        if reply not in ("y", "Y", "yes", "Yes", "YES"):
            say("Nothing removed.")
            return 0
        say()

    run.mode = "apply"
    say("gigabite uninstall — removing")
    say()
    run.run_steps()
    say()
    say("6/6  Done")
    run.kept_content_note()
    if run.kept:
        say()
        note("left in place because gigabite did not write them, or could not prove it did:")
        for line in run.kept:
            _out(line)
    if run.backups:
        say()
        note("backups taken before editing your files — delete them when you are happy:")
        for line in run.backups:
            _out(line)
    say()
    run.the_checkout_note(nothing_left=False)
    return 0


def main(command: str, argv: list) -> int:
    """`gigabite install` / `gigabite uninstall`."""
    try:
        return (install if command == "install" else uninstall)(list(argv))
    except _Stop as stop:
        return stop.code
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 130
