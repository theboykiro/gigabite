#!/usr/bin/env bash
# gigabite uninstaller — reverses install.sh, and touches nothing else.
#   • removes the `gigabite` launcher from your PATH and the line it added to your shell rc
#   • removes the Claude Code commands, subagents and skills the installer wrote
#   • removes the router block from ~/.claude/CLAUDE.md and the recall hook from settings.json
#   • unloads and removes the scheduled daily synthesis job and its log
# Your knowledge base (~/Knowledge) and your operating protocol (~/.core) are never
# touched, and a file that gigabite did not write is left where it is and reported.
#
#   ./uninstall.sh              show the plan, then ask
#   ./uninstall.sh --dry-run    show the plan and stop
#   ./uninstall.sh --yes        skip the question (required when stdin is a pipe)
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  \033[2m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; shift; for l in "$@"; do note "$l" >&2; done; exit 1; }

CORE_DIR="${GIGABITE_CORE_DIR:-$HOME/.core}"
KNOW_DIR="${GIGABITE_KNOWLEDGE_DIR:-$HOME/Knowledge}"
# The four directories install.sh tries, in its order. Overridable for the same
# reason the stores are: the safety properties of this script have to be exercisable
# against a throwaway HOME, and a test that had to name the machine's real PATH
# directories would be a test nobody could run twice.
BIN_DIRS="${GIGABITE_BIN_DIRS:-/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/bin}"
# Same seam, same reason: booting a job out of the live launchd domain is not
# something a test may do, so the command is named rather than hardcoded.
LAUNCHCTL="${GIGABITE_LAUNCHCTL:-launchctl}"

CMD_DIR="$HOME/.claude/commands"
AGENT_DIR="$HOME/.claude/agents"
SKILL_DIR="$HOME/.claude/skills"
HOOK_DIR="$HOME/.claude/gigabite"
GLOBAL_CLAUDE="$HOME/.claude/CLAUDE.md"
SETTINGS="$HOME/.claude/settings.json"
PLIST="$HOME/Library/LaunchAgents/com.gigabite.synthesis.plist"
LOG="$HOME/Library/Logs/gigabite-synthesis.log"
LABEL="com.gigabite.synthesis"

ASSUME_YES=0
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)     ASSUME_YES=1 ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,12p' "$REPO/uninstall.sh" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown option: $1" "Run ./uninstall.sh --help" ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Every step below runs twice: once as the plan, once for real. Sharing one code
# path is what makes the plan trustworthy — a plan assembled separately from the
# removal is a plan that can be wrong about it.
MODE=plan
PLANNED=0
KEPT=""
BACKUPS=""

