"""Tests for date normalisation (util.to_iso_utc / short_date / _infer_year).

Pure stdlib (unittest). No network, no filesystem, no clock dependence:
year-inference is tested by passing an explicit `today`, so these tests cannot
rot the way tests/test_calendar.py did when its hardcoded date rolled past.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import unittest
from datetime import date, datetime, timezone

from gigabite import util


class TestExplicitFormats(unittest.TestCase):
    def test_iso_passes_through_normalised(self):
        self.assertTrue(util.to_iso_utc("2026-07-29").startswith("2026-07-29T"))

    def test_iso_with_z_becomes_utc_offset(self):
        self.assertEqual(
            util.to_iso_utc("2026-07-29T10:00:00Z"),
            "2026-07-29T10:00:00+00:00",
        )

    def test_month_name_formats(self):
        for raw in ("29 July 2026", "Jul 29 2026", "Jul 29, 2026", "29 Jul 2026"):
            with self.subTest(raw=raw):
                self.assertTrue(util.to_iso_utc(raw).startswith("2026-07-29"), raw)

    def test_epoch_seconds_and_millis(self):
        self.assertTrue(util.to_iso_utc(1769644800).startswith("2026-01-29"))
        self.assertTrue(util.to_iso_utc(1769644800000).startswith("2026-01-29"))


class TestSlashDatesAreDayFirst(unittest.TestCase):
    """The bug: '%m/%d/%Y' was tried first, so 03/08 meant 8 March for days 1-12
    but 13+ fell through to day-first. Same format, different meaning by number."""
    def test_interpretation_does_not_depend_on_the_day_number(self):
        """Every day 1..12 must read day-first, exactly like 13..28 does."""
        for day in list(range(1, 13)) + [13, 20, 28]:
            with self.subTest(day=day):
                got = util.to_iso_utc(f"{day:02d}/08/2026")
                self.assertTrue(
                    got.startswith(f"2026-08-{day:02d}"),
                    f"{day:02d}/08/2026 -> {got}, expected August {day}",
                )

    def test_impossible_day_first_falls_back_to_month_first(self):
        # 08/13 is not a valid day-first date, so the US reading is the only one.
        self.assertTrue(util.to_iso_utc("08/13/2026").startswith("2026-08-13"))


class TestYearInference(unittest.TestCase):
    """'Jul 29' on a meeting note is the July that already happened."""

    def test_recent_past_takes_the_current_year(self):
        got = util._infer_year(datetime(1900, 7, 29), today=date(2026, 8, 4))
        self.assertEqual(got.year, 2026)

    def test_a_date_still_ahead_belongs_to_last_year(self):
        # On 4 Feb 2026, 'Dec 20' means December 2025, not ten months away.
        got = util._infer_year(datetime(1900, 12, 20), today=date(2026, 2, 4))
        self.assertEqual(got.year, 2025)

    def test_today_itself_is_the_current_year(self):
        got = util._infer_year(datetime(1900, 8, 4), today=date(2026, 8, 4))
        self.assertEqual((got.year, got.month, got.day), (2026, 8, 4))

    def test_leap_day_does_not_raise(self):
        got = util._infer_year(datetime(1904, 2, 29), today=date(2026, 3, 1))
        self.assertEqual((got.month, got.day), (2, 29))

    def test_year_less_input_parses_to_the_right_month_and_day(self):
        """Clock-independent: assert month/day and ISO shape, never the year."""
        for raw, (month, day) in (("Jul 29", (7, 29)), ("Aug 3", (8, 3)),
                                  ("3 Aug", (8, 3)), ("29 July", (7, 29))):
            with self.subTest(raw=raw):
                got = util.to_iso_utc(raw)
                parsed = datetime.fromisoformat(got)   # raises if not ISO
                self.assertEqual((parsed.month, parsed.day), (month, day))


class TestUnparseableIsPreservedNotMangled(unittest.TestCase):
    def test_empty_is_empty(self):
        for raw in (None, "", "   "):
            self.assertEqual(util.to_iso_utc(raw), "")

    def test_unparseable_string_is_returned_unchanged(self):
        self.assertEqual(util.to_iso_utc("sometime last week"), "sometime last week")

    def test_short_date_does_not_truncate_a_non_iso_value(self):
        """The old bug: 'Jul 29 2026'[:10] == 'Jul 29 202' in a filename."""
        self.assertEqual(util.short_date("sometime last week"), "sometime last week")

    def test_short_date_truncates_iso(self):
        self.assertEqual(util.short_date("2026-07-29T10:11:12+00:00"), "2026-07-29")
class TestRegressionTheFortyTwoNotes(unittest.TestCase):
    """The dates that were actually written into 42 misfiled meeting notes."""
