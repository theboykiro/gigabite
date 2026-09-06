# gigabite

gigabite is a local context router for Claude. It gives Claude a searchable memory
of everything you have already said and decided — your Claude Code sessions, your
claude.ai chats, your meetings, your own notes, and your calendar — and it
loads the relevant parts of that history into each turn before Claude answers. You
talk to it the way you would talk to an assistant who was in the room last week,
rather than to a model that starts every conversation from nothing.

The problem it solves is not retrieval for its own sake. It is the tax you pay for
having your working memory scattered across four tools, none of which can see the
others. A decision you argued through in a Claude Code session in March is invisible
to a claude.ai chat in August, and both are invisible to the meeting where the
subject comes up again. gigabite closes that gap by keeping one index over all of it
on your own machine, and by making sure every turn is answered with that index
already consulted.

Everything here is pure Python standard library. There is no server, no embedding
model, and no cloud service. The index is a single SQLite file, your content lives
in a plain folder in your home directory, and only code is ever committed to this
repository. If you want the reasoning behind the design rather than the mechanics of
it, start with [`docs/PHILOSOPHY.md`](docs/PHILOSOPHY.md).

---

## Installing

One command, from nothing to working:

```bash
curl -fsSL https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.sh | bash
```

That checks your Mac can run it, downloads the code to `~/gigabite`, and runs the
installer. Run it again any time to update.

If you would rather read a script than pipe it into a shell — a reasonable instinct,
and the reason the file is short and plain — download it, read it, then run it:

```bash
curl -fsSL -o bootstrap.sh https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.sh
less bootstrap.sh
bash bootstrap.sh
```

Or skip it entirely and do the two steps yourself. Clone into your home folder
rather than Desktop, Documents or Downloads: macOS blocks background jobs from
those, so the daily synthesis would never run.

```bash
git clone https://github.com/theboykiro/gigabite.git ~/gigabite
cd ~/gigabite && ./install.sh
```

`install.sh` is the same script in all three cases, and it is safe to re-run. It
creates the `~/.core` and `~/Knowledge` layouts without ever overwriting content you
already have, puts the `gigabite` launcher on your `PATH`, appends the router
protocol to `~/.claude/CLAUDE.md` inside markers it manages, registers the
ambient-recall hook in `~/.claude/settings.json`, schedules the gated end-of-day
synthesis job through launchd, and builds the initial index.

It also installs the Claude Code slash commands (`/gg`, `/search`,
`/search-status`, `/calendar`, `/meeting`), the `gg-*` subagents, and three skills
(`meeting-prep`, `decision-record`, `design-critique`) that trigger on what you ask
for rather than needing to be named, refreshing its own copies on every run so an
update reaches them. If a command or agent of that
name is already yours, it is left alone and the installer tells you it did so. The
one file it replaces outright is `~/Knowledge/README.md`, and your old copy is kept
beside it. Your knowledge base, your notes and your own `core.md` are never touched.

You need macOS and Python 3.9 or later; the system Python is fine. There is nothing
to `pip install`. Claude Code is not strictly required — `gigabite search` works on
its own in a terminal — but the router, the ambient recall and the slash commands
all live inside it, so without it you have the index and not the point of it.

Code comes from git, but your content does not. `~/.core` and `~/Knowledge` are
local, and they rebuild themselves as you add sources again. Re-run `./install.sh`
at any point to pick up changes to the tool.

### If you installed an earlier version

The layout has moved on since earlier releases, and `gigabite relocate` brings an
existing store up to date in one pass — `--dry-run` first if you want to see every
move before it happens. Earlier
releases kept the knowledge base hidden at `~/.knowledge`, which is precisely why it
was hard to find and trust; it is now `~/Knowledge`, an ordinary folder you can open
in Finder. Making it visible exposed the second problem: the tool's own furniture was
visible with it, so all of it now sits behind one hidden `.gigabite/` directory, and
the staging folder that used to sit alongside the projects is gone — you put a file
where it belongs instead. Nothing is deleted by the migration; the index is rebuilt
afterwards because it is derived data. If you would rather keep the stores elsewhere,
`GIGABITE_KNOWLEDGE_DIR` and `GIGABITE_CORE_DIR` override both paths.

