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
        # login saved by "Sign in to YouTube" (kept fresh from the Rebels Revolt Shorts browser profile)
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


# ================================================================ fast mode: audio first, then only the chosen parts
def download_audio(item: SourceItem, on_progress: Optional[Callable[[float, str], None]] = None,
                   cancel: Optional[threading.Event] = None, cookies_browser: str = "") -> tuple[str, dict]:
    """Audio track only (a few MB, seconds to fetch). Returns (path, info) - info has title/duration/id."""
    import yt_dlp
    cached = [f for f in DOWNLOADS.glob(f"{item.vid}.audio.*") if not f.name.endswith((".part", ".ytdl"))]
    meta_f = DOWNLOADS / f"{item.vid}.audio.json"
    if cached and meta_f.exists():
        import json
        return str(cached[0]), json.loads(meta_f.read_text(encoding="utf-8"))

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            on_progress((d.get("downloaded_bytes") or 0) / total if total else 0.0,
                        f"Fetching audio {d.get('_speed_str') or ''}".strip())

    def run(opts):
        opts.update({"format": "ba[ext=m4a]/ba/b", "outtmpl": str(DOWNLOADS / f"{item.vid}.audio.%(ext)s"),
                     "progress_hooks": [hook], "retries": 5, "fragment_retries": 5,
                     "concurrent_fragment_downloads": 4})
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(item.source, download=True)

    info = _with_login(run, cookies_browser) or {}
    files = [f for f in DOWNLOADS.glob(f"{item.vid}.audio.*") if f.suffix != ".json"
             and not f.name.endswith((".part", ".ytdl"))]
    if not files:
        raise RuntimeError("Audio download finished but no file was found.")
    meta = {"title": info.get("title") or item.title, "duration": float(info.get("duration") or 0),
            "id": info.get("id") or item.vid}
    import json
    meta_f.write_text(json.dumps(meta), encoding="utf-8")
    if meta["title"]:
        item.title = meta["title"]
    return str(files[0]), meta


def download_section(item: SourceItem, start: float, end: float, cancel: Optional[threading.Event] = None,
                     cookies_browser: str = "", max_height: int = 1440) -> str:
    """Download only [start, end] of the video in HD. Returns the file (timing is re-aligned by the caller)."""
    import yt_dlp
    from yt_dlp.utils import download_range_func
    tag = f"{item.vid}.sec_{int(start * 10)}_{int(end * 10)}"
    done = [f for f in DOWNLOADS.glob(f"{tag}.*") if f.suffix.lower() in VIDEO_EXT]
    if done:
        return str(done[0])

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    def run(opts):
        opts.update({
            "format": "bv*+ba/b", "format_sort": [f"res:{max_height}", "vcodec:vp9", "fps", "acodec"],
            "merge_output_format": "mp4", "outtmpl": str(DOWNLOADS / f"{tag}.%(ext)s"),
            "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
            "force_keyframes_at_cuts": False, "progress_hooks": [hook], "retries": 5, "fragment_retries": 5,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(item.source, download=True)

    _with_login(run, cookies_browser)
    files = [f for f in DOWNLOADS.glob(f"{tag}.*") if f.suffix.lower() in VIDEO_EXT]
    if not files:
        raise RuntimeError("Section download produced no file.")
    return str(files[0])


def align_offset(section: str, full_wav: str, guess: float, search: float = 12.0) -> Optional[float]:
    """Absolute source time at which `section` starts, found by matching its audio against the full track.

    Section downloads start at the nearest keyframe, not exactly where asked, so captions would drift without
    this. Returns None if the match is unreliable (caller then falls back to a full download)."""
    import subprocess
    import wave

    import numpy as np
    from .utils import NO_WINDOW, find_ffmpeg
    sr, rate = 16000, 400            # envelope resolution: 2.5 ms
    try:
        raw = subprocess.run([find_ffmpeg(), "-v", "error", "-i", section, "-t", "30", "-vn", "-ac", "1", "-ar",
                              str(sr), "-f", "s16le", "-"], capture_output=True, timeout=120,
                             creationflags=NO_WINDOW).stdout
    except Exception:
        return None
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if len(a) < sr * 3:
        return None
    with wave.open(full_wav, "rb") as w:
        fsr = w.getframerate()
        lo = max(0.0, guess - search)
        w.setpos(min(w.getnframes() - 1, int(lo * fsr)))
        b = np.frombuffer(w.readframes(int((search * 2 + 30) * fsr)), dtype=np.int16).astype(np.float32)
    if fsr != sr or len(b) < sr * 3:
        return None
    hop = sr // rate

    def env(x):
        n = len(x) // hop
        e = np.abs(x[: n * hop]).reshape(n, hop).mean(1)
        e = np.log1p(e)
        return (e - e.mean()) / (e.std() + 1e-6)

    ea, eb = env(a), env(b)
    if len(eb) <= len(ea):
        return None
    n = 1 << int(np.ceil(np.log2(len(ea) + len(eb))))
    corr = np.fft.irfft(np.fft.rfft(eb, n) * np.conj(np.fft.rfft(ea, n)), n)[: len(eb) - len(ea) + 1]
    k = int(np.argmax(corr))
    score = corr[k] / len(ea)
    if score < 0.35:
        return None
    return lo + k / rate
