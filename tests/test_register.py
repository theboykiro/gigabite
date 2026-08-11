"""The register router: which of spar / brief / execute a turn is in.

Every case here is a fixed prompt with a stated expectation, so the classifier can
be retuned later by reading this file rather than by re-measuring by hand. The
near-boundary ones carry a comment saying which way they should fall and why.

The asymmetry that governs the whole ladder is asserted directly in
`TestAmbiguityFallsTowardBrief`: suppressing recall on a turn that needed it is a
wrong answer the user has to catch, while injecting on a turn that did not is
noise. So a prompt the resolver is unsure about must never come back `spar`.

These are plain `TestCase`s rather than `TempRoot`s: `resolve_register` reads no
files, and `test_it_decides_without_reading_anything` pins that it cannot start.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import unittest
from unittest import mock

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite.features import routing  # noqa: E402


def mode(prompt, **kw):
    return routing.resolve_register(prompt, **kw)["mode"]


class TestSparringTurns(unittest.TestCase):
    """Short volleys mid-conversation. The context is already on screen."""

    ACKNOWLEDGEMENTS = [
        "yes",
        "committed",
        "yeah that makes sense",
        "ok cool",
        "try again",
    ]

    def test_a_bare_acknowledgement_is_a_sparring_turn(self):
        for prompt in self.ACKNOWLEDGEMENTS:
            self.assertEqual(mode(prompt), routing.SPAR, prompt)

    def test_a_tiny_question_with_no_substance_is_a_sparring_turn(self):
        """'are you done?' has nothing to retrieve — the answer is in the last turn,
        not in the archive. Three words is the whole allowance for a question."""
        for prompt in ("why?", "are you done?", "sure?"):
            self.assertEqual(mode(prompt), routing.SPAR, prompt)

    def test_a_bare_noun_fragment_is_a_sparring_turn(self):
        """Boundary, and it falls to spar deliberately. 'onboarding, not pricing' is
        a three-word fragment of a live argument: it has no verb, names nothing, and
        asks nothing, so whatever it means is on screen already. A fragment this
        short is also a poor query — retrieving on it would inject the noise the
        register router exists to stop."""
        self.assertEqual(mode("onboarding, not pricing"), routing.SPAR)

    def test_an_empty_prompt_routes_nowhere(self):
        for prompt in ("", "   ", "\n"):
            self.assertEqual(mode(prompt), routing.SPAR, repr(prompt))

    def test_a_sparring_turn_is_never_the_ambiguous_fallthrough(self):
        """`spar` is only ever returned on a positive signal. If it starts coming
        back as the default, the asymmetry the whole ladder rests on has inverted."""
        for prompt in self.ACKNOWLEDGEMENTS:
            self.assertNotEqual(routing.resolve_register(prompt)["confidence"], "ambiguous")


class TestBriefs(unittest.TestCase):
    """Questions about past work or current state. Full recall, cited."""

    def test_a_question_about_a_past_decision_is_a_brief(self):
        for prompt in (
            "what did we decide about the pricing anchor?",
            "why did we drop the enterprise tier",
            "what was the conclusion of the launch retro",
            "where did we land on onboarding",
        ):
            self.assertEqual(mode(prompt), routing.BRIEF, prompt)

    def test_asking_for_something_already_known_is_a_brief(self):
        """These verbs read rather than write, so they want evidence, not tools."""
        for prompt in (
            "summarise the pricing thread",
            "remind me what the mid-tier argument was",
            "compare the two onboarding proposals",
            "list the open blockers",
        ):
            self.assertEqual(mode(prompt), routing.BRIEF, prompt)

    def test_a_question_naming_a_file_is_a_brief_not_an_execute(self):
        """Boundary: an artifact is named, but nothing is being asked for on it."""
        self.assertEqual(mode("what does docs/AUTONOMY.md say about the router?"),
                         routing.BRIEF)

    def test_an_interrogative_opener_beats_a_verb_later_in_the_sentence(self):
        """Boundary: 'build' and 'write' are action verbs, but 'what should I…' is a
        question about what to do, not an instruction to do it. Falls to brief —
        which still injects everything, so the cost of being wrong here is nil."""
        for prompt in ("what should I build next?",
                       "should I write the migration first?"):
            self.assertEqual(mode(prompt), routing.BRIEF, prompt)


class TestExecutes(unittest.TestCase):
    """An instruction plus a target. Full recall, and tools are on the table."""

    def test_an_imperative_leading_the_prompt_is_execute(self):
        for prompt in (
            "move the repo out of Desktop",
            "run the full suite in reverse order",
            "merge everything from this session",
            "push all local changes to main",
        ):
            self.assertEqual(mode(prompt), routing.EXECUTE, prompt)

    def test_an_imperative_behind_polite_preamble_is_still_execute(self):
        """Boundary: the verb is not the first word. Four words of lookahead is
        exactly enough for the preamble people actually type."""
        for prompt in (
            "please update the readme",
            "can you fix the flaky ingest test",
            "ok now rename the module",
        ):
            self.assertEqual(mode(prompt), routing.EXECUTE, prompt)

    def test_a_short_imperative_is_execute_rather_than_spar(self):
        """Boundary: 'move the repo' is three words and would clear the spar ceiling
        on length alone. An instruction is not a volley however short it is."""
        self.assertEqual(mode("move the repo"), routing.EXECUTE)

    def test_a_verb_deep_in_a_long_sentence_does_not_make_it_an_instruction(self):
        """Boundary: 'rewrite' appears, but as the object of a question about a
        plan. Falls to brief."""
        self.assertEqual(
            mode("i am still unsure about the plan to rewrite the passage splitter"),
            routing.BRIEF)


class TestNamedTargets(unittest.TestCase):
    """A file, a link or an @project means the user is pointing at something."""

    POINTERS = [
        "the numbers in report.xlsx",
        "gigabite/features/routing.py again",
        "https://example.invalid/spec looks off",
        "`resolve_register` again",
        "@acme the mid tier",
    ]

    def test_a_named_target_is_never_a_sparring_turn(self):
        for prompt in self.POINTERS:
            self.assertNotEqual(mode(prompt), routing.SPAR, prompt)

    def test_an_instruction_against_a_named_target_is_execute(self):
        self.assertEqual(mode("please patch gigabite/features/routing.py"),
                         routing.EXECUTE)

    def test_a_careful_sentence_is_not_mistaken_for_a_file_path(self):
        """'e.g.' and 'i.e.' match word-dot-word, which is why the artifact test is
        a closed list of suffixes. Without that, every considered sentence became a
        turn that named an artifact."""
        s = routing.resolve_register("it depends, e.g. on the tier")["signals"]
        self.assertFalse(s["names_target"])


class TestOptionalSignals(unittest.TestCase):
    """Latency and corrections sharpen the answer; neither may be required."""

    def test_a_missing_latency_never_changes_the_answer(self):
        battery = ["yes", "why?", "move the repo", "what did we decide about pricing",
                   "summarise the retro", "please update the readme"]
        for prompt in battery:
            self.assertEqual(mode(prompt), mode(prompt, seconds_since_last=5.0), prompt)

    def test_a_long_pause_vetoes_a_sparring_turn(self):
        """Same three words, an hour later: the user left and came back, so what is
        on screen is no longer the context."""
        self.assertEqual(mode("ok what now"), routing.SPAR)
        self.assertEqual(mode("ok what now", seconds_since_last=3600.0), routing.BRIEF)

    def test_latency_can_only_veto_spar_never_create_it(self):
        """A fast arrival must not turn a real request into a volley."""
        prompt = "what did we decide about the pricing anchor"
        self.assertEqual(mode(prompt, seconds_since_last=0.5), routing.BRIEF)

    def test_a_correction_lowers_the_ceiling_rather_than_raising_it(self):
        """The previous answer was wrong. That is the worst moment to suppress
        context, so a correction pushes away from spar — except when it is so short
        there is nothing to search, which is what 'no' is."""
        self.assertEqual(mode("no"), routing.SPAR)
        self.assertEqual(mode("no, the other one entirely"), routing.BRIEF)
        self.assertEqual(mode("the other one entirely",
                              previous_was_correction=True), routing.BRIEF)

    def test_the_same_five_words_spar_when_nothing_was_corrected(self):
        """The pair to the case above: without the correction flag these five words
        are an ordinary volley."""
        self.assertEqual(mode("the other one entirely"), routing.SPAR)


class TestAmbiguityFallsTowardBrief(unittest.TestCase):
    """The load-bearing asymmetry. Getting these wrong is the expensive failure."""

    NEAR_BOUNDARY = [
        "i think the second option reads better but i am not certain yet",
        "the anchor felt high to me when we first talked about the tiers",
        "how does any of this survive someone else installing it",
        "what should I build next?",
        "not sure where to paste this",
    ]

    def test_no_near_boundary_prompt_suppresses_recall(self):
        for prompt in self.NEAR_BOUNDARY:
            self.assertNotEqual(mode(prompt), routing.SPAR, prompt)

    def test_the_fallthrough_is_always_brief(self):
        for prompt in self.NEAR_BOUNDARY + ["yes", "move the repo", "summarise it"]:
            r = routing.resolve_register(prompt)
            if r["confidence"] == "ambiguous":
                self.assertEqual(r["mode"], routing.BRIEF, prompt)


class TestTheContractTheCallersRelyOn(unittest.TestCase):

    def test_it_decides_without_reading_anything(self):
        """Pure means no store, no config, no filesystem. The recall hook runs this
        on the critical path of every prompt; a stat call in here is latency the
        user pays for on every turn."""
        with mock.patch("builtins.open", side_effect=AssertionError("read a file")):
            self.assertEqual(mode("what did we decide about pricing"), routing.BRIEF)

    def test_every_answer_is_one_of_the_three_modes(self):
        prompts = ["", "yes", "why?", "move the repo", "summarise the retro",
                   "what did we decide", "https://example.invalid/x", "@acme hello",
                   "a" * 400, "?" * 20, "🙂", "--limit 3"]
        for prompt in prompts:
            self.assertIn(mode(prompt), routing.MODES, repr(prompt))

    def test_the_signals_survive_json(self):
        """`route --json` embeds this whole dict; a non-serialisable value in it
        would break the recall hook rather than this module."""
        import json
        r = routing.resolve_register("please update docs/README.md",
                                     seconds_since_last=12.5)
        self.assertEqual(json.loads(json.dumps(r))["mode"], routing.EXECUTE)

    def test_the_same_prompt_always_gets_the_same_answer(self):
        for prompt in ("yes", "what did we decide", "move the repo"):
            self.assertEqual(routing.resolve_register(prompt),
                             routing.resolve_register(prompt), prompt)


if __name__ == "__main__":
    unittest.main()
