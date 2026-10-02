"""Story FX: turns a cut-together clip into a *told* story, like pro edits.

What it adds on top of the vibe engine (all decided from the words, so the preview and the export agree):

* Dramatic pauses ("beats"): the picture freezes for a moment right before the payoff line (after a question,
  before "but / then / lekin / phir…", before a strong line). The frame desaturates, the camera keeps pushing in,
  a riser builds, a short line of text lands on screen, then the story snaps back with a hit, a flash and a
  motion-blur streak. A cold open gets a "rewind" beat where the teaser hands over to the real story.
* Editorial titles: the hook as a magazine cover (big serif word, thin "Lightroom" type, glowing serif), with the
  big word placed *behind* the speaker on export (a person cut-out is laid back on top of the text).
* Streak transitions: directional motion blur + a quick whip zoom on every join and every beat.
* Film texture: grain, soft bloom, light leaks and vignette, matched to the vibe.

Times: the clip is first cut (jump cuts / parts) into the "pre" timeline. Each freeze is inserted at a pre time `t`
for `d` seconds, which pushes everything after it later. `to_final()` / `to_pre()` map between the two timelines.
"""
from __future__ import annotations

import random
import re
from typing import Optional

from . import fonts
from .highlights import INTENSE, STOP

W, H = 1080, 1920
LEVELS = {"off": "Off", "auto": "Auto", "strong": "Strong"}
FILLERS = {"um", "umm", "ummm", "uh", "uhh", "uhhh", "uhm", "erm", "hmm", "hmmm", "mmm"}

# a pause before a sentence that starts with one of these feels like a reveal
CUES = {"but", "however", "then", "suddenly", "until", "because", "so", "actually", "finally", "instead", "except",
        "lekin", "magar", "phir", "pher", "tab", "achanak", "asal", "aakhir", "akhir", "balke", "isliye", "kyunke",
        "kyunki", "toh", "to", "and", "turns", "that's", "thats", "here's", "heres", "the"}
STRONG_CUES = {"but", "however", "suddenly", "until", "actually", "finally", "instead", "lekin", "magar", "achanak",
               "asal", "aakhir", "akhir", "balke"}
SECOND_CUES = {"truth", "secret", "answer", "reason", "problem", "result", "twist", "moment", "day", "end", "when"}

FREEZE_D = {"suspense": 0.95, "emotional": 0.9, "story": 0.8, "motivational": 0.75, "chill": 0.8, "info": 0.65,
            "funny": 0.7, "hype": 0.62}
TITLE_LOOKS = {"story": ["serif", "thin"], "emotional": ["serif", "thin"], "suspense": ["serif", "glow"],
               "motivational": ["thin", "serif", "glow"], "info": ["thin", "glow"], "hype": ["glow", "serif"],
               "funny": ["glow", "thin"], "chill": ["thin", "serif"]}
TAGS = {"story": "STORYTIME", "emotional": "REAL", "suspense": "THE TRUTH", "motivational": "MINDSET",
        "info": "EXPLAINED", "hype": "MUST WATCH", "funny": "WAIT FOR IT", "chill": "MOMENT"}
TEXTURE = {   # grain, bloom, light leak, vignette
    "story": (0.55, 0.22, True, True), "emotional": (0.6, 0.26, True, True), "chill": (0.45, 0.28, True, False),
    "motivational": (0.45, 0.2, True, True), "suspense": (0.75, 0.12, False, True), "info": (0.3, 0.1, False, False),
    "hype": (0.38, 0.16, False, True), "funny": (0.3, 0.12, False, False),
}
# where the big title sits (centre, in 1920 px) for each framing: over the head in a face crop, above the video
# band when the landscape frame is shown whole
TITLE_Y = {"smart_crop": 470, "center_crop": 470, "zoom45": 430, "square": 470, "framed": 330, "blur_fit": 470,
           "black_fit": 470, "split": 300, "split_reverse": 1240, "two_speakers": 300, "fit": 470}

LATIN = re.compile(r"^[\x00-\x7FÀ-ɏ‘’“”…–—]*$")


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", (w or "").lower())


def is_filler(w: str) -> bool:
    return _norm(w) in FILLERS


