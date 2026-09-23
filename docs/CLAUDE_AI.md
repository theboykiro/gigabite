# Claude.ai integration

This integration pulls your claude.ai web conversations — both projectless chats and
chats inside projects, tagged with the project name — into the local index, so that
what you worked out in a browser tab is recalled alongside your Claude Code sessions
and your meetings.

Getting them is more awkward than it should be, and the awkwardness is worth
understanding before you pick a route, because the obvious route is the one that does
not currently work. This document explains why, describes the route that does, and
ends with the caveats you should know about.

## Why this is awkward

claude.ai has **no official API** for your web chats. The developer Messages API
cannot see them at all — it is a different product. The only way to read them
programmatically is through the same internal endpoints the web application itself
calls, and those endpoints are fronted by Cloudflare bot protection, which rejects
requests that do not come from a real browser session.

Own the trade-off that follows from using them at all: the endpoints are undocumented
and can change without notice, and automated access sits in a grey area under
claude.ai's terms of service. It is your own data, pulled with your own credentials
and stored only on your machine, but that is a judgement you are making rather than a
guarantee anybody has given you.

## The working route: export from inside the browser

The way past Cloudflare is not to go around it but to run inside it. The script at
`install/scripts/claude-ai-safari-export.js` executes on the claude.ai page itself, so
it carries the browser's own session and clearance, and it produces a
`conversations.json` that gigabite's importer already understands.

To run it in Safari: be logged in at claude.ai in the active tab; enable the Develop
menu under Safari → Settings → Advanced → *Show features for web developers*; open
Develop → Show JavaScript Console; paste the whole file and press Enter. If pasting is
blocked, type `allow pasting`, press Enter, and paste again. The script lists your
organisation's projects, lists every conversation, fetches each one in full, tags it
with its project name, and downloads the result.

Then move the file into place and index it:

```bash
mv ~/Downloads/conversations.json ~/Knowledge/.gigabite/imports/claude_ai/
gigabite ingest
gigabite materialize        # write each chat out as a file you can open
```

`.gigabite/imports/` is where raw machine-readable input lives, and it is hidden
because it is not content: a `conversations.json` is not something you read. That is
what the third command is for — it renders every indexed chat as markdown under
`~/Knowledge/<project>/conversations/`, so the chats exist somewhere you can open them
rather than only inside an export and a SQLite file. Each rendering names the document
it came from, so nothing is indexed twice, and re-running is a no-op.

Anthropic's own data export works as well and lands in the same folder. Go to
claude.ai → Settings → Privacy → Export data, and put either the `.zip` you receive or
the `conversations.json` extracted from it in `~/Knowledge/.gigabite/imports/claude_ai/`.
The importer reads both, and handles the schema defensively because Anthropic has
changed field names over time.

Either way, re-running the export later and replacing the file updates the
conversations already indexed rather than duplicating them, because entries are
de-duplicated by conversation id.

## The token route (removed)

A keychain-token pull (`gigabite claude-login` / `claude-sync`) was built and removed
before alpha, because Cloudflare blocks terminal clients. If an older version stored a
token for you, remove it with:

```bash
security delete-generic-password -s gigabite:claude_ai
```

## Caveats

**Cloudflare is the main one**, and it is currently active: a plain request from a
terminal client gets a `403`, which is exactly why the browser export exists.

**The JSON shape is not a contract.** Field names are handled defensively, but
claude.ai can change them at any time. The parser degrades gracefully and reports what
it could not map rather than failing silently, so a broken import announces itself.

**The conversation list may be capped.** The export script logs how many conversations
it found; if that number looks suspiciously round, pagination may be needed and the
script will need extending.

## In practice

Use the browser export. Run it every few weeks, put the file in
`~/Knowledge/.gigabite/imports/claude_ai/`, and run `gigabite ingest`. It takes a minute, it
requires no stored credential, and it is the only route that is not one Cloudflare
rule away from breaking.
