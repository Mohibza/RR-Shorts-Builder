"""Failures must be diagnosable afterwards: FFmpeg errors keep the real cause and log the full command."""
import unittest
from unittest import mock

from shortsforge import joblog
from shortsforge.utils import ToolMissing, find_ffmpeg, run_ffmpeg


class FFmpegFailureLogTest(unittest.TestCase):
    def test_failed_command_is_logged_in_full(self):
        try:
            find_ffmpeg()
        except ToolMissing:
            self.skipTest("ffmpeg not installed")
        with mock.patch.object(joblog, "write") as w:
            with self.assertRaises(RuntimeError) as ctx:
                run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=320x240:d=0.2",
                            "-vf", "crop=w=9999:h=9999", "-f", "null", "-"], 0.2)
        logged = "\n".join(str(c.args[0]) for c in w.call_args_list)
        self.assertIn("COMMAND:", logged)
        self.assertIn("crop=w=9999:h=9999", logged)
        self.assertIn("FFmpeg failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
