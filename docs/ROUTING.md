# Routing

Routing answers two questions. Which project is this prompt about, which decides what
recall may show? And where does a file written during a session land? The same rule
governs both: a folder's name is never a project.

## Which project a prompt belongs to

For every prompt, the recall hook resolves a project in this order and stops at the
first match:

1. **An `@marker` in the prompt**, such as `@acme`. It wins
   outright, for that prompt only. A marker that names no existing project is ignored.
2. **A binding for the current folder**, set with `gigabite project bind`.
3. **A keyword**: a word from the `keywords:` line in a project's
   `~/Knowledge/<project>/_project.md`. Keywords only count in a folder that has no
   binding. A bound folder stays in its project, and the recall block names the
   `@marker` to use if you meant the other one.
4. **Nothing.** No project means no recall. Recall never falls back to searching
   everything, because on a machine holding more than one client that is exactly the
   search that can't be safe.

When a project resolves, only that project is searched.

### Binding a folder

```bash
gigabite project bind acme            # this folder is acme; creates the project if new
gigabite project bind acme --dir PATH # bind another folder
gigabite project bind --none          # not project work: no recall, never asked again
gigabite project bind --forget        # drop the binding and be asked again
```

Run from a subfolder, `bind` binds the workspace it belongs to (the nearest folder with
a `.git`, a package manifest, a `CLAUDE.md` or `.claude/`). A binding covers the plain
subfolders beneath it and stops at the next workspace, so a separate checkout inside a
bound folder is asked about on its own rather than quietly included. `bind` refuses your
home folder and `/`, because a binding there would cover the whole machine. It also
refuses a folder that directly holds several separate projects (a `~/code` shelf) and
anything inside a dot-folder. Binding also
re-tags the Claude Code sessions already indexed from that folder, so their history
comes along. Bindings live in `~/Knowledge/.gigabite/bindings.json`.

### The "which project?" ask

In a folder that looks like a workspace and has no binding, the hook attaches a short
question for Claude to relay. It lists your projects and offers the two `bind` commands.
Once answered, the folder is never asked about again. Unanswered, the question comes back
once per Claude Code session. It is never asked in your home folder, in temporary or
scratch space, inside `~/Knowledge`, or in a folder with no workspace marker; bind those
by hand if you want recall there.

To add keywords to a project, edit the `keywords:` line in its `_project.md`, or create
the project with them: `gigabite project add acme --keywords "acme, acme corp"`.
`gigabite project list` shows your projects and their keywords.

## Where files land

This part is about where a file written during a session lands. It is the single
mechanism that stops your projects bleeding into one another, and the reason the
knowledge base can still be trusted after months of use.

### The rule

Code goes to the working directory — the folder Claude Code is currently pointed at.
It is version-controlled and it is pushed to GitHub.

Knowledge goes somewhere else entirely. Notes, context, meeting transcripts and
project metadata are written to the fixed central store at
`~/Knowledge/{project}/[{layer}/]`, **regardless** of the working directory. And the
knowledge base is always **read** from `~/Knowledge`, never from the working
directory.

### The failure this prevents

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

### How `features.save` enforces it

`gigabite/features/save.py` is the *only* correct way to persist knowledge. Every path
it produces is resolved under `config.KNOWLEDGE_DIR` and built from the detected
`{project, layer}`; the caller's current working directory is never consulted at any
point. Run it from inside any repository you like and the note still lands in the
central store.

Project and layer names are each sanitised into a single safe folder name by
`slugify` and `_safe_folder`, which strip `/`, `\`, and `..` outright. A name that
survives sanitisation cannot contain a path separator, so no project or layer name —
however it was derived, including from the filename of something you handed over —
can traverse out of the knowledge base.

Four functions make up the surface. `ensure_project(project, keywords, layers)`
creates `{project}/` and seeds a `_project.md` from the template, and is idempotent:
existing metadata is never clobbered. `save_note(text, project, layer, title, ts,
meta)` writes the note and returns its path, with `meta` adding extra frontmatter such
as `origin:` provenance and the `share:` egress marker. `save_file(src, …)` copies an
existing file — a screenshot, a PDF, a `.vtt` — in byte-for-byte under the same
`{project}/{layer}/` rule, because refusing a file you cannot extract text from means
losing it, and a screenshot is content. `list_projects()` enumerates
`{project}/_project.md` for context detection.

Everything else that writes knowledge goes through those. `features.intake` calls
them for anything handed over, `gigabite save` calls `save_note`, `features.materialize`
calls it for each rendered document. The invariant is that knowledge is only ever written by `features.save`;
there is no second path, and adding one would be the way this guarantee gets broken.

### The layout that results

```
~/Knowledge/
  {project}/
    _project.md                 project meta: name, keywords (for detection), layers
    {layer}/                    a nested context layer, e.g. meetings, delivery
      {YYYY-MM-DD-slug}.md      a saved note
      screenshot.png            a stored file, kept as it is
    {YYYY-MM-DD-slug}.md        a note with no layer sits at the project root
  {loose-file}                  project unresolved: visible, indexed, one drag from filed
  .gigabite/                    index, imports, bindings, aliases — not content
```

Every top-level folder is a project. That is the whole convention, and it replaced a
set of reserved `_` names that had to be explained to anyone who opened the folder.

Notes are named for their date and a slug of their title, so the folder sorts
chronologically in any file browser without needing the tool. Collisions get a `-2`,
`-3` suffix rather than overwriting.

### Indexing

`gigabite/sources/notes.py` closes the loop, and the store is also the intake surface:
a file placed anywhere under a project folder is indexed *where it sits* on the next
ingest, and moving it to another project later moves its project with it, because the
folder is the metadata. There is no staging area and no filing step to remember.

It indexes everything it finds under `{project}/[{layer}/]`, with narrow exceptions:
anything beginning with `.` (which is where all the machinery lives), the top-level
folder names an older layout used for the same machinery, files beginning with `_`,
`README.md`, and `_project.md`. Files that are not text are kept and indexed by
filename, type, size and date, with an explicit note that their contents were not
read — there is no OCR and nothing is inferred about an image. Loose files at the
knowledge root are indexed with an empty project, which is the honest record for
content whose home is not yet known.

Ingest is incremental, tracked by an `mtime:size` signature per file, so a re-run only
reads what changed. That also means a note you write by hand in a project folder is
indexed on the next ingest exactly like one the tool wrote, with no registration step.

### In short

One rule, one enforcement point, one layout. Knowledge is written only by
`features.save`, `features.save` resolves only under `~/Knowledge`, and the folder
structure it produces is the same structure the indexer reads back. As long as every
new feature that persists knowledge goes through that module, contexts cannot mix —
and any feature that bypasses it has silently opted out of the guarantee, whether or
not it looks like it works.
