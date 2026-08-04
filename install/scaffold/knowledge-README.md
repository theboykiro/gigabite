# ~/Knowledge

This folder is your knowledge base. Everything gigabite remembers on your behalf
lives here: meeting notes, decisions, project background, imported conversations,
and the search index that ties them together. It is deliberately an ordinary,
visible folder in your home directory, so you can open it in Finder, read anything
in it, edit it in any text editor, and move things around without needing the tool
to be running.

It is also entirely local. Nothing here is pushed to GitHub or to any web service.
The gigabite repository contains code and nothing else; this folder contains content
and nothing else. That split is the reason it is safe to keep client material here.

If you only remember one thing from this file, make it this: **to add something, put
it in `Inbox/`.** Everything below is detail you can come back for.

---

## How the folder is laid out

The top level has two kinds of entry. Folders named after a project hold your actual
knowledge. Folders starting with an underscore or a dot are reserved for the tool,
and are not projects.

```
~/Knowledge/
  Inbox/                  drop anything here; it gets filed for you
  <client-a>/              a project
    _project.md             its keywords and layers (drives auto-filing)
    meetings/               a layer — meeting notes and Granola transcripts
    delivery/               a layer
    strategy/               a layer
    2026-08-05-a-note.md    a note with no layer sits at the project root
  <client-b>/              another project
  <employer>/              your employer is its own top-level context
  gigabite/                a project of your own
  _sources/               raw exports, machine-read, not meant for browsing
    claude_ai/              your claude.ai export lands here
    granola/                bulk Granola exports land here
  _archive/               knowledge that decayed out of the active set
  _proposals/             the end-of-day synthesis proposals, awaiting your review
  .index/gigabite.db      the search index (SQLite)
```

A **project** is a top-level context: a client, your employer, a product of your
own. Keep clients and employer separate, because they have different rules about
what may be shared. A **layer** is a subfolder inside a project, a kind of context
rather than a topic — `meetings`, `delivery`, `strategy`. Layers are optional and
you decide the names; the tool does not impose a set. A note that does not belong to
any particular layer can sit at the project root.

The reserved folders are worth knowing but not worth visiting. `_sources/` holds raw
exports in whatever shape the exporting tool produced them, which is generally not
pleasant to read — it is indexed, not browsed. `_archive/` is where documents go
when you have not touched them for a while; nothing is deleted, and a search that
matches an archived document restores it automatically. `_proposals/` holds the
daily synthesis proposals described further down. `.index/` is the search database
and can always be rebuilt from the files around it.

## Adding things

The path that works when you are busy, offline, or out of AI credits is the drop
folder. Drag any file into `Inbox/`, then run:

```
gigabite file
```

The file is read, matched to a project from its filename and opening text, saved as
a note in the right place, and the original is moved into `Inbox/_filed/<date>/` as
your safety net. Nothing is deleted at any point. `Inbox/README.md` covers the
details — which file types work, how to force a particular project, and what to do
with anything that lands in `_needs-triage/`.

The other routes are conveniences on top of the same machinery. From Claude Code,
`/granola` files the meeting transcript on your clipboard, and `/calendar` reads a
pasted calendar screenshot and files the meetings it finds. From the terminal,
`gigabite save "…" --project <p> --layer <l>` writes a note directly, which is what
Claude uses when you ask it to remember something. Your claude.ai chats come in as a
browser export dropped into `_sources/claude_ai/`. Your Claude Code sessions need
nothing at all — they are read straight from `~/.claude/projects/` on every ingest.

Whichever route you use, run `gigabite ingest` afterwards if the tool has not already
done it for you. Ingest is incremental and quick: unchanged files are skipped, and a
newer version of a file you have already imported updates the existing entry rather
than duplicating it.

You can also just write a markdown file into a project folder by hand. The notes
ingester picks up any `*.md` under `<project>/[<layer>/]`, ignoring `README.md`,
`_project.md`, and anything in a folder whose name starts with `_` or `.`. A
frontmatter block with `title:` and `date:` is used when present; without one, the
title falls back to the first heading or the filename, and the note simply has no
date attached — so it is worth adding at least a `date:` line to anything you write
by hand.

## Setting up a project

Auto-filing works by keywords, so a project routes well only once it knows what it
sounds like. Create one, with the words that signal it, like this:

```
gigabite project add <name> --keywords "the words that signal it" --layers "meetings, delivery"
```

That creates `<name>/` and a `_project.md` holding the keywords, the layer names, and
empty sections for background, stakeholders, and vocabulary. Filling those in is worth
the ten minutes: the vocabulary section in particular is what lets a note that never
names the project outright still find its way to the right place. You
can edit `_project.md` by hand at any time, and existing files are never clobbered
when the project is touched again.

## Reviewing what you have

Because this is a normal folder, the first review tool is Finder. Open a project,
open its `meetings/` layer, and read. Nothing here is in a proprietary format.

For a view across everything, the tool gives you three commands:

```
gigabite status            what is indexed — counts by source, and the date span
gigabite search "..."      full-text search across all of it
gigabite doc <doc_id>      print one conversation or note in full
gigabite decay --status    the active/archived split, and the stalest documents
```

`gigabite status` is the honest answer to "is this thing actually working". If a
source shows zero documents, something upstream is not arriving. From Claude Code the
same information is available anywhere as `/recall-status`, and search as `/search`.

Once a day, gigabite writes a synthesis proposal into `_proposals/`: a digest of the
documents that changed, grouped by project, with two empty checklists for proposed
knowledge updates and proposed changes to your operating protocol. It is a proposal
and nothing more. Nothing in this folder and nothing in `~/.core/core.md` is ever
modified without you reading the proposal and applying the parts you accept. If you
never read them, the only cost is a small pile of unread files.

## The boundary this folder is protecting

Everything in here stays on the device. It is backed up by iCloud, never committed,
and never sent to a web surface. Notes written by the tool carry a `share: private`
marker and a record of where they came from, so that if you ever do share something
it is a deliberate, per-item decision rather than an accident of storage.

The one gap worth closing yourself is backup. iCloud syncs, but it does not give you
version history, and this folder becomes the most valuable thing on your machine
faster than you would expect. The code can be recovered from a clone in thirty
seconds; this cannot be recovered at all. Point a real versioned backup at it.
