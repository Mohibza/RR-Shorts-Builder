"""Settings and application paths."""
from __future__ import annotations

import functools
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import DATA_NAME, LEGACY_DATA_NAMES


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


def usable_music_dir(preferred: str) -> tuple[Path, bool]:
    """A music folder we can really write to (Windows' ransomware protection often blocks Music), and whether
    we had to fall back. The fallback is kept so downloads, made tracks and imports always work."""
    p = Path(preferred).expanduser() if preferred else Path.home() / "Music" / "RR Shorts Music"
    if _writable(p):
        return p, False
    for alt in (Path.home() / "RR Shorts" / "Music", data_dir() / "Music"):
        if _writable(alt):
            return alt, True
    return p, False


def music_folder(save: bool = True) -> Path:
    """Current music folder; switches (and remembers) a writable one if the saved folder is blocked."""
    s = Settings.load()
    d, moved = usable_music_dir(s.music_dir)
    if moved and save:
        # keep the tracks that were already there usable: copy them over once
        try:
            import shutil
            old = Path(s.music_dir)
            if old.is_dir():
                for f in old.iterdir():
                    if f.is_file() and not (d / f.name).exists():
                        shutil.copy2(f, d / f.name)
        except OSError:
            pass
        s.music_dir = str(d)
        try:
            s.save()
        except OSError:
            pass
    return d


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
    encode_speed: str = "fast"       # fast (2-3x quicker, same look on phones) | quality
    fast_mode: bool = True           # YouTube links: audio first, then only the chosen parts in HD
    auto_export: bool = True         # new interface: render every found clip right after analysis

    # Clip selection
    shorts_per_video: int = 5
    min_duration: float = 20.0
    max_duration: float = 59.0
    min_gap: float = 5.0             # seconds between chosen clips

    # Transcription
    whisper_model: str = "auto"      # auto (turbo on GPU, small on CPU) | tiny | base | small | medium | large-v3-turbo
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
    caption_pool: list = field(default_factory=list)   # styles used by "random" (empty = all)
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
    sfx_level: str = "auto"          # off | auto | subtle | medium | high  (generated, copyright-free)
    sfx_volume: float = 0.55
    remove_pauses: bool = True       # jump-cut dead air for faster pacing
    jamendo_client_id: str = ""      # free key for in-app Jamendo search
    music_safe_only: bool = True     # only show tracks that allow monetized use

    # Clip picking ("director")
    clip_picker: str = "gemini"      # gemini (free key) | claude | openai (ChatGPT) | local (offline); no key -> offline
    cold_open: bool = True           # may open a Short on its strongest line as a teaser
    gemini_api_key: str = ""
    gemini_model: str = "auto"       # auto = best Flash model your key can use
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "auto"       # auto = best mini/flagship GPT model the key can use
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
    tiktok_audited: bool = False
    web_upload_visible: bool = False # show the browser window during direct-sign-in uploads     # TikTok approved your app -> public posts (else "only me")
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
                        setattr(s, k, v)
                ver = int(data.get("settings_version", 1))
                if ver < 2:  # upgrade to the HD defaults
                    s.fps, s.quality_crf, s.source_quality = 0, 18, 1440
                if ver < 3:  # v1.4: Roman captions, Gemini editor, no part labels
                    s.caption_lang, s.part_label = "roman", False
                    if s.clip_picker == "local":
                        s.clip_picker = "gemini"
                if ver < 4:  # v2.1: sound effects adapt to each clip
                    if s.sfx_level == "medium":
                        s.sfx_level = "auto"
                    if s.whisper_model == "small":
                        s.whisper_model = "auto"
                s.settings_version = 4
            except Exception:
                pass
        return s

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def copy(self) -> "Settings":
        return Settings(**json.loads(json.dumps(asdict(self))))
