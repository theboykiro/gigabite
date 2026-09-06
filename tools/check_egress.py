#!/usr/bin/env python3
"""Fail if anything in this repository names real client work.

This repository promises to carry no client names. It broke that promise once, in
the way worth designing against: an a client team called Willow reached a public
commit as invented-sounding test vocabulary, and no amount of searching for the
names anyone *remembered* would have found it.

The first version of this script tried to derive the list automatically, keeping
proper nouns from the knowledge base and discarding ordinary English. It could not
have caught Willow either, because "willow" IS ordinary English — so are Cedar,
Harbour and Meadow. Codenames are chosen from the dictionary. That is the point
of them, and it defeats every heuristic of that shape.

So the list is maintained by hand, and lives outside the repository where it cannot
itself leak:

    ~/.core/egress-deny.txt     one term per line, '#' for comments

`--suggest` reads the knowledge base and ranks the proper nouns in client material
that are not on the list yet, as a triage queue. It is an aid to maintaining the
list, never a substitute: only a person can say whether a word is a client's.

    python3 tools/check_egress.py             # gate: exit 1 on any hit
    python3 tools/check_egress.py --suggest   # propose terms for the denylist
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
KNOWLEDGE = Path(os.environ.get("GIGABITE_KNOWLEDGE_DIR", HOME / "Knowledge"))
CORE = Path(os.environ.get("GIGABITE_CORE_DIR", HOME / ".core"))
DENY_FILE = CORE / "egress-deny.txt"

SKIP_PROJECTS = {"gigabite"}          # the product's own notes are not a client's
TEXT_SUFFIXES = {".md", ".txt", ""}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".gz", ".zip", ".db", ".sqlite"}

# Deliberately fictional stand-ins used throughout the tests and docs.
PLACEHOLDERS = {"acme", "contoso", "northwind", "widget", "sprocket", "jane",
                "janedoe", "john", "doe", "example", "alice", "bob", "foo", "bar"}

TECH = set("""
python bash json yaml sqlite fts utf api cli repo github git url uuid regex macos
finder claude anthropic opus sonnet haiku granola markdown frontmatter pathlib
datetime unittest pytest homebrew xcode google microsoft apple figma jira confluence
slack notion android flutter copilot sitecore optimizely contentful outlook gmail
zoom chrome safari firefox linux windows docker node npm react swift kotlin java
readme changelog license contributing roadmap philosophy architecture synthesis
routing autonomy monday tuesday wednesday thursday friday saturday sunday january
february march april june july august september october november december
""".split())


def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except Exception:
        return ""


def denylist() -> set[str]:
    terms = set()
    for line in read(DENY_FILE).splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            terms.add(line)
    return terms


def repo_corpus() -> list[tuple[str, str]]:
    """(label, text) for every tracked file, plus every commit message."""
    out = []
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                             capture_output=True, text=True).stdout.split()
    for rel in tracked:
        p = REPO / rel
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        out.append((rel, read(p)))
    out.append(("<commit messages>", subprocess.run(
        ["git", "-C", str(REPO), "log", "--all", "--format=%H%n%s%n%b"],
        capture_output=True, text=True).stdout))
    return out


def suggest() -> int:
    """Proper nouns in client material that are not on the denylist yet.

    No dictionary filtering, deliberately: that is what hid Willow. Capitalisation
    mid-sentence is the only signal used, so the output is noisy by design and is
    meant to be read, not trusted.
    """
    known = denylist() | PLACEHOLDERS | TECH
    found: Counter = Counter()
    if not KNOWLEDGE.exists():
        print(f"! nothing at {KNOWLEDGE}")
        return 1
    for project in sorted(p for p in KNOWLEDGE.iterdir() if p.is_dir()):
        if project.name.startswith(".") or project.name in SKIP_PROJECTS:
            continue
        for path in project.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]{3,})\b",
                                 read(path), re.M):
                w = m.group(1).lower()
                if w not in known:
                    found[w] += 1

    corpus = " ".join(t for _, t in repo_corpus()).lower()
    print(f"Proper nouns in your client material, not yet on the denylist.")
    print(f"Add the real ones to {DENY_FILE}.\n")
    print(f"{'term':22}{'mentions':>9}   in repo?")
    for w, n in found.most_common(50):
        here = "  <-- IN THIS REPO" if re.search(rf"\b{re.escape(w)}\b", corpus) else ""
        print(f"{w:22}{n:>9}{here}")
    return 0


def main() -> int:
    if "--suggest" in sys.argv:
        return suggest()

    terms = denylist()
    if not terms:
        print(f"! no denylist at {DENY_FILE} — nothing to check.")
        print("  Create it with one client term per line: team codenames, product")
        print("  names, colleague names, client and employer names.")
        return 1

    hits: dict[str, list[str]] = {}
    for label, text in repo_corpus():
        low = text.lower()
        for term in terms:
            pattern = re.escape(term).replace(r"\ ", r"\s+")
            if re.search(rf"\b{pattern}\b", low):
                hits.setdefault(term, []).append(label)

    if not hits:
        print(f"OK  {len(terms)} denied term(s) checked, none present.")
        return 0

    print(f"FAIL  {len(hits)} denied term(s) appear in this repository:\n")
    for term, where in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        places = sorted(set(where))
        print(f"  {term:22} {', '.join(places[:5])}{' …' if len(places) > 5 else ''}")
    print("\nReplace each with a placeholder before pushing. Commit messages count:")
    print("history is public forever, and a force-push does not remove what a")
    print("pull-request ref still points at.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
