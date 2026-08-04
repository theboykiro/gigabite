# Claude.ai integration

Pulls **all** your claude.ai web conversations — both projectless chats and chats
inside projects (tagged with the project name) — into the local index, and keeps
them fresh on every `ingest`.

## Why a session token

claude.ai has **no official API** for your web chats (the developer Messages API
can't see them). The only way to read them programmatically is the same internal
endpoints the web app calls, authenticated by your browser's `sessionKey` cookie.

**Own this trade-off:** those endpoints are undocumented and can change without
notice, and automated access is a grey area under claude.ai's terms of service.
It is your own data, pulled with your own credentials, stored only on your machine.

## Setup (once)

```bash
gigabite claude-login
```

It tells you how to copy your `sessionKey` (claude.ai → DevTools → Application →
Cookies → `sessionKey`, starts `sk-ant-sid…`) and opens the **macOS secure
prompt** to store it. The token:

- is entered into `security`'s hidden prompt — never on the command line or in
  shell history,
- is stored in your **login keychain** (service `gigabite:claude_ai`),
- is **never** seen by Claude, written to a file, or sent anywhere except
  claude.ai itself.

Then pull everything:

```bash
gigabite claude-sync        # first full pull; incremental after that
```

On first read of the token you may get a keychain prompt — choose **Always Allow**
so future syncs are silent.

## How refresh works

- `gigabite ingest` (and `/search`, which refreshes first) do an **incremental**
  live pull when a token is present: list conversations, compare `updated_at`,
  fetch full messages only for new/changed ones. Fast after the first sync.
- No token set → the live pull silently no-ops. Nothing else is affected.
- Skip the network for a purely local refresh: `gigabite ingest --no-remote`.
- Token expired/blocked → you'll see a clear `401/403` note; re-run `claude-login`.

## Rotating / removing the token

```bash
gigabite claude-login                                   # overwrites the stored token
security delete-generic-password -s gigabite:claude_ai  # removes it entirely
```

## Caveats / known unknowns

- **Cloudflare**: if claude.ai fronts these endpoints with bot protection, a plain
  request may get a `403`. If that happens, say so — the fallback is the browser
  route (drive your logged-in claude.ai via the Claude-in-Chrome extension).
- The exact JSON field names are handled defensively, but claude.ai can change
  them; the parser degrades gracefully and reports what it couldn't map.
- Verified here with mocked responses; the live endpoint shape is confirmed on
  your first `claude-sync`.
