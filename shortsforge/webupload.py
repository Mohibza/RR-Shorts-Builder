"""Direct sign-in uploads (no developer keys): the app's own Chrome profile fills in the platforms' normal upload
pages - YouTube Studio, TikTok Studio and Facebook Reels - exactly like you would, then publishes.

* Sign-in happens once per platform in a normal Chrome window (same private profile as the YouTube download
  login), so there are no API keys, audits or private-only limits.
* Uploads run in a minimized window of that profile, driven over Chrome's DevTools protocol.
* If a platform shows a security check (captcha / verification), the app stops and asks you to complete it
  yourself in the browser - it never tries to get around those checks.
* The pages are designed for people, so a platform redesign can break a step. When a step fails the app saves a
  screenshot in %APPDATA%\\RRShortsBuilder\\upload_errors so the problem is easy to see (and fix).
* Uploading through the websites with automation is outside YouTube / TikTok / Meta's official API route; use
  sensible daily limits (Publish page) and keep an eye on the first uploads.
"""
from __future__ import annotations

import base64
import json
import os
import re as _re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from . import browser_login
from .config import data_dir
from .publish import PublishError, _upsert
from .utils import NO_WINDOW

ERR_DIR = data_dir() / "upload_errors"
Progress = Callable[[float], None]

LOGIN = {
    "youtube": {"url": "https://accounts.google.com/ServiceLogin?service=youtube&continue="
                       "https%3A%2F%2Fstudio.youtube.com%2F",
                "domain": "youtube.com", "cookies": {"SID", "__Secure-3PSID", "SAPISID", "LOGIN_INFO"},
                "name": "YouTube (app browser)"},
    "tiktok": {"url": "https://www.tiktok.com/login", "domain": "tiktok.com",
               "cookies": {"sessionid", "sessionid_ss", "sid_tt"}, "name": "TikTok (app browser)"},
    "facebook": {"url": "https://www.facebook.com/login", "domain": "facebook.com", "cookies": {"c_user"},
                 "name": "Facebook (app browser)"},
    "instagram": {"url": "https://www.instagram.com/accounts/login/", "domain": "instagram.com",
                  "cookies": {"sessionid", "ds_user_id"}, "name": "Instagram (app browser)"},
}
PROFILES = data_dir() / "profiles"      # one private browser profile per signed-in account
UPLOAD_URL = {"youtube": "https://www.youtube.com/upload",
              "tiktok": "https://www.tiktok.com/tiktokstudio/upload?from=webapp",
              "facebook": "https://www.facebook.com/reels/create/",
              "instagram": "https://www.instagram.com/"}
OPEN_URL = {"youtube": "https://studio.youtube.com/", "tiktok": "https://www.tiktok.com/tiktokstudio",
            "facebook": "https://www.facebook.com/", "instagram": "https://www.instagram.com/"}


NICE = {"youtube": "YouTube", "tiktok": "TikTok", "facebook": "Facebook", "instagram": "Instagram"}


def profile_dir(acc: Optional[dict]) -> Path:
    """Browser profile of an account. The first/legacy account ("browser") shares the YouTube download login."""
    key = (acc or {}).get("profile") or ""
    return PROFILES / key if key else browser_login.PROFILE


def new_profile_key(platform: str) -> str:
    import uuid
    return f"{platform}-{uuid.uuid4().hex[:8]}"


class LoginNeeded(PublishError):
    pass


