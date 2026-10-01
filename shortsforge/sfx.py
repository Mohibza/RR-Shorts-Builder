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
    x = np.nan_to_num(x)
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


def impact(seed=16):
    """Cinematic hit: sub thump + noise crack + metallic ring."""
    t = _t(1.4)
    f = 70 * np.exp(-t * 5) + 34
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6) * 1.2
    y += _bandpass(_noise(len(t), seed), 300, 5000) * np.exp(-t * 26) * 1.4
    for fr, a in ((523.0, 0.18), (741.0, 0.12), (1187.0, 0.08)):
        y += np.sin(2 * np.pi * fr * t) * np.exp(-t * 3.5) * a
    return _stereo(_norm(np.tanh(y * 1.8), 0.92))


def braam(seed=17):
    """Low brass-like swell (trailer 'braam')."""
    t = _t(1.6)
    n = len(t)
    y = sum(2 * ((f * t) % 1) - 1 for f in (55.0, 55.4, 82.4, 110.2)) / 4
    y = _onepole_lp(y, 600 + 1800 * np.sin(np.pi * np.clip(t / 1.6, 0, 1)))
    env = np.minimum(1, t / 0.08) * np.exp(-np.maximum(0, t - 0.4) * 2.2)
    return _stereo(_norm(np.tanh(y * env * 3), 0.8))


def reverse_swell(seed=18):
    t = _t(0.9)
    n = len(t)
    y = _bandpass(_noise(n, seed), np.linspace(400, 2000, n), np.linspace(3000, 12000, n))
    env = (t / t[-1]) ** 3 * np.minimum(1, (t[-1] - t) / 0.02)
    return _stereo(_norm(y * env, 0.65), np.linspace(-0.3, 0.3, n))


def heartbeat(seed=19):
    t = _t(0.9)
    y = np.zeros(len(t))
    for st, a in ((0.0, 1.0), (0.24, 0.7)):
        k = int(st * SR)
        tt = t[: len(t) - k]
        y[k:] += np.sin(2 * np.pi * (55 * np.exp(-tt * 6) + 40) * tt) * np.exp(-tt * 16) * a
    return _stereo(_norm(np.tanh(y * 2), 0.85))


def stinger(seed=20):
    """Dark tension hit: detuned low cluster + noise burst."""
    t = _t(1.1)
    y = sum(np.sin(2 * np.pi * f * t) for f in (61.7, 65.4, 92.5, 130.8)) / 4
    y *= np.exp(-t * 2.4)
    y += _bandpass(_noise(len(t), seed), 800, 6000) * np.exp(-t * 14) * 0.8
    return _stereo(_norm(np.tanh(y * 2.4), 0.85))


def tick(seed=21):
    t = _t(0.05)
    y = np.sin(2 * np.pi * 3200 * t) * np.exp(-t * 700) + _bandpass(_noise(len(t), seed), 3000, 9000) * np.exp(-t * 900) * 0.4
    return _stereo(_norm(y, 0.5))


def soft_pop(seed=22):
    t = _t(0.1)
    f = 520 * np.exp(-t * 25) + 260
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 45)
    return _stereo(_norm(y, 0.6))


def sub_hit(seed=23):
    """808-style sub kick."""
    t = _t(0.8)
    f = 120 * np.exp(-t * 18) + 44
    y = np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * 2.5) * np.exp(-t * 3.5)
    y[: int(SR * 0.003)] += 0.3
    return _stereo(_norm(y, 0.92))


def clap_hit(seed=24):
    t = _t(0.25)
    n = len(t)
    y = np.zeros(n)
    for k0 in (0, 0.011, 0.023):
        k = int(k0 * SR)
        y[k:] += _bandpass(_noise(n - k, seed + k), 900, 6000) * np.exp(-np.arange(n - k) / SR * 30)
    return _stereo(_norm(y, 0.6))


def cowbell_hit(seed=25):
    t = _t(0.35)
    y = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (587.0, 845.0)) / 2
    y = _bandpass(y, 500, 3500) * np.exp(-t * 12)
    return _stereo(_norm(y, 0.45))


def airhorn(seed=26):
    t = _t(0.9)
    n = len(t)
    vib = 1 + 0.01 * np.sin(2 * np.pi * 6 * t)
    y = sum(2 * (((f * vib) * t) % 1) - 1 for f in (466.2, 587.3, 698.5))
    y = _onepole_lp(y / 3, 3500)
    env = np.minimum(1, t / 0.02) * np.minimum(1, (t[-1] - t) / 0.12)
    for k0 in (0.18, 0.36):   # the classic stutter
        k = int(k0 * SR)
        env[k:k + int(SR * 0.03)] *= 0.25
    return _stereo(_norm(np.tanh(y * env * 2.2), 0.55))


