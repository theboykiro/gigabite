"""Small, dependency-free helpers: text extraction, id hashing, FTS query safety."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

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
# markdown frontmatter
# ---------------------------------------------------------------------------

# Every markdown file this tool reads or writes may carry a YAML-ish header. One
# parser for all of them, here rather than in a source module, because nothing
# about it belongs to a source: notes, meeting exports, saved notes, project meta
# and SOPs all use it.
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` block off a markdown file.

    Returns ``(meta, body)``. Keys are lowercased, values stripped of surrounding
    quotes. Flat scalars only — no YAML dependency, and nothing that needs one.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def title_from_markdown(body: str, fallback: str) -> str:
    """First ``# heading`` if the body opens with one, else ``fallback``."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            break
    return fallback


# ---------------------------------------------------------------------------
# passages
# ---------------------------------------------------------------------------

# Retrieval unit size, in words. Everything indexed is normalised to roughly this
# size regardless of which source it came from.
#
# The problem this solves: sources arrive at wildly different granularity. A
# Granola meeting was one row of ~6,900 words; a Claude Code transcript was 191
# rows averaging ~145 words. BM25 divides by document length, so the meeting was
# penalised for being long while the transcript's short rows scored well — and
# the two were then compared as though they were the same kind of thing. Meetings
# and notes were not losing on relevance, they were losing on row shape.
#
# Both directions therefore need fixing: long messages are split, and runs of
# short ones are merged. TARGET is the size we aim for; MAX is the hard ceiling
# before a single message is broken up. Measured with tools/eval_recall.py — see
# the PR for the sweep. Larger passages retrieve multi-term queries better
# (co-occurring terms stay in one row) but blunt precision; smaller passages do
# the reverse.
PASSAGE_TARGET_WORDS = 180
PASSAGE_MAX_WORDS = 320

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_long(text: str, target: int, hard_max: int) -> list[str]:
    """Break one over-long text into ~target-word pieces on natural boundaries.

    Prefers paragraph breaks, falls back to sentence breaks, and only chops
    mid-sentence when a single sentence is itself larger than the ceiling (which
    happens with transcripts that carry no punctuation).
    """
    units = [u for u in _PARA_SPLIT.split(text) if u.strip()]
    if not units:
        return []

    # Re-split any paragraph that is on its own too large.
    refined: list[str] = []
    for unit in units:
        if word_count(unit) <= hard_max:
            refined.append(unit)
            continue
        for sent in _SENT_SPLIT.split(unit):
            if not sent.strip():
                continue
            if word_count(sent) <= hard_max:
                refined.append(sent)
                continue
            words = sent.split()
            for i in range(0, len(words), target):
                refined.append(" ".join(words[i:i + target]))

    out: list[str] = []
    buf: list[str] = []
    buf_words = 0
    for unit in refined:
        n = word_count(unit)
        if buf and buf_words + n > target:
            out.append("\n\n".join(buf))
            buf, buf_words = [], 0
        buf.append(unit)
        buf_words += n
    if buf:
        out.append("\n\n".join(buf))
    return out


@dataclass
class Passage:
    """One retrieval unit: text of roughly PASSAGE_TARGET_WORDS, plus provenance."""
    seq: int            # passage ordinal within the document
    role: str           # role of the first message it covers
    text: str
    ts_utc: str = ""
    first_msg: int = 0  # seq of the first message contributing to this passage
    last_msg: int = 0   # seq of the last


def passages(
    messages: Iterable,
    target: int = PASSAGE_TARGET_WORDS,
    hard_max: int = PASSAGE_MAX_WORDS,
) -> list[Passage]:
    """Normalise a document's messages into evenly sized retrieval passages.

    Accepts anything with ``.seq``, ``.role``, ``.text`` and ``.ts_utc``.

    Short adjacent messages are merged and long ones are split, so a meeting note
    and a chat transcript end up as comparable rows. Passages never span
    documents, and no text is dropped or duplicated: concatenating the passages
    in order reproduces the document. Message order is preserved, so the verbatim
    record stays reconstructable from the separate ``messages`` table.
    """
    out: list[Passage] = []
    buf: list[str] = []
    buf_words = 0
    buf_role = ""
    buf_ts = ""
    buf_first = 0
    buf_last = 0

    def flush() -> None:
        nonlocal buf, buf_words, buf_role, buf_ts, buf_first, buf_last
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            out.append(Passage(seq=len(out), role=buf_role, text=text, ts_utc=buf_ts,
                               first_msg=buf_first, last_msg=buf_last))
        buf, buf_words = [], 0
        buf_role, buf_ts = "", ""

    for m in messages:
        text = (m.text or "").strip()
        if not text:
            continue
        n = word_count(text)

        if n > hard_max:
            # A single oversized message: close whatever is buffered, then emit
            # this message as its own run of passages so a 7,000-word meeting
            # becomes many comparable rows instead of one unrankable slab.
            flush()
            for piece in _split_long(text, target, hard_max):
                out.append(Passage(seq=len(out), role=m.role, text=piece,
                                   ts_utc=m.ts_utc, first_msg=m.seq, last_msg=m.seq))
            continue

        if buf and buf_words + n > target:
            flush()
        if not buf:
            buf_role, buf_ts, buf_first = m.role, m.ts_utc, m.seq
        buf.append(text)
        buf_words += n
        buf_last = m.seq

    flush()
    return out


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


# Words that carry no retrieval signal but were being treated as *required* terms.
# "what did jane say" became '"what" "did" "jane" "say"', which under FTS5's
# implicit AND demanded all four words appear in the same message. Source material
# rarely contains "what"/"did"/"say" together — but a transcript of the user asking
# the question always does, so questions matched their own asking and nothing else.
_STOPWORDS = frozenset("""
a about after all also am an and any are as at be because been before being but by
can could did do does doing done for from further had has have having he her here
hers him his how i if in into is it its just me more most my no nor not of off on
once only or other our ours out over own same she should so some such than that the
their theirs them then there these they this those through to too under until up
very was we were what when where which while who whom why will with would you your
yours
""".split())

_QUOTED = re.compile(r'^"(.*?)"\*?$')


def _bare(token: str) -> str:
    """The word inside a quoted (possibly prefix-starred) FTS token."""
    m = _QUOTED.match(token)
    return (m.group(1) if m else token).lower()


def to_fts_query(raw: str, op: str = "AND", *, drop_stopwords: Optional[bool] = None) -> str:
    """Turn a natural-language query into a safe FTS5 MATCH expression.

    op='AND' -> every term must appear (implicit AND, tokens space-joined).
    op='OR'  -> any term may appear (ranked by bm25 so full matches rise).
    Never raises a MATCH syntax error; use --raw for raw boolean operators.

    Stop words are dropped from the AND form by default, since requiring them is
    what made a phrased question fail to retrieve the material that answers it.
    They are kept for OR, where an extra term only adds candidates and cannot
    exclude anything. If a query is *entirely* stop words ("what did they say")
    the filter is skipped rather than producing an empty match.
    """
    tokens = fts_tokens(raw)
    if drop_stopwords is None:
        drop_stopwords = op.upper() != "OR"
    if drop_stopwords:
        kept = [t for t in tokens if _bare(t) not in _STOPWORDS]
        if kept:                      # never let filtering empty the query
            tokens = kept
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
