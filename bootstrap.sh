#!/usr/bin/env bash
# gigabite bootstrap — one command from nothing to a working install.
#   curl -fsSL https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.sh | bash
#
#   • checks the machine can actually run it, and says plainly what to do if not
#   • clones to ~/gigabite (or updates an existing clone there)
#   • hands over to ./install.sh, which does the real work
# Safe to run again any time. Your notes and settings are left alone; the Claude Code
# commands and subagents that gigabite itself installs are replaced with current ones.
set -euo pipefail

REPO_SLUG="theboykiro/gigabite"
REPO_URL="https://github.com/$REPO_SLUG.git"
# Fixed, and deliberately not configurable. macOS refuses background jobs access to
# Desktop, Documents and Downloads, so a clone in any of those gives a working
# command line and a daily job that silently never runs — a failure whose only
# symptom is a line in a log nobody opens. Choosing the location removes the trap
# instead of documenting it.
DEST="$HOME/gigabite"

# The interpreter the launcher actually uses (bin/gigabite runs /usr/bin/python3
# directly), so checking `python3` from PATH would verify the wrong Python.
PY="/usr/bin/python3"
# A supported-configuration floor, not a technical one: the code itself parses and
# runs on 3.7, but 3.9 is what every macOS still receiving updates ships and the
# only version the suite is exercised against. Refusing below it fails honestly
# here rather than somewhere deep in an ingest.
PY_MIN_MAJOR=3
PY_MIN_MINOR=9

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
note() { printf '  \033[2m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; shift; for l in "$@"; do note "$l" >&2; done; exit 1; }

# ---------------------------------------------------------------------------
say "1/3  Checking this Mac can run gigabite"

[ "$(uname -s)" = "Darwin" ] && ok "macOS" || die \
  "gigabite only runs on macOS." \
  "It leans on Mac-only pieces for scheduling and notifications, so there is no Linux or Windows build."

# git and /usr/bin/python3 are both shipped by Apple's command line tools. Without
# them they exist as stubs that pop a dialog and fail, so ask once, up front,
# rather than letting each check fail separately with its own confusing error.
xcode-select -p >/dev/null 2>&1 || die \
  "Your Mac needs Apple's command line tools before anything else can be installed." \
  "Run this, click through the installer, wait for it to finish:" \
  "    xcode-select --install" \
  "Then run the gigabite command again."

git --version >/dev/null 2>&1 || die \
  "git is not working on this Mac, and gigabite is downloaded with it." \
  "Run this, click through the installer, wait for it to finish:" \
  "    xcode-select --install" \
  "Then run the gigabite command again."
ok "git"

[ -x "$PY" ] || die \
  "Python is missing from this Mac (expected it at $PY)." \
  "Run this, click through the installer, wait for it to finish:" \
  "    xcode-select --install" \
  "Then run the gigabite command again."

PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
[ -n "$PY_VERSION" ] || die \
  "Python is on this Mac but will not start, so gigabite cannot run." \
  "Reinstalling Apple's command line tools usually fixes it:" \
  "    xcode-select --install"
"$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)" || die \
  "gigabite needs Python $PY_MIN_MAJOR.$PY_MIN_MINOR or later, and this Mac has $PY_VERSION." \
  "That version comes with macOS, so the fix is a macOS update — check" \
  "System Settings > General > Software Update."
ok "Python $PY_VERSION"

# Not fatal: gigabite is a usable search tool on its own. But the router, the
# ambient recall and the slash commands are the reason it exists, and all of them
# live inside Claude Code — so say what is missing rather than installing quietly.
if command -v claude >/dev/null 2>&1 || [ -d "$HOME/.claude" ]; then
  ok "Claude Code"
else
  warn "Claude Code is not installed on this Mac."
  note "gigabite will still work in the terminal: gigabite search \"...\" over all your history."
  note "What you will not get is the part that matters most — Claude answering with your"
  note "own past work already loaded, and the /gg, /search and /meeting commands."
  note "Install Claude Code, run this again, and the rest switches on. Nothing is lost."
fi

# ---------------------------------------------------------------------------
say "2/3  Getting gigabite"

# Identified by what the folder contains, not where it was cloned from. Matching on
# the remote looked equivalent and is not: a contributor working from their own fork
# has a remote bearing their username, and would have been told their checkout "is
# not gigabite" and sent to move it out of the way for no reason.
is_gigabite_checkout() {
  git -C "$DEST" rev-parse --git-dir >/dev/null 2>&1 &&
    [ -f "$DEST/install.sh" ] && [ -f "$DEST/gigabite/__init__.py" ]
}

# -L as well as -e: a symlink pointing at something deleted is invisible to -e, so
# this would fall through to the clone and fail with git's own wording about a work
# tree dir, which reads as a network problem and sends the user somewhere useless.
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  if [ -L "$DEST" ]; then
    RESOLVED="$(cd -P "$DEST" 2>/dev/null && pwd || true)"
    [ -n "$RESOLVED" ] || die \
      "$DEST is a shortcut pointing at a folder that no longer exists." \
      "Remove it and run this again:" \
      "    rm ~/gigabite"
    # The fixed location is the whole point, and a shortcut can quietly undo it:
    # macOS stops background jobs reaching these folders, so an install behind one
    # gets a working command line and a daily job that never runs.
    case "$RESOLVED" in
      "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*)
        die \
          "$DEST is a shortcut into Desktop, Documents or Downloads." \
          "gigabite cannot run its daily background job from there. Remove the" \
          "shortcut and run this again to install a normal copy:" \
          "    rm ~/gigabite" ;;
    esac
  fi
  if is_gigabite_checkout; then
    note "already installed at $DEST — updating it"
    if git -C "$DEST" pull --ff-only >/dev/null 2>&1; then
      ok "updated to the latest version"
    else
      # --ff-only cannot rewrite or discard anything, so a failure here means the
      # copy is edited, on another branch, or the network is down. All three are
      # fine to carry on from; none are worth stopping a re-run for.
      warn "could not update this copy — carrying on with the version already there"
      note "usually means local edits, a different branch, or no internet"
    fi
  else
    die \
      "There is already something at $DEST, and it is not gigabite." \
      "Nothing has been touched. Move or rename that folder, then run this again:" \
      "    mv ~/gigabite ~/gigabite-old"
  fi
else
  # git's own failure text is developer-facing, and GIT_TERMINAL_PROMPT stops it
  # asking for a password it can never receive when this arrives down a pipe.
  GIT_TERMINAL_PROMPT=0 git clone --quiet "$REPO_URL" "$DEST" 2>/dev/null || die \
    "Could not download gigabite from GitHub." \
    "Check your internet connection and try again. If you are on a work network," \
    "access to github.com may be blocked."
  ok "downloaded to $DEST"
fi
note "installed to ~/gigabite so the daily background job can run"

# ---------------------------------------------------------------------------
say "3/3  Running the installer"
echo
cd "$DEST"
# Invoked through bash rather than as ./install.sh so a clone that arrived without
# its executable bit still installs instead of stopping here.
exec bash ./install.sh
