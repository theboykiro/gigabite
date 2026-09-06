"""Live daily pull of Granola meetings via the official public API.

Granola Business unlocks `https://public-api.granola.ai/v1` — a real, documented
API, unlike claude.ai's undocumented endpoints (see `claude_ai_live.py`, which
this module mirrors). The API key is created by you in the Granola desktop app
(Settings -> Connectors -> API keys) and stored by YOU in the macOS keychain via
`gigabite granola-login`; this module only ever reads your own keychain at
runtime and never logs or prints the key.

Endpoints used (base https://public-api.granola.ai/v1), auth via
`Authorization: Bearer grn_<key>`:
    GET /notes                        paginated (cursor / hasMore), filterable
                                       by `created_after`
    GET /notes/{id}?include=transcript full note: transcript + AI summary

A note without a completed AI summary 404s on the detail endpoint — treated as
"not finished processing yet" and skipped, not an error. That is expected at
the 7pm daily cadence for same-day meetings.

Parsing and indexing are NOT duplicated here: each fetched note is written as
raw JSON into `config.SOURCES_MEETINGS` (kept deliberately — see the comment on
`config.SOURCES_DIR` about rebuilding the index from scratch) and handed to the
existing `sources.meetings.ingest`, which already knows this document shape via
`document_from_granola_json`. Project routing is likewise not duplicated: a
note lands with `project=""` and is resolved later by
`features.materialize.run`, against gigabite's own keyword routing — never
whatever folder/workspace Granola itself assigned.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .. import config
from ..store import Store
from . import IngestReport, meetings

API_BASE = "https://public-api.granola.ai/v1"
KEYCHAIN_SERVICE = "gigabite:granola"

# The signature key `ingest` tracks the last successful pull under (there is no
# per-note sync_state here — that bookkeeping already lives in
# `meetings.ingest`'s per-file signatures).
_SYNC_KEY = "granola_api"


# ---------------------------------------------------------------------------
# token (user-managed, never handled in chat)
# ---------------------------------------------------------------------------

def keychain_account() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return "gigabite"


def read_token() -> Optional[str]:
    """Read the Granola API key from the user's keychain. None if absent."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", keychain_account(), "-w"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    tok = (r.stdout or "").strip()
    return tok or None


def delete_token() -> None:
    """Remove any stored key (ignore if absent)."""
    subprocess.run(
        ["security", "delete-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", keychain_account()],
        capture_output=True,
    )


def store_token_interactive() -> int:
    """Run the macOS secure prompt so the user enters the key themselves.

    Clears any prior item first (so re-running always works), then runs
    `security` with `-w` LAST so it drops straight into its own hidden prompt:
        password data for new item:
        retype password for new item:
    The value never appears on the command line, in shell history, or in this
    process — `security` reads it directly.
    """
    delete_token()
    return subprocess.call([
        "security", "add-generic-password",
        "-s", KEYCHAIN_SERVICE, "-a", keychain_account(),
        "-D", "gigabite Granola API key",
        "-w",
    ])


# ---------------------------------------------------------------------------
# HTTP (stdlib only)
# ---------------------------------------------------------------------------

class GranolaError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _get(path: str, token: str) -> object:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read()[:400].decode("utf-8", errors="replace")
        except Exception:
            pass
        snippet = " ".join(body.split())[:180]
        raise GranolaError(
            f"granola HTTP {e.code} for {url.split('?')[0]}: {snippet!r}",
            status=e.code,
        ) from None
    except urllib.error.URLError as e:
        raise GranolaError(f"network error reaching granola: {e.reason}") from None


def _items(data: object) -> list:
    if isinstance(data, list):
        return [n for n in data if isinstance(n, dict)]
    if isinstance(data, dict):
        for key in ("notes", "data", "results", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return [n for n in v if isinstance(n, dict)]
    return []


def list_notes(token: str, created_after: Optional[str] = None) -> list:
    """Every note visible to this key, paginated via `cursor` / `hasMore`."""
    out: list = []
    cursor: Optional[str] = None
    while True:
        params = {}
        if created_after:
            params["created_after"] = created_after
        if cursor:
            params["cursor"] = cursor
        qs = urllib.parse.urlencode(params)
        path = "/notes" + (f"?{qs}" if qs else "")
        data = _get(path, token)
        out.extend(_items(data))
        has_more = isinstance(data, dict) and bool(data.get("hasMore"))
        cursor = (data.get("cursor") or data.get("next_cursor")) if isinstance(data, dict) else None
        if not has_more or not cursor:
            break
    return out


def get_note(note_id: str, token: str) -> Optional[dict]:
    """The full note (transcript + summary), or None if not finished processing.

    A note whose AI summary has not completed 404s on the detail endpoint — that
    is normal at the daily cadence, not a failure, so it is reported as None
    rather than raised.
    """
    try:
        data = _get(f"/notes/{note_id}?include=transcript", token)
    except GranolaError as e:
        if e.status == 404:
            return None
        raise
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def ingest(store: Store, token: Optional[str] = None, force: bool = False,
           progress=None) -> IngestReport:
    report = IngestReport(source=config.SOURCE_MEETING)
    token = token or read_token()
    if not token:
        report.notes.append("no granola token in keychain — skipping live pull "
                            "(run `gigabite granola-login` to enable)")
        return report

    last_pull = None if force else store.get_signature(config.SOURCE_MEETING, _SYNC_KEY)
    try:
        notes = list_notes(token, created_after=last_pull)
    except GranolaError as e:
        report.errors.append(str(e))
        return report
    if not notes:
        report.notes.append("granola: no new notes since last pull")
        return report

    config.SOURCES_MEETINGS.mkdir(parents=True, exist_ok=True)
    # The watermark only ever moves past a note this pass actually captured.
    # A note whose summary isn't ready yet must stay eligible for
    # `created_after` on tomorrow's pull, or it would silently fall out of
    # range the moment the watermark passed its timestamp.
    newest = last_pull or ""
    for meta in notes:
        report.scanned += 1
        note_id = str(meta.get("id") or meta.get("note_id") or meta.get("uuid") or "")
        if not note_id:
            continue
        try:
            full = get_note(note_id, token)
        except GranolaError as e:
            report.errors.append(f"{note_id}: {e}")
            continue
        if full is None:
            report.notes.append(f"{note_id}: not finished processing yet — will retry next pull")
            continue
        (config.SOURCES_MEETINGS / f"{note_id}.json").write_text(
            json.dumps(full, ensure_ascii=False), encoding="utf-8")
        ts = str(meta.get("updated_at") or meta.get("created_at")
                 or full.get("updated_at") or full.get("created_at") or "")
        if ts > newest:
            newest = ts
        if progress:
            progress(report.scanned, len(notes))

    # Parsing/indexing is the existing meetings ingester's job — not duplicated here.
    sub = meetings.ingest(store, imports=config.SOURCES_MEETINGS, force=force)
    report.changed = sub.changed
    report.skipped += sub.skipped
    report.errors.extend(sub.errors)
    report.notes.extend(sub.notes)

    # Only move the watermark forward once the whole pass came back clean — a
    # failed note this run must still be offered again on the next pull.
    if not report.errors and newest and newest != (last_pull or ""):
        store.set_signature(config.SOURCE_MEETING, _SYNC_KEY, newest)
    store.commit()
    return report
