"""Knows what every track in your music folders sounds like, and picks the one that fits a clip's vibe.

Each track is listened to once (tempo, energy, brightness, how drum-heavy it is, where the first "drop" is)
and the result is cached, so choosing is instant. The file name and the app's own licence notes (e.g. an
original "Phonk Drift" track made by the generator) count too. Tracks in a "Trending" sub-folder of your
music folder get a boost: put the trending-style tracks you have the rights to there.

The choice is seeded per Short, so the preview and the export always agree, and different Shorts of the
same video get different tracks.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .config import data_dir

INDEX_FILE = data_dir() / "music_index.json"
MUSIC_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac", ".opus"}
VERSION = 2
_lock = threading.Lock()
_gen_lock = threading.Lock()

# what a vibe wants: tempo (bpm), energy 0..1, brightness 0..1, drums 0..1
TARGETS = {
    "hype": dict(bpm=135, energy=0.85, bright=0.55, perc=0.85),
    "motivational": dict(bpm=100, energy=0.7, bright=0.6, perc=0.6),
    "emotional": dict(bpm=75, energy=0.35, bright=0.4, perc=0.15),
    "funny": dict(bpm=115, energy=0.65, bright=0.8, perc=0.6),
    "suspense": dict(bpm=90, energy=0.45, bright=0.25, perc=0.4),
    "story": dict(bpm=88, energy=0.45, bright=0.5, perc=0.4),
    "info": dict(bpm=100, energy=0.5, bright=0.6, perc=0.5),
    "chill": dict(bpm=85, energy=0.3, bright=0.5, perc=0.3),
}
# words in a file name / the generator's mood -> vibes it suits
NAME_HINTS = [
    (("phonk", "trap", "drill", "bass boost", "808", "hype", "aggressive", "gym", "workout", "drift"), ("hype",)),
    (("motivat", "inspir", "uplift", "success", "rise", "anthem"), ("motivational",)),
    (("epic", "cinematic", "trailer", "orchestral", "heroic"), ("motivational", "suspense")),
    (("sad", "emotional", "piano", "melanchol", "tears", "heart"), ("emotional",)),
    (("dark", "suspense", "horror", "thriller", "tension", "mystery", "crime", "ambient"), ("suspense",)),
    (("happy", "fun", "funk", "comedy", "ukulele", "quirky", "bounce", "cartoon"), ("funny",)),
    (("lofi", "lo-fi", "chill", "relax", "calm", "study", "jazz"), ("chill", "story", "info")),
    (("upbeat", "pop", "energetic", "summer", "dance", "groove"), ("info", "funny", "hype")),
    (("story", "documentary", "news", "corporate"), ("story", "info", "suspense")),
]
MOOD_VIBES = {"phonk": ("hype",), "trap": ("hype",), "drill": ("hype", "suspense"), "upbeat": ("info", "hype"),
              "happy": ("funny",), "funk": ("funny", "info"), "motivational": ("motivational",),
              "epic": ("motivational", "suspense"), "cinematic": ("motivational", "suspense", "story"),
              "emotional": ("emotional",), "dark": ("suspense",), "lofi": ("chill", "story", "info"),
              "chill": ("chill", "story")}


def _load() -> dict:
    try:
        d = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        return d if d.get("v") == VERSION else {"v": VERSION, "tracks": {}}
    except Exception:
        return {"v": VERSION, "tracks": {}}


def _save(d: dict) -> None:
    try:
        INDEX_FILE.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def _decode(path: str, sr: int = 11025, seconds: float = 150) -> Optional[np.ndarray]:
    from .utils import NO_WINDOW, find_ffmpeg
    try:
        r = subprocess.run([find_ffmpeg(), "-v", "error", "-t", str(seconds), "-i", path, "-ac", "1", "-ar", str(sr),
                            "-f", "f32le", "-"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
        x = np.frombuffer(r.stdout, np.float32)
        return x if len(x) > sr * 5 else None
    except Exception:
        return None


def analyze(path: str) -> Optional[dict]:
    """Tempo, loudness, brightness, drum-heaviness and the first big energy rise ("drop") of a track."""
    sr = 11025
    x = _decode(path, sr)
    if x is None:
        return None
    hop, win = 512, 1024
    n = (len(x) - win) // hop
    if n < 50:
        return None
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(win)[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    rms = np.sqrt((frames ** 2).mean(1)) + 1e-9
    db = 20 * np.log10(rms)
    cent = (mag * freqs[None, :]).sum(1) / (mag.sum(1) + 1e-9)
    flux = np.maximum(0, np.diff(np.log1p(mag), axis=0)).sum(1)
    flux = np.concatenate([[0], flux])
    fps = sr / hop
    # tempo from the onset autocorrelation (60..180 BPM)
    o = flux - flux.mean()
    ac = np.correlate(o, o, "full")[len(o) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 60)
    lag = lo + int(np.argmax(ac[lo:hi])) if hi < len(ac) else lo
    bpm = 60 * fps / max(1, lag)
    while bpm < 70:
        bpm *= 2
    while bpm > 170:
        bpm /= 2
    perc = float(np.clip((np.percentile(flux, 90) / (np.median(flux) + 1e-6) - 1.5) / 4.0, 0, 1))
    loud = float(np.percentile(db, 75))
    # mastered tracks are all loud, so "energy" is mostly drums + tempo, a little loudness
    tempo_n = float(np.clip((bpm - 70) / 80, 0, 1))
    energy = float(np.clip(0.45 * perc + 0.35 * tempo_n + 0.2 * np.clip((loud + 36) / 24, 0, 1), 0, 1))
    bright = float(np.clip((np.median(cent) - 250) / 1800, 0, 1))
    # first strong rise in loudness (for lining the music's drop up with the end of the hook)
    sec = max(1, int(fps))
    e1 = np.convolve(db, np.ones(sec * 2) / (sec * 2), "same")
    rise = np.zeros_like(e1)
    rise[sec * 2:-sec * 2] = e1[sec * 4:] - e1[:-sec * 4]
    lim = int(min(len(rise), fps * 70))
    a0 = int(fps * 6)                        # ignore the fade-in
    drop = float((a0 + np.argmax(rise[a0:lim])) / fps) if lim > a0 + sec * 2 and rise[a0:lim].max() > 4 else 0.0
    dur = len(x) / sr
    return {"bpm": round(bpm, 1), "energy": round(energy, 3), "bright": round(bright, 3), "perc": round(perc, 3),
            "drop": round(drop, 2), "dur": round(dur, 1), "quiet": loud < -40}


def _key(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return str(p)


def tracks_in(folders: list) -> list[Path]:
    out, seen = [], set()
    for f in folders:
        if not f:
            continue
        base = Path(f)
        for sub in (base, base / "Trending"):
            try:
                for p in sorted(sub.iterdir()):
                    if p.suffix.lower() in MUSIC_EXT and p.is_file() and str(p) not in seen:
                        seen.add(str(p))
                        out.append(p)
            except OSError:
                pass
    return out


def features(paths: list[Path]) -> dict[str, dict]:
    """Cached analysis for these tracks (analyses new/changed ones now)."""
    with _lock:
        d = _load()
        tr = d["tracks"]
        changed = False
        out = {}
        for p in paths:
            k = _key(p)
            if k not in tr:
                a = analyze(str(p))
                tr[k] = a or {"bad": True}
                changed = True
            out[str(p)] = tr[k]
        if changed:
            _save(d)
        return out


def _meta_mood(p: Path) -> str:
    try:
        from .music_sources import load_meta
        m = load_meta(str(p.parent)).get(p.name) or {}
        return str(m.get("mood") or "")
    except Exception:
        return ""


def score(p: Path, f: dict, vibe: str) -> float:
    if not f or f.get("bad") or f.get("quiet"):
        return -9.0
    t = TARGETS.get(vibe, TARGETS["story"])
    bpm = f["bpm"]
    d_bpm = min(abs(bpm - t["bpm"]), abs(bpm * 2 - t["bpm"]), abs(bpm / 2 - t["bpm"])) / 40
    dist = d_bpm ** 2 + ((f["energy"] - t["energy"]) * 1.3) ** 2 + (f["bright"] - t["bright"]) ** 2 \
        + ((f["perc"] - t["perc"]) * 1.2) ** 2
    s = 1.0 - dist
    name = p.stem.lower()
    for words, vibes in NAME_HINTS:
        if any(w in name for w in words):
            s += 0.45 if vibe in vibes else -0.15
    mood = _meta_mood(p)
    if mood in MOOD_VIBES:
        s += 0.6 if vibe in MOOD_VIBES[mood] else -0.25
    if p.parent.name.lower() == "trending":
        s += 0.2
    if "test" in re.findall(r"[a-z]+", name):     # recordings like "... music test ..." rarely sound finished
        s -= 0.5
    if f.get("dur", 0) < 25:
        s -= 0.4
    return round(s, 3)


def choose(vibe: str, seed: int, index: int, folders: list, need: float, only: Optional[list] = None,
           gen_folder: str = "", log=lambda m: None) -> tuple[str, float, str]:
    """(path, start offset, why). Top matches rotate by clip so a video's Shorts don't all share one track."""
    paths = tracks_in(folders)
    if only:
        starred = [p for p in paths if p.name in set(only)]
        paths = starred or paths
    feats = features(paths) if paths else {}
    ranked = sorted(((score(p, feats.get(str(p)) or {}, vibe), p) for p in paths), key=lambda x: -x[0])
    good = [(s, p) for s, p in ranked if s > 0.25]
    if len(good) < 2 and gen_folder and not only:
        made = _generate_for(vibe, gen_folder, log)
        if made:
            feats.update(features([made]))
            good = sorted(good + [(score(made, feats.get(str(made)) or {}, vibe), made)], key=lambda x: -x[0])
    if good:
        pool = [x for x in good if x[0] >= good[0][0] - 0.5][:3]
    else:
        pool = ranked[:3]
    if not pool:
        return "", 0.0, ""
    rnd = random.Random(seed)
    pick = pool[(index + rnd.randrange(len(pool))) % len(pool)][1]
    f = feats.get(str(pick)) or {}
    return str(pick), offset_for(f, need, rnd), f"{vibe} match"


def offset_for(f: dict, need: float, rnd: random.Random, land: float = 2.2) -> float:
    """Start so the track's first drop lands just after the hook; else somewhere early in the track."""
    dur = float(f.get("dur") or 0)
    room = dur - need - 1.0
    if room <= 0:
        return 0.0
    drop = float(f.get("drop") or 0)
    if drop > land + 1:
        return round(min(room, max(0.0, drop - land)), 2)
    return round(rnd.uniform(0.0, max(0.0, min(room, dur * 0.35))), 2)


