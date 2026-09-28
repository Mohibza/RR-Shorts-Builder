"""Speech-model download: Hugging Face rate limits (HTTP 429) are retried; real failures stop the job clearly."""
import unittest
from types import SimpleNamespace
from unittest import mock

import huggingface_hub

from shortsforge import transcriber


class RateLimited(Exception):
    def __init__(self):
        super().__init__("429 Client Error: Too Many Requests")
        self.response = SimpleNamespace(status_code=429)


class NotFound(Exception):
    def __init__(self):
        super().__init__("404 Client Error: Not Found")
        self.response = SimpleNamespace(status_code=404)


class EnsureModelTest(unittest.TestCase):
    def run_with(self, outcomes):
        calls = []

        def fake_download(*a, **k):
            calls.append(1)
            out = outcomes[len(calls) - 1]
            if isinstance(out, Exception):
                raise out
            return out

        with mock.patch.object(transcriber, "model_path", return_value=None), \
                mock.patch.object(transcriber, "RETRY_WAITS", [0, 0, 0, 0]), \
                mock.patch.object(huggingface_hub, "HfApi", side_effect=RuntimeError("offline")), \
                mock.patch.object(huggingface_hub, "snapshot_download", side_effect=fake_download):
            return transcriber.ensure_model("small"), len(calls)

    def test_rate_limit_is_retried(self):
        path, calls = self.run_with([RateLimited(), RateLimited(), "C:/models/small"])
        self.assertEqual(path, "C:/models/small")
        self.assertEqual(calls, 3)

    def test_gives_up_with_clear_error(self):
        with self.assertRaises(transcriber.ModelDownloadError) as ctx:
            self.run_with([RateLimited()] * 5)
        self.assertIn("try again in a few minutes", str(ctx.exception))

    def test_permanent_error_is_not_retried(self):
        with self.assertRaises(transcriber.ModelDownloadError):
            self.run_with([NotFound(), "never reached"])


if __name__ == "__main__":
    unittest.main()
