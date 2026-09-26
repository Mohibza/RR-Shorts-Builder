"""Channel auto-watch: find new uploads on the channels listed in Settings and hand them to the build queue.

* When a channel is first added, its existing videos are remembered as "already seen", except the newest
  `first_n` (so turning it on doesn't try to process a whole back-catalogue).
* Every check looks at the newest ~15 uploads (live streams, premieres and Shorts are skipped) and returns the
  ones not seen before. They're marked seen as soon as they are queued, so nothing is built twice.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional

from .config import data_dir
from .downloader import SourceItem, _with_login, is_url

STATE = data_dir() / "watch_state.json"
Log = Optional[Callable[[str], None]]


def normalize(url: str) -> str:
    url = url.strip().strip('"').rstrip("/")
    if not url:
        return ""
    if url.startswith("@"):
        url = "https://www.youtube.com/" + url
    if not is_url(url):
        url = "https://" + url
    url = re.sub(r"/(videos|shorts|streams|featured|playlists)$", "", url)
    url = re.sub(r"^https?://(m\.|www\.)?youtube\.com", "https://www.youtube.com", url, flags=re.I)
    return url


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": {}, "last_check": 0}


def _save(st: dict) -> None:
    try:
        STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    except OSError:
        pass


def latest(channel: str, n: int = 15, cookies_browser: str = "") -> list[SourceItem]:
    """Newest regular uploads of a channel (newest first)."""
    import yt_dlp
    url = normalize(channel)
    if re.search(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)$", url):
        url += "/videos"

    def run(opts):
        opts.update({"extract_flat": "in_playlist", "skip_download": True, "playlistend": n})
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = _with_login(run, cookies_browser)
    out = []
    for e in (info or {}).get("entries") or []:
        if not e or e.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            continue
        vid = str(e.get("id") or "")
        if not vid:
            continue
        dur = float(e.get("duration") or 0)
        if dur and dur < 75:          # a Short, not a long video
            continue
        vurl = e.get("url") or f"https://www.youtube.com/watch?v={vid}"
        if not is_url(vurl):
            vurl = f"https://www.youtube.com/watch?v={vid}"
        out.append(SourceItem(vurl, e.get("title") or "video", vid, False, dur))
    return out[:n]


def check(channels: list[str], first_n: int = 1, per_check: int = 2, cookies_browser: str = "",
          log: Log = None) -> list[SourceItem]:
    """New videos to build, across all channels (at most `per_check` per channel each time)."""
    st = _load()
    seen: dict = st.setdefault("seen", {})
    found: list[SourceItem] = []
    for raw in channels:
        ch = normalize(raw)
        if not ch:
            continue
        try:
            vids = latest(ch, 15, cookies_browser)
        except Exception as e:
            if log:
                log(f"Auto-watch: couldn't check {ch}: {str(e).splitlines()[0][:160]}")
            continue
        known = set(seen.get(ch, []))
        if ch not in seen:            # first time: remember the back-catalogue, build only the newest few
            new = vids[:max(0, first_n)]
            known |= {v.vid for v in vids[max(0, first_n):]}
            if log:
                log(f"Auto-watch: now watching {ch} ({len(vids)} recent videos found"
                    + (f", building the newest {len(new)}" if new else "") + ")")
        else:
            new = [v for v in vids if v.vid not in known]
        new = new[:max(1, per_check)]
        new.reverse()                 # oldest first, so uploads go out in the channel's order
        for v in new:
            known.add(v.vid)
            found.append(v)
            if log:
                log(f"Auto-watch: new video on {ch}: “{v.title}”")
        seen[ch] = sorted(known)[-500:] if len(known) > 500 else sorted(known)
    st["last_check"] = time.time()
    _save(st)
    return found


def last_check() -> float:
    return float(_load().get("last_check") or 0)


def forget(channel: str) -> None:
    st = _load()
    st.get("seen", {}).pop(normalize(channel), None)
    _save(st)
