"""Auto-upload through the platforms' official APIs: YouTube (Data API v3), Facebook Pages (Reels API) and
TikTok (Content Posting API).

Sign-in opens the platform's own page in your normal browser (OAuth); the app catches the answer on a local
address (127.0.0.1 / localhost) and keeps the tokens on this PC, encrypted with Windows DPAPI.

Each platform needs a free developer app that you own (Google Cloud / Meta / TikTok for Developers): the
Publish page links to a step-by-step guide. Limits that come from the platforms, not from this app:
  * YouTube: videos from API projects Google hasn't audited stay locked Private (request the free audit once).
    Uploads use about 1,600 of the 10,000 daily quota units, so ~6 uploads per day per project.
  * TikTok: apps TikTok hasn't audited can only post "Only me" (private) videos.
  * Facebook: posts Reels to Pages you manage (personal profiles have no upload API).
"""
from __future__ import annotations

import base64
import hashlib
import os
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from .config import data_dir
from .secure import dpapi

ACCOUNTS_FILE = data_dir() / "accounts.bin"
GRAPH = "https://graph.facebook.com/v25.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v25.0"
FB_REDIRECT = "http://localhost:53682/"
TT_REDIRECT = "http://localhost:53683/callback/"
PLATFORMS = {"youtube": "YouTube", "facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok"}
UA = "RRShortsBuilder/1.5"

# overridable for tests
ENDPOINTS = {
    "google_auth": "https://accounts.google.com/o/oauth2/v2/auth",
    "google_token": "https://oauth2.googleapis.com/token",
    "yt_api": "https://www.googleapis.com/youtube/v3",
    "yt_upload": "https://www.googleapis.com/upload/youtube/v3/videos",
    "fb_dialog": "https://www.facebook.com/v25.0/dialog/oauth",
    "graph": GRAPH,
    "rupload": RUPLOAD,
    "tt_auth": "https://www.tiktok.com/v2/auth/authorize/",
    "tt_api": "https://open.tiktokapis.com/v2",
}

Progress = Callable[[float], None]
CANCEL = threading.Event()      # set by the UI to stop waiting for a browser sign-in


class PublishError(RuntimeError):
    """Upload/sign-in problem with a message fit to show the user."""

    def __init__(self, msg: str, retry: bool = False, wait: float = 0):
        super().__init__(msg)
        self.retry = retry          # temporary (network, 5xx, rate limit): try again later
        self.wait = wait            # >0: "busy right now" - try again after `wait` s without using up an attempt


# ================================================================ secure storage (Windows DPAPI)
_dpapi = dpapi
_lock = threading.RLock()


def load_accounts() -> dict:
    with _lock:
        try:
            raw = ACCOUNTS_FILE.read_bytes()
        except OSError:
            return {p: [] for p in PLATFORMS}
        try:
            data = json.loads(_dpapi(raw, False).decode("utf-8"))
        except Exception:
            return {p: [] for p in PLATFORMS}
        for p in PLATFORMS:
            data.setdefault(p, [])
        return data


def save_accounts(data: dict) -> None:
    with _lock:
        ACCOUNTS_FILE.write_bytes(_dpapi(json.dumps(data).encode("utf-8"), True))


def _upsert(platform: str, acc: dict) -> dict:
    with _lock:
        data = load_accounts()
        lst = list(data[platform])
        i = next((n for n, a in enumerate(lst) if a.get("id") == acc["id"]), None)
        old = lst[i] if i is not None else {}
        acc = {**old, "enabled": old.get("enabled", True), **acc}   # keep settings like the proxy
        if i is None:
            lst.append(acc)
        else:
            lst[i] = acc                                             # keep the account's place in the list
        data[platform] = lst
        save_accounts(data)
        return acc


def remove_account(platform: str, acc_id: str) -> None:
    with _lock:
        data = load_accounts()
        for a in data.get(platform, []):
            if a.get("id") == acc_id and a.get("mode") == "browser" and a.get("profile"):
                from . import webupload          # also delete that account's private browser profile
                webupload.remove_profile(a)
        data[platform] = [a for a in data[platform] if a.get("id") != acc_id]
        save_accounts(data)


