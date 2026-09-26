"""Find the best moments in a long video from its transcript + audio energy."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import numpy as np

TERMINAL = tuple(".?!۔؟।…")
STOP = set("""
a an the and or but if so of to in on at for with from by as is are was were be been being it its this that these those
i me my we our you your he she him her they them their what which who whom there here then than too very just also not no
do does did done have has had will would can could should may might must shall about into over under again more most some
such only own same other each few both all any because while until up down out off yeah okay ok like um uh gonna wanna
really actually basically literally right know mean thing things get got going go one two lot kind sort
hai hain ka ki ke ko se mein main aur ye yeh wo woh bhi to tha thi the kya na nahi ho hota hoti raha rahe rahi jo
""".split())

HOOK_WORDS = {
    # English
    "why", "how", "what", "secret", "secrets", "mistake", "mistakes", "never", "always", "stop", "truth", "nobody",
    "everyone", "best", "worst", "biggest", "crazy", "insane", "shocking", "actually", "wrong", "real", "reason",
    "problem", "money", "free", "fast", "easy", "hard", "most", "only", "first", "last", "important", "imagine",
    "listen", "look", "wait", "here's", "trick", "hack", "tip", "rule", "lesson", "you", "your", "don't", "can't",
    "won't", "must", "need", "should", "if", "because", "finally", "changed", "life", "success", "fail", "failed",
    # Roman Urdu / Hindi
    "kyun", "kyon", "kaise", "raaz", "galti", "kabhi", "zaroor", "sach", "paisa", "paise", "asal", "sabse", "yaad",
    "suno", "dekho", "sirf", "zindagi", "kamyabi", "masla",
    # Urdu / Hindi script
    "کیوں", "کیسے", "راز", "غلطی", "کبھی", "ضرور", "سچ", "پیسہ", "صرف", "زندگی", "کامیابی", "مسئلہ", "سنو", "دیکھو",
    "क्यों", "कैसे", "राज़", "गलती", "कभी", "ज़रूर", "सच", "पैसा", "सिर्फ", "ज़िंदगी", "सुनो", "देखो",
}
INTENSE = {
    "amazing", "incredible", "unbelievable", "insane", "crazy", "love", "hate", "huge", "massive", "terrible",
    "perfect", "powerful", "dangerous", "shocking", "honestly", "seriously", "literally", "exactly", "absolutely",
    "wow", "boom", "million", "billion", "thousand", "percent", "guaranteed", "zabardast", "kamaal", "bohat", "bahut",
    "زبردست", "کمال", "بہت", "ज़बरदस्त", "कमाल", "बहुत",
}
FILLER_PENALTY = {"subscribe", "sponsor", "sponsored", "description", "patreon", "merch", "bell", "notification",
                  "welcome", "intro", "outro", "promo", "discount", "coupon"}


@dataclass
class Clip:
    start: float
    end: float
    score: float
    text: str
    hook: str
    title: str
    keywords: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)
    # parts of the source that make up the Short, in playback order: [[start, end], ...] (absolute seconds).
    # Empty = one continuous piece start..end. Several parts = a stitched Short (cuts / cold open).
    segments: list = field(default_factory=list)

    def parts(self) -> list:
        return [tuple(p) for p in self.segments] if self.segments else [(self.start, self.end)]

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.parts())

    def to_dict(self) -> dict:
        return asdict(self)


def _norm(w: str) -> str:
    return re.sub(r"[^\w'؀-ۿऀ-ॿ]", "", w.lower())


def flatten_words(transcript: dict) -> list[dict]:
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            if w["e"] > w["s"] >= 0:
                words.append(dict(w))
        if seg.get("words"):
            words[-1]["seg_end"] = True
    words.sort(key=lambda w: w["s"])
    return words


def sentences(words: list[dict], pause: float = 0.7) -> list[dict]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w["e"]) if nxt else 99
        if w["w"].endswith(TERMINAL) or gap > pause or (w.get("seg_end") and gap > 0.35) or len(cur) > 45:
            out.append({"i0": cur[0], "i1": cur[-1], "s": words[cur[0]]["s"], "e": words[cur[-1]]["e"],
                        "text": " ".join(words[k]["w"] for k in cur),
                        "terminal": w["w"].endswith(TERMINAL)})
            cur = []
    return out


def _hook_score(text: str) -> float:
    toks = [_norm(t) for t in text.split()]
    toks = [t for t in toks if t]
    if not toks:
        return 0.0
    s = 0.0
    s += 1.5 if "?" in text or "؟" in text else 0
    s += sum(1.0 for t in toks[:8] if t in HOOK_WORDS) * 0.6
    s += 0.8 if re.search(r"\d", text) else 0
    s += 0.6 if 4 <= len(toks) <= 14 else (-0.4 if len(toks) > 25 else 0)
    s -= 0.9 if toks[0] in {"and", "but", "so", "or", "which", "then", "because", "that"} else 0  # mid-thought start
    return s


def make_title(text: str, max_words: int = 9) -> str:
    t = re.sub(r"\s+", " ", text).strip().strip(",;:-")
    words = t.split()
    lead = {"so", "and", "but", "um", "uh", "like", "okay", "well", "now", "yeah", "because"}
    while words and _norm(words[0]) in lead:
        words = words[1:]
    if not words:
        return t[:40]
    words = words[:max_words]
    title = " ".join(words).rstrip(",;:-")
    if len(t.split()) > max_words and not title.endswith(TERMINAL):
        title += "..."
    return title[0].upper() + title[1:]


def _energy_at(energy: np.ndarray, s: float, e: float, hop: float = 0.1) -> np.ndarray:
    a, b = int(s / hop), max(int(s / hop) + 1, int(e / hop))
    return energy[a:b] if len(energy) else np.zeros(1)


def _zs(vals: list[float]) -> list[float]:
    a = np.array(vals, dtype=float)
    sd = a.std()
    return list((a - a.mean()) / sd) if sd > 1e-9 else [0.0] * len(vals)


def find_highlights(
    transcript: dict,
    energy: np.ndarray,
    duration: float,
    n: int = 5,
    min_len: float = 20,
    max_len: float = 59,
    min_gap: float = 5,
) -> list[Clip]:
    words = flatten_words(transcript)
    if len(words) < 20:
        return energy_highlights(energy, duration, n, min_len, max_len, min_gap)

    sents = sentences(words)
    target = (min_len + max_len) / 2
    # global vocabulary for keyword importance
    vocab = Counter(_norm(w["w"]) for w in words)
    total = sum(vocab.values()) or 1
    med_db = float(np.median(energy)) if len(energy) > 5 else -30.0

    cands = []
    for i in range(len(sents)):
        s0 = sents[i]
        for j in range(i, len(sents)):
            dur = sents[j]["e"] - s0["s"]
            if dur > max_len:
                break
            if dur < min_len:
                continue
            ws = words[s0["i0"]: sents[j]["i1"] + 1]
            toks = [_norm(w["w"]) for w in ws]
            content = [t for t in toks if t and t not in STOP and len(t) > 2]
            text = " ".join(w["w"] for w in ws)
            gaps = sum(max(0.0, ws[k + 1]["s"] - ws[k]["e"] - 0.25) for k in range(len(ws) - 1))
            en = _energy_at(energy, s0["s"], sents[j]["e"])
            local = Counter(content)
            # repeated topical words inside the window that are not ultra common in the whole video
            topical = sum(min(c, 4) * math.log(total / vocab[t]) for t, c in local.items() if c >= 2)
            cands.append({
                "i": i, "j": j, "s": s0["s"], "e": sents[j]["e"], "text": text,
                "f": {
                    "hook": _hook_score(s0["text"]) + 0.4 * _hook_score(sents[i + 1]["text"] if i + 1 <= j else ""),
                    "pace": len(ws) / max(dur, 1),
                    "pauses": -gaps / max(dur, 1),
                    "energy": float(en.mean() - med_db) if len(en) else 0.0,
                    "dynamics": float(en.std()) if len(en) else 0.0,
                    "intense": sum(1 for t in toks if t in INTENSE or t in HOOK_WORDS) / max(len(toks), 1) * 10,
                    "topical": topical / max(len(toks), 1),
                    "variety": len(set(content)) / max(len(content), 1),
                    "complete": 1.0 if sents[j]["terminal"] else 0.0,
                    "conf": float(np.mean([w.get("p", 1) for w in ws])),
                    "length": -abs(dur - target) / max(target, 1),
                    "filler": -sum(1 for t in toks if t in FILLER_PENALTY),
                    "position": -1.0 if s0["s"] < min(20, duration * 0.03) else 0.0,
                },
            })
    if not cands:
        return energy_highlights(energy, duration, n, min_len, max_len, min_gap)

    weights = {"hook": 1.6, "pace": 0.6, "pauses": 0.7, "energy": 0.8, "dynamics": 0.4, "intense": 0.8,
               "topical": 0.5, "variety": 0.3, "complete": 0.9, "conf": 0.5, "length": 0.6, "filler": 1.2,
               "position": 0.8}
    keys = list(weights)
    cols = {k: _zs([c["f"][k] for c in cands]) for k in keys}
    for idx, c in enumerate(cands):
        c["score"] = sum(weights[k] * cols[k][idx] for k in keys)
        c["z"] = {k: round(cols[k][idx], 2) for k in keys}

    cands.sort(key=lambda c: c["score"], reverse=True)
    chosen: list[dict] = []
    for c in cands:
        if all(c["e"] + min_gap <= o["s"] or c["s"] >= o["e"] + min_gap for o in chosen):
            chosen.append(c)
        if len(chosen) >= n:
            break

    clips = []
    for c in chosen:
        ss = [sents[k] for k in range(c["i"], c["j"] + 1)]
        best = max(ss[: max(1, min(3, len(ss)))], key=lambda s: _hook_score(s["text"]))
        content = Counter(t for t in (_norm(w) for w in c["text"].split()) if t and t not in STOP and len(t) > 3)
        kws = [k for k, _ in content.most_common(6)]
        start = max(0.0, c["s"] - 0.15)
        end = min(duration, c["e"] + 0.4)
        top = sorted(c["z"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        clips.append(Clip(start, end, round(c["score"], 3), c["text"], best["text"], make_title(best["text"]),
                          kws, {k: v for k, v in top}))
    clips.sort(key=lambda c: c.start)
    return clips


def energy_highlights(energy: np.ndarray, duration: float, n: int, min_len: float, max_len: float,
                      min_gap: float, hop: float = 0.1) -> list[Clip]:
    """Fallback when there is little/no speech: pick the loudest, most dynamic windows."""
    L = (min_len + max_len) / 2
    if duration <= L:
        return [Clip(0, duration, 0, "", "", "Highlight")]
    win = int(L / hop)
    e = energy if len(energy) > win else np.pad(energy, (0, win))
    cs = np.cumsum(np.insert(e, 0, 0))
    means = (cs[win:] - cs[:-win]) / win
    order = np.argsort(-means)
    chosen = []
    for k in order:
        s = k * hop
        if s + L > duration:
            continue
        if all(s + L + min_gap <= a or s >= b + min_gap for a, b in chosen):
            chosen.append((s, s + L))
        if len(chosen) >= n:
            break
    chosen.sort()
    return [Clip(a, b, 0, "", "", f"Highlight {i + 1}") for i, (a, b) in enumerate(chosen)]


def words_in_range(transcript: dict, start: float, end: float) -> list[dict]:
    """Words inside [start, end], re-timed relative to start."""
    out = []
    for w in flatten_words(transcript):
        if w["s"] >= start - 0.05 and w["e"] <= end + 0.3:
            out.append({"w": w["w"], "s": max(0.0, w["s"] - start), "e": max(0.05, min(end, w["e"]) - start)})
    return out
