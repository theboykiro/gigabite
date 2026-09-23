#!/usr/bin/env bash
# gigabite installer — idempotent, additive, non-destructive.
#   • creates ~/.core and ~/Knowledge layout (copies templates only if absent)
#   • puts `gigabite` on your PATH
#   • installs the /search and /core-setup commands (user-level)
#   • wires the router block and two hooks into ~/.claude: ambient recall on every
#     prompt, and a background index refresh when a session starts
#   • offers to enable the "AI brain" integrations (Granola, more soon)
#   • builds the initial index
# Nothing here overwrites content you already have. Re-run any time.
#
#   ./install.sh                install, one line per step
#   ./install.sh --verbose      every file it touched, one line each
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BIN="$REPO/bin/gigabite"
chmod +x "$BIN"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  \033[2m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; shift; for l in "$@"; do note "$l" >&2; done; exit 1; }

VERBOSE="${GIGABITE_VERBOSE:-0}"
while [ $# -gt 0 ]; do
  case "$1" in
    -v|--verbose) VERBOSE=1 ;;
    -h|--help)
      sed -n '2,13p' "$REPO/install.sh" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    # Refused rather than ignored: a mistyped --verbose that quietly produced a
    # quiet install is the one failure this flag exists to prevent.
    *) die "unknown option: $1" "Run ./install.sh --help" ;;
  esac
  shift
done
# Every per-item confirmation goes through here. On the success path the installer
# prints one line per numbered step, because the last step is the only screen written for
# a first-time reader, and a wall of green ticks is what they scroll past to miss
# it. Nothing is dropped, only gated: --verbose (or GIGABITE_VERBOSE=1) restores
# the lot, which is what to ask someone for when their install misbehaves.
# Loud in either mode: every warn, anything skipped or backed up, and every
# instruction the user needs in order to undo something.
detail() { [ "$VERBOSE" = 1 ] || return 0; "$@"; }

CORE_DIR="${GIGABITE_CORE_DIR:-$HOME/.core}"
KNOW_DIR="${GIGABITE_KNOWLEDGE_DIR:-$HOME/Knowledge}"
# The PATH directories to try, in order, and the command that unloads the retired
# daily job. Overridable for one reason: the installer's behaviour has to be
# exercisable against a throwaway HOME, and a test that had to write outside it or
# touch the live launchd domain is a test nobody may run twice. uninstall.sh names
# both the same way, so the two scripts can be pointed at the same fake machine.
#
# ~/.local/bin only. Earlier installs took the first writable of /opt/homebrew/bin,
# /usr/local/bin, ... — which wrote outside HOME and replaced whatever `gigabite`
# already lived there. uninstall.sh still searches those, to clean up after them.
BIN_DIRS="${GIGABITE_BIN_DIRS:-$HOME/.local/bin}"
LAUNCHCTL="${GIGABITE_LAUNCHCTL:-launchctl}"

# ---------------------------------------------------------------------------
# Preflight — the same rule bootstrap.sh applies, for anyone who cloned by hand.
# The launcher runs /usr/bin/python3 directly (bin/gigabite), so that is the one
# checked. Without Apple's command line tools it is a stub that pops a dialog and
# fails, so ask for the tools first rather than letting Python fail confusingly.
PY=/usr/bin/python3
xcode-select -p >/dev/null 2>&1 || die \
  "Apple's command line tools are missing, and gigabite runs on the Python they ship." \
  "Run this, click through the installer, wait for it to finish:" \
  "    xcode-select --install" \
  "Then run ./install.sh again."
[ -x "$PY" ] || die \
  "Python is missing from this Mac (expected it at $PY)." \
  "Reinstall Apple's command line tools:  xcode-select --install"
PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
[ -n "$PY_VERSION" ] || die \
  "Python is on this Mac but will not start, so gigabite cannot run." \
  "Reinstalling Apple's command line tools usually fixes it:  xcode-select --install"
"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' || die \
  "gigabite needs Python 3.9 or later, and this Mac has $PY_VERSION." \
  "That version comes with macOS, so the fix is a macOS update — check" \
  "System Settings > General > Software Update."

