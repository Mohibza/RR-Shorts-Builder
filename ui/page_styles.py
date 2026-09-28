"""Styles gallery: preview every template and choose which ones go into the random mix."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout,
                               QWidget)

from shortsforge import previews
from shortsforge.captions import CAPTION_STYLES, CTA_STYLES, HOOK_STYLES
from shortsforge.config import DEFAULT_CAPTION_POOL, Settings
from shortsforge.effects import COLOR_GRADES

from . import theme
from .widgets import Toggle, label, rounded_pixmap
from .workers import PreviewWorker

PW, PH = 170, 302


class StyleTile(QFrame):
    def __init__(self, kind: str, key: str, name: str, in_mix: bool | None, fixed: bool):
        super().__init__()
        self.kind, self.key = kind, key
        self.path = ""
        self.pw, self.ph = PW, PH
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        self.img = QLabel()
        self.img.setFixedSize(PW, PH)
        self.img.setPixmap(rounded_pixmap("", PW, PH))
        self.img.setAlignment(Qt.AlignCenter)
        v.addWidget(self.img)
        self.name = name
        self.title = label(name)
        self.title.setStyleSheet("font-weight:700;")
        self.title.setToolTip(name)
        v.addWidget(self.title)
        # one compact row: [mix switch] ....... [pin]  (fits even small tiles)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.mix = None
        if in_mix is not None:
            self.mix = Toggle("", in_mix)
            self.mix.setToolTip("In the random mix (on/off)")
            row.addWidget(self.mix)
        row.addStretch(1)
        self.use = QPushButton()
        self.use.setObjectName("Ghost")
        self.use.setIcon(theme.icon("check" if fixed else "pin", theme.ACCENT2 if fixed else theme.TEXT))
        self.use.setFixedSize(34, 30)
        self.use.setToolTip("Always used (click to go back to random)" if fixed else "Always use this one")
        row.addWidget(self.use)
        v.addLayout(row)
        if fixed:
            self.setStyleSheet(f"QFrame#Card {{ border: 1px solid {theme.ACCENT}; }}")

    def set_image(self, path: str):
        self.path = path
        self.img.setPixmap(rounded_pixmap(path, self.pw, self.ph, 10))

    def extra_height(self) -> int:
        """Height of everything except the preview image."""
        return self.sizeHint().height() - self.img.height()

    def set_size(self, pw: int):
        pw = max(60, int(pw))
        ph = int(pw * 16 / 9)
        if (pw, ph) != (self.pw, self.ph):
            self.pw, self.ph = pw, ph
            self.img.setFixedSize(pw, ph)
            self.img.setPixmap(rounded_pixmap(self.path, pw, ph, 10))
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(self.name, Qt.ElideRight, pw))


class FitGallery(QWidget):
    """Lays out all tiles so the whole set fits the visible area: tiles grow or shrink with the window."""
    GAP = 14

    def __init__(self):
        super().__init__()
        self.tiles: list[StyleTile] = []

    def add(self, tile: StyleTile):
        tile.setParent(self)
        self.tiles.append(tile)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def showEvent(self, e):
        super().showEvent(e)
        self.relayout()

    def relayout(self):
        n = len(self.tiles)
        if not n or self.width() < 50:
            return
        W, H, g = self.width(), self.height(), self.GAP
        extra = max(t.extra_height() for t in self.tiles)
        best = None
        for cols in range(1, n + 1):
            rows = -(-n // cols)
            tw = (W - (cols - 1) * g) / cols
            pw = min(tw - 20, (((H - (rows - 1) * g) / rows) - extra) * 9 / 16, 230)
            if best is None or pw > best[0]:
                best = (pw, cols)
        pw, cols = best
        pw = max(60, int(pw))
        for t in self.tiles:
            t.set_size(pw)
        tw = pw + 20
        th = int(pw * 16 / 9) + extra
        rows = -(-n // cols)
        x0 = max(0, (W - (cols * tw + (cols - 1) * g)) // 2)
        for i, t in enumerate(self.tiles):
            r, c = divmod(i, cols)
            t.setGeometry(x0 + c * (tw + g), r * (th + g), tw, th)
            t.show()
        self.setMinimumHeight(0)


class StylesPage(QWidget):
    settings_changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.tiles: dict[tuple[str, str], StyleTile] = {}
        self.worker = None
        self._old: list = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 16)
        root.setSpacing(12)
        hr = QHBoxLayout()
        hr.addWidget(label("Styles & Templates", "H1"))
        hr.addStretch(1)
        rb = QPushButton(" Random mix everything")
        rb.setIcon(theme.icon("dice"))
        rb.clicked.connect(self.all_random)
        hr.addWidget(rb)
        root.addLayout(hr)
        root.addWidget(label("By default every Short gets a different combination. Switch templates in or out of the "
                             "random mix, or pin one to always use it. Previews use the real render engine.",
                             "Muted", wrap=True))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.built = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self.built:
            self.build()

    def build(self):
        self.built = True
        self.tabs.clear()
        self.tiles.clear()
        s = self.s
        specs = [
            ("cap", "Captions", CAPTION_STYLES, "caption_style", "caption_pool"),
            ("hook", "Hook titles", HOOK_STYLES, "hook_style", "hook_pool"),
            ("cta", "End cards", CTA_STYLES, "cta_style", None),
            ("grade", "Color grades", {k: v for k, v in COLOR_GRADES.items()}, "color_grade", None),
        ]
        jobs = []
        for kind, title, table, attr, pool_attr in specs:
            gal = FitGallery()
            current = getattr(s, attr)
            pool = getattr(s, pool_attr) if pool_attr else None
            for key, spec in table.items():
                in_mix = None
                if pool_attr is not None:
                    in_mix = (not pool) or key in pool
                tile = StyleTile(kind, key, spec["name"], in_mix, current == key)
                if tile.mix:
                    tile.mix.toggled.connect(lambda on, k=key, pa=pool_attr, tb=table: self._mix(pa, tb, k, on))
                tile.use.clicked.connect(lambda _=False, a=attr, k=key: self._fix(a, k))
                gal.add(tile)
                self.tiles[(kind, key)] = tile
                jobs.append((kind, key))
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 14, 0, 0)
            wl.addWidget(gal)
            self.tabs.addTab(wrap, title)
        if self.worker and self.worker.isRunning():
            self.worker.jobs.clear()          # stop the old run early
            self._old.append(self.worker)
        self.worker = PreviewWorker(jobs)
        self.worker.ready.connect(self._preview)
        self.worker.start()

    def _preview(self, kind, key, path):
        t = self.tiles.get((kind, key))
        if t and path:
            t.set_image(path)

    def _mix(self, pool_attr, table, key, on):
        pool = list(getattr(self.s, pool_attr) or table.keys())
        if on and key not in pool:
            pool.append(key)
        if not on and key in pool:
            pool.remove(key)
        if not pool:  # never allow an empty mix
            pool = [key]
            self.tiles[(("cap" if pool_attr == "caption_pool" else "hook"), key)].mix.setChecked(True)
        setattr(self.s, pool_attr, [] if set(pool) == set(table.keys()) else pool)
        self.s.save()
        self.settings_changed.emit()

    def _fix(self, attr, key):
        setattr(self.s, attr, "random" if getattr(self.s, attr) == key else key)
        self.s.save()
        idx = self.tabs.currentIndex()
        self.build()
        self.tabs.setCurrentIndex(idx)
        self.settings_changed.emit()

    def all_random(self):
        for a in ("caption_style", "hook_style", "cta_style", "color_grade", "motion", "intro"):
            setattr(self.s, a, "random")
        self.s.caption_pool, self.s.hook_pool = list(DEFAULT_CAPTION_POOL), []
        self.s.save()
        idx = self.tabs.currentIndex()
        self.build()
        self.tabs.setCurrentIndex(max(0, idx))
        self.settings_changed.emit()

    def refresh_previews(self):
        previews.clear()
        if self.built:
            self.build()