# ================================================================ small DevTools client
JS_LIB = r"""
(() => {
  if (window.__rr) return window.__rr;
  const deepAll = (sel, root = document) => {
    const out = [];
    const walk = (node) => {
      if (!node) return;
      if (node.querySelectorAll) node.querySelectorAll(sel).forEach(e => out.push(e));
      const all = node.querySelectorAll ? node.querySelectorAll('*') : [];
      for (const el of all) if (el.shadowRoot) walk(el.shadowRoot);
    };
    walk(root);
    return out;
  };
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const enabled = (el) => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true'
      && el.getAttribute('data-disabled') !== 'true' && !el.hasAttribute('disabled');
  const q = (sels) => {
    for (const s of [].concat(sels)) { const hit = deepAll(s).find(visible); if (hit) return hit; }
    return null;
  };
  const qAny = (sels) => { for (const s of [].concat(sels)) { const hit = deepAll(s)[0]; if (hit) return hit; } return null; };
  const norm = (t) => (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const byText = (texts, sel = 'button,[role="button"],ytcp-button,tp-yt-paper-radio-button,a,div[tabindex]') => {
    const want = [].concat(texts).map(norm);
    const els = deepAll(sel).filter(visible);
    for (const w of want) {
      const hit = els.find(e => norm(e.innerText) === w || norm(e.getAttribute('aria-label')) === w);
      if (hit) return hit;
    }
    return null;
  };
  const pageText = () => norm(document.body ? document.body.innerText : '');
  window.__rr = { deepAll, visible, enabled, q, qAny, byText, norm, pageText };
  return window.__rr;
})();
"""


