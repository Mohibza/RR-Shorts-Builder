"""Free background music: search + one-click download from open music catalogues.

Direct in-app search and download:
  * Openverse (WordPress.org's open media search, no key needed) indexes Creative-Commons music from
    Jamendo, ccMixter, Freesound, Wikimedia Commons and others.
  * Jamendo (600k+ tracks) through its official API with a free client ID.

Browser sources (their sites have no public music API, so they open in the app's own browser and every
download is saved straight into the music folder): Pixabay Music, YouTube Audio Library, Mixkit,
Free Music Archive.

Every download is recorded in `.rr_music.json` inside the music folder (title, artist, licence, credit line).
When a Short uses a track that needs attribution, the credit is added to its description automatically.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import __version__

UA = f"RRShortsBuilder/{__version__} (desktop app; +https://openverse.org)"
META_FILE = ".rr_music.json"

BROWSER_SOURCES = {
    # key: (label, url, licence note, needs credit)
    "pixabay": ("Pixabay Music", "https://pixabay.com/music/",
                "Pixabay Content License: free for monetized videos, no credit needed", False),
    "youtube": ("YouTube Audio Library", "https://studio.youtube.com/channel/UC/music",
                "YouTube Audio Library: cleared for YouTube, some tracks ask for a credit line", False),
    "mixkit": ("Mixkit", "https://mixkit.co/free-stock-music/",
               "Mixkit License: free, no credit needed", False),
    "fma": ("Free Music Archive", "https://freemusicarchive.org/search?adv=1&music-filter-CC-attribution-only=1",
            "Free Music Archive: licence varies per track (see the track page)", True),
}


@dataclass
class Track:
    id: str
    title: str
    artist: str
    duration: float            # seconds (0 = unknown)
    preview_url: str
    download_url: str
    license: str               # e.g. "CC BY 4.0"
    license_url: str
    source: str                # e.g. "Jamendo via Openverse"
    page_url: str = ""
    ext: str = "mp3"
    attribution: str = ""
    tags: list = field(default_factory=list)

    @property
    def needs_credit(self) -> bool:
        lic = self.license.upper()
        return not (lic.startswith("CC0") or "PUBLIC DOMAIN" in lic or lic.startswith("PDM"))

    @property
    def monetization_ok(self) -> bool:
        """Commercial use and adaptation (syncing to video) allowed."""
        lic = self.license.upper()
        return "NC" not in lic.split() and "-NC" not in lic and "ND" not in lic.replace("-", " ").split()

    def credit(self) -> str:
        if not self.needs_credit:
            return ""
        return self.attribution or (f"“{self.title}” by {self.artist} ({self.source}), licensed under "
                                    f"{self.license} {self.license_url}").strip()


class SourceError(RuntimeError):
    pass


def _get_json(url: str, timeout: float = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code == 429:
            raise SourceError("The music service is busy (too many searches). Wait a minute and try again.")
        if e.code in (401, 403):
            raise SourceError(f"Access refused by the music service ({e.code}). {body}".strip())
        raise SourceError(f"Music search failed ({e.code}). {body}".strip())
    except urllib.error.URLError as e:
        raise SourceError(f"Couldn't reach the music service: {e.reason}. Check your internet connection.")
    except (TimeoutError, OSError) as e:
        raise SourceError(f"Couldn't reach the music service: {e}")
    except json.JSONDecodeError:
        raise SourceError("The music service sent an unexpected reply. Try again later.")


# ---------------------------------------------------------------- licences
def _cc_from_url(url: str) -> str:
    """'https://creativecommons.org/licenses/by-nc-sa/3.0/' -> 'CC BY-NC-SA 3.0'."""
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/?([\d.]+)?", url or "", re.I)
    if not m:
        return ""
    kind, ver = m.group(2).lower(), m.group(3) or ""
    if kind in ("zero", "cc0"):
        return "CC0 1.0"
    if kind == "mark":
        return "Public Domain"
    return f"CC {kind.upper()} {ver}".strip()


def _ov_license(code: str, ver: str) -> str:
    code = (code or "").lower()
    if code == "cc0":
        return "CC0 1.0"
    if code == "pdm":
        return "Public Domain"
    return f"CC {code.upper()} {ver or ''}".strip()


# ---------------------------------------------------------------- Openverse
OPENVERSE = "https://api.openverse.org/v1/audio/"
PROVIDERS = {"jamendo": "Jamendo", "ccmixter": "ccMixter", "freesound": "Freesound", "wikimedia_audio": "Wikimedia",
             "wikimedia": "Wikimedia", "europeana": "Europeana"}


def search_openverse(query: str, page: int = 1, safe_only: bool = True, page_size: int = 20) -> tuple[list[Track], bool]:
    """Creative-Commons music from Jamendo, ccMixter, Freesound, Wikimedia... No key needed."""
    params = {"q": query or "background music", "page": max(1, page), "page_size": page_size,
              "category": "music", "mature": "false"}
    if safe_only:   # commercial use + may be put in a video
        params["license"] = "cc0,pdm,by,by-sa"
    data = _get_json(OPENVERSE + "?" + urllib.parse.urlencode(params))
    out = []
    for r in data.get("results", []) or []:
        url = r.get("url") or ""
        if not url.startswith("http"):
            continue
        lic = _ov_license(r.get("license", ""), r.get("license_version", ""))
        raw = (r.get("source") or r.get("provider") or "").lower()
        prov = PROVIDERS.get(raw, raw.replace("_", " ").title())
        ext = (r.get("filetype") or Path(urllib.parse.urlparse(url).path).suffix.lstrip(".") or "mp3").lower()
        if ext not in ("mp3", "ogg", "wav", "flac", "m4a", "aac"):
            ext = "mp3"
        dur = r.get("duration") or 0
        out.append(Track(
            id=f"ov:{r.get('id')}", title=(r.get("title") or "Untitled").strip(),
            artist=(r.get("creator") or "Unknown").strip(), duration=float(dur) / 1000 if dur else 0.0,
            preview_url=url, download_url=url, license=lic, license_url=r.get("license_url") or "",
            source=f"{prov} via Openverse" if prov else "Openverse", page_url=r.get("foreign_landing_url") or "",
            ext=ext, attribution=(r.get("attribution") or "").strip(),
            tags=[t.get("name", "") for t in (r.get("tags") or [])[:6] if isinstance(t, dict)]))
    more = page < int(data.get("page_count") or 1)
    return out, more


# ---------------------------------------------------------------- Jamendo
JAMENDO = "https://api.jamendo.com/v3.0/tracks/"
JAMENDO_KEY_URL = "https://devportal.jamendo.com/"


def search_jamendo(query: str, client_id: str, page: int = 1, safe_only: bool = True,
                   instrumental: bool = True, page_size: int = 20) -> tuple[list[Track], bool]:
    if not client_id.strip():
        raise SourceError("Jamendo needs a free client ID. Click “Get a free key”, sign up, create an app, "
                          "and paste the Client ID here.")
    params = {"client_id": client_id.strip(), "format": "json", "limit": page_size,
              "offset": (max(1, page) - 1) * page_size, "audioformat": "mp32", "audiodlformat": "mp32",
              "include": "licenses musicinfo", "order": "relevance" if query else "popularity_month",
              "groupby": "artist_id"}
    if query:
        params["search"] = query
    if instrumental:
        params["vocalinstrumental"] = "instrumental"
    data = _get_json(JAMENDO + "?" + urllib.parse.urlencode(params))
    head = data.get("headers") or {}
    if head.get("status") and head.get("status") != "success":
        raise SourceError(f"Jamendo: {head.get('error_message') or head.get('status')}")
    out = []
    for r in data.get("results", []) or []:
        lic = _cc_from_url(r.get("license_ccurl", "")) or "CC (see page)"
        dl = r.get("audiodownload") if r.get("audiodownload_allowed", True) else ""
        t = Track(id=f"jm:{r.get('id')}", title=(r.get("name") or "Untitled").strip(),
                  artist=(r.get("artist_name") or "Unknown").strip(), duration=float(r.get("duration") or 0),
                  preview_url=r.get("audio") or "", download_url=dl or r.get("audio") or "", license=lic,
                  license_url=r.get("license_ccurl") or "", source="Jamendo",
                  page_url=r.get("shareurl") or "", ext="mp3")
        if not t.download_url:
            continue
        if safe_only and not t.monetization_ok:
            continue
        out.append(t)
    more = int(head.get("results_count") or 0) >= page_size
    return out, more


def search(source: str, query: str, page: int = 1, safe_only: bool = True, jamendo_id: str = "") -> tuple[list[Track], bool]:
    if source == "jamendo":
        return search_jamendo(query, jamendo_id, page, safe_only)
    return search_openverse(query, page, safe_only)


# ---------------------------------------------------------------- downloads + library metadata
def _safe(s: str, n: int = 70) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().strip(".")
    return re.sub(r"\s+", " ", s)[:n] or "track"


def load_meta(folder: str) -> dict:
    try:
        return json.loads((Path(folder) / META_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_meta(folder: str, meta: dict) -> None:
    try:
        p = Path(folder) / META_FILE
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        try:  # hide it on Windows
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x02)
        except Exception:
            pass
    except OSError:
        pass


def record(folder: str, filename: str, info: dict) -> None:
    meta = load_meta(folder)
    meta[filename] = {**meta.get(filename, {}), **info, "added": time.time()}
    save_meta(folder, meta)


def download(track: Track, folder: str, progress: Callable[[float], None] = lambda f: None,
             cancel: Optional[Callable[[], bool]] = None) -> Path:
    """Download into the music folder and record licence + credit. Returns the saved file."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    name = _safe(f"{track.title} - {track.artist}") + "." + track.ext
    dest = Path(folder) / name
    if dest.exists() and dest.stat().st_size > 50_000:
        record(folder, dest.name, _info(track))
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(track.download_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            ctype = r.headers.get("Content-Type", "")
            if "text/html" in ctype:
                raise SourceError("That track can't be downloaded directly. Open its page instead.")
            got = 0
            while True:
                if cancel and cancel():
                    raise SourceError("Cancelled")
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    progress(min(1.0, got / total))
        if tmp.stat().st_size < 20_000:
            raise SourceError("The download was empty or blocked. Try another track.")
        tmp.replace(dest)
    except urllib.error.HTTPError as e:
        raise SourceError(f"Download failed ({e.code}). The file may have been removed. Try another track.")
    except urllib.error.URLError as e:
        raise SourceError(f"Download failed: {e.reason}")
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    record(folder, dest.name, _info(track))
    return dest


def _info(t: Track) -> dict:
    return {"title": t.title, "artist": t.artist, "license": t.license, "license_url": t.license_url,
            "source": t.source, "page_url": t.page_url, "credit": t.credit()}


def credit_for(path: str) -> str:
    """Credit line to put in a Short's description ('' if the track needs none or is unknown)."""
    if not path:
        return ""
    p = Path(path)
    return (load_meta(str(p.parent)).get(p.name) or {}).get("credit", "")


def track_info(folder: str, filename: str) -> dict:
    return load_meta(folder).get(filename) or {}


def tag_new_downloads(folder: str, names: list[str], source_key: str) -> None:
    """Label files that arrived from a browser source (so the library shows where they came from)."""
    label, _url, note, needs = BROWSER_SOURCES.get(source_key, ("", "", "", False))
    if not label:
        return
    meta = load_meta(folder)
    changed = False
    for n in names:
        if n not in meta:
            lic = {"pixabay": "Pixabay License", "youtube": "YouTube Audio Library",
                   "mixkit": "Mixkit License", "fma": "See track page"}.get(source_key, "")
            meta[n] = {"title": Path(n).stem, "source": label, "license": lic, "credit": "",
                       "note": note, "added": time.time()}
            changed = True
    if changed:
        save_meta(folder, meta)


def to_dict(t: Track) -> dict:
    return asdict(t)
