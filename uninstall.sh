#!/usr/bin/env bash
# gigabite uninstaller — reverses install.sh, and touches nothing else.
#   • removes the `gigabite` launcher from your PATH and the line it added to your shell rc
#   • removes the Claude Code commands (and any subagents or skills an older install wrote)
#   • removes the router block from ~/.claude/CLAUDE.md and its hooks from settings.json
#   • unloads and removes every com.gigabite.* launchd job, and the gigabite logs
#   • removes the folders it created, once they are empty
# Your knowledge base (~/Knowledge) and your operating protocol (~/.core) are never
# touched, and a file that gigabite did not write is left where it is and reported.
# Keys in your keychain are left too; the commands to delete them are printed.
#
#   ./uninstall.sh              show the plan, then ask
#   ./uninstall.sh --dry-run    show the plan and stop
#   ./uninstall.sh --yes        skip the question (required when stdin is a pipe)
#
# The work is `gigabite uninstall` (gigabite/installer.py), the same code Windows
# runs; this keeps the command every install has documented.
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# Through bash, so a launcher that lost its executable bit still uninstalls.
exec bash "$REPO/bin/gigabite" uninstall "$@"
