# SOPs & subagents

SOPs (standard operating procedures) are modular, versioned markdown files that
define **how a process runs** — a build → QA → review chain, a research pass, a PM
decision. They are separate from the core protocol on purpose: the core governs
*how the operator works* in general; an SOP governs *how a specific job gets done*,
and agents mix and match them per task (ARCHITECTURE §2.1, README "Reusable SOPs").

## What's here

Scaffold SOPs (`install/scaffold/sops/`), installed to `~/.core/capability/sops/`:

| SOP | Role(s) | What it governs |
|---|---|---|
| `sop-build-qa-review.md` | builder, qa, reviewer | Build → QA → user-review chain with a hand-off gate at each step and a definition of done before work reaches the user. |
| `sop-research.md` | researcher | Gather → synthesize → cite. Search the user's own history (`gigabite search`) before going external; egress-check before anything leaves the device. |
| `sop-pm-decision.md` | pm | Frame → options → trade-offs → recommendation. Leads with the call, not a survey. |

Scaffold subagents (`install/scaffold/agents/`), installed to `~/.claude/agents/`:

| Agent | Loads SOP | When to use |
|---|---|---|
| `gg-builder.md` | `sop-build-qa-review` (builder role) | Build a feature/deliverable the user will act on. |
| `gg-reviewer.md` | `sop-build-qa-review` (QA + reviewer roles) | QA and review built work before it reaches the user. |
| `gg-researcher.md` | `sop-research` | Research/analysis needing facts from more than one place. |

## How subagents load SOPs

Each agent body instructs the agent to, before working:

1. Load its SOP from `~/.core/capability/sops/<sop-file>` and follow it.
2. Run `gigabite search "<query>"` to pull relevant prior context from the user's own
   history before doing new work or going external.
3. Load and obey the tone in `~/.core/core.md`.

The SOP file is the operating instructions; the agent frontmatter's `description`
tells Claude Code *when* to spin the agent up.

## Reading SOPs from code

`gigabite/features/sops.py`:

- `sops_dir() -> Path` — `~/.core/capability/sops/`.
- `list_sops() -> list[dict]` — each `{name, title, role, summary, path}`, parsed from
  frontmatter (title falls back to the body's H1).
- `load_sop(name) -> str | None` — full markdown by name; matches with or without the
  `.md` suffix and `sop-` prefix.

## Adding a new SOP + agent

1. **Write the SOP** in `install/scaffold/sops/sop-<name>.md` with frontmatter:
   ```
   ---
   name: <name>
   role: <role(s)>
   summary: <one line>
   ---
   ```
   Keep it tight — purpose, when to use, the sequence, hand-off criteria, definition
   of done. It has to read as usable operating instructions, not documentation.
2. **Write the agent** in `install/scaffold/agents/gg-<name>.md` with frontmatter
   `name`, `description` (when to use it), optional `tools`. The body must load the SOP
   from `~/.core/capability/sops/sop-<name>.md`, use `gigabite search`, and obey
   `~/.core/core.md`.
3. **Reinstall** so the files land in `~/.core/capability/sops/` and `~/.claude/agents/`.
   `list_sops()` picks up the new SOP automatically — no code change.
