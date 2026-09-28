"""One Short failing to render must not cost the user the other Shorts of the video."""
import tempfile
import unittest
from unittest import mock

import numpy as np

from shortsforge import pipeline
from shortsforge.config import Settings
from shortsforge.downloader import SourceItem
from shortsforge.highlights import Clip


def run(fail_on: set):
    s = Settings()
    s.output_dir = tempfile.mkdtemp(prefix="sf_test_")
    s.caption_lang = "auto"
    clips = [Clip(i * 60.0, i * 60.0 + 30, 1.0, "", "", f"Clip {i + 1}") for i in range(5)]
    rendered = []

    def fake_render(self, src, info, item, tr, clip, choice, index, *a, **k):
        if index + 1 in fail_on:
            raise RuntimeError("FFmpeg failed (code 4294967274):\n[Parsed_crop_9] Failed to configure input pad")
        rendered.append(index + 1)
        return pipeline.ShortResult(f"{index}.mp4", "", clip.title, clip.start, clip.end, 1.0, "", "", {})

    with mock.patch.object(pipeline, "download", return_value="src.mp4"), \
            mock.patch.object(pipeline, "probe", return_value={"duration": 600.0, "width": 1920, "height": 1080,
                                                               "has_audio": False, "fps": 30}), \
            mock.patch.object(pipeline.AI, "from_settings", return_value=None), \
            mock.patch.object(pipeline.director, "pick", return_value=clips), \
            mock.patch.object(pipeline.metadata, "generate", return_value=[{} for _ in clips]), \
            mock.patch.object(pipeline.Pipeline, "render_clip", fake_render):
        p = pipeline.Pipeline(s, log=lambda m: None)
        return p, p.process(SourceItem("x.mp4", "Test video", "test_vid", is_local=True)), rendered


class IsolationTest(unittest.TestCase):
    def test_other_shorts_still_render(self):
        p, results, rendered = run({2})
        self.assertEqual(rendered, [1, 3, 4, 5])
        self.assertEqual(len(results), 4)
        self.assertEqual([n for n, _ in p.failed], [2])

    def test_all_failed_is_an_error(self):
        with self.assertRaises(RuntimeError):
            run({1, 2, 3, 4, 5})


if __name__ == "__main__":
    unittest.main()
