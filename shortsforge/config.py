"""Settings and application paths."""
from __future__ import annotations

import functools
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import DATA_NAME, LEGACY_DATA_NAMES
from .secure import seal, unseal


def app_root() -> Path:
    """Folder that holds the app's bundled assets (works for source and PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


@functools.lru_cache(maxsize=None)
def data_dir() -> Path:
    """Per-user writable folder for cache, models, fonts and settings."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / DATA_NAME
    if not d.exists():
        # first start after the rebrand: carry over settings, login, models and caches
        for old in LEGACY_DATA_NAMES:
            src = base / old
            if src.is_dir():
                try:
                    src.rename(d)
                except OSError:
                    return src          # locked/in use: keep using the old folder this time
                break
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output_dir() -> Path:
    return Path.home() / "RR Shorts"


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".sf_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def usable_output_dir(preferred: str) -> tuple[Path, bool]:
    """Return a folder we can really write to, and whether we had to fall back.

    Windows can refuse new folders inside Videos/Documents/Desktop (Controlled Folder Access, or a
    OneDrive-redirected folder that isn't available), which shows up as "cannot find the file specified"."""
    p = Path(preferred).expanduser() if preferred else default_output_dir()
    if _writable(p):
        return p, False
    for alt in (default_output_dir(), data_dir() / "Shorts"):
        if _writable(alt):
            return alt, True
    return p, False


ASSETS = app_root() / "assets"
BUNDLED_FONTS = ASSETS / "fonts"
MUSIC_DIR_DEFAULT = Path.home() / "Music" / "RR Shorts Music"
CACHE_DIR = data_dir() / "cache"
FONTS_DIR = data_dir() / "fonts"
MODELS_DIR = data_dir() / "models"
SETTINGS_FILE = data_dir() / "settings.json"
for _d in (CACHE_DIR, FONTS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
try:
    MUSIC_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)
except OSError:
    pass


# the bold, phone-readable caption styles; Clean Minimal, Karaoke Sweep, Typewriter and Caption Card render
# small (size 115-130) and read poorly on a phone, so "random" leaves them out unless the user adds them back
DEFAULT_CAPTION_POOL = ("hormozi", "beast", "boxed", "neon", "oneword", "comic", "marker", "slide", "pill", "hollow")
SECRET_FIELDS = ("gemini_api_key", "anthropic_api_key", "yt_client_secret", "fb_app_secret", "tt_client_secret")


