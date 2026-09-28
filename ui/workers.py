"""Background threads so the UI never freezes."""
from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import asdict
from typing import Optional

from PySide6.QtCore import QThread, Signal

from shortsforge import fonts, joblog, previews
from shortsforge.config import Settings
from shortsforge.downloader import SourceItem, expand
from shortsforge.pipeline import Pipeline, StyleChoice
from shortsforge.utils import Cancelled


class ExpandWorker(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, source: str, cookies: str = ""):
        super().__init__()
        self.source, self.cookies = source, cookies

    def run(self):
        try:
            self.done.emit(expand(self.source, self.cookies))
        except Exception as e:
            self.failed.emit(str(e).split("\n")[0][:400])


class QueueWorker(QThread):
    """Processes queued videos one by one, forever (until stopped)."""
    log = Signal(str)
    progress = Signal(int, str, float, str)      # job_id, stage, frac, detail
    short_ready = Signal(int, dict)
    job_state = Signal(int, str, str)            # job_id, state, message

    def __init__(self):
        super().__init__()
        self.q: "queue.Queue[tuple[int, SourceItem, Settings]]" = queue.Queue()
        self.cancels: dict[int, threading.Event] = {}
        self._stop = False

    def add(self, job_id: int, item: SourceItem, settings: Settings):
        self.cancels[job_id] = threading.Event()
        self.q.put((job_id, item, settings))

    def cancel(self, job_id: int):
        ev = self.cancels.get(job_id)
        if ev:
            ev.set()

    def stop(self):
        self._stop = True
        for ev in self.cancels.values():
            ev.set()
        self.q.put(None)  # type: ignore[arg-type]

    def run(self):
        while not self._stop:
            task = self.q.get()
            if task is None:
                break
            job_id, item, settings = task
            ev = self.cancels[job_id]
            if ev.is_set():
                self.job_state.emit(job_id, "cancelled", "Cancelled")
                continue
            self.job_state.emit(job_id, "running", "")
            try:
                p = Pipeline(
                    settings,
                    log=self.log.emit,
                    progress=lambda st, f, d, j=job_id: self.progress.emit(j, st, f, d),
                    on_short=lambda r, j=job_id: self.short_ready.emit(j, asdict(r)),
                    cancel=ev,
                )
                res = p.process(item)
                bad = f" · {len(p.failed)} failed (see log)" if p.failed else ""
                self.job_state.emit(job_id, "done", f"{len(res)} Shorts ready{bad} · {settings.output_dir}")
            except Cancelled:
                self.job_state.emit(job_id, "cancelled", "Cancelled")
                self.log.emit(f"✖ Cancelled: {item.title}")
            except Exception as e:
                msg = str(e).strip().split("\n")[0][:300]
                self.log.emit(f"✖ {item.title}: {e}\n{traceback.format_exc(limit=3)}")
                joblog.write(f"✖ JOB FAILED {item.title} ({item.source}): {e}\n{traceback.format_exc()}")
                self.job_state.emit(job_id, "failed", msg)


class RestyleWorker(QThread):
    progress = Signal(float)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, settings: Settings, plan_file: str, choice: StyleChoice, hook_text: Optional[str]):
        super().__init__()
        self.s, self.plan_file, self.choice, self.hook = settings, plan_file, choice, hook_text

    def run(self):
        try:
            p = Pipeline(self.s, log=lambda m: None)
            r = p.restyle(self.plan_file, self.choice, self.hook, self.progress.emit)
            self.done.emit(asdict(r))
        except Exception as e:
            joblog.write(f"✖ RE-RENDER FAILED {self.plan_file}: {e}\n{traceback.format_exc()}")
            self.failed.emit(str(e).split("\n")[0][:400])


class PreviewWorker(QThread):
    ready = Signal(str, str, str)   # kind, key, path

    def __init__(self, jobs: list[tuple[str, str]]):
        super().__init__()
        self.jobs = jobs

    def run(self):
        fn = {"cap": previews.caption_preview, "hook": previews.hook_preview, "cta": previews.cta_preview,
              "grade": previews.grade_preview}
        while self.jobs:
            kind, key = self.jobs.pop(0)
            try:
                self.ready.emit(kind, key, str(fn[kind](key)))
            except Exception:
                self.ready.emit(kind, key, "")


class FontWorker(QThread):
    finished_with = Signal(list)
    log = Signal(str)

    def run(self):
        try:
            self.finished_with.emit(fonts.ensure_fonts(self.log.emit))
        except Exception:
            self.finished_with.emit(["?"])


class UploadWorker(QThread):
    """Uploads queued Shorts when they're due, one at a time, forever (until stopped)."""
    started_job = Signal(dict)
    finished_job = Signal(dict)
    progress = Signal(str, float)

    def __init__(self):
        super().__init__()
        self._stop = False
        self._kick = threading.Event()

    def kick(self):
        self._kick.set()

    def stop(self):
        self._stop = True
        self._kick.set()

    def run(self):
        from shortsforge import uploadqueue
        uploadqueue.recover()
        while not self._stop:
            try:
                job = uploadqueue.take_due()
            except Exception:
                job = None
            if job:
                self.started_job.emit(job)
                s = Settings.load()          # always the latest keys / privacy choices
                res = uploadqueue.run_job(job, s, lambda f, i=job["id"]: self.progress.emit(i, f))
                self.finished_job.emit(res or job)
                continue
            self._kick.wait(20)
            self._kick.clear()


class WatchWorker(QThread):
    """Checks the watched channels for new uploads."""
    found = Signal(list)
    log = Signal(str)

    def __init__(self, s: Settings):
        super().__init__()
        self.s = s

    def run(self):
        from shortsforge import autowatch
        try:
            items = autowatch.check(self.s.watch_channels, self.s.watch_first_n, self.s.watch_per_check,
                                    self.s.cookies_browser, self.log.emit)
        except Exception as e:
            self.log.emit(f"Auto-watch error: {e}")
            items = []
        self.found.emit(items)
