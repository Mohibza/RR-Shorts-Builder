"""Virality score 0-99 for each clip, with a breakdown (hook, emotion, value, flow) and plain-language reasons.

Uses what the clip actually contains: the opening line, emotional/intense words, loudness against the rest of the
video, useful content (numbers, advice, rare topic words) and how cleanly it starts and ends. Scores are
calibrated per video, so the best moment of any video lands in the 85-98 range and weaker ones spread below.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

from .highlights import INTENSE, _hook_score, _norm

ADVICE = {"how", "why", "secret", "mistake", "tip", "tips", "never", "always", "should", "stop", "start", "best",
          "worst", "rule", "rules", "lesson", "learn", "learned", "truth", "reason", "step", "steps", "trick",
          "kaise", "kyun", "ghalti", "galti", "raaz", "sach", "sabak", "tareeqa", "hamesha", "kabhi"}
STRONG = INTENSE | {"money", "paisa", "paise", "rich", "broke", "fail", "failed", "success", "million", "crore",
                    "lakh", "died", "fired", "quit", "shocked", "scared", "love", "hate"}
CONTEXT = {"and", "but", "so", "because", "which", "that", "also", "then", "aur", "lekin", "to", "phir"}


def _words_in(words: list[dict], parts: list) -> list[dict]:
    out = []
    for a, b in parts:
        out += [w for w in words if w["s"] >= a - 0.05 and w["e"] <= b + 0.05]
    return out


def _features(clip, words: list[dict], energy: Optional[np.ndarray], med_db: float) -> dict:
    parts = clip.parts()
    ws = _words_in(words, parts)
    toks = [t for t in (_norm(w["w"]) for w in ws) if t]
    n = max(1, len(toks))
    first = " ".join(w["w"] for w in ws if w["s"] < parts[0][0] + 4.0) or clip.hook or clip.title
    dur = sum(b - a for a, b in parts)
    strong = [t for t in toks if t in STRONG]
    advice = [t for t in toks if t in ADVICE]
    nums = sum(1 for w in ws if re.search(r"\d", w["w"]))
    en = 0.0
    if energy is not None and len(energy):
        vals = [energy[int(a * 10):max(int(a * 10) + 1, int(b * 10))] for a, b in parts]
        vals = np.concatenate([v for v in vals if len(v)]) if any(len(v) for v in vals) else np.zeros(1)
        en = float(np.mean(vals) - med_db)
    last = ws[-1]["w"] if ws else ""
    return {
        "hook_raw": _hook_score(first) + (0.8 if "?" in first else 0) + (0.5 if re.search(r"\d", first) else 0)
        - (1.2 if toks and toks[0] in CONTEXT else 0),
        "emotion_raw": len(strong) / n * 20 + max(-2.0, min(4.0, en / 3)) + 0.4 * sum(w["w"].endswith("!") for w in ws),
        "value_raw": len(advice) / n * 25 + min(3, nums) * 0.6 + len(set(clip.keywords or [])) * 0.15,
        "flow_raw": (1.0 if last.rstrip().endswith((".", "!", "?", "۔", "؟")) else -0.6)
        - 0.4 * max(0, len(parts) - 1) - abs(dur - 38) / 38 + (0.6 if len(parts) > 1 and clip.reasons.get("cold_open") else 0),
        "strong": list(dict.fromkeys(strong))[:4], "advice": list(dict.fromkeys(advice))[:3], "first": first,
        "question": "?" in first or "؟" in first, "numbers": nums, "dur": dur, "parts": len(parts),
        "energy": en, "ends_clean": last.rstrip().endswith((".", "!", "?", "۔", "؟")),
    }


def _scale(vals: list[float], lo: float = 45, hi: float = 97) -> list[float]:
    """Rank-and-spread within the video: best -> ~hi, worst -> ~lo, keeping relative gaps."""
    if not vals:
        return []
    a = np.array(vals, float)
    if a.max() - a.min() < 1e-6:
        return [float((lo + hi) / 2 + 8)] * len(vals)
    z = (a - a.min()) / (a.max() - a.min())
    return [float(lo + (hi - lo) * (0.35 + 0.65 * v)) for v in z]


def score_clips(clips: list, words: list[dict], energy: Optional[np.ndarray] = None) -> list[dict]:
    """Returns one {score, breakdown{hook,emotion,value,flow}, reasons[]} per clip (same order)."""
    if not clips:
        return []
    med = float(np.median(energy)) if energy is not None and len(energy) > 5 else -30.0
    feats = [_features(c, words, energy, med) for c in clips]
    dims = {}
    for k in ("hook", "emotion", "value", "flow"):
        dims[k] = _scale([f[k + "_raw"] for f in feats], 40, 98)
    base = _scale([getattr(c, "score", 0.0) for c in clips], 50, 97)      # the director's own ranking
    out = []
    for i, (c, f) in enumerate(zip(clips, feats)):
        bd = {k: int(round(dims[k][i])) for k in dims}
        total = 0.35 * bd["hook"] + 0.2 * bd["emotion"] + 0.2 * bd["value"] + 0.25 * bd["flow"]
        total = 0.55 * total + 0.45 * base[i]
        if (c.reasons or {}).get("type", "").endswith("pick"):           # an AI editor chose it
            total += 3
        reasons = []
        if f["question"]:
            reasons.append("Opens with a question that makes people stay for the answer")
        elif bd["hook"] >= 80:
            reasons.append("Strong first line: it works without any context")
        if f["strong"]:
            reasons.append("Emotional words: " + ", ".join(f["strong"]))
        if f["advice"]:
            reasons.append("Useful takeaway (" + ", ".join(f["advice"]) + ")")
        if f["numbers"]:
            reasons.append("Specific numbers make it believable")
        if f["energy"] > 3:
            reasons.append("More energetic than the rest of the video")
        if f["ends_clean"]:
            reasons.append("Ends on a complete thought")
        if (c.reasons or {}).get("cold_open"):
            reasons.append("Teaser opening: starts on its most gripping line")
        if (c.reasons or {}).get("why"):
            reasons.append(str(c.reasons["why"])[:140])
        if not reasons:
            reasons.append("Complete, self-contained moment")
        out.append({"score": int(max(1, min(99, round(total)))), "breakdown": bd, "reasons": reasons[:5]})
    return out


def alt_hooks(clip, words: list[dict], n: int = 3) -> list[str]:
    """Offline hook options: the chosen title, the strongest sentence, and a curiosity version."""
    from .highlights import make_title
    opts = [clip.title]
    if clip.hook and make_title(clip.hook) not in opts:
        opts.append(make_title(clip.hook))
    ws = _words_in(words, clip.parts())
    text = " ".join(w["w"] for w in ws)
    sents = [s.strip() for s in re.split(r"(?<=[.!?؟۔])\s+", text) if len(s.split()) >= 4]
    for s in sorted(sents, key=lambda s: -_hook_score(s))[:3]:
        t = make_title(s)
        if t not in opts:
            opts.append(t)
    kws = [k for k in (clip.keywords or []) if len(k) > 3]
    if kws and len(opts) < n + 1:
        opts.append(f"Nobody talks about {kws[0]} like this")
    return [o for o in opts if o][:n]