class Chrome:
    """The app's browser profile, started with DevTools on a private local port."""

    def __init__(self, visible: bool = False, profile: Optional[Path] = None):
        self.visible = visible
        self.profile = Path(profile or browser_login.PROFILE)
        self.proc = None
        self.ws = None
        self._id = 0
        self.session = None
        self.target = None

    # -- lifecycle
    def __enter__(self):
        from websockets.sync.client import connect
        b = browser_login.find_browser()
        if not b:
            raise PublishError("Chrome or Edge was not found on this PC.")
        self.profile.mkdir(parents=True, exist_ok=True)
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        args = [b[1], *browser_login._base_flags(self.profile), "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1", "--remote-allow-origins=*", "--mute-audio",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows", "--window-size=1366,900", "about:blank"]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW)
        try:
            port, path = browser_login._wait_port(40, self.profile)
        except RuntimeError:
            self._kill()
            raise PublishError("The app's browser is busy (is its window open?). Close it; the upload will "
                               "retry automatically.", retry=True)
        self.ws = connect(f"ws://127.0.0.1:{port}{path}", max_size=64 * 1024 * 1024, open_timeout=15)
        return self

    def __exit__(self, *exc):
        try:
            self.send("Browser.close", timeout=5)
        except Exception:
            pass
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self._kill()

    def _kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass

    # -- protocol
    def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = "page",
             timeout: float = 30) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        sid = self.session if session == "page" else session
        if sid and not method.startswith(("Target.", "Browser.", "Storage.")):
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))
        end = time.time() + timeout
        while time.time() < end:
            data = json.loads(self.ws.recv(timeout=max(0.1, end - time.time())))
            if data.get("id") == self._id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error'].get('message')}")
                return data.get("result", {})
        raise TimeoutError(method)

    def open(self, url: str):
        t = self.send("Target.createTarget", {"url": "about:blank"})
        self.target = t["targetId"]
        self.session = self.send("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
        self.send("Page.enable")
        try:   # extra tab from the command line isn't needed
            for info in self.send("Target.getTargets").get("targetInfos", []):
                if info.get("type") == "page" and info["targetId"] != self.target:
                    self.send("Target.closeTarget", {"targetId": info["targetId"]})
        except Exception:
            pass
        if not self.visible:
            try:
                w = self.send("Browser.getWindowForTarget", {"targetId": self.target})
                self.send("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {"windowState": "minimized"}})
            except Exception:
                pass
        self.send("Page.navigate", {"url": url})
        time.sleep(2)

    def js(self, expr: str, timeout: float = 30):
        r = self.send("Runtime.evaluate", {"expression": JS_LIB + ";(() => {" + expr + "})()",
                                           "awaitPromise": True, "returnByValue": True, "userGesture": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "page script error"))
        return r.get("result", {}).get("value")

    def wait(self, expr: str, timeout: float, what: str, interval: float = 1.0, check=None):
        end = time.time() + timeout
        while time.time() < end:
            if check:
                check()
            try:
                v = self.js(expr)
            except Exception:
                v = None
            if v:
                return v
            time.sleep(interval)
        raise TimeoutError(what)

    def wait_el(self, find_expr: str, timeout: float, what: str, check=None, interval: float = 1.0):
        """Wait until the element found by `find_expr` (a 'return …' snippet) exists."""
        return self.wait(f"return !!(() => {{ {find_expr} }})()", timeout, what, interval, check)

    def element(self, find_expr: str):
        r = self.send("Runtime.evaluate", {"expression": JS_LIB + ";(() => {" + find_expr + "})()",
                                           "returnByValue": False})
        oid = r.get("result", {}).get("objectId")
        if not oid:
            raise RuntimeError("element not found")
        return oid

    def set_file(self, find_expr: str, path: str):
        self.send("DOM.enable")
        self.send("DOM.setFileInputFiles", {"files": [str(Path(path).resolve())], "objectId": self.element(find_expr)})

    def click(self, find_expr: str) -> bool:
        """Real mouse click in the middle of the element (falls back to a script click)."""
        rect = self.js(f"const e = (() => {{ {find_expr} }})(); if (!e) return null; e.scrollIntoView({{block:'center'}});"
                       "const r = e.getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2, r.width];")
        if not rect:
            return False
        x, y, w = rect
        if w:
            for t in ("mousePressed", "mouseReleased"):
                self.send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
        else:
            self.js(f"const e = (() => {{ {find_expr} }})(); e && e.click(); return true;")
        return True

    def type(self, find_expr: str, text: str, replace: bool = True):
        ok = self.js(f"const e = (() => {{ {find_expr} }})(); if (!e) return false; e.scrollIntoView({{block:'center'}});"
                     "e.focus(); if (" + ("true" if replace else "false") + ") { document.execCommand('selectAll', false); }"
                     "return true;")
        if not ok:
            raise RuntimeError("text box not found")
        if replace:
            self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace",
                                                 "windowsVirtualKeyCode": 8})
            self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace",
                                                 "windowsVirtualKeyCode": 8})
        for i, line in enumerate(text.split("\n")):
            if i:   # shift+enter = a new line inside the box (never submits the form)
                self.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "text": "\r",
                                                     "unmodifiedText": "\r", "windowsVirtualKeyCode": 13,
                                                     "modifiers": 8})
                self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                                     "windowsVirtualKeyCode": 13, "modifiers": 8})
            if line:
                self.send("Input.insertText", {"text": line})

    def key(self, name: str, code: int):
        for t in ("keyDown", "keyUp"):
            self.send("Input.dispatchKeyEvent", {"type": t, "key": name, "code": name, "windowsVirtualKeyCode": code})

    def url(self) -> str:
        try:
            return self.js("return location.href") or ""
        except Exception:
            return ""

    def screenshot(self, name: str) -> str:
        try:
            ERR_DIR.mkdir(parents=True, exist_ok=True)
            data = self.send("Page.captureScreenshot", {"format": "png"}, timeout=20)["data"]
            p = ERR_DIR / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.png"
            p.write_bytes(base64.b64decode(data))
            return str(p)
        except Exception:
            return ""

    def cookies(self) -> list[dict]:
        return self.send("Storage.getCookies", session=None).get("cookies", [])


# ================================================================ sign-in
def open_sign_in(platform: str, profile: Optional[Path] = None) -> subprocess.Popen:
    """Normal browser window (no automation) of that account's profile on the platform's sign-in page."""
    b = browser_login.find_browser()
    if not b:
        raise PublishError("Chrome or Edge was not found on this PC.")
    prof = Path(profile or browser_login.PROFILE)
    prof.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([b[1], *browser_login._base_flags(prof), "--new-window", LOGIN[platform]["url"]])