def options(s, edits_story: Optional[dict] = None) -> dict:
    """Effective Story FX switches: the global settings, overridden by this clip's edits."""
    o = {"level": getattr(s, "story_fx", "auto") or "auto",
         "pauses": bool(getattr(s, "story_pauses", True)), "titles": bool(getattr(s, "story_titles", True)),
         "transitions": bool(getattr(s, "story_transitions", True)),
         "textures": bool(getattr(s, "story_textures", True)), "behind": bool(getattr(s, "story_behind", True))}
    for k, v in (edits_story or {}).items():
        if k in o and v is not None and v != "":
            o[k] = v if k == "level" else bool(v)
    if o["level"] not in LEVELS:
        o["level"] = "auto"
    return o


# ------------------------------------------------------------------ timeline mapping
def to_final(t: float, freezes: list) -> float:
    """pre time -> final time (freezes at or before t push it later)."""
    return t + sum(f["d"] for f in freezes if f["t"] < t - 1e-6)


def to_pre(T: float, freezes: list) -> tuple[float, int]:
    """final time -> (pre time, index of the freeze showing at T or -1)."""
    acc = 0.0
    for i, f in enumerate(sorted(freezes, key=lambda x: x["t"])):
        a = f["t"] + acc
        if T < a:
            return T - acc, -1
        if T < a + f["d"]:
            return f["t"], i
        acc += f["d"]
    return T - acc, -1


def freeze_spans(freezes: list) -> list[tuple[float, float]]:
    """Final-timeline (start, end) of every freeze."""
    out, acc = [], 0.0
    for f in sorted(freezes, key=lambda x: x["t"]):
        out.append((round(f["t"] + acc, 3), round(f["t"] + acc + f["d"], 3)))
        acc += f["d"]
    return out


def shift_words(words: list, freezes: list) -> list:
    if not freezes:
        return words
    return [{**w, "s": round(to_final(w["s"], freezes), 3), "e": round(to_final(w["e"], freezes), 3)} for w in words]


def shift_camera(camera: list, freezes: list) -> list:
    """Camera path in final time; it holds still through each freeze."""
    if not freezes or not camera:
        return camera
    pts = [(to_final(t, freezes), x) for t, x in camera]
    for f in freezes:
        x = _cam_at(camera, f["t"])
        if x is None:
            continue
        a = to_final(f["t"], freezes)
        pts += [(a, x), (a + f["d"], x)]
    pts.sort(key=lambda p: p[0])
    out: list = []
    for t, x in pts:
        if out and t - out[-1][0] < 1e-3:
            out[-1] = (out[-1][0], x)
        else:
            out.append((round(t, 3), x))
    return [list(p) for p in out]


def _cam_at(camera: list, t: float) -> Optional[float]:
    if not camera:
        return None
    if t <= camera[0][0]:
        return camera[0][1]
    for (t0, x0), (t1, x1) in zip(camera, camera[1:]):
        if t0 <= t <= t1:
            return x0 + (x1 - x0) * (t - t0) / max(1e-6, t1 - t0)
    return camera[-1][1]


# ------------------------------------------------------------------ planning
def _sentences(words: list) -> list[tuple[int, int]]:
    """(first, last) word index of each sentence."""
    out, a = [], 0
    for i, w in enumerate(words):
        end = re.search(r"[.?!۔؟।]$", w["w"].strip()) is not None
        gap = (words[i + 1]["s"] - w["e"]) if i + 1 < len(words) else 9.0
        if end or gap > 0.42 or i == len(words) - 1:
            out.append((a, i))
            a = i + 1
    return out


def _best_word(toks: list[str], keywords: set) -> int:
    best, bi = -1.0, -1
    for i, t in enumerate(toks):
        k = _norm(t)
        if len(k) < 3 or k in STOP:
            continue
        sc = min(len(k), 9) * 0.3 + (3.0 if k in keywords else 0) + (2.0 if k in INTENSE else 0) + 0.15 * i
        if len(k) > 12:
            sc -= 2.5
        if sc > best:
            best, bi = sc, i
    return bi


