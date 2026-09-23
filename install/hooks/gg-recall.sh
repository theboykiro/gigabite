#!/usr/bin/env bash
# gigabite UserPromptSubmit hook — ambient recall.
# On each prompt, retrieve strongly-relevant prior context from the local index
# and inject it so the conversation is answered WITH your history loaded.
# Silent unless there's a genuinely strong hit, so it never derails other work.
#
# Reads the hook JSON on stdin; prints a compact recall block to stdout (which
# Claude Code adds to the turn's context). Never fails the prompt: always exit 0.

GIGABITE_BIN="__GIGABITE_BIN__"
PY=/usr/bin/python3

input=$(cat)
# Line 1: the session id (safe characters only, may be empty). The rest: the prompt.
parsed=$(printf '%s' "$input" | "$PY" -c 'import sys,json,re
try:
    d = json.load(sys.stdin)
    p = d.get("prompt") or ""
    s = d.get("session_id") or ""
    if isinstance(p, str) and p:
        print(re.sub(r"[^A-Za-z0-9_-]", "", s if isinstance(s, str) else "")[:128])
        print(p)
except Exception:
    pass' 2>/dev/null)
session=${parsed%%$'\n'*}
prompt=${parsed#*$'\n'}
[ "$prompt" = "$parsed" ] && prompt=""

# An empty prompt has nothing to route. Everything else goes to the router: the
# `register` axis below owns the "is this turn worth recalling for?" call, so a
# word-count pre-filter here could only disagree with it — and it did, silently,
# in the direction that loses recall: `widget pricing` is two words and a real
# project's keywords, which is precisely the short turn that should recall.
[ -z "$prompt" ] && exit 0

# The session id lets an unanswered "which project?" come back once per session
# (features/bindings.py). `--` so a prompt that starts with a dash is not a flag.
json=$("$GIGABITE_BIN" route --json ${session:+--session-id "$session"} -- "$prompt" 2>/dev/null) || exit 0

GG_JSON="$json" "$PY" - <<'PY'
import os, json
try:
    d = json.loads(os.environ.get("GG_JSON", ""))
except Exception:
    raise SystemExit(0)
# The register router (AUTONOMY §7): a sparring turn gets nothing injected. An
# absent or unrecognised mode falls back to injecting, because a stale binary that
# says nothing about the register should behave the way it did before it existed.
if (d.get("register") or {}).get("mode") == "spar":
    raise SystemExit(0)
hits = d.get("hits") or []
# bm25 scores are negative; more negative = stronger. Only inject strong hits.
strong = [h for h in hits if isinstance(h.get("score"), (int, float)) and h["score"] < -1.0]
if not strong:
    # Nothing recalled. When the turn resolved to no project at all, the router
    # hands back a one-time question instead — asking which project this folder is
    # beats vanishing, because silence reads as "no memory of this" and a fresh
    # install has no projects to resolve to. Absent on every other path, including
    # a binary too old to know about it.
    ask = d.get("ask")
    text = (ask or {}).get("text") if isinstance(ask, dict) else None
    if isinstance(text, str) and text:
        print(text)
    raise SystemExit(0)
ctx = d.get("context") or {}
out = ["[gigabite recall — prior context from your own history; cite as source · title · date if used]"]
if ctx.get("project"):
    out.append(f"detected context: {ctx['project']} ({ctx.get('confidence')})")
seen = set()
for h in strong:
    if h["doc_id"] in seen:
        continue
    seen.add(h["doc_id"])
    date = (h.get("created_utc") or "")[:10]
    snip = " ".join((h.get("snippet") or "").split())[:240]
    out.append(f"- {h.get('title','?')} [{h.get('source')} · {date} · {h['doc_id']}]: {snip}")
    if len(seen) >= 5:
        break
print("\n".join(out))
PY
exit 0
