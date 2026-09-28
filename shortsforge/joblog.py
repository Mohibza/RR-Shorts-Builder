"""Persistent job log (%APPDATA%\\RRShortsBuilder\\logs\\jobs.log) so every failure can be diagnosed afterwards.

The in-app log panel is gone when the app closes; this file keeps the last few runs (rotated at 5 MB)."""
from __future__ import annotations

import logging
import logging.handlers
import threading

from .config import data_dir

LOG_DIR = data_dir() / "logs"
LOG_FILE = LOG_DIR / "jobs.log"
_lock = threading.Lock()
_logger: logging.Logger | None = None


def _get() -> logging.Logger:
    global _logger
    with _lock:
        if _logger is None:
            lg = logging.getLogger("rrshorts.jobs")
            lg.setLevel(logging.INFO)
            lg.propagate = False
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3,
                                                         encoding="utf-8")
                h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
                lg.addHandler(h)
            except OSError:
                lg.addHandler(logging.NullHandler())   # a log we can't write must never break a job
            _logger = lg
        return _logger


def write(msg: str) -> None:
    try:
        _get().info(str(msg).rstrip())
    except Exception:
        pass