def plan(words: list, D: float, pieces: list, vibe: str, seed: int, title: str, keywords: list,
         lang: str, layout: str, opts: dict, cold_open: bool, watermark: str = "", accent: str = "#FFE400",
         seams: Optional[list] = None, fps: int = 30, head_top: Optional[float] = None,
         head_exact: bool = False, place: Optional[dict] = None) -> dict:
    """The Story FX plan for one Short. words/pieces/seams are in the pre (cut) timeline."""
    level = opts.get("level", "auto")
    empty = {"level": "off", "freezes": [], "title": None, "beats": [], "streaks": [], "leaks": [],
             "texture": None, "behind": False, "blackouts": []}
    if level == "off" or D < 4:
        return empty
    rnd = random.Random(seed * 131 + 7)
    kw = {_norm(k) for k in keywords or []}
    roman = lang in ("ur", "hi", "pa")
    freezes: list = []

    # 1) beats (dramatic pauses)
    if opts.get("pauses", True) and words and D >= 8:
        fd = FREEZE_D.get(vibe, 0.75) * (1.1 if level == "strong" else 1.0)
        fd = round(max(0.62, min(1.1, fd)), 2)
        n_max = (1 + (D >= 40)) if level == "auto" else (2 + (D >= 32))
        cands = []
        if cold_open and len(pieces) >= 2:
            p0 = pieces[0]
            k0 = p0[2] if len(p0) > 2 and p0[2] else [(0.0, p0[1] - p0[0])]
            seam = sum(b - a for a, b in k0)
            if 1.5 < seam < D - 3:
                cands.append({"t": round(seam - 0.02, 3), "score": 9.0, "kind": "rewind", "next": None})
        sents = _sentences(words)
        for j in range(len(sents) - 1):
            a, b = sents[j]
            na, nb = sents[j + 1]
            last = words[b]
            t = last["e"] + min(0.12, max(0.02, (words[na]["s"] - last["e"]) / 2))
            if t < 3.2 or t > D - 3.0:
                continue
            nxt = [_norm(w["w"]) for w in words[na:nb + 1]]
            sc = 0.0
            if last["w"].strip().endswith(("?", "؟")):
                sc += 3.0
            if nxt and nxt[0] in STRONG_CUES:
                sc += 3.0
            elif nxt and nxt[0] in CUES and len(nxt) > 1 and nxt[1] in SECOND_CUES | STRONG_CUES:
                sc += 2.0
            elif nxt and nxt[0] in CUES:
                sc += 0.6
            if any(x in INTENSE or x in kw for x in nxt):
                sc += 1.2
            if nb - na >= 3:
                sc += 0.4
            rel = t / D
            sc += 1.0 if 0.45 <= rel <= 0.85 else (0.4 if 0.3 <= rel < 0.45 else 0.0)
            sc += rnd.random() * 0.3
            cands.append({"t": round(t, 3), "score": sc, "kind": "reveal", "next": (na, nb)})
        thr = 1.6 if level == "auto" else 1.0
        for c in sorted(cands, key=lambda c: -c["score"]):
            if len(freezes) >= n_max or c["score"] < thr:
                continue
            if any(abs(c["t"] - f["t"]) < (6.0 if level == "auto" else 4.5) for f in freezes):
                continue
            freezes.append({"t": c["t"], "d": fd if c["kind"] == "reveal" else round(max(0.62, fd * 0.85), 2),
                            "kind": c["kind"], "next": c["next"]})
        freezes.sort(key=lambda f: f["t"])
        for f in freezes:                       # whole frames, so the export and the preview agree exactly
            f["d"] = round(max(1, round(f["d"] * fps)) / fps, 4)
    spans = freeze_spans(freezes)
    Df = D + sum(f["d"] for f in freezes)

    # 2) editorial title (the hook as a magazine cover)
    tl = None
    ty = TITLE_Y.get(layout, 470)
    behind_ok = bool(head_exact and opts.get("behind", True))
    if head_top is not None:
        if behind_ok:     # tuck the big word over the top of the head: it goes behind the speaker on export
            ty = int(max(300, min(820, head_top + 30)))
        else:             # no cut-out: keep the title clear above the head
            ty = int(max(260, min(TITLE_Y.get(layout, 470), head_top - 170)))
    look = rnd.choice(TITLE_LOOKS.get(vibe, ["serif"]))
    if opts.get("titles", True) and title and LATIN.match(title) and len(title.split()) <= 12:
        t1 = round(min(3.0, max(2.0, Df * 0.28), (spans[0][0] - 0.1) if spans else 99), 2)
        if t1 >= 1.6:
            els = _title_els(title, look, ty, 0.1, t1, kw, TAGS.get(vibe, "STORY"), watermark, accent, vibe, rnd)
            if els:
                tl = {"t0": 0.1, "t1": t1, "look": look, "y": ty, "els": els}

    if tl:      # dragged / resized in the editor
        _move(tl["els"], place, "title")
    # 3) beat text (what lands on screen while the picture is frozen)
    beats = []
    if opts.get("titles", True):
        for f, (a, b) in zip(freezes, spans):
            els = _beat_els(f, words, a, b, kw, roman, look, ty, accent)
            if els:
                _move(els, place, "beat")
                beats.append({"t0": a, "t1": b, "els": els})

    # 4) streak transitions: every part join, every beat's snap back, the title leaving
    streaks: list = []
    if opts.get("transitions", True):
        cand: list = []          # (priority, time): beat snap-backs and part joins first, then the title leaving
        cand += [(0, b) for _a, b in spans]
        for t, _removed, join in (seams or []):
            if join and 0.4 < t < D - 0.4:
                cand.append((0, round(to_final(t, freezes), 3)))
        if tl:
            cand.append((1, tl["t1"]))
        if level == "strong":
            for t, removed, join in (seams or []):
                if not join and removed >= 0.8 and 2 < t < D - 2:
                    cand.append((2, round(to_final(t, freezes), 3)))
        for _p, x in sorted(cand):
            if 0.2 < x < Df - 0.2 and all(abs(x - y) > 1.2 for y in streaks) and len(streaks) < 14:
                streaks.append(round(x, 2))
        streaks.sort()

    # 5) texture
    tex = None
    if opts.get("textures", True):
        g, bl, leak, vig = TEXTURE.get(vibe, TEXTURE["story"])
        m = 1.25 if level == "strong" else 1.0
        tex = {"grain": round(min(1.0, g * m), 2), "bloom": round(min(0.4, bl * m), 2), "leak": leak, "vignette": vig}
    leaks = ([0.0] + [b for _a, b in spans]) if tex and tex["leak"] else []

    return {"level": level, "freezes": [{"t": f["t"], "d": f["d"], "kind": f["kind"]} for f in freezes],
            "spans": spans, "title": tl, "beats": beats, "streaks": streaks, "leaks": [round(x, 2) for x in leaks],
            "texture": tex, "behind": bool(tl and behind_ok), "blackouts": spans, "D": round(Df, 3)}


