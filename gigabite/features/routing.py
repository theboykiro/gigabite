"""Context detection + retrieval — the heart of the conversational interface.

Given whatever the user just typed, resolve which project/layer they're in
(ARCHITECTURE §3) and pull the most relevant prior context from the index, so a
turn can be answered *with* the user's own history loaded, not from a blank slate.

Resolution priority:
  1. explicit marker   @project  or  @project:layer      -> wins outright
  2. keyword match     against `keywords:` in each ~/.knowledge/{project}/_project.md
  3. ambiguous         -> no project; search runs unscoped

This module reads the project metadata files directly (it does not depend on the
knowledge-write feature), so detection works the moment a _project.md exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .. import config

_MARKER = re.compile(r"@([A-Za-z0-9][\w-]*)(?::([A-Za-z0-9][\w-]*))?")
_FRONT = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _scan_projects() -> list[dict]:
    """Read ~/.knowledge/{project}/_project.md keyword sets. Cheap, no deps."""
    out: list[dict] = []
    root = config.KNOWLEDGE_DIR
    if not root.exists():
        return out
    for meta in root.glob("*/_project.md"):
        project = meta.parent.name
        keywords: list[str] = []
        layers: list[str] = []
        m = _FRONT.match(meta.read_text(encoding="utf-8", errors="replace"))
        if m:
            for line in m.group(1).splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip().lower()
                if k == "keywords":
                    keywords = [t.strip().lower() for t in v.split(",") if t.strip()]
                elif k == "layers":
                    layers = [t.strip().lower() for t in v.split(",") if t.strip()]
        out.append({"name": project, "keywords": keywords, "layers": layers})
    return out


def resolve_context(prompt: str, projects: Optional[list[dict]] = None) -> dict:
    """Return {project, layer, confidence, reason}. project/layer may be None."""
    prompt = prompt or ""
    projects = projects if projects is not None else _scan_projects()
    known = {p["name"].lower(): p for p in projects}

    # 1. explicit @marker
    for m in _MARKER.finditer(prompt):
        proj, layer = m.group(1), m.group(2)
        # match case-insensitively to a known project, else accept as-is
        real = known.get(proj.lower())
        name = real["name"] if real else proj
        return {"project": name, "layer": layer, "confidence": "explicit",
                "reason": f"explicit marker @{proj}" + (f":{layer}" if layer else "")}

    # 2. keyword match
    low = prompt.lower()
    best, best_score = None, 0
    for p in projects:
        score = sum(1 for kw in p["keywords"] if kw and re.search(rf"\b{re.escape(kw)}\b", low))
        if score > best_score:
            best, best_score = p, score
    if best and best_score > 0:
        return {"project": best["name"], "layer": None, "confidence": "keyword",
                "reason": f"matched {best_score} keyword(s) for {best['name']}"}

    # 3. ambiguous
    return {"project": None, "layer": None, "confidence": "ambiguous",
            "reason": "no project marker or keyword match; searching everything"}


def route(store, prompt: str, *, limit: int = 6, scope_to_project: bool = True) -> dict:
    """Resolve context and retrieve the most relevant prior conversations.

    When the project is confidently known we scope the search to it, then top up
    with unscoped hits so nothing relevant is hidden by a wrong guess.
    """
    ctx = resolve_context(prompt)
    project = ctx["project"] if (scope_to_project and ctx["confidence"] != "ambiguous") else None

    hits = store.search(prompt, project=project, limit=limit)
    if project and len(hits) < limit:
        seen = {h["doc_id"] for h in hits}
        for h in store.search(prompt, limit=limit):
            if h["doc_id"] not in seen:
                hits.append(h)
                if len(hits) >= limit:
                    break
    return {"context": ctx, "hits": hits}
