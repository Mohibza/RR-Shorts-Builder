"""Main window: sidebar navigation + pages, wires the queue worker."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from shortsforge import __version__, cookies, fonts
from shortsforge.config import Settings, usable_output_dir
from shortsforge.utils import ToolMissing, find_ffmpeg, set_ffmpeg_override

from . import theme
from .page_create import CreatePage
from .page_library import LibraryPage
from .page_music import MusicPage
from .page_publish import PublishPage
from .page_settings import SettingsPage
from .page_styles import StylesPage
from .widgets import label
from .workers import FontWorker, QueueWorker, UploadWorker, WatchWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.s = Settings.load()
        base, moved = usable_output_dir(self.s.output_dir)
        if moved:  # e.g. Windows blocks new folders in Videos -> use a folder we can write to
            self.s.output_dir = str(base)
            self.s.save()
        set_ffmpeg_override(self.s.ffmpeg_path)
        self.setWindowTitle("RR Shorts Builder — AI Shorts Generator")
        self.setWindowIcon(theme.app_icon())
        # opens maximized (see app.py); pages lay themselves out to fit the screen
        self.setMinimumSize(1024, 640)
        self.job_seq = 0
        self.jobs: dict = {}
        self._login_retry: list = []
        self._asking = False

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # ---- sidebar
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(theme.S(208))
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 22, 16, 18)
        sv.setSpacing(6)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(theme.S(38), theme.S(38)))
        brand.addWidget(logo)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(label("RR Shorts", "Logo"))
        bt.addWidget(label(f"BUILDER · v{__version__}", "LogoSub"))
        brand.addLayout(bt)
        brand.addStretch(1)
        sv.addLayout(brand)
        sv.addSpacing(22)

        self.stack = QStackedWidget()
        self.create = CreatePage(self.s)
        self.library = LibraryPage(self.s)
        self.music = MusicPage(self.s)
        self.publish = PublishPage(self.s)
        self.styles = StylesPage(self.s)
        self.settings = SettingsPage(self.s)
        pages = [("Create", "spark", self.create), ("Library", "film", self.library),
                 ("Publish", "upload", self.publish), ("Music", "music", self.music),
                 ("Styles", "palette", self.styles), ("Settings", "settings", self.settings)]
        self.page_index = {id(p): i for i, (_, _, p) in enumerate(pages)}
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_btns = []
        for i, (name, ic, page) in enumerate(pages):
            b = QPushButton("  " + name)
            b.setObjectName("Nav")
            b.setIcon(theme.icon(ic, theme.TEXT))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self.go(k))
            self.nav.addButton(b, i)
            self.nav_btns.append(b)
            sv.addWidget(b)
            self.stack.addWidget(page)
        sv.addStretch(1)
        self.status_card = QLabel()
        self.status_card.setWordWrap(True)
        self.status_card.setObjectName("Small")
        self.status_card.setStyleSheet(f"background:{theme.PANEL2}; border:1px solid {theme.BORDER};"
                                       "border-radius:8px; padding:10px; line-height: 140%;")
        sv.addWidget(self.status_card)
        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.nav_btns[0].setChecked(True)

        # ---- worker
        self.worker = QueueWorker()
        self.worker.log.connect(self.create.append_log)
        self.worker.progress.connect(self.create.on_progress)
        self.worker.job_state.connect(self._job_state)
        self.worker.short_ready.connect(self.create.on_short)
        self.worker.start()
        self.running = 0

        self.create.enqueue.connect(self.enqueue)
        self.create.cancel_job.connect(self.worker.cancel)
        self.create.open_short.connect(self.open_short)
        self.create.manage_music.connect(lambda: self.go(self.music))
        self.create.open_settings.connect(lambda: self.go(self.settings))
        self.music.changed.connect(self._music_changed)
        self.settings.saved.connect(self._settings_saved)
        self.styles.settings_changed.connect(self._styles_changed)

        # ---- autopilot: uploads + channel watch
        self.uploader = UploadWorker()
        self.uploader.started_job.connect(lambda j: (self.publish.refresh_queue(), self._update_status()))
        self.uploader.finished_job.connect(self._upload_finished)
        self.uploader.start()
        self.worker.short_ready.connect(self._auto_schedule)
        self.publish.queue_changed.connect(self.uploader.kick)
        self.library.upload_requested.connect(self._upload_one)
        self.settings.check_now.connect(lambda: self._watch_tick(force=True))
        self.settings.watch_changed.connect(self._update_status)
        self._watcher = None
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self._watch_tick)
        self.watch_timer.start(60 * 1000)
        QTimer.singleShot(20 * 1000, self._watch_tick)

        fit_widgets(self)
        QTimer.singleShot(200, self.startup_checks)

    # ------------------------------------------------------------------
    def go(self, i):
        if not isinstance(i, int):
            i = self.page_index[id(i)]
        target = self.stack.widget(i)
        if self.stack.currentWidget() is self.library and target is not self.library:
            self.library.stop()
        if self.stack.currentWidget() is self.music and target is not self.music:
            self.music.stop()
        self.stack.setCurrentIndex(i)
        self.nav_btns[i].setChecked(True)
        if target is self.library:
            self.library.reload()
        elif target is self.music:
            self.music.refill()

    def open_short(self, plan_file: str):
        self.go(self.library)
        self.library.select_plan(plan_file)

    def enqueue(self, items: list, settings: Settings):
        for it in items:
            self.job_seq += 1
            self.jobs[self.job_seq] = (it, settings)
            self.create.add_job(self.job_seq, it.title, it.source)
            self.worker.add(self.job_seq, it, settings)
        self.running += len(items)
        self._update_status()

    def _job_state(self, job_id: int, state: str, msg: str):
        self.create.on_state(job_id, state, msg)
        if state in ("done", "failed", "cancelled"):
            self.running = max(0, self.running - 1)
            self._update_status()
        if state == "failed" and cookies.needs_login(msg) and job_id in self.jobs:
            self._login_retry.append(job_id)
            if not self._asking:
                QTimer.singleShot(0, self._retry_after_login)

    def _retry_after_login(self):
        self._asking = True
        try:
            ok = self.create.ask_login()
        finally:
            self._asking = False
        ids, self._login_retry = self._login_retry, []
        if ok:
            self.enqueue([self.jobs[i][0] for i in ids], self.jobs[ids[0]][1])

    def _music_changed(self):
        self.create.t_music.setChecked(self.s.add_music)
        self.create.update_music_info()

    def _settings_saved(self):
        self._update_status()
        self.create.update_account()
        self.create.update_ai_chip()
        self.create.update_music_info()
        self.music._watch()
        self.music.refill()

    def _styles_changed(self):
        # keep the Create page's caption combo in sync with Styles page choices
        c = self.create.cap
        c.setCurrentIndex(max(0, c.findData(self.s.caption_style)))

    def _update_status(self):
        try:
            find_ffmpeg()
            ff = "FFmpeg ✓"
        except ToolMissing:
            ff = "<span style='color:#FF5A5F'>FFmpeg missing</span>"
        miss = len(fonts.missing_fonts())
        ft = "Fonts ✓" if not miss else f"{miss} fonts pending"
        q = f"{self.running} job(s) in queue" if self.running else "Idle"
        yt = "YouTube ✓" if cookies.has_login() else "YouTube: not signed in"
        auto = []
        if getattr(self.s, "watch_enabled", False) and self.s.watch_channels:
            auto.append(f"watching {len(self.s.watch_channels)} channel(s)")
        try:
            from shortsforge import uploadqueue
            w = sum(1 for j in uploadqueue.load() if j["status"] in ("waiting", "uploading"))
            if w:
                auto.append(f"{w} upload(s) queued")
        except Exception:
            pass
        extra = ("<br><span style='color:#9B8CFF'>Autopilot: " + ", ".join(auto) + "</span>") if auto else ""
        self.status_card.setText(f"<b>{q}</b><br>{ff} · {ft}<br>{yt}{extra}")

    def startup_checks(self):
        self._update_status()
        try:
            find_ffmpeg()
        except ToolMissing as e:
            QMessageBox.warning(self, "FFmpeg needed", str(e))
        if fonts.missing_fonts():
            self._fw = FontWorker()
            self._fw.log.connect(self.create.append_log)
            self._fw.finished_with.connect(lambda _: (self._update_status(), self.settings.update_font_status()))
            self._fw.start()

    def closeEvent(self, e):
        if self.running and QMessageBox.question(
                self, "Quit RR Shorts Builder", "Shorts are still being generated. Quit anyway?") != QMessageBox.Yes:
            e.ignore()
            return
        self.library.stop()
        self.music.stop()
        self.worker.stop()
        self.worker.wait(3000)
        self.uploader.stop()
        self.uploader.wait(2000)
        super().closeEvent(e)

    # ------------------------------------------------------------------ autopilot
    def _auto_schedule(self, job_id: int, r: dict):
        s = Settings.load()
        if not getattr(s, "auto_upload", False) or not r.get("plan_file"):
            return
        from shortsforge import publish, uploadqueue
        try:
            jobs = uploadqueue.schedule(r["plan_file"], s)
        except Exception as e:
            self.create.append_log(f"Auto-upload: couldn't schedule: {e}")
            return
        for j in jobs:
            import time as _t
            self.create.append_log(f"Auto-upload: “{j['title'][:50]}” → {j['account']} "
                                   f"({j['platform']}) at {_t.strftime('%a %H:%M', _t.localtime(j['due']))}")
        if not jobs and not any(publish.enabled_accounts(p) for p in s.upload_platforms or []):
            self.create.append_log("Auto-upload is on, but no account is connected (Publish page).")
        self.publish.refresh_queue()
        self._update_status()
        self.uploader.kick()

    def _upload_one(self, plan_file: str):
        from shortsforge import publish, uploadqueue
        s = Settings.load()
        plats = [p for p in (s.upload_platforms or []) if publish.enabled_accounts(p)] or \
                [p for p in publish.PLATFORMS if publish.enabled_accounts(p)]
        if not plats:
            QMessageBox.information(self, "Upload", "Connect an account on the Publish page first.")
            self.go(self.publish)
            return
        jobs = uploadqueue.schedule(plan_file, s, plats)
        if not jobs:
            QMessageBox.information(self, "Upload", "This Short is already queued or uploaded to those accounts.")
        self.publish.refresh_queue()
        self.go(self.publish)
        self.uploader.kick()

    def _upload_finished(self, j: dict):
        self.publish.refresh_queue()
        self._update_status()
        name = f"{j.get('platform', '')} · {j.get('account', '')}"
        if j.get("status") == "done":
            self.create.append_log(f"✔ Uploaded “{j.get('title', '')[:50]}” to {name}"
                                   + (f" ({j['note']})" if j.get("note") else ""))
        elif j.get("status") == "failed":
            self.create.append_log(f"✕ Upload to {name} failed: {j.get('error', '')}")
        elif j.get("error"):
            self.create.append_log(f"Upload to {name} will retry: {j.get('error', '')}")

    def _watch_tick(self, force: bool = False):
        s = Settings.load()
        self.s.watch_enabled, self.s.watch_channels = s.watch_enabled, s.watch_channels
        if not s.watch_channels or (not s.watch_enabled and not force):
            return
        if self._watcher and self._watcher.isRunning():
            return
        from shortsforge import autowatch
        import time as _t
        if not force and _t.time() - autowatch.last_check() < max(15, s.watch_interval_min) * 60:
            return
        self._watcher = WatchWorker(s.copy())
        self._watcher.log.connect(self.create.append_log)
        self._watcher.found.connect(self._watch_found)
        self._watcher.start()

    def _watch_found(self, items: list):
        self.settings.update_watch_status()
        if items:
            self.enqueue(items, Settings.load())
            self.create.append_log(f"Auto-watch: queued {len(items)} new video(s)")


def fit_widgets(root) -> None:
    """Let dropdowns shrink with the window instead of forcing it wider (long item texts)."""
    from PySide6.QtWidgets import QComboBox
    for c in root.findChildren(QComboBox):
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
