"""Context detection + retrieval — the heart of the conversational interface.

Given whatever the user just typed, resolve which project/layer they're in
(ARCHITECTURE §3) and pull the most relevant prior context from the index, so a
turn can be answered *with* the user's own history loaded, not from a blank slate.

Resolution priority:
  1. explicit marker   @project  or  @project:layer      -> wins outright
  2. a binding the user set for this working directory (features/bindings.py)
  3. keyword match     against `keywords:` in each ~/Knowledge/{project}/_project.md
  4. ambiguous         -> no project, and recall retrieves nothing at all

A binding outranks a keyword. A binding is something the user deliberately said
about this folder; a keyword is a stray word in a sentence, and mentioning one
piece of work while inside another's directory must not pull the first one's
material in. Only the `@marker` beats it, because that is the same class of
signal — the user saying so — and more specific: it is scoped to this one turn.

A working directory only ever resolves a project through (3), an explicit binding.
Its *name* never does: see `resolve_context`.

This module reads the project metadata files directly (it does not depend on the
knowledge-write feature), so detection works the moment a _project.md exists.

**Two axes.** The above resolves *which project*. `resolve_register` resolves *what
kind of turn this is* — spar, brief or execute (docs/AUTONOMY.md §7) — which decides
how much recall is worth injecting. It is the same job on a second axis, which is
why it lives here rather than in a module of its own.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from .. import config, util
from . import bindings

# An explicit project marker: '@project' or '@project:layer'.
#
# The leading lookbehind is load-bearing. Without it the '@' in an email address
# matched, so 'jane.doe@contoso.com' in the signature of a forwarded mail read
# as '@contoso' and filed the document under that project — and 'jane@Janes-
# MacBook-Pro' in a shell prompt read as a marker too. A marker is something typed
# at a word boundary, never the middle of an address.
_MARKER = re.compile(r"(?<![\w.@-])@([A-Za-z0-9][\w-]*)(?::([A-Za-z0-9][\w-]*))?")
_FRONT = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _scan_projects() -> list[dict]:
    """Read ~/Knowledge/{project}/_project.md keyword sets. Cheap, no deps."""
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


def _keyword_match(prompt: str, projects: list[dict]) -> tuple[Optional[dict], int]:
    """Best project by whole-word keyword hits in *prompt*, and its score.

    Split out because the answer is needed in two places now: to resolve an
    unbound turn, and — in a bound one, where it no longer decides anything — to
    say in the reason which keyword was overruled.
    """
    low = (prompt or "").lower()
    best, best_score = None, 0
    for p in projects:
        score = sum(1 for kw in p["keywords"]
                    if kw and re.search(rf"\b{re.escape(kw)}\b", low))
        if score > best_score:
            best, best_score = p, score
    return best, best_score


def resolve_context(prompt: str, projects: Optional[list[dict]] = None, *,
                    accept_unknown_marker: bool = True,
                    cwd: Optional[str] = None) -> dict:
    """Return {project, layer, confidence, reason}. project/layer may be None.

    *accept_unknown_marker* controls what an ``@marker`` naming a project that does
    not exist yet means. When the prompt is something a person typed, it means
    "start that project", so it is accepted as written. When the prompt is a
    *document* being filed, it means nothing of the sort: a Claude Code transcript
    contains the shell prompt ``@Janes-MacBook-Pro``, and a chat about sunglasses
    contains the handle ``@leonardo``. Both were duly accepted and created project
    folders named after a laptop and an eyewear brand — a folder name is not a
    project, and inventing one from stray text is the failure this module exists to
    prevent. Callers filing content pass ``False``, and an unknown marker is then
    ignored in favour of keyword matching.

    *cwd* is an optional working directory, and it is off unless a caller passes
    one: a session inside a directory the user has *bound* is strong evidence of
    that project's scope, but a *document* being filed must never take a project
    from wherever the shell happened to be. When one is passed, only an explicit
    ``@marker`` outranks the binding it finds: a keyword elsewhere in the prompt
    does not, because a passing mention of other work is not a request to leave
    this folder's scope. The keyword is still computed, so the reason string can
    say it was overruled and name the ``@marker`` that would honour it.

    **A directory name is never a project.** ``cwd`` resolves only through an
    explicit binding. Matching the basename against the project registry looked
    harmless — it only ever matched a project that already existed — and it was
    not: ``clientB/docs/alpha`` is a folder about ``alpha``, not the ``alpha``
    project, and it scoped recall to ``alpha`` and injected ``alpha``'s material
    into a session in someone else's repo, with no binding anywhere. The same rule
    the router states for filing holds for retrieval: the user says which project a
    directory is, or nothing does.
    """
    prompt = prompt or ""
    projects = projects if projects is not None else _scan_projects()
    known = {p["name"].lower(): p for p in projects}

    # 1. explicit @marker
    for m in _MARKER.finditer(prompt):
        proj, layer = m.group(1), m.group(2)
        # match case-insensitively to a known project, else accept as-is
        real = known.get(proj.lower())
        if real is None and not accept_unknown_marker:
            continue
        name = real["name"] if real else proj
        return {"project": name, "layer": layer, "confidence": "explicit",
                "reason": f"explicit marker @{proj}" + (f":{layer}" if layer else "")}

    best, best_score = _keyword_match(prompt, projects)

    # 2. a binding the user set for this working directory. It outranks the
    #    directory's *name* because it is something they said, not something the
    #    filesystem happened to be called — and it outranks a keyword in the
    #    prompt for the same reason, at more length: the keyword is a word in a
    #    sentence, the binding is a statement about this folder.
    if cwd:
        bound, bound_project = bindings.lookup(cwd)
        if bound:
            real = known.get((bound_project or "").lower())
            if real is not None:
                # Name the keyword that was overruled, and how to override back.
                # The reason string is shown to the user and to the model, so a
                # keyword silently doing nothing is exactly what it must explain.
                note = ""
                if best is not None and best["name"] != real["name"]:
                    note = (f"; ignored {best_score} keyword(s) for {best['name']}"
                            f" — type @{best['name']} to search it instead")
                return {"project": real["name"], "layer": None, "confidence": "binding",
                        "reason": f"this directory is bound to the {real['name']} project"
                                  + note}
            if bound_project is None:
                # "not project work": resolved, and resolved to nothing. Ambiguous
                # by confidence, so recall stays scoped-to-nothing — but the ask
                # never fires here again, which is the point of recording it.
                return {"project": None, "layer": None, "confidence": "ambiguous",
                        "reason": "this directory is marked as not project work"}
            # Bound to a project that no longer has a _project.md. Scope to
            # nothing rather than to a folder that is not there — and *not* to
            # whatever the prompt's keywords suggest, which would reopen the leak
            # this precedence closed: a missing metadata file is not the user
            # saying this directory is somebody else's work now.
            return {"project": None, "layer": None, "confidence": "ambiguous",
                    "reason": f"this directory is bound to {bound_project}, "
                              "which no longer has a _project.md"}

    # 3. keyword match
    if best and best_score > 0:
        return {"project": best["name"], "layer": None, "confidence": "keyword",
                "reason": f"matched {best_score} keyword(s) for {best['name']}"}

    # 4. ambiguous. Note what is *not* here: the directory's name.
    return {"project": None, "layer": None, "confidence": "ambiguous",
            "reason": "no project marker, keyword or binding for this directory"}


# ---------------------------------------------------------------------------
# the register router — the second axis (docs/AUTONOMY.md §7)
# ---------------------------------------------------------------------------
#
# Which of three registers a turn is in decides how much prior context is worth
# injecting. It is decided from the text alone, with no model call: putting a
# round-trip in front of every prompt in order to save a round-trip is the one way
# to get this obviously wrong (ROADMAP item 3, "wrong if").
#
# The rule that governs every tuning choice below: **ambiguity resolves toward
# `brief`, never toward `spar`.** Suppressing recall on a turn that needed it is a
# wrong answer the user has to notice and correct; injecting on a turn that did not
# is 1.5 KB of noise. The two failures are not the same size, so the ladder is not
# symmetric.

SPAR = "spar"
BRIEF = "brief"
EXECUTE = "execute"
MODES = (SPAR, BRIEF, EXECUTE)

# Verbs that *change* something. A prompt led by one of these is an instruction,
# whatever else is in it. Read-shaped verbs are deliberately absent — they are in
# _RECALL_VERBS, because "summarise the pricing thread" wants evidence, not tools.
_ACTION_VERBS = frozenset("""
add apply archive build bump change clean commit convert copy create delete deploy
draft drop edit export file fix generate implement index ingest install make merge
migrate move open patch publish pull push refactor release remove rename replace
rerun reset restore rewrite run save schedule send set ship split start stop sync
tidy update upgrade wire write
""".split())

# Verbs that ask for something the system already knows. These force `brief`: the
# answer is evidence, and evidence needs recall.
_RECALL_VERBS = frozenset("""
analyse analyze check compare describe explain find list look recall recap remind
review search show summarise summarize tell
""".split())

# Words that mark a question about past work or current state. Any of these makes a
# turn a `brief` at any length — this is the signal that must never be missed, so it
# is checked over the whole prompt rather than just the opening.
_PAST_REFERENCE = frozenset("""
we our us already before earlier previously yesterday ago decided decision decisions
agreed concluded discussed said status
""".split())

# An opening word that makes the sentence a question rather than an instruction.
# Without this, "what should I build next?" reads as an order to build something.
_INTERROGATIVE_OPENERS = frozenset("""
am are can could did do does had has have is may might shall should was were will
would what when where which who whom whose why how
""".split())

# "can you", "could you", "would you" — an instruction wearing a question mark.
_POLITE_MODALS = ("can", "could", "would", "will")

# The prompt itself pushing back on the previous answer. Only counted at the start
# of the prompt: "no" in the middle of a sentence is not a correction.
_CORRECTION_OPENERS = frozenset("""
no nope not wrong incorrect actually nah
""".split())

# File extensions that make a token a named artifact. A closed list rather than
# "word dot word", because that pattern also matches "e.g." and "i.e." and would
# have read every careful sentence as pointing at a file.
_ARTIFACT_SUFFIXES = (
    ".md", ".txt", ".py", ".sh", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".sql", ".csv", ".html", ".css", ".go", ".rs", ".java", ".rb", ".pdf",
    ".png", ".jpg", ".jpeg", ".xlsx", ".docx", ".pptx", ".zip", ".log", ".db",
)

_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_PATHISH = re.compile(r"(?:^|\s)[~./]?[\w.-]*/[\w./-]+")
_BACKTICKED = re.compile(r"`[^`]+`")
_WORD = re.compile(r"[A-Za-z0-9][\w'-]*")

# How far into a prompt a verb still counts as leading it. Four words is enough to
# clear the polite preamble people actually type — "please", "can you", "could you
# please", "ok now" — and short enough that a verb in the object of a sentence
# ("the plan to rewrite the parser") does not read as an instruction.
IMPERATIVE_SCAN_WORDS = 4

# The longest a turn can be and still be a volley rather than a request.
#
# Measured against the author's own indexed Claude Code prompts (n≈940 after
# filtering the tool results that share role='user'): turns of six words or fewer
# are acknowledgements ("yes", "committed", "try again"), short imperatives ("move
# the repo") and one-line questions ("are you done?"). Substantive asks are an order
# of magnitude longer — the median typed turn is dozens of words. Six is therefore a
# ceiling with clear air above it, not a midpoint. To revise it, re-run that
# distribution over `messages` where role='user'; the tool-result noise is the only
# hard part.
SPAR_MAX_WORDS = 6

# Questions and corrections get a much lower ceiling, because both are shapes where
# missing context is expensive: a question is the turn most likely to be answerable
# only from history, and a correction means the previous answer was already wrong —
# the worst possible moment to starve the retry. Three words admits "why?", "are you
# done?" and "no" while sending "what should I build next?" to `brief`.
SPAR_MAX_WORDS_WHEN_ASKING = 3

# A pause this long means the user left and came back, so whatever is on screen is
# no longer the context. Latency is a **veto only**: when it is absent — which is
# every call today, since the hook sees one prompt and has no memory — nothing
# changes. An optional signal that could flip an answer by being missing would make
# the resolver's behaviour depend on its caller.
SPAR_MAX_SECONDS = 180.0


def _signals(prompt: str, correction: bool, seconds_since_last) -> dict:
    words = _WORD.findall(prompt)
    lowered = [w.lower() for w in words]
    head = lowered[:IMPERATIVE_SCAN_WORDS]
    # Filenames have to be tested against whitespace tokens, not word tokens:
    # `_WORD` splits "report.xlsx" into two words, which is right for counting and
    # useless for recognising a file. Trailing punctuation is stripped so a path at
    # the end of a sentence still reads as one.
    tokens = [t.strip("\"'()[]{}<>,;:!?").rstrip(".").lower() for t in prompt.split()]
    stripped = _BACKTICKED.sub(" ", _URL.sub(" ", prompt))
    return {
        "words": len(words),
        "question": "?" in prompt,
        "leading_action": bool(lowered) and lowered[0] in _ACTION_VERBS,
        "leading_recall": bool(lowered) and lowered[0] in _RECALL_VERBS,
        "action_verb": any(w in _ACTION_VERBS for w in head),
        "recall_verb": any(w in _RECALL_VERBS for w in head),
        "interrogative_opener": bool(lowered) and lowered[0] in _INTERROGATIVE_OPENERS,
        # "can you fix it" opens with an interrogative and is an instruction anyway.
        # A modal aimed at the second person is politeness, not a question.
        "polite_request": lowered[:2] in ([m, "you"] for m in _POLITE_MODALS),
        "past_reference": any(w in _PAST_REFERENCE for w in lowered),
        "names_target": bool(
            _URL.search(prompt) or _PATHISH.search(stripped) or _BACKTICKED.search(prompt)
            or any(t.endswith(_ARTIFACT_SUFFIXES) for t in tokens)
            or _MARKER.search(prompt)),
        "correction": bool(correction) or (bool(lowered) and lowered[0] in _CORRECTION_OPENERS),
        "seconds_since_last": seconds_since_last,
    }


def resolve_register(prompt: str, *, previous_was_correction: bool = False,
                     seconds_since_last: Optional[float] = None) -> dict:
    """Return {mode, confidence, reason, signals} for one prompt. Pure.

    No store, no network, no model, and nothing read from disk — the whole decision
    is the text plus two optional facts the caller may know: whether the previous
    turn was a correction, and how long ago it arrived. Both are optional because
    the recall hook knows neither; supplying them only ever sharpens the answer.

    The ladder, first match wins:

      1. nothing to route                       -> spar
      2. an action verb leads the prompt        -> execute
      3. a read verb leads the prompt           -> brief
      4. it refers to past work or state        -> brief
      5. a read verb in the opening             -> brief
      6. an action verb in the opening, and it
         is not a question ("can you" is not)   -> execute
      7. it names a file, link or project       -> execute if imperative, else brief
      8. short, and not arriving after a pause  -> spar
      9. anything else                          -> brief

    `confidence` is `explicit` when a definite signal fired (a verb, an artifact),
    `inferred` for the short-turn rule, and `ambiguous` for the fallthrough — which
    is always `brief`, by construction.
    """
    prompt = prompt or ""
    s = _signals(prompt, previous_was_correction, seconds_since_last)

    def out(mode, confidence, reason):
        return {"mode": mode, "confidence": confidence, "reason": reason, "signals": s}

    if not s["words"]:
        return out(SPAR, "explicit", "nothing to route")
    if s["leading_action"]:
        return out(EXECUTE, "explicit", "an imperative leads the prompt")
    if s["leading_recall"]:
        return out(BRIEF, "explicit", "it opens by asking for something already known")
    if s["past_reference"]:
        return out(BRIEF, "explicit", "it refers to past work or current state")
    if s["recall_verb"]:
        return out(BRIEF, "explicit", "it asks for something already known")
    if s["action_verb"] and (s["polite_request"] or not s["interrogative_opener"]):
        return out(EXECUTE, "explicit", "an imperative in the opening words")
    if s["names_target"]:
        # A named file, link or @project is never a volley — the user is pointing at
        # something. Which side of brief/execute it lands on depends on whether they
        # asked for work on it or asked about it.
        if s["action_verb"]:
            return out(EXECUTE, "explicit", "an imperative against a named target")
        return out(BRIEF, "explicit", "it names a file, link or project")

    ceiling = (SPAR_MAX_WORDS_WHEN_ASKING if (s["question"] or s["correction"])
               else SPAR_MAX_WORDS)
    slow = (seconds_since_last is not None and seconds_since_last > SPAR_MAX_SECONDS)
    if s["words"] <= ceiling and not slow:
        return out(SPAR, "inferred",
                   "%d words, nothing named, still in the exchange" % s["words"])

    return out(BRIEF, "ambiguous", "no clear signal; defaulting to full recall")


# The recall gate: which hits the hook injects (`inject: true` on a hit).
#
# It used to be the hook's `score < -1.0` on raw bm25. bm25's idf collapses on a
# small corpus, so on a new install an exact match scored about -5e-06 and nothing
# was ever injected, while the same document scored -30 in a 120-session index.
# No absolute score means the same thing on both, so the gate is built from two
# ratios instead:
#
#   coverage  share of the prompt's meaningful words (stop words dropped, each
#             weighted by idf — see Store.term_coverage) that the passage holds.
#             A hit that matched only "the", or only a word most of the index
#             contains, covers ~nothing and is never injected.
#   relative  a hit's score against the best hit's. Only applied when the project
#             was resolved by a keyword in the prompt: a binding or an @marker is
#             the user saying which project this is, so scope is already the
#             precision guard and the top hits that clear coverage go in.
INJECT_MIN_COVERAGE = 0.5
INJECT_MIN_RELATIVE = 0.5
_SCOPED_BY_USER = ("binding", "explicit")


def mark_injectable(store, prompt: str, hits: list, confidence: str) -> list:
    """Set ``inject`` on every hit (in place) and return *hits*. Never raises."""
    for h in hits:
        h["inject"] = False
    try:
        terms = util.meaningful_terms(prompt)
        cover = store.term_coverage(terms, [h.get("rowid") for h in hits])
    except Exception:
        return hits
    scores = [h["score"] for h in hits if isinstance(h.get("score"), (int, float))]
    best = min(scores) if scores else 0.0
    for h in hits:
        if cover.get(h.get("rowid"), 0.0) < INJECT_MIN_COVERAGE:
            continue
        if confidence not in _SCOPED_BY_USER:
            score = h.get("score")
            if not isinstance(score, (int, float)) or best >= 0 \
                    or score / best < INJECT_MIN_RELATIVE:
                continue
        h["inject"] = True
    return hits


def route(store, prompt: str, *, limit: int = 6,
          cwd: Optional[str] = None,
          previous_was_correction: bool = False,
          seconds_since_last: Optional[float] = None,
          session_id: Optional[str] = None) -> dict:
    """Resolve context and retrieve the most relevant prior conversations.

    **Scoped means scoped.** Recall is injected into every prompt on the machine,
    so a hit from another project is not a stale suggestion the reader can ignore
    — it arrives as the user's own memory, in a session about someone else's work.
    On a laptop that holds more than one client that is a confidentiality failure,
    which is why there is no top-up: when a project resolves, only that project is
    searched, and a thin result is the honest answer that the project has little
    to say. Padding it from elsewhere is never the better trade.

    **No project means no hits, but not silence.** A turn that resolves to nothing
    returns an ``ask``: the block the hook injects to ask the user, once, which
    project this directory is. Without it the tool is indistinguishable from one
    with no memory — most sharply on a fresh install, which has no projects at all
    and would otherwise say nothing from the first prompt to the last.

    For the same reason, **no project means no hits**. An ambiguous turn used to
    fall back to searching everything; searching everything is exactly the query
    that cannot be safe, because it is the one with no scope at all. Silence costs
    a turn of context. A confident injection from the wrong client cannot be undone.

    The working directory is consulted only through a binding the user made (see
    ``resolve_context``), defaulting to the process's own — the hook runs inside
    the session's directory. Pass ``cwd=""`` to resolve from the prompt text alone.

    An ``@marker`` naming a project that does not exist is ignored here, unlike when
    a person types one to start a project: recall can only search projects that have
    content, so a marker like the ``@Janes-MacBook-Pro`` in a pasted shell prompt
    scoped the search to nothing and then reported that laptop back as the detected
    context. Falling through to keyword matching gives the real project instead.

    A `spar` turn returns no hits and never touches the index. Skipping the query
    rather than discarding its results afterwards is what buys anything: `search`
    is ~1-4ms of a ~220ms hook, so this is not a latency win — the subprocess spawn
    is the cost and we are already inside it. What it does buy is the ~1.5KB of
    recall never injected into a volley, and no `search(record=True)` write.

    **The project axis can rescue a turn from `spar`.** "pricing anchor" is four
    words with no verb, which by text alone is a volley — but if those words are the
    keywords of a real project, they are a topic and not banter, and that is exactly
    the turn where prior context pays. `resolve_register` cannot see that, because
    it is pure and the project registry is on disk; here the answer is already in
    hand, so it costs nothing to use. It only ever moves `spar` up to `brief`, never
    the other way.

    Every hit carries ``inject``: whether the recall hook should put it in the
    turn (``mark_injectable``). ``hits`` itself is unfiltered, so a caller that
    wants everything the search found still gets it.
    """
    register = resolve_register(prompt,
                                previous_was_correction=previous_was_correction,
                                seconds_since_last=seconds_since_last)
    here = os.getcwd() if cwd is None else cwd
    projects = _scan_projects()
    ctx = resolve_context(prompt, projects, accept_unknown_marker=False, cwd=here)
    if register["mode"] == SPAR and ctx["confidence"] != "ambiguous":
        register = dict(register, mode=BRIEF, confidence="explicit",
                        reason="short, but it names a known project (%s)" % ctx["reason"])
    if register["mode"] == SPAR:
        return {"context": ctx, "hits": [], "register": register, "ask": None}

    project = ctx["project"] if ctx["confidence"] != "ambiguous" else None
    if project is None:
        # Nothing to recall — say so rather than vanish. `ask` is the one-time
        # question that turns this directory into a resolved one for good; it is
        # None once the user has answered it, and in any directory where the
        # question would be meaningless (features/bindings.py).
        return {"context": ctx, "hits": [], "register": register,
                "ask": bindings.ask_for(here, projects, session_id)}

    hits = mark_injectable(store, prompt,
                           store.search(prompt, project=project, limit=limit),
                           ctx["confidence"])
    return {"context": ctx, "hits": hits, "register": register, "ask": None}