def open_page(platform: str, profile: Optional[Path] = None) -> subprocess.Popen:
    """Open the platform in that account's browser (e.g. to complete a security check)."""
    b = browser_login.find_browser()
    if not b:
        raise PublishError("Chrome or Edge was not found on this PC.")
    return subprocess.Popen([b[1], *browser_login._base_flags(Path(profile or browser_login.PROFILE)),
                             "--new-window", OPEN_URL[platform]])


def check_login(platform: str, profile: Optional[Path] = None) -> bool:
    """Is this account's browser signed in to the platform? (reads its cookies in a hidden run)"""
    info = LOGIN[platform]
    with browser_login.BROWSER_LOCK:
        with Chrome(visible=False, profile=profile) as c:
            names = {x["name"] for x in c.cookies() if info["domain"] in x.get("domain", "")}
    return bool(names & info["cookies"])


def register(platform: str, profile_key: str = "", label: str = "") -> dict:
    """Add a signed-in account (browser mode). profile_key "" = the shared main profile."""
    acc_id = f"browser:{profile_key}" if profile_key else "browser"
    return _upsert(platform, {"id": acc_id, "name": label.strip() or LOGIN[platform]["name"], "mode": "browser",
                              "profile": profile_key})


def remove_profile(acc: dict) -> None:
    key = (acc or {}).get("profile")
    if key:
        import shutil
        shutil.rmtree(PROFILES / key, ignore_errors=True)


# ================================================================ uploads
def _guard(c: Chrome, platform: str):
    """Stop on sign-in pages and security checks (never bypass them)."""
    u = c.url()
    if any(k in u for k in ("accounts.google.com", "/login", "/signin")):
        raise LoginNeeded(f"The app's browser is signed out of {NICE[platform]}. Publish page → Sign in again.")
    try:
        found = c.js("const t = __rr.pageText(); return ["
                     "!!__rr.q(['#captcha-verify-container','.captcha_verify_container','iframe[src*=\"captcha\"]',"
                     "'[id*=\"captcha\" i]']), t.includes('verify it') || t.includes('security check') || "
                     "t.includes('confirm it\\'s you') || t.includes(\"confirm it's you\"), "
                     "/checkpoint|challenge/.test(location.pathname)]")
    except Exception:
        found = None
    if found and any(found):
        raise PublishError(f"{NICE[platform]} asked for a security check. Publish page → Open browser, "
                           "complete it, then click Retry.", retry=False)


def _fail(c: Chrome, platform: str, step: str, e: Exception) -> PublishError:
    shot = c.screenshot(f"{platform}_{step}")
    if isinstance(e, PublishError):
        return e
    msg = f"{NICE[platform]} upload stopped at “{step}”"
    if shot:
        msg += f" (screenshot: {shot})"
    return PublishError(msg + ". It will retry; if it keeps failing, the site layout may have changed.",
                        retry=True)


