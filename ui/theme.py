"""Dark theme and icon helpers."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

BG = "#0B0D12"
PANEL = "#12151C"
PANEL2 = "#191D26"
BORDER = "#232835"
TEXT = "#E7E9EF"
MUTED = "#8C93A6"
ACCENT = "#6D5CFF"
ACCENT2 = "#9B8CFF"
GOOD = "#34C77B"
WARN = "#F2A93B"
BAD = "#F0585E"

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial; font-size: 9.75pt;
     color: {TEXT}; }}
QMainWindow, QWidget#Root, QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
QWidget#Sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
QFrame#SidePanel {{ background: {PANEL}; border-left: 1px solid {BORDER}; }}
QLabel#Logo {{ font-size: 15pt; font-weight: 700; padding: 2px 0; letter-spacing: 0.2px; }}
QLabel#LogoSub {{ color: {MUTED}; font-size: 8pt; }}
QPushButton#Nav {{
    text-align: left; padding: 10px 12px; border: none; border-radius: 8px;
    background: transparent; color: {MUTED}; font-size: 10pt; font-weight: 600;
}}
QPushButton#Nav:hover {{ background: {PANEL2}; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: rgba(109,92,255,0.14); color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QLabel#H1 {{ font-size: 19pt; font-weight: 700; letter-spacing: -0.2px; }}
QLabel#H2 {{ font-size: 11.5pt; font-weight: 700; }}
QLabel#Section {{ color: {MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.2px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Small {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#Pill {{ background: rgba(109,92,255,0.16); color: #C3BAFF; border-radius: 9px; padding: 2px 9px;
    font-size: 8.5pt; font-weight: 600; }}
QLabel#Chip {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 12px; padding: 5px 12px;
    font-size: 8.5pt; font-weight: 600; color: {MUTED}; }}
QLabel#Chip[state="on"] {{ color: {GOOD}; border-color: rgba(52,199,123,0.35); }}
QLabel#Chip[state="warn"] {{ color: {WARN}; border-color: rgba(242,169,59,0.35); }}
QFrame#Card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#InputShell {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLineEdit#BigInput {{ background: transparent; border: none; font-size: 11.5pt; padding: 8px 4px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 9px; min-height: 20px;
    selection-background-color: {ACCENT};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #2F3545; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QPlainTextEdit#Log {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 8.5pt; background: {BG}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: #343B4D; background: #1F2430; }}
QPushButton:disabled {{ color: #555B6B; }}
QPushButton#Primary {{ border: none; color: white; padding: 10px 20px; font-size: 10.5pt; font-weight: 700;
    background: {ACCENT}; }}
QPushButton#Primary:hover {{ background: #7E6FFF; }}
QPushButton#Primary:pressed {{ background: #5B4BEA; }}
QPushButton#Primary:disabled {{ background: #262B38; color: #6B7183; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid {BORDER}; }}
QPushButton#Ghost:hover {{ background: {PANEL2}; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT2}; padding: 2px 4px; font-weight: 600; }}
QPushButton#Link:hover {{ color: #BDB3FF; text-decoration: underline; }}
QPushButton#Link:checked {{ color: {TEXT}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid #4A2A30; color: {BAD}; }}
QCheckBox {{ spacing: 8px; }}
QRadioButton {{ spacing: 8px; padding: 2px 0; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 2px solid #3A4152; background: {PANEL2}; }}
QRadioButton::indicator:checked {{ border: 4px solid {ACCENT}; background: {TEXT}; }}
QProgressBar {{ background: {PANEL2}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 3px; background: {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2B3140; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2B3140; border-radius: 3px; min-width: 30px; }}
QListWidget, QTreeWidget, QTableWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border-radius: 10px; padding: 6px; color: {TEXT}; }}
QListWidget::item:selected {{ background: rgba(109,92,255,0.20); }}
QListWidget::item:hover {{ background: {PANEL2}; }}
QTreeWidget::item {{ padding: 7px 4px; border-bottom: 1px solid {BORDER}; }}
QListWidget#Box {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QListWidget#Box::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget#Box::item:selected {{ background: rgba(109,92,255,0.18); color: {TEXT}; }}
QListWidget#Cats {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 8px; }}
QListWidget#Cats::item {{ border-radius: 8px; padding: 0 8px; color: {MUTED}; font-weight: 600; }}
QListWidget#Cats::item:hover {{ background: {PANEL2}; color: {TEXT}; }}
QListWidget#Cats::item:selected {{ background: rgba(109,92,255,0.16); color: {TEXT}; }}
QTreeWidget::item:selected {{ background: rgba(109,92,255,0.18); color: {TEXT}; }}
QTreeWidget::item:hover {{ background: {PANEL2}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 4px; font-size: 8pt; font-weight: 700; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; font-weight: 600; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {PANEL2}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; border-radius: 7px; background: {TEXT}; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QMessageBox {{ background: {PANEL}; }}
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
"""

