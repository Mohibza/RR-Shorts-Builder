"""API keys must not sit in settings.json as plain text, and old plain settings must keep working."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shortsforge import config

KEY = "AIzaSyTEST-not-a-real-key-1234567890"


@unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
class SecretsTest(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp()) / "settings.json"
        self.patch = mock.patch.object(config, "SETTINGS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_saved_encrypted_and_loaded_back(self):
        s = config.Settings()
        s.gemini_api_key = KEY
        s.watermark = "@mychannel"
        s.save()
        raw = self.file.read_text(encoding="utf-8")
        self.assertNotIn(KEY, raw)
        self.assertIn("dpapi:", raw)
        self.assertIn("@mychannel", raw)          # normal settings stay readable
        self.assertEqual(config.Settings.load().gemini_api_key, KEY)

    def test_old_plain_settings_still_load(self):
        self.file.write_text(json.dumps({"gemini_api_key": KEY, "settings_version": 3}), encoding="utf-8")
        self.assertEqual(config.Settings.load().gemini_api_key, KEY)

    def test_unreadable_secret_becomes_empty_not_crash(self):
        self.file.write_text(json.dumps({"gemini_api_key": "dpapi:bm90LWEtcmVhbC1ibG9i", "shorts_per_video": 7,
                                         "settings_version": 3}), encoding="utf-8")
        s = config.Settings.load()
        self.assertEqual(s.gemini_api_key, "")
        self.assertEqual(s.shorts_per_video, 7)


if __name__ == "__main__":
    unittest.main()