is_protected() { # the two directories that hold the only things a clone cannot rebuild
  case "$1" in
    "$CORE_DIR"|"$CORE_DIR"/*|"$KNOW_DIR"|"$KNOW_DIR"/*) return 0 ;;
  esac
  return 1
}

record() { # list-var-name path reason — remembered for the closing summary
  case "$1" in
    KEPT)    KEPT="$KEPT
  $2 — $3" ;;
    BACKUPS) BACKUPS="$BACKUPS
  $2" ;;
  esac
}

keep() { # path reason
  warn "kept $1 — $2"
  [ "$MODE" = apply ] && record KEPT "$1" "$2" || true
}

# Every deletion goes through here. The guard is not decoration: ~/Knowledge and
# ~/.core are the user's own content, so refusing them at the single point of
# deletion is cheaper than trusting each of a dozen callers to have built its
# path correctly.
remove_path() { # path description
  if is_protected "$1"; then
    warn "refused to remove $1 — that is inside your knowledge base or ~/.core"
    return 0
  fi
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then
    note "already gone: $2"
    return 0
  fi
  if [ "$MODE" = plan ]; then
    PLANNED=$((PLANNED + 1)); note "remove $1"
  else
    rm -f "$1"; ok "removed $2"
  fi
}

# "Ours" is install.sh's own test, deliberately unchanged: the marker, or any
# mention of gigabite at all, because installs predating the marker have none.
# Here the trade runs the other way round — a false positive deletes a file the
# user wrote — so a file failing the test is kept and reported, never removed.
is_ours() { grep -qi "gigabite" "$1" 2>/dev/null; }

remove_managed() { # path description
  if [ ! -e "$1" ]; then note "already gone: $2"; return 0; fi
  if ! is_ours "$1"; then keep "$1" "gigabite did not write that file"; return 0; fi
  remove_path "$1" "$2"
}

# A directory gigabite created may have picked up files of the user's since. It goes
# only when nothing is left in it. The third argument names the file this run is
# about to take out of it, so the plan can see past a directory that is empty in
# every way except the one being fixed.
remove_dir_if_empty() { # dir description [name-already-planned-for-removal]
  if [ ! -d "$1" ]; then note "already gone: $2"; return 0; fi
  if is_protected "$1"; then
    warn "refused to remove $1 — that is inside your knowledge base or ~/.core"
    return 0
  fi
  local leftover
  if [ -n "${3:-}" ]; then
    leftover="$(ls -A "$1" 2>/dev/null | grep -vxF "$3" || true)"
  else
    leftover="$(ls -A "$1" 2>/dev/null || true)"
  fi
  if [ -n "$leftover" ]; then
    keep "$1" "it still holds files gigabite did not write"
    return 0
  fi
  if [ "$MODE" = plan ]; then
    PLANNED=$((PLANNED + 1)); note "remove $1/"
  else
    rmdir "$1" && ok "removed $2"
  fi
}

# ---------------------------------------------------------------------------
step_path() {
  say "1/6  Taking gigabite off your PATH"
  local found=0 d link target root old_ifs="$IFS"
  IFS=:
  set -- $BIN_DIRS                       # split on ':' without losing spaces in a path
  IFS="$old_ifs"
  for d in "$@"; do
    [ -n "$d" ] || continue
    link="$d/gigabite"
    if [ -L "$link" ]; then
      target="$(readlink "$link")"
      case "$target" in /*) : ;; *) target="$d/$target" ;; esac
      # Removed only when it resolves into a gigabite checkout, on the same
      # evidence bootstrap.sh uses: an unrelated binary called `gigabite` is
      # someone else's, and this script has no business deleting it.
      root="$(dirname "$(dirname "$target")")"
      if [ -f "$root/install.sh" ] && [ -f "$root/gigabite/__init__.py" ]; then
        remove_path "$link" "the gigabite launcher at $link"
        found=1
      else
        keep "$link" "it does not point into a gigabite checkout"
        found=1
      fi
    elif [ -e "$link" ]; then
      keep "$link" "it is not a symlink, so gigabite did not put it there"
      found=1
    fi
  done
  [ "$found" = 1 ] || note "already gone: the gigabite launcher (no link in $BIN_DIRS)"

  # The PATH export install.sh appended, identified by the tag it appended with.
  # Only that line goes; every other byte of the file is left as it is.
  local rc
  for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if [ -f "$rc" ] && grep -qF "added by gigabite" "$rc"; then
      if [ "$MODE" = plan ]; then
        PLANNED=$((PLANNED + 1)); note "remove the '# added by gigabite' PATH line from $rc"
      else
        /usr/bin/python3 - "$rc" <<'PY'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(l for l in lines if "added by gigabite" not in l)
PY
        ok "removed the PATH line from $rc"
      fi
    else
      note "already gone: the PATH line in $rc"
    fi
  done
}

# ---------------------------------------------------------------------------
step_claude_files() {
  say "2/6  Removing the Claude Code commands, subagents and skills"
  local f a s skill
  # The five current commands, plus the two that were renamed away and may still
  # be sitting there from an older install.
  for f in gg search search-status calendar meeting core-setup recall-status granola; do
    remove_managed "$CMD_DIR/$f.md" "/$f"
  done
  for a in gg-builder gg-researcher gg-reviewer; do
    remove_managed "$AGENT_DIR/$a.md" "subagent $a"
  done
  for s in "$REPO/install/scaffold/skills/"*/SKILL.md; do
    [ -e "$s" ] || continue
    skill="$(basename "$(dirname "$s")")"
    remove_managed "$SKILL_DIR/$skill/SKILL.md" "skill $skill"
    remove_dir_if_empty "$SKILL_DIR/$skill" "skill directory $skill" "SKILL.md"
  done
}

