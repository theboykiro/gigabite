# Claude.ai integration

This integration pulls your claude.ai web conversations — both projectless chats and
chats inside projects, tagged with the project name — into the local index, so that
what you worked out in a browser tab is recalled alongside your Claude Code sessions
and your meetings.

Getting them is more awkward than it should be, and the awkwardness is worth
understanding before you pick a route, because the obvious route is the one that does
not currently work. This document explains why, describes the route that does, covers
the token-based alternative and its status, and ends with the caveats you should know
about either way.

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
mv ~/Downloads/conversations.json ~/Knowledge/_sources/claude_ai/
gigabite ingest
```

Anthropic's own data export works as well and lands in the same folder. Go to
claude.ai → Settings → Privacy → Export data, and drop either the `.zip` you receive
or the `conversations.json` extracted from it into `~/Knowledge/_sources/claude_ai/`.
The importer reads both, and handles the schema defensively because Anthropic has
changed field names over time.

Either way, re-running the export later and dropping the newer file updates the
conversations already indexed rather than duplicating them, because entries are
de-duplicated by conversation id.

## The token route, and its status

A programmatic route is built and documented, and Cloudflare currently blocks it. It
is described here because it is in the codebase, because it works cleanly the moment
the block lifts, and because the security handling around it is the pattern to follow
for any future credential.

Setup is once:

```bash
gigabite claude-login
```

The command tells you how to copy your `sessionKey` from claude.ai — DevTools →
Application (or Storage) → Cookies → `sessionKey`, which starts `sk-ant-sid…` — and
then opens the **macOS secure prompt** to store it. The token is entered into the
`security` tool's hidden prompt, so it never appears on the command line or in shell
history; it is stored in your login keychain under the service `gigabite:claude_ai`;
and it is never seen by Claude, never written to a file, and never sent anywhere
except claude.ai itself. On first read you may get a keychain access prompt — choose
**Always Allow** so later syncs are silent.

Then pull:

```bash
gigabite claude-sync        # first full pull; incremental afterwards
```

Refresh is incremental: the sync lists conversations, compares `updated_at`, and
fetches full messages only for the ones that are new or changed. `gigabite ingest`
does not run this by default — local sources only — because a Cloudflare-gated call on
every refresh would add latency and noise for nothing. Pass `--remote` if you want it
included. With no token stored, the live pull silently does nothing and no other
source is affected. If the token expires or is blocked you will see a clear `401` or
`403` note; re-run `claude-login`.

To rotate or remove it:

```bash
gigabite claude-login                                   # overwrites the stored token
security delete-generic-password -s gigabite:claude_ai  # removes it entirely
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

Use the browser export. Run it every few weeks, drop the file in
`~/Knowledge/_sources/claude_ai/`, and run `gigabite ingest`. It takes a minute, it
requires no stored credential, and it is the only route that is not one Cloudflare
rule away from breaking. The token path stays in the tree for the day that changes.
