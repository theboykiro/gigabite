"""Tests for the core setup coverage pass (`features.core_coverage`).

Pure stdlib (unittest). No network, no model; the index is a temp SQLite file.

The three tests that matter are the three failure modes the design names, because
each of them produces a plausible-looking wrong answer rather than an error:

  1. An index of assistant turns must not evidence anything. The corpus is mostly
     assistant output, so a pass that skips the role filter learns the
     assistant's voice and proposes a protocol telling it to sound like itself.
  2. Meeting transcripts must not evidence anything — they record how someone
     talks to people, not how they want software to behave.
  3. A stated slot must not be evidenced by register alone. Only a correction is
     direct evidence about desired assistant behaviour.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import os
import sys
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, util  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402
from gigabite.features import core_coverage  # noqa: E402


# A local stand-in for `core_slots.Slot`, passed through the `slots=` seam. It is
# deliberately not a second copy of the registry — just the attribute surface the
# coverage pass reads, so these tests pin behaviour rather than slot content.
@dataclass(frozen=True)
class FakeSlot:
    id: str
    kind: str
    evidence_terms: tuple = ()
    section: int = 1
    title: str = ""
    question: str = ""
    shipped_text: str = ""


# The ids are real registry ids on purpose: a correction is attributed to a slot
# by id (`core_coverage._DIRECTIVE_CORRECTIONS`), so a stand-in with an invented
# id could only ever be evidenced by register. `STATED` is the slot the fixture
# correction below — "stop asking me, just do it" — is actually about.
TONE = FakeSlot(id="tone.do", kind="revealed", evidence_terms=("blunt", "preamble"))
LENGTH = FakeSlot(id="tone.length", kind="revealed", evidence_terms=("summary",))
STATED = FakeSlot(id="decisions.momentum", kind="stated", evidence_terms=("summary",))
SHIPPED = FakeSlot(id="egress.confidential", kind="shipped")
SLOTS = (SHIPPED, TONE, LENGTH, STATED)


def state_of(coverages, slot_id):
    return next(c.state for c in coverages if c.slot_id == slot_id)


def evidence_of(coverages, slot_id):
    return next(c.evidence for c in coverages if c.slot_id == slot_id)


class _CoverageBase(unittest.TestCase):
    def setUp(self):
        self.db = _harness.SCRATCH / f"{self.id().split('.')[-1]}.db"
        if self.db.exists():
            self.db.unlink()
        self.st = Store(connect(self.db))

    def add(self, native_id, messages, *, source=config.SOURCE_CLAUDE_CODE,
            title="Session", created="2026-01-02T09:00:00+00:00"):
        """Index a document from (role, text) — or (role, text, origin) — tuples.

        Origin defaults to what the role implies: a user turn was typed, anything
        else has no provenance to state. Tests that care about replayed text pass
        it explicitly, so no fixture asserts provenance by accident.
        """
        rows = []
        for i, m in enumerate(messages):
            role, text = m[0], m[1]
            origin = m[2] if len(m) > 2 else (
                util.ORIGIN_TYPED if role in core_coverage.USER_ROLES
                else util.ORIGIN_NONE)
            rows.append(Message(seq=i, role=role, text=text, origin=origin))
        self.st.upsert_document(Document(
            source=source, native_id=native_id, title=title, project="widgets",
            created_utc=created, updated_utc=created, messages=rows))
        self.st.commit()

    def assess(self, **kw):
        return core_coverage.assess(self.st, slots=SLOTS, **kw)


class TestEmptyIndex(_CoverageBase):
    """A fresh install on a second machine: no history, no crash, no evidence."""

    def test_every_non_shipped_slot_is_empty_and_shipped_stays_shipped(self):
        cov = self.assess()
        self.assertEqual(state_of(cov, SHIPPED.id), "shipped")
        for slot in (TONE, LENGTH, STATED):
            self.assertEqual(state_of(cov, slot.id), "empty", slot.id)

    def test_shipped_slots_carry_no_evidence(self):
        self.assertEqual(evidence_of(self.assess(), SHIPPED.id), ())

    def test_summarise_reports_all_four_states(self):
        counts = core_coverage.summarise(self.assess())
        self.assertEqual(counts, {"shipped": 1, "evidenced": 0, "thin": 0, "empty": 3})


class TestEvidencedFromUserTurns(_CoverageBase):
    def setUp(self):
        super().setUp()
        self.add("s1", [
            ("user", "be blunt with me, I do not want the preamble"),
            ("assistant", "Understood, I will keep it direct."),
        ])
        self.add("s2", [
            ("user", "skip the summary at the end, it repeats what you said"),
            ("assistant", "Noted."),
        ], source=config.SOURCE_CLAUDE_AI)
        # A second, independent document: register is counted once per document,
        # so one session can never carry a slot on its own.
        self.add("s3", [
            ("user", "the preamble paragraph is the part I always delete"),
            ("assistant", "Understood."),
        ], source=config.SOURCE_CLAUDE_AI)

    def test_a_tone_slot_is_evidenced(self):
        self.assertEqual(state_of(self.assess(), TONE.id), "evidenced")

    def test_the_evidence_is_real_and_attributable(self):
        rows = evidence_of(self.assess(), TONE.id)
        self.assertTrue(rows)
        row = rows[0]
        self.assertIsInstance(row, core_coverage.Evidence)
        self.assertTrue(row.doc_id)
        self.assertEqual(row.source, config.SOURCE_CLAUDE_CODE)
        self.assertTrue(row.title)
        self.assertTrue(row.created_utc)
        self.assertTrue(row.why)

    def test_a_correction_quotes_the_users_own_words(self):
        """The user must confirm a quote of themselves, not an assertion."""
        rows = evidence_of(self.assess(), LENGTH.id)
        whys = [r.why for r in rows]
        self.assertTrue(any(w.startswith("correction —") for w in whys), whys)
        self.assertTrue(any("skip the summary" in w for w in whys), whys)

    def test_limit_per_slot_caps_the_bundle(self):
        for n in range(6):
            self.add(f"extra{n}", [("user", f"no preamble please, attempt {n}")])
        self.assertLessEqual(len(evidence_of(self.assess(limit_per_slot=2), TONE.id)), 2)

    def test_assessment_is_deterministic(self):
        first = self.assess()
        second = self.assess()
        self.assertEqual(
            [(c.slot_id, c.state, tuple(e.why for e in c.evidence)) for c in first],
            [(c.slot_id, c.state, tuple(e.why for e in c.evidence)) for c in second])


class TestRoleFilter(_CoverageBase):
    """Failure mode 1 — the corpus is mostly assistant output by volume."""

    def setUp(self):
        super().setUp()
        self.add("a1", [
            ("assistant", "I will be blunt and skip the preamble for you."),
            ("assistant", "Here is a summary: I will skip the summary next time."),
            ("assistant", "To be blunt, no preamble, and I will keep the summary short."),
        ])

    def test_assistant_only_index_evidences_nothing(self):
        cov = self.assess()
        for slot in (TONE, LENGTH, STATED):
            self.assertNotEqual(state_of(cov, slot.id), "evidenced", slot.id)
            self.assertEqual(state_of(cov, slot.id), "empty", slot.id)

    def test_a_user_turn_in_the_same_document_still_counts(self):
        """The filter is per turn, not per document — it must not over-reject."""
        self.add("a2", [("user", "be blunt, drop the preamble"),
                        ("assistant", "Sure.")])
        self.assertTrue(evidence_of(self.assess(), TONE.id))

    def test_replayed_text_under_the_user_role_is_not_the_users_voice(self):
        """Provenance, not the words: this text reads exactly like a user turn."""
        self.add("a3", [
            ("user", "be blunt with me and drop the preamble", util.ORIGIN_REPLAYED),
            ("user", "skip the summary at the end", util.ORIGIN_REPLAYED),
        ])
        cov = self.assess()
        self.assertEqual(state_of(cov, TONE.id), "empty")
        self.assertEqual(state_of(cov, LENGTH.id), "empty")

    def test_a_system_reminder_does_not_discard_a_typed_turn(self):
        """The false-negative half: the harness appends, the user still typed it."""
        self.add("a4", [
            ("user", "be blunt, drop the preamble\n"
                     "<system-reminder>Codebase instructions follow.</system-reminder>"),
        ])
        self.assertTrue(evidence_of(self.assess(), TONE.id))


class TestMeetingsAreNotPreference(_CoverageBase):
    """Failure mode 2 — speech to colleagues is not a claim about software."""

    def test_meeting_text_evidences_nothing(self):
        self.add("m1", [("transcript",
                         "I would be blunt about it, and skip the summary, "
                         "no preamble, just the answer")],
                 source=config.SOURCE_MEETING, title="Weekly sync")
        cov = self.assess()
        for slot in (TONE, LENGTH, STATED):
            self.assertEqual(state_of(cov, slot.id), "empty", slot.id)


class TestStatedSlotsNeedCorrections(_CoverageBase):
    """Failure mode 3 — self-description is not the operative preference."""

    def test_register_alone_does_not_evidence_a_stated_slot(self):
        self.add("s1", [("user", "the summary section is the part I read first")])
        self.add("s2", [("user", "put the summary in the summary document")])
        cov = self.assess()
        self.assertNotEqual(state_of(cov, STATED.id), "evidenced")

    def test_a_correction_does_evidence_a_stated_slot(self):
        self.add("s3", [("user", "stop asking me, just do it and skip the summary")])
        cov = self.assess()
        self.assertEqual(state_of(cov, STATED.id), "evidenced")
        self.assertIn("correction —", evidence_of(cov, STATED.id)[0].why)

    def test_a_single_register_hit_is_thin_not_evidenced(self):
        """One document agreeing is a voice, not a pattern."""
        self.add("s4", [("user", "the preamble paragraph goes above the table")])
        self.assertEqual(state_of(self.assess(), TONE.id), "thin")


class TestPastedProseIsNotTheUsersWords(_CoverageBase):
    """Failure mode 4 — a transcript pasted into a turn the user really typed.

    Provenance cannot help here: the message is genuinely the user's, so `role`,
    `origin` and `source` all say so. Length and position are the only signals
    that separate an instruction from a paste.
    """

    # A pasted meeting transcript: speaker-labelled lines, one of which is
    # correction-shaped. Padded past CORRECTION_MAX_CHARS, as the real ones are.
    PASTE = (
        "Notes from the vendor call, pasted below.\n\n"
        + "Alex: we walked through the migration plan and the rollout dates.\n"
          "Sam: the reporting pack is fine, nobody reads past page one anyway.\n"
          "Alex: agreed, and the schedule slips a week either way.\n" * 20
        + "Sam: honestly, stop asking me, just do it and skip the summary.\n"
        + "Alex: fine. I will write it up and send it round tomorrow.\n" * 20
    )

    def test_a_pasted_transcript_evidences_nothing(self):
        self.assertGreater(len(self.PASTE), core_coverage.CORRECTION_MAX_CHARS)
        self.add("p1", [("user", self.PASTE)])
        cov = self.assess()
        for slot in (LENGTH, STATED):
            self.assertNotEqual(state_of(cov, slot.id), "evidenced", slot.id)
        self.assertEqual(core_coverage._correction(self.PASTE), None)

    def test_a_correction_buried_deep_in_a_long_turn_is_not_quoted(self):
        """Under the length cap, but nowhere a person puts an instruction."""
        buried = ("x " * 400) + "stop asking me, just do it and skip the summary"
        self.assertLess(len(buried), core_coverage.CORRECTION_MAX_CHARS)
        self.assertGreater(len(buried), core_coverage.CORRECTION_HEAD_CHARS)
        self.assertIsNone(core_coverage._correction(buried))

    def test_a_normal_length_correction_still_evidences(self):
        self.add("p2", [("user", "stop asking me, just do it and skip the summary")])
        cov = self.assess()
        self.assertEqual(state_of(cov, STATED.id), "evidenced")
        self.assertIn("skip the summary", evidence_of(cov, STATED.id)[0].why)

    def test_a_long_turn_that_opens_with_a_correction_still_evidences(self):
        """The false-negative direction: long, but the correction is led with."""
        led = ("skip the summary and just do it. Here is the context you asked "
               "for, in full:\n" + ("detail on the rollout plan. " * 60))
        self.assertGreater(len(led), core_coverage.CORRECTION_HEAD_CHARS)
        self.add("p3", [("user", led)])
        self.assertEqual(state_of(self.assess(), STATED.id), "evidenced")


class TestCorrectionsAreNotGatedOnTerms(_CoverageBase):
    """The inversion: corrections are found corpus-wide, terms only attribute.

    The bug this pins is a recall ceiling rather than a wrong answer, which is
    why it survived two rounds of correctness fixes. When `evidence_terms` chose
    the candidate turns, a correction counted only if a configured phrase
    happened to appear in the same message — so the plainest corrections in the
    corpus, which are short and use none of the vocabulary, were never examined.
    """

    # No slot lists "apolog", "hedge" or "explain" among its terms, and neither
    # sentence contains "blunt", "preamble" or "summary".
    NO_TERM_MATCH = "stop apologising every time, it wastes a line"

    def test_a_correction_with_no_term_match_is_still_detected(self):
        found = core_coverage._correction(self.NO_TERM_MATCH)
        self.assertIsNotNone(found)
        self.assertEqual(found.weight, core_coverage.STRONG_CORRECTION)
        for slot in (TONE, LENGTH, STATED):
            self.assertFalse(
                any(t in self.NO_TERM_MATCH for t in slot.evidence_terms), slot.id)

    def test_a_correction_with_no_term_match_still_evidences_its_slot(self):
        cut = FakeSlot(id="tone.cut", kind="revealed", evidence_terms=("cut the",))
        repair = FakeSlot(id="tone.repair", kind="stated", evidence_terms=("wrong",))
        self.add("c1", [("user", self.NO_TERM_MATCH)])
        cov = core_coverage.assess(self.st, slots=(cut, repair))
        self.assertEqual(state_of(cov, "tone.cut"), "evidenced")
        self.assertEqual(state_of(cov, "tone.repair"), "evidenced")
        self.assertIn("apolog", evidence_of(cov, "tone.cut")[0].why)

    def test_attribution_lands_on_the_expected_slots_and_no_others(self):
        """A correction is evidence for what it is about, not for every slot."""
        cases = {
            "stop apologising, just say what broke":
                ("tone.cut", "tone.repair"),
            # Two families in one sentence attribute to the union of both.
            "stop apologising and just do it":
                ("tone.cut", "tone.repair", "decisions.momentum"),
            # ...and never to `autonomy.grid`: one cell is not a three-cell
            # answer, and an evidenced slot is a slot that is no longer asked.
            "just do it, no need to ask": ("decisions.momentum",),
            "push back when I'm wrong instead of agreeing":
                ("decisions.pushback",),
            "stop hedging, say how confident you are":
                ("tone.cut", "info.uncertainty"),
            "too long — keep it short":
                ("tone.length",),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                found = core_coverage._correction(text)
                self.assertIsNotNone(found, text)
                self.assertEqual(sorted(found.slots), sorted(expected))

    def test_a_bare_rejection_still_needs_a_term_to_land(self):
        """"No, wrong" says an answer missed, not which habit to change."""
        found = core_coverage._correction("no, that is wrong")
        self.assertIsNotNone(found)
        self.assertEqual(found.slots, ())
        self.add("r1", [("user", "no, that is wrong")])
        self.add("r2", [("user", "no, wrong — the summary is not what I asked for")])
        cov = self.assess()
        # r2 mentions the length slot's term; r1 attributes to nothing at all.
        self.assertTrue(any("rejected the previous answer" in e.why
                            for e in evidence_of(cov, LENGTH.id)))
        self.assertEqual(len(evidence_of(cov, LENGTH.id)), 1)

    def test_an_unattributed_correction_alone_leaves_every_slot_unevidenced(self):
        self.add("r1", [("user", "no, that is wrong")])
        cov = self.assess()
        for slot in (TONE, LENGTH, STATED):
            self.assertEqual(state_of(cov, slot.id), "empty", slot.id)


class TestReadOnly(_CoverageBase):
    """`record=True` would stamp access times and un-archive documents."""

    def _rows(self):
        return {r["doc_id"]: (r["accessed_utc"], r["active"])
                for r in self.st.conn.execute(
                    "SELECT doc_id, accessed_utc, active FROM documents")}

    def test_assessing_does_not_touch_access_times_or_active_flags(self):
        self.add("r1", [("user", "be blunt, no preamble, skip the summary")])
        before = self._rows()
        self.assess()
        self.assertEqual(self._rows(), before)

    def test_an_archived_document_stays_archived(self):
        self.add("r2", [("user", "be blunt, no preamble")])
        doc_id = next(iter(self._rows()))
        self.st.set_active(doc_id, False)
        self.assess()
        self.assertEqual(self._rows()[doc_id][1], 0)


class TestRegistryIntegration(_CoverageBase):
    """Runs against the real registry once step 1 lands; skipped until then."""

    def test_real_registry_assesses_without_error(self):
        try:
            from gigabite.features import core_slots
        except ImportError:
            self.skipTest("core_slots registry not present yet")
        cov = core_coverage.assess(self.st)
        self.assertEqual(len(cov), len(core_slots.SLOTS))
        counts = core_coverage.summarise(cov)
        self.assertEqual(sum(counts.values()), len(cov))
        for c in cov:
            slot = core_slots.get_slot(c.slot_id)
            expected = "shipped" if slot.kind == "shipped" else "empty"
            self.assertEqual(c.state, expected, c.slot_id)


if __name__ == "__main__":
    unittest.main()