def set_enabled(platform: str, acc_id: str, on: bool) -> None:
    with _lock:
        data = load_accounts()
        for a in data[platform]:
            if a.get("id") == acc_id:
                a["enabled"] = on
        save_accounts(data)


def enabled_accounts(platform: str) -> list[dict]:
    return [a for a in load_accounts().get(platform, []) if a.get("enabled", True)]


# ================================================================ HTTP helpers
def _req(method: str, url: str, data=None, headers: Optional[dict] = None, form: Optional[dict] = None,
         js=None, timeout: float = 60, raw: bool = False):
    h = {"User-Agent": UA, **(headers or {})}
    body = data
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif js is not None:
        body = json.dumps(js).encode("utf-8")
        h.setdefault("Content-Type", "application/json; charset=UTF-8")
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
    except urllib.error.HTTPError as e:
        if raw:
            return e
        txt = ""
        try:
            txt = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise _http_error(e.code, txt)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise PublishError(f"Network problem: {getattr(e, 'reason', e)}. Will retry.", retry=True)
    if raw:
        return resp
    txt = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(txt) if txt.strip() else {}
    except json.JSONDecodeError:
        return {"_text": txt}


def _http_error(code: int, body: str) -> PublishError:
    msg = body[:400]
    try:
        j = json.loads(body)
        err = j.get("error", j)
        if isinstance(err, dict):
            msg = err.get("message") or err.get("error_description") or err.get("error_user_msg") or msg
            reason = ""
            if isinstance(err.get("errors"), list) and err["errors"]:
                reason = err["errors"][0].get("reason", "")
            if reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
                return PublishError("YouTube's daily upload quota is used up. The app will try again tomorrow.",
                                    retry=True)
        elif isinstance(err, str):
            msg = j.get("error_description") or err
    except Exception:
        pass
    retry = code == 429 or code >= 500
    if code in (401, 403) and not retry:
        msg = f"Access refused ({code}): {msg}"
    return PublishError(f"{msg} (HTTP {code})", retry=retry)


# ================================================================ OAuth loopback helper
class _Catcher(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in q.items()}
        ok = "code" in q
        page = ("<html><body style='font-family:Segoe UI,Arial;background:#0B0D12;color:#E8EAF0;text-align:center;"
                "padding-top:18vh'><h2>" + ("✓ Connected to Rebels Revolt Shorts" if ok else "Sign-in was cancelled")
                + "</h2><p>You can close this tab and go back to the app.</p></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def log_message(self, *a):
        pass


def _browser_auth(auth_url_for: Callable[[str], str], host: str, port: int, path: str = "/",
                  timeout: float = 300, open_browser: Callable[[str], None] = webbrowser.open) -> tuple[dict, str]:
    """Open the sign-in page, wait for the redirect to http://host:port/path. Returns (query, redirect_uri)."""
    class H(_Catcher):
        result = {}

    class V6(http.server.HTTPServer):
        address_family = socket.AF_INET6

    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), H)
    except OSError:
        raise PublishError(f"Port {port} is busy (another sign-in window open?). Close it and try again.")
    port = srv.server_address[1]
    servers = [srv]
    if host == "localhost":        # browsers may send "localhost" to IPv6 (::1): listen there too
        try:
            servers.append(V6(("::1", port), H))
        except OSError:
            pass
    redirect = f"http://{host}:{port}{path}"
    for sv in servers:
        sv.timeout = 0.4
    open_browser(auth_url_for(redirect))
    end = time.time() + timeout
    try:
        CANCEL.clear()
        while time.time() < end and not H.result and not CANCEL.is_set():
            for sv in servers:
                sv.handle_request()
                if H.result:
                    break
    finally:
        for sv in servers:
            sv.server_close()
    if not H.result:
        raise PublishError("Sign-in cancelled." if CANCEL.is_set() else "Sign-in timed out. Try Connect again.")
    if "error" in H.result:
        raise PublishError(f"Sign-in cancelled: {H.result.get('error_description') or H.result['error']}")
    return H.result, redirect


def _pkce(hex_digest: bool = False) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)[:64]
    d = hashlib.sha256(verifier.encode()).digest()
    challenge = d.hex() if hex_digest else base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    return verifier, challenge