def scratch(seed=27):
    """Vinyl scratch: noise through a sweeping band + pitch wobble."""
    t = _t(0.35)
    n = len(t)
    sweep = 800 + 2500 * np.abs(np.sin(2 * np.pi * 3.2 * t))
    y = _bandpass(_noise(n, seed), sweep * 0.6, sweep * 1.6)
    y += np.sin(2 * np.pi * np.cumsum(220 + 380 * np.sin(2 * np.pi * 3.2 * t)) / SR) * 0.4
    return _stereo(_norm(y * np.clip(np.sin(np.pi * t / t[-1]), 0, 1) ** 0.6, 0.55))


def tape_stop(seed=28):
    t = _t(0.6)
    f = 330 * (1 - t / t[-1]) ** 1.8 + 20
    y = (2 * ((np.cumsum(f) / SR) % 1) - 1) * np.exp(-t * 1.5)
    return _stereo(_norm(_onepole_lp(y, 1800), 0.5))


def boing(seed=29):
    t = _t(0.55)
    f = 180 + 120 * np.exp(-t * 6) * np.sin(2 * np.pi * 14 * t)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    return _stereo(_norm(y, 0.6))


def slide_whistle(seed=30, up=True):
    t = _t(0.5)
    f = np.linspace(700, 1800, len(t)) if up else np.linspace(1800, 600, len(t))
    f = f * (1 + 0.02 * np.sin(2 * np.pi * 7 * t))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.clip(np.sin(np.pi * t / t[-1]), 0, 1) ** 0.5
    return _stereo(_norm(y, 0.4))


def cartoon_pop(seed=31):
    t = _t(0.16)
    f = 1500 * np.exp(-t * 20) + 400
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 28) * (1 + 0.4 * np.sin(2 * np.pi * 45 * t))
    return _stereo(_norm(y, 0.6))


def honk(seed=32):
    t = _t(0.3)
    y = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (392.0, 415.3)) / 2
    y = _onepole_lp(y, 2200) * np.minimum(1, t / 0.01) * np.minimum(1, (t[-1] - t) / 0.04)
    return _stereo(_norm(y, 0.4))


def sad_horn(seed=33):
    t = _t(1.2)
    y = np.zeros(len(t))
    for i, f in enumerate((392.0, 370.0, 349.2, 329.6)):
        k = int(i * 0.24 * SR)
        L = int((0.5 if i == 3 else 0.24) * SR)
        tt = np.arange(min(L, len(t) - k)) / SR
        vib = 1 + (0.02 * np.sin(2 * np.pi * 5 * tt) if i == 3 else 0)
        seg = 2 * (((f * vib) * tt) % 1) - 1
        y[k:k + len(tt)] += _onepole_lp(seg, 1500) * np.minimum(1, (tt[-1] - tt) / 0.03 + 0.001)
    return _stereo(_norm(y, 0.4))


def bubble(seed=34):
    t = _t(0.12)
    f = 400 + 1400 * (t / t[-1]) ** 2
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 25)
    return _stereo(_norm(y, 0.5))


def beep(seed=35):
    t = _t(0.12)
    y = np.sin(2 * np.pi * 1760 * t) * np.minimum(1, t / 0.005) * np.minimum(1, (t[-1] - t) / 0.01)
    return _stereo(_norm(y, 0.35))


def blip(seed=36):
    t = _t(0.07)
    y = np.sign(np.sin(2 * np.pi * (1200 + 1800 * t / t[-1]) * t)) * np.exp(-t * 40)
    return _stereo(_norm(_onepole_lp(y, 6000), 0.35))


def data(seed=37):
    rng = np.random.default_rng(seed)
    t = _t(0.35)
    y = np.zeros(len(t))
    pos = 0
    while pos < len(t):
        L = int(SR * 0.025)
        f = rng.choice([880, 1320, 1760, 2640])
        tt = np.arange(min(L, len(t) - pos)) / SR
        y[pos:pos + len(tt)] = np.sin(2 * np.pi * f * tt) * 0.6
        pos += L + int(SR * rng.uniform(0.005, 0.02))
    return _stereo(_norm(y * np.linspace(1, 0.5, len(t)), 0.3), rng.uniform(-0.4, 0.4))