def _generate_for(vibe: str, folder: str, log) -> Optional[Path]:
    """Nothing in the library suits this vibe: compose an original one once (copyright-free)."""
    from . import musicgen, vibe as vb
    moods = [m for m in vb.VIBES.get(vibe, vb.VIBES["story"])["music"] if m in musicgen.MOODS]
    if not moods:
        return None
    mood = moods[0]
    with _gen_lock:
        name = musicgen.MOODS[mood]["name"]
        have = sorted(Path(folder).glob(f"RR Original - {name} *.m4a")) if Path(folder).exists() else []
        if have:
            return have[0]
        try:
            log(f"  No {vibe} music in your library yet: composing an original {name} track (one time)…")
            return musicgen.make_track(mood, folder, 120)
        except Exception as e:
            log(f"  (couldn't make a {name} track: {e})")
            return None


def describe(folders: list) -> list[dict]:
    """For the Music page: each track's detected feel."""
    paths = tracks_in(folders)
    feats = features(paths)
    out = []
    for p in paths:
        f = feats.get(str(p)) or {}
        if f.get("bad"):
            continue
        best = sorted(TARGETS, key=lambda v: -score(p, f, v))[:2]
        out.append({"name": p.name, "trending": p.parent.name.lower() == "trending", "bpm": f.get("bpm"),
                    "vibes": best})
    return out
