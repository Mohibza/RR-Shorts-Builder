"""Vibe engine: reads what a clip *feels* like and turns that into creative choices.

One clip = one vibe (hype, motivational, emotional, funny, suspense, story, info, chill). The vibe decides
which music moods fit, which sound-effect pack plays, how the camera moves, how the Short opens and which
colour look suits it. Every Short also gets its own seed so two Shorts with the same vibe still get a
different track, different sound variants and a different opening.

Detection works offline (word lists in English, Roman Urdu/Hindi, Urdu and Hindi script + how fast and how
loud the speaker is). When an AI key is set, the director can also hand us a vibe; that one wins.
"""
from __future__ import annotations

import random
import re
import zlib
from typing import Iterable, Optional

VIBES: dict[str, dict] = {
    "hype": dict(
        name="Hype / Energy", music=["phonk", "trap", "drill", "upbeat"], pack="hype",
        motions=["punch", "punch", "zoom_pulse"], intros=["zoom_slam", "shake", "rgb_glitch", "whip"],
        grades=["punchy", "hdr", "vibrant", "cyberpunk"], zoom=0.16),
    "motivational": dict(
        name="Motivational", music=["motivational", "cinematic", "epic", "upbeat"], pack="cinematic",
        motions=["punch", "punch", "slow_zoom"], intros=["zoom_slam", "flash", "punch_in"],
        grades=["cinematic", "golden", "hdr"], zoom=0.13),
    "emotional": dict(
        name="Emotional", music=["emotional", "chill", "cinematic"], pack="clean",
        motions=["slow_zoom", "punch", "ken_burns"], intros=["fade_black", "fade_white", "punch_in"],
        grades=["moody", "cinematic", "faded", "matte"], zoom=0.09),
    "funny": dict(
        name="Funny", music=["funk", "happy", "upbeat"], pack="funny",
        motions=["punch", "punch", "sway"], intros=["whip", "zoom_slam", "shake"],
        grades=["vibrant", "punchy", "pastel"], zoom=0.18),
    "suspense": dict(
        name="Suspense / Story of crime & power", music=["dark", "cinematic", "epic", "phonk"], pack="cinematic",
        motions=["punch", "punch", "slow_zoom"], intros=["rgb_glitch", "fade_black", "zoom_slam"],
        grades=["moody", "noir", "cinematic", "night"], zoom=0.12),
    "story": dict(
        name="Storytime", music=["lofi", "cinematic", "chill", "emotional"], pack="clean",
        motions=["punch", "ken_burns", "punch"], intros=["punch_in", "fade_black", "flash"],
        grades=["cinematic", "warm", "golden"], zoom=0.1),
    "info": dict(
        name="Tips / Explainer", music=["lofi", "upbeat", "chill", "funk"], pack="tech",
        motions=["punch", "punch", "breathe"], intros=["punch_in", "flash", "whip"],
        grades=["hdr", "teal", "vibrant"], zoom=0.12),
    "chill": dict(
        name="Calm / Chill", music=["chill", "lofi", "emotional"], pack="clean",
        motions=["breathe", "slow_zoom", "punch"], intros=["fade_white", "fade_black", "none"],
        grades=["dream", "pastel", "warm"], zoom=0.08),
}

