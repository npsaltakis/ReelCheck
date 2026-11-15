"""Τεστ για τις Ρυθμίσεις: προεπιλογές, τύποι, και ότι τα όρια χρησιμοποιούνται στους ελέγχους."""
import os
import tempfile
import unittest

from app.core import config, prefs


class PrefsTestCase(unittest.TestCase):
    """Κάθε τεστ με δικό του προσωρινό settings.json (ποτέ το πραγματικό του χρήστη)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.SETTINGS_FILE
        config.SETTINGS_FILE = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        config.SETTINGS_FILE = self.saved
        self.tmp.cleanup()


class TestPrefs(PrefsTestCase):
    def test_defaults_and_types(self):
        self.assertEqual(prefs.get("scan_silence_min"), 10.0)
        prefs.set("scan_silence_min", "4")          # π.χ. από παλιό αρχείο ως κείμενο
        self.assertEqual(prefs.get("scan_silence_min"), 4.0)
        prefs.set("sub_max_cps", "όχι αριθμός")
        self.assertEqual(prefs.get("sub_max_cps"), prefs.DEFAULTS["sub_max_cps"])
        prefs.set("update_auto", 0)
        self.assertIs(prefs.get("update_auto"), False)

    def test_subtitle_limits_follow_settings(self):
        from app.tools.subcheck import check_entries
        e = [(0.0, 2.0, [{"type": "text", "text": "x" * 30}])]           # 15 χαρακτήρες/δευτ.
        self.assertEqual(check_entries(e), [])
        prefs.set("sub_max_cps", 10)
        self.assertTrue(any("γρήγορος" in p[3] for p in check_entries(e)))

    def test_scan_thresholds_follow_settings(self):
        from app.checks.scan import scan_findings
        res = {"seconds": 3600, "black": [(1000, 1004)], "silence": [(2000, 2006)], "peak_db": [-1] * 6,
               "clip": [0] * 6, "dead": [], "audio_seconds": 3600,
               "lufs": {"integrated": -14.0, "lra": 5.0, "true_peak": -1.0}}
        warns = lambda: [t for lv, t in scan_findings(res) if lv == "warn"]
        self.assertEqual(warns(), [])                   # 4 δευτ. μαύρο, 6 σιωπή: κάτω από τα όρια
        prefs.set("scan_black_min", 3)
        prefs.set("scan_silence_min", 5)
        prefs.set("lufs_check", True)
        w = warns()
        self.assertTrue(any("μαύρη" in t for t in w))
        self.assertTrue(any("σιωπή" in t for t in w))
        self.assertTrue(any("πολύ δυνατή" in t for t in w))   # -14 LUFS με στόχο -24 ±3


if __name__ == "__main__":
    unittest.main()