# ---------------------------------------------------------------------------
step_conversational() {
  say "3/6  Unwiring the conversational layer (router protocol + ambient recall)"
  local status
  status=$(GIGABITE_MODE="$MODE" /usr/bin/python3 - "$GLOBAL_CLAUDE" <<'PY'
import os, sys, shutil

path = sys.argv[1]
mode = os.environ["GIGABITE_MODE"]
start, end = "<!-- gigabite:router:start -->", "<!-- gigabite:router:end -->"
if not os.path.exists(path):
    print("absent"); sys.exit(0)
current = open(path, encoding="utf-8").read()
i, j = current.find(start), current.find(end)
if i == -1:
    # An end marker with no start is damage, not absence: bounding the block is
    # guesswork either way, so the file is left exactly as found.
    print("corrupt" if j != -1 else "absent"); sys.exit(0)
if j == -1 or j < i:
    print("corrupt"); sys.exit(0)

before = current[:i].rstrip("\n")
after = current[j + len(end):].lstrip("\n")
remainder = [part for part in (before, after) if part.strip()]
if mode == "plan":
    print("block"); sys.exit(0)

shutil.copyfile(path, path + ".gigabite-bak")
if not remainder:
    os.remove(path); print("removed-file"); sys.exit(0)
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n\n".join(remainder).rstrip("\n") + "\n")
print("removed")
PY
  ) || status="error"
  case "$status" in
    block)        PLANNED=$((PLANNED + 1)); note "remove the router block from $GLOBAL_CLAUDE" ;;
    removed)      ok "removed the router block from ~/.claude/CLAUDE.md (backup: CLAUDE.md.gigabite-bak)"
                  record BACKUPS "$GLOBAL_CLAUDE.gigabite-bak" ;;
    removed-file) ok "removed ~/.claude/CLAUDE.md — the router block was all it held (backup: CLAUDE.md.gigabite-bak)"
                  record BACKUPS "$GLOBAL_CLAUDE.gigabite-bak" ;;
    absent)       note "already gone: the router block in ~/.claude/CLAUDE.md" ;;
    corrupt)      warn "router markers in ~/.claude/CLAUDE.md look damaged — left the file untouched"
                  [ "$MODE" = apply ] && record KEPT "$GLOBAL_CLAUDE" "its router markers are damaged; remove the block by hand" || true ;;
    *)            warn "could not read the router block in ~/.claude/CLAUDE.md ($status) — left it untouched" ;;
  esac

  status=$(GIGABITE_MODE="$MODE" /usr/bin/python3 - "$SETTINGS" <<'PY'
import json, os, shutil, sys

path = sys.argv[1]
mode = os.environ["GIGABITE_MODE"]
NEEDLE = "gg-recall.sh"
if not os.path.exists(path):
    print("absent"); sys.exit(0)
try:
    with open(path) as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise ValueError
except Exception:
    # A file we cannot parse is a file we cannot edit safely. Keep a copy and stop.
    if mode != "plan":
        shutil.copy2(path, path + ".gigabite.bak")
    print("unparseable"); sys.exit(0)

hooks = cfg.get("hooks")
ups = hooks.get("UserPromptSubmit") if isinstance(hooks, dict) else None
if not isinstance(ups, list):
    print("absent"); sys.exit(0)

# Filtered at the inner hook level, not the entry level: an entry may carry the
# user's own hook alongside ours under one matcher, and dropping the entry whole
# would take theirs with it.
kept, changed = [], False
for entry in ups:
    if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
        inner = [h for h in entry["hooks"] if NEEDLE not in json.dumps(h)]
        if len(inner) != len(entry["hooks"]):
            changed = True
            if not inner:
                continue
            entry = dict(entry, hooks=inner)
        kept.append(entry)
    elif NEEDLE in json.dumps(entry):
        changed = True
    else:
        kept.append(entry)

if not changed:
    print("absent"); sys.exit(0)
if mode == "plan":
    print("hook"); sys.exit(0)

if kept:
    hooks["UserPromptSubmit"] = kept
else:
    del hooks["UserPromptSubmit"]
    if not hooks:
        del cfg["hooks"]
shutil.copy2(path, path + ".gigabite.bak")
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
print("removed")
PY
  ) || status="error"
  case "$status" in
    hook)        PLANNED=$((PLANNED + 1)); note "remove the ambient recall hook from $SETTINGS" ;;
    removed)     ok "removed the ambient recall hook from ~/.claude/settings.json (backup: settings.json.gigabite.bak)"
                 record BACKUPS "$SETTINGS.gigabite.bak" ;;
    absent)      note "already gone: the ambient recall hook in ~/.claude/settings.json" ;;
    unparseable) warn "~/.claude/settings.json isn't valid JSON — backed it up to .gigabite.bak and did NOT modify it"
                 [ "$MODE" = apply ] && record KEPT "$SETTINGS" "it is not valid JSON; remove the gg-recall.sh hook by hand" || true ;;
    *)           warn "could not read ~/.claude/settings.json ($status) — left it untouched" ;;
  esac

  remove_path "$HOOK_DIR/gg-recall.sh" "the ambient recall hook script"
  remove_dir_if_empty "$HOOK_DIR" "$HOOK_DIR" "gg-recall.sh"
}

