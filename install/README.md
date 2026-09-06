# install/

This directory holds everything `install.sh` copies **onto the machine**. Nothing in
here is imported by the `gigabite` package at runtime, and nothing in here runs as
part of the tool itself. It is the integration surface — the files that teach Claude
Code, launchd, and your home directory how to work with gigabite — kept in one place
so that the repository root stays about the tool rather than about its plumbing.

The separation matters when you are reading the code. If you are trying to
understand what gigabite *does*, none of this is relevant. If you are trying to
understand what changed on your machine after you ran the installer, all of it is.

## What each directory installs

The installer walks these in order, copying templates only where the destination
does not already exist, so re-running it never overwrites something you have edited.

| Directory | Installs to | What it is |
|---|---|---|
| `claude-commands/` | `~/.claude/commands/` | The slash commands — `/gg`, `/search`, `/search-status`, `/calendar`, `/meeting`. The placeholder `__GIGABITE_BIN__` is substituted for the real launcher path at install time, because Claude Code needs an absolute path in its `allowed-tools` declaration. |
| `hooks/` | `~/.claude/gigabite/` | `gg-recall.sh`, the `UserPromptSubmit` hook that injects ambient recall into every turn. The installer also registers it in `~/.claude/settings.json`; remove it there to switch ambient recall off. |
| `scaffold/` | `~/.core/`, `~/Knowledge/`, `~/.claude/agents/` | Starter templates: `core.md`, the capability README, the SOPs, the `gg-*` subagents, the `_project.md` template, and the README that explains the knowledge base to a human who opens it. Copied only if absent — with one exception, below. |
| `launchd/` | `~/Library/LaunchAgents/` | `com.gigabite.synthesis.plist`, the scheduled end-of-day job that writes a gated synthesis proposal and runs the decay pass. Nothing it produces is applied automatically. |
| `scripts/` | *nothing — run by hand* | `claude-ai-safari-export.js`, which you paste into the claude.ai browser console to export your chats. It is here because it is part of the setup story, not because the installer touches it. |

The `scaffold/` entry is the one that reaches furthest. It seeds `~/.core/core.md` and
`~/.core/capability/`, the README at `~/Knowledge/README.md`, and the subagent
definitions Claude Code loads from `~/.claude/agents/`. Everything it writes is a
template with no real content in it.

That knowledge README is the one file the installer *refreshes* rather than leaves
alone, and the reason is worth stating: it is instructions, not content. It tells you
where things go, so a stale copy sends you to a folder that no longer exists — a worse
outcome than losing a note you wrote. The replaced text is moved into
`~/Knowledge/.gigabite/originals/` first, so nothing is destroyed and the installer's
promise still holds.

## Two rules when editing anything here

**Scaffold holds templates, never real content.** Anything with a real client name,
a stakeholder, a price, or an internal detail belongs in `~/Knowledge`, not in this
repository. This is the same confidentiality boundary the rest of the project runs
on, and the scaffold is the easiest place to breach it by accident, because these
files are the ones that look most like notes.

**Paths here are referenced from `install.sh` as `$REPO/install/...`.** The installer
copies specific files by name. Move or rename anything in this directory and that
script has to move with it, or the install will silently seed one fewer file than it
should.

Between them these two rules cover the only ways this directory tends to break: by
leaking content that should have stayed local, or by drifting out of step with the
script that reads it. Check both before committing a change in here.