def _youtube(c: Chrome, video: str, meta: dict, s, progress: Progress) -> dict:
    step = "open upload page"
    try:
        c.open(UPLOAD_URL["youtube"])
        c.wait("return document.readyState === 'complete' && location.href !== 'about:blank'", 60, step)
        time.sleep(2)
        _guard(c, "youtube")
        step = "choose file"
        c.wait("return !!__rr.qAny('input[type=file]')", 60, step, check=lambda: _guard(c, "youtube"))
        c.set_file("return __rr.qAny('input[type=file]')", video)
        progress(0.1)
        step = "title"
        title_q = "return __rr.q(['#title-textarea #textbox', 'ytcp-social-suggestions-textbox#title-textarea #textbox'])"
        c.wait(title_q.replace("return ", "return !!"), 90, step)
        time.sleep(1.5)
        title = (meta.get("title") or "New Short").replace("<", "").replace(">", "")[:100]
        c.type(title_q, title)
        step = "description"
        desc_q = "return __rr.q(['#description-textarea #textbox', 'ytcp-social-suggestions-textbox#description-textarea #textbox'])"
        c.type(desc_q, (meta.get("description") or "").replace("<", "").replace(">", "")[:4900])
        step = "made for kids"
        c.click("return __rr.q(['tp-yt-paper-radio-button[name=\"VIDEO_MADE_FOR_KIDS_NOT_MFK\"]', "
                "'[name=\"VIDEO_MADE_FOR_KIDS_NOT_MFK\"]'])")
        tags = [t for t in (meta.get("tags") or []) if t][:15]
        if tags:                                   # optional: never fail an upload over tags
            try:
                c.click("return __rr.q(['#toggle-button'])")
                time.sleep(1)
                tq = "return __rr.q(['#tags-container #text-input', 'input[aria-label*=\"Tags\" i]'])"
                if c.js(tq.replace("return ", "return !!")):
                    c.type(tq, ", ".join(tags)[:450] + ",", replace=False)
            except Exception:
                pass
        step = "next steps"
        for _ in range(4):
            if c.js("return !!__rr.q('tp-yt-paper-radio-button[name=\"PUBLIC\"]')"):
                break
            c.click("return __rr.q('#next-button')")
            time.sleep(1.5)
        step = "visibility"
        vis = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}.get(getattr(s, "yt_privacy", "public"),
                                                                                    "PUBLIC")
        c.wait(f"return !!__rr.q('tp-yt-paper-radio-button[name=\"{vis}\"]')", 60, step)
        c.click(f"return __rr.q('tp-yt-paper-radio-button[name=\"{vis}\"]')")
        step = "wait for upload"
        end = time.time() + 45 * 60
        while time.time() < end:
            _guard(c, "youtube")
            st = c.js("const b = __rr.q('#done-button'); const p = __rr.q(['ytcp-video-upload-progress', "
                      "'.progress-label']); return [b ? __rr.enabled(b) : false, p ? p.innerText : '']") or [False, ""]
            m = _re.search(r"(\d{1,3})\s*%", st[1] or "")
            if m:
                progress(0.1 + 0.8 * min(100, int(m.group(1))) / 100)
            if st[0] and not _re.search(r"uploading|upload\s+\d", (st[1] or "").lower()):
                break
            if "daily upload limit" in (c.js("return __rr.pageText()") or ""):
                raise PublishError("YouTube's daily upload limit for this channel is reached. Will retry tomorrow.",
                                   retry=True)
            time.sleep(3)
        else:
            raise TimeoutError(step)
        step = "publish"
        c.click("return __rr.q('#done-button')")
        link = c.wait("const a = __rr.q(['ytcp-video-info a', '#share-url', 'a.ytcp-video-info', "
                      "'.video-url-fadeable a']); if (a) return a.href || a.innerText; "
                      "return __rr.q(['ytcp-uploads-still-processing-dialog', 'ytcp-video-share-dialog', "
                      "'ytcp-prechecks-warning-dialog']) ? 'ok' : null", 120, step)
        progress(1.0)
        url = link if isinstance(link, str) and link.startswith("http") else "https://studio.youtube.com/"
        return {"id": url.rsplit("/", 1)[-1], "url": url, "note": f"{vis.lower()} (browser)"}
    except Exception as e:
        raise _fail(c, "youtube", step, e)