### Uninstalling

```bash
cd ~/gigabite && ./uninstall.sh
```

It shows you exactly what it is about to remove and waits for a yes. `--dry-run`
prints that list and stops; `--yes` skips the question, and is required when the
script is fed down a pipe rather than run at a terminal. It is safe to run twice —
the second time it tells you everything is already gone.

It reverses `install.sh` step for step: the `gigabite` launcher and the `PATH` line
in your `.zshrc` and `.bash_profile`, the slash commands, the `gg-*` subagents, the
three skills, the router block in `~/.claude/CLAUDE.md`, the ambient-recall hook in
`~/.claude/settings.json`, the scheduled synthesis job and its log. Files it edits
rather than owns are backed up first, and it tells you where.

**`~/Knowledge` and `~/.core` are never touched**, and the script says so and prints
where they are. They hold your meetings, your notes and your own `core.md` — the only
things on the machine a fresh clone cannot rebuild. On the same principle, a command
or agent carrying one of gigabite's names that gigabite did not write is left exactly
where it is and reported, never deleted. The clone is left too, because the script is
running from inside it; it prints the one `rm -rf ~/gigabite` you can run yourself.

## Getting content in

gigabite is only as useful as the history it holds, so the capture paths are
deliberately cheap. Two of them need nothing from you at all, and the rest are a
matter of putting a file where it belongs.

There is one destination, and it is the store itself: a file placed anywhere under
`~/Knowledge/<project>/[<layer>/]` is indexed where it sits on the next ingest. There
is no staging folder and no filing step, because that arrangement gave content two
possible homes and made "where is my meeting?" depend on whether a pass had run yet.
When something arrives without a project the tool can resolve, it lands loose at the
top of `~/Knowledge` — visible, indexed, one drag from being filed. No folder is ever
invented for it, because a confidently misfiled note is worse than an unfiled one.

| Source | How it arrives |
|---|---|
| **Claude Code** | Every session under `~/.claude/projects/` is read automatically on each `gigabite ingest`. Nothing to do. |
| **Claude.ai** | Run `install/scripts/claude-ai-safari-export.js` in the claude.ai browser console, put the downloaded `conversations.json` in `~/Knowledge/.gigabite/imports/claude_ai/`, then `gigabite ingest && gigabite materialize`. |
| **Meetings** | Export the meeting as Markdown (from Granola, or anything else) into `~/Knowledge/<project>/meetings/`. That is the only destination — `/meeting` in Claude Code is a shortcut that puts a copied transcript in that same folder, not a second place to look. |
| **Notes** | `gigabite save "…" --project <p> [--layer <l>]`, which routes the note into `~/Knowledge/<project>/<layer>/`. |
| **Calendar** | Paste a screenshot into Claude Code and run `/calendar`; the meetings are parsed, filed, and matched with prep. |
| **Anything else** | `gigabite add path/to/file` — a screenshot, a PDF, a transcript. Nothing is refused: a file whose text cannot be read is kept and indexed by name, type, size and date, with no pretence that its contents were read. |

The claude.ai path deserves a word of explanation, because it looks more awkward
than it should. claude.ai has no official API for your web chats, and the internal
endpoints its web app uses are Cloudflare-gated against terminal clients. The
in-page export script sidesteps that by running inside the browser, where it carries
your real session, and produces a `conversations.json` that the importer already
understands — including chats inside projects, tagged with the project name. A
keychain-token route (`gigabite claude-login` and `gigabite claude-sync`) is built
and documented, but Cloudflare currently blocks it, so the browser export is the
route that works. The detail is in [`docs/CLAUDE_AI.md`](docs/CLAUDE_AI.md).

