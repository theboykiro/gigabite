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
say "1/5  Creating the local store layout"
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

# ---------------------------------------------------------------------------
say "2/5  Putting gigabite on your PATH"
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
say "3/5  Installing Claude Code commands (user-level)"
CMD_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DIR"
for f in search recall-status; do
  sed "s|__GIGABITE_BIN__|$BIN|g" "$REPO/claude-commands/$f.md" > "$CMD_DIR/$f.md"
  ok "/$f"
done

# ---------------------------------------------------------------------------
say "4/5  Building the initial index"
"$BIN" ingest || warn "ingest reported issues (see above)"

# ---------------------------------------------------------------------------
say "5/5  Done"
"$BIN" status || true
echo
note "Search from the terminal:   gigabite search \"...\""
note "Search from Claude Code:     /search ...   (works from any folder)"
note "Add Claude.ai chats:         drop your export in $KNOW_DIR/_inbox/claude_ai/"
note "Add Granola notes:           see $KNOW_DIR/_inbox/granola/README.md"
