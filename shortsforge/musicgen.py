"""Original background music made on this PC: drums, bass, chords and a melody, generated from scratch.

Nothing is sampled from anyone's recording, so the tracks carry no copyright claims (Content ID can't match
them) and can be used on any platform, monetized or not. Each call with a new seed makes a different track.
"""
from __future__ import annotations

import random
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

SR = 44100

# mood -> (bpm, scale, progression (scale degrees), swing, drum pattern, instrument, extras)
MOODS = {
    "lofi":         dict(name="Lo-fi Chill", bpm=(78, 88), minor=True, prog=[[0, 5, 3, 4], [0, 3, 5, 4], [5, 3, 0, 4]],
                         swing=0.18, drums="lofi", keys="epiano", lead="soft", crackle=True, cutoff=2600),
    "chill":        dict(name="Chill Vibes", bpm=(90, 100), minor=False, prog=[[0, 4, 5, 3], [0, 5, 3, 4]],
                         swing=0.1, drums="soft", keys="pad", lead="soft", crackle=False, cutoff=4500),
    "upbeat":       dict(name="Upbeat Pop", bpm=(114, 124), minor=False, prog=[[0, 4, 5, 3], [5, 3, 0, 4], [0, 5, 3, 4]],
                         swing=0.0, drums="pop", keys="pluck", lead="bright", crackle=False, cutoff=7000),
    "happy":        dict(name="Happy Bounce", bpm=(120, 128), minor=False, prog=[[0, 3, 4, 0], [0, 4, 5, 4]],
                         swing=0.08, drums="pop", keys="pluck", lead="bright", crackle=False, cutoff=8000),
    "motivational": dict(name="Motivational", bpm=(96, 106), minor=False, prog=[[5, 3, 0, 4], [0, 4, 5, 3]],
                         swing=0.0, drums="big", keys="piano", lead="arp", crackle=False, cutoff=6000),
    "epic":         dict(name="Epic Cinematic", bpm=(84, 92), minor=True, prog=[[0, 5, 2, 6], [0, 6, 5, 6]],
                         swing=0.0, drums="big", keys="strings", lead="arp", crackle=False, cutoff=5000),
    "trap":         dict(name="Trap Beat", bpm=(136, 146), minor=True, prog=[[0, 5, 6, 4], [0, 0, 5, 6]],
                         swing=0.0, drums="trap", keys="pad", lead="bell", crackle=False, cutoff=5500),
    "phonk":        dict(name="Phonk Drift", bpm=(128, 142), minor=True, prog=[[0, 0, 5, 6], [0, 6, 5, 4]],
                         swing=0.0, drums="phonk", keys="pad", lead="cowbell", crackle=False, cutoff=6500),
    "drill":        dict(name="Drill Beat", bpm=(138, 146), minor=True, prog=[[0, 5, 3, 4], [0, 3, 5, 6]],
                         swing=0.0, drums="drill", keys="strings", lead="bell", crackle=False, cutoff=6000),
    "emotional":    dict(name="Emotional Piano", bpm=(68, 80), minor=True, prog=[[0, 5, 2, 6], [5, 3, 0, 4]],
                         swing=0.0, drums="none", keys="strings", lead="keys", crackle=False, cutoff=5000),
    "cinematic":    dict(name="Cinematic Trailer", bpm=(86, 96), minor=True, prog=[[0, 5, 6, 4], [0, 3, 6, 5]],
                         swing=0.0, drums="big", keys="strings", lead="brass", crackle=False, cutoff=5500),
    "funk":         dict(name="Funky Groove", bpm=(104, 116), minor=False, prog=[[0, 3, 0, 4], [0, 5, 3, 4]],
                         swing=0.06, drums="funk", keys="pluck", lead="bright", crackle=False, cutoff=8000),
    "dark":         dict(name="Dark Suspense", bpm=(70, 80), minor=True, prog=[[0, 1, 0, 6], [0, 5, 1, 0]],
                         swing=0.0, drums="soft", keys="strings", lead="bell", crackle=False, cutoff=2500),
}
GAINS = {"kick": 0.34, "snare": 0.38, "hat": 0.7, "bass": 0.2, "keys": 0.42, "lead": 0.42}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """One-pole low-pass (twice) without scipy: blocked recursion in numpy."""
    a = float(np.exp(-2 * np.pi * cutoff / SR))
    try:
        from scipy.signal import lfilter
        y = lfilter([1 - a], [1, -a], x)
        return lfilter([1 - a], [1, -a], y)
    except Exception:
        # FFT low-pass fallback
        n = len(x)
        f = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(n, 1 / SR)
        f *= 1 / (1 + (freqs / cutoff) ** 4)
        return np.fft.irfft(f, n)


