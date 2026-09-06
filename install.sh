#!/usr/bin/env bash
# gigabite installer — idempotent, additive, non-destructive.
#   • creates ~/.core and ~/Knowledge layout (copies templates only if absent)
#   • puts `gigabite` on your PATH
#   • installs /search and /search-status Claude Code commands (user-level)
#   • builds the initial index
# Nothing here overwrites content you already have. Re-run any time.
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BIN="$REPO/bin/gigabite"
chmod +x "$BIN"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  \033[2m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

CORE_DIR="${GIGABITE_CORE_DIR:-$HOME/.core}"
KNOW_DIR="${GIGABITE_KNOWLEDGE_DIR:-$HOME/Knowledge}"

# ---------------------------------------------------------------------------
say "1/7  Creating the local store layout"
"$BIN" paths >/dev/null           # triggers ensure_dirs()
mkdir -p "$CORE_DIR/capability"    # `paths` above created the knowledge layout
ok "core:      $CORE_DIR"
ok "knowledge: $KNOW_DIR"

copy_if_absent() { # src dest
  if [ -e "$2" ]; then note "kept existing $(label "$2")"; else cp "$1" "$2"; ok "seeded $(label "$2")"; fi
}
# Several scaffold files are called README.md, so a bare basename tells you nothing
# about which one the installer just touched. Show the parent folder with it.
label() { printf '%s/%s' "$(basename "$(dirname "$1")")" "$(basename "$1")"; }
# The knowledge README is instructions, not your content: it tells you where things
# go. A stale one sends you to a folder that no longer exists, which is worse than
# losing a note you wrote in it — so it is refreshed rather than kept, and the old
# text is set aside first. Nothing is destroyed; the installer's promise holds.
refresh_doc() { # src dest
  if [ -e "$2" ] && cmp -s "$1" "$2"; then note "up to date $(label "$2")"; return; fi
  if [ -e "$2" ]; then
    # Set aside inside the machinery folder, so the replaced copy is kept without
    # appearing in the knowledge base as a stray file.
    kept_dir="$KNOW_DIR/.gigabite/originals"
    mkdir -p "$kept_dir"
    kept="$kept_dir/replaced-$(date +%Y-%m-%d)-$(basename "$2")"
    mv "$2" "$kept"
    warn "$(label "$2") was out of date — refreshed (old text kept as $(basename "$kept"))"
  else
    ok "seeded $(label "$2")"
  fi
  cp "$1" "$2"
}
copy_if_absent "$REPO/install/scaffold/core.md"              "$CORE_DIR/core.md"
copy_if_absent "$REPO/install/scaffold/capability-README.md" "$CORE_DIR/capability/README.md"
refresh_doc    "$REPO/install/scaffold/knowledge-README.md"  "$KNOW_DIR/README.md"

# SOPs the subagents load at runtime
if [ -d "$REPO/install/scaffold/sops" ]; then
  mkdir -p "$CORE_DIR/capability/sops"
  for sop in "$REPO/install/scaffold/sops/"*.md; do
    [ -e "$sop" ] && copy_if_absent "$sop" "$CORE_DIR/capability/sops/$(basename "$sop")"
  done
fi

# ---------------------------------------------------------------------------
say "2/7  Putting gigabite on your PATH"
INSTALLED=""
for d in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/bin"; do
  if mkdir -p "$d" 2>/dev/null && [ -w "$d" ]; then
    ln -sf "$BIN" "$d/gigabite" && INSTALLED="$d/gigabite" && ok "linked $d/gigabite" && break
  fi
done
if [ -z "$INSTALLED" ]; then
  warn "couldn't write to a PATH dir; use the launcher directly: $BIN"
else
  BIN_DIR="$(dirname "$INSTALLED")"
  case ":$PATH:" in
    *":$BIN_DIR:"*) : ;;                       # already on PATH
    *)
      LINE="export PATH=\"$BIN_DIR:\$PATH\"  # added by gigabite"
      for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
        touch "$rc"
        grep -qF "added by gigabite" "$rc" 2>/dev/null || printf '\n%s\n' "$LINE" >> "$rc"
      done
      ok "added $BIN_DIR to PATH (in .zshrc/.bash_profile)"
      note "open a new terminal, or run:  export PATH=\"$BIN_DIR:\$PATH\""
      ;;
  esac
fi

# ---------------------------------------------------------------------------
say "3/7  Installing Claude Code commands + subagents (user-level)"
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
# A slash command or agent by one of our names may already be the user's own work.
# Overwriting it unconditionally spent something they never agreed to risk, and did
# it silently. So a file that shows no sign of being ours is left exactly as it is.
#
# "Ours" is any file carrying the marker, or — because installs predating the marker
# have none — any file that mentions gigabite at all: a command we wrote embeds the
# launcher's path, and an agent we wrote names the chain it belongs to. The trade is
# deliberate. Someone else's file that happens to say "gigabite" is far rarer than an
# existing install needing its update, and only the second failure is certain.
is_ours() { grep -qi "gigabite" "$1" 2>/dev/null; }
install_managed() { # src dest label
  if [ -e "$2" ] && ! is_ours "$2"; then
    warn "kept your own $3 — gigabite did not write that file, so it is untouched"
    return
  fi
  sed "s|__GIGABITE_BIN__|$BIN|g" "$1" > "$2"
  ok "$3"
}
for f in gg search search-status calendar meeting; do
  [ -e "$REPO/install/claude-commands/$f.md" ] || continue
  install_managed "$REPO/install/claude-commands/$f.md" "$CMD_DIR/$f.md" "/$f"
