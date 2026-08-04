# gigabite

gigabite is a local context router for Claude. It gives Claude a searchable memory
of everything you have already said and decided — your Claude Code sessions, your
claude.ai chats, your Granola meetings, your own notes, and your calendar — and it
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

Installation is a single script, and it is safe to run repeatedly:

```bash
./install.sh
```

The script is idempotent and additive. It creates the `~/.core` and `~/Knowledge`
layouts without ever overwriting content you already have, puts the `gigabite`
launcher on your `PATH`, installs the Claude Code slash commands (`/gg`, `/search`,
`/recall-status`, `/calendar`, `/granola`) and the `gg-*` subagents, appends the
router protocol to `~/.claude/CLAUDE.md`, registers the ambient-recall hook in
`~/.claude/settings.json`, schedules the gated end-of-day synthesis job through
launchd, and finally builds the initial index. Where a file already exists it says
so and leaves it alone.

You need macOS and Python 3.9 or later; the system Python is fine. There is nothing
to `pip install`.

On a new machine the whole setup is a clone and a run:

```bash
git clone <your-repo-url> && cd giga-bite && ./install.sh
```

Code comes from git, but your content does not. `~/.core` and `~/Knowledge` are
local, and they rebuild themselves as you add sources again. Re-run `./install.sh`
at any point to pick up changes to the tool.

### If you installed an earlier version

Earlier releases kept the knowledge base at `~/.knowledge` — a hidden folder, which
is precisely why it was hard to find and use — and put the drop folder inside this
repository as a visible workaround. Both of those have moved. The knowledge base is
now `~/Knowledge`, an ordinary folder you can open in Finder, and the drop folder is
`~/Knowledge/Inbox`. Move the contents of the old hidden folder across, delete the
repository's `Inbox/` directory once it is empty, and re-run `./install.sh` to
rebuild the index in its new home. If you would rather keep the stores elsewhere,
`GIGABITE_KNOWLEDGE_DIR` and `GIGABITE_INBOX_DROP_DIR` still override both paths.

## Getting content in

gigabite is only as useful as the history it holds, so the capture paths are
deliberately cheap. Two of them need nothing from you at all, and the rest are a
matter of dropping a file somewhere obvious.

| Source | How it arrives |
|---|---|
| **Claude Code** | Every session under `~/.claude/projects/` is read automatically on each `gigabite ingest`. Nothing to do. |
| **Claude.ai** | Run `install/scripts/claude-ai-safari-export.js` in the claude.ai browser console, then drop the downloaded `conversations.json` into `~/Knowledge/_sources/claude_ai/` and run `gigabite ingest`. |
| **Granola** | Export a meeting as Markdown and drop it into `~/Knowledge/Inbox/`, or copy the transcript and run `/granola` in Claude Code. Either way it is filed into the project's `meetings/` folder. |
| **Notes** | `gigabite save "…" --project <p> [--layer <l>]`, which routes the note into `~/Knowledge/<project>/<layer>/`. |
| **Calendar** | Paste a screenshot into Claude Code and run `/calendar`; the meetings are parsed, filed, and matched with prep. |
| **Anything else** | Drop the file in `~/Knowledge/Inbox/` and run `gigabite file`. See [`install/scaffold/inbox-README.md`](install/scaffold/inbox-README.md). |

The claude.ai path deserves a word of explanation, because it looks more awkward
than it should. claude.ai has no official API for your web chats, and the internal
endpoints its web app uses are Cloudflare-gated against terminal clients. The
in-page export script sidesteps that by running inside the browser, where it carries
your real session, and produces a `conversations.json` that the importer already
understands — including chats inside projects, tagged with the project name. A
keychain-token route (`gigabite claude-login` and `gigabite claude-sync`) is built
and documented, but Cloudflare currently blocks it, so the browser export is the
route that works. The detail is in [`docs/CLAUDE_AI.md`](docs/CLAUDE_AI.md).

Granola is awkward for a different reason. Version 6 encrypts its entire local store
behind a macOS keychain item, so the notes cannot be read unattended, which is why
they are supplied by hand. A clean pull from Granola's public API is designed and
half-built for whenever you get API access; see [`docs/GRANOLA.md`](docs/GRANOLA.md).