# ---------------------------------------------------------------------------
step_launchd() {
  say "4/6  Unscheduling the daily synthesis job"
  if [ "$MODE" = apply ]; then
    if command -v "$LAUNCHCTL" >/dev/null 2>&1; then
      # Not being loaded is the normal case after a reboot, and no reason to stop.
      "$LAUNCHCTL" bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && ok "unloaded $LABEL" \
        || note "$LABEL was not loaded"
    else
      warn "launchctl not found — skipped unloading $LABEL"
    fi
  elif [ -e "$PLIST" ]; then
    note "unload $LABEL"
  fi
  remove_path "$PLIST" "the LaunchAgent plist"
}

# ---------------------------------------------------------------------------
step_log() {
  say "5/6  Removing the synthesis log"
  remove_path "$LOG" "$LOG"
}

run_steps() {
  step_path
  step_claude_files
  step_conversational
  step_launchd
  step_log
}

# ---------------------------------------------------------------------------
kept_content_note() {
  note "your knowledge base is untouched:  $KNOW_DIR"
  note "your operating protocol is untouched:  $CORE_DIR/core.md"
}

say "gigabite uninstall — this is the plan"
echo
run_steps
echo
say "Left alone, deliberately"
kept_content_note
note "gigabite writes nothing else into either, and this script removes nothing from them"
echo

if [ "$PLANNED" -eq 0 ]; then
  say "Nothing left to remove — gigabite is already uninstalled"
  note "the clone itself is still here: rm -rf $REPO"
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  say "--dry-run: $PLANNED item(s) would be removed. Nothing was touched."
  exit 0
fi

if [ "$ASSUME_YES" != 1 ]; then
  [ -t 0 ] || die \
    "Not running in a terminal, so there is nobody to ask." \
    "Re-run with --yes to remove the $PLANNED item(s) above, or --dry-run to just see them:" \
    "    ./uninstall.sh --yes"
  printf 'Remove the %s item(s) above? [y/N] ' "$PLANNED"
  read -r reply
  case "$reply" in
    y|Y|yes|Yes|YES) ;;
    *) say "Nothing removed."; exit 0 ;;
  esac
  echo
fi

MODE=apply
say "gigabite uninstall — removing"
echo
run_steps

echo
say "6/6  Done"
kept_content_note
if [ -n "$KEPT" ]; then
  echo
  note "left in place because gigabite did not write them, or could not prove it did:"
  printf '%s\n' "$KEPT" | sed '/^$/d'
fi
if [ -n "$BACKUPS" ]; then
  echo
  note "backups taken before editing your files — delete them when you are happy:"
  printf '%s\n' "$BACKUPS" | sed '/^$/d'
fi
echo
note "the clone is still here, because this script is running from inside it:"
say "    rm -rf $REPO"