# macOS refuses launchd jobs access to these folders. Nothing gigabite installs by
# default is a launchd job any more, but the optional Granola daily pull is, and it
# would fail there with nothing but "Operation not permitted" in its log.
# REPO is a physical path (cd -P), so HOME is compared both as given and resolved.
HOME_P="$(cd -P "$HOME" 2>/dev/null && pwd || printf '%s' "$HOME")"
case "$REPO" in
  "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*|\
  "$HOME_P"/Desktop/*|"$HOME_P"/Documents/*|"$HOME_P"/Downloads/*)
    warn "gigabite is in $(dirname "$REPO") — macOS blocks background jobs there, so the"
    note "optional Granola daily pull could not run. Moving the clone to ~/gigabite and"
    note "re-running ./install.sh avoids that; everything else works where it is." ;;
esac

# Claude Code, by its own traces — not ~/.claude, which this script creates itself,
# so testing for that would say yes on every second run.
has_claude_code() {
  command -v claude >/dev/null 2>&1 || [ -e "$HOME/.claude.json" ] || [ -d "$HOME/.claude/projects" ]
}
HAS_CLAUDE=0
has_claude_code && HAS_CLAUDE=1

# ---------------------------------------------------------------------------
say "1/8  Creating the local store layout"
"$BIN" paths >/dev/null           # triggers ensure_dirs()
ok "core: $CORE_DIR  ·  knowledge: $KNOW_DIR"

copy_if_absent() { # src dest
  if [ -e "$2" ]; then detail note "kept existing $(label "$2")"; else cp "$1" "$2"; detail ok "seeded $(label "$2")"; fi
}
# Several scaffold files are called README.md, so a bare basename tells you nothing
# about which one the installer just touched. Show the parent folder with it.
label() { printf '%s/%s' "$(basename "$(dirname "$1")")" "$(basename "$1")"; }
# The knowledge README is instructions, not your content: it tells you where things
# go. A stale one sends you to a folder that no longer exists, which is worse than
# losing a note you wrote in it — so it is refreshed rather than kept, and the old
# text is set aside first. Nothing is destroyed; the installer's promise holds.
refresh_doc() { # src dest
  if [ -e "$2" ] && cmp -s "$1" "$2"; then detail note "up to date $(label "$2")"; return; fi
  if [ -e "$2" ]; then
    # Set aside inside the machinery folder, so the replaced copy is kept without
    # appearing in the knowledge base as a stray file.
    kept_dir="$KNOW_DIR/.gigabite/originals"
    mkdir -p "$kept_dir"
    kept="$kept_dir/replaced-$(date +%Y-%m-%d)-$(basename "$2")"
    mv "$2" "$kept"
    warn "$(label "$2") was out of date — refreshed (old text kept as $(basename "$kept"))"
  else
    detail ok "seeded $(label "$2")"
  fi
  cp "$1" "$2"
}
copy_if_absent "$REPO/install/scaffold/core.md"              "$CORE_DIR/core.md"
refresh_doc    "$REPO/install/scaffold/knowledge-README.md"  "$KNOW_DIR/README.md"

