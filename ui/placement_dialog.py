"""Placement editor: move/resize captions, hook heading, end card, watermark and the video framing of a
finished Short, with a live preview frame rendered by the real engine. Drag the guides on the preview
or use the sliders, then Apply & Re-render."""
from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QPushButton,
                               QSlider, QVBoxLayout, QWidget)

from shortsforge.captions import H, HOOK_STYLES, POSITIONS, WM_POSITIONS
from shortsforge.config import Settings
from shortsforge.pipeline import Pipeline, StyleChoice

from . import theme
from .widgets import Card, label

PW, PH = 405, 720          # preview size (9:16)

GUIDES = [  # key, label, colour
    ("hook_y", "Hook", "#FF4D8D"),
    ("cap_y", "Captions", "#6D5CFF"),
    ("cta_y", "End card", "#2ECC71"),
]


class PreviewThread(QThread):
    ready = Signal(str, float)
    failed = Signal(str)

    def __init__(self, s: Settings, plan_file: str, choice: StyleChoice, hook: Optional[str], t: float, out: str):
        super().__init__()
        self.args = (s, plan_file, choice, hook, t, out)

    def run(self):
        s, pf, ch, hook, t, out = self.args
        try:
            png, dur = Pipeline(s, log=lambda m: None).preview(pf, ch, hook, t, out)
            self.ready.emit(png, dur)
        except Exception as e:
            self.failed.emit(str(e).strip().splitlines()[-1][:300] if str(e).strip() else repr(e))


class Canvas(QWidget):
    """Shows the preview frame with draggable horizontal guides."""
    moved = Signal(str, float)      # key, fraction of height

    def __init__(self):
        super().__init__()
        self.setMinimumSize(180, 320)
        self.setMouseTracking(True)
        self.pix: Optional[QPixmap] = None
        self.values: dict[str, float] = {}
        self.visible_keys: set[str] = set()
        self.drag: Optional[str] = None
        self.hover: Optional[str] = None
        self.busy = False
        self.message = "Rendering preview…"

    def set_pixmap(self, path: str):
        pm = QPixmap(path)
        self.pix = pm if not pm.isNull() else None
        self.update()

    def _key_at(self, y: float) -> Optional[str]:
        best = None
        for key, _l, _c in GUIDES:
            if key not in self.visible_keys or key not in self.values:
                continue
            d = abs(self.values[key] * self.height() - y)
            if d < 16 and (best is None or d < best[0]):
                best = (d, key)
        return best[1] if best else None

    def mousePressEvent(self, e):
        self.drag = self._key_at(e.position().y())

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self.drag:
            f = min(0.93, max(0.07, y / max(1, self.height())))
            self.values[self.drag] = f
            self.moved.emit(self.drag, f)
            self.update()
        else:
            k = self._key_at(y)
            if k != self.hover:
                self.hover = k
                self.setCursor(Qt.SizeVerCursor if k else Qt.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        PW, PH = self.width(), self.height()
        r = QRectF(0, 0, PW, PH)
        p.fillRect(r, QColor("#000000"))
        if self.pix:
            p.drawPixmap(r.toRect(), self.pix)
        else:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, self.message)
        # safe-zone hint: YouTube's buttons and title cover the right edge and the bottom
        p.fillRect(QRectF(PW - 58, PH * 0.45, 58, PH * 0.42), QColor(255, 255, 255, 18))
        p.fillRect(QRectF(0, PH * 0.87, PW, PH * 0.13), QColor(255, 255, 255, 18))
        f = QFont()
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        for key, text, colr in GUIDES:
            if key not in self.visible_keys or key not in self.values:
                continue
            y = self.values[key] * PH
            active = key in (self.drag, self.hover)
            c = QColor(colr)
            c.setAlpha(255 if active else 150)
            pen = QPen(c, 2 if active else 1.2, Qt.SolidLine if active else Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(0, y), QPointF(PW, y))
            tag = QRectF(6, y - 9, 70, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(tag, 9, 9)
            p.setPen(QColor("white"))
            p.drawText(tag, Qt.AlignCenter, "⇕ " + text)
        if self.busy:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(PW - 118, 10, 108, 26), 13, 13)
            p.setPen(QColor("white"))
            p.drawText(QRectF(PW - 118, 10, 108, 26), Qt.AlignCenter, "Updating…")
        p.end()


