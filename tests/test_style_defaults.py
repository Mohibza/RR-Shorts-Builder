"""The random caption mix must only use styles that read well on a phone, by default and after upgrading."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shortsforge import config
from shortsforge.captions import CAPTION_STYLES
from shortsforge.pipeline import StyleRotator

SMALL = {"minimal", "karaoke", "typewriter", "card"}


class StyleDefaultsTest(unittest.TestCase):
    def test_pool_styles_exist(self):
        self.assertTrue(set(config.DEFAULT_CAPTION_POOL) <= set(CAPTION_STYLES))

    def test_random_mix_skips_small_styles(self):
        rot = StyleRotator(config.Settings(), seed=1234)
        used = {rot.choice(i).caption_style for i in range(40)}
        self.assertFalse(used & SMALL, used)
        self.assertGreater(len(used), 5)       # still a real mix

    def _load(self, data: dict) -> config.Settings:
        f = Path(tempfile.mkdtemp()) / "settings.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        with mock.patch.object(config, "SETTINGS_FILE", f):
            return config.Settings.load()

    def test_upgrade_old_all_styles_setting(self):
        s = self._load({"caption_pool": [], "settings_version": 3})
        self.assertEqual(s.caption_pool, list(config.DEFAULT_CAPTION_POOL))

    def test_upgrade_keeps_users_own_choice(self):
        s = self._load({"caption_pool": ["minimal", "neon"], "settings_version": 3})
        self.assertEqual(s.caption_pool, ["minimal", "neon"])


if __name__ == "__main__":
    unittest.main()
