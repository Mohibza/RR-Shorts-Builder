"""Reusable widgets: toggle switch, cards, flow layout, job cards, thumbnails."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (Property, QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme


class Toggle(QCheckBox):
    """iOS-style switch."""

    def __init__(self, text: str = "", checked: bool = False, parent=None):
        super().__init__(text, parent)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setStyleSheet("QCheckBox::indicator { width: 0; height: 0; } QCheckBox { spacing: 0; padding-left: 46px; }")
        self.setMinimumHeight(24)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float):
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = 20
        y = (self.height() - h) // 2
        track = QRect(0, y, 38, h)
        off, on = QColor("#2A2F3C"), QColor(theme.ACCENT)
        c = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(track, h / 2, h / 2)
        p.setBrush(QColor("white"))
        x = 3 + self._pos * (38 - 6 - 14)
        p.drawEllipse(int(x), y + 3, 14, 14)
        p.end()


class Card(QFrame):
    def __init__(self, parent=None, pad: int = 18, spacing: int = 12):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(pad, pad, pad, pad)
        self.lay.setSpacing(spacing)


def label(text: str, obj: str = "", wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


def field(title: str, widget: QWidget, hint: str = "") -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(5)
    t = label(title, "Small", wrap=True)   # wraps instead of forcing the window wider
    v.addWidget(t)
    v.addWidget(widget)
    if hint:
        h = label(hint, "Small", wrap=True)
        v.addWidget(h)
    return w


class FlowLayout(QLayout):
    """Wrapping layout for thumbnail grids."""

    def __init__(self, parent=None, spacing: int = 14):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s + QSize(2, 2)

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            sh = it.sizeHint()
            nx = x + sh.width() + self._sp
            if nx - self._sp > rect.right() and line > 0:
                x = rect.x()
                y += line + self._sp
                nx = x + sh.width() + self._sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sh))
            x = nx
            line = max(line, sh.height())
        return y + line - rect.y()

    def clear(self):
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().deleteLater()


def rounded_pixmap(path: str, w: int, h: int, radius: int = 12) -> QPixmap:
    src = QPixmap(path) if path and Path(path).exists() else QPixmap()
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(clip)
    if src.isNull():
        p.fillRect(0, 0, w, h, QColor(theme.PANEL2))
    else:
        s = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap((w - s.width()) // 2, (h - s.height()) // 2, s)
    p.end()
    return out


class ShortThumb(QFrame):
    clicked = Signal(str)

    def __init__(self, thumb: str, title: str, key: str, subtitle: str = "", w: int = 140):
        super().__init__()
        self.key = key
        self.setCursor(Qt.PointingHandCursor)
        h = int(w * 16 / 9)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.img = QLabel()
        self.img.setPixmap(rounded_pixmap(thumb, w, h))
        self.img.setFixedSize(w, h)
        v.addWidget(self.img)
        t = label(title, wrap=True)
        t.setFixedWidth(w)
        t.setStyleSheet("font-weight:600; font-size:9pt;")
        t.setMaximumHeight(36)
        v.addWidget(t)
        if subtitle:
            s = label(subtitle, "Small", wrap=True)
            s.setFixedWidth(w)
            v.addWidget(s)
        self.setFixedWidth(w)
        self.setFixedHeight(h + 6 + 38 + (22 if subtitle else 0))

    def mouseReleaseEvent(self, e):
        self.clicked.emit(self.key)
        super().mouseReleaseEvent(e)


class JobCard(QFrame):
    cancel_clicked = Signal(int)
    open_clicked = Signal(int)

    def __init__(self, job_id: int, title: str, source: str):
        super().__init__()
        self.job_id = job_id
        self.setObjectName("Card")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(14)
        self.badge = QLabel()
        self.badge.setFixedSize(40, 40)
        self.badge.setAlignment(Qt.AlignCenter)
        self._set_badge("queued")
        h.addWidget(self.badge)
        mid = QVBoxLayout()
        mid.setSpacing(5)
        self.title = label(title)
        self.title.setStyleSheet("font-weight:700;")
        self.title.setMinimumWidth(100)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.stage = label("Waiting in queue", "Small")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(8)
        mid.addWidget(self.title)
        mid.addWidget(self.stage)
        mid.addWidget(self.bar)
        h.addLayout(mid, 1)
        self.pct = label("", "Muted")
        self.pct.setFixedWidth(44)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self.pct)
        self.btn = QPushButton()
        self.btn.setIcon(theme.icon("x", theme.MUTED))
        self.btn.setObjectName("Ghost")
        self.btn.setToolTip("Cancel")
        self.btn.setFixedSize(36, 36)
        self.btn.clicked.connect(self._btn)
        h.addWidget(self.btn)
        self.state = "queued"

    def _set_badge(self, state: str):
        colors = {"queued": theme.MUTED, "running": theme.ACCENT, "done": theme.GOOD, "failed": theme.BAD,
                  "cancelled": theme.WARN}
        names = {"queued": "film", "running": "spark", "done": "check", "failed": "x", "cancelled": "x"}
        c = colors.get(state, theme.MUTED)
        q = QColor(c)
        self.badge.setStyleSheet(f"background: rgba({q.red()},{q.green()},{q.blue()},40); border-radius: 12px;")
        self.badge.setPixmap(theme.icon(names.get(state, "film"), c, 22).pixmap(22, 22))

    def set_progress(self, stage: str, frac: float, detail: str = ""):
        if self.state == "queued":
            self.set_state("running")
        self.bar.setValue(int(frac * 1000))
        self.pct.setText(f"{int(frac * 100)}%")
        self.stage.setText(stage + (f" · {detail}" if detail else ""))

    def set_state(self, state: str, msg: str = ""):
        self.state = state
        self._set_badge(state)
        if state in ("done", "failed", "cancelled"):
            if state == "done":
                self.bar.setValue(1000)
                self.pct.setText("100%")
                self.btn.setIcon(theme.icon("folder", theme.TEXT))
                self.btn.setToolTip("Open folder")
            else:
                self.btn.setIcon(theme.icon("x", theme.MUTED))
                self.btn.setEnabled(False)
            if msg:
                self.stage.setText(msg)
                self.stage.setToolTip(msg)

    def _btn(self):
        if self.state == "done":
            self.open_clicked.emit(self.job_id)
        else:
            self.cancel_clicked.emit(self.job_id)
