#!/usr/bin/env bash
# gigabite installer — idempotent, additive, non-destructive.
#   • creates ~/.core and ~/.knowledge layout (copies templates only if absent)
#   • puts `gigabite` on your PATH
#   • installs /search and /recall-status Claude Code commands (user-level)
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
KNOW_DIR="${GIGABITE_KNOWLEDGE_DIR:-$HOME/.knowledge}"

# ---------------------------------------------------------------------------
say "1/6  Creating the local store layout"
"$BIN" paths >/dev/null           # triggers ensure_dirs()
mkdir -p "$CORE_DIR/capability" "$KNOW_DIR/_inbox/claude_ai" "$KNOW_DIR/_inbox/granola"
ok "core:      $CORE_DIR"
ok "knowledge: $KNOW_DIR"

copy_if_absent() { # src dest
  if [ -e "$2" ]; then note "kept existing $(basename "$2")"; else cp "$1" "$2"; ok "seeded $(basename "$2")"; fi
}
copy_if_absent "$REPO/scaffold/core.md"              "$CORE_DIR/core.md"
copy_if_absent "$REPO/scaffold/capability-README.md" "$CORE_DIR/capability/README.md"
copy_if_absent "$REPO/scaffold/knowledge-README.md"  "$KNOW_DIR/README.md"
copy_if_absent "$REPO/scaffold/inbox-claude_ai.md"   "$KNOW_DIR/_inbox/claude_ai/README.md"
copy_if_absent "$REPO/scaffold/inbox-granola.md"     "$KNOW_DIR/_inbox/granola/README.md"

# SOPs the subagents load at runtime
if [ -d "$REPO/scaffold/sops" ]; then
  mkdir -p "$CORE_DIR/capability/sops"
  for sop in "$REPO/scaffold/sops/"*.md; do
    [ -e "$sop" ] && copy_if_absent "$sop" "$CORE_DIR/capability/sops/$(basename "$sop")"
  done
fi

# ---------------------------------------------------------------------------
say "2/6  Putting gigabite on your PATH"
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
say "3/6  Installing Claude Code commands + subagents (user-level)"
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
for f in gg search recall-status calendar; do
  [ -e "$REPO/claude-commands/$f.md" ] || continue
  sed "s|__GIGABITE_BIN__|$BIN|g" "$REPO/claude-commands/$f.md" > "$CMD_DIR/$f.md"
  ok "/$f"
done
if [ -d "$REPO/scaffold/agents" ]; then
  AGENT_DIR="$HOME/.claude/agents"
  mkdir -p "$AGENT_DIR"
  for a in "$REPO/scaffold/agents/"*.md; do
    [ -e "$a" ] && cp "$a" "$AGENT_DIR/$(basename "$a")" && ok "subagent $(basename "$a" .md)"
  done
fi

# ---------------------------------------------------------------------------
say "4/6  Wiring the conversational layer (router protocol + ambient recall)"
# Router constitution: append a managed block to ~/.claude/CLAUDE.md (never clobber).
GLOBAL_CLAUDE="$HOME/.claude/CLAUDE.md"
touch "$GLOBAL_CLAUDE"
if grep -qF "gigabite:router:start" "$GLOBAL_CLAUDE" 2>/dev/null; then
  note "router protocol already in ~/.claude/CLAUDE.md"
else
  printf '\n' >> "$GLOBAL_CLAUDE"; cat "$REPO/scaffold/CLAUDE.md" >> "$GLOBAL_CLAUDE"
  ok "added router protocol to ~/.claude/CLAUDE.md"
fi
# Ambient recall hook: install script + register UserPromptSubmit in settings.json.
HOOK_DIR="$HOME/.claude/gigabite"
mkdir -p "$HOOK_DIR"
sed "s|__GIGABITE_BIN__|$BIN|g" "$REPO/hooks/gg-recall.sh" > "$HOOK_DIR/gg-recall.sh"
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
say "5/6  Building the initial index"
"$BIN" ingest || warn "ingest reported issues (see above)"

# ---------------------------------------------------------------------------
say "6/6  Done"
"$BIN" status || true
echo
note "Search from the terminal:   gigabite search \"...\""
note "Search from Claude Code:     /search ...   (works from any folder)"
note "Add Claude.ai chats:         drop your export in $KNOW_DIR/_inbox/claude_ai/"
note "Add Granola notes:           see $KNOW_DIR/_inbox/granola/README.md"