def _move(els: list, place: Optional[dict], key: str) -> None:
    """Apply the editor's drag (key_dx / key_dy, fractions of the frame) and resize (key_scale) to a text group.
    The group moves and scales as one, around its biggest element."""
    p = place or {}
    try:
        dx, dy = float(p.get(f"{key}_dx") or 0) * W, float(p.get(f"{key}_dy") or 0) * H
        sc = min(2.0, max(0.4, float(p.get(f"{key}_scale") or 1.0)))
    except (TypeError, ValueError):
        return
    if not els or (abs(dx) < 0.5 and abs(dy) < 0.5 and abs(sc - 1) < 0.01):
        return
    a = max(els, key=lambda e: e["size"])
    ax, ay = a["x"], a["y"]
    for e in els:
        e["x"] = int(round(ax + (e["x"] - ax) * sc + dx))
        e["y"] = int(round(ay + (e["y"] - ay) * sc + dy))
        e["size"] = max(12, int(round(e["size"] * sc)))
        if e.get("wipe"):
            e["wipe"] = [int(round(ax + (w - ax) * sc + dx)) for w in e["wipe"]]


def _el(text, font, size, x, y, an, t0, t1, anim, color="#FFFFFF", **kw) -> dict:
    return {"text": text, "font": font, "size": int(size), "x": int(x), "y": int(y), "an": an,
            "t0": round(t0, 3), "t1": round(t1, 3), "anim": anim, "color": color, **kw}


def _fit(font: str, text: str, width: float, max_size: float, spacing: float = 0.0) -> int:
    unit = fonts.text_width(font, text, 1.0) + spacing * max(0, len(text) - 1) / 100.0
    return int(max(60, min(max_size, width / max(unit, 1e-3))))


def _title_case(w: str) -> str:
    return w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w


