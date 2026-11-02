"""claude.ai exports: picked up from Downloads by every ingest, and nudged when stale.

claude.ai chats can't be pulled, so making the export is the one manual step. The
ingest that follows takes the newest export out of Downloads, replaces the one it
had, and says when the last export is more than a week old.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import sys
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: E402  redirects every store into a temp dir

from gigabite import config, ingest  # noqa: E402
from gigabite.sources import claude_ai  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402


def _convs(uuid="c1", text="What is the widget budget?"):
    return [{
        "uuid": uuid, "name": "Widget budget",
        "created_at": "2026-05-01T00:00:00Z", "updated_at": "2026-05-01T00:10:00Z",
        "chat_messages": [
            {"sender": "human", "text": text},
            {"sender": "assistant", "text": "Locked at plan level."},
        ],
    }]


def _age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


class TestPickup(_harness.TempRoot):
    def setUp(self):
        super().setUp()
        self.box = config.SOURCES_CLAUDE_AI
        self.box.mkdir(parents=True, exist_ok=True)
        self.dl = config.DOWNLOADS_DIR
        self.store = Store(connect(self.db))

    def test_safari_export_in_downloads_is_moved_in_and_indexed(self):
        (self.dl / "conversations 2.json").write_text(json.dumps(_convs()))
        rep = claude_ai.ingest(self.store)
        self.assertEqual(rep.changed, 1)
        self.assertTrue(self.store.search("widget budget"))
        self.assertEqual([p.name for p in self.box.iterdir()], ["conversations.json"])
        self.assertEqual(list(self.dl.iterdir()), [])
        self.assertTrue(any("picked up" in n for n in rep.notes))

    def test_anthropic_zip_and_its_safari_unpacked_folder(self):
        zpath = self.dl / "data-2026-11-02.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("conversations.json", json.dumps(_convs()))
        self.assertEqual(claude_ai.pickup(self.box, self.dl), self.box / zpath.name)

        unpacked = self.dl / "data-2026-11-09"
        unpacked.mkdir()
        (unpacked / "conversations.json").write_text(json.dumps(_convs("c2")))
        (unpacked / "users.json").write_text("[]")
        _age(self.box / zpath.name, 1)
        self.assertEqual(claude_ai.pickup(self.box, self.dl), self.box / "conversations.json")
        self.assertEqual([p.name for p in unpacked.iterdir()], ["users.json"])

    def test_newer_export_replaces_the_old_one_and_keeps_one_backup(self):
        (self.box / "conversations.json").write_text(json.dumps(_convs("old")))
        _age(self.box / "conversations.json", 30)
        for i in (1, 2):
            (self.dl / "conversations.json").write_text(json.dumps(_convs(f"new{i}")))
            self.assertIsNotNone(claude_ai.pickup(self.box, self.dl))
            _age(self.box / "conversations.json", 10 - i)
        names = sorted(p.name for p in self.box.iterdir())
        self.assertEqual(len(names), 2)
        self.assertEqual(names[0], "conversations.json")
        self.assertTrue(names[1].startswith("conversations.json.replaced-"))
        current = json.loads((self.box / "conversations.json").read_text())
        self.assertEqual(current[0]["uuid"], "new2")

    def test_leaves_older_exports_and_other_tools_files_alone(self):
        (self.box / "conversations.json").write_text(json.dumps(_convs("have")))
        stale = self.dl / "conversations.json"
        stale.write_text(json.dumps(_convs("older")))
        _age(stale, 3)
        chatgpt = self.dl / "chatgpt.zip"
        with zipfile.ZipFile(chatgpt, "w") as zf:
            zf.writestr("conversations.json", json.dumps([{"id": "x", "mapping": {}}]))
        (self.dl / "conversations-notes.json").write_text("not json")
        self.assertIsNone(claude_ai.pickup(self.box, self.dl))
        self.assertTrue(stale.exists() and chatgpt.exists())

    def test_explicit_imports_never_touch_downloads(self):
        other = self.root.parent / "one-off"
        other.mkdir()
        (other / "conversations.json").write_text(json.dumps(_convs("x")))
        (self.dl / "conversations.json").write_text(json.dumps(_convs()))
        claude_ai.ingest(self.store, imports=other)
        self.assertTrue((self.dl / "conversations.json").exists())

    def test_full_ingest_run_picks_it_up(self):
        (self.dl / "conversations.json").write_text(json.dumps(_convs()))
        reports = ingest.run(self.store, sources=[config.SOURCE_CLAUDE_AI])
        self.assertEqual(reports[config.SOURCE_CLAUDE_AI].changed, 1)


class TestNudge(_harness.TempRoot):
    def setUp(self):
        super().setUp()
        self.box = config.SOURCES_CLAUDE_AI
        self.box.mkdir(parents=True, exist_ok=True)
        self.store = Store(connect(self.db))

    def _export(self, days):
        path = self.box / "conversations.json"
        path.write_text(json.dumps(_convs()))
        _age(path, days)

    def test_stale_export_is_reported_and_notified_once_a_day(self):
        self._export(20)
        config.NOTIFY = True
        with mock.patch.object(claude_ai.sys, "platform", "darwin"), \
                mock.patch.object(claude_ai.subprocess, "run") as run:
            rep = claude_ai.ingest(self.store)
            claude_ai.ingest(self.store)
        self.assertTrue(any("20 days old" in n for n in rep.notes))
        self.assertEqual(run.call_count, 1)
        self.assertIn("20 days ago", run.call_args[0][0][-1])

    def test_fresh_export_says_nothing(self):
        self._export(2)
        config.NOTIFY = True
        with mock.patch.object(claude_ai.subprocess, "run") as run:
            rep = claude_ai.ingest(self.store)
        self.assertFalse(any("days old" in n for n in rep.notes))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
