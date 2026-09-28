"""Trial status + "enter a license key" dialog. Shown automatically when a job hits the trial limit
(QueueWorker.license_blocked), and reachable any time from Settings -> License."""
from __future__ import annotations

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from shortsforge import licensing

from . import theme
from .widgets import label

# Update this once the storefront/landing page is live (see license-server/README.md, phase 2).
BUY_URL = "https://rrshortsbuilder.com/buy"


class _Call(QThread):
    """Runs a licensing.* network call off the UI thread."""
    done = Signal(object, str)   # LicenseState or None, error message

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except licensing.LicenseError as e:
            self.done.emit(None, str(e))
        except Exception as e:
            self.done.emit(None, f"Unexpected error: {e}")


class LicenseDialog(QDialog):
    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("License")
        self.setMinimumWidth(520)
        self._threads: list[_Call] = []   # keep refs alive while running; Qt would otherwise GC them mid-flight

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(14)
        v.addWidget(label("License", "H2"))
        if reason:
            r = label(reason, "Small", wrap=True)
            r.setStyleSheet(f"color:{theme.WARN}; font-weight:600;")
            v.addWidget(r)

        self.status = label("Checking…", "", wrap=True)
        v.addWidget(self.status)

        row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setPlaceholderText("RRSF-XXXXXXXXXXXXXXXX")
        row.addWidget(self.key, 1)
        act = QPushButton(" Activate")
        act.setObjectName("Primary")
        act.setIcon(theme.icon("check", "#FFFFFF"))
        act.clicked.connect(self.activate)
        row.addWidget(act)
        v.addLayout(row)

        self.msg = label("", "Small", wrap=True)
        v.addWidget(self.msg)

        bottom = QHBoxLayout()
        buy = QPushButton(" Buy a license")
        buy.setIcon(theme.icon("link"))
        buy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUY_URL)))
        bottom.addWidget(buy)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        v.addLayout(bottom)

        self._show(licensing.current_state())
        self._run(licensing.refresh)

    def _show(self, s: licensing.LicenseState):
        if s.status == "active":
            plan = f" ({s.plan})" if s.plan else ""
            self.status.setText(f"✓ Licensed{plan} — unlimited Shorts on this PC.")
            self.status.setStyleSheet(f"color:{theme.GOOD}; font-weight:600;")
        elif s.status in ("trial", "unknown"):
            left = s.videos_left
            self.status.setText(f"Trial: {left} of {s.videos_allowed} Shorts left on this PC."
                                if left else f"Trial used up (0 of {s.videos_allowed} left). Enter a license key below.")
            self.status.setStyleSheet(f"color:{theme.TEXT if left else theme.BAD}; font-weight:600;")
        else:
            self.status.setText("Trial used up. Enter a license key below, or buy one.")
            self.status.setStyleSheet(f"color:{theme.BAD}; font-weight:600;")

    def _run(self, fn):
        w = _Call(fn)
        self._threads.append(w)
        w.done.connect(lambda state, err, w=w: (self._done(state, err), self._threads.remove(w)))
        w.start()

    def _done(self, state, err: str):
        if state is not None:
            self._show(state)
        elif err:
            # a failed background refresh shouldn't blank out a status we already showed from cache
            if self.msg.text() == "":
                self.msg.setText(err)

    def activate(self):
        key = self.key.text().strip()
        if not key:
            self.msg.setText("Paste your license key first.")
            self.msg.setStyleSheet(f"color:{theme.BAD};")
            return
        self.msg.setText("Activating…")
        self.msg.setStyleSheet("")

        def do():
            return licensing.redeem(key)

        def finished(state, err):
            if state is not None:
                self._show(state)
                self.msg.setText("✓ Activated. Thanks!")
                self.msg.setStyleSheet(f"color:{theme.GOOD};")
            else:
                self.msg.setText(err or "That key didn't work.")
                self.msg.setStyleSheet(f"color:{theme.BAD};")

        w = _Call(do)
        self._threads.append(w)
        w.done.connect(lambda state, err, w=w: (finished(state, err), self._threads.remove(w)))
        w.start()

    def _close(self):
        for w in list(self._threads):
            w.wait(5000)

    def reject(self):
        self._close()
        super().reject()

    def accept(self):
        self._close()
        super().accept()


def show_license(parent=None, reason: str = ""):
    LicenseDialog(parent, reason).exec()