def _env(n: int, a: float, d: float, s: float, r: float, hold: float) -> np.ndarray:
    t = np.arange(n) / SR
    e = np.where(t < a, t / max(a, 1e-4), s + (1 - s) * np.exp(-(t - a) / max(d, 1e-4)))
    rel = t > hold
    e[rel] *= np.exp(-(t[rel] - hold) / max(r, 1e-4))
    return e


class _Mix:
    def __init__(self, seconds: float):
        self.n = int(SR * seconds) + SR * 3
        self.L = np.zeros(self.n, np.float32)
        self.R = np.zeros(self.n, np.float32)

    def add(self, t: float, x: np.ndarray, gain: float = 1.0, pan: float = 0.0):
        i = int(t * SR)
        if i >= self.n:
            return
        x = x[: self.n - i] * gain
        self.L[i:i + len(x)] += x * (1 - max(0, pan))
        self.R[i:i + len(x)] += x * (1 + min(0, pan))


# ------------------------------------------------------------------ instruments
def kick(big=False, trap=False):
    dur = 0.9 if trap else 0.45
    t = np.arange(int(SR * dur)) / SR
    f = (140 if not trap else 110) * np.exp(-t * (30 if not trap else 9)) + (48 if not trap else 42)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * (7 if not trap else 2.6))
    y[: int(SR * 0.004)] += np.random.default_rng(1).uniform(-1, 1, int(SR * 0.004)) * 0.3
    return np.tanh(y * (2.6 if big else 1.8)) * 0.9


def snare(seed, soft=False):
    t = np.arange(int(SR * 0.3)) / SR
    rng = np.random.default_rng(seed)
    noise = rng.uniform(-1, 1, len(t)) * np.exp(-t * (22 if not soft else 30))
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)
    y = _lowpass(noise, 7000 if not soft else 3500) * 0.8 + tone * 0.5
    return y * (0.55 if soft else 0.8)


def clap(seed):
    t = np.arange(int(SR * 0.3)) / SR
    rng = np.random.default_rng(seed)
    y = np.zeros(len(t))
    for k in (0, 0.012, 0.024):
        i = int(k * SR)
        y[i:] += rng.uniform(-1, 1, len(t) - i) * np.exp(-(t[: len(t) - i]) * 35)
    return _lowpass(y, 6000) * 0.5


def hat(seed, open_=False):
    t = np.arange(int(SR * (0.25 if open_ else 0.06))) / SR
    rng = np.random.default_rng(seed)
    x = rng.uniform(-1, 1, len(t))
    y = x - _lowpass(x, 7000)
    return y * np.exp(-t * (14 if open_ else 70)) * 0.35


def note(freq: float, dur: float, kind: str, seed: int = 0) -> np.ndarray:
    n = int(SR * (dur + 0.6))
    t = np.arange(n) / SR
    if kind == "epiano":
        y = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 3)
             + 0.1 * np.sin(2 * np.pi * freq * 3.01 * t) * np.exp(-t * 6))
        y *= _env(n, 0.005, 1.2, 0.35, 0.25, dur) * (1 + 0.08 * np.sin(2 * np.pi * 4.5 * t))
    elif kind == "piano":
        y = sum(a * np.sin(2 * np.pi * freq * h * t) * np.exp(-t * (1.5 + h)) for h, a in ((1, 1), (2, .4), (3, .2), (4, .08)))
        y *= _env(n, 0.003, 0.8, 0.2, 0.2, dur)
    elif kind == "pluck":
        saw = 2 * ((freq * t) % 1) - 1
        y = _lowpass(saw * np.exp(-t * 6), 3000) * _env(n, 0.003, 0.25, 0.0, 0.1, dur)
    elif kind == "strings":
        y = sum(2 * (((freq * (1 + d)) * t) % 1) - 1 for d in (-0.004, 0, 0.005)) / 3
        y = _lowpass(y, 1800) * _env(n, 0.35, 1.0, 0.85, 0.5, dur)
    elif kind == "bell":
        y = (np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 4)) * np.exp(-t * 2.2)
        y *= _env(n, 0.002, 1, 1, 0.3, dur + 0.4)
    elif kind == "bass":
        y = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
        y = np.tanh(y * 1.4) * _env(n, 0.005, 0.4, 0.7, 0.08, dur)
    elif kind == "sub808":
        f = freq * (1 + 0.5 * np.exp(-t * 40))
        y = np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * 2.0) * _env(n, 0.003, 1.5, 0.6, 0.15, dur)
    elif kind == "cowbell":
        y = sum(np.sign(np.sin(2 * np.pi * freq * r * t)) for r in (1.0, 1.44)) / 2
        y = _lowpass(y, 3500) * np.exp(-t * 9) * _env(n, 0.002, 1, 1, 0.05, dur)
    elif kind == "brass":
        y = sum(2 * (((freq * (1 + d)) * t) % 1) - 1 for d in (-0.003, 0.004)) / 2
        y = _lowpass(y, 1200) * _env(n, 0.12, 0.8, 0.8, 0.3, dur) * (1 + 0.04 * np.sin(2 * np.pi * 5 * t))
    elif kind == "tom":
        f = freq * (1 + 0.6 * np.exp(-t * 25))
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
    else:  # pad
        y = sum(2 * (((freq * (1 + d)) * t) % 1) - 1 for d in (-0.006, 0, 0.007)) / 3
        y = _lowpass(y, 1400) * _env(n, 0.6, 1.5, 0.8, 0.8, dur)
    return y.astype(np.float32)


