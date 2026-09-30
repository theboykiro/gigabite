<!-- gigabite:router:start -->
# gigabite — the user's local memory (auto-loaded)

The `gigabite` tool indexes the user's Claude Code sessions, claude.ai chats, meetings
and notes on this machine, grouped by project. Answer with that history, not from a
blank slate.

- **Recall.** A `[gigabite recall]` block may already be attached to the turn, scoped
  to the project this folder is bound to. When the user asks about past work and it
  isn't enough, search: `__GIGABITE_BIN__ search "<terms>" --project <p>`, using the project
  the block names (`detected context: <p>`). Search without `--project` only when the
  user asks across projects. Cite what you use as *(source · title · date)*. Never
  invent history — if nothing comes back, say so.
- **The "which project?" ask.** When a `[gigabite — this folder is not linked to a
  project]` block appears, ask the user and run the command it gives with *their*
  answer. Never pick the project yourself, and never from the folder name.
- **Delegate by the operation's shape, not the task's.** Context is re-read every
  turn, so what piles up in this thread is paid for again on every message. Spawn a
  subagent whenever you need only the *conclusion* of an operation: scanning or
  grepping many files, reading logs, transcripts or long output, sizing up an
  unfamiliar area, any sweep whose raw output you would skim once and never quote.
  Use `Explore` for a plain search and `general-purpose` for multi-step work. Read a
  file directly only when you need its exact contents to edit or quote it; answer
  quick calls inline.
- **Every agent spawn names its `model`.** `sonnet` for search, fan-out, mechanical
  edits and summaries; `opus` for architecture, security review, ambiguous judgement
  calls and the final QA gate. A spawn with no model is refused by a hook: re-issue
  it with one instead of doing the work yourself.
- **Operating protocol.** The user's voice and working rules are in `~/.core/core.md`,
  imported below. Follow it. It is changed only through `/core-setup` or by the user.
- **Saving knowledge.** Use the tool, never the working directory:
  `__GIGABITE_BIN__ save "<text>" --project <p> [--layer <l>]` for a note,
  `__GIGABITE_BIN__ paste --stdin` for pasted text (plain `__GIGABITE_BIN__ paste` reads the
  clipboard), `__GIGABITE_BIN__ add <path>` for a file. If no project resolves, the tool
  leaves the file at the top of `~/Knowledge` for the user to file — that is the
  correct outcome, not something to fix by guessing.

@~/.core/core.md
<!-- gigabite:router:end -->
