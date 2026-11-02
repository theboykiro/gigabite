#!/usr/bin/env bash
# gigabite installer — idempotent, additive, non-destructive.
#   • creates ~/.core and ~/Knowledge layout (copies templates only if absent), and
#     on a first install opens ~/Knowledge in Finder so you know it exists
#   • puts `gigabite` on your PATH
#   • installs the /search and /core-setup commands (user-level)
#   • wires the router block and three hooks into ~/.claude: ambient recall on every
#     prompt, a background index refresh when a session starts, and a guard that
#     makes every agent spawn name its model
#   • offers to enable the "AI brain" integrations (Granola, more soon)
#   • builds the initial index
# Nothing here overwrites content you already have. Re-run any time.
#
#   ./install.sh                install, one line per step
#   ./install.sh --verbose      every file it touched, one line each
#
# The work is `gigabite install` (gigabite/installer.py), the same code Windows
# runs. What stays here is the one thing Python cannot do for itself: check that
# Python is there and starts.
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BIN="$REPO/bin/gigabite"
chmod +x "$BIN"

note() { printf '  \033[2m%s\033[0m\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; shift; for l in "$@"; do note "$l" >&2; done; exit 1; }

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

exec "$BIN" install "$@"