def laser(seed=38):
    t = _t(0.3)
    f = 3000 * np.exp(-t * 11) + 250
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 8)
    return _stereo(_norm(y, 0.45), np.linspace(-0.6, 0.6, len(t)))


def cash(seed=39):
    """Cash register: bell + drawer rattle."""
    t = _t(0.8)
    y = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, a, d in ((2093, 1, 5), (2637, 0.6, 6), (4186, 0.3, 9)))
    k = int(0.02 * SR)
    y[k:] += _bandpass(_noise(len(t) - k, seed), 1500, 8000) * np.exp(-np.arange(len(t) - k) / SR * 30) * 0.5
    return _stereo(_norm(y, 0.55))


GENERATORS = {
    "sparkle": sparkle, "thud": thud, "zap": zap, "click": click, "bass_drop": bass_drop, "swoosh_down": swoosh_down,
    "whoosh": lambda: whoosh(1), "whoosh2": lambda: whoosh(11, 0.45), "rise": lambda: whoosh(21, 0.4, up=True),
    "swipe": swipe, "pop": pop, "pop_hi": lambda: pop(13, 1.4), "pop_lo": lambda: pop(23, 0.7),
    "key1": lambda: key(4), "key2": lambda: key(14), "key3": lambda: key(24), "key4": lambda: key(34),
    "ding": ding, "boom": boom, "shutter": shutter, "glitch": glitch, "riser": riser,
    "impact": impact, "braam": braam, "reverse_swell": reverse_swell, "heartbeat": heartbeat, "stinger": stinger,
    "tick": tick, "soft_pop": soft_pop, "sub_hit": sub_hit, "clap_hit": clap_hit, "cowbell_hit": cowbell_hit,
    "airhorn": airhorn, "scratch": scratch, "tape_stop": tape_stop, "boing": boing,
    "slide_up": lambda: slide_whistle(30, True), "slide_down": lambda: slide_whistle(40, False),
    "cartoon_pop": cartoon_pop, "honk": honk, "sad_horn": sad_horn, "bubble": bubble, "beep": beep, "blip": blip,
    "data": data, "laser": laser, "cash": cash,
}

