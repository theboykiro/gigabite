# ~/Knowledge/Inbox — drop files here

This is the one folder you need. Drag a file in, run one command, and it is filed
into your knowledge base in the right place. It is deliberately the least clever part
of the system: **no AI is involved**, it is plain Python, and it works when you are
out of credits, offline, or simply in a hurry.

Everything else in gigabite is an optimisation on top of this. If a fancier route
fails you, this one will not.

## How to use it

Drag the file into this folder, then run:

```
gigabite file
```

It prints what it filed and where it went. That is the whole procedure. The same
filing pass also runs automatically on every `gigabite ingest`, so anything you leave
here will eventually be picked up without you doing anything — though a file modified
in the last minute is left for the next pass, so that an unattended run never grabs
something half-written.

If you want to see what would happen without anything actually changing:

```
gigabite file --dry-run
```

## What you can drop

Text is read; anything else is left alone rather than guessed at.

| Works | Extensions |
|---|---|
| Notes, meeting notes, documents | `.md`, `.markdown`, `.txt` |
| Transcripts and subtitles | `.vtt` — timestamps and cue numbers are stripped automatically |
| Structured exports | `.json` — flattened into readable `key: value` lines so it indexes as prose |

A PDF, a Word document, an image, or an audio file is **not** opened and **not**
guessed at. It goes to `_needs-triage/` untouched. Nothing is ever deleted, whatever
happens to it.

## Where things end up

A filed file is saved as a markdown note under `~/Knowledge/<project>/<layer>/`. The
project is worked out from the filename and the opening stretch of the text, matched
against the keywords held in each project's `_project.md`.

When it matters, force the project rather than hoping. Drop the file into a subfolder
named after the project instead of at the top level:

```
Inbox/acme/pricing-call.md            -> project: acme
Inbox/acme/delivery/pricing-call.md   -> project: acme, layer: delivery
```

The folder name is matched loosely, so `acme`, `Acme`, and `ACME` all reach the same
project, and both the folder name and the `project:` name in its meta file are
checked. If the folder does not match a project you already have, it is ignored and
the contents are auto-detected as usual. To see the projects you have and the
keywords each of them routes on:

```
gigabite project list
```

## The two special folders

**`_filed/<date>/`** holds your original files, moved here after they were filed. The
copy in your knowledge base is the one that gets searched; this is your safety net.
If two files share a name, the second becomes `name-2.md`. Nothing is overwritten and
nothing is deleted, so clear this out yourself whenever you feel like it.

**`_needs-triage/`** holds files that could not be filed confidently. The tool never
guesses at a project. There are three usual reasons a file lands here: no project
matched, in which case move it into an `Inbox/<project>/` folder and re-run; the file
type is not supported, in which case convert it or copy the text into a `.md` file;
or the file was empty, or was not readable text. Fix the cause, move the file back up
into `Inbox/`, and run `gigabite file` again.

## A note on privacy

Nothing that passes through here leaves your machine. This folder sits inside
`~/Knowledge`, which is never committed to git and never sent to a web surface, so
client and meeting content cannot reach GitHub by way of the drop box. Each note
written from here is stamped with `origin: inbox/<the path you dropped it at>` so you
can always trace where a claim came from, and with `share: private`, so that sharing
anything later has to be a deliberate act.