# ------------------------------------------------------------------ composition
def generate(mood: str, seconds: float = 90, seed: Optional[int] = None,
             progress: Callable[[float], None] = lambda f: None) -> tuple[np.ndarray, dict]:
    M = MOODS.get(mood, MOODS["lofi"])
    seed = seed if seed is not None else random.randrange(1, 10 ** 6)
    rnd = random.Random(seed)
    bpm = rnd.randint(*M["bpm"])
    beat = 60 / bpm
    bar = beat * 4
    root = rnd.choice([57, 58, 59, 60, 61, 62, 63]) - (12 if M["minor"] else 12)
    scale = MINOR if M["minor"] else MAJOR
    prog = rnd.choice(M["prog"])
    bars = max(12, int(seconds / bar))
    mix = _Mix(bars * bar)
    swing = M["swing"]
    G = dict(GAINS)
    G["keys"] *= {"strings": 1.4, "pad": 1.5, "epiano": 1.0, "piano": 1.0, "pluck": 1.8}.get(M["keys"], 1.0)
    urban = M["drums"] in ("trap", "phonk", "drill")

    def deg(d, octave=0):
        return root + scale[d % 7] + 12 * (d // 7 + octave)

    def chord(d):
        return [deg(d), deg(d + 2), deg(d + 4)]

    # a short melody motif, reused with variation (sounds composed, not random)
    pent = [0, 1, 2, 4, 5] if not M["minor"] else [0, 2, 3, 4, 6]
    motif = [(rnd.choice([0, 0.5, 1, 1.5, 2, 2.5, 3]), rnd.choice(pent)) for _ in range(rnd.randint(4, 6))]
    motif.sort()

    kicks = {"lofi": [0, 2.5], "soft": [0, 2], "pop": [0, 1, 2, 3], "big": [0, 1.5, 2, 3.5], "trap": [0, 2.75],
             "phonk": [0, 1.75, 2.5], "drill": [0, 2.5, 3.25], "funk": [0, 0.75, 2.5], "none": []}[M["drums"]]
    for b in range(bars):
        t0 = b * bar
        section = "intro" if b < 2 else "outro" if b >= bars - 2 else ("break" if (b // 8) % 3 == 2 and b % 8 < 2 else "main")
        d = prog[b % len(prog)]
        # chords
        for m in chord(d):
            mix.add(t0, note(_hz(m + 12), bar * 0.98, M["keys"], seed + b), G["keys"],
                    rnd.uniform(-0.4, 0.4))
        # bass
        if section != "intro":
            bass_kind = "sub808" if urban else "bass"
            pattern = ([0, 2.5] if M["drums"] in ("lofi", "trap", "drill") else [0, 1.75, 2.5] if M["drums"] == "phonk"
                       else [0, 0.75, 1.5, 2, 2.75, 3.5] if M["drums"] == "funk"
                       else [0, 1.5, 2, 3] if M["drums"] in ("pop", "big") else [0, 2])
            for j, p in enumerate(pattern):
                octv = 12 if (M["drums"] == "funk" and j % 2) else 0
                mix.add(t0 + p * beat, note(_hz(deg(d) - 12 + octv),
                                            beat * (1.4 if len(pattern) <= 3 else 0.5 if M["drums"] == "funk" else 0.9),
                                            bass_kind), G["bass"])
        # drums
        if M["drums"] != "none" and (section in ("main", "outro") or (section == "break" and urban)):
            for p in kicks:
                if section == "break" and p > 0:
                    continue
                mix.add(t0 + p * beat, kick(M["drums"] == "big", urban), G["kick"])
            for p in ((2,) if M["drums"] == "drill" else (1, 3)):
                s = clap(seed + b) if M["drums"] in ("pop", "trap", "phonk", "funk") else snare(seed + b + p, M["drums"] in ("lofi", "soft"))
                mix.add(t0 + p * beat + (0.02 if M["drums"] == "lofi" else 0), s, G["snare"] if M["drums"] != "soft" else G["snare"] * 0.65)
            if M["drums"] == "big" and b % 2 == 1:           # trailer toms
                for p, m in ((3, 45), (3.25, 43), (3.5, 40), (3.75, 38)):
                    mix.add(t0 + p * beat, note(_hz(m), 0.3, "tom"), G["kick"] * 0.8, 0.2)
            steps = 12 if M["drums"] == "drill" else 16 if urban or M["drums"] == "funk" else 8
            for i in range(steps):
                sw = swing * beat / 2 if i % 2 else 0
                if urban and b % 2 == 1 and i >= steps - 4:     # hi-hat rolls
                    for r in range(3):
                        mix.add(t0 + (i + r / 3) * bar / steps, hat(seed + i * 7 + r), G["hat"] * 0.8)
                    continue
                mix.add(t0 + i * bar / steps + sw, hat(seed + i, open_=(i == steps - 2 and b % 4 == 3)),
                        G["hat"] if i % 2 == 0 else G["hat"] * 0.65, 0.25)
        # melody on the main sections
        if section == "main" and M["lead"]:
            kind = {"soft": "epiano", "bright": "pluck", "arp": "piano", "bell": "bell", "cowbell": "cowbell",
                    "keys": "piano", "brass": "brass"}[M["lead"]]
            if M["lead"] == "cowbell":                     # phonk: the cowbell riff on 8ths
                riff = [0, 2, 3, 2, 0, 4, 3, 2]
                for i, dd in enumerate(riff):
                    if (i + b) % 5 == 4:
                        continue
                    m = root + 24 + scale[(pent[dd % len(pent)]) % 7]
                    mix.add(t0 + i * beat / 2, note(_hz(m), beat / 2, kind), G["lead"] * 0.7, 0.2 if i % 2 else -0.2)
            elif M["lead"] == "brass" and b % 2 == 0:       # long trailer brass stabs
                mix.add(t0, note(_hz(deg(d) + 12), bar * 0.9, kind), G["lead"] * 0.9)
                mix.add(t0, note(_hz(deg(d + 4) + 12), bar * 0.9, kind), G["lead"] * 0.6)
            elif M["lead"] == "arp":
                for i in range(8):
                    m = chord(d)[i % 3] + 24
                    mix.add(t0 + i * beat / 2, note(_hz(m), beat / 2, kind), G["lead"] * 0.8, 0.3 if i % 2 else -0.3)
            else:
                shift = 0 if (b // 4) % 2 == 0 else rnd.choice([1, 2, -1])
                for (p, dd) in motif:
                    m = root + 24 + scale[(pent[pent.index(dd)] + shift) % 7]
                    mix.add(t0 + p * beat, note(_hz(m), beat * 0.8, kind), G["lead"], rnd.uniform(-0.3, 0.3))
        progress(0.9 * (b + 1) / bars)

    L, R = mix.L, mix.R
    if M["crackle"]:
        rng = np.random.default_rng(seed)
        cr = np.zeros(len(L), np.float32)
        idx = rng.integers(0, len(L), int(len(L) / SR * 18))
        cr[idx] = rng.uniform(-0.25, 0.25, len(idx))
        hiss = rng.normal(0, 0.004, len(L)).astype(np.float32)
        L, R = L + cr + hiss, R + cr + hiss
    L, R = _lowpass(L, M["cutoff"]), _lowpass(R, M["cutoff"])
    st = np.stack([L, R], 1)
    st = st[: int(bars * bar * SR + SR * 1.5)]
    st = np.tanh(st * 1.3)
    st /= max(1e-6, float(np.max(np.abs(st)))) / 0.89
    fade_in, fade_out = int(SR * 1.0), int(SR * 3.0)
    st[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    st[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    progress(1.0)
    return st.astype(np.float32), {"bpm": bpm, "seed": seed, "mood": mood, "bars": bars}


def make_track(mood: str, folder: str, seconds: float = 90, seed: Optional[int] = None,
               progress: Callable[[float], None] = lambda f: None) -> Path:
    """Generate a track and save it (M4A) into the music folder, with licence info recorded."""
    from . import music_sources
    from .utils import NO_WINDOW, find_ffmpeg
    audio, info = generate(mood, seconds, seed, progress)
    Path(folder).mkdir(parents=True, exist_ok=True)
    name = MOODS.get(mood, MOODS["lofi"])["name"]
    n = 1
    while True:
        dest = Path(folder) / f"RR Original - {name} {n}.m4a"
        if not dest.exists():
            break
        n += 1
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "t.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", str(dest)],
                       check=True, capture_output=True, creationflags=NO_WINDOW)
    music_sources.record(folder, dest.name, {
        "title": f"{name} {n}", "artist": "Rebels Revolt Shorts (original)", "source": "Made on your PC",
        "license": "Original · no copyright claims", "license_url": "", "credit": "",
        "mood": mood,
        "note": f"Generated by the app ({info['bpm']} BPM, seed {info['seed']}). Free to use anywhere, monetized too."})
    return dest
