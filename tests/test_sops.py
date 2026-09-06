"""Tests for the SOP feature layer. Pure stdlib (unittest). No network, no home writes.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402
from gigabite.features import sops  # noqa: E402

# Repo scaffold SOPs — the source of truth the installer copies from.
_REPO = Path(__file__).resolve().parent.parent
_SCAFFOLD_SOPS = _REPO / "install" / "scaffold" / "sops"


def _install_scaffold_sops() -> Path:
    """Copy the repo's scaffold SOPs into the temp core/capability/sops dir.

    Asserts the source directory exists first: globbing a moved-away directory
    yields nothing silently, which used to surface as four confusing assertion
    failures downstream instead of "the path moved".
    """
    if not _SCAFFOLD_SOPS.is_dir():
        raise AssertionError(
            f"scaffold SOPs not found at {_SCAFFOLD_SOPS} — did the directory move? "
            f"Update _SCAFFOLD_SOPS in this file to match."
        )
    dest = sops.sops_dir()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    copied = [shutil.copy(src, dest / src.name) for src in _SCAFFOLD_SOPS.glob("*.md")]
    if not copied:
        raise AssertionError(f"no *.md SOPs in {_SCAFFOLD_SOPS}")
    return dest


class TestListSops(unittest.TestCase):
    def setUp(self):
        _install_scaffold_sops()

    def test_empty_when_dir_absent(self):
        shutil.rmtree(sops.sops_dir())
        self.assertEqual(sops.list_sops(), [])
    def test_title_derived_from_heading(self):
        by_name = {s["name"]: s for s in sops.list_sops()}
        # heading is "# SOP: Build → QA → User Review" -> label stripped
        self.assertEqual(by_name["build-qa-review"]["title"], "Build → QA → User Review")


class TestLoadSop(unittest.TestCase):
    def setUp(self):
        _install_scaffold_sops()

    def test_load_by_bare_name(self):
        text = sops.load_sop("build-qa-review")
        self.assertIsNotNone(text)
        self.assertIn("gigabite search", text)

    def test_load_matches_with_prefix_and_suffix(self):
        a = sops.load_sop("build-qa-review")
        self.assertEqual(a, sops.load_sop("sop-build-qa-review"))
        self.assertEqual(a, sops.load_sop("sop-build-qa-review.md"))
        self.assertEqual(a, sops.load_sop("build-qa-review.md"))

    def test_load_unknown_returns_none(self):
        self.assertIsNone(sops.load_sop("does-not-exist"))