# ---------------------------------------------------------------------------
say "2/8  Putting gigabite on your PATH"
# A `gigabite` that is a symlink into a gigabite checkout is ours to replace (an
# older install, or another clone). Anything else by that name is somebody else's
# program, and it is left exactly where it is — uninstall.sh applies the same test.
is_gigabite_link() { # path
  [ -L "$1" ] || return 1
  local target root
  target="$(readlink "$1")"
  case "$target" in /*) : ;; *) target="$(dirname "$1")/$target" ;; esac
  root="$(dirname "$(dirname "$target")")"
  [ -f "$root/install.sh" ] && [ -f "$root/gigabite/__init__.py" ]
}
# Whatever `gigabite` the current PATH already finds, looked up before linking.
OTHER="$(command -v gigabite 2>/dev/null || true)"
INSTALLED=""
OLD_IFS="$IFS"
IFS=:
set -- $BIN_DIRS                       # split on ':' without losing spaces in a path
IFS="$OLD_IFS"
for d in "$@"; do
  [ -n "$d" ] || continue
  if { [ -e "$d/gigabite" ] || [ -L "$d/gigabite" ]; } && ! is_gigabite_link "$d/gigabite"; then
    warn "left $d/gigabite alone — it is not gigabite's launcher"
    continue
  fi
  if mkdir -p "$d" 2>/dev/null && [ -w "$d" ]; then
    ln -sf "$BIN" "$d/gigabite" && INSTALLED="$d/gigabite" && break
  fi
done
if [ -n "$OTHER" ] && [ "$OTHER" != "$INSTALLED" ] && ! is_gigabite_link "$OTHER"; then
  warn "another program called gigabite is at $OTHER — left alone"
fi
if [ -z "$INSTALLED" ]; then
  warn "couldn't write to a PATH dir; use the launcher directly: $BIN"
else
  BIN_DIR="$(dirname "$INSTALLED")"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ok "linked $INSTALLED" ;;  # already on PATH
    *)
      LINE="export PATH=\"$BIN_DIR:\$PATH\"  # added by gigabite"
      # zsh is the macOS login shell, so .zshrc is always written (created if need
      # be). The bash files only when they already exist: creating a .bash_profile
      # would make bash stop reading a ~/.profile the user relies on. The blank
      # line goes in only when there is something to separate the export from;
      # uninstall.sh takes it out again.
      PATH_ADDED=0
      for rc in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do
        [ "$rc" = "$HOME/.zshrc" ] || [ -f "$rc" ] || continue
        grep -qF "added by gigabite" "$rc" 2>/dev/null && continue
        if [ -s "$rc" ]; then printf '\n%s\n' "$LINE" >> "$rc"; else printf '%s\n' "$LINE" >> "$rc"; fi
        PATH_ADDED=1
      done
      if [ "$PATH_ADDED" = 1 ]; then
        ok "linked $INSTALLED — and added $BIN_DIR to PATH in your shell startup file"
      else
        ok "linked $INSTALLED ($BIN_DIR is already on PATH in your shell startup file)"
      fi
      # Loud in either mode: without it the command they were just given does
      # not exist in the shell they are standing in.
      note "open a new terminal, or run:  export PATH=\"$BIN_DIR:\$PATH\""
      ;;
  esac
fi

# ---------------------------------------------------------------------------
say "3/8  Installing Claude Code commands (user-level)"
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
# A slash command by one of our names may already be the user's own work.
# Overwriting it unconditionally spent something they never agreed to risk, and did
# it silently. So a file that shows no sign of being ours is left exactly as it is.
#
# "Ours" is any file carrying the marker, or — because installs predating the marker
# have none — any file that mentions gigabite at all: a command we wrote embeds the
# launcher's path, and an agent we wrote names the chain it belongs to. The trade is
# deliberate. Someone else's file that happens to say "gigabite" is far rarer than an
# existing install needing its update, and only the second failure is certain.
is_ours() { grep -qi "gigabite" "$1" 2>/dev/null; }
# WROTE counts what these loops actually wrote, so each group can report a number
# instead of a line per file. The count is the only thing that gets quieter: the
# "kept your own" warn below is the reason this function exists, and it is printed
# in either mode.
WROTE=0
install_managed() { # src dest label
  if [ -e "$2" ] && ! is_ours "$2"; then
    warn "kept your own $3 — gigabite did not write that file, so it is untouched"
    return
  fi
  sed "s|__GIGABITE_BIN__|$BIN|g" "$1" > "$2"
  detail ok "$3"
  WROTE=$((WROTE + 1))
}
for f in search core-setup; do
  [ -e "$REPO/install/claude-commands/$f.md" ] || continue
  install_managed "$REPO/install/claude-commands/$f.md" "$CMD_DIR/$f.md" "/$f"
done
CMD_N=$WROTE
# Commands an older install wrote that no longer ship: /recall-status and /granola
# were renamed away; /gg, /search-status, /calendar and /meeting were retired (the
# ambient hook, `gigabite status` and `gigabite paste` cover them). A file left
# behind keeps working and calls commands that are gone. Removed only when it is
# ours, on the same test as everything else here.
for stale_cmd in recall-status granola gg search-status calendar meeting; do
  STALE="$CMD_DIR/$stale_cmd.md"
  if [ -e "$STALE" ] && is_ours "$STALE"; then
    rm -f "$STALE" && note "removed /$stale_cmd — it is no longer part of gigabite"
  fi
done
# Subagents and skills an older install wrote. They are deferred until after the
# alpha, and a copy left behind keeps triggering on its own and calls commands that
# are gone. Removed only when ours; a skill's directory goes only if that empties it.
for a in gg-builder gg-researcher gg-reviewer; do
  STALE="$HOME/.claude/agents/$a.md"
  if [ -e "$STALE" ] && is_ours "$STALE"; then
    rm -f "$STALE" && note "removed subagent $a — it is no longer part of gigabite"
  fi
done
for skill in meeting-prep decision-record design-critique; do
  STALE="$HOME/.claude/skills/$skill/SKILL.md"
  if [ -e "$STALE" ] && is_ours "$STALE"; then
    rm -f "$STALE" && note "removed skill $skill — it is no longer part of gigabite"
    rmdir "$HOME/.claude/skills/$skill" 2>/dev/null || true
  fi
done
ok "slash commands installed: $CMD_N"


# ---------------------------------------------------------------------------
say "4/8  Wiring the conversational layer (router protocol + recall and refresh hooks)"
# Router constitution: keep a marker-bounded managed block in ~/.claude/CLAUDE.md in
# sync with the scaffold. Only the block is touched; the user's own content is kept.
GLOBAL_CLAUDE="$HOME/.claude/CLAUDE.md"
touch "$GLOBAL_CLAUDE"
ROUTER_STATUS=$(ROUTER_SRC="$REPO/install/scaffold/CLAUDE.md" GIGABITE_BIN="$BIN" /usr/bin/python3 - "$GLOBAL_CLAUDE" <<'PY'
import os, sys, shutil

path = sys.argv[1]
block = open(os.environ["ROUTER_SRC"], encoding="utf-8").read().strip("\n")
# The same placeholder the commands and hooks use, so the block can name the
# launcher by absolute path rather than trusting PATH in Claude Code's shell.
block = block.replace("__GIGABITE_BIN__", os.environ["GIGABITE_BIN"])
start, end = "<!-- gigabite:router:start -->", "<!-- gigabite:router:end -->"
current = open(path, encoding="utf-8").read()

i, j = current.find(start), current.find(end)
if i == -1:
    updated = (current.rstrip("\n") + "\n\n" + block + "\n") if current.strip() else block + "\n"
    status = "added"
elif j == -1 or j < i:
    # Start marker with no usable end marker: the block is unsafe to bound. Leave it.
    print("corrupt"); sys.exit(0)
else:
    existing = current[i:j + len(end)]
    if existing == block:
        print("current"); sys.exit(0)
    updated = current[:i] + block + current[j + len(end):]
    status = "updated"

# A zero-byte file (this script's own `touch`) has nothing worth a backup, and the
# backup would be residue that outlives an uninstall.
if current:
    shutil.copyfile(path, path + ".gigabite-bak")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(updated)
print(status)
PY
)
# Routine outcomes are folded into this step's one line, below. An outcome that
# left a backup behind, or left the file alone, is not routine and is printed.
case "$ROUTER_STATUS" in
  added)   detail ok "added router protocol to ~/.claude/CLAUDE.md" ;;
  updated) ok "updated router protocol in ~/.claude/CLAUDE.md (backup: CLAUDE.md.gigabite-bak)" ;;
  current) detail note "router protocol already up to date" ;;
  corrupt) note "router markers in ~/.claude/CLAUDE.md look damaged — left untouched" ;;
  *)       note "could not sync router protocol in ~/.claude/CLAUDE.md" ;;
esac
# The two hooks. Each script is installed with the launcher's absolute path baked in,
# and registered by absolute path, so neither depends on the PATH Claude Code runs
# hooks with:
#   UserPromptSubmit → gg-recall.sh   recalls prior context into every prompt
#   SessionStart     → gg-refresh.sh  refreshes the index in the background, so the
#                                     work from earlier today is recallable now
HOOK_DIR="$HOME/.claude/gigabite"
mkdir -p "$HOOK_DIR"
for h in gg-recall gg-refresh; do
  sed "s|__GIGABITE_BIN__|$BIN|g" "$REPO/install/hooks/$h.sh" > "$HOOK_DIR/$h.sh"
  chmod +x "$HOOK_DIR/$h.sh"
done
HOOK_STATUS=$(GIGABITE_HOOK_DIR="$HOOK_DIR" /usr/bin/python3 - "$HOME/.claude/settings.json" <<'PY'
import json, os, sys, shutil
path = sys.argv[1]; hook_dir = os.environ["GIGABITE_HOOK_DIR"]
WANTED = (("UserPromptSubmit", os.path.join(hook_dir, "gg-recall.sh")),
          ("SessionStart", os.path.join(hook_dir, "gg-refresh.sh")))
cfg = {}
if os.path.exists(path):
    try:
        with open(path) as fh:
            cfg = json.load(fh)
    except Exception:
        # Never clobber a file we couldn't parse — back it up and bail out.
        shutil.copy2(path, path + ".gigabite.bak")
        print("unparseable"); sys.exit(0)
    if not isinstance(cfg, dict):
        shutil.copy2(path, path + ".gigabite.bak")
        print("unparseable"); sys.exit(0)
hooks = cfg.setdefault("hooks", {})
if not isinstance(hooks, dict):
    print("hooks-not-dict"); sys.exit(0)          # leave user's config untouched
added = []
for event, hook in WANTED:
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        print(f"{event}-not-list"); sys.exit(0)
    if hook in json.dumps(entries):
        continue
    # The nested form Claude Code's settings schema uses; no matcher, so it fires
    # for every prompt / every way a session starts.
    entries.append({"hooks": [{"type": "command", "command": hook}]})
    added.append(event)
if not added:
    print("exists"); sys.exit(0)
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
print("added:" + ",".join(added))
PY
) || HOOK_STATUS="error"
case "$HOOK_STATUS" in
  added:*)       detail ok "hooks registered (${HOOK_STATUS#added:})" ;;
  exists)        detail note "hooks already registered" ;;
  unparseable)   warn "~/.claude/settings.json isn't valid JSON — backed it up to .gigabite.bak and did NOT modify it. Add the hooks manually or fix the file and re-run." ;;
  *)             warn "could not register the hooks automatically ($HOOK_STATUS). The scripts are in $HOOK_DIR; add them to settings.json manually (gg-recall.sh on UserPromptSubmit, gg-refresh.sh on SessionStart)." ;;
esac
# The daily 18:00 launchd job earlier installs scheduled is retired: the refresh
# hook above does its one remaining job (ingest) when the index is about to be
# used, instead of at an hour the Mac may be asleep. Unloaded through the seam, and
# only when its plist is there, so a fresh install never touches launchd at all.
OLD_PLIST="$HOME/Library/LaunchAgents/com.gigabite.synthesis.plist"
if [ -e "$OLD_PLIST" ]; then
  "$LAUNCHCTL" bootout "gui/$(id -u)/com.gigabite.synthesis" >/dev/null 2>&1 || true
  rm -f "$OLD_PLIST" "$HOME/Library/Logs/gigabite-synthesis.log"
  note "retired the old 18:00 daily job — the index now refreshes when a Claude Code session starts"
fi
[ "$HAS_CLAUDE" = 1 ] || warn "Claude Code isn't installed yet — all of this switches on once it is"
# The step's one line. It carries the way out, because hooks that run by themselves
# are not something to leave someone unable to switch off.
ok "router protocol + recall and refresh hooks (remove them from ~/.claude/settings.json to disable)"

# ---------------------------------------------------------------------------
say "5/8  Enabling the \"AI brain\" integrations (Granola, more soon)"
if [ -t 0 ]; then
  "$BIN" integrations
else
  note "run 'gigabite integrations' later to enable Granola and future integrations"
fi

# ---------------------------------------------------------------------------
say "6/8  Personalising the operating protocol"
# The protocol ships with sections marked [FILL], and an install that leaves them
# there leaves the user with a generic assistant. The interview that fills them in
# is a conversation, so it lives in Claude Code, not here.
#
# Same guard as the integrations step, for the same reason: the documented install
# is `curl … | bash`, so stdin is a consumed pipe with no terminal behind it, and
# anything that waits for a keystroke hangs the install outright. Deferred with a
# printed instruction instead. `|| true` because `set -e` is on and a protocol that
# is merely unfinished must not abort an otherwise good install.
CORE_FILL=0
grep -q '\[FILL\]' "$CORE_DIR/core.md" 2>/dev/null && CORE_FILL=1
if [ "$CORE_FILL" = 0 ]; then
  ok "your protocol is already filled in — left untouched"
elif [ -t 0 ] && [ "$HAS_CLAUDE" = 1 ]; then
  "$BIN" core interview || true
  note "run /core-setup in Claude Code to answer these — nothing is written without you"
else
  note "core.md still has [FILL] sections — run /core-setup in Claude Code to fill them in"
fi

# ---------------------------------------------------------------------------
say "7/8  Building the initial index"
"$BIN" ingest || warn "ingest reported issues (see above)"

# ---------------------------------------------------------------------------
say "8/8  Done"
echo
# `status` reports on a database; this reports on the user's own work and hands
# them one command that is verified to find something in it. Read-only, and
# re-runnable at any time with `gigabite welcome`.
"$BIN" welcome || warn "installed, but couldn't summarise the index — try: gigabite welcome"