def _title_els(title, look, ty, t0, t1, kw, tag, watermark, accent, vibe, rnd) -> list:
    toks = [x for x in title.replace("\n", " ").split() if x]
    if not toks:
        return []
    # the big word: a strong content word, ideally near the end ("Getting up close with the FOREST")
    tail = toks[-4:] if len(toks) > 4 else toks
    bi = _best_word(tail, kw)
    if bi < 0:
        bi = _best_word(toks, kw)
        if bi < 0:
            return []
    else:
        bi += len(toks) - len(tail)
    big = re.sub(r"^[^\w]+|[^\w]+$", "", toks[bi])
    if not big:
        return []
    kicker = " ".join(toks[:bi]).strip(" ,:-")
    after = " ".join(toks[bi + 1:]).strip(" ,:-")
    if not kicker and after:
        kicker, after = after, ""
    if len(kicker.split()) > 7:
        kicker = " ".join(kicker.split()[-7:])
    sub_r = (watermark or "").strip()[:28]
    els = []
    if look == "serif":
        B = big.upper()
        S = _fit("Abril Fatface", B, 980, 320)
        els.append(_el(B, "Abril Fatface", S, 540, ty, 5, t0 + 0.12, t1, ["fade", "scale", "track"], shadow=True,
                       role="big"))
        if kicker:
            ks = min(56, _fit("Lato", kicker, 900, 56))
            els.append(_el(kicker, "Lato", ks, 540, ty - S * 0.56, 5, t0, t1, ["fade", "rise"], shadow=True))
        ly = ty + S * 0.5
        left = after or tag.title()
        els.append(_el(left[:30], "Lato", 40, 78, ly, 4, t0 + 0.45, t1, ["fade"], shadow=True))
        if sub_r or after:
            els.append(_el(sub_r or tag.title(), "Lato", 40, 1002, ly, 6, t0 + 0.55, t1, ["fade"], shadow=True))
    elif look == "thin":
        B = _title_case(big)
        S = _fit("Lato Light", B, 900, 270)
        bw = fonts.text_width("Lato Light", B, S)
        x0, x1 = 540 - bw / 2, 540 + bw / 2
        els.append(_el(B, "Lato Light", S, 540, ty, 5, t0 + 0.1, t1, ["fade", "wipe"], shadow=True, role="big",
                       wipe=[int(x0) - 10, int(x1) + 10]))
        if kicker:
            k = kicker if len(kicker.split()) <= 4 else " ".join(kicker.split()[-4:])
            ks = min(48, _fit("Lato", k, 600, 48))
            els.append(_el(k, "Lato", ks, max(60, x0 + S * 0.04), ty - S * 0.36, 1, t0, t1, ["fade", "rise"],
                           bold=True, shadow=True))
        tw = fonts.text_width("Lato", tag, 34)
        if x1 + 14 + tw < 1050:
            els.append(_el(tag, "Lato", 34, x1 + 8, ty + S * 0.25, 1, t0 + 0.5, t1, ["fade"], color=accent,
                           bold=True, shadow=True))
        else:
            els.append(_el(tag, "Lato", 34, x1, ty + S * 0.62, 6, t0 + 0.5, t1, ["fade"], color=accent, bold=True,
                           shadow=True))
        if after:
            els.append(_el(after[:34], "Lato", 42, 540, ty + S * 0.95, 5, t0 + 0.6, t1, ["fade", "rise"], shadow=True))
    else:   # glow
        B = _title_case(big)
        S = _fit("DM Serif Display", B, 940, 250)
        els.append(_el(B, "DM Serif Display", S, 540, ty, 5, t0 + 0.12, t1, ["fade", "scale"], glow="#FFFFFF",
                       role="big"))
        if kicker:
            ks = min(50, _fit("Lato", kicker, 880, 50))
            els.append(_el(kicker, "Lato", ks, 540, ty - S * 0.66, 5, t0, t1, ["fade", "rise"], color=accent,
                           bold=True, shadow=True))
        if after:
            els.append(_el(after[:34], "Lato", 46, 540, ty + S * 0.64, 5, t0 + 0.45, t1, ["fade", "rise"],
                           shadow=True))
    return els


def _beat_els(f, words, a, b, kw, roman, look, ty, accent) -> list:
    if f["kind"] == "rewind":
        k, big = ("shuru se" if roman else "let's"), "REWIND"
    else:
        na, nb = f["next"] or (0, -1)
        nxt = [w["w"] for w in words[na:nb + 1]]
        first = _norm(nxt[0]) if nxt else ""
        if first in STRONG_CUES:
            big = re.sub(r"[^\w']", "", nxt[0]).upper() + "…"
            k = "ab dekhiye" if roman else "wait for it"
        else:
            k = "ab dekhiye" if roman else "wait for it"
            big = "…"
            if nxt:
                bi = _best_word(nxt, kw)
                if bi >= 0:
                    big = re.sub(r"^[^\w]+|[^\w]+$", "", nxt[bi]).upper() + "?"
                    k = "aur phir" if roman else "and then"
    y = min(1300, max(420, ty + 120))
    font = "Abril Fatface" if look != "glow" else "DM Serif Display"
    S = _fit(font, big, 900, 230)
    d = b - a
    return [_el(k, "Lato", 46, 540, y - S * 0.6, 5, a + 0.05, b, ["fade", "track"], bold=True, color=accent,
                shadow=True),
            _el(big, font, S, 540, y, 5, a + min(0.18, d * 0.25), b, ["fade", "scale"], shadow=True,
                glow="#FFFFFF" if look == "glow" else None)]


