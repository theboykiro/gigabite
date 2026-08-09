"""The one invariant in `ingest.py` that nothing else guards.

`ingest._INGESTERS` is a plain dict and its docstring says "'note' must stay last",
because the note ingester asks whether a document is already indexed in order to
decide whether a file is a *rendering* of one. Run it before the source that owns
that document and the rendering claims it, then the raw export overwrites it on the
same pass — the same content indexed twice.

The whole of `test_materialize.py` exists to prevent exactly that double-indexing,
and every one of those tests passes with the dict in the wrong order, because they
call the ingesters directly. One reordered line in a dict literal would reintroduce
the bug the largest file in this suite is written to catch. This is that guard.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import unittest

from gigabite import config, ingest


class TestIngestOrder(unittest.TestCase):
    def test_note_runs_last(self):
        self.assertEqual(list(ingest._INGESTERS)[-1], config.SOURCE_NOTE)

    def test_every_ingester_key_is_a_known_source(self):
        """A typo'd key would be skipped by `run`'s `fn is None: continue`.

        Only this direction, not the reverse: `ALL_SOURCES` is the set of document
        *labels* and is wider than the set of scanners. `calendar` is a label with
        no ingester of its own — meetings are written as files and picked up by the
        note scanner — so requiring a one-to-one match would assert something the
        design does not claim.
        """
        self.assertTrue(set(ingest._INGESTERS).issubset(set(config.ALL_SOURCES)))

    def test_an_unknown_source_is_skipped_rather_than_crashing(self):
        reports = ingest.run(store=None, sources=["not-a-source"])
        self.assertEqual(reports, {})


if __name__ == "__main__":
    unittest.main()
