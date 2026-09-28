"""Sound effects, synthesized from scratch (no samples, so nothing can be copyright-claimed).

Each effect is generated with numpy the first time it's needed and cached as a WAV.
`plan()` decides *when* to play what (hooks, zooms, captions, end card) and `mix_track()` renders
one stereo track for the whole Short that the renderer mixes under the voice.
"""
from __future__ import annotations

import re
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from .config import data_dir

SR = 48000
SFX_DIR = data_dir() / "sfx"
SFX_DIR.mkdir(parents=True, exist_ok=True)
VERSION = 1


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n)


def _onepole_lp(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """Time-varying one-pole low-pass (cutoff in Hz, scalar or per-sample)."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    a = 1 - np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # short sounds only -> plain loop is fine
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def _bandpass(x, lo, hi):
    return _onepole_lp(x, hi) - _onepole_lp(x, lo)


def _env(n: int, attack: float, release_curve: float = 4.0) -> np.ndarray:
    a = max(1, int(n * attack))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a) ** 1.5
    e[a:] = np.exp(-release_curve * np.linspace(0, 1, n - a))
    return e


def _stereo(mono: np.ndarray, pan: np.ndarray | float = 0.0) -> np.ndarray:
    p = np.broadcast_to(np.asarray(pan, dtype=float), mono.shape)
    l = mono * np.sqrt(0.5 * (1 - p))
    r = mono * np.sqrt(0.5 * (1 + p))
    return np.stack([l, r], axis=1)


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = np.max(np.abs(x)) or 1.0
    return x / m * peak


# ---------------------------------------------------------------------------- generators
def whoosh(seed=1, dur=0.55, up=False):
    t = _t(dur)
    n = len(t)
    x = _noise(n, seed)
    f = (np.linspace(300, 4500, n) if up else 400 + 3800 * np.sin(np.pi * t / dur) ** 2)
    y = _bandpass(x, f * 0.35, f)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** (1.2 if not up else 0.7)
    if up:
        env = env * np.linspace(0.3, 1, n)
    return _stereo(_norm(y * env, 0.8), np.linspace(-0.7, 0.7, n))


def swipe(seed=2):
    t = _t(0.22)
    n = len(t)
    y = _bandpass(_noise(n, seed), np.linspace(1500, 5000, n), np.linspace(4000, 11000, n))
    return _stereo(_norm(y * _env(n, 0.35, 5), 0.7), np.linspace(0.6, -0.6, n))


def pop(seed=3, pitch=1.0):
    t = _t(0.12)
    f = (900 * pitch) * np.exp(-t * 28) + 220 * pitch
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = np.sin(ph) * np.exp(-t * 38)
    click = _noise(len(t), seed) * np.exp(-t * 900) * 0.25
    return _stereo(_norm(y + click, 0.85))


def key(seed=4):
    """Mechanical keyboard click: bright tick + short body thump."""
    rng = np.random.default_rng(seed)
    t = _t(0.06)
    n = len(t)
    tick = _bandpass(_noise(n, seed), 2500, 7000 + rng.uniform(-800, 800)) * np.exp(-t * 400)
    body = np.sin(2 * np.pi * rng.uniform(140, 190) * t) * np.exp(-t * 90) * 0.5
    rel = np.zeros(n)
    k = int(SR * rng.uniform(0.028, 0.04))
    rel[k:] = _bandpass(_noise(n - k, seed + 7), 3000, 8000) * np.exp(-np.arange(n - k) / SR * 600) * 0.4
    return _stereo(_norm(tick + body + rel, 0.7), rng.uniform(-0.3, 0.3))


def ding(seed=5):
    t = _t(1.2)
    y = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)
            for f, a, d in ((1318.5, 1.0, 3.2), (2637, 0.45, 5), (3955, 0.2, 7), (1975.5, 0.25, 4)))
    y *= np.minimum(1, t / 0.004)
    return _stereo(_norm(y, 0.6))


def boom(seed=6):
    t = _t(0.9)
    f = 110 * np.exp(-t * 4) + 38
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
    y += _onepole_lp(_noise(len(t), seed), 400) * np.exp(-t * 18) * 2.0
    y = np.tanh(y * 2.2)
    return _stereo(_norm(y, 0.9))


def shutter(seed=7):
    t = _t(0.16)
    n = len(t)
    y = np.zeros(n)
    for start, amp in ((0.0, 1.0), (0.07, 0.8)):
        k = int(start * SR)
        m = n - k
        y[k:] += _bandpass(_noise(m, seed + k), 1200, 9000) * np.exp(-np.arange(m) / SR * 220) * amp
    return _stereo(_norm(y, 0.7))


def glitch(seed=8):
    rng = np.random.default_rng(seed)
    t = _t(0.3)
    n = len(t)
    y = np.zeros(n)
    pos = 0
    while pos < n:
        seg = int(SR * rng.uniform(0.012, 0.045))
        f = rng.choice([180, 360, 720, 1440, 2880])
        tt = np.arange(seg) / SR
        y[pos:pos + seg] = np.sign(np.sin(2 * np.pi * f * tt))[: n - pos] * rng.uniform(0.3, 1)
        pos += seg + int(SR * rng.uniform(0, 0.02))
    y = np.round(y * 6) / 6  # bit-crush
    return _stereo(_norm(y * np.linspace(1, 0.4, n), 0.45), rng.uniform(-0.4, 0.4))


def riser(seed=9):
    t = _t(0.6)
    f = 200 * np.exp(t * 3.2)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.4 + _bandpass(_noise(len(t), seed), f, f * 3) * 0.8
    return _stereo(_norm(y * (t / t[-1]) ** 2 * np.minimum(1, (t[-1] - t) / 0.03), 0.6))


def sparkle(seed=10):
    """Bright rising three-note shimmer (for 'wow / amazing' moments)."""
    t = _t(0.7)
    y = np.zeros(len(t))
    for i, f in enumerate((1568.0, 2093.0, 2637.0)):
        k = int(SR * i * 0.06)
        tt = t[: len(t) - k]
        y[k:] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * 7) * (0.8 - i * 0.15)
        y[k:] += np.sin(2 * np.pi * f * 2.01 * tt) * np.exp(-tt * 11) * 0.2
    y += _bandpass(_noise(len(t), seed), 6000, 12000) * np.exp(-t * 9) * 0.15
    return _stereo(_norm(y, 0.55), np.linspace(-0.4, 0.4, len(t)))


def thud(seed=11):
    """Short deep hit, softer than the boom."""
    t = _t(0.35)
    f = 90 * np.exp(-t * 9) + 45
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 11)
    y += _onepole_lp(_noise(len(t), seed), 300) * np.exp(-t * 40)
    return _stereo(_norm(np.tanh(y * 1.8), 0.85))


def zap(seed=12):
    """Quick laser zap (for 'fast / instant / speed')."""
    t = _t(0.22)
    f = 2400 * np.exp(-t * 14) + 180
    y = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-t * 12)
    y = _onepole_lp(y, 5000)
    return _stereo(_norm(y, 0.4), np.linspace(0.5, -0.5, len(t)))


def click(seed=13):
    """Crisp UI click."""
    t = _t(0.05)
    y = _bandpass(_noise(len(t), seed), 2000, 9000) * np.exp(-t * 500)
    y += np.sin(2 * np.pi * 1200 * t) * np.exp(-t * 300) * 0.5
    return _stereo(_norm(y, 0.6))


def bass_drop(seed=14):
    """Long sub-bass sweep down (big reveal)."""
    t = _t(1.3)
    f = 160 * np.exp(-t * 2.4) + 32
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, t / 0.02) * np.exp(-t * 1.8)
    return _stereo(_norm(np.tanh(y * 2.5), 0.9))


def swoosh_down(seed=15):
    t = _t(0.45)
    n = len(t)
    f = np.linspace(5000, 400, n)
    y = _bandpass(_noise(n, seed), f * 0.35, f)
    env = np.sin(np.pi * np.clip(t / t[-1], 0, 1)) ** 0.8
    return _stereo(_norm(y * env, 0.75), np.linspace(0.7, -0.7, n))


GENERATORS = {
    "sparkle": sparkle, "thud": thud, "zap": zap, "click": click, "bass_drop": bass_drop, "swoosh_down": swoosh_down,
    "whoosh": lambda: whoosh(1), "whoosh2": lambda: whoosh(11, 0.45), "rise": lambda: whoosh(21, 0.4, up=True),
    "swipe": swipe, "pop": pop, "pop_hi": lambda: pop(13, 1.4), "pop_lo": lambda: pop(23, 0.7),
    "key1": lambda: key(4), "key2": lambda: key(14), "key3": lambda: key(24), "key4": lambda: key(34),
    "ding": ding, "boom": boom, "shutter": shutter, "glitch": glitch, "riser": riser,
}


def _write_wav(path: Path, x: np.ndarray) -> None:
    x16 = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x16.tobytes())


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767
    return a.reshape(-1, 2)


_cache: dict[str, np.ndarray] = {}


def sound(name: str) -> np.ndarray:
    if name in _cache:
        return _cache[name]
    p = SFX_DIR / f"{name}_v{VERSION}.wav"
    if not p.exists():
        _write_wav(p, GENERATORS[name]())
    _cache[name] = _read_wav(p)
    return _cache[name]


# ---------------------------------------------------------------------------- timing
LEVEL_GAIN = {"subtle": 0.6, "medium": 1.0, "high": 1.15}


# words that get a matching sound (English + Roman Urdu/Hindi)
MATCH_WORDS = {
    "ding": {"money", "cash", "dollar", "dollars", "paisa", "paise", "paisay", "rupay", "rupees", "rupee", "crore",
             "lakh", "million", "billion", "profit", "income", "salary", "rich", "sale", "sales", "earn", "kamai"},
    "boom": {"never", "nahi", "nahin", "kabhi", "stop", "mistake", "ghalti", "galti", "fail", "failed", "danger",
             "warning", "shocking", "crazy", "boom", "lost", "khatam", "khtm", "worst"},
    "zap": {"fast", "quick", "quickly", "instant", "instantly", "speed", "jaldi", "foran", "turant", "seconds",
            "minute", "minutes", "now", "abhi"},
    "sparkle": {"magic", "beautiful", "perfect", "shine", "dream", "khubsurat", "khoobsurat", "wonderful", "love",
                "pyar", "happy", "khush"},
    "pop_hi": {"wow", "amazing", "zabardast", "kamal", "kamaal", "best", "secret", "raaz", "trick", "hack", "free",
               "easy", "simple", "asaan", "win", "success", "kamyab", "kamiab"},
}
# the longest stretch without any sound before a light "pattern interrupt" is added (keeps attention)
RETENTION_GAP = {"subtle": 8.0, "medium": 4.5, "high": 3.0}
FILLERS = [("swipe", 0.22), ("pop_hi", 0.2), ("whoosh2", 0.24), ("click", 0.24), ("key1", 0.22), ("sparkle", 0.16),
           ("swoosh_down", 0.2), ("glitch", 0.14), ("pop", 0.2), ("zap", 0.12), ("thud", 0.22)]
SFX_LEVELS = ("off", "auto", "subtle", "medium", "high")


def resolve_level(level: str, words: list[dict], dur: float) -> str:
    """'auto' picks the sound-design intensity from the clip itself: fast talkers get more, calm clips less."""
    if level != "auto":
        return level
    wps = len(words) / max(1.0, dur)
    loud = sum(1 for w in words if w["w"].rstrip().endswith(("!", "?"))) / max(1, len(words))
    if wps >= 3.0 or loud > 0.12:
        return "high"
    if wps < 1.8:
        return "subtle"
    return "medium"


def plan(level: str, dur: float, words: list[dict], chunk_starts: list[float], caption_style: dict,
         hook_anim: Optional[str], intro: str, motion: str, punch_times: list[float], has_cta: bool,
         emphasis: set, seams: Optional[list] = None, hook_end: float = 0.0) -> list[tuple[float, str, float]]:
    """Return [(time, sound, gain)] for one Short.

    seams = [(t, removed_seconds, is_part_join)] where the Short jumps (pauses cut / parts joined): each gets a
    transition sound. After everything else, gaps longer than RETENTION_GAP get a light pattern interrupt on the
    next word so the sound design keeps moving all the way through."""
    level = resolve_level(level, words, dur)
    if level == "off":
        return []
    ev = _plan_core(level, dur, words, chunk_starts, caption_style, hook_anim, intro, motion, punch_times,
                    has_cta, emphasis)
    ev += _plan_retention(level, dur, words, chunk_starts, seams or [], ev, hook_end)
    return _dedupe(ev)


def _plan_retention(level: str, dur: float, words: list[dict], chunk_starts: list[float], seams: list,
                    existing: list, hook_end: float) -> list:
    out = []
    # 1) cuts: whoosh on joined parts, soft swipe on bigger jump cuts
    last = -9.0
    for t, removed, join in seams:
        if not (0.4 < t < dur - 0.4):
            continue
        if join:
            out.append((max(0.0, t - 0.15), "whoosh", 0.5))
            last = t
        elif removed >= (0.35 if level == "high" else 0.6) and t - last > (1.8 if level == "high" else 3.0):
            out.append((max(0.0, t - 0.08), "swipe" if len(out) % 2 else "whoosh2", 0.2 if level != "high" else 0.28))
            last = t
    # 2) words that match a sound, and sentence turns (questions / exclamations)
    if level != "subtle":
        last = -9.0
        for i, w in enumerate(words):
            k = re.sub(r"[^\w']", "", w["w"].lower())
            snd = next((s for s, ks in MATCH_WORDS.items() if k in ks), None)
            if snd and w["s"] > 1.0 and w["s"] - last > 3.5:
                out.append((w["s"], snd, {"ding": 0.4, "boom": 0.32, "pop_hi": 0.3, "zap": 0.2, "sparkle": 0.3}[snd]))
                last = w["s"]
            elif w["w"].rstrip().endswith("?") and i + 1 < len(words) and words[i + 1]["s"] - last > 3.0:
                out.append((words[i + 1]["s"] - 0.05, "rise", 0.3))
                last = words[i + 1]["s"]
    # 3) never let it go quiet for long: fill gaps with a light pattern interrupt on the next word/caption
    gap = RETENTION_GAP.get(level, 4.5)
    anchors = sorted(set([round(w["s"], 2) for w in words] + [round(c, 2) for c in chunk_starts]))
    times = sorted([t for t, _n, _g in existing + out] + [max(1.2, hook_end)])
    k = 0
    cursor = times[0] if times else 1.2
    stop = dur - (3.0 if dur > 10 else 0.8)
    while cursor + gap < stop:
        nxt = next((t for t in times if t > cursor + 0.3), stop)
        if nxt - cursor > gap:
            target = cursor + gap * 0.8
            a = next((x for x in anchors if target - 0.6 <= x <= cursor + gap), target)
            name, g = FILLERS[k % len(FILLERS)]
            out.append((a, name, g * (1.2 if level == "high" else 1.0)))
            k += 1
            times.append(a)
            times.sort()
            cursor = a
        else:
            cursor = nxt
    return out


def _dedupe(ev: list) -> list:
    ev = sorted(ev, key=lambda e: e[0])
    out = []
    for e in ev:
        near = [o for o in out if e[0] - o[0] < 0.2]
        if any(o[1] == e[1] for o in near) or (len(near) >= (2 if e[0] > 0.3 else 3)):
            continue   # never stack more than two sounds at once (three for the opening hit)
        out.append(e)
    return out


def _plan_core(level: str, dur: float, words: list[dict], chunk_starts: list[float], caption_style: dict,
               hook_anim: Optional[str], intro: str, motion: str, punch_times: list[float], has_cta: bool,
               emphasis: set) -> list[tuple[float, str, float]]:
    ev: list[tuple[float, str, float]] = []
    # opening: intro + hook
    if intro == "flash":
        ev.append((0.0, "shutter", 0.8))
    elif intro == "shake":
        ev.append((0.0, "boom", 0.9))
    elif intro == "fade_black":
        ev.append((0.0, "rise", 0.6))
    hook_sfx = {"pop": [("boom", 0.7), ("pop_lo", 0.6)], "drop": [("whoosh", 0.7)], "slide": [("swipe", 0.8)],
                "flicker": [("glitch", 0.5)], "glitch": [("glitch", 0.7), ("boom", 0.4)], "fade": [("whoosh2", 0.4)]}
    if hook_anim:
        for name, g in hook_sfx.get(hook_anim, []):
            ev.append((0.02, name, g))
    if has_cta and dur >= 8:
        ev.append((max(0.0, dur - 2.6), "ding", 0.55))
    if level == "subtle":
        return ev

    # zooms on key words
    if motion == "punch":
        for i, p in enumerate(punch_times):
            if 0.6 < p < dur - 0.6 and i % 2 == 0:
                ev.append((max(0.0, p - 0.12), "whoosh2", 0.45))
    # captions
    mode = caption_style.get("mode")
    last = -9.0
    if mode == "typewriter":
        for i, w in enumerate(words):
            ev.append((w["s"], f"key{i % 4 + 1}", 0.35))
    elif caption_style.get("slide"):
        for t in chunk_starts:
            if t - last > 1.4:
                ev.append((t, "swipe", 0.25))
                last = t
    elif caption_style.get("pop") or caption_style.get("bounce") or mode == "oneword":
        every = 0.0 if level == "high" else 2.2
        for t in chunk_starts:
            if t - last > max(every, 0.35):
                ev.append((t, "pop" if level == "high" else "pop_hi", 0.28))
                last = t
    # emphasis hits (numbers, key words)
    last = -9.0
    for w in words:
        k = re.sub(r"[^\w']", "", w["w"].lower())
        if (k in emphasis or re.fullmatch(r"\$?\d[\d,.%]*[kmb%]?", k or "-")) and w["s"] - last > 3.0 and w["s"] > 1.0:
            ev.append((w["s"], "pop_lo" if level == "medium" else "boom", 0.35 if level == "medium" else 0.45))
            last = w["s"]
    if level == "high" and dur > 12:
        ev.append((max(0.0, dur - 3.2), "riser", 0.4))
        if hook_anim in ("pop", "glitch"):
            ev.append((0.02, "bass_drop", 0.35))
    return ev


def mix_track(events: list[tuple[float, str, float]], dur: float, out_path: str, volume: float = 0.55,
              level: str = "medium") -> Optional[str]:
    """Render all events into one stereo WAV the length of the Short."""
    if not events:
        return None
    n = int(SR * (dur + 1.5))
    track = np.zeros((n, 2), dtype=np.float32)
    g0 = volume * LEVEL_GAIN.get(level, 1.0 if level != "subtle" else 0.6)
    for t, name, g in events:
        s = sound(name)
        a = int(max(0.0, t) * SR)
        if a >= n:
            continue
        b = min(n, a + len(s))
        track[a:b] += s[: b - a] * (g * g0)
    track = np.tanh(track * 1.2) / 1.2  # gentle limiter so overlaps never clip
    _write_wav(Path(out_path), track[: int(SR * dur)])
    return out_path