Whatever the source, the rhythm is the same: put a file somewhere and run
`gigabite ingest`. Ingest is incremental — files whose size and modification time
are unchanged are skipped, and re-dropping a newer export updates the existing
document in place rather than creating a duplicate. Documents you have not touched
for thirty days are archived out of the default search, non-destructively, and a
search that matches one brings it straight back.

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
/recall-status                                 what is indexed right now
/calendar                                      after pasting a calendar screenshot
/granola                                       file the transcript on your clipboard
```

The terminal is there when you want to work directly against the index, or when
Claude is not in front of you. These are the commands that matter most:

```bash
gigabite search "enterprise pricing anchor"     # search everything
gigabite search "budget" --source granola       # restrict to one source
gigabite search "roadmap" --project acme --all  # scope to a project; --all includes archived
gigabite doc <doc_id>                           # print a full conversation
gigabite save "Decided X because Y" -p acme -l delivery -t "Title"
gigabite project add acme --keywords "acme, acme corp"
gigabite file                                   # file whatever is in ~/Knowledge/Inbox
gigabite paste                                  # file the clipboard (a copied transcript)
gigabite calendar agenda --day today            # meetings with attached prep
gigabite synthesize                             # write the gated end-of-day proposal
gigabite decay --status                         # reference-frequency archiving
gigabite ingest                                 # refresh the index, incrementally
gigabite status                                 # what is indexed
gigabite paths                                  # where everything lives
```

Two more exist for narrower jobs: `gigabite reindex` clears and rebuilds the index
from scratch, and `gigabite route` resolves context and recalls passages as JSON,
which is what `/gg` and the ambient hook call underneath. Run `gigabite --help`, or
`gigabite <command> --help`, for the full flag list on any of them.

If you are ever unsure where something ended up, `gigabite paths` prints the resolved
location of the core directory, the knowledge base, the index database, and each
drop folder.

## How it works

Three moving parts sit behind all of the above, and they are worth understanding
because they explain most of the tool's behaviour.

**Ingest** normalises every source into the same shape. A *document* is one
conversation, meeting, note, or calendar entry; it carries a source, a project, a
title, and timestamps. Each document holds *messages* — the individual turns of a
chat, the segments of a transcript, or the single body of a note. Because every
source lands in that shape, search does not care where something came from.

**The index** is one SQLite FTS5 table with a row per message, kept at
`~/Knowledge/.index/gigabite.db`. The document title is denormalised onto every
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
`~/Knowledge`, organised by project. The operating protocol — your voice, tone, and
decision principles — lives in `~/.core/core.md` and is loaded whole on every routed
turn. Secrets, if there are ever any, live in the macOS keychain and nowhere else.
The rule that keeps this from degrading is that knowledge is never written to the
working directory, no matter which repository Claude Code happens to be pointed at;
[`docs/ROUTING.md`](docs/ROUTING.md) explains the mechanism.

The repository itself is arranged around what each thing is *for* — read it, run it,
the code, the docs, the machine integration, the tests:

```
README.md            start here
install.sh           one-shot installer (idempotent, non-destructive)
bin/gigabite         self-locating launcher — this is what ends up on your PATH
gigabite/            the package
  config.py            paths & constants (overridable via env)
  store.py             SQLite + FTS5 index, upsert & search
  util.py              text extraction, time parsing, FTS query safety
  ingest.py            orchestrates sources + the Inbox filing pass
  cli.py               the `gigabite` command
  features/            save · routing · inbox · calendar · synthesis · decay · sops
  sources/
    claude_code.py     ~/.claude/projects/**/*.jsonl
    claude_ai.py       browser export (.zip / conversations.json)
    granola.py         Granola exports (the manual path)
    granola_live.py    experimental keychain-decrypt connector (dormant)
    notes.py           ~/Knowledge/{project}/[{layer}/]*.md
docs/                design & reference — start with PHILOSOPHY.md
install/             everything install.sh copies onto the machine
  claude-commands/     /gg, /search, /recall-status, /calendar, /granola
  hooks/               the ambient-recall UserPromptSubmit hook
  scaffold/            templates for ~/.core, ~/Knowledge, ~/.claude/agents
  launchd/             the scheduled daily synthesis job
  scripts/             the claude.ai in-browser export helper
tests/               python3 -m unittest discover -s tests
```

Note what is absent: there is no content directory in this tree. The knowledge base
is not here, the drop folder is not here, and neither is the index.

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
