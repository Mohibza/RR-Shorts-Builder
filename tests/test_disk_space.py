"""A full disk must stop the job up front with a clear message, not fail halfway through."""
import shutil
import unittest
from collections import namedtuple
from unittest import mock

from shortsforge import pipeline, utils
from shortsforge.config import Settings
from shortsforge.downloader import SourceItem

Usage = namedtuple("Usage", "total used free")


class DiskSpaceTest(unittest.TestCase):
    def test_message_names_drive_and_sizes(self):
        with mock.patch.object(shutil, "disk_usage", return_value=Usage(100e9, 99e9, 1e9)):
            with self.assertRaises(utils.NotEnoughSpace) as ctx:
                utils.check_free_space("C:/some/new/folder", 7e9, "the video download")
        msg = str(ctx.exception)
        self.assertIn("7.0 GB", msg)
        self.assertIn("1.0 GB free", msg)

    def test_enough_space_passes(self):
        with mock.patch.object(shutil, "disk_usage", return_value=Usage(100e9, 10e9, 90e9)):
            utils.check_free_space("C:/", 7e9, "x")

    def test_stream_download_refused_before_starting(self):
        item = SourceItem("https://www.youtube.com/watch?v=abc", "3 hour stream", "no_such_cached_vid",
                          duration=3 * 3600)
        with mock.patch.object(shutil, "disk_usage", return_value=Usage(100e9, 98e9, 2e9)), \
                mock.patch.object(pipeline, "download") as dl:
            with self.assertRaises(utils.NotEnoughSpace):
                pipeline.Pipeline(Settings(), log=lambda m: None).process(item)
        dl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