# ---------------------------------------------------------------------------- sound packs
# Every pack answers the same "roles" with its own sounds; each pick also gets a random variant (pitch/speed),
# so no two Shorts sound alike and the same sound never plays twice in a row.
PACKS: dict[str, dict[str, list]] = {
    "cinematic": {
        "slam": ["impact", "boom", "bass_drop"], "whip": ["whoosh", "swoosh_down"], "glitch": ["glitch", "stinger"],
        "impact": ["impact", "boom"], "flash": ["shutter", "impact"], "swell": ["reverse_swell", "riser"],
        "hit": ["thud", "impact"], "hook": ["impact", "braam", "whoosh"], "zoom": ["whoosh2", "thud", "reverse_swell"],
        "cut": ["whoosh", "swoosh_down"], "jump": ["swipe", "whoosh2"], "caption": ["tick", "soft_pop"],
        "emph": ["thud", "impact"], "money": ["ding", "cash"], "neg": ["stinger", "boom"], "fast": ["whoosh2", "zap"],
        "wow": ["sparkle", "braam"], "magic": ["sparkle"], "question": ["rise", "reverse_swell"],
        "filler": ["tick", "whoosh2", "heartbeat", "swoosh_down", "reverse_swell"], "outro": ["riser", "reverse_swell"],
        "cta": ["ding", "impact"]},
    "hype": {
        "slam": ["bass_drop", "sub_hit", "boom"], "whip": ["whoosh", "scratch"], "glitch": ["glitch", "tape_stop"],
        "impact": ["sub_hit", "boom"], "flash": ["shutter", "sub_hit"], "swell": ["riser", "rise"], "hit": ["sub_hit", "clap_hit"],
        "hook": ["airhorn", "sub_hit", "bass_drop"], "zoom": ["sub_hit", "whoosh2", "clap_hit"],
        "cut": ["scratch", "whoosh"], "jump": ["swipe", "clap_hit"], "caption": ["pop", "clap_hit"],
        "emph": ["sub_hit", "cowbell_hit"], "money": ["cash", "ding"], "neg": ["boom", "tape_stop"],
        "fast": ["zap", "whoosh2"], "wow": ["airhorn", "sparkle"], "magic": ["sparkle"], "question": ["rise", "scratch"],
        "filler": ["cowbell_hit", "clap_hit", "scratch", "swipe", "sub_hit"], "outro": ["riser"], "cta": ["cash", "ding"]},
    "clean": {
        "slam": ["thud", "soft_pop"], "whip": ["swipe", "whoosh2"], "glitch": ["click", "soft_pop"], "impact": ["thud"],
        "flash": ["shutter", "soft_pop"], "swell": ["rise", "reverse_swell"], "hit": ["soft_pop", "thud"],
        "hook": ["soft_pop", "whoosh2", "sparkle"], "zoom": ["whoosh2", "soft_pop", "swipe"], "cut": ["swipe", "whoosh2"],
        "jump": ["swipe"], "caption": ["soft_pop", "click", "bubble"], "emph": ["soft_pop", "thud"], "money": ["ding"],
        "neg": ["thud"], "fast": ["swipe", "whoosh2"], "wow": ["sparkle"], "magic": ["sparkle"], "question": ["rise"],
        "filler": ["soft_pop", "swipe", "click", "bubble"], "outro": ["rise", "reverse_swell"], "cta": ["ding"]},
    "funny": {
        "slam": ["boing", "honk"], "whip": ["slide_up", "whoosh"], "glitch": ["glitch", "cartoon_pop"],
        "impact": ["boing", "cartoon_pop"], "flash": ["cartoon_pop", "shutter"], "swell": ["slide_up"],
        "hit": ["cartoon_pop", "boing"], "hook": ["boing", "honk", "slide_up"], "zoom": ["boing", "cartoon_pop", "slide_up"],
        "cut": ["scratch", "whoosh"], "jump": ["cartoon_pop", "swipe"], "caption": ["cartoon_pop", "pop_hi", "bubble"],
        "emph": ["boing", "honk"], "money": ["cash", "ding"], "neg": ["sad_horn", "slide_down"], "fast": ["slide_up", "zap"],
        "wow": ["sparkle", "boing"], "magic": ["sparkle"], "question": ["slide_up"],
        "filler": ["cartoon_pop", "boing", "honk", "bubble"], "outro": ["slide_up"], "cta": ["ding", "cash"]},
    "tech": {
        "slam": ["glitch", "laser", "sub_hit"], "whip": ["laser", "swoosh_down"], "glitch": ["glitch", "data"],
        "impact": ["sub_hit", "glitch"], "flash": ["shutter", "beep"], "swell": ["riser"], "hit": ["blip", "sub_hit"],
        "hook": ["glitch", "beep", "laser"], "zoom": ["blip", "laser", "zap"], "cut": ["glitch", "swipe"],
        "jump": ["blip", "click"], "caption": ["blip", "click"], "emph": ["beep", "data"], "money": ["ding", "beep"],
        "neg": ["glitch", "tape_stop"], "fast": ["zap", "laser"], "wow": ["sparkle", "data"], "magic": ["sparkle"],
        "question": ["rise", "beep"], "filler": ["data", "blip", "click", "glitch"], "outro": ["riser"], "cta": ["ding"]},
}
PACK_NAMES = {"auto": "Auto (matches the vibe)", "cinematic": "Cinematic", "hype": "Hype / Trap", "clean": "Clean & Minimal",
              "funny": "Funny / Cartoon", "tech": "Tech / Glitch"}
VARIANTS = (1.0, 0.89, 1.12, 0.95, 1.06)   # pitch/speed of each variant
INTRO_ROLE = {"zoom_slam": "slam", "whip": "whip", "rgb_glitch": "glitch", "shake": "impact", "flash": "flash",
              "fade_black": "swell", "fade_white": "swell", "punch_in": "hit"}


class Picker:
    """Seeded sound choice per role: varied, never the same sound (and variant) twice in a row."""

    def __init__(self, pack: str, seed: int):
        import random
        self.pack = PACKS.get(pack) or PACKS["clean"]
        self.rnd = random.Random(seed)
        self.last = ""
        self.last_role: dict[str, str] = {}

    def __call__(self, role: str) -> str:
        pool = list(self.pack.get(role) or PACKS["clean"].get(role) or ["pop"])
        cands = [p for p in pool if p != self.last and p != self.last_role.get(role)] or pool
        name = self.rnd.choice(cands)
        var = self.rnd.randrange(len(VARIANTS))
        self.last = name
        self.last_role[role] = name
        return f"{name}@{var}" if var else name


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
    """A sound by name; "name@k" = variant k (same sound, pitched/sped up or down)."""
    if name in _cache:
        return _cache[name]
    base, _, var = name.partition("@")
    if var:
        x = sound(base)
        f = VARIANTS[int(var) % len(VARIANTS)]
        n = max(2, int(len(x) / f))
        src = np.linspace(0, len(x) - 1, n)
        y = np.stack([np.interp(src, np.arange(len(x)), x[:, c]) for c in (0, 1)], 1).astype(np.float32)
        _cache[name] = y
        return y
    p = SFX_DIR / f"{base}_v{VERSION}.wav"
    if not p.exists():
        _write_wav(p, GENERATORS[base]())
    _cache[name] = _read_wav(p)
    return _cache[name]


# ---------------------------------------------------------------------------- timing
LEVEL_GAIN = {"subtle": 0.6, "medium": 1.0, "high": 1.15}


# words that get a matching sound (English + Roman Urdu/Hindi) -> sound role
MATCH_WORDS = {
    "money": {"money", "cash", "dollar", "dollars", "paisa", "paise", "paisay", "rupay", "rupees", "rupee", "crore",
              "lakh", "million", "billion", "profit", "income", "salary", "rich", "sale", "sales", "earn", "kamai"},
    "neg": {"never", "nahi", "nahin", "kabhi", "stop", "mistake", "ghalti", "galti", "fail", "failed", "danger",
            "warning", "shocking", "crazy", "boom", "lost", "khatam", "khtm", "worst", "dead", "died", "killed"},
    "fast": {"fast", "quick", "quickly", "instant", "instantly", "speed", "jaldi", "foran", "turant", "seconds",
             "minute", "minutes", "now", "abhi"},
    "magic": {"magic", "beautiful", "perfect", "shine", "dream", "khubsurat", "khoobsurat", "wonderful", "love",
              "pyar", "happy", "khush"},
    "wow": {"wow", "amazing", "zabardast", "kamal", "kamaal", "best", "secret", "raaz", "trick", "hack", "free",
            "easy", "simple", "asaan", "win", "success", "kamyab", "kamiab"},
}
ROLE_GAIN = {"money": 0.4, "neg": 0.34, "fast": 0.22, "magic": 0.3, "wow": 0.3}
# the longest stretch without any sound before a light "pattern interrupt" is added (keeps attention)
RETENTION_GAP = {"subtle": 8.0, "medium": 4.5, "high": 3.0}
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
         emphasis: set, seams: Optional[list] = None, hook_end: float = 0.0, pack: str = "clean",
         seed: int = 0, story: Optional[list] = None) -> list[tuple[float, str, float]]:
    """Return [(time, sound, gain)] for one Short.

    `pack` picks the family of sounds (cinematic, hype, clean, funny, tech) and `seed` makes every Short choose
    its own sounds/variants from it. punch_times = the focus zooms (each gets a sound so zoom + sound hit together).
    seams = [(t, removed_seconds, is_part_join)] where the Short jumps (pauses cut / parts joined): each gets a
    transition sound. After everything else, gaps longer than RETENTION_GAP get a light pattern interrupt on the
    next word so the sound design keeps moving all the way through."""
    level = resolve_level(level, words, dur)
    if level == "off":
        return []
    pick = Picker(pack, seed)
    # Story FX first (riser into each beat, hit when it snaps back, whoosh on streaks): they win any clash
    ev = [(max(0.0, t), pick(role), g) for t, role, g in (story or []) if t < dur - 0.1]
    ev += _plan_core(level, dur, words, chunk_starts, caption_style, hook_anim, intro, punch_times, has_cta,
                     emphasis, pick)
    ev += _plan_retention(level, dur, words, chunk_starts, seams or [], ev, hook_end, pick)
    return _dedupe(ev)