def _tiktok(c: Chrome, video: str, meta: dict, s, progress: Progress) -> dict:
    step = "open upload page"
    try:
        c.open(UPLOAD_URL["tiktok"])
        time.sleep(3)
        _guard(c, "tiktok")
        step = "choose file"
        c.wait("return !!__rr.qAny(['input[type=file][accept*=\"video\"]', 'input[type=file]'])", 60, step,
               check=lambda: _guard(c, "tiktok"))
        c.set_file("return __rr.qAny(['input[type=file][accept*=\"video\"]', 'input[type=file]'])", video)
        progress(0.1)
        step = "caption"
        cap_q = "return __rr.q(['.public-DraftEditor-content', 'div[contenteditable=\"true\"]'])"
        c.wait(cap_q.replace("return ", "return !!"), 120, step, check=lambda: _guard(c, "tiktok"))
        time.sleep(2)
        caption = (meta.get("title", "") + " " + " ".join(meta.get("hashtags") or [])).strip()[:2000]
        c.type(cap_q, caption + " ")
        time.sleep(1)
        c.key("Escape", 27)                       # close the hashtag suggestion list
        step = "wait for upload"
        post_q = ("const b = __rr.q(['button[data-e2e=\"post_video_button\"]']) || __rr.byText(['Post'], 'button'); "
                  "return b")
        end = time.time() + 30 * 60
        while time.time() < end:
            _guard(c, "tiktok")
            st = c.js(post_q.replace("return b", "const t = __rr.pageText(); return [b ? __rr.enabled(b) : false, "
                                                 "/uploading|\\d+%/.test(t) && !/uploaded/.test(t)]")) or [False, True]
            if st[0] and not st[1]:
                break
            time.sleep(3)
        else:
            raise TimeoutError(step)
        progress(0.9)
        step = "post"
        c.click(post_q)
        end = time.time() + 120
        while time.time() < end:
            time.sleep(2)
            c.click("return __rr.byText(['Post now', 'Continue to post', 'Post anyway'], 'button')")
            done = c.js("const t = __rr.pageText(); return location.href.includes('/tiktokstudio/content') || "
                        "t.includes('your video has been uploaded') || t.includes('video published') || "
                        "t.includes('manage your posts')")
            if done:
                progress(1.0)
                return {"id": "", "url": "https://www.tiktok.com/tiktokstudio/content", "note": "posted (browser)"}
            _guard(c, "tiktok")
        raise TimeoutError("confirm post")
    except Exception as e:
        raise _fail(c, "tiktok", step, e)


def _facebook(c: Chrome, video: str, meta: dict, s, progress: Progress) -> dict:
    step = "open reels page"
    try:
        c.open(UPLOAD_URL["facebook"])
        time.sleep(3)
        _guard(c, "facebook")
        step = "choose file"
        c.wait("return !!__rr.qAny(['input[type=file][accept*=\"video\"]', 'input[type=file]'])", 60, step,
               check=lambda: _guard(c, "facebook"))
        c.set_file("return __rr.qAny(['input[type=file][accept*=\"video\"]', 'input[type=file]'])", video)
        progress(0.2)
        nxt = "return __rr.byText(['Next'], '[role=\"button\"],button')"
        step = "next"
        for _ in range(2):
            c.wait(f"const b = (() => {{ {nxt} }})(); return b && __rr.enabled(b)", 180, step)
            c.click(nxt)
            time.sleep(2.5)
        step = "description"
        box = "return __rr.q(['div[role=\"textbox\"][contenteditable=\"true\"]'])"
        c.wait(box.replace("return ", "return !!"), 60, step)
        text = (meta.get("title", "") + "\n\n" + meta.get("description", "")).strip()[:2000]
        c.type(box, text)
        step = "publish"
        pub = "return __rr.byText(['Publish', 'Share now', 'Post', 'Share reel'], '[role=\"button\"],button')"
        c.wait(f"const b = (() => {{ {pub} }})(); return b && __rr.enabled(b)", 20 * 60, step,
               interval=3, check=lambda: _guard(c, "facebook"))
        c.click(pub)
        c.wait("const t = __rr.pageText(); return !location.href.includes('/reels/create') || "
               "t.includes('your reel is') || t.includes('reel published') || t.includes('being processed')",
               180, "confirm publish")
        progress(1.0)
        return {"id": "", "url": "https://www.facebook.com/reels/", "note": "published (browser)"}
    except Exception as e:
        raise _fail(c, "facebook", step, e)