Meetings arrive by hand, and deliberately so: there is no integration with a
meeting-notes app, and nothing here reads one's local store — which is why the source
is called `meeting` rather than after any of them. A meeting is in the index because
it is a file in `~/Knowledge/<project>/meetings/` — a route with no credential in it
and nothing to break when the app that recorded it ships an update. A clean pull from
Granola's public API is the future route whenever you have access; see
[`docs/MEETINGS.md`](docs/MEETINGS.md).

An export is machine-readable rather than readable, so a claude.ai chat would
otherwise exist only inside `conversations.json` and inside SQLite — leaving
`~/Knowledge` a partial view of the store while claiming to be all of it.
`gigabite materialize` writes every indexed document out as a markdown file under its
project (meetings into `meetings/`, chats into `conversations/`), so the folder is the
complete picture. Each file it writes names the document it renders in its
frontmatter, which is what stops the same conversation being indexed twice; running it
again does nothing, and raw imports are moved aside rather than deleted.

Whatever the source, the rhythm is the same: put a file where it belongs and run
`gigabite ingest`. Ingest is incremental — files whose size and modification time
are unchanged are skipped, and a newer export updates the existing document in place
rather than creating a duplicate. Documents you have not touched for thirty days are
archived out of the default search, non-destructively, and a search that matches one
brings it straight back.

## Using it day to day

The primary interface is conversation, not the command line. Because the installer
registers a `UserPromptSubmit` hook, every message you send in Claude Code already
has a block of recalled context attached to it, so ordinary questions are answered
against your history without you asking for it. When you want that explicitly — the
operating protocol loaded in full, a fresh ingest, and a deliberate recall pass — use
the slash commands:

```
/gg what's still open on the traffic drop?    load core + recall + answer
/search <query>                                search everything
/search-status                                 what is indexed right now
/calendar                                      after pasting a calendar screenshot
/meeting                                       file the transcript on your clipboard
```

The terminal is there when you want to work directly against the index, or when
Claude is not in front of you. These are the commands that matter most:

```bash
gigabite search "enterprise pricing anchor"     # search everything
gigabite search "budget" --source meeting       # restrict to one source
gigabite search "roadmap" --project acme --all  # scope to a project; --all includes archived
gigabite doc <doc_id>                           # print a full conversation
gigabite save "Decided X because Y" -p acme -l delivery -t "Title"
gigabite project add acme --keywords "acme, acme corp"
gigabite paste                                  # a copied transcript -> its project folder
gigabite add ~/Desktop/shot.png -p acme         # store any file in a project folder
gigabite materialize --dry-run                  # render indexed documents as files
gigabite calendar agenda --day today            # meetings with attached prep
gigabite synthesize                             # write the gated end-of-day proposal
gigabite decay --status                         # reference-frequency archiving
gigabite ingest                                 # refresh the index, incrementally
gigabite status                                 # what is indexed
gigabite paths                                  # where everything lives
```

Three more exist for narrower jobs: `gigabite reindex` clears and rebuilds the index
from scratch, `gigabite relocate` brings an older layout up to date, and
`gigabite route` resolves context and recalls passages as JSON, which is what `/gg`
and the ambient hook call underneath. Run `gigabite --help`, or
`gigabite <command> --help`, for the full flag list on any of them.

If you are ever unsure where something ended up, `gigabite paths` prints the resolved
location of the core directory, the knowledge base, the projects inside it, and the
hidden `.gigabite/` directory that holds the machinery.

## How it works

Three moving parts sit behind all of the above, and they are worth understanding
because they explain most of the tool's behaviour.

**Ingest** normalises every source into the same shape. A *document* is one
conversation, meeting, note, or calendar entry; it carries a source, a project, a
title, and timestamps. Each document holds *messages* — the individual turns of a
chat, the segments of a transcript, or the single body of a note. Because every
source lands in that shape, search does not care where something came from.

