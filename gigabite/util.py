"""Small, dependency-free helpers: text extraction, id hashing, FTS query safety."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

def doc_id(source: str, native_id: str) -> str:
    """Stable, collision-resistant document id, namespaced by source."""
    h = hashlib.sha1(f"{source}:{native_id}".encode("utf-8")).hexdigest()[:16]
    return f"{source}:{h}"


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

def clean_text(s: Any) -> str:
    """Coerce to a trimmed string, collapsing runs of blank lines."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def coalesce_blocks(content: Any) -> str:
    """Flatten a message 'content' field to searchable text.

    Handles the several shapes Claude uses:
      - a plain string
      - a list of typed blocks: {type: text|thinking|tool_result|...}
    Tool-call plumbing (tool_use inputs, images) is intentionally dropped;
    we keep human/assistant prose and thinking.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "thinking":
                parts.append(str(block.get("thinking", "")))
            elif btype == "tool_result":
                # tool results can carry pasted/returned text worth searching
                inner = block.get("content")
                parts.append(coalesce_blocks(inner))
            # tool_use / image / redacted_thinking -> skip
    elif isinstance(content, dict):
        return coalesce_blocks([content])
    return "\n".join(p for p in parts if p and p.strip())


def word_count(s: str) -> int:
    return len(re.findall(r"\w+", s or ""))


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

def to_iso_utc(value: Any) -> str:
    """Best-effort normalise a timestamp to ISO-8601 UTC. Returns '' on failure."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        # epoch seconds or ms
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(value).strip()
    if not s:
        return ""
    # already ISO-ish -> normalise Z
    candidate = s.replace("Z", "+00:00")
    for parser in (_iso, _loose):
        dt = parser(candidate)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
    return s  # keep the raw string rather than lose information


def _iso(s: str):
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _loose(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def short_date(iso: str) -> str:
    if not iso:
        return "—"
    return iso[:10]


# ---------------------------------------------------------------------------
# FTS5 query safety
# ---------------------------------------------------------------------------

_FTS_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def to_fts_query(raw: str) -> str:
    """Turn a natural-language query into a safe FTS5 MATCH expression.

    Each token is quoted as a phrase and AND-ed together. A trailing '*' on a
    token is preserved as a prefix search. This never raises a MATCH syntax
    error, at the cost of not exposing raw boolean operators (use --raw for that).
    """
    tokens = []
    for m in _FTS_TOKEN.finditer(raw or ""):
        tok = m.group(0)
        # allow prefix search if the user typed word*
        end = m.end()
        star = end < len(raw) and raw[end] == "*"
        tokens.append(f'"{tok}"*' if star else f'"{tok}"')
    return " ".join(tokens)


def chunks(seq: Iterable, n: int):
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