done
# Commands that have been renamed: /recall-status -> /search-status, because it
# reports on the search index and "recall" named the mechanism rather than the thing
# being asked about; /granola -> /meeting, because the tool it came from is one
# person's habit and the job is filing a meeting. The old file keeps working, so
# leaving it behind would mean two commands for one job. Removed only when it is
# ours, on the same test as everything else here.
for stale_cmd in recall-status granola; do
  STALE="$CMD_DIR/$stale_cmd.md"
  if [ -e "$STALE" ] && is_ours "$STALE"; then
    rm -f "$STALE" && note "removed /$stale_cmd — it has been renamed"
  fi
done
if [ -d "$REPO/install/scaffold/agents" ]; then
  AGENT_DIR="$HOME/.claude/agents"
  mkdir -p "$AGENT_DIR"
  for a in "$REPO/install/scaffold/agents/"*.md; do
    [ -e "$a" ] || continue
    install_managed "$a" "$AGENT_DIR/$(basename "$a")" "subagent $(basename "$a" .md)"
  done
fi


# ---------------------------------------------------------------------------
say "4/7  Wiring the conversational layer (router protocol + ambient recall)"
# Router constitution: keep a marker-bounded managed block in ~/.claude/CLAUDE.md in
# sync with the scaffold. Only the block is touched; the user's own content is kept.
GLOBAL_CLAUDE="$HOME/.claude/CLAUDE.md"
touch "$GLOBAL_CLAUDE"
ROUTER_STATUS=$(ROUTER_SRC="$REPO/install/scaffold/CLAUDE.md" /usr/bin/python3 - "$GLOBAL_CLAUDE" <<'PY'
import os, sys, shutil

path = sys.argv[1]
block = open(os.environ["ROUTER_SRC"], encoding="utf-8").read().strip("\n")
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

shutil.copyfile(path, path + ".gigabite-bak")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(updated)
print(status)
PY
)
case "$ROUTER_STATUS" in
  added)   ok "added router protocol to ~/.claude/CLAUDE.md" ;;
  updated) ok "updated router protocol in ~/.claude/CLAUDE.md (backup: CLAUDE.md.gigabite-bak)" ;;
  current) note "router protocol already up to date" ;;
  corrupt) note "router markers in ~/.claude/CLAUDE.md look damaged — left untouched" ;;
  *)       note "could not sync router protocol in ~/.claude/CLAUDE.md" ;;
esac
# Ambient recall hook: install script + register UserPromptSubmit in settings.json.
HOOK_DIR="$HOME/.claude/gigabite"
mkdir -p "$HOOK_DIR"
sed "s|__GIGABITE_BIN__|$BIN|g" "$REPO/install/hooks/gg-recall.sh" > "$HOOK_DIR/gg-recall.sh"
chmod +x "$HOOK_DIR/gg-recall.sh"
HOOK_STATUS=$(GIGABITE_HOOK="$HOOK_DIR/gg-recall.sh" /usr/bin/python3 - "$HOME/.claude/settings.json" <<'PY'
import json, os, sys, shutil
path = sys.argv[1]; hook = os.environ["GIGABITE_HOOK"]
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
ups = hooks.setdefault("UserPromptSubmit", [])
if not isinstance(ups, list):
    print("ups-not-list"); sys.exit(0)
if hook in json.dumps(ups):
    print("exists"); sys.exit(0)
ups.append({"hooks": [{"type": "command", "command": hook}]})
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
print("added")
PY
) || HOOK_STATUS="error"
case "$HOOK_STATUS" in
  added)         ok "ambient recall hook registered (UserPromptSubmit). Remove it from ~/.claude/settings.json to disable." ;;
  exists)        note "ambient recall hook already registered" ;;
  unparseable)   warn "~/.claude/settings.json isn't valid JSON — backed it up to .gigabite.bak and did NOT modify it. Add the hook manually or fix the file and re-run." ;;
  *)             warn "could not register the recall hook automatically ($HOOK_STATUS). Hook script is at $HOOK_DIR/gg-recall.sh; add it to settings.json manually." ;;
esac

# ---------------------------------------------------------------------------
say "5/7  Scheduling the gated end-of-day synthesis (launchd)"
DAILY="$REPO/bin/gigabite-daily"; chmod +x "$DAILY"
LOG="$HOME/Library/Logs/gigabite-synthesis.log"
LA_DIR="$HOME/Library/LaunchAgents"; PLIST="$LA_DIR/com.gigabite.synthesis.plist"
mkdir -p "$LA_DIR" "$(dirname "$LOG")"
sed -e "s|__DAILY_BIN__|$DAILY|g" -e "s|__LOG__|$LOG|g" \
    "$REPO/install/launchd/com.gigabite.synthesis.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/com.gigabite.synthesis" 2>/dev/null || true
if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
  ok "scheduled: gigabite synthesize + decay daily at 18:00 (gated; nothing auto-applies)"
else
  warn "installed the LaunchAgent plist but couldn't load it now; it will load at next login. ($PLIST)"
fi
note "disable with: launchctl bootout gui/$(id -u)/com.gigabite.synthesis"

# ---------------------------------------------------------------------------
say "6/7  Building the initial index"
"$BIN" ingest || warn "ingest reported issues (see above)"

# ---------------------------------------------------------------------------
say "7/7  Done"
echo
# `status` reports on a database; this reports on the user's own work and hands
# them one command that is verified to find something in it. Read-only, and
# re-runnable at any time with `gigabite welcome`.
"$BIN" welcome || warn "installed, but couldn't summarise the index — try: gigabite welcome"