# ================================================================ YouTube
YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def youtube_connect(client_id: str, client_secret: str, open_browser=webbrowser.open) -> dict:
    if not client_id.strip() or not client_secret.strip():
        raise PublishError("Add your Google OAuth Client ID and Client secret first (see the setup guide).")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)

    def url(redirect):
        return ENDPOINTS["google_auth"] + "?" + urllib.parse.urlencode({
            "client_id": client_id.strip(), "redirect_uri": redirect, "response_type": "code", "scope": YT_SCOPES,
            "access_type": "offline", "prompt": "consent", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state})
    q, redirect = _browser_auth(url, "127.0.0.1", 0, "/", open_browser=open_browser)
    if q.get("state") != state:
        raise PublishError("Sign-in answer didn't match (state). Try again.")
    tok = _req("POST", ENDPOINTS["google_token"], form={
        "code": q["code"], "client_id": client_id.strip(), "client_secret": client_secret.strip(),
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise PublishError("Google didn't return a refresh token. Remove the app's access at "
                           "myaccount.google.com/permissions and connect again.")
    ch = _req("GET", ENDPOINTS["yt_api"] + "/channels?part=snippet&mine=true",
              headers={"Authorization": f"Bearer {tok['access_token']}"})
    items = ch.get("items") or []
    if not items:
        raise PublishError("This Google account has no YouTube channel.")
    it = items[0]
    return _upsert("youtube", {"id": it["id"], "name": it["snippet"].get("title", "YouTube channel"),
                               "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
                               "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60})


def _yt_token(acc: dict, client_id: str, client_secret: str) -> str:
    if acc.get("access_token") and time.time() < float(acc.get("expires_at", 0)):
        return acc["access_token"]
    try:
        tok = _req("POST", ENDPOINTS["google_token"], form={
            "client_id": client_id, "client_secret": client_secret, "refresh_token": acc["refresh_token"],
            "grant_type": "refresh_token"})
    except PublishError as e:
        if "invalid_grant" in str(e) or "expired or revoked" in str(e):
            raise PublishError("YouTube sign-in expired. On the Publish page click Connect YouTube again. "
                               "(Tip: set your Google OAuth app to “In production” so it stays signed in.)")
        raise
    acc["access_token"] = tok["access_token"]
    acc["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    _upsert("youtube", acc)
    return acc["access_token"]


def _clean_yt(s: str, n: int) -> str:
    return s.replace("<", "").replace(">", "").strip()[:n]


def youtube_upload(acc: dict, video: str, meta: dict, s, progress: Progress = lambda f: None) -> dict:
    token = _yt_token(acc, s.yt_client_id.strip(), s.yt_client_secret.strip())
    size = Path(video).stat().st_size
    tags, total = [], 0
    for t in meta.get("tags") or []:
        t = _clean_yt(t, 60).replace(",", " ")
        if t and total + len(t) + 3 <= 480:
            tags.append(t)
            total += len(t) + 3
    title = _clean_yt(meta.get("title") or "New Short", 100)
    if "#shorts" not in title.lower() and len(title) <= 92:
        title += " #shorts"
    body = {"snippet": {"title": title, "description": _clean_yt(meta.get("description", ""), 4900),
                        "tags": tags, "categoryId": "22"},
            "status": {"privacyStatus": getattr(s, "yt_privacy", "public") or "public",
                       "selfDeclaredMadeForKids": False}}
    r = _req("POST", ENDPOINTS["yt_upload"] + "?uploadType=resumable&part=snippet,status", js=body, raw=True,
             headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Length": str(size),
                      "X-Upload-Content-Type": "video/mp4"})
    if isinstance(r, urllib.error.HTTPError):
        raise _http_error(r.code, r.read().decode("utf-8", "replace"))
    loc = r.headers.get("Location")
    if not loc:
        raise PublishError("YouTube didn't start the upload. Try again later.", retry=True)
    chunk = 8 * 1024 * 1024
    sent = 0
    tries = 0
    with open(video, "rb") as f:
        while True:
            f.seek(sent)
            data = f.read(chunk)
            end = sent + len(data) - 1
            resp = _req("PUT", loc, data=data, raw=True, timeout=300, headers={
                "Authorization": f"Bearer {token}", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {sent}-{end}/{size}", "Content-Type": "video/mp4"})
            code = resp.getcode() if not isinstance(resp, urllib.error.HTTPError) else resp.code
            if code in (200, 201):
                out = json.loads(resp.read().decode("utf-8"))
                progress(1.0)
                vid = out.get("id", "")
                note = out.get("status", {}).get("privacyStatus", "")
                cover = meta.get("cover") or ""
                if vid and cover and Path(cover).is_file() and Path(cover).stat().st_size <= 2 * 1024 * 1024:
                    # optional custom thumbnail; YouTube only accepts it for some channels/videos, so never fail on it
                    tr = _req("POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=" + vid,
                              data=Path(cover).read_bytes(), raw=True, timeout=120,
                              headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
                    ok_t = not isinstance(tr, urllib.error.HTTPError)
                    note += " + thumbnail" if ok_t else " (thumbnail not accepted by YouTube)"
                return {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "note": note}
            if code == 308:
                rng = resp.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                progress(sent / size)
                tries = 0
                continue
            if code >= 500 and tries < 5:       # resume where YouTube got to
                tries += 1
                time.sleep(2 ** tries)
                q = _req("PUT", loc, data=b"", raw=True, headers={"Authorization": f"Bearer {token}",
                                                                   "Content-Range": f"bytes */{size}"})
                qc = q.getcode() if not isinstance(q, urllib.error.HTTPError) else q.code
                if qc == 308:
                    rng = q.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    continue
            raise _http_error(code, resp.read().decode("utf-8", "replace"))


# ================================================================ Facebook Pages (Reels)
FB_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"


def _meta_login(app_id: str, app_secret: str, scopes: str, open_browser=webbrowser.open) -> str:
    """Facebook Login (Meta app) -> long-lived user token."""
    if not app_id.strip() or not app_secret.strip():
        raise PublishError("Add your Meta App ID and App secret first (see the setup guide).")
    state = secrets.token_urlsafe(16)

    def url(redirect):
        return ENDPOINTS["fb_dialog"] + "?" + urllib.parse.urlencode({
            "client_id": app_id.strip(), "redirect_uri": redirect, "state": state, "scope": scopes,
            "response_type": "code"})
    q, redirect = _browser_auth(url, "localhost", 53682, "/", open_browser=open_browser)
    if q.get("state") != state:
        raise PublishError("Sign-in answer didn't match (state). Try again.")
    g = ENDPOINTS["graph"]
    short = _req("GET", g + "/oauth/access_token?" + urllib.parse.urlencode({
        "client_id": app_id.strip(), "redirect_uri": redirect, "client_secret": app_secret.strip(),
        "code": q["code"]}))
    long = _req("GET", g + "/oauth/access_token?" + urllib.parse.urlencode({
        "grant_type": "fb_exchange_token", "client_id": app_id.strip(), "client_secret": app_secret.strip(),
        "fb_exchange_token": short["access_token"]}))
    return long["access_token"]


def facebook_connect(app_id: str, app_secret: str, open_browser=webbrowser.open) -> list[dict]:
    token = _meta_login(app_id, app_secret, FB_SCOPES, open_browser)
    g = ENDPOINTS["graph"]
    pages = _req("GET", g + "/me/accounts?" + urllib.parse.urlencode({
        "fields": "id,name,access_token,tasks", "limit": 100, "access_token": token}))
    got = []
    for p in pages.get("data") or []:
        if p.get("access_token"):
            got.append(_upsert("facebook", {"id": p["id"], "name": p.get("name", "Page"),
                                            "token": p["access_token"]}))
    if not got:
        raise PublishError("No Facebook Pages found. Reels upload works for Pages you manage (not personal "
                           "profiles). Make sure you ticked your Page when Facebook asked.")
    return got


def facebook_upload(acc: dict, video: str, meta: dict, s, progress: Progress = lambda f: None) -> dict:
    g, tok, pid = ENDPOINTS["graph"], acc["token"], acc["id"]
    start = _req("POST", f"{g}/{pid}/video_reels", form={"upload_phase": "start", "access_token": tok})
    vid, up = start.get("video_id"), start.get("upload_url") or f"{ENDPOINTS['rupload']}/{start.get('video_id')}"
    if not vid:
        raise PublishError(f"Facebook didn't start the upload: {start}", retry=True)
    size = Path(video).stat().st_size
    with open(video, "rb") as f:
        data = f.read()
    progress(0.1)
    r = _req("POST", up, data=data, timeout=600, headers={
        "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size), "Content-Type": "application/octet-stream"})
    if not r.get("success", True):
        raise PublishError(f"Facebook upload failed: {r}", retry=True)
    progress(0.8)
    desc = (meta.get("title", "") + "\n\n" + meta.get("description", "")).strip()[:2200]
    fin = _req("POST", f"{g}/{pid}/video_reels", form={
        "access_token": tok, "video_id": vid, "upload_phase": "finish", "video_state": "PUBLISHED",
        "description": desc, "title": meta.get("title", "")[:250]})
    if not fin.get("success", False):
        raise PublishError(f"Facebook didn't publish the Reel: {fin}", retry=True)
    progress(1.0)
    return {"id": vid, "url": f"https://www.facebook.com/reel/{vid}", "note": "published"}


# ================================================================ Instagram (Graph API, Business/Creator accounts)
IG_SCOPES = FB_SCOPES + ",instagram_basic,instagram_content_publish,business_management"


def instagram_connect(app_id: str, app_secret: str, open_browser=webbrowser.open) -> list[dict]:
    token = _meta_login(app_id, app_secret, IG_SCOPES, open_browser)
    g = ENDPOINTS["graph"]
    pages = _req("GET", g + "/me/accounts?" + urllib.parse.urlencode({
        "fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100,
        "access_token": token}))
    got = []
    for p in pages.get("data") or []:
        ig = p.get("instagram_business_account") or {}
        if ig.get("id") and p.get("access_token"):
            got.append(_upsert("instagram", {"id": ig["id"], "name": "@" + ig.get("username", "instagram"),
                                             "token": p["access_token"]}))
    if not got:
        raise PublishError("No Instagram Business/Creator account found. In the Instagram app switch to a "
                           "Professional account and link it to your Facebook Page, then connect again. "
                           "(Or use Sign in, which works with any account.)")
    return got


def instagram_upload(acc: dict, video: str, meta: dict, s, progress: Progress = lambda f: None) -> dict:
    g, tok, ig = ENDPOINTS["graph"], acc["token"], acc["id"]
    caption = (meta.get("title", "") + "\n\n" + meta.get("description", "")).strip()[:2150]
    cont = _req("POST", f"{g}/{ig}/media", form={"media_type": "REELS", "upload_type": "resumable",
                                                 "caption": caption, "share_to_feed": "true", "access_token": tok})
    cid = cont.get("id")
    up = cont.get("uri") or f"{ENDPOINTS['rupload'].replace('video-upload', 'ig-api-upload')}/{cid}"
    if not cid:
        raise PublishError(f"Instagram didn't start the upload: {cont}", retry=True)
    size = Path(video).stat().st_size
    with open(video, "rb") as f:
        data = f.read()
    progress(0.1)
    r = _req("POST", up, data=data, timeout=600, headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                                          "file_size": str(size)})
    if r.get("success") is False:
        raise PublishError(f"Instagram upload failed: {r}", retry=True)
    progress(0.5)
    for _ in range(120):                      # Instagram processes the video (usually < 1 minute)
        st = _req("GET", f"{g}/{cid}?" + urllib.parse.urlencode({"fields": "status_code,status",
                                                                 "access_token": tok}))
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram rejected the video: {st.get('status') or code}")
        time.sleep(5)
    else:
        raise PublishError("Instagram is still processing the video. Will retry.", retry=True)
    pub = _req("POST", f"{g}/{ig}/media_publish", form={"creation_id": cid, "access_token": tok})
    mid = pub.get("id", "")
    link = "https://www.instagram.com/"
    try:
        link = _req("GET", f"{g}/{mid}?" + urllib.parse.urlencode({"fields": "permalink",
                                                                  "access_token": tok})).get("permalink", link)
    except PublishError:
        pass
    progress(1.0)
    return {"id": mid, "url": link, "note": "reel published"}


# ================================================================ TikTok
TT_SCOPES = "user.info.basic,video.publish"


def tiktok_connect(client_key: str, client_secret: str, open_browser=webbrowser.open) -> dict:
    if not client_key.strip() or not client_secret.strip():
        raise PublishError("Add your TikTok Client key and Client secret first (see the setup guide).")
    verifier, challenge = _pkce(hex_digest=True)     # TikTok desktop: hex SHA-256
    state = secrets.token_urlsafe(16)

    def url(redirect):
        return ENDPOINTS["tt_auth"] + "?" + urllib.parse.urlencode({
            "client_key": client_key.strip(), "scope": TT_SCOPES, "response_type": "code",
            "redirect_uri": redirect, "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256"})
    q, redirect = _browser_auth(url, "localhost", 53683, "/callback/", open_browser=open_browser)
    if q.get("state") != state:
        raise PublishError("Sign-in answer didn't match (state). Try again.")
    tok = _req("POST", ENDPOINTS["tt_api"] + "/oauth/token/", form={
        "client_key": client_key.strip(), "client_secret": client_secret.strip(), "code": q["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier})
    if tok.get("error") or not tok.get("access_token"):
        raise PublishError(f"TikTok sign-in failed: {tok.get('error_description') or tok.get('error') or tok}")
    name = "TikTok account"
    try:
        u = _req("GET", ENDPOINTS["tt_api"] + "/user/info/?fields=open_id,display_name",
                 headers={"Authorization": f"Bearer {tok['access_token']}"})
        name = (u.get("data") or {}).get("user", {}).get("display_name") or name
    except PublishError:
        pass
    return _upsert("tiktok", {"id": tok["open_id"], "name": name, "access_token": tok["access_token"],
                              "refresh_token": tok.get("refresh_token", ""),
                              "expires_at": time.time() + int(tok.get("expires_in", 86400)) - 120})


def _tt_token(acc: dict, key: str, secret: str) -> str:
    if time.time() < float(acc.get("expires_at", 0)):
        return acc["access_token"]
    tok = _req("POST", ENDPOINTS["tt_api"] + "/oauth/token/", form={
        "client_key": key, "client_secret": secret, "grant_type": "refresh_token",
        "refresh_token": acc.get("refresh_token", "")})
    if not tok.get("access_token"):
        raise PublishError("TikTok sign-in expired. On the Publish page click Connect TikTok again.")
    acc.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", acc.get("refresh_token")),
               expires_at=time.time() + int(tok.get("expires_in", 86400)) - 120)
    _upsert("tiktok", acc)
    return acc["access_token"]


def _tt_check(resp: dict) -> dict:
    err = resp.get("error") or {}
    if err.get("code") not in (None, "", "ok"):
        code = err.get("code")
        msg = err.get("message") or code
        if code == "unaudited_client_can_only_post_to_private_accounts":
            msg = ("TikTok only lets apps it hasn't audited post to PRIVATE accounts. Set your TikTok account to "
                   "private, or get the app audited by TikTok (then tick “TikTok approved my app”).")
        raise PublishError(f"TikTok: {msg}", retry=code in ("rate_limit_exceeded", "internal_error"))
    return resp.get("data") or {}


def tiktok_upload(acc: dict, video: str, meta: dict, s, progress: Progress = lambda f: None) -> dict:
    api = ENDPOINTS["tt_api"]
    tok = _tt_token(acc, s.tt_client_key.strip(), s.tt_client_secret.strip())
    auth = {"Authorization": f"Bearer {tok}"}
    info = _tt_check(_req("POST", api + "/post/publish/creator_info/query/", js={}, headers=auth))
    opts = info.get("privacy_level_options") or ["SELF_ONLY"]
    want = "PUBLIC_TO_EVERYONE" if getattr(s, "tiktok_audited", False) else "SELF_ONLY"
    privacy = want if want in opts else ("SELF_ONLY" if "SELF_ONLY" in opts else opts[0])
    size = Path(video).stat().st_size
    if size < 5 * 1024 * 1024:
        chunk, count = size, 1
    else:
        chunk = 10 * 1024 * 1024 if size >= 10 * 1024 * 1024 else size
        count = max(1, size // chunk)
    caption = (meta.get("title", "") + " " + " ".join(meta.get("hashtags") or [])).strip()[:2200]
    init = _tt_check(_req("POST", api + "/post/publish/video/init/", headers=auth, js={
        "post_info": {"title": caption, "privacy_level": privacy, "disable_duet": False,
                      "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                        "total_chunk_count": count}}))
    pub, up = init.get("publish_id"), init.get("upload_url")
    if not up:
        raise PublishError("TikTok didn't return an upload address. Try again later.", retry=True)
    with open(video, "rb") as f:
        for i in range(count):
            a = i * chunk
            n = chunk if i < count - 1 else size - a       # the last chunk takes the remainder
            f.seek(a)
            data = f.read(n)
            _req("PUT", up, data=data, timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {a}-{a + len(data) - 1}/{size}"})
            progress(0.1 + 0.8 * (i + 1) / count)
    status = ""
    for _ in range(40):                     # wait for TikTok to process (up to ~2 minutes)
        time.sleep(3)
        st = _tt_check(_req("POST", api + "/post/publish/status/fetch/", headers=auth, js={"publish_id": pub}))
        status = st.get("status", "")
        if status == "PUBLISH_COMPLETE":
            break
        if status == "FAILED":
            raise PublishError(f"TikTok rejected the video: {st.get('fail_reason', 'unknown reason')}")
    progress(1.0)
    note = "posted" + (" (Only me: TikTok hasn't audited your app yet)" if privacy == "SELF_ONLY" else "")
    return {"id": pub, "url": "https://www.tiktok.com/", "note": note if status else "processing on TikTok"}


# ================================================================ dispatch
CONNECT = {"youtube": youtube_connect, "facebook": facebook_connect, "instagram": instagram_connect,
           "tiktok": tiktok_connect}
UPLOAD = {"youtube": youtube_upload, "facebook": facebook_upload, "instagram": instagram_upload,
          "tiktok": tiktok_upload}


def credentials_ok(platform: str, s) -> bool:
    return {"youtube": bool(s.yt_client_id.strip() and s.yt_client_secret.strip()),
            "facebook": bool(s.fb_app_id.strip() and s.fb_app_secret.strip()),
            "instagram": bool(s.fb_app_id.strip() and s.fb_app_secret.strip()),
            "tiktok": bool(s.tt_client_key.strip() and s.tt_client_secret.strip())}[platform]


def connect(platform: str, s, open_browser=webbrowser.open):
    if platform == "youtube":
        return youtube_connect(s.yt_client_id, s.yt_client_secret, open_browser)
    if platform == "facebook":
        return facebook_connect(s.fb_app_id, s.fb_app_secret, open_browser)
    if platform == "instagram":
        return instagram_connect(s.fb_app_id, s.fb_app_secret, open_browser)
    return tiktok_connect(s.tt_client_key, s.tt_client_secret, open_browser)


def lane(platform: str, acc_id: str) -> str:
    """Uploads in the same lane run one after another; different lanes may run at the same time.
    Browser accounts share a lane when they share a browser profile; API accounts get one lane each."""
    acc = next((a for a in load_accounts().get(platform, []) if a.get("id") == acc_id), None)
    if acc and acc.get("mode") == "browser":
        from . import webupload
        return "profile:" + os.path.normcase(str(webupload.profile_dir(acc)))
    return f"api:{platform}:{acc_id}"


def upload(platform: str, acc_id: str, video: str, meta: dict, s, progress: Progress = lambda f: None) -> dict:
    acc = next((a for a in load_accounts().get(platform, []) if a.get("id") == acc_id), None)
    if not acc:
        raise PublishError(f"The {PLATFORMS[platform]} account is no longer connected.")
    if not Path(video).exists():
        raise PublishError("The video file was moved or deleted.")
    if acc.get("mode") == "browser":            # direct sign-in: the app's browser uses the normal upload page
        from . import webupload
        return webupload.upload(platform, video, meta, s, progress, acc)
    return UPLOAD[platform](acc, video, meta, s, progress)
