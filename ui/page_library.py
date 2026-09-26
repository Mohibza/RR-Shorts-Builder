"""Library: browse every generated Short, preview it, copy metadata, restyle with one click."""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from shortsforge.captions import CAPTION_STYLES, CTA_STYLES, HOOK_STYLES
from shortsforge.config import Settings
from shortsforge.effects import COLOR_GRADES, INTROS, LAYOUTS, MOTIONS
from shortsforge.pipeline import PLAN_SUFFIX, StyleChoice, load_library
from shortsforge.utils import open_path

from . import theme
from .widgets import Card, field, label, rounded_pixmap
from .placement_dialog import PlacementDialog
from .workers import RestyleWorker

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # pragma: no cover
    HAVE_MM = False


def combo(items, current):
    c = QComboBox()
    for k, v in items:
        c.addItem(v, k)
    c.setCurrentIndex(max(0, c.findData(current)))
    return c


class AspectBox(QWidget):
    """Keeps its child at 9:16, as big as the space allows, centred (the Short preview)."""

    def __init__(self):
        super().__init__()
        self.child = None
        self.setMinimumSize(150, 266)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.child is None:
            return
        w, h = self.width(), self.height()
        cw = min(w, int(h * 9 / 16))
        ch = int(cw * 16 / 9)
        self.child.setGeometry((w - cw) // 2, (h - ch) // 2, cw, ch)


class LibraryPage(QWidget):
    changed = Signal()
    upload_requested = Signal(str)     # plan file

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.items: list[dict] = []
        self.current: dict | None = None
        self.worker = None
        self.place: dict = {}      # manual placement of the selected Short

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        root.addWidget(split)

        # ---- left: grid
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(30, 26, 18, 20)
        lv.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label("Library", "H1"))
        top.addStretch(1)
        self.count_lbl = label("", "Muted")
        top.addWidget(self.count_lbl)
        rf = QPushButton()
        rf.setIcon(theme.icon("refresh"))
        rf.setToolTip("Refresh")
        rf.setFixedSize(38, 38)
        rf.clicked.connect(self.reload)
        top.addWidget(rf)
        of = QPushButton(" Output folder")
        of.setIcon(theme.icon("folder"))
        of.clicked.connect(self.open_output)
        top.addWidget(of)
        lv.addLayout(top)
        fl = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles…")
        self.search.textChanged.connect(self.refill)
        self.src_filter = QComboBox()
        self.src_filter.currentIndexChanged.connect(self.refill)
        fl.addWidget(self.search, 2)
        fl.addWidget(self.src_filter, 3)
        lv.addLayout(fl)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(144, 256))
        self.grid.setGridSize(QSize(168, 320))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(6)
        self.grid.currentItemChanged.connect(self._selected)
        lv.addWidget(self.grid, 1)
        self.empty = label("No Shorts yet — generate some on the Create page.", "Muted")
        lv.addWidget(self.empty)
        split.addWidget(left)

        # ---- right: preview (scales with the window, always 9:16) + inspector tabs; nothing scrolls
        right = QWidget()
        rh = QHBoxLayout(right)
        rh.setContentsMargins(10, 20, 22, 18)
        rh.setSpacing(16)

        pv = Card(pad=12)
        self.vbox = AspectBox()
        if HAVE_MM:
            self.video = QVideoWidget(self.vbox)
            self.video.setStyleSheet("background: black;")
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(0.8)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            self.player.setLoops(QMediaPlayer.Infinite)
        else:
            self.video = label("Preview not available")
            self.video.setParent(self.vbox)
            self.player = None
        self.vbox.child = self.video
        pv.lay.addWidget(self.vbox, 1)
        ctr = QHBoxLayout()
        ctr.setSpacing(6)
        self.play_btn = QPushButton()
        self.play_btn.setIcon(theme.icon("pause"))
        self.play_btn.setFixedSize(40, 36)
        self.play_btn.clicked.connect(self.toggle_play)
        ctr.addStretch(1)
        ctr.addWidget(self.play_btn)
        for txt, ic, fn, tip in ((" Placement", "move", None, "Move captions, hook, end card, watermark or re-frame "
                                                              "the video (live preview)"),
                                 ("", "play", self.open_file, "Open in your video player"),
                                 ("", "folder", self.open_folder, "Show in folder"),
                                 ("", "trash", self.delete, "Delete this Short")):
            b = QPushButton(txt)
            b.setIcon(theme.icon(ic, theme.BAD if ic == "trash" else theme.TEXT))
            b.setToolTip(tip)
            if ic == "trash":
                b.setObjectName("Danger")
            if not txt:
                b.setFixedSize(40, 36)
            b.clicked.connect(fn or self.edit_placement)
            ctr.addWidget(b)
        ctr.addStretch(1)
        pv.lay.addLayout(ctr)
        rh.addWidget(pv, 1)

        # inspector
        self.insp = QTabWidget()
        self.insp.setMinimumWidth(theme.S(340))
        self.insp.setMaximumWidth(theme.S(460))

        meta = QWidget()
        ml = QVBoxLayout(meta)
        ml.setContentsMargins(2, 14, 2, 0)
        ml.setSpacing(12)
        def copy_btn(fn, tip):
            b = QPushButton()
            b.setIcon(theme.icon("copy"))
            b.setToolTip(tip)
            b.setFixedSize(38, 36)
            b.clicked.connect(fn)
            return b

        self.up_title = QLineEdit()
        self.up_title.setPlaceholderText("Viral upload title")
        self.up_title.setMaxLength(100)
        tr = QHBoxLayout()
        tr.addWidget(self.up_title, 1)
        tr.addWidget(copy_btn(lambda: self._copy(self.up_title.text()), "Copy title"))
        ml.addWidget(field("Upload title", _wrap(tr)))
        self.desc = QPlainTextEdit()
        self.desc.setMinimumHeight(70)
        dr = QHBoxLayout()
        dr.addWidget(self.desc, 1)
        dr.addWidget(copy_btn(lambda: self._copy(self._full_description()), "Copy full description "
                                                                          "(with link, credit and hashtags)"),
                     0, Qt.AlignTop)
        ml.addWidget(field("Description (the full-video link, music credit and hashtags are added automatically)",
                           _wrap(dr)), 1)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("tag one, tag two, …")
        tg = QHBoxLayout()
        tg.addWidget(self.tags, 1)
        tg.addWidget(copy_btn(lambda: self._copy(self.tags.text()), "Copy tags"))
        ml.addWidget(field("Tags (comma separated)", _wrap(tg)))
        self.hashtags = QLineEdit()
        self.hashtags.setPlaceholderText("#shorts #viral")
        ml.addWidget(field("Hashtags", self.hashtags))
        sr = QHBoxLayout()
        self.save_meta_btn = QPushButton(" Save details")
        self.save_meta_btn.setIcon(theme.icon("check"))
        self.save_meta_btn.clicked.connect(self.save_details)
        self.queue_btn = QPushButton(" Upload…")
        self.queue_btn.setIcon(theme.icon("upload"))
        self.queue_btn.setToolTip("Add this Short to the upload queue (Publish page)")
        self.queue_btn.clicked.connect(self._queue_upload)
        sr.addWidget(self.save_meta_btn)
        sr.addStretch(1)
        sr.addWidget(self.queue_btn)
        ml.addLayout(sr)
        self.info = label("", "Small", wrap=True)
        ml.addWidget(self.info)
        self.insp.addTab(meta, "Upload details")

        st = QWidget()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(2, 14, 2, 0)
        sl.setSpacing(12)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        self.r_cap = combo([(k, v["name"]) for k, v in CAPTION_STYLES.items()], "hormozi")
        self.r_hook = combo([("", "No hook title")] + [(k, v["name"]) for k, v in HOOK_STYLES.items()], "")
        self.r_cta = combo([("", "No end card")] + [(k, v["name"]) for k, v in CTA_STYLES.items()], "")
        self.r_grade = combo([(k, v["name"]) for k, v in COLOR_GRADES.items()], "none")
        self.r_motion = combo(list(MOTIONS.items()), "none")
        self.r_intro = combo(list(INTROS.items()), "none")
        self.r_layout = combo([(k, v) for k, v in LAYOUTS.items()], "auto")
        self.r_pos = combo([("lower", "Lower third"), ("middle", "Center"), ("upper", "Upper")], "lower")
        for i, (t, w) in enumerate((("Captions", self.r_cap), ("Hook title", self.r_hook), ("End card", self.r_cta),
                                    ("Color grade", self.r_grade), ("Motion", self.r_motion),
                                    ("Intro", self.r_intro), ("Framing", self.r_layout),
                                    ("Caption position", self.r_pos))):
            g.addWidget(field(t, w), i // 2, i % 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        sl.addLayout(g)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("On-screen hook heading")
        sl.addWidget(field("On-screen hook text (edit, then Re-render)", self.title_edit))
        pl = QPushButton("  Edit placement…")
        pl.setIcon(theme.icon("move"))
        pl.setToolTip("Move and resize the captions, hook heading, end card and watermark, or re-frame the\n"
                      "video, with a live preview. Then re-render.")
        pl.clicked.connect(self.edit_placement)
        self.place_lbl = label("", "Small")
        prow = QHBoxLayout()
        prow.addWidget(pl)
        prow.addWidget(self.place_lbl, 1)
        sl.addLayout(prow)
        sl.addStretch(1)
        br = QHBoxLayout()
        sur = QPushButton(" Surprise me")
        sur.setIcon(theme.icon("dice"))
        sur.clicked.connect(self.surprise)
        br.addWidget(sur)
        br.addStretch(1)
        sl.addLayout(br)
        self.insp.addTab(st, "Restyle")

        iv = QVBoxLayout()
        iv.setSpacing(10)
        iv.addWidget(self.insp, 1)
        self.apply_btn = QPushButton("  Re-render")
        self.apply_btn.setIcon(theme.icon("refresh", "#FFFFFF"))
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setToolTip("Render again with the title, style and placement shown")
        self.apply_btn.clicked.connect(self.apply)
        iv.addWidget(self.apply_btn)
        self.r_bar = QProgressBar()
        self.r_bar.setRange(0, 1000)
        self.r_bar.setVisible(False)
        iv.addWidget(self.r_bar)
        iw = QWidget()
        iw.setLayout(iv)
        iv.setContentsMargins(0, 0, 0, 0)
        iw.setMinimumWidth(theme.S(340))
        iw.setMaximumWidth(theme.S(460))
        rh.addWidget(iw, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        self.detail = right
        self.detail.setEnabled(False)

    # ------------------------------------------------------------------
    def reload(self, select_plan: str = ""):
        self.items = load_library(self.s.output_dir)
        srcs = {}
        for d in self.items:
            srcs.setdefault(d.get("source_title", "?"), 0)
            srcs[d.get("source_title", "?")] += 1
        cur = self.src_filter.currentData()
        self.src_filter.blockSignals(True)
        self.src_filter.clear()
        self.src_filter.addItem(f"All videos ({len(self.items)})", "")
        for k, n in srcs.items():
            self.src_filter.addItem(f"{k} ({n})", k)
        self.src_filter.setCurrentIndex(max(0, self.src_filter.findData(cur)))
        self.src_filter.blockSignals(False)
        self.refill(select_plan=select_plan)

    def refill(self, *_, select_plan: str = ""):
        q = self.search.text().lower().strip()
        src = self.src_filter.currentData() or ""
        keep = select_plan or (self.current or {}).get("plan_file", "")
        self.grid.blockSignals(True)
        self.grid.clear()
        shown = 0
        sel_item = None
        for d in self.items:
            if src and d.get("source_title") != src:
                continue
            title = d.get("meta", {}).get("title") or Path(d["output"]).stem
            if q and q not in title.lower() and q not in d.get("source_title", "").lower():
                continue
            it = QListWidgetItem(QIcon(rounded_pixmap(d.get("thumb", ""), 144, 256)),
                                 title if len(title) < 48 else title[:46] + "…")
            it.setData(Qt.UserRole, d)
            it.setToolTip(f"{title}\n{d.get('source_title', '')}")
            self.grid.addItem(it)
            shown += 1
            if d["plan_file"] == keep:
                sel_item = it
        self.grid.blockSignals(False)
        self.count_lbl.setText(f"{shown} Shorts")
        self.empty.setVisible(shown == 0)
        if sel_item:
            self.grid.setCurrentItem(sel_item)
        elif self.grid.count() and not self.current:
            self.grid.setCurrentRow(0)

    def select_plan(self, plan_file: str):
        self.reload(select_plan=plan_file)

    def _selected(self, it, _prev=None):
        if not it:
            return
        d = it.data(Qt.UserRole)
        self.current = d
        self.detail.setEnabled(True)
        m = d.get("meta", {})
        self.title_edit.setText(d.get("hook_text") or m.get("title", ""))
        self.up_title.setText(m.get("title", ""))
        body = m.get("body")
        if body is None:   # Shorts made before 1.5: strip the parts that are added automatically now
            body = "\n".join(ln for ln in m.get("description", "").splitlines()
                             if not ln.strip().startswith(("#", "▶", "🎵"))).strip()
        self.desc.setPlainText(body)
        self.tags.setText(", ".join(m.get("tags") or []))
        self.hashtags.setText(" ".join(m.get("hashtags") or ["#shorts"]))
        c = d["clip"]
        st = d.get("style", {})
        self.info.setText(
            f"From “{d.get('source_title', '')}” · {int(c['start'] // 60)}:{int(c['start'] % 60):02d}–"
            f"{int(c['end'] // 60)}:{int(c['end'] % 60):02d} ({c['end'] - c['start']:.0f}s) · "
            f"{(d['clip'].get('reasons') or {}).get('type', 'score')} · {c.get('score', 0):.1f}")
        for w, key in ((self.r_cap, "caption_style"), (self.r_hook, "hook_style"), (self.r_cta, "cta_style"),
                       (self.r_grade, "color_grade"), (self.r_motion, "motion"), (self.r_intro, "intro"),
                       (self.r_layout, "layout"), (self.r_pos, "position")):
            w.setCurrentIndex(max(0, w.findData(st.get(key) or "")))
        self.place = dict(st.get("place") or {})
        self._place_note()
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(d["output"]))
            self.player.play()
            self.play_btn.setIcon(theme.icon("pause"))

    def _meta_fields(self) -> tuple:
        tags = [x.strip() for x in self.tags.text().split(",") if x.strip()]
        hs = ["#" + h.lstrip("#") for h in self.hashtags.text().replace(",", " ").split() if h.strip("#")]
        return self.up_title.text().strip(), self.desc.toPlainText().strip(), tags, hs or ["#shorts"]

    def _full_description(self) -> str:
        from shortsforge import metadata
        title, body, tags, hs = self._meta_fields()
        d = self.current or {}
        return metadata.compose({"description": body, "hashtags": hs}, d.get("source", ""),
                                (d.get("meta") or {}).get("credit", ""))

    def save_details(self) -> bool:
        if not self.current:
            return False
        from shortsforge.pipeline import save_meta
        title, body, tags, hs = self._meta_fields()
        try:
            self.current["meta"] = save_meta(self.current["plan_file"], title, body, tags, hs)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't save", str(e))
            return False
        self.save_meta_btn.setText(" Saved ✓")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self.save_meta_btn.setText(" Save details"))
        return True

    def _queue_upload(self):
        if self.current and self.save_details():
            self.upload_requested.emit(self.current["plan_file"])

    def toggle_play(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setIcon(theme.icon("play"))
        else:
            self.player.play()
            self.play_btn.setIcon(theme.icon("pause"))

    def stop(self):
        if self.player:
            self.player.stop()

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)

    def open_output(self):
        from shortsforge.config import usable_output_dir
        base, moved = usable_output_dir(self.s.output_dir)
        if moved:
            self.s.output_dir = str(base)
            self.s.save()
        open_path(str(base))

    def open_file(self):
        if self.current:
            open_path(self.current["output"])

    def open_folder(self):
        if self.current:
            open_path(str(Path(self.current["output"]).parent))

    def delete(self):
        if not self.current:
            return
        if QMessageBox.question(self, "Delete Short", "Delete this Short from disk?") != QMessageBox.Yes:
            return
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        out = Path(self.current["output"])
        for p in (out, out.with_suffix(".jpg"), out.with_suffix(".txt"), Path(str(out)[:-4] + PLAN_SUFFIX)):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self.current = None
        self.detail.setEnabled(False)
        self.reload()

    def surprise(self):
        for w in (self.r_cap, self.r_hook, self.r_cta, self.r_grade, self.r_motion, self.r_intro):
            start = 1 if w in (self.r_hook, self.r_cta) else 0
            w.setCurrentIndex(random.randint(start, w.count() - 1))

    def _choice(self) -> StyleChoice:
        return StyleChoice(
            caption_style=self.r_cap.currentData(), hook_style=self.r_hook.currentData() or None,
            cta_style=self.r_cta.currentData() or None, color_grade=self.r_grade.currentData(),
            motion=self.r_motion.currentData(), intro=self.r_intro.currentData(),
            layout=self.r_layout.currentData(), position=self.r_pos.currentData(), place=dict(self.place))

    def _place_note(self):
        n = len(self.place)
        self.place_lbl.setText(f"{n} custom setting{'s' if n != 1 else ''}" if n else "Default positions")

    def edit_placement(self):
        if not self.current or (self.worker and self.worker.isRunning()):
            return
        if self.player:
            self.player.pause()
        dlg = PlacementDialog(self.s, self.current, self._choice(), self.title_edit.text().strip() or None, self)
        if dlg.exec() != PlacementDialog.Accepted:
            if self.player:
                self.player.play()
            return
        self.place = dict(dlg.place)
        self._place_note()
        if dlg.remember:
            self.s.placement = dict(dlg.place)
            self.s.save()
        self.apply()

    def apply(self):
        if not self.current or (self.worker and self.worker.isRunning()):
            return
        ch = self._choice()
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("  Rendering…")
        self.r_bar.setVisible(True)
        self.r_bar.setValue(0)
        self.worker = RestyleWorker(self.s.copy(), self.current["plan_file"], ch, self.title_edit.text().strip())
        self.worker.progress.connect(lambda f: self.r_bar.setValue(int(f * 1000)))
        self.worker.done.connect(self._restyled)
        self.worker.failed.connect(self._restyle_failed)
        self.worker.start()

    def _end_restyle(self):
        self.apply_btn.setEnabled(True)
        self.apply_btn.setText("  Re-render")
        self.r_bar.setVisible(False)

    def _restyled(self, r: dict):
        self._end_restyle()
        self.current = None
        self.reload(select_plan=r["plan_file"])
        self.changed.emit()

    def _restyle_failed(self, msg: str):
        self._end_restyle()
        QMessageBox.warning(self, "Re-render failed", msg)
        if self.current:
            self._selected(self.grid.currentItem())


def _wrap(layout) -> QWidget:
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w
