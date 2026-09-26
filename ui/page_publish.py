"""Publish: connect YouTube / Facebook Page / TikTok, auto-upload with random gaps, and the upload queue."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from shortsforge import publish, uploadqueue
from shortsforge.config import Settings

from . import theme
from .widgets import Card, Toggle, field, label

GUIDES = {
    "youtube": {
        "title": "Connect YouTube (Google Cloud, free, ~10 minutes once)",
        "fields": [("yt_client_id", "Client ID"), ("yt_client_secret", "Client secret")],
        "steps": [
            "Open <a href='https://console.cloud.google.com/projectcreate'>Google Cloud</a> and create a project "
            "(e.g. “RR Shorts”).",
            "Open <a href='https://console.cloud.google.com/apis/library/youtube.googleapis.com'>YouTube Data API v3"
            "</a> and click <b>Enable</b>.",
            "Open <a href='https://console.cloud.google.com/auth/overview'>Google Auth Platform</a>: set it up as "
            "<b>External</b> with your app name and email. Under <b>Audience</b> click <b>Publish app</b> "
            "(In production) so the sign-in doesn't expire every 7 days.",
            "Under <a href='https://console.cloud.google.com/auth/clients'>Clients</a> click <b>Create client</b> → "
            "Application type <b>Desktop app</b>. Copy the <b>Client ID</b> and <b>Client secret</b> below.",
            "Save, then click <b>Connect YouTube</b>. If Google says “hasn't verified this app”, click "
            "<b>Advanced → Go to (your app)</b>: it's your own app.",
            "<b>Important:</b> Google keeps videos uploaded by an API project it hasn't audited <b>locked as "
            "Private</b>. Fill in its free <a href='https://support.google.com/youtube/contact/yt_api_form'>YouTube "
            "API audit form</a> once; after approval, uploads go out with the visibility you choose. About 6 uploads "
            "a day fit the free quota.",
        ],
    },
    "facebook": {
        "title": "Connect a Facebook Page (Meta for Developers, free)",
        "fields": [("fb_app_id", "App ID"), ("fb_app_secret", "App secret")],
        "steps": [
            "Open <a href='https://developers.facebook.com/apps/creation/'>Meta for Developers</a> → "
            "<b>Create app</b>. Choose the use case <b>Manage everything on your Page</b> (or Other → Business).",
            "In the app, open <b>Facebook Login</b> → Settings and add <b>http://localhost:53682/</b> to "
            "<b>Valid OAuth Redirect URIs</b>.",
            "Under <b>App settings → Basic</b> copy the <b>App ID</b> and <b>App secret</b> below.",
            "The permissions pages_show_list, pages_read_engagement and pages_manage_posts work for your own Pages "
            "while the app is in Development mode: no review needed.",
            "Save, then click <b>Connect Facebook</b> and tick the Page(s) to post to. Reels go to Pages; "
            "Facebook has no upload API for personal profiles.",
        ],
    },
    "tiktok": {
        "title": "Connect TikTok (TikTok for Developers, free)",
        "fields": [("tt_client_key", "Client key"), ("tt_client_secret", "Client secret")],
        "steps": [
            "Open <a href='https://developers.tiktok.com/apps/'>TikTok for Developers → Manage apps</a> and create "
            "an app.",
            "Add <b>Login Kit</b> (platform <b>Desktop</b>) with redirect URI <b>http://localhost:53683/callback/</b>, "
            "and add <b>Content Posting API</b> with <b>Direct Post</b> turned on. Scopes: user.info.basic, "
            "video.publish.",
            "Copy the <b>Client key</b> and <b>Client secret</b> below, save, then click <b>Connect TikTok</b>.",
            "Until TikTok audits your app, it only allows <b>Only me</b> posts to a <b>private</b> TikTok account. "
            "Submit the app for review in the developer portal; once approved, tick “TikTok approved my app”.",
        ],
    },
}
ICON = {"youtube": "play", "facebook": "film", "tiktok": "music"}


class KeysDialog(QDialog):
    def __init__(self, s: Settings, platform: str, parent=None):
        super().__init__(parent)
        g = GUIDES[platform]
        self.s, self.platform = s, platform
        self.setWindowTitle(g["title"])
        self.setMinimumWidth(660)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(12)
        v.addWidget(label(g["title"], "H2"))
        for i, st in enumerate(g["steps"], 1):
            row = QHBoxLayout()
            n = QLabel(str(i))
            n.setFixedSize(24, 24)
            n.setAlignment(Qt.AlignCenter)
            n.setStyleSheet(f"background:{theme.ACCENT}; color:white; border-radius:12px; font-weight:700;")
            row.addWidget(n, 0, Qt.AlignTop)
            t = QLabel(st.replace("<a href=", f"<a style='color:{theme.ACCENT2}' href="))
            t.setWordWrap(True)
            t.setOpenExternalLinks(True)
            t.setTextFormat(Qt.RichText)
            row.addWidget(t, 1)
            v.addLayout(row)
        self.edits = {}
        grid = QGridLayout()
        for i, (attr, name) in enumerate(g["fields"]):
            e = QLineEdit(getattr(s, attr, ""))
            if "secret" in attr:
                e.setEchoMode(QLineEdit.Password)
            self.edits[attr] = e
            grid.addWidget(field(name, e), 0, i)
        v.addLayout(grid)
        br = QHBoxLayout()
        br.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = QPushButton("  Save keys")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        br.addWidget(c)
        br.addWidget(ok)
        v.addLayout(br)

    def _save(self):
        for attr, e in self.edits.items():
            setattr(self.s, attr, e.text().strip())
        self.s.save()
        self.accept()


class ConnectWorker(QThread):
    done = Signal(str, str)        # platform, message ("" = ok)

    def __init__(self, platform: str, s: Settings):
        super().__init__()
        self.platform, self.s = platform, s

    def run(self):
        try:
            r = publish.connect(self.platform, self.s)
            names = ", ".join(a["name"] for a in (r if isinstance(r, list) else [r]))
            self.done.emit(self.platform, "ok:" + names)
        except publish.PublishError as e:
            self.done.emit(self.platform, str(e))
        except Exception as e:
            self.done.emit(self.platform, f"{type(e).__name__}: {e}")


class AccountCard(Card):
    def __init__(self, page: "PublishPage", platform: str):
        super().__init__(pad=16, spacing=10)
        self.page, self.platform = page, platform
        h = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.icon(ICON[platform], theme.ACCENT2, 18).pixmap(18, 18))
        h.addWidget(ic)
        h.addWidget(label(publish.PLATFORMS[platform], "H2"))
        h.addStretch(1)
        self.use = Toggle("Auto-upload", platform in (page.s.upload_platforms or []))
        self.use.setToolTip("Include this platform when new Shorts are uploaded automatically")
        self.use.toggled.connect(page._save)
        h.addWidget(self.use)
        self.lay.addLayout(h)
        self.status = label("", "Small", wrap=True)
        self.lay.addWidget(self.status)
        self.box = QVBoxLayout()
        self.box.setSpacing(4)
        self.lay.addLayout(self.box)
        self.lay.addStretch(1)
        br = QHBoxLayout()
        self.keys = QPushButton(" Setup keys")
        self.keys.setIcon(theme.icon("key"))
        self.keys.clicked.connect(lambda: page.setup_keys(platform))
        self.conn = QPushButton("  Connect")
        self.conn.setObjectName("Primary")
        self.conn.setIcon(theme.icon("link", "#FFFFFF"))
        self.conn.clicked.connect(lambda: page.connect(platform))
        br.addWidget(self.keys)
        br.addStretch(1)
        br.addWidget(self.conn)
        self.lay.addLayout(br)

    def refresh(self):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        accs = publish.load_accounts().get(self.platform, [])
        if not publish.credentials_ok(self.platform, self.page.s):
            self.status.setText("Step 1: click Setup keys (one-time, free developer app).")
        elif not accs:
            self.status.setText("Keys saved. Step 2: click Connect and sign in.")
        else:
            self.status.setText({"youtube": "Uploads as your channel.",
                                 "facebook": "Posts Reels to the ticked Pages.",
                                 "tiktok": "Posts to this TikTok account."}[self.platform])
        for a in accs:
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox(a.get("name", a["id"]))
            cb.setChecked(a.get("enabled", True))
            cb.toggled.connect(lambda on, i=a["id"]: publish.set_enabled(self.platform, i, on))
            rm = QPushButton()
            rm.setIcon(theme.icon("trash", theme.BAD))
            rm.setFixedSize(30, 28)
            rm.setObjectName("Danger")
            rm.setToolTip("Disconnect this account")
            rm.clicked.connect(lambda _=False, i=a["id"], n=a.get("name", ""): self.page.disconnect(self.platform, i, n))
            rh.addWidget(cb, 1)
            rh.addWidget(rm)
            self.box.addWidget(row)
        self.conn.setText("  Add account" if accs else "  Connect")


class PublishPage(QWidget):
    queue_changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._conn = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 18)
        root.setSpacing(14)
        hr = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("Publish", "H1"))
        tv.addWidget(label("Connect your accounts once. New Shorts are uploaded automatically with their viral title, "
                           "description and tags, spaced out at random times.", "Muted", wrap=True))
        hr.addLayout(tv, 1)
        self.t_auto = Toggle("Auto-upload new Shorts", getattr(settings, "auto_upload", False))
        self.t_auto.toggled.connect(self._save)
        hr.addWidget(self.t_auto, 0, Qt.AlignBottom)
        root.addLayout(hr)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.cards = {p: AccountCard(self, p) for p in publish.PLATFORMS}
        for c in self.cards.values():
            cards.addWidget(c, 1)
        root.addLayout(cards)

        sc = Card(pad=16, spacing=10)
        sc.lay.addWidget(label("SCHEDULE", "Section"))
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        s = settings
        self.gmin = QSpinBox()
        self.gmin.setRange(5, 1440)
        self.gmin.setSuffix(" min")
        self.gmin.setValue(int(s.upload_gap_min))
        self.gmax = QSpinBox()
        self.gmax.setRange(5, 2880)
        self.gmax.setSuffix(" min")
        self.gmax.setValue(int(s.upload_gap_max))
        gap = QWidget()
        gh = QHBoxLayout(gap)
        gh.setContentsMargins(0, 0, 0, 0)
        gh.addWidget(self.gmin, 1)
        gh.addWidget(label("to", "Muted"))
        gh.addWidget(self.gmax, 1)
        self.cap = QSpinBox()
        self.cap.setRange(1, 50)
        self.cap.setValue(int(s.upload_daily_cap))
        self.qs = QSpinBox()
        self.qs.setRange(0, 23)
        self.qs.setSuffix(":00")
        self.qs.setValue(int(s.upload_quiet_start))
        self.qe = QSpinBox()
        self.qe.setRange(0, 23)
        self.qe.setSuffix(":00")
        self.qe.setValue(int(s.upload_quiet_end))
        quiet = QWidget()
        qh = QHBoxLayout(quiet)
        qh.setContentsMargins(0, 0, 0, 0)
        qh.addWidget(self.qs, 1)
        qh.addWidget(label("to", "Muted"))
        qh.addWidget(self.qe, 1)
        self.priv = QComboBox()
        for k, v in (("public", "Public"), ("unlisted", "Unlisted"), ("private", "Private")):
            self.priv.addItem(v, k)
        self.priv.setCurrentIndex(max(0, self.priv.findData(s.yt_privacy)))
        self.tt_ok = QCheckBox("TikTok approved my app (post publicly)")
        self.tt_ok.setChecked(bool(s.tiktok_audited))
        g.addWidget(field("Random gap between uploads", gap), 0, 0)
        g.addWidget(field("Max per account per day", self.cap), 0, 1)
        g.addWidget(field("No uploads between (quiet hours)", quiet), 0, 2)
        g.addWidget(field("YouTube visibility", self.priv), 0, 3)
        g.addWidget(self.tt_ok, 1, 3)
        for i in range(4):
            g.setColumnStretch(i, 1)
        sc.lay.addLayout(g)
        for w in (self.gmin, self.gmax, self.cap, self.qs, self.qe):
            w.editingFinished.connect(self._save)
        self.priv.currentIndexChanged.connect(self._save)
        self.tt_ok.toggled.connect(self._save)
        root.addWidget(sc)

        qh2 = QHBoxLayout()
        qh2.addWidget(label("Upload queue", "H2"))
        qh2.addStretch(1)
        self.q_info = label("", "Small")
        qh2.addWidget(self.q_info)
        root.addLayout(qh2)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["WHEN", "SHORT", "WHERE", "STATUS"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.resizeSection(2, 230)
        h.setSectionResizeMode(3, QHeaderView.Interactive)
        h.resizeSection(3, 300)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._open(it))
        root.addWidget(self.tree, 1)
        br = QHBoxLayout()
        for text, ic, fn, tip in ((" Upload now", "send", self.upload_now, "Move the selected uploads to the front"),
                                  (" Retry", "refresh", self.retry, "Try failed uploads again"),
                                  (" Open", "globe", lambda: self._open(self.tree.currentItem()),
                                   "Open the uploaded video"),
                                  (" Remove", "trash", self.remove, "Remove from the queue"),
                                  (" Clear finished", "check", self.clear_done, "Hide uploads that are done")):
            b = QPushButton(text)
            b.setIcon(theme.icon(ic, theme.BAD if ic == "trash" else theme.TEXT))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            br.addWidget(b)
        br.addStretch(1)
        root.addLayout(br)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_queue)
        self.timer.start(5000)
        self.refresh_all()

    # ------------------------------------------------------------------
    def refresh_all(self):
        for c in self.cards.values():
            c.refresh()
        self.refresh_queue()

    def refresh_queue(self):
        q = uploadqueue.load()
        sel = {i.data(0, Qt.UserRole) for i in self.tree.selectedItems()}
        self.tree.clear()
        order = {"uploading": 0, "waiting": 1, "failed": 2, "done": 3}
        now = time.time()
        for j in sorted(q, key=lambda j: (order.get(j["status"], 9), j["due"] if j["status"] != "done"
                                          else -j.get("done_at", 0))):
            when = time.strftime("%a %d %b %H:%M", time.localtime(j.get("done_at") or j["due"]))
            st = {"waiting": "Waiting" + (" (due now)" if j["due"] <= now else ""),
                  "uploading": "Uploading…", "done": "✓ Uploaded" + (f" · {j['note']}" if j.get("note") else ""),
                  "failed": "✕ Failed"}[j["status"]]
            if j.get("error") and j["status"] != "done":
                st += f" · {j['error'][:140]}"
            it = QTreeWidgetItem([when, j.get("title") or j["video"], f"{publish.PLATFORMS[j['platform']]} · "
                                                                        f"{j.get('account', '')}", st])
            it.setToolTip(3, j.get("error") or j.get("url") or "")
            it.setData(0, Qt.UserRole, j["id"])
            if j["status"] == "failed":
                it.setForeground(3, _brush(theme.BAD))
            elif j["status"] == "done":
                it.setForeground(3, _brush(theme.GOOD))
            self.tree.addTopLevelItem(it)
            if j["id"] in sel:
                it.setSelected(True)
        w = sum(1 for j in q if j["status"] == "waiting")
        nxt = min((j["due"] for j in q if j["status"] == "waiting"), default=0)
        self.q_info.setText(f"{w} waiting" + (f" · next {time.strftime('%H:%M', time.localtime(nxt))}" if nxt else "")
                            + ("" if self.t_auto.isChecked() else " · auto-upload is off"))

    def _ids(self) -> list:
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]

    def upload_now(self):
        for i in self._ids():
            uploadqueue.set_status(i, due=time.time() - 1)
        self.refresh_queue()
        self.queue_changed.emit()

    def retry(self):
        for j in uploadqueue.load():
            if j["id"] in self._ids() and j["status"] == "failed":
                uploadqueue.set_status(j["id"], status="waiting", due=time.time(), attempts=0)
        self.refresh_queue()
        self.queue_changed.emit()

    def remove(self):
        ids = self._ids()
        if ids and QMessageBox.question(self, "Remove", f"Remove {len(ids)} upload(s) from the queue?") == QMessageBox.Yes:
            for i in ids:
                uploadqueue.remove(i)
            self.refresh_queue()

    def clear_done(self):
        uploadqueue.clear_finished()
        self.refresh_queue()

    def _open(self, it):
        if not it:
            return
        j = next((j for j in uploadqueue.load() if j["id"] == it.data(0, Qt.UserRole)), None)
        if j and j.get("url"):
            QDesktopServices.openUrl(QUrl(j["url"]))

    # ------------------------------------------------------------------
    def setup_keys(self, platform: str):
        if KeysDialog(self.s, platform, self).exec():
            self.cards[platform].refresh()

    def connect(self, platform: str):
        if not publish.credentials_ok(platform, self.s):
            self.setup_keys(platform)
            if not publish.credentials_ok(platform, self.s):
                return
        if self._conn and self._conn.isRunning():
            publish.CANCEL.set()
            return
        c = self.cards[platform]
        c.conn.setText("  Cancel sign-in")
        c.status.setText("Your browser opened the sign-in page. Sign in and allow access, then come back here.")
        self._conn = ConnectWorker(platform, self.s.copy())
        self._conn.done.connect(self._connected)
        self._conn.start()

    def _connected(self, platform: str, msg: str):
        self.cards[platform].refresh()
        if msg.startswith("ok:"):
            QMessageBox.information(self, "Connected", f"{publish.PLATFORMS[platform]} connected: {msg[3:]}")
            if platform not in (self.s.upload_platforms or []):
                self.cards[platform].use.setChecked(True)
        elif msg != "Sign-in cancelled.":
            QMessageBox.warning(self, f"{publish.PLATFORMS[platform]} sign-in", msg)

    def disconnect(self, platform: str, acc_id: str, name: str):
        if QMessageBox.question(self, "Disconnect", f"Disconnect {name}?") == QMessageBox.Yes:
            publish.remove_account(platform, acc_id)
            self.cards[platform].refresh()

    def _save(self, *_):
        s = self.s
        s.auto_upload = self.t_auto.isChecked()
        s.upload_platforms = [p for p, c in self.cards.items() if c.use.isChecked()]
        s.upload_gap_min = self.gmin.value()
        s.upload_gap_max = max(self.gmin.value(), self.gmax.value())
        s.upload_daily_cap = self.cap.value()
        s.upload_quiet_start, s.upload_quiet_end = self.qs.value(), self.qe.value()
        s.yt_privacy = self.priv.currentData()
        s.tiktok_audited = self.tt_ok.isChecked()
        s.save()
        self.refresh_queue()


def _brush(c: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(c))
