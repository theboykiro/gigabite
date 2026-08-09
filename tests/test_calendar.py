"""Calendar feature tests — project tagging, prep attachment, agenda filtering."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402
from gigabite.features import calendar as cal  # noqa: E402
from gigabite.features import save  # noqa: E402


def fresh_store(name) -> Store:
    """A Store on its own file. See _harness for where these land."""
    return _harness.scratch_store(name)


class TestCalendar(unittest.TestCase):
    def test_add_tags_project_and_agenda_attaches_prep(self):
        st = fresh_store("cal")
        # a project with a detection keyword + a prior note to become "prep"
        save.ensure_project("acme", keywords=["acme"])
        st.upsert_document(Document(
            source="note", native_id="n1", title="Acme pricing", project="acme",
            messages=[Message(0, "note", "acme enterprise pricing anchored to seats")]))

        # agenda(day="today") is asserted below, so the meetings must BE today —
        # a hardcoded date makes this test a time bomb. It must also be *UTC*
        # today, because that is the clock agenda() compares against: with a
        # local date this failed nightly between 00:00 and 03:00 in UTC+3, where
        # date.today() has already rolled over but UTC has not.
        today = datetime.now(timezone.utc).date().isoformat()
        ids = cal.add_meetings(st, [
            {"title": "Acme pricing sync", "start": f"{today}T15:00",
             "attendees": ["Alice", "Bob"], "location": "Zoom"},
            {"title": "Dentist", "start": f"{today}T09:00"},
        ])
        self.assertEqual(len(ids), 2)

        # the Acme meeting is auto-tagged to the acme project via keyword
        acme_docs = [d for d in st.iter_documents()
                     if d["source"] == config.SOURCE_CALENDAR and d["project"] == "acme"]
        self.assertEqual(len(acme_docs), 1)
        self.assertEqual(acme_docs[0]["title"], "Acme pricing sync")

        # agenda attaches prep (the prior acme note) to the acme meeting
        items = cal.agenda(st, day="today")
        titles = [it["meeting"]["title"] for it in items]
        self.assertIn("Acme pricing sync", titles)
        acme_item = next(it for it in items if it["meeting"]["title"] == "Acme pricing sync")
        self.assertTrue(acme_item["prep"])
        self.assertEqual(acme_item["prep"][0]["title"], "Acme pricing")
        # prep never includes calendar entries themselves
        self.assertTrue(all(h["source"] != config.SOURCE_CALENDAR for h in acme_item["prep"]))

    def test_agenda_day_filter(self):
        st = fresh_store("cal2")
        cal.add_meetings(st, [
            {"title": "Old standup", "start": "2020-01-01T09:00"},
            {"title": "Future review", "start": "2099-01-01T09:00"},
        ])
        today_titles = [it["meeting"]["title"] for it in cal.agenda(st, day="2099-01-01")]
        self.assertEqual(today_titles, ["Future review"])
        self.assertEqual(len(cal.agenda(st, day="all")), 2)

    def test_ignores_untitled(self):
        st = fresh_store("cal3")
        ids = cal.add_meetings(st, [{"start": "2026-07-20T10:00"}, {"title": "  "}])
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
