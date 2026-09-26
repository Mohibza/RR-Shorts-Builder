"""Music: your background-music library + in-app search/download of free music.

Tabs:
  * My library - tracks in the music folder (star, preview, remove), how music is used, volume.
  * Find free music - search Openverse (Creative Commons, no key) or Jamendo (free key), preview, one-click
    download. Pixabay Music, YouTube Audio Library, Mixkit and Free Music Archive open in the app's
    browser and every download lands in the library automatically.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QFileSystemWatcher, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton,
                               QSlider, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from shortsforge import browser_login, music_sources
from shortsforge.config import Settings
from shortsforge.music_sources import BROWSER_SOURCES, JAMENDO_KEY_URL, Track
from shortsforge.utils import open_path

from . import theme
from .widgets import Card, Toggle, label

MUSIC_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}
MOODS = ["Lofi", "Upbeat", "Cinematic", "Motivational", "Chill", "Corporate", "Emotional"]

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False


def _length(path: Path) -> float:
    try:
        import mutagen
        return float(mutagen.File(str(path)).info.length)
    except Exception:
        return 0.0


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}" if sec else "–"


class SearchWorker(QThread):
    results = Signal(list, bool, int)      # tracks, more, page
    failed = Signal(str)

    def __init__(self, source: str, query: str, page: int, safe: bool, jamendo_id: str):
        super().__init__()
        self.a = (source, query, page, safe, jamendo_id)

    def run(self):
        source, query, page, safe, jid = self.a
        try:
            tracks, more = music_sources.search(source, query, page, safe, jid)
            # Shorts need a real music bed: skip tiny clips/jingles
            tracks = [t for t in tracks if not (0 < t.duration < 25)]
            self.results.emit(tracks, more, page)
        except Exception as e:
            self.failed.emit(str(e))


class DownloadWorker(QThread):
    progress = Signal(str, float)
    done = Signal(str, str)                # track id, saved path
    failed = Signal(str, str)

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder
        self.queue: list[Track] = []

    def run(self):
        while self.queue:
            t = self.queue.pop(0)
            try:
                p = music_sources.download(t, self.folder, lambda f, i=t.id: self.progress.emit(i, f))
                self.done.emit(t.id, str(p))
            except Exception as e:
                self.failed.emit(t.id, str(e))


class MusicPage(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._lengths: dict[str, float] = {}
        self._browser_src: Optional[str] = None
        self._known: set[str] = set()
        self._results: dict[str, Track] = {}
        self._star_after: set[str] = set()
        self._search: Optional[SearchWorker] = None
        self._dl: Optional[DownloadWorker] = None
        self._page = 1
        self._query = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 18)
        root.setSpacing(14)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("Music", "H1"))
        tv.addWidget(label("Background tracks for your Shorts. Find free music below: tracks download straight into "
                           "your library and the licence is saved with each one.", "Muted", wrap=True))
        root.addLayout(tv)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_library(), "  My library  ")
        self.tabs.addTab(self._build_find(), "  Find free music  ")
        root.addWidget(self.tabs, 1)

        self.player = None
        if HAVE_MM:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(0.8)
            self.player.setAudioOutput(self.audio)
            self.player.playbackStateChanged.connect(self._state)
            self.player.errorOccurred.connect(lambda *_: self._set_now("Couldn't play this preview."))

        self.watcher = QFileSystemWatcher(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(1200)   # downloads write in chunks: wait until they settle
        self._debounce.timeout.connect(self.refill)
        self.watcher.directoryChanged.connect(lambda _p: self._debounce.start())
        self._watch()
        self._known = {p.name for p in self.tracks()}
        self.refill()

    # ================================================================== library tab
    def _build_library(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(14)

        c = Card(pad=18)
        row = QHBoxLayout()
        row.setSpacing(28)
        col1 = QVBoxLayout()
        col1.addWidget(label("USE IN SHORTS", "Section"))
        self.t_use = Toggle("Add background music", self.s.add_music)
        self.t_use.toggled.connect(self._save)
        col1.addWidget(self.t_use)
        self.mode_all = QRadioButton("Random track from the whole library")
        self.mode_star = QRadioButton("Only starred tracks")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_all)
        grp.addButton(self.mode_star)
        (self.mode_star if getattr(self.s, "music_mode", "random") == "starred" else self.mode_all).setChecked(True)
        self.mode_all.toggled.connect(self._save)
        col1.addWidget(self.mode_all)
        col1.addWidget(self.mode_star)
        row.addLayout(col1, 1)
        col2 = QVBoxLayout()
        col2.addWidget(label("LEVEL", "Section"))
        self.t_auto = Toggle("Auto level (recommended)", getattr(self.s, "music_auto", True))
        self.t_auto.setToolTip("Matches every track's loudness to sit well under the voice, then ducks it further\n"
                               "while someone is speaking. The slider below fine-tunes it.")
        self.t_auto.toggled.connect(self._save)
        col2.addWidget(self.t_auto)
        vr = QHBoxLayout()
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(2, 40)
        self.vol.setValue(int(round(self.s.music_volume * 100)))
        self.vol_lbl = label("", "Muted")
        self.vol.valueChanged.connect(self._vol_changed)
        self.vol.sliderReleased.connect(self._save)
        vr.addWidget(label("Volume", "Small"))
        vr.addWidget(self.vol, 1)
        vr.addWidget(self.vol_lbl)
        col2.addLayout(vr)
        col2.addWidget(label("Music always dips automatically while someone is talking.", "Small"))
        row.addLayout(col2, 1)
        c.lay.addLayout(row)
        root.addWidget(c)
        self._vol_changed(self.vol.value())

        tb = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your tracks…")
        self.search.textChanged.connect(self.refill)
        tb.addWidget(self.search, 1)
        for text, ic, fn in ((" Add files", "plus", self.add_files), (" Open folder", "folder", self.open_folder),
                             ("", "refresh", self.refill)):
            b = QPushButton(text)
            b.setIcon(theme.icon(ic))
            if not text:
                b.setToolTip("Refresh")
                b.setFixedWidth(40)
            b.clicked.connect(fn)
            tb.addWidget(b)
        root.addLayout(tb)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "TRACK", "SOURCE · LICENCE", "LENGTH", "SIZE"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 36)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.resizeSection(2, 240)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.play(it))
        self.tree.itemClicked.connect(self._clicked)
        self.tree.currentItemChanged.connect(lambda *_: self._show_credit())
        root.addWidget(self.tree, 1)

        pr = QHBoxLayout()
        self.play_btn = QPushButton("  Preview")
        self.play_btn.setIcon(theme.icon("play"))
        self.play_btn.clicked.connect(lambda: self.play(self.tree.currentItem()))
        self.star_btn = QPushButton("  Star")
        self.star_btn.setIcon(theme.icon("star"))
        self.star_btn.clicked.connect(lambda: self.toggle_star(self.tree.currentItem()))
        self.credit_btn = QPushButton("  Copy credit")
        self.credit_btn.setIcon(theme.icon("copy"))
        self.credit_btn.setToolTip("Copy the attribution line for this track (added to Short descriptions automatically)")
        self.credit_btn.clicked.connect(self._copy_credit)
        self.del_btn = QPushButton("  Remove")
        self.del_btn.setObjectName("Danger")
        self.del_btn.setIcon(theme.icon("trash", theme.BAD))
        self.del_btn.clicked.connect(self.remove)
        self.now = label("", "Small")
        for b in (self.play_btn, self.star_btn, self.credit_btn, self.del_btn):
            pr.addWidget(b)
        pr.addSpacing(12)
        pr.addWidget(self.now, 1)
        self.count_lbl = label("", "Small")
        pr.addWidget(self.count_lbl)
        root.addLayout(pr)
        return w

    # ================================================================== find tab
    def _build_find(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(12)

        sr = QHBoxLayout()
        self.src = QComboBox()
        self.src.addItem("Openverse · Creative Commons (no key)", "openverse")
        self.src.addItem("Jamendo · 600k indie tracks (free key)", "jamendo")
        self.src.setToolTip("Openverse searches Creative-Commons music from Jamendo, ccMixter, Freesound,\n"
                            "Wikimedia Commons and more, with no sign-up.\nJamendo searches its own catalogue "
                            "directly (needs a free client ID).")
        self.src.setMinimumWidth(220)
        self.src.currentIndexChanged.connect(self._src_changed)
        sr.addWidget(self.src)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search a mood or genre: lofi, upbeat, cinematic, corporate…")
        self.q.returnPressed.connect(lambda: self.do_search(1))
        sr.addWidget(self.q, 1)
        go = QPushButton("  Search")
        go.setObjectName("Primary")
        go.setIcon(theme.icon("search", "#FFFFFF"))
        go.clicked.connect(lambda: self.do_search(1))
        sr.addWidget(go)
        root.addLayout(sr)

        mr = QHBoxLayout()
        mr.setSpacing(6)
        for m in MOODS:
            b = QPushButton(m)
            b.setObjectName("Ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, mm=m: (self.q.setText(mm.lower()), self.do_search(1)))
            mr.addWidget(b)
        mr.addStretch(1)
        self.t_safe = Toggle("Monetization-safe only", getattr(self.s, "music_safe_only", True))
        self.t_safe.setToolTip("Only CC0, Public Domain, CC BY and CC BY-SA: allowed on monetized channels and\n"
                               "allowed to be put into a video. Hides NonCommercial (NC) and NoDerivatives (ND).")
        self.t_safe.toggled.connect(self._safe_changed)
        mr.addWidget(self.t_safe)
        root.addLayout(mr)

        self.jam_row = QWidget()
        jr = QHBoxLayout(self.jam_row)
        jr.setContentsMargins(0, 0, 0, 0)
        self.jam_key = QLineEdit(getattr(self.s, "jamendo_client_id", ""))
        self.jam_key.setPlaceholderText("Paste your Jamendo Client ID")
        self.jam_key.editingFinished.connect(self._save_jam)
        get = QPushButton("Get a free key")
        get.setObjectName("Link")
        get.setCursor(Qt.PointingHandCursor)
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(JAMENDO_KEY_URL)))
        jr.addWidget(label("Jamendo Client ID", "Small"))
        jr.addWidget(self.jam_key, 1)
        jr.addWidget(get)
        jr.addWidget(label("Sign up → My apps → Add app → copy the Client ID", "Small"))
        self.jam_row.setVisible(False)
        root.addWidget(self.jam_row)

        self.res = QTreeWidget()
        self.res.setHeaderLabels(["", "TITLE", "ARTIST", "LENGTH", "LICENCE", "FROM"])
        self.res.setRootIsDecorated(False)
        self.res.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.res.setUniformRowHeights(True)
        h = self.res.header()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 36)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.resizeSection(2, 170)
        for i in (3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Interactive)
        h.resizeSection(5, 170)
        self.res.itemDoubleClicked.connect(lambda it, _c: self.preview_remote(it))
        root.addWidget(self.res, 1)

        br = QHBoxLayout()
        self.r_play = QPushButton("  Preview")
        self.r_play.setIcon(theme.icon("play"))
        self.r_play.clicked.connect(lambda: self.preview_remote(self.res.currentItem()))
        self.r_add = QPushButton("  Download && add")
        self.r_add.setObjectName("Primary")
        self.r_add.setIcon(theme.icon("download", "#FFFFFF"))
        self.r_add.clicked.connect(lambda: self.download_selected(star=False))
        self.r_star = QPushButton("  Download && star")
        self.r_star.setIcon(theme.icon("star"))
        self.r_star.setToolTip("Download and star it (use with “Only starred tracks”)")
        self.r_star.clicked.connect(lambda: self.download_selected(star=True))
        self.r_page = QPushButton("  Open page")
        self.r_page.setIcon(theme.icon("globe"))
        self.r_page.clicked.connect(self.open_track_page)
        for b in (self.r_play, self.r_add, self.r_star, self.r_page):
            br.addWidget(b)
        self.r_status = label("Search to find free music. Double-click a result to preview it.", "Small", wrap=True)
        br.addSpacing(10)
        br.addWidget(self.r_status, 1)
        self.more = QPushButton("Load more")
        self.more.setVisible(False)
        self.more.clicked.connect(lambda: self.do_search(self._page + 1))
        br.addWidget(self.more)
        root.addLayout(br)
        self.dl_bar = QProgressBar()
        self.dl_bar.setRange(0, 1000)
        self.dl_bar.setVisible(False)
        root.addWidget(self.dl_bar)

        c = Card(pad=16)
        c.lay.addWidget(label("MORE FREE SOURCES", "Section"))
        c.lay.addWidget(label("These sites have no public music search API, so they open in your RR Shorts Builder "
                              "browser. Click Download on any track there and it lands in My library "
                              "automatically.", "Small", wrap=True))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        for i, (key, (name, _url, note, _credit)) in enumerate(BROWSER_SOURCES.items()):
            b = QPushButton(f"  {name}")
            b.setIcon(theme.icon("globe"))
            b.setToolTip(note)
            b.clicked.connect(lambda _=False, k=key: self.open_source(k))
            g.addWidget(b, 0, i)
            g.addWidget(label(note.split(":", 1)[-1].strip(), "Small", wrap=True), 1, i, Qt.AlignTop)
        c.lay.addLayout(g)
        root.addWidget(c)
        return w

    # ================================================================== library logic
    def folder(self) -> Path:
        p = Path(self.s.music_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p

    def _watch(self):
        for d in self.watcher.directories():
            self.watcher.removePath(d)
        if self.folder().exists():
            self.watcher.addPath(str(self.folder()))

    def tracks(self) -> list[Path]:
        try:
            return sorted((p for p in self.folder().iterdir() if p.suffix.lower() in MUSIC_EXT),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []

    def refill(self, *_):
        all_tracks = self.tracks()
        names = {p.name for p in all_tracks}
        new = names - self._known
        if new and self._browser_src:
            music_sources.tag_new_downloads(str(self.folder()), sorted(new), self._browser_src)
        self._known = names
        meta = music_sources.load_meta(str(self.folder()))
        q = self.search.text().lower().strip()
        starred = set(getattr(self.s, "music_selected", []) or [])
        cur = self.tree.currentItem().data(1, Qt.UserRole) if self.tree.currentItem() else None
        self.tree.clear()
        for p in all_tracks:
            m = meta.get(p.name) or {}
            if q and q not in p.stem.lower() and q not in (m.get("artist", "") + m.get("source", "")).lower():
                continue
            if p.name not in self._lengths:
                self._lengths[p.name] = _length(p)
            src = " · ".join(x for x in (m.get("source", ""), m.get("license", "")) if x) or "Your file"
            try:
                size = f"{p.stat().st_size / 1048576:.1f} MB"
            except OSError:
                continue
            it = QTreeWidgetItem(["", p.stem, src, _fmt(self._lengths[p.name]), size])
            it.setData(1, Qt.UserRole, str(p))
            tip = m.get("credit") or m.get("note") or ""
            if tip:
                it.setToolTip(2, ("Credit (added to descriptions): " + tip) if m.get("credit") else tip)
            if p.name in starred:
                it.setIcon(0, theme.icon("star", theme.WARN, 16, fill=True))
            self.tree.addTopLevelItem(it)
            if str(p) == cur:
                self.tree.setCurrentItem(it)
        total = len(all_tracks)
        self.count_lbl.setText(f"{total} tracks · {len(starred & names)} starred" if total else
                               "No tracks yet. Open “Find free music” or click “Add files”.")
        self._mark_downloaded()
        self._show_credit()
        self.changed.emit()

    def _clicked(self, it, col):
        if col == 0:
            self.toggle_star(it)

    def _star(self, name: str, on: bool):
        sel = list(getattr(self.s, "music_selected", []) or [])
        if on and name not in sel:
            sel.append(name)
        elif not on and name in sel:
            sel.remove(name)
        self.s.music_selected = sel
        self.s.save()

    def toggle_star(self, it):
        if not it:
            return
        name = Path(it.data(1, Qt.UserRole)).name
        self._star(name, name not in (getattr(self.s, "music_selected", []) or []))
        self.refill()

    def _current_meta(self) -> dict:
        it = self.tree.currentItem()
        if not it:
            return {}
        p = Path(it.data(1, Qt.UserRole))
        return music_sources.track_info(str(p.parent), p.name)

    def _show_credit(self):
        self.credit_btn.setEnabled(bool(self._current_meta().get("credit")))

    def _copy_credit(self):
        c = self._current_meta().get("credit", "")
        if c:
            QGuiApplication.clipboard().setText(c)
            self._set_now("Credit line copied.")

    def _set_now(self, text: str):
        self.now.setText(text)
        self.r_status.setText(text)

    def play(self, it):
        if not it or not self.player:
            return
        path = it.data(1, Qt.UserRole)
        if self.player.source().toLocalFile() == path and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.now.setText(f"Playing: {Path(path).stem}")

    def _state(self, st):
        playing = st == QMediaPlayer.PlayingState
        for b in (self.play_btn, self.r_play):
            b.setIcon(theme.icon("pause" if playing else "play"))
            b.setText("  Pause" if playing else "  Preview")
        if not playing and st == QMediaPlayer.StoppedState:
            self.now.setText("")

    def stop(self):
        if self.player:
            self.player.stop()

    def remove(self):
        it = self.tree.currentItem()
        if not it:
            return
        p = Path(it.data(1, Qt.UserRole))
        if QMessageBox.question(self, "Remove track", f"Delete “{p.stem}” from your music library?") != QMessageBox.Yes:
            return
        if self.player and self.player.source().toLocalFile() == str(p):
            self.player.stop()
            self.player.setSource(QUrl())
        try:
            p.unlink()
        except OSError as e:
            QMessageBox.warning(self, "Couldn't delete", str(e))
        self.refill()

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add music", "", "Audio (*.mp3 *.m4a *.wav *.ogg *.aac *.flac)")
        for f in files:
            try:
                shutil.copy2(f, self.folder() / Path(f).name)
            except OSError:
                pass
        self.refill()

    def open_folder(self):
        open_path(str(self.folder()))

    # ================================================================== find logic
    def _src_changed(self, *_):
        self.jam_row.setVisible(self.src.currentData() == "jamendo")

    def _save_jam(self):
        self.s.jamendo_client_id = self.jam_key.text().strip()
        self.s.save()

    def _safe_changed(self, on: bool):
        self.s.music_safe_only = on
        self.s.save()
        if self._query or self.res.topLevelItemCount():
            self.do_search(1)

    def do_search(self, page: int = 1):
        if self._search and self._search.isRunning():
            return
        src = self.src.currentData()
        if src == "jamendo":
            self._save_jam()
            if not self.s.jamendo_client_id:
                self.jam_row.setVisible(True)
                self.r_status.setText("Jamendo needs a free Client ID. Click “Get a free key”, or use Openverse "
                                      "(no key).")
                self.jam_key.setFocus()
                return
        self._query = self.q.text().strip()
        if page == 1:
            self.res.clear()
            self._results.clear()
        self.r_status.setText("Searching…")
        self.more.setVisible(False)
        self._search = SearchWorker(src, self._query, page, self.t_safe.isChecked(), self.s.jamendo_client_id)
        self._search.results.connect(self._got_results)
        self._search.failed.connect(lambda m: self.r_status.setText(m))
        self._search.start()

    def _got_results(self, tracks: list, more: bool, page: int):
        self._page = page
        for t in tracks:
            if t.id in self._results:
                continue
            self._results[t.id] = t
            it = QTreeWidgetItem(["", t.title, t.artist, _fmt(t.duration), t.license, t.source])
            it.setData(1, Qt.UserRole, t.id)
            it.setToolTip(1, ", ".join(x for x in t.tags if x) or t.title)
            it.setToolTip(4, ("Needs a credit line (added to your descriptions automatically)"
                              if t.needs_credit else "No credit needed") +
                          ("" if t.monetization_ok else "\n⚠ Not for monetized channels (NonCommercial/NoDerivatives)"))
            if not t.monetization_ok:
                it.setForeground(4, theme_color(theme.WARN))
            self.res.addTopLevelItem(it)
        n = self.res.topLevelItemCount()
        self.r_status.setText(f"{n} tracks. Double-click to preview, then Download & add." if n else
                              "Nothing found. Try another word (e.g. “piano”, “beat”, “ambient”).")
        self.more.setVisible(more and bool(tracks))
        self._mark_downloaded()

    def _downloaded_names(self) -> dict:
        meta = music_sources.load_meta(str(self.folder()))
        present = {p.name for p in self.tracks()}
        return {(m.get("title", ""), m.get("artist", "")): n for n, m in meta.items() if n in present}

    def _mark_downloaded(self):
        if not hasattr(self, "res"):
            return
        have = self._downloaded_names()
        for i in range(self.res.topLevelItemCount()):
            it = self.res.topLevelItem(i)
            t = self._results.get(it.data(1, Qt.UserRole))
            if t and (t.title, t.artist) in have:
                it.setIcon(0, theme.icon("check", theme.GOOD, 16))

    def _selected_tracks(self) -> list[Track]:
        return [self._results[i.data(1, Qt.UserRole)] for i in self.res.selectedItems()
                if i.data(1, Qt.UserRole) in self._results]

    def preview_remote(self, it):
        if not it or not self.player:
            return
        t = self._results.get(it.data(1, Qt.UserRole))
        if not t:
            return
        if self.player.source().toString() == t.preview_url and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        self.player.setSource(QUrl(t.preview_url))
        self.player.play()
        self.r_status.setText(f"Playing preview: {t.title} — {t.artist}")

    def open_track_page(self):
        for t in self._selected_tracks()[:3]:
            if t.page_url:
                QDesktopServices.openUrl(QUrl(t.page_url))

    def download_selected(self, star: bool = False):
        tracks = self._selected_tracks()
        if not tracks:
            self.r_status.setText("Select one or more tracks first.")
            return
        risky = [t for t in tracks if not t.monetization_ok]
        if risky and QMessageBox.question(
                self, "Licence check",
                f"{len(risky)} of these tracks are NonCommercial or NoDerivatives. They must not be used on a "
                "monetized channel.\n\nDownload anyway?") != QMessageBox.Yes:
            return
        if not self._dl or not self._dl.isRunning():
            self._dl = DownloadWorker(str(self.folder()))
            self._dl.progress.connect(self._dl_progress)
            self._dl.done.connect(self._dl_done)
            self._dl.failed.connect(self._dl_failed)
            self._dl.finished.connect(lambda: self.dl_bar.setVisible(False))
        for t in tracks:
            if star:
                self._star_after.add(t.id)
            self._dl.queue.append(t)
        self.dl_bar.setVisible(True)
        self.dl_bar.setValue(0)
        self.r_status.setText(f"Downloading {len(tracks)} track{'s' if len(tracks) > 1 else ''}…")
        if not self._dl.isRunning():
            self._dl.start()

    def _dl_progress(self, tid: str, f: float):
        self.dl_bar.setValue(int(f * 1000))

    def _dl_done(self, tid: str, path: str):
        t = self._results.get(tid)
        name = Path(path).name
        if tid in self._star_after:
            self._star(name, True)
            self._star_after.discard(tid)
        extra = "  Credit line saved: it's added to your Short descriptions." if t and t.needs_credit else ""
        self.r_status.setText(f"✓ Added “{Path(path).stem}” to My library.{extra}")
        self.refill()

    def _dl_failed(self, tid: str, msg: str):
        self._star_after.discard(tid)
        t = self._results.get(tid)
        self.r_status.setText(f"Couldn't download “{t.title if t else tid}”: {msg}")

    def open_source(self, key: str):
        name, url, note, _credit = BROWSER_SOURCES[key]
        try:
            browser_login.open_in_browser(url, str(self.folder()))
        except Exception as e:
            QMessageBox.warning(self, "Couldn't open the browser", str(e))
            return
        self._browser_src = key
        self._known = {p.name for p in self.tracks()}
        extra = {"youtube": "• Sign in with your channel account if asked (only the first time)\n",
                 "pixabay": "• Pixabay may ask you to log in (free) before downloading\n"}.get(key, "")
        QMessageBox.information(
            self, name,
            f"{name} is opening in your RR Shorts Builder browser.\n\n{extra}"
            "• Search and preview tracks there\n"
            "• Click Download on the ones you like: they appear in My library automatically\n\n"
            f"Licence: {note}.")
        self.tabs.setCurrentIndex(0)

    # ================================================================== settings
    def _vol_changed(self, v: int):
        if self.t_auto.isChecked():
            self.vol_lbl.setText("default" if v == 12 else (f"louder +{v - 12}" if v > 12 else f"quieter {v - 12}"))
        else:
            self.vol_lbl.setText(f"{v}%")

    def _save(self, *_):
        self.s.add_music = self.t_use.isChecked()
        self.s.music_mode = "starred" if self.mode_star.isChecked() else "random"
        self.s.music_auto = self.t_auto.isChecked()
        self.s.music_volume = self.vol.value() / 100
        self._vol_changed(self.vol.value())
        self.s.save()
        self.changed.emit()

    # kept for callers from older versions (main window "Manage music")
    def open_library(self):
        self.open_source("youtube")


def theme_color(hexstr: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(hexstr))
