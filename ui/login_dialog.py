"""YouTube sign-in using the user's real Chrome/Edge (Google blocks sign-in inside embedded app browsers)."""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout)

from shortsforge import browser_login, cookies

from . import theme
from .widgets import label


class _Harvest(QThread):
    done = Signal(int, str)

    def run(self):
        try:
            self.done.emit(browser_login.harvest(), "")
        except Exception as e:
            self.done.emit(0, str(e))


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to YouTube")
        self.setMinimumWidth(560)
        self.proc: subprocess.Popen | None = None
        self.worker = None
        self.browser = browser_login.find_browser()

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(14)
        v.addWidget(label("Sign in to YouTube", "H2"))
        v.addWidget(label("YouTube only lets signed-in users download when it suspects a bot. Sign in once in your "
                          "real browser, and Rebels Revolt Shorts keeps the login renewed in the background after that.",
                          "Muted", wrap=True))

        steps = QFrame()
        steps.setObjectName("Card")
        sv = QVBoxLayout(steps)
        sv.setContentsMargins(18, 16, 18, 16)
        sv.setSpacing(8)
        bname = self.browser[0] if self.browser else "Chrome / Edge"
        for i, t in enumerate((f"Click the button below. A {bname} window opens with a separate Rebels Revolt Shorts profile.",
                               "Sign in with the Google account that owns your channel.",
                               "When your YouTube home page shows, click Finish (or just close that window).")):
            r = QHBoxLayout()
            n = QLabel(str(i + 1))
            n.setFixedSize(26, 26)
            n.setAlignment(Qt.AlignCenter)
            n.setStyleSheet(f"background:{theme.ACCENT}; color:white; border-radius:13px; font-weight:700;")
            r.addWidget(n, 0, Qt.AlignTop)
            r.addWidget(label(t, wrap=True), 1)
            sv.addLayout(r)
        v.addWidget(steps)

        self.status = label("", "Small", wrap=True)
        v.addWidget(self.status)

        row = QHBoxLayout()
        imp = QPushButton(" Import cookies.txt")
        imp.setIcon(theme.icon("file"))
        imp.setToolTip("Alternative: export cookies.txt from any browser where you're signed in to YouTube.")
        imp.clicked.connect(self.import_file)
        row.addWidget(imp)
        row.addStretch(1)
        self.finish_btn = QPushButton("  Finish")
        self.finish_btn.setIcon(theme.icon("check"))
        self.finish_btn.setVisible(False)
        self.finish_btn.clicked.connect(self.finish)
        row.addWidget(self.finish_btn)
        self.open_btn = QPushButton(f"  Sign in with {bname}")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setIcon(theme.icon("link", "#FFFFFF"))
        self.open_btn.clicked.connect(self.open_browser)
        self.open_btn.setEnabled(self.browser is not None)
        row.addWidget(self.open_btn)
        v.addLayout(row)
        if not self.browser:
            self._set("Chrome or Edge wasn't found. Install Google Chrome, or use Import cookies.txt.", theme.BAD)

        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.timeout.connect(self._poll)

    def _set(self, text: str, color: str = theme.MUTED):
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color}; font-weight:600;")

    def open_browser(self):
        try:
            self.proc = browser_login.open_sign_in()
        except Exception as e:
            self._set(str(e), theme.BAD)
            return
        self.open_btn.setEnabled(False)
        self.open_btn.setText("  Browser open…")
        self.finish_btn.setVisible(True)
        self._set("Sign in in the browser window, then click Finish or close the window.", theme.WARN)
        self.timer.start()

    def _poll(self):
        if self.proc and self.proc.poll() is not None:
            self.timer.stop()
            self.proc = None
            self._harvest()

    def finish(self):
        """Close the sign-in window gracefully (so cookies are written) and read the login."""
        self.finish_btn.setEnabled(False)
        if self.proc and self.proc.poll() is None:
            self._set("Closing the browser and saving your login…")
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T"], capture_output=True,
                                   creationflags=0x08000000)
                else:
                    self.proc.terminate()
            except Exception:
                pass
            QTimer.singleShot(8000, self._force_close)  # if it ignores the request
        else:
            self._harvest()

    def _force_close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _harvest(self):
        if self.worker and self.worker.isRunning():
            return
        self._set("Reading your YouTube login…")
        self.worker = _Harvest()
        self.worker.done.connect(self._harvested)
        self.worker.start()

    def _harvested(self, n: int, err: str):
        if n > 0 and cookies.has_login():
            self._set("✓ Signed in — Rebels Revolt Shorts will keep this login fresh automatically.", theme.GOOD)
            QTimer.singleShot(700, self.accept)
            return
        self.open_btn.setEnabled(True)
        self.open_btn.setText("  Open browser again")
        self.finish_btn.setVisible(False)
        self.finish_btn.setEnabled(True)
        self._set(err or "You're not signed in to YouTube in that window yet. Open it again and finish signing in.",
                  theme.BAD)

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if not path:
            return
        try:
            ok = cookies.import_file(path)
        except Exception as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        if ok:
            self.accept()
        else:
            QMessageBox.warning(self, "Not signed in", "That file has no YouTube login in it. Export it while "
                                "you're signed in to youtube.com.")

    def reject(self):
        self.timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.wait(20000)
        super().reject()


def sign_in(parent=None) -> bool:
    return LoginDialog(parent).exec() == QDialog.Accepted and cookies.has_login()
