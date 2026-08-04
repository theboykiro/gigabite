# Knowledge routing

Knowledge routing is the rule that decides where a file written during a session
actually lands. It sounds like a detail and it is not: it is the single mechanism that
stops your projects bleeding into one another, and it is the reason the knowledge base
can be trusted after six months of use rather than becoming an archaeological site.

This document states the rule, explains the failure it prevents, describes how the
code enforces it, and shows the layout that results. It is the detailed version of
`ARCHITECTURE.md` §4.

## The rule

Code goes to the working directory — the folder Claude Code is currently pointed at.
It is version-controlled and it is pushed to GitHub.

Knowledge goes somewhere else entirely. Notes, context, synthesised insight, and
project metadata are written to the fixed central store at
`~/Knowledge/{project}/[{layer}/]`, **regardless** of the working directory. And the
knowledge base is always **read** from `~/Knowledge`, never from the working
directory.

## The failure this prevents

If a knowledge file defaults to the working directory, it is filed into whatever
repository happens to be selected at that moment. That is almost never the right
project, and the damage compounds: a note about client A sitting inside client B's
repository is both lost to search for A and a confidentiality problem for B, and the
next person to read that repository — including you, later — has no way to know it
does not belong there.

The failure is quiet, which is what makes it serious. Nothing errors, nothing warns,
and the misfiling is only discovered when a search comes back empty for something you
distinctly remember writing. Preventing it structurally, rather than by remembering to
be careful, is the whole point of the rule.

## How `save_note` enforces it

`gigabite/features/save.py` is the *only* correct way to persist knowledge. Every path
it produces is resolved under `config.KNOWLEDGE_DIR` and built from the detected
`{project, layer}`; the caller's current working directory is never consulted at any
point. Run it from inside any repository you like and the note still lands in the
central store.

Project and layer names are each sanitised into a single safe folder name by
`slugify` and `_safe_folder`, which strip `/`, `\`, and `..` outright. A name that
survives sanitisation cannot contain a path separator, so no project or layer name —
however it was derived, including from a filename in the drop folder — can traverse
out of the knowledge base.

Three functions make up the surface. `ensure_project(project, keywords, layers)`
creates `{project}/` and seeds a `_project.md` from the template, and is idempotent:
existing metadata is never clobbered. `save_note(text, project, layer, title, ts,
meta)` writes the note and returns its path, with `meta` adding extra frontmatter such
as `origin:` provenance and the `share:` egress marker. `list_projects()` enumerates
`{project}/_project.md` for context detection.

Everything else that writes knowledge goes through those. The drop-folder filing pass
calls `save_note`, the `gigabite save` command calls `save_note`, and the calendar
module calls it for meeting records. There is no second path, and adding one would be
the way this guarantee gets broken.

## The layout that results

```
~/Knowledge/
  {project}/
    _project.md                 project meta: name, keywords (for detection), layers
    {layer}/                    a nested context layer, e.g. meetings, delivery
      {YYYY-MM-DD-slug}.md      a saved note
    {YYYY-MM-DD-slug}.md        a note with no layer sits at the project root
  Inbox/                        the drop folder — staging, never storage
  _sources/ _archive/ _proposals/ .index/
                                reserved (leading '_' or '.'), skipped by the notes ingester
```

Notes are named for their date and a slug of their title, so the folder sorts
chronologically in any file browser without needing the tool. Collisions get a `-2`,
`-3` suffix rather than overwriting.

## Indexing

`gigabite/sources/notes.py` closes the loop. It scans `~/Knowledge` and indexes every
`*.md` found under `{project}/[{layer}/]`, skipping reserved top-level folders — those
with a leading `_` or `.` — along with any path segment that starts the same way, and
the two meta files `README.md` and `_project.md`. The document's project is the top
folder and its layer is the directory path between the project folder and the file, so
the layout above is also the metadata: nothing has to be declared twice.

Ingest is incremental, tracked by an `mtime:size` signature per file, so a re-run only
reads what changed. That also means a note you write by hand in a project folder is
indexed on the next ingest exactly like one the tool wrote, with no registration step.

## In short

One rule, one enforcement point, one layout. Knowledge is written only through
`save_note`, `save_note` resolves only under `~/Knowledge`, and the folder structure it
produces is the same structure the indexer reads back. As long as every new feature
that persists knowledge goes through that function, contexts cannot mix — and any
feature that bypasses it has silently opted out of the guarantee, whether or not it
looks like it works.
