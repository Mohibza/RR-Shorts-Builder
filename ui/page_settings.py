"""Settings: output, engine, transcription, audio, tools."""
from __future__ import annotations

import shutil

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QStackedWidget, QSpinBox, QVBoxLayout, QWidget)

from shortsforge import cookies, fonts, licensing
from shortsforge.config import CACHE_DIR, MODELS_DIR, Settings, data_dir
from shortsforge.utils import ToolMissing, find_ffmpeg, open_path, pick_encoder, set_ffmpeg_override

from . import theme
from .widgets import Card, Toggle, field, label
from .workers import FontWorker


def _combo(items, cur):
    c = QComboBox()
    for k, v in items:
        c.addItem(v, k)
    c.setCurrentIndex(max(0, c.findData(cur)))
    return c


def _path_row(edit: QLineEdit, pick_dir: bool, title: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton()
    b.setIcon(theme.icon("folder"))
    b.setFixedSize(38, 36)

    def pick():
        if pick_dir:
            d = QFileDialog.getExistingDirectory(w, title, edit.text())
        else:
            d, _ = QFileDialog.getOpenFileName(w, title, edit.text())
        if d:
            edit.setText(d)
    b.clicked.connect(pick)
    h.addWidget(b)
    return w


class SettingsPage(QWidget):
    saved = Signal()
    watch_changed = Signal()
    check_now = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = s = settings
        # Preferences-style: categories on the left, one panel at a time (fits any screen, no scrolling)
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 22, 32, 22)
        root.setSpacing(16)
        hr = QHBoxLayout()
        hr.addWidget(label("Settings", "H1"))
        hr.addStretch(1)
        sv = QPushButton("  Save settings")
        sv.setObjectName("Primary")
        sv.setIcon(theme.icon("check", "#FFFFFF"))
        sv.clicked.connect(self.save)
        hr.addWidget(sv)
        root.addLayout(hr)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.cats = QListWidget()
        self.cats.setObjectName("Cats")
        self.cats.setFixedWidth(theme.S(210))
        self.cats.setIconSize(QSize(18, 18))
        self.panels = QStackedWidget()
        self.cats.currentRowChanged.connect(self.panels.setCurrentIndex)
        body.addWidget(self.cats)
        body.addWidget(self.panels, 1)
        root.addLayout(body, 1)

        # Output
        c = Card()
        c.lay.addWidget(label("Output", "H2"))
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(12)
        self.out = QLineEdit(s.output_dir)
        g.addWidget(field("Save Shorts to", _path_row(self.out, True, "Output folder")), 0, 0, 1, 3)
        self.enc = _combo([("auto", "Auto (GPU if available)"), ("libx264", "CPU x264 (best compatibility)"),
                           ("h264_nvenc", "NVIDIA NVENC"), ("h264_qsv", "Intel QuickSync"),
                           ("h264_amf", "AMD AMF")], s.encoder)
        self.crf = QSpinBox()
        self.crf.setRange(14, 32)
        self.crf.setValue(s.quality_crf)
        self.fps = _combo([(0, "Match source (up to 60)"), (30, "30 fps"), (60, "60 fps"), (24, "24 fps")], s.fps)
        self.srcq = _combo([(1080, "1080p (faster download)"), (1440, "1440p (sharper, recommended)"),
                            (2160, "4K (sharpest, big download)")], s.source_quality)
        g.addWidget(field("Video encoder", self.enc), 1, 0)
        g.addWidget(field("Quality (lower = better, 16–20 = HD)", self.crf), 1, 1)
        g.addWidget(field("Frame rate", self.fps), 1, 2)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 120)
        self.gap.setSuffix(" s")
        self.gap.setValue(s.min_gap)
        g.addWidget(field("Minimum gap between chosen moments", self.gap), 2, 0)
        g.addWidget(field("Download quality (higher = crisper vertical crops)", self.srcq), 2, 1)
        c.lay.addLayout(g)
        self._panel("Output", "film", c)

        # AI
        c = Card()
        c.lay.addWidget(label("AI transcription", "H2"))
        c.lay.addWidget(label("Speech is transcribed on your PC with Whisper — nothing is uploaded. Models download "
                              "once on first use (tiny 75 MB … large-v3 3 GB).", "Muted", wrap=True))
        h = QHBoxLayout()
        self.gpu = Toggle("Use NVIDIA GPU for Whisper (needs CUDA 12 libraries — run setup.bat with GPU option)",
                          s.use_gpu)
        h.addWidget(self.gpu)
        h.addStretch(1)
        c.lay.addLayout(h)
        mb = QPushButton(" Open models folder")
        mb.setIcon(theme.icon("folder"))
        mb.clicked.connect(lambda: open_path(MODELS_DIR))
        c.lay.addWidget(mb, 0, Qt.AlignLeft)
        self._panel("AI transcription", "spark", c)

        # AI editor
        c = Card()
        c.lay.addWidget(label("AI editor", "H2"))
        c.lay.addWidget(label("The AI editor reads each video's transcript, picks the most viral self-contained moments "
                              "(with cuts and teaser openings), writes the hook headings and converts Urdu captions to "
                              "Roman Urdu. Gemini has a free tier (about 2 requests per video). Keys are stored only "
                              "on this PC.", "Muted", wrap=True))
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(12)
        self.gem_key = QLineEdit(getattr(s, "gemini_api_key", ""))
        self.gem_key.setEchoMode(QLineEdit.Password)
        self.gem_key.setPlaceholderText("AIza…")
        self.gem_model = QLineEdit(getattr(s, "gemini_model", "auto") or "auto")
        self.gem_model.setToolTip("auto = the best Flash model your key can use (falls back to Flash-Lite when the\n"
                                  "daily free limit is reached). Or type a model id.")
        getk = QPushButton(" Get a free Gemini key")
        getk.setIcon(theme.icon("key"))
        getk.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/apikey")))
        tg = QPushButton(" Test")
        tg.setIcon(theme.icon("check"))
        tg.clicked.connect(lambda: self.test_key("gemini"))
        gr = QWidget()
        gh = QHBoxLayout(gr)
        gh.setContentsMargins(0, 0, 0, 0)
        gh.addWidget(self.gem_key, 1)
        gh.addWidget(tg)
        gh.addWidget(getk)
        g.addWidget(field("Gemini API key (recommended, free)", gr), 0, 0)
        g.addWidget(field("Gemini model", self.gem_model), 0, 1)
        self.api_key = QLineEdit(getattr(s, "anthropic_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-ant-…  (optional, paid)")
        self.model = QLineEdit(getattr(s, "claude_model", "claude-sonnet-5"))
        tc = QPushButton(" Test")
        tc.setIcon(theme.icon("check"))
        tc.clicked.connect(lambda: self.test_key("claude"))
        cr = QWidget()
        ch = QHBoxLayout(cr)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.addWidget(self.api_key, 1)
        ch.addWidget(tc)
        g.addWidget(field("Anthropic (Claude) API key", cr), 1, 0)
        g.addWidget(field("Claude model", self.model), 1, 1)
        self.oai_key = QLineEdit(getattr(s, "openai_api_key", ""))
        self.oai_key.setEchoMode(QLineEdit.Password)
        self.oai_key.setPlaceholderText("sk-…  (optional, paid)")
        self.oai_model = QLineEdit(getattr(s, "openai_model", "auto") or "auto")
        to = QPushButton(" Test")
        to.setIcon(theme.icon("check"))
        to.clicked.connect(lambda: self.test_key("openai"))
        orow = QWidget()
        oh = QHBoxLayout(orow)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.addWidget(self.oai_key, 1)
        oh.addWidget(to)
        g.addWidget(field("OpenAI (ChatGPT) API key", orow), 2, 0)
        g.addWidget(field("ChatGPT model", self.oai_model), 2, 1)
        g.setColumnStretch(0, 3)
        g.setColumnStretch(1, 1)
        c.lay.addLayout(g)
        self.key_status = label("", "Small", wrap=True)
        c.lay.addWidget(self.key_status)
        self._panel("AI editor", "wand", c)

        # Audio
        c = Card()
        c.lay.addWidget(label("Audio", "H2"))
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        self.loud = Toggle("Normalize loudness for mobile (-14 LUFS)", s.loudnorm)
        g.addWidget(self.loud, 0, 0, 1, 2)
        self.music = QLineEdit(s.music_dir)
        g.addWidget(field("Music library folder (manage tracks on the Music page)",
                          _path_row(self.music, True, "Music folder")), 1, 0, 1, 2)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(getattr(s, "sfx_volume", 0.55) * 100))
        self.vol_lbl = label(f"{self.vol.value()}%", "Muted")
        self.vol.valueChanged.connect(lambda v: self.vol_lbl.setText(f"{v}%"))
        vr = QWidget()
        vh = QHBoxLayout(vr)
        vh.setContentsMargins(0, 0, 0, 0)
        vh.addWidget(self.vol, 1)
        vh.addWidget(self.vol_lbl)
        g.addWidget(field("Sound effects volume", vr), 2, 0, 1, 2)
        c.lay.addLayout(g)
        self._panel("Audio", "music", c)

        # YouTube account
        c = Card()
        c.lay.addWidget(label("YouTube account", "H2"))
        c.lay.addWidget(label("If YouTube says “Sign in to confirm you're not a bot”, sign in here once. The login "
                              "stays on this PC and is only used to download videos.", "Muted", wrap=True))
        yr = QHBoxLayout()
        self.yt_status = label("", "")
        yr.addWidget(self.yt_status, 1)
        b1 = QPushButton(" Sign in to YouTube")
        b1.setIcon(theme.icon("link"))
        b1.clicked.connect(self.yt_sign_in)
        b2 = QPushButton(" Import cookies.txt")
        b2.setIcon(theme.icon("file"))
        b2.clicked.connect(self.yt_import)
        b3 = QPushButton("Sign out")
        b3.setObjectName("Danger")
        b3.clicked.connect(self.yt_sign_out)
        for b in (b1, b2, b3):
            yr.addWidget(b)
        c.lay.addLayout(yr)
        self._panel("YouTube account", "link", c)
        self.update_yt()

        # Tools
        c = Card()
        c.lay.addWidget(label("Tools", "H2"))
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(12)
        self.ff = QLineEdit(s.ffmpeg_path)
        self.ff.setPlaceholderText("Auto-detect (leave empty)")
        g.addWidget(field("FFmpeg location (ffmpeg.exe or its folder)", _path_row(self.ff, False, "ffmpeg.exe")),
                    0, 0, 1, 2)
        self.ff_status = label("", "Small", wrap=True)
        g.addWidget(self.ff_status, 1, 0, 1, 2)
        tb = QPushButton(" Test FFmpeg")
        tb.setIcon(theme.icon("check"))
        tb.clicked.connect(self.test_ffmpeg)
        g.addWidget(tb, 2, 0, Qt.AlignLeft)
        self.cookies = _combo([("", "Don't use browser cookies"), ("chrome", "Chrome"), ("edge", "Edge"),
                               ("firefox", "Firefox"), ("brave", "Brave")], s.cookies_browser)
        g.addWidget(field("Fallback: read cookies from a browser (Firefox works best; close Chrome/Edge first)",
                          self.cookies), 3, 0, 1, 2)
        c.lay.addLayout(g)
        fr = QHBoxLayout()
        self.font_lbl = label("", "Small", wrap=True)
        fb = QPushButton(" Download caption fonts")
        fb.setIcon(theme.icon("download"))
        fb.clicked.connect(self.get_fonts)
        fr.addWidget(fb)
        fr.addWidget(self.font_lbl, 1)
        c.lay.addLayout(fr)
        cr = QHBoxLayout()
        ob = QPushButton(" Open data folder")
        ob.setIcon(theme.icon("folder"))
        ob.clicked.connect(lambda: open_path(data_dir()))
        cb = QPushButton(" Clear download cache")
        cb.setIcon(theme.icon("trash", theme.BAD))
        cb.setObjectName("Danger")
        cb.clicked.connect(self.clear_cache)
        cr.addWidget(ob)
        cr.addWidget(cb)
        cr.addStretch(1)
        c.lay.addLayout(cr)
        self._panel("Tools", "settings", c)

        # Auto-watch channels
        c = Card()
        c.lay.addWidget(label("Auto-watch channels", "H2"))
        c.lay.addWidget(label("Add your channel links. While the app is open it checks them regularly, downloads every "
                              "new video and builds Shorts automatically with viral titles, descriptions and tags. "
                              "With Auto-upload on (Publish page) they're posted to your connected accounts too.",
                              "Muted", wrap=True))
        self.w_on = Toggle("Watch these channels for new videos", getattr(s, "watch_enabled", False))
        c.lay.addWidget(self.w_on)
        ar = QHBoxLayout()
        self.w_add = QLineEdit()
        self.w_add.setPlaceholderText("https://www.youtube.com/@yourchannel")
        self.w_add.returnPressed.connect(self._add_channel)
        ab = QPushButton(" Add")
        ab.setIcon(theme.icon("plus"))
        ab.clicked.connect(self._add_channel)
        rb = QPushButton(" Remove")
        rb.setObjectName("Danger")
        rb.clicked.connect(self._remove_channel)
        ar.addWidget(self.w_add, 1)
        ar.addWidget(ab)
        ar.addWidget(rb)
        c.lay.addLayout(ar)
        self.w_list = QListWidget()
        self.w_list.setObjectName("Box")
        self.w_list.setMinimumHeight(90)
        self.w_list.setMaximumHeight(160)
        for ch in getattr(s, "watch_channels", []) or []:
            self.w_list.addItem(ch)
        c.lay.addWidget(self.w_list)
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        self.w_int = QSpinBox()
        self.w_int.setRange(15, 1440)
        self.w_int.setSingleStep(15)
        self.w_int.setSuffix(" min")
        self.w_int.setValue(int(getattr(s, "watch_interval_min", 60)))
        self.w_first = QSpinBox()
        self.w_first.setRange(0, 10)
        self.w_first.setValue(int(getattr(s, "watch_first_n", 1)))
        self.w_per = QSpinBox()
        self.w_per.setRange(1, 10)
        self.w_per.setValue(int(getattr(s, "watch_per_check", 2)))
        g.addWidget(field("Check every", self.w_int), 0, 0)
        g.addWidget(field("When adding a channel, also build its newest", self.w_first), 0, 1)
        g.addWidget(field("Max new videos per channel per check", self.w_per), 0, 2)
        c.lay.addLayout(g)
        wr = QHBoxLayout()
        self.w_status = label("", "Small", wrap=True)
        cn = QPushButton(" Check now")
        cn.setIcon(theme.icon("refresh"))
        cn.clicked.connect(self._check_now)
        wr.addWidget(self.w_status, 1)
        wr.addWidget(cn)
        c.lay.addLayout(wr)
        self._panel("Auto-watch", "calendar", c)
        self.update_watch_status()
        self.w_on.toggled.connect(lambda _on: self._save_watch())

        # License
        c = Card()
        c.lay.addWidget(label("License", "H2"))
        c.lay.addWidget(label("Every Rebels Revolt Shorts download gets 3 free Shorts to try. After that, activate "
                              "a license key to keep going. It's tied to this PC.", "Muted", wrap=True))
        self.lic_status = label("Checking…", "")
        c.lay.addWidget(self.lic_status)
        lb = QPushButton(" Manage license")
        lb.setIcon(theme.icon("key"))
        lb.clicked.connect(self.manage_license)
        c.lay.addWidget(lb, 0, Qt.AlignLeft)
        self._panel("License", "key", c)
        self.update_license_status()
        self.cats.setCurrentRow(0)
        self.update_font_status()
        self._fw = None

    def _panel(self, name: str, icon: str, card):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(card)
        v.addStretch(1)
        self.panels.addWidget(page)
        it = QListWidgetItem(theme.icon(icon, theme.TEXT), "  " + name)
        it.setSizeHint(QSize(0, theme.S(42)))
        self.cats.addItem(it)

    def test_key(self, provider: str = "gemini"):
        from shortsforge.llm import test_key
        key = {"gemini": self.gem_key, "openai": self.oai_key}.get(provider, self.api_key).text().strip()
        model = {"gemini": self.gem_model, "openai": self.oai_model}.get(provider, self.model).text().strip() or \
            ("claude-sonnet-5" if provider == "claude" else "auto")
        if not key:
            self.key_status.setText("Paste a key first.")
            return
        self.key_status.setText("Testing…")
        self.key_status.setStyleSheet("")
        self.key_status.repaint()
        try:
            msg = test_key(provider, key, model)
            nice = {"gemini": "Gemini", "openai": "ChatGPT"}.get(provider, "Claude")
            self.key_status.setText(f"✓ {nice}: {msg}. Press Save settings.")
            self.key_status.setStyleSheet(f"color: {theme.GOOD};")
        except Exception as e:
            self.key_status.setText(f"✗ {e}")
            self.key_status.setStyleSheet(f"color: {theme.BAD};")

    def update_yt(self):
        on = cookies.has_login()
        self.yt_status.setText("✓ Signed in" if on else "Not signed in")
        self.yt_status.setStyleSheet(f"color: {theme.GOOD if on else theme.MUTED}; font-weight: 600;")

    def yt_sign_in(self):
        from .login_dialog import sign_in
        sign_in(self.window())
        self.update_yt()
        self.saved.emit()

    def yt_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if not path:
            return
        try:
            ok = cookies.import_file(path)
            QMessageBox.information(self, "Imported", "YouTube login imported ✓" if ok else
                                    "Imported, but no signed-in YouTube session was found in it.")
        except Exception as e:
            QMessageBox.warning(self, "Import failed", str(e))
        self.update_yt()
        self.saved.emit()

    def yt_sign_out(self):
        from shortsforge import browser_login
        browser_login.sign_out()
        self.update_yt()
        self.saved.emit()

    def update_license_status(self):
        if not licensing.enabled():
            self.lic_status.setText("Your own copy (run from the project folder): no trial limits. "
                                    "Installer builds you give to others use the license check.")
            self.lic_status.setStyleSheet(f"color: {theme.GOOD}; font-weight: 600;")
            return
        s = licensing.current_state()
        if s.status == "active":
            self.lic_status.setText(f"✓ Licensed{f' ({s.plan})' if s.plan else ''}")
            self.lic_status.setStyleSheet(f"color: {theme.GOOD}; font-weight: 600;")
        elif s.status in ("trial", "unknown"):
            self.lic_status.setText(f"Trial: {s.videos_left} of {s.videos_allowed} Shorts left")
            self.lic_status.setStyleSheet(f"color: {theme.TEXT if s.videos_left else theme.BAD}; font-weight: 600;")
        else:
            self.lic_status.setText("Trial used up")
            self.lic_status.setStyleSheet(f"color: {theme.BAD}; font-weight: 600;")

    def manage_license(self):
        from .license_dialog import show_license
        show_license(self.window())
        self.update_license_status()

    def update_font_status(self):
        miss = fonts.missing_fonts()
        self.font_lbl.setText("All caption fonts installed ✓" if not miss else
                              f"{len(miss)} fonts missing ({', '.join(miss[:4])}{'…' if len(miss) > 4 else ''}) — "
                              "Windows fallbacks are used until downloaded.")

    def get_fonts(self):
        self.font_lbl.setText("Downloading…")
        self._fw = FontWorker()
        self._fw.finished_with.connect(lambda _: self.update_font_status())
        self._fw.start()

    def test_ffmpeg(self):
        set_ffmpeg_override(self.ff.text().strip())
        try:
            path = find_ffmpeg()
            enc = pick_encoder(self.enc.currentData())
            self.ff_status.setText(f"✓ Found {path}\nEncoder in use: {enc}")
            self.ff_status.setStyleSheet(f"color: {theme.GOOD};")
        except ToolMissing as e:
            self.ff_status.setText(str(e))
            self.ff_status.setStyleSheet(f"color: {theme.BAD};")

    def clear_cache(self):
        if QMessageBox.question(self, "Clear cache", "Delete downloaded source videos and extracted audio? "
                                "(Existing Shorts stay; restyling them will need a new download.)") != QMessageBox.Yes:
            return
        for sub in ("downloads", "audio"):
            shutil.rmtree(CACHE_DIR / sub, ignore_errors=True)
            (CACHE_DIR / sub).mkdir(parents=True, exist_ok=True)
        QMessageBox.information(self, "Cache cleared", "Done.")

    # ------------------------------------------------------------------ auto-watch
    def _channels(self) -> list:
        return [self.w_list.item(i).text() for i in range(self.w_list.count())]

    def _add_channel(self):
        from shortsforge.autowatch import normalize
        u = normalize(self.w_add.text())
        if not u:
            return
        if "youtube.com" not in u and "youtu.be" not in u:
            QMessageBox.warning(self, "Channel link", "Paste a YouTube channel link, e.g. https://www.youtube.com/@name")
            return
        if u not in self._channels():
            self.w_list.addItem(u)
        self.w_add.clear()
        self._save_watch()

    def _remove_channel(self):
        it = self.w_list.currentItem()
        if it:
            from shortsforge.autowatch import forget
            forget(it.text())
            self.w_list.takeItem(self.w_list.row(it))
            self._save_watch()

    def _save_watch(self):
        s = self.s
        s.watch_enabled = self.w_on.isChecked()
        s.watch_channels = self._channels()
        s.watch_interval_min = self.w_int.value()
        s.watch_first_n = self.w_first.value()
        s.watch_per_check = self.w_per.value()
        s.save()
        self.watch_changed.emit()
        self.update_watch_status()

    def _check_now(self):
        self._save_watch()
        if not self._channels():
            QMessageBox.information(self, "Auto-watch", "Add a channel link first.")
            return
        self.check_now.emit()
        self.w_status.setText("Checking now… new videos appear in the queue on the Create page.")

    def update_watch_status(self, text: str = ""):
        import time as _t
        from shortsforge.autowatch import last_check
        lc = last_check()
        when = _t.strftime("%d %b %H:%M", _t.localtime(lc)) if lc else "never"
        state = "On" if self.w_on.isChecked() else "Off"
        self.w_status.setText(text or f"{state} · {len(self._channels())} channel(s) · last check: {when}")

    def save(self):
        s = self.s
        from shortsforge.config import usable_output_dir
        wanted = self.out.text().strip() or s.output_dir
        base, moved = usable_output_dir(wanted)
        if moved:
            QMessageBox.warning(self, "Folder not writable",
                                f"Windows won't allow new files in:\n{wanted}\n\n(This happens with protected folders "
                                f"like Videos/Documents.) Shorts will be saved to:\n{base}")
            self.out.setText(str(base))
        s.output_dir = str(base)
        s.encoder = self.enc.currentData()
        s.quality_crf = self.crf.value()
        s.fps = int(self.fps.currentData())
        s.source_quality = int(self.srcq.currentData())
        s.min_gap = self.gap.value()
        s.use_gpu = self.gpu.isChecked()
        s.loudnorm = self.loud.isChecked()
        s.music_dir = self.music.text().strip()
        s.sfx_volume = self.vol.value() / 100
        s.ffmpeg_path = self.ff.text().strip()
        s.cookies_browser = self.cookies.currentData()
        s.anthropic_api_key = self.api_key.text().strip()
        s.gemini_api_key = self.gem_key.text().strip()
        s.gemini_model = self.gem_model.text().strip() or "auto"
        if s.gemini_api_key and not getattr(s, "anthropic_api_key", "") and s.clip_picker == "local":
            s.clip_picker = "gemini"
        s.claude_model = self.model.text().strip() or "claude-sonnet-5"
        s.openai_api_key = self.oai_key.text().strip()
        s.openai_model = self.oai_model.text().strip() or "auto"
        self._save_watch()
        set_ffmpeg_override(s.ffmpeg_path)
        s.save()
        self.saved.emit()
        QMessageBox.information(self, "Saved", "Settings saved.")