**The index** is one SQLite FTS5 table with a row per message, kept at
`~/Knowledge/.gigabite/index/gigabite.db`. The document title is denormalised onto every
message row so that a title match can be weighted separately, and ranking uses
`bm25` with the title weighted five times the body. A query first runs as an AND of
all its terms, with common stop words dropped so that a naturally phrased question
does not exclude the material that answers it, and falls back to an OR pass ranked by
`bm25` if the AND pass finds nothing. Your own Claude Code transcripts are ranked
down by a fixed factor, because a transcript of you asking about something is a dense
textual match for that question without being evidence about it; the penalty demotes
them rather than excluding them, so a session transcript still wins when it genuinely
is the best answer.

**Storage** is split deliberately. Code lives in this repository. Content lives in
`~/Knowledge`, one top-level folder per project, with everything mechanical — the
index, raw imports, the archive, proposals, routing aliases — behind a single hidden
`.gigabite/` directory, so `ls ~/Knowledge` shows your projects and a README and
nothing you have to explain. The operating protocol — your voice, tone, and decision
principles — lives in `~/.core/core.md` and is loaded whole on every routed turn.
Secrets, if there are ever any, live in the macOS keychain and nowhere else. The rule
that keeps this from degrading is that knowledge is never written to the working
directory, no matter which repository Claude Code happens to be pointed at;
[`docs/ROUTING.md`](docs/ROUTING.md) explains the mechanism.

The repository itself is arranged around what each thing is *for* — read it, run it,
the code, the docs, the machine integration, the tests:

```
README.md            start here
install.sh           one-shot installer (idempotent, non-destructive)
uninstall.sh         reverses it, and never touches ~/Knowledge or ~/.core
bin/gigabite         self-locating launcher — this is what ends up on your PATH
gigabite/            the package
  config.py            paths & constants (overridable via env)
  store.py             SQLite + FTS5 index, upsert & search
  util.py              text extraction, time parsing, FTS query safety
  ingest.py            runs every source in order; notes last, and why
  cli.py               the `gigabite` command
  features/            save · intake · routing · materialize · relocate ·
                       calendar · synthesis · decay · sops
  sources/
    claude_code.py     ~/.claude/projects/**/*.jsonl
    claude_ai.py       browser export (.zip / conversations.json)
    meetings.py        meeting notes and exports you supply
    notes.py           ~/Knowledge/{project}/[{layer}/] — every file in it
docs/                design & reference — start with PHILOSOPHY.md
install/             everything install.sh copies onto the machine
  claude-commands/     /gg, /search, /search-status, /calendar, /meeting
  hooks/               the ambient-recall UserPromptSubmit hook
  scaffold/            templates for ~/.core, ~/Knowledge, ~/.claude/{agents,skills}
  launchd/             the scheduled daily synthesis job
  scripts/             the claude.ai in-browser export helper
tests/               python3 -m unittest discover -s tests
```

Note what is absent: there is no content directory in this tree. The knowledge base
is not here, and neither is the index.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite touches no network and writes nothing to your home directory — the tests
redirect the stores to a temporary directory through the same environment variables
you would use to relocate them yourself.

## Where things live, and why that matters

The separation between code and content is the one rule this project will not bend
on, and it is a client-confidentiality boundary rather than a preference. Only code
belongs in git. Your operating protocol in `~/.core` and your knowledge base in
`~/Knowledge` stay on the device, backed up by iCloud, and the repository must remain
free of client names, stakeholders, and internal detail — including in examples. Every
note gigabite writes carries an explicit `share: private` marker, so that sharing
anything later is a deliberate act and never a side effect.

That leaves one thing worth your attention. iCloud is a sync, not a version history,
and your knowledge base will become the most valuable thing on the machine long
before you notice. Set up a real versioned backup for `~/Knowledge` — the note in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) covers the reasoning. Everything else
in this system is reproducible from a clone and a script; that folder is not.

## Contributing

Bug reports, ideas, and pull requests are welcome —
[`CONTRIBUTING.md`](CONTRIBUTING.md) covers the flow from fork to merge, including for
people who have not sent a pull request before. The one rule that is not negotiable is
the boundary above: only code goes in this repository, never content, and never a real
client name — not even in an example.

## License

MIT — see [`LICENSE`](LICENSE).
