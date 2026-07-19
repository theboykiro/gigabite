"""EXPERIMENTAL live Granola connector.

Granola v6 stores everything encrypted at rest, keyed by the macOS keychain
item `Granola Safe Storage / Granola Key`. Reading that key triggers a one-time
macOS authorization prompt — so this can only run interactively, when *you*
click "Allow" (ideally "Always Allow"). It is never run during unattended
ingest.

Status: the exact at-rest encryption format is not published, so this tries the
known Electron/Chromium patterns and reports precisely what it finds. If it
can't decrypt, it prints a diagnostic and you fall back to the guaranteed
export path (see GRANOLA.md). Nothing here is claimed to be verified.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Optional

from .. import config
from ..store import Store
from . import IngestReport
from .granola import document_from_granola_json, _iter_json_documents

KEYCHAIN_SERVICE = "Granola Safe Storage"
KEYCHAIN_ACCOUNT = "Granola Key"
CACHE_FILE = config.GRANOLA_APP_DIR / "cache-v6.json.enc"


# ---------------------------------------------------------------------------
# keychain
# ---------------------------------------------------------------------------

def read_keychain_key(timeout: float = 30.0) -> Optional[bytes]:
    """Read the Granola safeStorage key. Prompts for authorization on macOS."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    return raw.encode("utf-8") if raw else None


# ---------------------------------------------------------------------------
# decryption attempts
# ---------------------------------------------------------------------------

def _openssl_cbc(key: bytes, iv: bytes, data: bytes, bits: int) -> Optional[bytes]:
    """AES-CBC decrypt via the system openssl CLI (no python crypto dep)."""
    try:
        r = subprocess.run(
            ["openssl", "enc", f"-aes-{bits}-cbc", "-d", "-nopad",
             "-K", key.hex(), "-iv", iv.hex()],
            input=data, capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    out = r.stdout
    if out and 1 <= out[-1] <= 16:  # strip PKCS#7 padding
        out = out[:-out[-1]]
    return out


def _try_safestorage(keychain_key: bytes, blob: bytes) -> Optional[bytes]:
    """Chromium/Electron os_crypt v10/v11 format: 'vXX' + AES-128-CBC."""
    if blob[:3] not in (b"v10", b"v11"):
        return None
    pwd = keychain_key
    dk = hashlib.pbkdf2_hmac("sha1", pwd, b"saltysalt", 1003, dklen=16)
    iv = b" " * 16
    return _openssl_cbc(dk, iv, blob[3:], bits=128)


def _try_gcm(keychain_key: bytes, blob: bytes) -> Optional[bytes]:
    """Raw-key AES-256-GCM: nonce(12) + ciphertext + tag(16). Needs `cryptography`."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return None
    for key in _candidate_keys(keychain_key):
        if len(key) not in (16, 24, 32):
            continue
        try:
            nonce, ct = blob[:12], blob[12:]
            return AESGCM(key).decrypt(nonce, ct, None)
        except Exception:
            continue
    return None


def _candidate_keys(keychain_key: bytes):
    """Plausible key materials derived from the keychain secret."""
    yield keychain_key
    import base64
    try:
        yield base64.b64decode(keychain_key)
    except Exception:
        pass
    yield hashlib.sha256(keychain_key).digest()


def decrypt_blob(keychain_key: bytes, blob: bytes) -> Optional[bytes]:
    for fn in (_try_safestorage, _try_gcm):
        out = fn(keychain_key, blob)
        if out and _looks_like_json(out):
            return out
    return None


def _looks_like_json(b: bytes) -> bool:
    s = b.lstrip()[:1]
    return s in (b"{", b"[")


# ---------------------------------------------------------------------------
# parsing the decrypted cache
# ---------------------------------------------------------------------------

def _documents_from_cache(payload: bytes):
    data = json.loads(payload.decode("utf-8", errors="replace"))
    # cache may be double-encoded: {"cache": "<json string>"}
    if isinstance(data, dict) and isinstance(data.get("cache"), str):
        data = json.loads(data["cache"])
    state = data.get("state", data) if isinstance(data, dict) else data
    docs = state.get("documents") if isinstance(state, dict) else None
    if isinstance(docs, dict):
        yield from docs.values()
    elif isinstance(docs, list):
        yield from docs
    else:
        yield from _iter_json_documents(data)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def connect(store: Store, *, diagnose_only: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_GRANOLA)

    if not CACHE_FILE.exists():
        report.errors.append(f"Granola cache not found at {CACHE_FILE}")
        return report

    report.notes.append("Requesting the Granola keychain key — approve the macOS prompt…")
    key = read_keychain_key()
    if not key:
        report.errors.append(
            "Could not read the keychain key (prompt denied, timed out, or not present). "
            "Use the export path instead — see GRANOLA.md."
        )
        return report
    report.notes.append(f"keychain key acquired ({len(key)} bytes)")

    blob = CACHE_FILE.read_bytes()
    report.notes.append(f"cache: {len(blob)} bytes, magic={blob[:4]!r}")

    payload = decrypt_blob(key, blob)
    if payload is None:
        report.errors.append(
            "Decryption did not yield JSON with the known formats "
            "(Electron safeStorage CBC / raw-key GCM). The at-rest format has "
            "likely changed. Falling back to the export path is recommended; "
            "the byte diagnostics above narrow down the format."
        )
        return report

    report.notes.append(f"decrypted {len(payload)} bytes of JSON")
    if diagnose_only:
        return report

    for obj in _documents_from_cache(payload):
        report.scanned += 1
        try:
            doc = document_from_granola_json(obj, ref=str(CACHE_FILE))
            if doc and store.upsert_document(doc):
                report.changed += 1
            else:
                report.skipped += 1
        except Exception as e:
            report.errors.append(str(e))
    store.commit()
    return report