def _slider(lo: int, hi: int, val: int) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    return s


class CanvasBox(QWidget):
    """Keeps the preview canvas at 9:16, as large as the window allows."""

    def __init__(self, canvas: Canvas):
        super().__init__()
        self.canvas = canvas
        canvas.setParent(self)
        self.setMinimumSize(200, 356)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        cw = min(w, int(h * 9 / 16))
        ch = int(cw * 16 / 9)
        self.canvas.setGeometry((w - cw) // 2, (h - ch) // 2, cw, ch)


class PlacementDialog(QDialog):
    """Returns `.place` (dict) and `.remember` (use as default for new Shorts) when accepted."""

    def __init__(self, settings: Settings, plan: dict, choice: StyleChoice, hook_text: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit placement")
        self.setModal(True)
        # open big (like a pro editor), sized to this screen; everything fits without scrolling
        from PySide6.QtGui import QGuiApplication
        scr = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        ag = scr.availableGeometry()
        self.resize(max(900, int(ag.width() * 0.94)), max(600, int(ag.height() * 0.92)))
        self.move(ag.x() + (ag.width() - self.width()) // 2, ag.y() + (ag.height() - self.height()) // 2)
        self.s, self.plan, self.hook = settings, plan, hook_text
        self.base = choice
        self.place: dict = dict(choice.place or {})
        self.remember = False
        self.dur = float(plan.get("prep", {}).get("D") or (plan["clip"]["end"] - plan["clip"]["start"]))
        self.t = 1.0
        self._thread: Optional[PreviewThread] = None
        self._pending = False
        self._tmp = os.path.join(tempfile.gettempdir(), f"rr_place_{os.getpid()}.png")

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(20)

        # ---- left: preview
        lv = QVBoxLayout()
        lv.setSpacing(10)
        self.canvas = Canvas()
        self.canvas.moved.connect(self._dragged)
        lv.addWidget(CanvasBox(self.canvas), 1)
        tr = QHBoxLayout()
        self.t_slider = _slider(0, max(1, int(self.dur * 10) - 1), 10)
        self.t_slider.valueChanged.connect(self._time_changed)
        self.t_lbl = label("0:01", "Small")
        tr.addWidget(label("Time", "Small"))
        tr.addWidget(self.t_slider, 1)
        tr.addWidget(self.t_lbl)
        lv.addLayout(tr)
        jr = QHBoxLayout()
        for text, t in (("Hook", 1.0), ("Captions", min(self.dur - 0.5, 6.0)), ("End card", max(0.0, self.dur - 1.2))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, tt=t: self.t_slider.setValue(int(tt * 10)))
            jr.addWidget(b)
        lv.addLayout(jr)
        self.status = label("Drag the coloured guides, or use the sliders. Shaded areas are covered by "
                            "YouTube's buttons.", "Small", wrap=True)
        lv.addWidget(self.status)
        root.addLayout(lv, 5)

        # ---- right: controls
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(12)
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        rv.addWidget(label("Edit placement", "H1"))
        rv.addWidget(label("Changes show in the preview in about a second. Nothing is re-rendered until you "
                           "press Apply.", "Muted", wrap=True))

        self.sl: dict[str, QSlider] = {}
        self.vl: dict[str, object] = {}

        def row(grid: QGridLayout, r: int, key: str, text: str, lo: int, hi: int, val: int, fmt):
            s = _slider(lo, hi, val)
            v = label(fmt(val), "Small")
            v.setMinimumWidth(52)
            grid.addWidget(label(text, "Small"), r, 0)
            grid.addWidget(s, r, 1)
            grid.addWidget(v, r, 2)
            s.valueChanged.connect(lambda x, k=key, lab=v, f=fmt: (lab.setText(f(x)), self._slid(k, x)))
            self.sl[key] = s
            return s

        pct = lambda x: f"{x}%"       # noqa: E731
        size = lambda x: f"{x}%"      # noqa: E731

        # captions
        c = Card(pad=16)
        c.lay.addWidget(label("CAPTIONS", "Section"))
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        row(g, 0, "cap_y", "Height", 12, 90, int(self._default("cap_y") * 100), pct)
        row(g, 1, "cap_scale", "Size", 50, 150, int(self.place.get("cap_scale", 1.0) * 100), size)
        c.lay.addLayout(g)
        c.lay.addStretch(1)
        cards.addWidget(c, 0, 0)

        # hook
        c = Card(pad=16)
        c.lay.addWidget(label("HOOK HEADING", "Section"))
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        row(g, 0, "hook_y", "Height", 7, 80, int(self._default("hook_y") * 100), pct)
        row(g, 1, "hook_scale", "Size", 50, 150, int(self.place.get("hook_scale", 1.0) * 100), size)
        style_dur = (HOOK_STYLES.get(choice.hook_style or "", {}) or {}).get("dur") or 4.0
        row(g, 2, "hook_dur", "On screen", 10, 80, int(float(self.place.get("hook_dur") or style_dur) * 10),
            lambda x: f"{x / 10:.1f}s")
        c.lay.addLayout(g)
        if not choice.hook_style:
            c.lay.addWidget(label("This Short has no hook heading (pick one under Restyle).", "Small", wrap=True))
        c.lay.addStretch(1)
        cards.addWidget(c, 0, 1)

        # end card + watermark
        c = Card(pad=16)
        c.lay.addWidget(label("END CARD & WATERMARK", "Section"))
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        row(g, 0, "cta_y", "End card height", 15, 85, int(self._default("cta_y") * 100), pct)
        self.wm = QComboBox()
        for k, v in WM_POSITIONS.items():
            self.wm.addItem(v, k)
        self.wm.setCurrentIndex(max(0, self.wm.findData(self.place.get("wm_pos", "top"))))
        self.wm.currentIndexChanged.connect(lambda _i: self._set("wm_pos", self.wm.currentData()))
        g.addWidget(label("Watermark", "Small"), 1, 0)
        g.addWidget(self.wm, 1, 1, 1, 2)
        row(g, 2, "wm_scale", "Watermark size", 60, 160, int(self.place.get("wm_scale", 1.0) * 100), size)
        c.lay.addLayout(g)
        if not settings.watermark.strip():
            c.lay.addWidget(label("Set your watermark text (e.g. @yourchannel) on the Create page.", "Small",
                                  wrap=True))
        c.lay.addStretch(1)
        cards.addWidget(c, 1, 0)

        # framing
        c = Card(pad=16)
        c.lay.addWidget(label("VIDEO FRAMING", "Section"))
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        sign = lambda x: f"{x:+d}" if x else "0"    # noqa: E731
        row(g, 0, "frame_x", "Left / right", -100, 100, int(self.place.get("frame_x", 0) * 100), sign)
        row(g, 1, "frame_y", "Up / down", -100, 100, int(self.place.get("frame_y", 0) * 100), sign)
        row(g, 2, "frame_zoom", "Zoom", 100, 200, int(self.place.get("frame_zoom", 1.0) * 100), size)
        c.lay.addLayout(g)
        c.lay.addWidget(label("Face-follow still tracks the speaker; this nudges it. With “Full frame + blurred "
                              "background”, Up/down moves the video so captions can sit below it.", "Small", wrap=True))
        c.lay.addStretch(1)
        cards.addWidget(c, 1, 1)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        rv.addLayout(cards)

        self.keep = QCheckBox("Use these positions for new Shorts too")
        rv.addWidget(self.keep)
        rv.addStretch(1)

        br = QHBoxLayout()
        reset = QPushButton(" Reset")
        reset.setIcon(theme.icon("refresh"))
        reset.clicked.connect(self.reset)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("  Apply && Re-render")
        ok.setObjectName("Primary")
        ok.setIcon(theme.icon("check", "#FFFFFF"))
        ok.clicked.connect(self._accept)
        br.addWidget(reset)
        br.addStretch(1)
        br.addWidget(cancel)
        br.addWidget(ok)
        rv.addLayout(br)
        root.addWidget(right, 7)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.refresh)
        self._sync_canvas()
        self.canvas.busy = True
        QTimer.singleShot(50, self.refresh)

    # ------------------------------------------------------------------
    def _default(self, key: str) -> float:
        if key in self.place:
            return float(self.place[key])
        if key == "cap_y":
            return POSITIONS.get(self.base.position, 0.67)
        if key == "hook_y":
            st = HOOK_STYLES.get(self.base.hook_style or "", {}) or {}
            return (st.get("y", 300) - 10 + int(st.get("size", 120) * 0.5)) / H   # ~2-line heading centre
        if key == "cta_y":
            return 0.42
        return 0.5

    def _sync_canvas(self):
        self.canvas.values = {k: self._default(k) for k, _l, _c in GUIDES}
        vis = {"cap_y"}
        if self.base.hook_style and self.t <= float(self.place.get("hook_dur") or 4.0) + 0.5:
            vis.add("hook_y")
        if self.base.cta_style and self.t >= self.dur - 2.8:
            vis.add("cta_y")
        self.canvas.visible_keys = vis
        self.canvas.update()

    def _set(self, key: str, value):
        self.place[key] = value
        self._sync_canvas()
        self._debounce.start()

    def _slid(self, key: str, x: int):
        if key in ("frame_x", "frame_y"):
            v = x / 100
        elif key == "hook_dur":
            v = x / 10
        else:
            v = x / 100
        self._set(key, round(v, 3))

    def _dragged(self, key: str, f: float):
        s = self.sl.get(key)
        if s:
            s.blockSignals(True)
            s.setValue(int(round(f * 100)))
            s.blockSignals(False)
        self.place[key] = round(f, 3)
        self._debounce.start()

    def _time_changed(self, v: int):
        self.t = v / 10
        self.t_lbl.setText(f"{int(self.t // 60)}:{self.t % 60:04.1f}")
        self._sync_canvas()
        self._debounce.start()

    def reset(self):
        self.place = {}
        style_dur = ((HOOK_STYLES.get(self.base.hook_style or "", {}) or {}).get("dur") or 4.0)
        targets = {"cap_y": int(self._default("cap_y") * 100), "hook_y": int(self._default("hook_y") * 100),
                   "cta_y": 42, "frame_x": 0, "frame_y": 0, "hook_dur": int(style_dur * 10)}
        for k, s in self.sl.items():
            s.setValue(targets.get(k, 100))     # updates the value labels
        self.wm.setCurrentIndex(0)
        self.place = {}                         # defaults = no overrides at all
        self._sync_canvas()
        self._debounce.start()

    # ------------------------------------------------------------------
    def refresh(self):
        if self._thread and self._thread.isRunning():
            self._pending = True
            return
        self._pending = False
        self.canvas.busy = True
        self.canvas.update()
        ch = replace(self.base, place=dict(self.place))
        self._thread = PreviewThread(self.s.copy(), self.plan["plan_file"], ch, self.hook, self.t, self._tmp)
        self._thread.ready.connect(self._ready)
        self._thread.failed.connect(self._failed)
        self._thread.finished.connect(self._finished)
        self._thread.start()

    def _ready(self, png: str, dur: float):
        self.canvas.set_pixmap(png)
        if abs(dur - self.dur) > 0.2:
            self.dur = dur
            self.t_slider.setMaximum(max(1, int(dur * 10) - 1))

    def _failed(self, msg: str):
        self.canvas.message = "Preview failed"
        self.status.setText(f"Preview failed: {msg}")

    def _finished(self):
        self.canvas.busy = False
        self.canvas.update()
        if self._pending:
            self.refresh()

    def _accept(self):
        self.remember = self.keep.isChecked()
        self.accept()

    def done(self, r):
        if self._thread and self._thread.isRunning():
            self._thread.wait(4000)
        super().done(r)
