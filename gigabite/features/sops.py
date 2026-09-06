"""SOPs — modular standard operating procedures loaded by role-based subagents.

SOPs are versioned markdown files living beside the core protocol, in
``~/.core/capability/sops/``. Each carries YAML-ish frontmatter (``name``,
``role``, ``summary``) and a body that is an agent's operating instructions for
a given process (build → QA → review, research, PM decision, ...).

A spawned Claude Code subagent loads the SOP relevant to its role and follows
it. This module is the read side: enumerate what's installed and fetch one by
name. Nothing here writes — SOPs are seeded by the installer.

Frontmatter is parsed with ``util.parse_frontmatter`` — no YAML dependency.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from gigabite import config, util


def sops_dir() -> Path:
    """Where installed SOPs live: ``~/.core/capability/sops/``."""
    return config.CORE_DIR / "capability" / "sops"


def _canonical(name: str) -> str:
    """Normalise a SOP name for matching: drop path, ``.md`` and ``sop-`` prefix."""
    stem = Path(name).name
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    if stem.lower().startswith("sop-"):
        stem = stem[4:]
    return stem.strip().lower()


def _title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            # drop a leading "SOP:" label if the heading carries one
            return re.sub(r"^SOP:\s*", "", line[2:].strip(), flags=re.IGNORECASE)
        if line:
            break
    return fallback


def _sop_files() -> list[Path]:
    d = sops_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.name.lower() != "readme.md")


def list_sops() -> list[dict]:
    """Every installed SOP as ``{name, title, role, summary, path}``, name-sorted."""
    out: list[dict] = []
    for path in _sop_files():
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, body = util.parse_frontmatter(raw)
        name = meta.get("name") or _canonical(path.name)
        out.append({
            "name": name,
            "title": meta.get("title") or _title_from_body(body, name),
            "role": meta.get("role", ""),
            "summary": meta.get("summary", ""),
            "path": str(path),
        })
    return out


def load_sop(name: str) -> Optional[str]:
    """Full markdown of one SOP by name, or None. Matches with/without ``.md``/``sop-``."""
    target = _canonical(name)
    for path in _sop_files():
        candidates = {_canonical(path.name)}
        meta, _ = util.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if meta.get("name"):
            candidates.add(_canonical(meta["name"]))
        if target in candidates:
            return path.read_text(encoding="utf-8", errors="replace")
    return None
