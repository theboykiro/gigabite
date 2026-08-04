"""Small, dependency-free helpers: text extraction, id hashing, FTS query safety."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
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
    """Best-effort normalise a timestamp to ISO-8601 UTC.

    Returns '' only for genuinely empty input or an unusable epoch number. A
    string that cannot be parsed is returned **unchanged** (see the end of this
    function) — losing it entirely would be worse, because callers put this value
    in filenames and `short_date('')` renders as '—'.

    So the contract is: the result is ISO-8601 when parsing succeeded, and
    otherwise whatever you passed in. Callers that need a real date must check,
    not assume. `short_date` only truncates ISO-shaped values for this reason.
    """
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


# Formats carrying an explicit year. Day-first precedes month-first deliberately:
# these sources are UK-authored, so '03/08/2026' means 3 August. Previously
# '%m/%d/%Y' was tried first, which silently read it as 8 March — but only for
# days 1-12, since 13+ fails month-first and fell through to day-first. The same
# format therefore meant different things depending on the number, undetectably.
# '%m/%d/%Y' is kept last so an unambiguous US-style date still parses.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d/%m/%Y", "%d/%m/%y",
    "%d %b %Y", "%b %d %Y", "%d %B %Y", "%B %d %Y",
    "%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y",
    "%m/%d/%Y",
)

# Year-less formats — Granola writes meeting dates like 'Jul 29' / '3 Aug'.
_DATE_FORMATS_NO_YEAR = ("%d %b", "%b %d", "%d %B", "%B %d")


def _infer_year(dt: datetime, today=None) -> datetime:
    """Attach a year to a year-less date, assuming it is recent past.

    'Jul 29' on a meeting note means the 29 July that already happened, not next
    year's. Use the current year unless that lands in the future, in which case
    the date belongs to last year. One day of tolerance absorbs timezone skew.
    """
    today = today or datetime.now(timezone.utc).date()
    for year in (today.year, today.year - 1):
        try:
            candidate = dt.replace(year=year)
        except ValueError:      # 29 Feb in a non-leap year
            continue
        if candidate.date() <= today + timedelta(days=1):
            return candidate
    return dt


def _loose(s: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    for fmt in _DATE_FORMATS_NO_YEAR:
        try:
            return _infer_year(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return None


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def short_date(iso: str) -> str:
    """YYYY-MM-DD from an ISO timestamp. '—' when empty.

    Only truncates values that really are ISO-shaped. Slicing unconditionally
    turned an unparsed 'Jul 29 2026' into 'Jul 29 202' — a corrupted string in a
    filename. An unparseable value is now returned whole: still wrong, but
    visibly wrong rather than silently mangled.
    """
    if not iso:
        return "—"
    return iso[:10] if _ISO_DATE.match(iso) else iso


# ---------------------------------------------------------------------------
# FTS5 query safety
# ---------------------------------------------------------------------------

_FTS_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def fts_tokens(raw: str) -> list[str]:
    """Quoted, FTS5-safe tokens from a natural query (prefix '*' preserved)."""
    tokens = []
    for m in _FTS_TOKEN.finditer(raw or ""):
        tok = m.group(0)
        end = m.end()
        star = end < len(raw) and raw[end] == "*"
        tokens.append(f'"{tok}"*' if star else f'"{tok}"')
    return tokens


def to_fts_query(raw: str, op: str = "AND") -> str:
    """Turn a natural-language query into a safe FTS5 MATCH expression.

    op='AND' -> every term must appear (implicit AND, tokens space-joined).
    op='OR'  -> any term may appear (ranked by bm25 so full matches rise).
    Never raises a MATCH syntax error; use --raw for raw boolean operators.
    """
    tokens = fts_tokens(raw)
    joiner = " OR " if op.upper() == "OR" else " "
    return joiner.join(tokens)


def chunks(seq: Iterable, n: int):
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf
