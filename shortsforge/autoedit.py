"""Auto Edit: turns a raw recording into a finished edit in one pass.

It listens (where is someone talking?), looks at what the hands did (clicks, typing, mouse movement logged by the
recorder) and decides:

* dead air with nothing happening      -> cut
* long stretches of typing / waiting   -> played faster
* "um", "uh" and obvious retakes       -> cut
* clicks and typing                    -> smooth zoom to that spot
* speech                               -> captions
* markers                              -> chapters

Every decision becomes an ordinary clip, zoom block or caption on the timeline, so anything can be changed or
removed afterwards. The whole timeline is cut together (screen, webcam, PC sound stay in sync); music tracks that
duck under the voice are left alone.
"""
from __future__ import annotations

import copy
import re
import threading
from typing import Callable, Optional

from . import vfx
from .pacing import is_filler

DEFAULTS = {"cuts": True, "fillers": True, "retakes": True, "speedup": True, "zoom": True, "zoom_level": 1.7,
            "captions": True, "chapters": True, "audio": True, "cursor": True, "min_gap": 0.9}


def _merge(iv: list[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        if b <= a:
            continue
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _speech_from_energy(wav: str) -> list[tuple[float, float]]:
    import numpy as np
    from .transcriber import energy_curve
    e = energy_curve(wav, 0.05)
    if len(e) < 10:
        return []
    noise, peak = float(np.percentile(e, 15)), float(np.percentile(e, 97))
    if peak - noise < 8:                              # flat: all noise or all silence
        return []
    on = e > noise + 0.35 * (peak - noise)
    iv, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            iv.append((start * 0.05, i * 0.05))
            start = None
    if start is not None:
        iv.append((start * 0.05, len(on) * 0.05))
    return [(a, b) for a, b in _merge(iv, 0.3) if b - a >= 0.15]


class TimeMap:
    """Old timeline time -> new timeline time after cuts and speed-ups."""

    def __init__(self, actions: list[dict]):
        self.pts = [(0.0, 0.0)]
        t_old, t_new = 0.0, 0.0
        for a in sorted(actions, key=lambda x: x["t0"]):
            if a["t0"] > t_old:
                t_new += a["t0"] - t_old
                self.pts.append((a["t0"], t_new))
            if a["kind"] == "speed":
                t_new += (a["t1"] - a["t0"]) / a["k"]
            t_old = a["t1"]
            self.pts.append((t_old, t_new))

    def __call__(self, t: float) -> float:
        p = self.pts
        if t >= p[-1][0]:
            return p[-1][1] + (t - p[-1][0])
        for (a0, b0), (a1, b1) in zip(p, p[1:]):
            if a0 <= t <= a1:
                return b0 + (b1 - b0) * ((t - a0) / (a1 - a0) if a1 > a0 else 0)
        return t


def apply_actions(p: dict, actions: list[dict], fps: int) -> dict:
    """Cut / speed up the whole timeline (every track except ducking music), keeping everything in sync."""
    if not actions:
        return p
    tm = TimeMap(actions)
    skip = {t["id"] for t in p["tracks"] if t.get("duck")}
    bounds = sorted({a["t0"] for a in actions} | {a["t1"] for a in actions})
    out = []
    for it in p["items"]:
        if it["track"] in skip:
            out.append(it)
            continue
        sp = max(0.05, float(it.get("speed") or 1))
        a, b = it["start"], it["start"] + (it["out"] - it["in"]) / sp
        cuts = [a] + [x for x in bounds if a + 1e-4 < x < b - 1e-4] + [b]
        for i, (c0, c1) in enumerate(zip(cuts, cuts[1:])):
            mid = (c0 + c1) / 2
            act = next((x for x in actions if x["t0"] <= mid < x["t1"]), None)
            if act and act["kind"] == "cut":
                continue
            n = copy.deepcopy(it)
            n["id"] = it["id"] if i == 0 and not act else _nid()
            n["in"] = round(it["in"] + (c0 - a) * sp, 4)
            n["out"] = round(it["in"] + (c1 - a) * sp, 4)
            n["start"] = round(tm(c0) * fps) / fps
            if c0 > a + 1e-4:
                n["fade_in"] = 0.0
                n.pop("enter", None)
            if c1 < b - 1e-4:
                n["fade_out"] = 0.0
                n.pop("exit", None)
                n.pop("tail", None)
            if act and act["kind"] == "speed":
                n["speed"] = round(sp * act["k"], 3)
                n["muted"] = True                       # sped-up typing / waiting has no useful sound
                n["auto"] = "speed"
            if n["out"] - n["in"] > 1.0 / fps:
                out.append(n)
    p["items"] = out
    els = []
    for e in p.get("els") or []:
        s, t = tm(e["start"]), tm(e["start"] + e.get("dur", 0))
        if t - s >= 0.12:
            els.append({**e, "start": round(s, 3), "dur": round(t - s, 3)})
    p["els"] = els
    p["markers"] = sorted({round(tm(m), 2) for m in p.get("markers") or []})
    for c in p.get("chapters") or []:
        c["t"] = round(tm(c["t"]), 2)
    return p


def _nid() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def _captions(words: list[dict], maxchars: int = 44, maxdur: float = 3.6) -> list[tuple[float, float, str]]:
    out, cur = [], []
    for w in words:
        txt = " ".join(x["w"] for x in cur)
        if cur and (len(txt) + 1 + len(w["w"]) > maxchars or w["e"] - cur[0]["s"] > maxdur or w["s"] - cur[-1]["e"] > 0.6
                    or (re.search(r"[.!?]$", cur[-1]["w"]) and len(txt) > 14)):
            out.append((cur[0]["s"], cur[-1]["e"], txt))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0]["s"], cur[-1]["e"], " ".join(x["w"] for x in cur)))
    res = []
    for i, (a, b, txt) in enumerate(out):                    # hold each line until the next one (max +0.8 s)
        nxt = out[i + 1][0] if i + 1 < len(out) else b + 0.8
        res.append((a, max(b, min(nxt, b + 0.8)), txt.strip()))
    return res


def _zoom_blocks(clicks: list, keys: list, moves: list, z: float) -> list[dict]:
    """Zoom blocks (timeline time) from where the clicking and typing happened."""
    pts = [(t, x, y) for t, x, y in clicks]
    # typing with no click nearby: zoom to where the cursor was resting
    last = -10.0
    for t in keys:
        if t - last > 2.5 and not any(abs(t - c[0]) < 2.0 for c in pts):
            pos = next(((m[1], m[2]) for m in reversed(moves) if m[0] <= t), None)
            if pos:
                pts.append((t, pos[0], pos[1]))
        last = t
    pts.sort()
    blocks: list[list] = []
    for t, x, y in pts:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        b = blocks[-1] if blocks else None
        if b and t - b[-1][0] <= 4.0 and abs(x - b[0][1]) < 0.22 and abs(y - b[0][2]) < 0.26:
            b.append((t, x, y))
        else:
            blocks.append([(t, x, y)])
    out, prev_end = [], 0.0
    lo, hi = 0.5 / z, 1 - 0.5 / z
    ks = sorted(keys)
    for i, b in enumerate(blocks):
        start = max(prev_end, b[0][0] - 0.7)
        # hold the zoom for as long as the typing goes on: follow the key presses until they stop for 2.5 s
        last = b[-1][0]
        for k in ks:
            if k > last + 2.5:
                break
            if k > last:
                last = k
        end = last + 1.5
        if i + 1 < len(blocks):                               # but hand over to the next spot when a click goes elsewhere
            end = min(end, max(b[-1][0] + 1.0, blocks[i + 1][0][0] - 0.7))
        if end - start < 1.2:
            continue
        cx = min(hi, max(lo, sum(q[1] for q in b) / len(b)))
        cy = min(hi, max(lo, sum(q[2] for q in b) / len(b)))
        out.append({"id": _nid(), "kind": "zoom", "start": round(start, 2), "dur": round(end - start, 2), "cx": round(cx, 3),
                    "cy": round(cy, 3), "z": z, "ease": 0.55, "auto": True})
        prev_end = end
    return out


