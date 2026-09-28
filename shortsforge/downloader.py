"""Video sources: YouTube / any yt-dlp URL, playlists, channels, and local files."""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import CACHE_DIR
from .utils import Cancelled, find_ffmpeg

DOWNLOADS = CACHE_DIR / "downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv"}


@dataclass
class SourceItem:
    source: str          # URL or local path
    title: str
    vid: str             # stable id used for caching
    is_local: bool = False
    duration: float = 0.0


def _local_id(path: str) -> str:
    st = os.stat(path)
    return "local_" + hashlib.md5(f"{Path(path).resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def _ydl_base_opts(cookies_browser: str = "") -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True}
    try:
        opts["ffmpeg_location"] = find_ffmpeg()
    except Exception:
        pass
    from .cookies import COOKIE_FILE, has_login
    if has_login():
        # login saved by "Sign in to YouTube" (kept fresh from the RR Shorts Builder browser profile)
        opts["cookiefile"] = str(COOKIE_FILE)
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def _with_login(run, cookies_browser: str = "", log=None):
    """Run a yt-dlp call; if YouTube asks for sign-in, silently renew the login once and retry."""
    from . import browser_login
    from .cookies import needs_login
    browser_login.refresh(max_age_hours=12, log=log)
    try:
        return run(_ydl_base_opts(cookies_browser))
    except Cancelled:
        raise
    except Exception as e:
        if needs_login(str(e)) and browser_login.refresh(force=True, log=log):
            return run(_ydl_base_opts(cookies_browser))
        raise


def expand(source: str, cookies_browser: str = "") -> list[SourceItem]:
    """Turn user input (video URL, playlist, channel, file or folder) into items."""
    source = source.strip().strip('"')
    if not source:
        return []
    p = Path(source).expanduser()
    if not is_url(source) and p.exists():
        p = p.resolve()
        if p.is_dir():
            files = sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT)
            return [SourceItem(str(f), f.stem, _local_id(str(f)), True) for f in files]
        return [SourceItem(str(p), p.stem, _local_id(str(p)), True)]
    if not is_url(source):
        raise ValueError("Enter a YouTube link, a playlist/channel link, or pick a video file.")

    import yt_dlp  # lazy

    url = source
    # Channel root -> its uploads tab
    if re.search(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?$", url):
        url = url.rstrip("/") + "/videos"

    def run(opts):
        opts.update({"extract_flat": "in_playlist", "skip_download": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = _with_login(run, cookies_browser)

    items: list[SourceItem] = []

    def add(e: dict):
        if not e:
            return
        if e.get("_type") in ("playlist", "multi_video") and e.get("entries"):
            for sub in e["entries"]:
                add(sub)
            return
        vurl = e.get("webpage_url") or e.get("url") or ""
        if vurl and not is_url(vurl) and e.get("ie_key") == "Youtube":
            vurl = f"https://www.youtube.com/watch?v={vurl}"
        if not vurl:
            return
        items.append(SourceItem(vurl, e.get("title") or "video", str(e.get("id") or hashlib.md5(vurl.encode()).hexdigest()[:11]),
                                False, float(e.get("duration") or 0)))

    add(info)
    return items


def cached_file(item: SourceItem) -> Optional[str]:
    """The finished download of this item from an earlier run, if there is one."""
    hits = [f for f in DOWNLOADS.glob(f"{item.vid}.*") if f.suffix.lower() in VIDEO_EXT and f.stem == item.vid]
    return str(hits[0]) if hits else None


def download(
    item: SourceItem,
    on_progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[threading.Event] = None,
    cookies_browser: str = "",
    max_height: int = 1440,
) -> str:
    """Download (or pass through) a source. Returns local file path."""
    if item.is_local:
        return item.source
    existing = cached_file(item)
    if existing:
        if on_progress:
            on_progress(1.0, "Using cached download")
        return existing

    import yt_dlp

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            speed = d.get("_speed_str") or ""
            on_progress(done / total if total else 0.0, f"Downloading {speed}".strip())

    def run(opts):
        opts.update({
            # best quality up to max_height; prefer VP9/H.264 over AV1 (faster to edit, readable everywhere)
            "format": "bv*+ba/b",
            "format_sort": [f"res:{max_height}", "vcodec:vp9", "fps", "acodec"],
            "merge_output_format": "mp4",
            "outtmpl": str(DOWNLOADS / f"{item.vid}.%(ext)s"),
            "progress_hooks": [hook],
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": 4,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(item.source, download=True)

    info = _with_login(run, cookies_browser)
    if info and info.get("title"):
        item.title = info["title"]
    files = [f for f in DOWNLOADS.glob(f"{item.vid}.*") if f.suffix.lower() in VIDEO_EXT and f.stem == item.vid]
    if not files:
        raise RuntimeError("Download finished but no video file was found.")
    return str(files[0])
