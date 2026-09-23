# install/

Everything `install.sh` copies onto the machine. Nothing here is imported by the
`gigabite` package at runtime. If you want to know what changed on your machine after
installing, this is the list; `../uninstall.sh` takes each row back off again.

| Directory | Installs to | What it is |
|---|---|---|
| `claude-commands/` | `~/.claude/commands/` | `/search` and `/core-setup`. `__GIGABITE_BIN__` is replaced with the launcher's absolute path at install time. |
| `hooks/` | `~/.claude/gigabite/` | The Claude Code hooks, registered in `~/.claude/settings.json`: `gg-recall.sh` (`UserPromptSubmit`, ambient recall) and `gg-refresh.sh` (`SessionStart`, refreshes the index in the background; log: `~/Knowledge/.gigabite/refresh.log`). Remove them from `settings.json` to switch them off. |
| `scaffold/` | `~/.core/`, `~/Knowledge/`, `~/.claude/CLAUDE.md` | `core.md` (copied only if absent), the knowledge-base README, and the router block kept between markers in `~/.claude/CLAUDE.md`. |
| `launchd/` | `~/Library/LaunchAgents/` | The optional daily Granola pull. It's installed by `gigabite integrations`, only if you enable it. |
| `scripts/` | nothing (you run it) | `claude-ai-safari-export.js`, pasted into the claude.ai browser console to export your chats. |

`~/Knowledge/README.md` is the one file refreshed on every run, because it's instructions,
not content. The old copy is moved to `~/Knowledge/.gigabite/originals/` first. Neither
script touches your notes or an existing `core.md`.

When editing here:

- **Templates only, never real content.** No client names, people, prices or internal
  details. These files are the easiest place to leak them, because they look like notes.
- **`install.sh` copies files by name.** Rename or retire a file and update `install.sh`
  and `uninstall.sh` too, including their lists of stale commands. Otherwise the old copy
  is stranded on users' machines.
