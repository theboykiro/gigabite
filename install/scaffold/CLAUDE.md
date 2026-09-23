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
