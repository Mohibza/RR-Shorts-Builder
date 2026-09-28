"""Projects: one analysed video with its clips, ready to preview instantly and export on demand.

A project is plain JSON (data_dir/projects/<id>.json). Each clip keeps everything needed to preview it live in the
UI (media file + offset, words, poster, camera path) and to export it later with the user's edits:

edits = {
  "trim":   [t0, t1],            # playback-time window to keep (seconds from the clip's start), optional
  "cut":    [word_index, ...],   # words removed from the Short (indices into clip["words"])
  "fix":    {word_index: text},  # caption spelling fixes
  "hook":   "on-screen heading",
  "style":  {caption_style, hook_style, cta_style, color_grade, motion, intro, layout, position},
  "place":  {...placement...},
  "audio":  {"music": "auto" | "none" | <path>, "music_volume": 0.12, "sfx_level": "medium"},
  "meta":   {"title", "description", "tags", "hashtags"},
}
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from .config import data_dir

PROJECTS = data_dir() / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
_lock = threading.RLock()


def _path(pid: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pid)[:80]
    return PROJECTS / f"{safe}.json"


def save(p: dict) -> dict:
    with _lock:
        p["updated"] = time.time()
        tmp = _path(p["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_path(p["id"]))
        return p


def load(pid: str) -> Optional[dict]:
    with _lock:
        try:
            return json.loads(_path(pid).read_text(encoding="utf-8"))
        except Exception:
            return None


def delete(pid: str) -> None:
    with _lock:
        _path(pid).unlink(missing_ok=True)


def list_all() -> list[dict]:
    out = []
    for f in PROJECTS.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda p: p.get("created", 0), reverse=True)
    return out


def summary(p: dict) -> dict:
    """Light version for lists (no words / tracks)."""
    return {k: v for k, v in p.items() if k != "clips"} | {
        "clips": [{k: v for k, v in c.items() if k not in ("words", "tracks", "prep")} for c in p.get("clips", [])]}


def clip(p: dict, cid: str) -> Optional[dict]:
    return next((c for c in p.get("clips", []) if c["id"] == cid), None)


def update_clip(pid: str, cid: str, **fields) -> Optional[dict]:
    with _lock:
        p = load(pid)
        if not p:
            return None
        c = clip(p, cid)
        if not c:
            return None
        c.update(fields)
        save(p)
        return c


# ---------------------------------------------------------------- edits -> what actually plays
def base_parts(c: dict) -> list[list[float]]:
    cl = c["clip"]
    segs = cl.get("segments") or []
    return [list(s) for s in segs] if segs else [[cl["start"], cl["end"]]]


def playback_parts(c: dict, edits: Optional[dict] = None) -> list[list[float]]:
    """Absolute source ranges in playback order after trim + removed words."""
    edits = edits or {}
    parts = base_parts(c)
    trim = edits.get("trim")
    if trim:
        t0, t1 = float(trim[0]), float(trim[1])
        out, acc = [], 0.0
        for a, b in parts:
            L = b - a
            s, e = max(t0, acc), min(t1, acc + L)
            if e - s > 0.05:
                out.append([a + (s - acc), a + (e - acc)])
            acc += L
        parts = out or parts
    cuts = []
    words = c.get("words") or []
    for i in sorted(set(int(x) for x in edits.get("cut") or [] if str(x).lstrip("-").isdigit())):
        if 0 <= i < len(words):
            w = words[i]
            a, b = w["s"] - 0.02, w["e"] + 0.02
            if cuts and a <= cuts[-1][1] + 0.12:     # neighbouring removed words -> one cut
                cuts[-1][1] = max(cuts[-1][1], b)
            else:
                cuts.append([a, b])
    for ca, cb in cuts:
        nxt = []
        for a, b in parts:
            if cb <= a or ca >= b:
                nxt.append([a, b])
                continue
            if ca - a > 0.08:
                nxt.append([a, ca])
            if b - cb > 0.08:
                nxt.append([cb, b])
        parts = nxt
    return [p for p in parts if p[1] - p[0] > 0.05] or base_parts(c)


def edited_words(c: dict, edits: Optional[dict] = None) -> list[dict]:
    """Clip words with spelling fixes applied and removed words dropped (absolute times)."""
    edits = edits or {}
    cut = set(int(x) for x in edits.get("cut") or [] if str(x).lstrip("-").isdigit())
    fix = {int(k): v for k, v in (edits.get("fix") or {}).items() if str(k).lstrip("-").isdigit()}
    out = []
    for i, w in enumerate(c.get("words") or []):
        if i in cut:
            continue
        t = str(fix.get(i, w["w"])).strip()
        if t:
            out.append({"w": t, "s": w["s"], "e": w["e"], "p": 1.0})
    return out