# Minimal line icons (24x24, stroke based)
_ICONS = {
    "spark": '<path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.3"/><circle cx="12" cy="7.5" r="1.3"/>'
               '<circle cx="16" cy="10" r="1.3"/><path d="M12 21a2.5 2.5 0 0 1 0-5h2a3 3 0 0 0 3-3"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1'
                'a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8'
                'l.1-.1A1.7 1.7 0 0 0 1.2 14H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 7a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8'
                '-2.8l.1.1A1.7 1.7 0 0 0 9 2.6V2a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7'
                ' 0 0 0 21.4 9H22a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-2.5 2z"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
    "pause": '<path d="M7 4h4v16H7zM13 4h4v16h-4z"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "refresh": '<path d="M20 11A8 8 0 0 0 5.3 6.3L3 9M4 13a8 8 0 0 0 14.7 4.7L21 15"/><path d="M3 4v5h5M21 20v-5h-5"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    "dice": '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.2"/><circle cx="16" cy="16" r="1.2"/>'
            '<circle cx="12" cy="12" r="1.2"/><circle cx="16" cy="8" r="1.2"/><circle cx="8" cy="16" r="1.2"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    "check": '<path d="M5 12l5 5L20 7"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "upload": '<path d="M12 20V9M7 13l5-5 5 5M5 4h14"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "send": '<path d="M4 12l16-8-6 16-3-7z"/>',
    "pin": '<path d="M9 4h6l-1 6 4 4H6l4-4z"/><path d="M12 14v7"/>',
    "move": '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    "wand": '<path d="M15 4V2M15 10V8M19 6h2M9 6h2M17.8 3.2l1.4-1.4M17.8 8.8l1.4 1.4M3 21l12-12"/>',
}


def icon(name: str, color: str = TEXT, size: int = 20, fill: bool = False) -> QIcon:
    body = _ICONS.get(name, _ICONS["spark"])
    f = color if fill else "none"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{f}" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for s in (size, size * 2):
        pm = QPixmap(QSize(s, s))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        r.render(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def _draw_logo(s: int) -> QPixmap:
    """RR Shorts Builder logo: gradient tile, bold "RR" and a play badge."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QLinearGradient, QPolygonF
    global _LOGO_FAMILY
    if _LOGO_FAMILY is None:
        _LOGO_FAMILY = "Arial"
        try:
            from shortsforge.config import BUNDLED_FONTS
            fid = QFontDatabase.addApplicationFont(str(BUNDLED_FONTS / "Poppins-Black.ttf"))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                _LOGO_FAMILY = fams[0]
        except Exception:
            pass
    pm = QPixmap(s, s)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    k = s / 64.0
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor("#FF4D8D"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(2 * k, 2 * k, 60 * k, 60 * k), 15 * k, 15 * k)
    f = QFont(_LOGO_FAMILY)
    f.setPixelSize(max(6, int(30 * k)))
    f.setWeight(QFont.Black)
    f.setLetterSpacing(QFont.AbsoluteSpacing, -1.2 * k)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, 3 * k, 64 * k, 40 * k), Qt.AlignCenter, "RR")
    # play badge
    p.setBrush(QColor(255, 255, 255, 235))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(19 * k, 42 * k, 26 * k, 13 * k), 6.5 * k, 6.5 * k)
    p.setBrush(QColor("#FF4D8D"))
    p.drawPolygon(QPolygonF([QPointF(29 * k, 45 * k), QPointF(37 * k, 48.5 * k), QPointF(29 * k, 52 * k)]))
    p.end()
    return pm


_LOGO_FAMILY = None


def app_icon() -> QIcon:
    """App logo drawn in code (no external files needed)."""
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(_draw_logo(s))
    return ic


# ---------------------------------------------------------------- screen-adaptive scale
K = 1.0   # UI scale for the current screen (1.0 on laptops, up to 1.25 on big monitors)


def screen_scale(avail_w: int, avail_h: int) -> float:
    """Designed to fit a 1366x768 laptop; grow text, controls and spacing on larger screens."""
    return round(max(1.0, min(1.25, avail_w / 1440, avail_h / 800)), 3)


def scaled_qss(k: float) -> str:
    """The stylesheet with font sizes and control padding multiplied by k."""
    import re
    global K
    K = k
    if abs(k - 1.0) < 0.01:
        return QSS
    s = re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m.group(1)) * k:.2f}pt", QSS)

    def px(decl):
        return re.sub(r"(\d+)px", lambda m: f"{round(int(m.group(1)) * k)}px", decl.group(0))
    return re.sub(r"(padding|min-height|spacing)\s*:[^;]+;", px, s)


def S(v: float) -> int:
    """Scale a pixel size from code (sidebar width, panel widths) by the screen factor."""
    return int(round(v * K))
