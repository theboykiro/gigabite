# install/

Everything `install.sh` copies **onto the machine**. Nothing in here is imported by
the `gigabite` package at runtime — it's the integration surface, kept together so
the repo root stays about the tool itself.

| Directory | Installs to | What it is |
|---|---|---|
| `claude-commands/` | `~/.claude/commands/` | The slash commands — `/gg`, `/search`, `/granola`, `/calendar`, `/recall-status`. `__GIGABITE_BIN__` is substituted for the real launcher path on install. |
| `hooks/` | `~/.claude/gigabite/` | `gg-recall.sh`, the `UserPromptSubmit` hook that injects ambient recall into every turn. |
| `scaffold/` | `~/.core/`, `~/.knowledge/`, `~/.claude/agents/` | Starter templates: `core.md`, the SOPs, the `gg-*` subagents, project/inbox READMEs. **Copied only if absent** — your real content is never overwritten. |
| `launchd/` | `~/Library/LaunchAgents/` | The scheduled end-of-day synthesis job. |
| `scripts/` | *(nothing — run by hand)* | `claude-ai-safari-export.js`, pasted into the claude.ai browser console to export your chats. |

Two rules when editing anything here:

1. **`scaffold/` holds templates, never real content.** Anything with a real client
   name, stakeholder, or internal detail belongs in `~/.knowledge/`, not the repo.
2. **Paths are referenced from `install.sh` as `$REPO/install/...`.** Move a file
   here and that script must move with it.
