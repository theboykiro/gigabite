# ~/Knowledge/_sources/granola

This folder is where raw Granola exports land. It is a machine-read staging area
rather than something to browse: the files here keep whatever shape Granola produced
them in, and the readable version of a meeting ends up in the project's `meetings/`
layer once it has been filed. You are not expected to open anything in here.

The reason Granola needs a folder of its own is that its notes cannot be read
automatically. Version 6 encrypts the entire local store behind a macOS keychain key,
and reading that key triggers an authorisation prompt, so nothing can pull your
meetings unattended. They have to be supplied by hand, which is what the routes below
do.

## The route to use

Copy the transcript in Granola and, in Claude Code, run `/granola`. The command reads
your clipboard directly, picks the title and date out of the transcript's own header,
auto-detects the project, files the meeting, and indexes it — without the transcript
ever passing through the chat. From a terminal the same thing is `gigabite paste`.

For a meeting you have already exported as a file, drop it into `~/Knowledge/Inbox/`
instead, or into `~/Knowledge/Inbox/<project>/meetings/` if you want to be certain
which project it belongs to, and run `gigabite file`. That path files it as a
readable note alongside your hand-written meeting notes.

## Bulk exports

When you want to bring a batch of meetings across at once, export them from Granola
as **Markdown** and drop the files straight into this folder. `.txt` and `.json`
exports work too. Then run:

```
gigabite ingest
```

Both the notes and the transcript are indexed, and YAML front matter (`title:`,
`date:`, `project:`) is picked up when it is present. Re-dropping a newer version of
a meeting updates the existing entry rather than creating a second one.

## The experimental route

`gigabite granola-connect` attempts to read the Granola keychain key — you approve a
one-time macOS prompt — and decrypt the local cache directly. Granola's at-rest
format is not published, so it tries the known Electron patterns and prints
diagnostics if none of them match. Treat it as a spike rather than a supported path;
`--diagnose` will tell you what it saw without indexing anything. If it cannot
decrypt, use the routes above, which is what they exist for. The full status is in
`docs/GRANOLA.md` in the repository.
