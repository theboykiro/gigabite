# Features

This document is an overview of what gigabite does, written for someone deciding
whether a capability already exists before building it. It describes the system in
four groups — the context stack, the ways knowledge gets in, the loops that maintain
it, and the security posture — and closes with what has been deliberately left out.
Each capability is stated here in terms of what it gives you; `ARCHITECTURE.md` has
the mechanism behind any of them.

## Who it is for

A single operator running several parallel projects under a high context load:
someone who works the same way across every project but needs the project knowledge
itself cleanly separated. The system is project-agnostic by design. Project names,
domains, and content are data that it loads, never anything hard-coded, which is why
the repository can stay free of client material while the tool is entirely specific
to your work.

## The context stack

Everything runs through **one interface**, Claude Code. There is no second
application and no browser to switch to, which matters less for convenience than for
consistency: one entry point means one place where the protocol is loaded and one
place where recall happens.

On each task the system assembles a **three-layer context stack**. The core protocol
is always loaded and is project-agnostic — it is how you work. Project context is
detected per task and cleanly isolated — it is what you are working on. Conversation
context is the live turn — it is the question in front of you. The core stays fixed
while the other two shift with the task.

The **core protocol** itself is a single `core.md`, loaded whole on every session,
carrying your operating style, decision principles, and tone. It is identical across
all projects on purpose.

**Context detection** resolves which project and which nested layer a task belongs to,
from an explicit `@project` marker, from continuity with the current thread, or from
keyword matching against each project's metadata. It asks only when the ambiguity
genuinely matters; otherwise it loads and proceeds.

**Nested context layers** let a single project hold distinct sub-contexts — delivery
against strategy, or a separate initiative running inside the same account — that load
independently rather than blending into one undifferentiated pile.

## Getting knowledge in and keeping it separate

The **knowledge base is separate from the working directory**, and this is the feature
that prevents the most damaging class of error. Code goes to whichever folder Claude
Code is pointed at; knowledge routes to the fixed central store at `~/Knowledge`,
based on detected context, so nothing is ever misfiled into the wrong project because
of which repository happened to be open.

**An obvious drop folder** at `~/Knowledge/Inbox` handles capture with no AI involved
at all. Drag in a `.md`, `.txt`, `.vtt`, or `.json` file, run `gigabite file`, and it
is read, routed to a project, saved as a note, and the original moved aside as a
safety net. It works when you are offline or out of credits, which is the point.

**Meeting notes** arrive either through that drop folder or through `/granola`, which
files the transcript on your clipboard without it ever passing through the chat.
Either way the meeting is filed into the relevant project's `meetings/` layer,
alongside anything you wrote by hand.

**Conversation history** is captured from both Claude surfaces. Claude Code sessions
are read automatically from `~/.claude/projects/` on every ingest; claude.ai chats,
inside projects and out, arrive as a browser export and are de-duplicated by
conversation id so re-exporting is safe.

**Calendar awareness** comes from a pasted screenshot, because the calendar sits in a
managed environment with no AI access. The meetings are parsed out, mapped to
projects, filed as searchable documents, and matched with recalled prep for the
meeting ahead.

**Search across all of it** is a single full-text index with title-weighted ranking,
an AND pass that falls back to OR, and a deliberate rank penalty on your own session
transcripts, which otherwise match your questions perfectly while answering nothing.

## Keeping it maintained

**Daily synthesis** runs at day's end, reviews the documents that changed, and writes
a proposal into `~/Knowledge/_proposals/`: a per-project digest with checklists for
proposed knowledge updates and proposed changes to the core protocol. Nothing is
applied without your approval, and the module that writes it cannot write anywhere
else.

**Reference-frequency decay** archives documents you have not touched inside a thirty-
day window. Archived is not deleted: the rows stay in the index, remain searchable on
request, and are restored automatically the moment a search matches them. Active
context therefore stays a function of what you actually use.

**Reusable SOPs** are modular, versioned operating procedures loaded by role. They
drive agent chains such as build → QA → user-review, and they are what the `gg-builder`,
`gg-reviewer`, and `gg-researcher` subagents load before they start work.

## Security posture

Content is local and iCloud-backed, and only code reaches GitHub. Credentials live in
the macOS keychain and are retrieved at runtime. Every stored note carries an explicit
egress marker, so sharing is always a deliberate per-item act. Any step that would
send text off the device is expected to check its payload for confidential content
first, at the point of egress.

## What is deliberately absent

**External tool access** — an open call to act on outside systems — is shelved. It was
flagged as legally and contractually fraught in the operating environment, so it is
not built and not stubbed.

More broadly, the absences are as deliberate as the features. There is no server, no
embedding model, no cloud component, and no third-party dependency; the entire tool is
Python standard library over a local SQLite file. Every one of those is a thing that
cannot break, cannot leak, and cannot stop working when a subscription lapses. When
weighing a new feature, that list is the standard it has to clear: if it requires a
service, a credential, or the operator's ongoing diligence, it needs a much stronger
justification than convenience.
