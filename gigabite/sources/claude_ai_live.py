"""Live pull of claude.ai web chats — projectless AND inside projects.

claude.ai has no official API for your web conversations, so this uses the same
internal endpoints the web app calls, authenticated by your own session cookie
(`sessionKey`). The token is stored by YOU in the macOS keychain (see
`gigabite claude-login`); this module only ever reads your own keychain at
runtime and never logs or prints the token.

Undocumented endpoints — they can change, and automated access is a grey area
under claude.ai's terms. It's your own data; you own that call. If the token is
absent the connector silently no-ops, so ordinary ingest is unaffected.

Endpoints used (base https://claude.ai/api):
    GET /organizations
    GET /organizations/{org}/projects
    GET /organizations/{org}/chat_conversations
    GET /organizations/{org}/chat_conversations/{uuid}?tree=True&rendering_mode=raw
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Optional

from .. import config
from ..store import Store
from . import IngestReport
from .claude_ai import conversation_to_document

API_BASE = "https://claude.ai/api"
KEYCHAIN_SERVICE = "gigabite:claude_ai"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


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
    """Read the claude.ai sessionKey from the user's keychain. None if absent."""
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
    """Remove any stored token (ignore if absent)."""
    subprocess.run(
        ["security", "delete-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", keychain_account()],
        capture_output=True,
    )


def store_token_interactive() -> int:
    """Run the macOS secure prompt so the user enters the token themselves.

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
        "-D", "gigabite claude.ai sessionKey",
        "-w",
    ])


# ---------------------------------------------------------------------------
# HTTP (stdlib only)
# ---------------------------------------------------------------------------

class ClaudeAiError(Exception):
    pass


def _get(path: str, token: str) -> object:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Cookie": f"sessionKey={token}",
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://claude.ai/",
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
        server = ""
        try:
            server = e.headers.get("server", "") or e.headers.get("Server", "")
        except Exception:
            pass
        cf = "cloudflare" in (server + body).lower() or "just a moment" in body.lower() \
            or "cf-ray" in body.lower() or "/cdn-cgi/" in body.lower()
        tag = " [cloudflare bot-block]" if cf else ""
        snippet = " ".join(body.split())[:180]
        if e.code in (401, 403):
            raise ClaudeAiError(
                f"claude.ai {e.code}{tag} on {url.split('?')[0]} — "
                f"server={server!r}; body: {snippet!r}"
            ) from None
        raise ClaudeAiError(f"claude.ai HTTP {e.code} for {url.split('?')[0]}: {snippet!r}") from None
    except urllib.error.URLError as e:
        raise ClaudeAiError(f"network error reaching claude.ai: {e.reason}") from None


def list_organizations(token: str) -> list:
    data = _get("/organizations", token)
    return [o for o in data if isinstance(o, dict) and o.get("uuid")] if isinstance(data, list) else []


def list_projects(org: str, token: str) -> dict:
    """uuid -> project name."""
    try:
        data = _get(f"/organizations/{org}/projects", token)
    except ClaudeAiError:
        return {}
    out = {}
    if isinstance(data, list):
        for p in data:
            if isinstance(p, dict) and p.get("uuid"):
                out[p["uuid"]] = p.get("name") or ""
    return out


def list_conversations(org: str, token: str) -> list:
    data = _get(f"/organizations/{org}/chat_conversations", token)
    return [c for c in data if isinstance(c, dict) and c.get("uuid")] if isinstance(data, list) else []


def get_conversation(org: str, uuid: str, token: str) -> dict:
    data = _get(
        f"/organizations/{org}/chat_conversations/{uuid}?tree=True&rendering_mode=raw",
        token,
    )
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def ingest(store: Store, token: Optional[str] = None, force: bool = False,
           progress=None) -> IngestReport:
    report = IngestReport(source=config.SOURCE_CLAUDE_AI)
    token = token or read_token()
    if not token:
        report.notes.append("no claude.ai token in keychain — skipping live pull "
                            "(run `gigabite claude-login` to enable)")
        return report

    try:
        orgs = list_organizations(token)
    except ClaudeAiError as e:
        report.errors.append(str(e))
        return report
    if not orgs:
        report.notes.append("claude.ai returned no organizations")
        return report

    for org in orgs:
        org_id = org["uuid"]
        projects = list_projects(org_id, token)
        try:
            convs = list_conversations(org_id, token)
        except ClaudeAiError as e:
            report.errors.append(str(e))
            continue

        for meta in convs:
            report.scanned += 1
            uuid = meta["uuid"]
            sig = str(meta.get("updated_at") or meta.get("created_at") or "")
            sync_key = f"{org_id}:{uuid}"
            if not force and sig and store.get_signature(config.SOURCE_CLAUDE_AI, sync_key) == sig:
                report.skipped += 1
                continue
            try:
                full = get_conversation(org_id, uuid, token)
                if not full.get("chat_messages") and not full.get("messages"):
                    # list metadata sometimes carries no body; fall back to meta
                    full = {**meta, **full}
                doc = conversation_to_document(full, ref="claude.ai (live)")
                if doc is None:
                    report.skipped += 1
                else:
                    proj_uuid = meta.get("project_uuid") or full.get("project_uuid")
                    if proj_uuid:
                        doc.project = projects.get(proj_uuid, "project")
                        doc.extra["project_uuid"] = proj_uuid
                    doc.extra["org_uuid"] = org_id
                    if store.upsert_document(doc):
                        report.changed += 1
                    else:
                        report.skipped += 1
                if sig:
                    store.set_signature(config.SOURCE_CLAUDE_AI, sync_key, sig)
                if progress:
                    progress(report.scanned, len(convs))
            except ClaudeAiError as e:
                report.errors.append(f"{uuid}: {e}")
            except Exception as e:
                report.errors.append(f"{uuid}: {e}")

    store.commit()
    return report