@dataclass
class Settings:
    # Output
    output_dir: str = str(default_output_dir())
    width: int = 1080
    height: int = 1920
    fps: int = 0                     # 0 = match source (max 60)
    quality_crf: int = 18
    source_quality: int = 1440       # max download height (sharper crops)
    encoder: str = "auto"            # auto | libx264 | h264_nvenc | h264_qsv | h264_amf

    # Clip selection
    shorts_per_video: int = 5
    min_duration: float = 20.0
    max_duration: float = 59.0
    min_gap: float = 5.0             # seconds between chosen clips

    # Transcription
    whisper_model: str = "small"     # tiny | base | small | medium | large-v3
    language: str = "auto"           # spoken language: auto or ISO code (en, ur, hi, ...)
    caption_lang: str = "roman"      # roman (Latin letters: Roman Urdu / English) | auto | en | ur | hi
    use_gpu: bool = False            # NVIDIA CUDA for Whisper (needs CUDA 12 libs)

    # Style
    # every style option accepts "random" = rotate so each Short looks different
    caption_style: str = "random"
    hook_style: str = "random"
    cta_style: str = "random"
    layout: str = "auto"             # auto | smart_crop | blur_fit | center_crop | split
    color_grade: str = "random"
    motion: str = "random"
    intro: str = "random"
    caption_pool: list = field(default_factory=lambda: list(DEFAULT_CAPTION_POOL))  # used by "random" (empty = all)
    hook_pool: list = field(default_factory=list)
    hook_title: bool = True
    cta_text: str = "Follow for more"
    watermark: str = ""              # e.g. @yourchannel
    progress_bar: bool = True
    part_label: bool = False
    caption_position: str = "lower"  # lower | middle | upper
    placement: dict = field(default_factory=dict)  # default manual placement for new Shorts (editor)

    # Audio
    loudnorm: bool = True
    music_dir: str = str(MUSIC_DIR_DEFAULT)
    music_volume: float = 0.12       # manual level (or trim on top of Auto)
    music_auto: bool = True          # auto-level music against the voice
    music_mode: str = "random"       # random (any track) | starred (only starred tracks)
    music_selected: list = field(default_factory=list)
    sfx_level: str = "medium"        # off | subtle | medium | high  (generated, copyright-free)
    sfx_volume: float = 0.55
    remove_pauses: bool = True       # jump-cut dead air for faster pacing
    jamendo_client_id: str = ""      # free key for in-app Jamendo search
    music_safe_only: bool = True     # only show tracks that allow monetized use

    # Clip picking ("director")
    clip_picker: str = "gemini"      # gemini (free key) | claude | local (offline); no key -> offline
    cold_open: bool = True           # may open a Short on its strongest line as a teaser
    gemini_api_key: str = ""
    gemini_model: str = "auto"       # auto = best Flash model your key can use
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5"
    add_music: bool = False

    # Autopilot: channel auto-watch
    watch_enabled: bool = False
    watch_channels: list = field(default_factory=list)
    watch_interval_min: int = 60     # how often to look for new uploads
    watch_first_n: int = 1           # when a channel is added, also build its newest N videos
    watch_per_check: int = 2         # max new videos per channel per check

    # Autopilot: auto-upload (official APIs)
    auto_upload: bool = False
    upload_platforms: list = field(default_factory=lambda: ["youtube"])
    upload_gap_min: int = 45         # random gap between uploads (minutes)
    upload_gap_max: int = 180
    upload_daily_cap: int = 6        # per platform, per day
    upload_quiet_start: int = 1      # no uploads between these hours (local time); equal = off
    upload_quiet_end: int = 8
    yt_privacy: str = "public"       # public | unlisted | private
    tiktok_audited: bool = False     # TikTok approved your app -> public posts (else "only me")
    yt_client_id: str = ""
    yt_client_secret: str = ""
    fb_app_id: str = ""
    fb_app_secret: str = ""
    tt_client_key: str = ""
    tt_client_secret: str = ""

    # Tools
    ffmpeg_path: str = ""            # empty = auto detect
    cookies_browser: str = ""        # e.g. chrome, edge, firefox (for yt-dlp)

    extra: dict = field(default_factory=dict)
    settings_version: int = 4

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in names:
                        setattr(s, k, unseal(v) if k in SECRET_FIELDS else v)
                ver = int(data.get("settings_version", 1))
                if ver < 2:  # upgrade to the HD defaults
                    s.fps, s.quality_crf, s.source_quality = 0, 18, 1440
                if ver < 3:  # v1.4: Roman captions, Gemini editor, no part labels
                    s.caption_lang, s.part_label = "roman", False
                    if s.clip_picker == "local":
                        s.clip_picker = "gemini"
                if ver < 4 and not s.caption_pool:  # "all styles" included ones too small to read on a phone
                    s.caption_pool = list(DEFAULT_CAPTION_POOL)
                s.settings_version = 4
            except Exception:
                pass
        return s

    def save(self) -> None:
        data = asdict(self)
        for k in SECRET_FIELDS:        # API keys / app secrets are encrypted at rest (Windows DPAPI)
            data[k] = seal(data.get(k) or "")
        tmp = SETTINGS_FILE.with_suffix(".tmp")   # write + rename: a crash mid-save can't wipe the settings
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)

    def copy(self) -> "Settings":
        return Settings(**json.loads(json.dumps(asdict(self))))