def _instagram(c: Chrome, video: str, meta: dict, s, progress: Progress) -> dict:
    step = "open instagram"
    btn = "button,[role=\"button\"],a,[role=\"link\"],div[tabindex]"
    try:
        c.open(UPLOAD_URL["instagram"])
        time.sleep(3)
        _guard(c, "instagram")
        for txt in (["Not now", "Not Now"], ["Allow all cookies", "Decline optional cookies"]):
            c.click(f"return __rr.byText({txt!r}, 'button,[role=\"button\"]')")   # dismiss pop-ups if any
        step = "create"
        create = ("const i = __rr.q(['svg[aria-label=\"New post\"]', 'svg[aria-label=\"Create\"]']); "
                  "if (i) return i.closest('a,[role=\"link\"],[role=\"button\"],div[tabindex]') || i; "
                  f"return __rr.byText(['Create', 'New post'], '{btn}')")
        c.wait_el(create, 40, step, check=lambda: _guard(c, "instagram"))
        c.click(create)
        time.sleep(1.5)
        c.click(f"return __rr.byText(['Post'], '{btn}')")      # newer menu: Post / Live video / Ad
        step = "choose file"
        c.wait("return !!__rr.qAny('input[type=file][accept*=\"video\"], input[type=file]')", 40, step)
        c.set_file("return __rr.qAny(['input[type=file][accept*=\"video\"]', 'input[type=file]'])", video)
        progress(0.15)
        time.sleep(2.5)
        c.click("return __rr.byText(['OK'], 'button')")        # "Video posts are now shared as reels"
        step = "crop"
        try:                                                     # keep the full 9:16 frame
            c.click("const i = __rr.q('svg[aria-label=\"Select crop\"]'); return i && (i.closest('button,[role=\"button\"]') || i)")
            time.sleep(0.8)
            c.click(f"return __rr.byText(['Original'], '{btn},span')")
        except Exception:
            pass
        step = "next"
        nxt = f"return __rr.byText(['Next'], '{btn}')"
        for _ in range(2):
            c.wait_el(nxt, 90, step)
            c.click(nxt)
            time.sleep(2)
        step = "caption"
        box = ("return __rr.q(['div[aria-label=\"Write a caption...\"][contenteditable=\"true\"]', "
               "'div[role=\"textbox\"][contenteditable=\"true\"]'])")
        c.wait_el(box, 60, step)
        cap = (meta.get("title", "") + "\n\n" + meta.get("description", "")).strip()[:2150]
        c.type(box, cap)
        step = "share"
        share = f"return __rr.byText(['Share'], '{btn}')"
        c.wait_el(share, 30, step)
        c.click(share)
        progress(0.6)
        c.wait("const t = __rr.pageText(); return t.includes('your reel has been shared') || "
               "t.includes('reel shared') || t.includes('your post has been shared') || "
               "!!__rr.q('img[alt=\"Animated checkmark\"]')", 20 * 60, "confirm share", interval=3,
               check=lambda: _guard(c, "instagram"))
        progress(1.0)
        return {"id": "", "url": "https://www.instagram.com/", "note": "reel shared (browser)"}
    except Exception as e:
        raise _fail(c, "instagram", step, e)


FLOW = {"youtube": _youtube, "tiktok": _tiktok, "facebook": _facebook, "instagram": _instagram}


def upload(platform: str, video: str, meta: dict, s, progress: Progress = lambda f: None,
           acc: Optional[dict] = None) -> dict:
    if not Path(video).exists():
        raise PublishError("The video file was moved or deleted.")
    visible = bool(getattr(s, "web_upload_visible", False))
    with browser_login.BROWSER_LOCK:
        with Chrome(visible=visible, profile=profile_dir(acc)) as c:
            return FLOW[platform](c, video, meta, s, progress)
