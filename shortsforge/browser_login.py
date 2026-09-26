"""YouTube sign-in through the user's real Chrome/Edge, then silent cookie refresh.

Flow
1. `open_sign_in()` starts genuine Chrome/Edge with a private RR Shorts Builder profile (no automation flags),
   so Google treats it like any normal browser. The user signs in once.
2. `harvest()` relaunches that same profile *headless* with DevTools on a private port and reads the
   cookies with Storage.getCookies, then writes cookies.txt for yt-dlp.
3. Later, `refresh()` repeats step 2 silently whenever the saved login gets old or YouTube asks again.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import cookies
from .config import data_dir
from .utils import NO_WINDOW

PROFILE = data_dir() / "browser-profile"
SIGNIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true"
              "&continue=https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26next%3D%252F")


def find_browser() -> Optional[tuple[str, str]]:
    """(name, exe path) of Chrome, Edge or Brave."""
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        la = os.environ.get("LOCALAPPDATA", "")
        cands = [
            ("Google Chrome", rf"{pf}\Google\Chrome\Application\chrome.exe"),
            ("Google Chrome", rf"{pf86}\Google\Chrome\Application\chrome.exe"),
            ("Google Chrome", rf"{la}\Google\Chrome\Application\chrome.exe"),
            ("Microsoft Edge", rf"{pf86}\Microsoft\Edge\Application\msedge.exe"),
            ("Microsoft Edge", rf"{pf}\Microsoft\Edge\Application\msedge.exe"),
            ("Brave", rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe"),
            ("Brave", rf"{la}\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for name, p in cands:
            if Path(p).is_file():
                return name, p
        return None
    for name, exe in (("Google Chrome", "google-chrome"), ("Chromium", "chromium"), ("Chromium", "chromium-browser"),
                      ("Microsoft Edge", "microsoft-edge")):
        w = shutil.which(exe)
        if w:
            return name, w
    env = os.environ.get("SHORTSFORGE_BROWSER")
    if env and Path(env).is_file():
        return "Chromium", env
    return None


def _base_flags() -> list[str]:
    return [f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=ChromeWhatsNewUI"]


def open_sign_in() -> subprocess.Popen:
    """Open a normal browser window (dedicated profile) on the YouTube sign-in page."""
    b = find_browser()
    if not b:
        raise RuntimeError("Chrome or Edge was not found on this PC.")
    PROFILE.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([b[1], *_base_flags(), "--new-window", SIGNIN_URL])


def _wait_port(timeout: float) -> tuple[int, str]:
    f = PROFILE / "DevToolsActivePort"
    end = time.time() + timeout
    while time.time() < end:
        try:
            lines = f.read_text().split("\n")
            if len(lines) >= 2 and lines[0].strip().isdigit():
                return int(lines[0]), lines[1].strip()
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("The browser didn't start in time.")


def _cdp(ws, method: str, params: Optional[dict] = None, msg_id: int = 1) -> dict:
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    end = time.time() + 15
    while time.time() < end:
        data = json.loads(ws.recv(timeout=15))
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(data["error"].get("message", "DevTools error"))
            return data.get("result", {})
    raise RuntimeError("DevTools did not answer")


def harvest(visit_youtube: bool = True, timeout: float = 40) -> int:
    """Start the profile headless, read its cookies, save cookies.txt. Returns number of cookies saved."""
    from websockets.sync.client import connect

    b = find_browser()
    if not b:
        raise RuntimeError("Chrome or Edge was not found on this PC.")
    if not PROFILE.exists():
        raise RuntimeError("Not signed in yet.")
    (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)
    args = [b[1], *_base_flags(), "--headless=new", "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1", "--remote-allow-origins=*", "--disable-gpu", "--mute-audio",
            "https://www.youtube.com/" if visit_youtube else "about:blank"]
    if os.name != "nt" and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        port, path = _wait_port(timeout)
        if visit_youtube:
            time.sleep(4)  # let youtube.com refresh the session cookies
        with connect(f"ws://127.0.0.1:{port}{path}", max_size=32 * 1024 * 1024, open_timeout=10) as ws:
            res = _cdp(ws, "Storage.getCookies")
            raw = res.get("cookies", [])
            try:
                _cdp(ws, "Browser.close", msg_id=2)
            except Exception:
                pass
        items = [{"domain": c["domain"], "name": c["name"], "value": c["value"], "path": c.get("path", "/"),
                  "secure": c.get("secure", False), "httponly": c.get("httpOnly", False),
                  "expires": int(c["expires"]) if c.get("expires", -1) and c.get("expires", -1) > 0 else 0}
                 for c in raw]
        if not any(i["name"] in cookies.LOGIN_NAMES and "youtube.com" in i["domain"] for i in items):
            return 0
        return cookies.write_cookies(items)
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def profile_exists() -> bool:
    return PROFILE.exists() and any(PROFILE.iterdir())


def refresh(max_age_hours: float = 12, force: bool = False, log=None) -> bool:
    """Silently renew the saved login from the browser profile. Returns True if a fresh login is saved."""
    if not profile_exists() or find_browser() is None:
        return False
    try:
        age = (time.time() - cookies.COOKIE_FILE.stat().st_mtime) / 3600 if cookies.COOKIE_FILE.exists() else 1e9
    except OSError:
        age = 1e9
    if not force and cookies.has_login() and age < max_age_hours:
        return True
    try:
        n = harvest()
        if log:
            log("YouTube login refreshed from your browser profile" if n else
                "Browser profile is not signed in to YouTube any more")
        return n > 0
    except Exception as e:
        if log:
            log(f"Couldn't refresh YouTube login ({e})")
        return False


def sign_out() -> None:
    cookies.sign_out()
    shutil.rmtree(PROFILE, ignore_errors=True)


AUDIO_LIBRARY_URL = "https://studio.youtube.com/channel/UC/music"  # "UC" = your own channel


def _set_download_folder(folder: str) -> None:
    """Make the RR Shorts Builder browser profile save downloads straight into `folder` without asking."""
    prefs_path = PROFILE / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8")) if prefs_path.exists() else {}
    except Exception:
        prefs = {}
    prefs.setdefault("download", {}).update({"default_directory": folder, "prompt_for_download": False,
                                            "directory_upgrade": True})
    prefs.setdefault("savefile", {})["default_directory"] = folder
    prefs.setdefault("profile", {})["exit_type"] = "Normal"   # no "restore pages?" bubble
    prefs_path.write_text(json.dumps(prefs), encoding="utf-8")


def open_in_browser(url: str, music_folder: str) -> subprocess.Popen:
    """Open `url` in the RR Shorts Builder browser profile, with downloads saved straight into `music_folder`."""
    b = find_browser()
    if not b:
        raise RuntimeError("Chrome or Edge was not found on this PC.")
    Path(music_folder).mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    _set_download_folder(str(Path(music_folder).resolve()))
    return subprocess.Popen([b[1], *_base_flags(), "--new-window", url])


def open_audio_library(music_folder: str) -> subprocess.Popen:
    """Open YouTube's Audio Library (in YouTube Studio); every download lands in the music folder."""
    return open_in_browser(AUDIO_LIBRARY_URL, music_folder)
