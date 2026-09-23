"""The harness's keychain guard, exercised rather than trusted.

No test may run the macOS `security` tool — a test that reads the keychain passes
or fails depending on what the person running it has stored, and can raise an
access prompt on their machine. `tests/_harness.py` makes any such call raise; this
file pins that it does, and that it leaves every other command alone.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: E402


class TestTheKeychainGuard(unittest.TestCase):

    def tearDown(self):
        # These attempts are the test's own, made on purpose; forgetting them keeps
        # the end-of-run check for accidental ones meaningful.
        del _harness._KEYCHAIN_ATTEMPTS[:]
        super().tearDown()

    def test_every_way_of_running_security_raises(self):
        for call in (
            lambda: subprocess.run(["security", "find-generic-password", "-s", "x", "-w"],
                                   capture_output=True),
            lambda: subprocess.call(["/usr/bin/security", "delete-generic-password"]),
            lambda: subprocess.check_output("security list-keychains", shell=True),
        ):
            with self.assertRaises(_harness.KeychainAccessInTest):
                call()

    def test_the_credential_reader_cannot_reach_the_keychain_unmocked(self):
        from gigabite.sources import granola_live
        with self.assertRaises(_harness.KeychainAccessInTest):
            granola_live.read_token()

    def test_other_commands_still_run(self):
        out = subprocess.run(["/bin/echo", "security"], capture_output=True, text=True)
        self.assertEqual("security\n", out.stdout)

    def test_home_is_a_temp_directory(self):
        self.assertTrue(str(Path.home()).startswith(str(_harness._SESSION_ROOT)))


if __name__ == "__main__":
    unittest.main()