# ------------------------------------------------------------------ ASS (titles + beat text)
def _ass_color(hex_: str, alpha: int = 0) -> str:
    h = (hex_ or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def ass(splan: dict) -> str:
    """ASS file with the editorial title and the beat text (drawn under the speaker cut-out)."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
            "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: S,Lato,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    groups = ([splan["title"]] if splan.get("title") else []) + list(splan.get("beats") or [])
    for g in groups:
        for e in g["els"]:
            lines += _ass_el(e)
    return head + "\n".join(lines) + ("\n" if lines else "")


def _ass_el(e: dict) -> list[str]:
    from .captions import esc
    t0, t1 = e["t0"], e["t1"]
    x, y, an = e["x"], e["y"], e["an"]
    anim = e.get("anim") or []
    tags = [f"\\an{an}", f"\\fn{e['font']}", f"\\fs{e['size']}", f"\\c{_ass_color(e['color'])}",
            "\\bord0", "\\shad0"]
    if e.get("bold"):
        tags.append("\\b1")
    if "rise" in anim:
        tags.append(f"\\move({x},{y + 34},{x},{y},0,380)")
    else:
        tags.append(f"\\pos({x},{y})")
    if "fade" in anim:
        tags.append(f"\\fad({220 if 'scale' not in anim else 140},{260})")
    if "scale" in anim:
        tags.append("\\fscx114\\fscy114\\t(0,460,0.6,\\fscx100\\fscy100)")
    if "track" in anim:
        sp = max(2, int(e["size"] * 0.05))
        tags.append(f"\\fsp{sp}\\t(0,800,0.5,\\fsp0)")
    if "wipe" in anim and e.get("wipe"):
        a, b = e["wipe"]
        tags.append(f"\\clip({a},0,{a + 1},1920)\\t(0,650,0.7,\\clip({a},0,{b},1920))")
    text = esc(e["text"])
    st, en = _ts(t0), _ts(t1)
    out = []
    if e.get("shadow"):
        # soft drop shadow so thin white type reads on any background
        sh = [t for t in tags if not t.startswith("\\c")] + [f"\\c{_ass_color('#000000')}", "\\alpha&H70&",
                                                              f"\\blur{max(3, e['size'] // 28)}"]
        sh = [t.replace(f"\\pos({x},{y})", f"\\pos({x + 3},{y + 5})") for t in sh]
        sh = [t.replace(f"\\move({x},{y + 34},{x},{y},0,380)", f"\\move({x + 3},{y + 39},{x + 3},{y + 5},0,380)")
              for t in sh]
        out.append(f"Dialogue: 0,{st},{en},S,,0,0,0,,{{{''.join(sh)}}}{text}")
    if e.get("glow"):
        gl = [t for t in tags if not t.startswith("\\c")] + [f"\\c{_ass_color(e['glow'])}", "\\alpha&H88&",
                                                             f"\\blur{max(8, e['size'] // 9)}", f"\\bord{max(3, e['size'] // 30)}",
                                                             f"\\3c{_ass_color(e['glow'])}"]
        out.append(f"Dialogue: 0,{st},{en},S,,0,0,0,,{{{''.join(gl)}}}{text}")
    out.append(f"Dialogue: 1,{st},{en},S,,0,0,0,,{{{''.join(tags)}}}{text}")
    return out


# ------------------------------------------------------------------ sounds
def sfx_events(splan: dict) -> list[tuple[float, str, float]]:
    """(time, role, gain) for the sound designer: riser into every beat, a hit when it snaps back, a whoosh on
    each streak, a hit when the title lands."""
    ev = []
    if splan.get("title"):
        ev.append((splan["title"]["t0"] + 0.1, "hook", 0.55))
    for a, b in splan.get("spans") or []:
        ev.append((a, "swell", 0.42))
        ev.append((b - 0.02, "impact", 0.6))
    for t in splan.get("streaks") or []:
        ev.append((max(0.0, t - 0.14), "cut", 0.4))
    return ev
