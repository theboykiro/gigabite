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
prompt=$(printf '%s' "$input" | "$PY" -c 'import sys,json;
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: pass' 2>/dev/null)

# skip trivial / empty prompts
[ -z "$prompt" ] && exit 0
words=$(printf '%s' "$prompt" | wc -w | tr -d ' ')
[ "${words:-0}" -lt 3 ] && exit 0

json=$("$GIGABITE_BIN" route --json "$prompt" 2>/dev/null) || exit 0

GG_JSON="$json" "$PY" - <<'PY'
import os, json
try:
    d = json.loads(os.environ.get("GG_JSON", ""))
except Exception:
    raise SystemExit(0)
hits = d.get("hits") or []
# bm25 scores are negative; more negative = stronger. Only inject strong hits.
strong = [h for h in hits if isinstance(h.get("score"), (int, float)) and h["score"] < -1.0]
if not strong:
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