# words that pull a clip toward a vibe (lower-case; English, Roman Urdu/Hindi, Urdu, Hindi)
LEX: dict[str, set] = {
    "hype": {"insane", "crazy", "boom", "let's", "lets", "go", "fire", "beast", "win", "won", "winning", "champion",
             "destroy", "crushed", "crush", "fastest", "biggest", "power", "unstoppable", "hustle", "grind", "money",
             "million", "billion", "rich", "zabardast", "kamaal", "kamal", "khatarnak", "ज़बरदस्त", "कमाल", "زبردست",
             "کمال", "dhamaka", "tabahi", "jeet", "jeeta", "jeetna"},
    "motivational": {"dream", "dreams", "goal", "goals", "success", "successful", "never", "give", "up", "believe",
                     "discipline", "hard", "work", "future", "change", "life", "purpose", "achieve", "focus",
                     "mehnat", "kamyabi", "kamiyabi", "sapna", "sapne", "khwab", "manzil", "himmat", "haar", "koshish",
                     "मेहनत", "कामयाबी", "सपना", "हिम्मत", "محنت", "کامیابی", "خواب", "ہمت", "منزل"},
    "emotional": {"mother", "father", "mom", "dad", "cry", "cried", "tears", "died", "death", "lost", "alone", "pain",
                  "hurt", "sad", "love", "miss", "sorry", "broken", "heart", "family", "ammi", "abbu", "maa", "baap",
                  "dukh", "dard", "aansu", "rona", "roya", "mohabbat", "pyar", "akela", "maut", "माँ", "दर्द", "आंसू",
                  "प्यार", "ماں", "درد", "آنسو", "محبت", "موت"},
    "funny": {"haha", "hahaha", "lol", "funny", "joke", "joking", "laugh", "laughing", "hilarious", "weird", "stupid",
              "dumb", "silly", "prank", "mazaak", "mazak", "hansi", "hasna", "pagal", "bewakoof", "मज़ाक", "हंसी",
              "پاگل", "مذاق"},
    "suspense": {"arrest", "arrested", "police", "army", "attack", "war", "killed", "kill", "murder", "secret",
                 "mystery", "danger", "dangerous", "assassination", "operation", "mission", "agent", "spy", "crime",
                 "gang", "don", "terror", "bomb", "escape", "plan", "target", "enemy", "shocking", "truth",
                 "hidden", "never", "raaz", "khatra", "qatl", "jung", "fauj", "dushman", "saazish", "sazish",
                 "गिरफ्तार", "हमला", "रहस्य", "मिशन", "دشمن", "حملہ", "راز", "جنگ", "قتل"},
    "story": {"story", "once", "remember", "happened", "then", "when", "was", "years", "ago", "day", "night",
              "told", "said", "kahani", "kissa", "qissa", "yaad", "hua", "tha", "thi", "कहानी", "किस्सा", "کہانی",
              "قصہ", "یاد"},
    "info": {"how", "tip", "tips", "step", "steps", "learn", "use", "tool", "way", "method", "trick", "hack",
             "explain", "why", "because", "means", "first", "second", "third", "data", "percent", "number", "rule",
             "tareeqa", "tarika", "seekho", "samjho", "matlab", "kaise", "kyun", "tarah", "tareeka", "तरीका",
             "कैसे", "क्यों", "طریقہ", "کیسے", "کیوں"},
    "chill": {"calm", "peace", "peaceful", "relax", "relaxing", "slow", "beautiful", "nature", "morning", "coffee",
              "sukoon", "aram", "aaram", "khubsurat", "सुकून", "آرام", "سکون"},
}
_TOK = re.compile(r"[^\w']+", re.UNICODE)


def _tokens(words: Iterable) -> list[str]:
    out = []
    for w in words:
        t = w["w"] if isinstance(w, dict) else str(w)
        out += [x for x in _TOK.split(t.lower()) if x]
    return out


def detect(words: list[dict], energy: Optional[list] = None, hint: str = "") -> dict:
    """{'vibe': key, 'scores': {...}, 'pace': words/sec}. `hint` = a vibe an AI already suggested."""
    if hint in VIBES:
        return {"vibe": hint, "scores": {hint: 1.0}, "pace": 0.0, "source": "ai"}
    toks = _tokens(words)
    n = max(1, len(toks))
    dur = max(1.0, (words[-1]["e"] - words[0]["s"]) if words else 1.0)
    pace = len(words) / dur
    text = " ".join(w["w"] for w in words)
    sc = {k: 0.0 for k in VIBES}
    for t in toks:
        for k, lex in LEX.items():
            if t in lex:
                sc[k] += 1.0
    for k in sc:
        sc[k] = sc[k] / n * 100            # hits per 100 words
    q = text.count("?") + text.count("؟")
    ex = text.count("!")
    sc["info"] += q * 0.6
    sc["suspense"] += q * 0.35
    sc["hype"] += ex * 0.8 + max(0.0, pace - 3.0) * 2.2
    sc["funny"] += ex * 0.4
    sc["chill"] += max(0.0, 2.2 - pace) * 2.0
    sc["story"] += 0.8                     # a gentle default: most talking-head clips are some kind of story
    if energy is not None and len(energy) > 10:
        import numpy as np
        e = np.asarray(energy, dtype=float)
        spread = float(np.percentile(e, 90) - np.percentile(e, 20))
        if spread > 18:
            sc["hype"] += 1.5
            sc["suspense"] += 0.5
        elif spread < 9:
            sc["chill"] += 1.0
            sc["emotional"] += 0.5
    best = max(sc, key=lambda k: sc[k])
    return {"vibe": best, "scores": {k: round(v, 2) for k, v in sc.items()}, "pace": round(pace, 2), "source": "words"}


def seed_for(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode()) & 0x7FFFFFFF


def pick(vibe: str, key: str, seed: int, avoid: Optional[str] = None) -> str:
    """A seeded choice from the vibe's list for `key` (motions / intros / grades / music)."""
    opts = list(VIBES.get(vibe, VIBES["story"])[key])
    rnd = random.Random(seed * 31 + len(key))
    rnd.shuffle(opts)
    for o in opts:
        if o != avoid:
            return o
    return opts[0]


def pack_for(vibe: str) -> str:
    return VIBES.get(vibe, VIBES["story"])["pack"]


def zoom_amount(vibe: str) -> float:
    return float(VIBES.get(vibe, VIBES["story"])["zoom"])


def catalog() -> dict:
    return {k: {"name": v["name"], "music": v["music"], "pack": v["pack"]} for k, v in VIBES.items()}