def run(p: dict, opts: dict, progress: Callable[[str, float], None], cancel: Optional[threading.Event] = None,
        settings=None) -> tuple[dict, dict]:
    """Returns (new project, summary). `p` is not changed."""
    o = {**DEFAULTS, **(opts or {})}
    p = copy.deepcopy(p)
    fps = int(p.get("fps") or 30)
    media = {m["id"]: m for m in p["media"]}
    tracks = {t["id"]: t for t in p["tracks"]}
    cur = p.get("cursor") or {}
    # the main take: the screen recording if there is one, else the first clip that has sound, else the first clip
    main_media = cur.get("media") if cur.get("media") in media else None
    cand = sorted((it for it in p["items"] if tracks.get(it["track"]) and not tracks[it["track"]].get("duck")),
                  key=lambda it: it["start"])
    if not main_media:
        pick = next((it for it in cand if media[it["media"]].get("has_audio")), cand[0] if cand else None)
        if not pick:
            raise ValueError("Add a video to the timeline first.")
        main_media = pick["media"]
    main = [it for it in cand if it["media"] == main_media]
    if not main:
        raise ValueError("The recording isn't on the timeline any more.")
    m = media[main_media]
    before = max(it["start"] + (it["out"] - it["in"]) / max(0.05, it.get("speed") or 1) for it in p["items"])
    p["els"] = [e for e in p.get("els") or [] if not e.get("auto")]

    def to_tl(ts: float) -> Optional[float]:
        for it in main:
            if it["in"] - 1e-3 <= ts <= it["out"] + 1e-3:
                return it["start"] + (ts - it["in"]) / max(0.05, it.get("speed") or 1)
        return None

    # ---- 1. listen
    words: list[dict] = []
    speech_src: list[tuple[float, float]] = []
    has_voice = False
    if m.get("has_audio"):
        from .transcriber import extract_audio, transcribe
        progress("Listening to the recording", 0.03)
        wav = extract_audio(m["path"], "edit_" + m["key"], m["duration"], cancel)
        if o["captions"] or o["fillers"] or o["retakes"]:
            from .config import Settings
            from .pipeline import resolve_model
            s = settings or Settings.load()
            try:
                tr = transcribe(wav, "edit_" + m["key"], m["duration"], resolve_model(s), s.language,
                                (True if s.use_gpu else None),
                                lambda f, d: progress("Writing down what is said", 0.08 + 0.62 * f), cancel, None,
                                on_stage=lambda st, f, d: progress(st, 0.06))
                for seg in tr.get("segments", []):
                    for w in seg.get("words") or []:
                        if w.get("w", "").strip():
                            words.append({"w": w["w"].strip(), "s": float(w["s"]), "e": float(w["e"])})
                p["transcript_lang"] = tr.get("language", "")
            except Exception as e:                                # no speech model: fall back to loudness
                if type(e).__name__ == "Cancelled":
                    raise
                progress("Speech model unavailable, using loudness instead", 0.6)
        if words:
            speech_src = _merge([(w["s"] - 0.1, w["e"] + 0.15) for w in words if not (o["fillers"] and is_filler(w["w"]))], 0.3)
        else:
            speech_src = _speech_from_energy(wav)
        has_voice = bool(speech_src)
    progress("Finding cuts, zooms and captions", 0.74)

    # ---- 2. what the hands did (video time of the main take)
    ev = vfx.load_events(cur["events"], float(cur.get("offset") or 0)) if cur.get("events") else {"clicks": [], "moves": [], "keys": []}
    clicks = [(to_tl(c[0]), c[1], c[2]) for c in ev["clicks"]]
    clicks = [c for c in clicks if c[0] is not None]
    keys = [t for t in (to_tl(k) for k in ev["keys"]) if t is not None]
    moves = [(to_tl(mv[0]), mv[1], mv[2]) for mv in ev["moves"]]
    moves = [mv for mv in moves if mv[0] is not None]
    busy = _merge([(c[0] - 0.8, c[0] + 1.2) for c in clicks] + [(k - 0.6, k + 1.0) for k in keys]
                  + [(mv[0] - 0.2, mv[0] + 0.5) for mv in moves], 0.2)
    typing = [iv for iv in _merge([(k, k + 0.01) for k in keys], 1.6) if iv[1] - iv[0] >= 3.5]

    def is_busy(a: float, b: float) -> bool:
        return any(x < b and y > a for x, y in busy)

    # ---- 3. decide: cut, speed up or keep (timeline time)
    actions: list[dict] = []
    snap = lambda t: round(t * fps) / fps                       # noqa: E731
    speech = _merge([(to_tl(a), to_tl(b)) for a, b in speech_src if to_tl(a) is not None and to_tl(b) is not None], 0.0)
    have_events = bool(ev["clicks"] or ev["keys"] or ev["moves"])
    for it in main:
        a = it["start"]
        b = a + (it["out"] - it["in"]) / max(0.05, it.get("speed") or 1)
        if has_voice:
            talk = [(max(a, x), min(b, y)) for x, y in speech if y > a and x < b]
            gaps, t = [], a
            for x, y in talk:
                if x - t >= o["min_gap"]:
                    gaps.append((t, x))
                t = max(t, y)
            if b - t >= o["min_gap"]:
                gaps.append((t, b))
        elif have_events:
            gaps = [(a, b)]
        else:
            gaps = []
        for g0, g1 in gaps:
            if not has_voice:                                   # silent screen recording: work from activity only
                t = g0
                for x, y in [iv for iv in busy if iv[1] > g0 and iv[0] < g1] + [(g1, g1)]:
                    if o["cuts"] and x - t >= 2.5:
                        actions.append({"kind": "cut", "t0": snap(t + 0.3), "t1": snap(x - 0.3)})
                    t = max(t, y)
                continue
            lead, tail = (0.12 if g0 > a else 0.0), (0.14 if g1 < b else 0.0)
            c0, c1 = g0 + lead, g1 - tail
            if c1 - c0 < 0.4:
                continue
            if have_events and is_busy(c0, c1):
                if o["speedup"] and c1 - c0 >= 3.0:
                    k = 2.0 if c1 - c0 < 6 else 3.0 if c1 - c0 < 15 else 4.0
                    actions.append({"kind": "speed", "t0": snap(c0 + 0.2), "t1": snap(c1 - 0.2), "k": k})
            elif o["cuts"]:
                actions.append({"kind": "cut", "t0": snap(c0), "t1": snap(c1)})
    if o["speedup"] and not has_voice:
        for x, y in typing:
            if not any(act["t0"] < y and act["t1"] > x for act in actions):
                actions.append({"kind": "speed", "t0": snap(x + 0.5), "t1": snap(y - 0.3), "k": 2.5})
    # fillers and retakes (need the words)
    if words and o["fillers"]:
        for w in words:
            if is_filler(w["w"]):
                x, y = to_tl(w["s"] - 0.03), to_tl(w["e"] + 0.03)
                if x is not None and y is not None and y - x > 0.08:
                    actions.append({"kind": "cut", "t0": snap(x), "t1": snap(y)})
    if words and o["retakes"]:
        sents, cur_s = [], []
        for w in words:
            cur_s.append(w)
            if re.search(r"[.!?]$", w["w"]):
                sents.append(cur_s)
                cur_s = []
        if cur_s:
            sents.append(cur_s)
        key3 = lambda s_: [re.sub(r"\W", "", x["w"].lower()) for x in s_[:3]]          # noqa: E731
        for s1, s2 in zip(sents, sents[1:]):
            if 3 <= len(s1) <= 12 and len(s2) >= len(s1) and key3(s1) == key3(s2) and s2[0]["s"] - s1[-1]["e"] < 10:
                x, y = to_tl(s1[0]["s"] - 0.05), to_tl(s2[0]["s"] - 0.05)
                if x is not None and y is not None:
                    actions.append({"kind": "cut", "t0": snap(x), "t1": snap(y), "why": "retake"})
    # tidy: no overlaps (cuts win), nothing tiny
    actions = [x for x in actions if x["t1"] - x["t0"] >= 2.0 / fps]
    actions.sort(key=lambda x: (x["t0"], x["kind"] != "cut"))
    clean: list[dict] = []
    for x in actions:
        if clean and x["t0"] < clean[-1]["t1"]:
            if x["kind"] == "cut" and clean[-1]["kind"] == "cut":
                clean[-1]["t1"] = max(clean[-1]["t1"], x["t1"])
                continue
            x = {**x, "t0": clean[-1]["t1"]}
            if x["t1"] - x["t0"] < 0.3:
                continue
        clean.append(x)
    actions = clean

    # ---- 4. things that sit on top (still in the old timing; step 5 moves them along with the cuts)
    summary = {"cuts": sum(1 for x in actions if x["kind"] == "cut"), "speedups": sum(1 for x in actions if x["kind"] == "speed"),
               "zooms": 0, "captions": 0, "chapters": 0, "voice": has_voice, "events": have_events}
    if o["zoom"] and have_events:
        zs = _zoom_blocks(clicks, keys, moves, float(o["zoom_level"]))
        dead = [(x["t0"], x["t1"]) for x in actions if x["kind"] == "cut"]
        zs = [z for z in zs if not any(a <= z["start"] + 0.7 and z["start"] + z["dur"] - 1.7 <= b for a, b in dead)]
        p["els"] += zs
        summary["zooms"] = len(zs)
    if o["captions"] and words:
        real = [w for w in words if not is_filler(w["w"])]
        for a, b, txt in _captions(real):
            x, y = to_tl(a), to_tl(b)
            if x is not None and y is not None and y - x > 0.2:
                p["els"].append({"id": _nid(), "kind": "caption", "start": round(x, 3), "dur": round(y - x, 3), "text": txt, "auto": True})
                summary["captions"] += 1
        if not p.get("captions"):
            from .veditor import CAPTION_STYLE
            p["captions"] = dict(CAPTION_STYLE)
    if o["chapters"]:
        marks = sorted(set([0.0] + [float(x) for x in p.get("markers") or []]))
        if len(marks) > 1:
            chs = []
            for i, t in enumerate(marks):
                title = "Intro" if i == 0 else f"Part {i + 1}"
                near = [w["w"] for w in words if to_tl(w["s"]) is not None and t <= to_tl(w["s"]) <= t + 4][:6]
                if len(near) >= 3:
                    title = re.sub(r"[.,!?]+$", "", " ".join(near)).capitalize()
                chs.append({"t": t, "title": title})
            p["chapters"] = chs
            summary["chapters"] = len(chs)
    if o["audio"]:
        for it in p["items"]:
            if it["media"] == main_media and m.get("has_audio"):
                it["afx"] = {"denoise": True, "level": True}
    if o["cursor"] and cur.get("events"):
        p["cursor"] = {**cur, "ripple": True}

    # ---- 5. cut it
    progress("Cutting the timeline", 0.92)
    p = apply_actions(p, actions, fps)
    p["els"] = [e for e in p["els"] if not (e.get("auto") and e["kind"] == "zoom" and e["dur"] < 1.2)]
    summary["zooms"] = sum(1 for e in p["els"] if e.get("auto") and e["kind"] == "zoom")
    after = max([it["start"] + (it["out"] - it["in"]) / max(0.05, it.get("speed") or 1) for it in p["items"]] or [0.0])
    summary.update(before=round(before, 1), after=round(after, 1), saved=round(before - after, 1))
    p["auto"] = summary
    progress("Done", 1.0)
    return p, summary