def _plan_retention(level: str, dur: float, words: list[dict], chunk_starts: list[float], seams: list,
                    existing: list, hook_end: float, pick: "Picker") -> list:
    out = []
    busy = sorted(t for t, _n, _g in existing)

    def free(t: float, gap: float = 0.5) -> bool:
        return all(abs(t - b) > gap for b in busy)
    # 1) cuts: whoosh on joined parts, soft swipe on bigger jump cuts
    last = -9.0
    for t, removed, join in seams:
        if not (0.4 < t < dur - 0.4):
            continue
        if join:
            out.append((max(0.0, t - 0.15), pick("cut"), 0.5))
            last = t
        elif removed >= (0.35 if level == "high" else 0.6) and t - last > (1.8 if level == "high" else 3.0) and free(t):
            out.append((max(0.0, t - 0.08), pick("jump"), 0.2 if level != "high" else 0.28))
            last = t
    # 2) words that match a sound, and sentence turns (questions / exclamations)
    if level != "subtle":
        last = -9.0
        for i, w in enumerate(words):
            k = re.sub(r"[^\w']", "", w["w"].lower())
            role = next((r for r, ks in MATCH_WORDS.items() if k in ks), None)
            if role and w["s"] > 1.0 and w["s"] - last > 3.5 and free(w["s"], 0.3):
                out.append((w["s"], pick(role), ROLE_GAIN[role]))
                last = w["s"]
            elif w["w"].rstrip().endswith("?") and i + 1 < len(words) and words[i + 1]["s"] - last > 3.0:
                out.append((words[i + 1]["s"] - 0.05, pick("question"), 0.3))
                last = words[i + 1]["s"]
    # 3) never let it go quiet for long: fill gaps with a light pattern interrupt on the next word/caption
    gap = RETENTION_GAP.get(level, 4.5)
    anchors = sorted(set([round(w["s"], 2) for w in words] + [round(c, 2) for c in chunk_starts]))
    times = sorted([t for t, _n, _g in existing + out] + [max(1.2, hook_end)])
    cursor = times[0] if times else 1.2
    stop = dur - (3.0 if dur > 10 else 0.8)
    while cursor + gap < stop:
        nxt = next((t for t in times if t > cursor + 0.3), stop)
        if nxt - cursor > gap:
            target = cursor + gap * 0.8
            a = next((x for x in anchors if target - 0.6 <= x <= cursor + gap), target)
            out.append((a, pick("filler"), 0.2 * (1.2 if level == "high" else 1.0)))
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
        if any(o[1].split("@")[0] == e[1].split("@")[0] for o in near) or (len(near) >= (2 if e[0] > 0.3 else 3)):
            continue   # never stack more than two sounds at once (three for the opening hit)
        out.append(e)
    return out


def _plan_core(level: str, dur: float, words: list[dict], chunk_starts: list[float], caption_style: dict,
               hook_anim: Optional[str], intro: str, punch_times: list[float], has_cta: bool,
               emphasis: set, pick: "Picker") -> list[tuple[float, str, float]]:
    ev: list[tuple[float, str, float]] = []
    # opening: intro effect + hook
    role = INTRO_ROLE.get(intro)
    if role:
        ev.append((0.0, pick(role), 0.85 if role in ("slam", "impact") else 0.7))
    if hook_anim:
        ev.append((0.03, pick("hook"), 0.6))
    if has_cta and dur >= 8:
        ev.append((max(0.0, dur - 2.6), pick("cta"), 0.55))
    if level == "subtle":
        return ev

    # focus zooms: one sound on each, landing with the zoom
    last = -9.0
    for p in punch_times:
        if 0.6 < p < dur - 0.6 and p - last > 1.0:
            ev.append((max(0.0, p - 0.1), pick("zoom"), 0.42))
            last = p
    # captions
    mode = caption_style.get("mode")
    last = -9.0
    if mode == "typewriter":
        for i, w in enumerate(words):
            ev.append((w["s"], f"key{i % 4 + 1}", 0.35))
    elif caption_style.get("slide"):
        for t in chunk_starts:
            if t - last > 1.4:
                ev.append((t, pick("jump"), 0.22))
                last = t
    elif caption_style.get("pop") or caption_style.get("bounce") or mode == "oneword":
        every = 0.0 if level == "high" else 2.2
        for t in chunk_starts:
            if t - last > max(every, 0.35):
                ev.append((t, pick("caption"), 0.26))
                last = t
    # emphasis hits (numbers, key words)
    last = -9.0
    for w in words:
        k = re.sub(r"[^\w']", "", w["w"].lower())
        if (k in emphasis or re.fullmatch(r"\$?\d[\d,.%]*[kmb%]?", k or "-")) and w["s"] - last > 3.0 and w["s"] > 1.0:
            ev.append((w["s"], pick("emph"), 0.35 if level == "medium" else 0.45))
            last = w["s"]
    if level == "high" and dur > 12:
        ev.append((max(0.0, dur - 3.2), pick("outro"), 0.4))
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
