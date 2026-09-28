"""Create page: source input + grouped settings on the left, live queue and fresh Shorts on the right."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from shortsforge import cookies
from shortsforge.captions import CAPTION_STYLES
from shortsforge.config import Settings
from shortsforge.downloader import VIDEO_EXT
from shortsforge.effects import LAYOUTS
from shortsforge.utils import open_path

from . import theme
from .widgets import FlowLayout, JobCard, ShortThumb, Toggle, field, label
from .workers import ExpandWorker

LANGS = [("auto", "Auto-detect"), ("ur", "Urdu"), ("en", "English"), ("hi", "Hindi"), ("pa", "Punjabi"),
         ("ar", "Arabic"), ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("tr", "Turkish"),
         ("id", "Indonesian"), ("pt", "Portuguese"), ("ru", "Russian"), ("bn", "Bengali")]
CAPTION_LANGS = [("roman", "Roman Urdu / English  (kya kar rahe ho)"), ("en", "English (translate everything)"),
                 ("auto", "Original script"), ("ur", "Urdu script · اردو"), ("hi", "Hindi script · हिन्दी")]
SFX_LEVELS = [("auto", "Auto (matches each clip)"), ("off", "Off"), ("subtle", "Subtle"), ("medium", "Energetic"), ("high", "Maximum")]
QUALITY = [("auto", "Auto · best for this PC"), ("tiny", "Fastest · tiny (75 MB)"), ("base", "Fast · base (145 MB)"),
           ("small", "Balanced · small (484 MB)"), ("medium", "Accurate · medium (1.5 GB)"),
           ("large-v3-turbo", "Best · large-v3 turbo (1.6 GB, GPU)")]
PICKERS = [("gemini", "Gemini AI editor (free key)"), ("claude", "Claude AI editor"), ("local", "Offline director")]


def combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    c = QComboBox()
    for k, v in items:
        c.addItem(v, k)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


class InputShell(QFrame):
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("InputShell")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            u = md.urls()[0]
            self.dropped.emit(u.toLocalFile() if u.isLocalFile() else u.toString())
        elif md.hasText():
            self.dropped.emit(md.text().strip())


class Section(QFrame):
    """Card with a small icon + uppercase title, then content."""

    def __init__(self, title: str, icon: str, hint: str = ""):
        super().__init__()
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 18)
        v.setSpacing(12)
        h = QHBoxLayout()
        h.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(theme.icon(icon, theme.ACCENT2, 16).pixmap(16, 16))
        h.addWidget(ic)
        h.addWidget(label(title.upper(), "Section"))
        h.addStretch(1)
        if hint:
            h.addWidget(label(hint, "Small"))
        v.addLayout(h)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(12)
        v.addLayout(self.grid)
        self.v = v


class CreatePage(QWidget):
    enqueue = Signal(list, object)      # items, settings snapshot
    cancel_job = Signal(int)
    open_short = Signal(str)            # plan file
    manage_music = Signal()
    open_settings = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = s = settings
        self.cards: dict[int, JobCard] = {}
        self.job_dirs: dict[int, str] = {}
        self._expander = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ================================================================ left: source + settings
        # fills the window like a desktop app: no page scrolling, the cards share the space
        body = QWidget()
        outer.addWidget(body, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(28, 20, 24, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("New Shorts", "H1"))
        head.addLayout(tv, 1)
        self.ai_chip = label("", "Chip")
        head.addWidget(self.ai_chip, 0, Qt.AlignBottom)
        root.addLayout(head)

        self.shell = InputShell()
        sh = QHBoxLayout(self.shell)
        sh.setContentsMargins(16, 8, 8, 8)
        sh.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(theme.icon("link", theme.MUTED, 18).pixmap(18, 18))
        sh.addWidget(ic)
        self.input = QLineEdit()
        self.input.setObjectName("BigInput")
        self.input.setPlaceholderText("https://youtube.com/watch?v=…   ·   youtube.com/@channel   ·   drop a file here")
        self.input.returnPressed.connect(self.submit)
        sh.addWidget(self.input, 1)
        browse = QPushButton(" Open file")
        browse.setIcon(theme.icon("file", theme.TEXT))
        browse.setObjectName("Ghost")
        browse.clicked.connect(self.browse)
        sh.addWidget(browse)
        self.go = QPushButton("  Generate Shorts")
        self.go.setIcon(theme.icon("wand", "#FFFFFF"))
        self.go.setObjectName("Primary")
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.clicked.connect(self.submit)
        sh.addWidget(self.go)
        self.shell.dropped.connect(self._dropped)
        root.addWidget(self.shell)

        ar = QHBoxLayout()
        ar.setContentsMargins(4, 0, 0, 0)
        self.acct = label("", "Small")
        self.acct_btn = QPushButton("Sign in to YouTube")
        self.acct_btn.setObjectName("Link")
        self.acct_btn.setCursor(Qt.PointingHandCursor)
        self.acct_btn.clicked.connect(self._account_clicked)
        ar.addWidget(self.acct)
        ar.addWidget(self.acct_btn)
        ar.addStretch(1)
        root.addLayout(ar)
        self.update_account()

        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.setVerticalSpacing(14)
        root.addLayout(cards)

        # ---- Clips
        sec = Section("Clips", "film", "what gets picked")
        self.count = QSpinBox()
        self.count.setRange(1, 30)
        self.count.setValue(s.shorts_per_video)
        self.minlen = QSpinBox()
        self.minlen.setRange(8, 170)
        self.minlen.setSuffix(" s")
        self.minlen.setValue(int(s.min_duration))
        self.maxlen = QSpinBox()
        self.maxlen.setRange(10, 180)
        self.maxlen.setSuffix(" s")
        self.maxlen.setValue(int(s.max_duration))
        lenrow = QWidget()
        lr = QHBoxLayout(lenrow)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.addWidget(self.minlen, 1)
        lr.addWidget(label("to", "Muted"))
        lr.addWidget(self.maxlen, 1)
        self.picker = combo(PICKERS, getattr(s, "clip_picker", "gemini"))
        self.picker.setToolTip("Gemini / Claude read the whole transcript like a human editor and pick the most viral,\n"
                               "self-contained moments. Offline director works without any key.")
        self.picker.currentIndexChanged.connect(self.update_ai_chip)
        self.lang = combo(LANGS, s.language)
        sec.grid.addWidget(field("Shorts per video", self.count), 0, 0)
        sec.grid.addWidget(field("Length of each Short", lenrow), 0, 1)
        sec.grid.addWidget(field("Viral moment picker", self.picker), 1, 0)
        sec.grid.addWidget(field("Spoken language", self.lang), 1, 1)
        self.t_cuts = Toggle("Remove pauses", getattr(s, "remove_pauses", True))
        self.t_cuts.setToolTip("Jump-cut the silent gaps between sentences for faster pacing.")
        self.t_cold = Toggle("Teaser opening", getattr(s, "cold_open", True))
        self.t_cold.setToolTip("Open on the most gripping line, then play the full moment.")
        tr = QHBoxLayout()
        tr.setSpacing(24)
        for t in (self.t_cuts, self.t_cold):
            tr.addWidget(t)
        tr.addStretch(1)
        sec.v.addLayout(tr)
        sec.v.addStretch(1)
        self._stretch(sec.grid)
        cards.addWidget(sec, 0, 0)

        # ---- Captions
        sec = Section("Captions", "spark", "text on screen")
        self.cap_lang = combo(CAPTION_LANGS, getattr(s, "caption_lang", "roman"))
        self.cap_lang.setToolTip("Roman Urdu writes Urdu/Hindi speech in Latin letters (\"kya kar rahe ho\");\n"
                                 "English speech stays English.")
        caps = [("random", "Random mix (different every Short)")] + [(k, v["name"]) for k, v in CAPTION_STYLES.items()]
        self.cap = combo(caps, s.caption_style)
        self.pos = combo([("lower", "Lower third"), ("middle", "Center"), ("upper", "Upper")], s.caption_position)
        self.quality = combo(QUALITY, s.whisper_model)
        sec.grid.addWidget(field("Caption text", self.cap_lang), 0, 0)
        sec.grid.addWidget(field("Caption style", self.cap), 0, 1)
        sec.grid.addWidget(field("Caption position", self.pos), 1, 0)
        sec.grid.addWidget(field("Transcription quality", self.quality), 1, 1)
        sec.v.addStretch(1)
        self._stretch(sec.grid)
        cards.addWidget(sec, 0, 1)

        # ---- Look
        sec = Section("Look", "palette", "framing & text")
        self.layout_c = combo(list(LAYOUTS.items()), s.layout)
        self.cta = QLineEdit(s.cta_text)
        self.cta.setPlaceholderText("Empty = no end card")
        self.wm = QLineEdit(s.watermark)
        self.wm.setPlaceholderText("@yourchannel")
        sec.grid.addWidget(field("Framing", self.layout_c), 0, 0)
        sec.grid.addWidget(field("Watermark", self.wm), 0, 1)
        sec.grid.addWidget(field("End card text", self.cta), 1, 0, 1, 2)
        self.t_hook = Toggle("Hook heading", s.hook_title)
        self.t_hook.setToolTip("Bold hook heading at the top, shown only in the first seconds")
        self.t_bar = Toggle("Progress bar", s.progress_bar)
        tr = QHBoxLayout()
        tr.setSpacing(24)
        for t in (self.t_hook, self.t_bar):
            tr.addWidget(t)
        tr.addStretch(1)
        sec.v.addLayout(tr)
        sec.v.addStretch(1)
        self._stretch(sec.grid)
        cards.addWidget(sec, 1, 0)

        # ---- Audio
        sec = Section("Audio", "music", "sound design")
        self.sfx = combo(SFX_LEVELS, getattr(s, "sfx_level", "medium"))
        self.sfx.setToolTip("Whooshes, pops, keyboard typing, dings and bass hits timed to the edits.\n"
                            "All sounds are generated by Rebels Revolt Shorts itself, so they're copyright-free.")
        sec.grid.addWidget(field("Sound effects", self.sfx), 0, 0)
        mw = QWidget()
        mv = QVBoxLayout(mw)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(4)
        mh = QHBoxLayout()
        mh.setContentsMargins(0, 0, 0, 0)
        self.t_music = Toggle("Background music", s.add_music)
        self.music_info = label("", "Small", wrap=True)
        mm = QPushButton("Manage music")
        mm.setObjectName("Link")
        mm.setCursor(Qt.PointingHandCursor)
        mm.clicked.connect(self.manage_music.emit)
        mh.addWidget(self.t_music)
        mh.addStretch(1)
        mh.addWidget(mm)
        mv.addLayout(mh)
        mv.addWidget(self.music_info)
        sec.grid.addWidget(field("Music", mw), 1, 0, 1, 2)
        sec.v.addStretch(1)
        self._stretch(sec.grid)
        cards.addWidget(sec, 1, 1)
        root.addStretch(1)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)

        # ================================================================ right: queue + results
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setMinimumWidth(300)
        side.setMaximumWidth(420)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(20, 26, 20, 20)
        sv.setSpacing(12)
        qh = QHBoxLayout()
        qh.addWidget(label("Queue", "H2"))
        qh.addStretch(1)
        self.log_btn = QPushButton("Log")
        self.log_btn.setObjectName("Link")
        self.log_btn.setCheckable(True)
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.toggled.connect(lambda on: self.log.setVisible(on))
        qh.addWidget(self.log_btn)
        sv.addLayout(qh)
        self.empty = label("No jobs yet. Paste a link and press Generate.", "Muted", wrap=True)
        sv.addWidget(self.empty)
        qs = QScrollArea()
        qs.setWidgetResizable(True)
        qs.setMinimumHeight(140)
        qw = QWidget()
        self.queue_box = QVBoxLayout(qw)
        self.queue_box.setContentsMargins(0, 0, 4, 0)
        self.queue_box.setSpacing(8)
        self.queue_box.addStretch(1)
        qs.setWidget(qw)
        sv.addWidget(qs, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMinimumHeight(90)
        self.log.setObjectName("Log")
        sv.addWidget(self.log, 2)
        sv.addWidget(label("Fresh Shorts", "H2"))
        self.results_hint = label("Finished Shorts appear here. Click one to preview, copy details or restyle.",
                                  "Muted", wrap=True)
        sv.addWidget(self.results_hint)
        rs = QScrollArea()
        rs.setWidgetResizable(True)
        self.results_w = QWidget()
        self.results = FlowLayout(self.results_w, spacing=12)
        rs.setWidget(self.results_w)
        sv.addWidget(rs, 3)
        outer.addWidget(side)
        self.side = side
        self.update_ai_chip()
        self.update_music_info()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.side.setFixedWidth(int(min(theme.S(420), max(theme.S(290), self.width() * 0.27))))

    @staticmethod
    def _stretch(grid: QGridLayout):
        for c in range(2):
            grid.setColumnStretch(c, 1)

    # ------------------------------------------------------------------
    def update_ai_chip(self, *_):
        p = self.picker.currentData()
        key = {"gemini": getattr(self.s, "gemini_api_key", ""), "claude": getattr(self.s, "anthropic_api_key", "")}
        if p in key and key[p].strip():
            self.ai_chip.setText(f"●  AI editor: {'Gemini' if p == 'gemini' else 'Claude'}")
            self.ai_chip.setProperty("state", "on")
        elif p in key:
            self.ai_chip.setText(f"●  Add a {'Gemini' if p == 'gemini' else 'Claude'} key in Settings")
            self.ai_chip.setProperty("state", "warn")
        else:
            self.ai_chip.setText("●  Offline director")
            self.ai_chip.setProperty("state", "off")
        self.ai_chip.style().unpolish(self.ai_chip)
        self.ai_chip.style().polish(self.ai_chip)

    def update_music_info(self):
        try:
            n = len([p for p in Path(self.s.music_dir).iterdir()
                     if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac")])
        except Exception:
            n = 0
        mode = "starred tracks" if getattr(self.s, "music_mode", "random") == "starred" else "random track"
        self.music_info.setText(f"{n} tracks · {mode} · {'Auto level' if getattr(self.s, 'music_auto', True) else 'manual level'}")

    def snapshot(self) -> Settings:
        s = self.s
        s.shorts_per_video = self.count.value()
        s.min_duration = float(min(self.minlen.value(), self.maxlen.value() - 5))
        s.max_duration = float(self.maxlen.value())
        s.caption_style = self.cap.currentData()
        s.layout = self.layout_c.currentData()
        s.language = self.lang.currentData()
        s.caption_lang = self.cap_lang.currentData()
        s.sfx_level = self.sfx.currentData()
        s.whisper_model = self.quality.currentData()
        s.caption_position = self.pos.currentData()
        s.cta_text = self.cta.text()
        s.watermark = self.wm.text()
        s.hook_title = self.t_hook.isChecked()
        s.progress_bar = self.t_bar.isChecked()
        s.part_label = False
        s.add_music = self.t_music.isChecked()
        s.remove_pauses = self.t_cuts.isChecked()
        s.cold_open = self.t_cold.isChecked()
        s.clip_picker = self.picker.currentData()
        s.save()
        return s.copy()

    def browse(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT))
        f, _ = QFileDialog.getOpenFileName(self, "Choose a video", "", f"Videos ({exts})")
        if f:
            self.input.setText(f)
            self.submit()

    def _dropped(self, path: str):
        self.input.setText(path)
        self.submit()

    def submit(self):
        p = self.picker.currentData()
        need = {"gemini": ("gemini_api_key", "Gemini"), "claude": ("anthropic_api_key", "Claude")}.get(p)
        if need and not getattr(self.s, need[0], "").strip():
            box = QMessageBox(self)
            box.setWindowTitle(f"{need[1]} key needed")
            box.setIcon(QMessageBox.Information)
            box.setText(f"The {need[1]} AI editor needs an API key (Settings → AI editor).\n\n"
                        "Continue with the offline director for now?")
            go = box.addButton("Use offline director", QMessageBox.AcceptRole)
            st = box.addButton("Open Settings", QMessageBox.ActionRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is st:
                self.open_settings.emit()
                return
            if box.clickedButton() is not go:
                return
            self.picker.setCurrentIndex(self.picker.findData("local"))
        src = self.input.text().strip()
        if not src:
            self.input.setFocus()
            return
        self.go.setEnabled(False)
        self.go.setText("  Reading link…")
        self._expander = ExpandWorker(src, self.s.cookies_browser)
        self._expander.done.connect(self._expanded)
        self._expander.failed.connect(self._expand_failed)
        self._expander.start()

    def _reset_go(self):
        self.go.setEnabled(True)
        self.go.setText("  Generate Shorts")

    def _expand_failed(self, msg: str):
        self._reset_go()
        if cookies.needs_login(msg):
            if self.ask_login():
                self.submit()  # retry the same link with the saved login
            return
        QMessageBox.warning(self, "Couldn't read that link", msg)

    def ask_login(self, reason: str = "") -> bool:
        """YouTube wants a signed-in user: offer the sign-in. Returns True when signed in."""
        box = QMessageBox(self)
        box.setWindowTitle("YouTube needs you to sign in")
        box.setIcon(QMessageBox.Information)
        again = " again" if cookies.has_login() else ""
        box.setText(f"YouTube is asking to confirm you're not a bot.\n\nSign in{again} to YouTube once and the "
                    "download continues automatically." + (f"\n\n{reason}" if reason else ""))
        go = box.addButton("Sign in to YouTube", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return False
        from .login_dialog import sign_in
        ok = sign_in(self.window())
        self.update_account()
        return ok

    def update_account(self):
        if cookies.has_login():
            self.acct.setText(f"YouTube account: <span style='color:{theme.GOOD}'>signed in</span>")
            self.acct_btn.setText("Switch account")
        else:
            self.acct.setText("YouTube account: not signed in (only needed if YouTube asks)")
            self.acct_btn.setText("Sign in")

    def _account_clicked(self):
        from .login_dialog import sign_in
        sign_in(self.window())
        self.update_account()

    def _expanded(self, items: list):
        self._reset_go()
        if not items:
            QMessageBox.information(self, "No videos", "No videos were found at that link.")
            return
        if len(items) > 1:
            n, ok = QInputDialog.getInt(self, "Playlist / channel",
                                        f"Found {len(items)} videos. How many (newest first) should be processed?",
                                        min(len(items), 2), 1, len(items))
            if not ok:
                return
            items = items[:n]
        self.input.clear()
        self.enqueue.emit(items, self.snapshot())

    # ------------------------------------------------------------------
    def add_job(self, job_id: int, title: str, source: str):
        self.empty.hide()
        c = JobCard(job_id, title, source)
        c.cancel_clicked.connect(self.cancel_job.emit)
        c.open_clicked.connect(lambda j: open_path(self.job_dirs.get(j, self.s.output_dir)))
        self.cards[job_id] = c
        self.queue_box.insertWidget(0, c)

    def on_progress(self, job_id: int, stage: str, frac: float, detail: str):
        c = self.cards.get(job_id)
        if c:
            c.set_progress(stage, frac, detail)

    def on_state(self, job_id: int, state: str, msg: str):
        c = self.cards.get(job_id)
        if c:
            if state == "running":
                c.set_state("running")
                c.stage.setText("Starting…")
            else:
                c.set_state(state, msg)

    def on_short(self, job_id: int, r: dict):
        self.job_dirs[job_id] = str(Path(r["path"]).parent)
        self.results_hint.hide()
        t = ShortThumb(r["thumb"], r["title"], r["plan_file"], "", w=150)
        t.clicked.connect(self.open_short.emit)
        self.results.addWidget(t)
        self.results_w.updateGeometry()

    def append_log(self, text: str):
        self.log.appendPlainText(text)
