"""Video editor (long-form): projects, media, timeline -> FFmpeg export.

A project is plain JSON (edits/<id>/project.json):

    {id, name, created, updated, width, height, fps, bg,
     media:  [{id, path, name, kind: video|audio|image, duration, width, height, fps, has_audio, key}],
     tracks: [{id, kind: video|audio, name, muted, hidden}],        # video tracks: later in the list = on top
     items:  [{id, track, media, start, in, out, speed, volume, muted, fade_in, fade_out,
               x, y, scale, rot, opacity, crop: [l, t, r, b]}]}

`start` is where the item sits on the timeline, `in`/`out` are seconds inside the source, so an item lasts
(out - in) / speed. x/y are the item's centre as a fraction of the canvas; scale 1 = fitted inside the canvas.

Everything heavy that helps the screen (filmstrips, waveforms, posters, preview copies of videos the built-in
player can't decode) is cached per source file under edits/_cache, never inside the project.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .config import data_dir
from . import fonts, vfx
from .utils import (Cancelled, encoder_args, ffmpeg_cwd, filter_path, find_ffmpeg, pick_encoder, probe, run, run_ffmpeg,
                    safe_name)

EDIT_DIR = data_dir() / "edits"
CACHE = EDIT_DIR / "_cache"
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ts"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac", ".opus", ".wma"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
WEB_VIDEO = {".mp4", ".m4v", ".mov", ".webm"}
WEB_CODECS = {"h264", "vp8", "vp9", "av1"}
SIZES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160), "720p": (1280, 720),
         "vertical": (1080, 1920), "square": (1080, 1080)}
_LOCK = threading.RLock()


def _id() -> str:
    return uuid.uuid4().hex[:8]


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


# ================================================================ media
def kind_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    return "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else "image" if ext in IMAGE_EXT else ""


def cache_key(path: str) -> str:
    p = Path(path)
    try:
        st = p.stat()
        sig = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = str(p)
    return hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest()[:16]


def describe(path: str) -> dict:
    """A media entry for a file (probed once, when it is added)."""
    p = Path(path)
    kind = kind_of(path)
    if not kind or not p.is_file():
        raise ValueError(f"This file type can't be used in the editor: {p.name}")
    m = {"id": _id(), "path": str(p), "name": p.name, "kind": kind, "duration": 0.0, "width": 0, "height": 0,
         "fps": 30.0, "has_audio": kind == "audio", "key": cache_key(path), "vcodec": ""}
    if kind == "image":
        try:
            import cv2
            im = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            m["height"], m["width"] = int(im.shape[0]), int(im.shape[1])
        except Exception:
            m["width"], m["height"] = 1920, 1080
        m["duration"] = 5.0
    else:
        info = probe(str(p))
        m.update(duration=round(float(info.get("duration") or 0), 3), width=int(info.get("width") or 0),
                 height=int(info.get("height") or 0), fps=round(float(info.get("fps") or 30), 3),
                 has_audio=bool(info.get("has_audio")), vcodec=str(info.get("vcodec") or ""))
        if kind == "video" and (not m["width"] or not m["height"]):
            m["kind"] = "audio"                 # a "video" file that only holds sound
        if m["duration"] <= 0:
            raise ValueError(f"This file can't be read: {p.name}")
    return m


def needs_proxy(m: dict) -> bool:
    """Can the built-in player show this file directly? If not, a light preview copy is made (export always
    uses the original)."""
    if m["kind"] != "video":
        return False
    ext = Path(m["path"]).suffix.lower()
    if ext not in WEB_VIDEO:
        return True
    vc = (m.get("vcodec") or "").lower()
    return bool(vc) and vc not in WEB_CODECS


_JOBS: dict[str, threading.Thread] = {}


def assets(m: dict, emit: Optional[Callable] = None) -> dict:
    """What exists so far for this media: {poster, strip, strip_n, wave, proxy, ready}. Starts making the missing
    parts in the background (once)."""
    d = CACHE / m["key"]
    meta = _read(d / "meta.json") or {}
    out = {"key": m["key"], "poster": "", "strip": "", "strip_n": int(meta.get("strip_n") or 0), "wave": "",
           "proxy": "", "need_proxy": needs_proxy(m), "ready": bool(meta.get("ready")), "error": meta.get("error") or ""}
    for k, f in (("poster", "poster.jpg"), ("strip", "strip.jpg"), ("wave", "wave.png"), ("proxy", "proxy.mp4")):
        if (d / f).exists() and (k != "proxy" or meta.get("proxy_done")):
            out[k] = str(d / f)
    if not out["ready"]:
        with _LOCK:
            t = _JOBS.get(m["key"])
            if not t or not t.is_alive():
                t = threading.Thread(target=_make_assets, args=(dict(m), emit), daemon=True)
                _JOBS[m["key"]] = t
                t.start()
    return out


def _make_assets(m: dict, emit: Optional[Callable]) -> None:
    d = CACHE / m["key"]
    d.mkdir(parents=True, exist_ok=True)
    meta = _read(d / "meta.json") or {}
    ff = find_ffmpeg()

    def tell():
        _write(d / "meta.json", meta)
        if emit:
            try:
                emit("edit", {"type": "media", "key": m["key"]})
            except Exception:
                pass
    try:
        if m["kind"] in ("video", "image") and not (d / "strip.jpg").exists():
            n = _strip(m, d)
            meta["strip_n"] = n
            tell()
        if m.get("has_audio") and not (d / "wave.png").exists():
            run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", m["path"], "-filter_complex",
                 "aformat=channel_layouts=mono,showwavespic=s=3000x120:colors=white:scale=sqrt", "-frames:v", "1",
                 str(d / "wave.png")], timeout=900)
            tell()
        if needs_proxy(m) and not meta.get("proxy_done"):
            tmp = d / "proxy.part.mp4"
            r = run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", m["path"], "-vf", "scale=-2:'min(720,ih)'",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-g", "30", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)], timeout=6 * 3600)
            if r.returncode == 0 and tmp.exists():
                tmp.replace(d / "proxy.mp4")
                meta["proxy_done"] = True
            else:
                meta["error"] = "A preview copy of this video couldn't be made. It can still be exported."
    except Exception as e:
        meta["error"] = str(e).split("\n")[0][:200]
    meta["ready"] = True
    tell()


def _strip(m: dict, d: Path) -> int:
    """Filmstrip sprite (frames side by side, 64 px high) + a poster. Returns how many frames it holds."""
    import cv2
    import numpy as np
    H = 64
    if m["kind"] == "image":
        im = cv2.imread(m["path"], cv2.IMREAD_COLOR)
        if im is None:
            return 0
        w = max(8, int(im.shape[1] * H / im.shape[0]))
        cv2.imwrite(str(d / "strip.jpg"), cv2.resize(im, (w, H), interpolation=cv2.INTER_AREA))
        ph = min(360, im.shape[0])
        cv2.imwrite(str(d / "poster.jpg"), cv2.resize(im, (max(8, int(im.shape[1] * ph / im.shape[0])), ph),
                                                      interpolation=cv2.INTER_AREA))
        return 1
    dur = float(m["duration"])
    n = int(max(8, min(60, dur / 2)))
    cap = cv2.VideoCapture(m["path"])
    tiles, w = [], 0
    try:
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_MSEC, (i + 0.5) / n * dur * 1000)
            ok, fr = cap.read()
            if not ok or fr is None:
                if tiles:
                    tiles.append(tiles[-1])
                continue
            if not w:
                w = max(8, int(fr.shape[1] * H / fr.shape[0]))
                ph = min(360, fr.shape[0])
            if i == min(n - 1, max(0, n // 10)) or not (d / "poster.jpg").exists():
                cv2.imwrite(str(d / "poster.jpg"), cv2.resize(fr, (max(8, int(fr.shape[1] * ph / fr.shape[0])), ph),
                                                              interpolation=cv2.INTER_AREA))
            tiles.append(cv2.resize(fr, (w, H), interpolation=cv2.INTER_AREA))
    finally:
        cap.release()
    if not tiles:
        return 0
    cv2.imwrite(str(d / "strip.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return len(tiles)


# ================================================================ projects
def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _pfile(pid: str) -> Path:
    return EDIT_DIR / Path(pid).name / "project.json"


def new_item(m: dict, track: str, start: float = 0.0, **over) -> dict:
    it = {"id": _id(), "track": track, "media": m["id"], "start": round(start, 3), "in": 0.0,
          "out": round(float(m["duration"]), 3), "speed": 1.0, "volume": 1.0, "muted": False, "fade_in": 0.0,
          "fade_out": 0.0, "x": 0.5, "y": 0.5, "scale": 1.0, "rot": 0.0, "opacity": 1.0, "crop": [0, 0, 0, 0]}
    it.update(over)
    return it


def create(name: str = "", paths: Optional[list[str]] = None, recording: Optional[dict] = None) -> dict:
    """A new project. With files: they are placed one after another. With a recording: screen, webcam (as a
    small picture in the corner) and PC sound each get their own track, lined up."""
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + _id()[:4]
    p = {"id": pid, "name": name or "Untitled project", "created": time.time(), "updated": time.time(),
         "width": 1920, "height": 1080, "fps": 30, "bg": "#000000", "media": [], "items": [], "els": [],
         "captions": dict(CAPTION_STYLE),
         "tracks": [{"id": "v1", "kind": "video", "name": "Video 1", "muted": False, "hidden": False},
                    {"id": "v2", "kind": "video", "name": "Video 2", "muted": False, "hidden": False},
                    {"id": "a1", "kind": "audio", "name": "Audio 1", "muted": False, "hidden": False},
                    {"id": "a2", "kind": "audio", "name": "Audio 2", "muted": False, "hidden": False}]}
    if recording:
        scr = describe(recording["video"])
        p["media"].append(scr)
        p["width"], p["height"] = _even(scr["width"] or 1920), _even(scr["height"] or 1080)
        p["fps"] = 60 if scr["fps"] > 45 else 30
        p["name"] = name or recording.get("name") or "Screen recording"
        p["tracks"][0]["name"] = "Screen"
        p["items"].append(new_item(scr, "v1"))
        if recording.get("webcam") and Path(recording["webcam"]).is_file():
            cam = describe(recording["webcam"])
            p["media"].append(cam)
            p["tracks"][1]["name"] = "Webcam"
            p["tracks"][1]["pin"] = True               # the webcam stays in its corner when the screen zooms
            p["items"].append(new_item(cam, "v2", scale=0.24, x=0.865, y=0.84, muted=True))
        if recording.get("system") and Path(recording["system"]).is_file():
            snd = describe(recording["system"])
            p["media"].append(snd)
            p["tracks"][2]["name"] = "PC sound"
            p["items"].append(new_item(snd, "a1"))
        p["recording"] = recording.get("id") or ""
        p["markers"] = list(recording.get("markers") or [])
        p["events"] = recording.get("events") or ""
        if p["events"]:
            p["cursor"] = {"media": scr["id"], "events": p["events"], "ripple": True, "ripple_color": "#FFD400",
                           "highlight": False, "highlight_color": "#FFD400", "spotlight": False, "size": 1.0,
                           "offset": 0.0 if recording.get("events_synced") else 1.5}
    else:
        t, first = {"v1": 0.0, "a1": 0.0}, True
        for path in paths or []:
            m = describe(path)
            p["media"].append(m)
            tr = "a1" if m["kind"] == "audio" else "v1"
            p["items"].append(new_item(m, tr, t[tr]))
            t[tr] += m["duration"]
            if first and m["kind"] == "video":
                first = False
                p["width"], p["height"] = _even(m["width"] or 1920), _even(m["height"] or 1080)
                p["fps"] = 60 if m["fps"] > 45 else 30
                if not name:
                    p["name"] = Path(path).stem[:60]
    save(p)
    return p


def load(pid: str) -> Optional[dict]:
    p = _read(_pfile(pid))
    if p is not None:                      # projects made before effects existed
        p.setdefault("els", [])
        p.setdefault("captions", dict(CAPTION_STYLE))
    return p


def save(p: dict) -> dict:
    p["updated"] = time.time()
    with _LOCK:
        _write(_pfile(p["id"]), p)
        _ALLOWED.clear()
    return p


def add_media(pid: str, paths: list[str]) -> list[dict]:
    p = load(pid)
    if p is None:
        raise ValueError("Project not found.")
    have = {m["path"]: m for m in p["media"]}
    out = []
    for path in paths:
        m = have.get(str(Path(path))) or describe(path)
        if m["path"] not in have:
            p["media"].append(m)
            have[m["path"]] = m
        out.append(m)
    save(p)
    return out


def duration(p: dict) -> float:
    return max([it["start"] + (it["out"] - it["in"]) / max(0.01, it.get("speed") or 1) for it in p["items"]] or [0.0])


def listing() -> list[dict]:
    out = []
    if not EDIT_DIR.exists():
        return out
    for d in EDIT_DIR.iterdir():
        if d.name.startswith("_") or not d.is_dir():
            continue
        p = _read(d / "project.json")
        if not p:
            continue
        first = next((m for m in p.get("media", []) if m["kind"] in ("video", "image")), None)
        poster = ""
        if first and (CACHE / first["key"] / "poster.jpg").exists():
            poster = str(CACHE / first["key"] / "poster.jpg")
        out.append({"id": p["id"], "name": p.get("name") or "", "updated": p.get("updated") or 0,
                    "duration": round(duration(p), 2), "width": p.get("width"), "height": p.get("height"),
                    "poster": poster, "items": len(p.get("items", [])), "recording": p.get("recording") or ""})
    out.sort(key=lambda x: -x["updated"])
    return out


def delete(pid: str) -> None:
    d = EDIT_DIR / Path(pid).name
    if d.is_dir() and d.parent == EDIT_DIR and not d.name.startswith("_"):
        shutil.rmtree(d, ignore_errors=True)
        _ALLOWED.clear()


_ALLOWED: set = set()


def is_media(path: Path) -> bool:
    """Files the user put into an editor project may be shown by the player, wherever they are on the PC."""
    with _LOCK:
        if not _ALLOWED:
            _ALLOWED.add("")
            if EDIT_DIR.exists():
                for d in EDIT_DIR.iterdir():
                    p = _read(d / "project.json") if d.is_dir() and not d.name.startswith("_") else None
                    for m in (p or {}).get("media", []):
                        try:
                            _ALLOWED.add(str(Path(m["path"]).resolve()))
                        except OSError:
                            pass
        return str(path) in _ALLOWED


# ================================================================ export
def _atempo(speed: float) -> list[str]:
    out, s = [], float(speed)
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-3:
        out.append(f"atempo={s:.5f}")
    return out


CAPTION_STYLE = {"font": "Poppins", "size": 0.05, "color": "#FFFFFF", "bold": True, "box": True, "box_color": "#000000",
                 "box_alpha": 0.62, "box_pad": 12, "stroke": 0, "shadow": 0, "y": 0.88, "upper": False,
                 "anim_in": {"type": "fade", "dur": 0.12}}
_PLAIN = ("x", "y", "scale", "rot", "opacity", "crop", "fx", "volume", "muted", "afx")


def _plain(it: dict) -> bool:
    return (abs(float(it.get("speed") or 1) - 1) < 1e-6 and not it.get("fade_in") and not it.get("fade_out")
            and not (it.get("enter") or {}).get("type") and not (it.get("exit") or {}).get("type")
            and not it.get("tail"))


def _units(items: list[dict], fps: int) -> list[list[dict]]:
    """Group clips that were cut out of one take (same file, same look, back to back on the timeline) so the file
    is decoded once instead of once per piece. A hundred jump cuts stay as fast as one clip."""
    out: list[list[dict]] = []
    for it in items:
        prev = out[-1][-1] if out else None
        if (prev is not None and _plain(it) and _plain(prev) and prev["track"] == it["track"]
                and prev["media"] == it["media"] and it["in"] >= prev["out"] - 0.5 / fps
                and abs(it["start"] - (prev["start"] + prev["out"] - prev["in"])) < 1.5 / fps
                and all(prev.get(k) == it.get(k) for k in _PLAIN)):
            out[-1].append(it)
        else:
            out.append([it])
    return out


def _cursor(p: dict) -> tuple[dict, dict]:
    cur = p.get("cursor") or {}
    if not cur.get("events") or not (cur.get("ripple", True) or cur.get("highlight") or cur.get("spotlight")):
        return {}, {}
    return cur, vfx.load_events(cur["events"], float(cur.get("offset") or 0))


def build(p: dict, out_file: str, width: int = 0, height: int = 0, fps: int = 0, quality: str = "high",
          encoder: str = "auto", audio: bool = True, tag: str = "x") -> tuple[list[str], float]:
    """FFmpeg arguments that render the project (and how long the result is)."""
    W, H = _even(width or p["width"]), _even(height or p["height"])
    F = int(fps or p.get("fps") or 30)
    media = {m["id"]: m for m in p["media"]}
    tracks = {t["id"]: t for t in p["tracks"]}
    order = {t["id"]: i for i, t in enumerate(p["tracks"])}
    D = duration(p)
    if D <= 0.05:
        raise ValueError("The timeline is empty. Add a video first.")
    work = EDIT_DIR / Path(p["id"]).name / "render"
    work.mkdir(parents=True, exist_ok=True)
    fdir = filter_path(fonts.fonts_dir())
    n_ass = [0]

    def ass_file(text: str) -> str:
        n_ass[0] += 1
        f = work / f"{tag}_{n_ass[0]}.ass"
        f.write_text(text, encoding="utf-8")
        return f"ass=filename={filter_path(f)}:fontsdir={fdir}"

    items = [it for it in p["items"] if it["media"] in media and it["track"] in tracks
             and it["out"] - it["in"] > 0.02 and it["start"] < D]
    items.sort(key=lambda it: (order[it["track"]], it["start"]))
    cur, cev = _cursor(p)
    args: list[str] = []
    graph = [f"color=c={p.get('bg') or '#000000'}:s={W}x{H}:r={F}:d={D:.3f},format=yuv420p[bg0]"]
    last, n_in = "bg0", 0
    voice, music, pinned_ops = [], [], []
    sx = W / p["width"]
    for unit in _units(items, F):
        it, m, tr = unit[0], media[unit[0]["media"]], tracks[unit[0]["track"]]
        run = len(unit) > 1
        sp = max(0.05, float(it.get("speed") or 1))
        st = float(it["start"])
        tail = max(0.0, float(it.get("tail") or 0))
        if run:                                     # frame-exact pieces: no drift however many cuts
            cuts, total = [], 0
            for a, nx in zip(unit, unit[1:] + [None]):
                end_t = nx["start"] if nx else a["start"] + (a["out"] - a["in"])
                cnt = int(round((end_t - st) * F)) - int(round((a["start"] - st) * F))
                if cnt > 0:
                    cuts.append((int(round((a["in"] - it["in"]) * F)), cnt))
                    total += cnt
            if not cuts:
                continue
            dur = total / F
            src_len = (cuts[-1][0] + cuts[-1][1] + 2) / F
        else:
            src_len = it["out"] - it["in"]
            dur = src_len / sp
        visual = tr["kind"] == "video" and m["kind"] in ("video", "image") and not tr.get("hidden")
        audible = (m.get("has_audio") and not it.get("muted") and not tr.get("muted")
                   and float(it.get("volume", 1)) > 0.001 and audio)
        if not visual and not audible:
            continue
        if m["kind"] == "image":
            args += ["-loop", "1", "-framerate", str(F), "-t", f"{dur + tail:.3f}", "-i", m["path"]]
        else:
            args += ["-ss", f"{it['in']:.3f}", "-t", f"{src_len + tail * sp + 0.05:.3f}", "-i", m["path"]]
        k = n_in
        n_in += 1
        ent, ext = it.get("enter") or {}, it.get("exit") or {}
        fi = min(max(float(it.get("fade_in") or 0), float(ent.get("dur") or 0.5) if ent.get("type") in ("fade", "zoom") else 0), dur / 2)
        fo = min(max(float(it.get("fade_out") or 0), float(ext.get("dur") or 0.5) if ext.get("type") in ("fade", "zoom") else 0), dur / 2)
        if visual:
            l, t, r, b = [min(0.9, max(0.0, float(v))) for v in (it.get("crop") or [0, 0, 0, 0])]
            cw, ch = max(2.0, m["width"] * (1 - l - r)), max(2.0, m["height"] * (1 - t - b))
            f = min(p["width"] / cw, p["height"] / ch) * float(it.get("scale") or 1) * sx
            w, h = _even(cw * f), _even(ch * f)
            rot, op = float(it.get("rot") or 0), min(1.0, max(0.0, float(it.get("opacity", 1))))
            c: list[str] = []
            if m["kind"] != "image":
                c.append(f"fps={F}")
            if cur and cur.get("media") == m["id"] and m["kind"] == "video":
                txt = vfx.cursor_ass(cur, cev, float(it["in"]), float(it["in"]) + src_len, m["width"], m["height"])
                if txt:
                    c.append(ass_file(txt))
            if run:
                c.append("select='" + "+".join(f"between(n,{a},{a + n - 1})" for a, n in cuts) + f"',setpts=N/{F}/TB")
            elif m["kind"] != "image":
                c += [f"setpts=(PTS-STARTPTS)/{sp:.5f}", f"fps={F}"]
            if tail > 0:
                c.append(f"tpad=stop_mode=clone:stop_duration={tail:.3f}")
                c.append(f"trim=0:{dur + tail:.3f}")
            if l or t or r or b:
                c.append(f"crop=iw*{1 - l - r:.5f}:ih*{1 - t - b:.5f}:iw*{l:.5f}:ih*{t:.5f}")
            c += vfx.fx_filters(it.get("fx"))
            x_off, y_off, zoomy = vfx.enter_exit(it, dur, W, H)
            if zoomy:
                M = vfx.scale_anim(it, dur, "t")
                c.append(f"scale=w='2*trunc({w}*{M}/2)':h='2*trunc({h}*{M}/2)':eval=frame:flags=bicubic")
            else:
                c.append(f"scale={w}:{h}:flags=bicubic")
            alpha = abs(rot) > 0.01 or op < 0.999 or fi > 0 or fo > 0 or m["path"].lower().endswith((".png", ".webp"))
            if alpha:
                c.append("format=rgba")
                if abs(rot) > 0.01:
                    a = math.radians(rot)
                    c.append(f"rotate={a:.6f}:ow=rotw({a:.6f}):oh=roth({a:.6f}):c=none")
                if op < 0.999:
                    c.append(f"colorchannelmixer=aa={op:.4f}")
                if fi > 0:
                    c.append(f"fade=t=in:st=0:d={fi:.3f}:alpha=1")
                if fo > 0:
                    c.append(f"fade=t=out:st={dur - fo:.3f}:d={fo:.3f}:alpha=1")
            c.append(f"setpts=PTS+{st:.3f}/TB")
            graph.append(f"[{k}:v]" + ",".join(c) + f"[v{k}]")
            cx, cy = float(it.get("x", 0.5)) * W, float(it.get("y", 0.5)) * H
            if zoomy:                                # the size changes every frame: centre it by the same formula
                M = vfx.scale_anim(it, dur, f"(t-{st:.3f})")
                xs, ys = f"{cx:.2f}-{w}*{M}/2", f"{cy:.2f}-{h}*{M}/2"
            else:
                xs, ys = f"{cx:.2f}-w/2", f"{cy:.2f}-h/2"
            ov = (f"overlay=x='{xs}{x_off}':y='{ys}{y_off}':eof_action=pass:"
                  f"enable='between(t,{st:.3f},{st + dur + tail:.3f})'")
            if tr.get("pin"):
                pinned_ops.append((f"v{k}", ov))
            else:
                graph.append(f"[{last}][v{k}]{ov}[o{k}]")
                last = f"o{k}"
        if audible:
            if run and len(cuts) > 1:
                graph.append(f"[{k}:a]asplit={len(cuts)}" + "".join(f"[s{k}_{i}]" for i in range(len(cuts))))
                for i, (a, n) in enumerate(cuts):
                    graph.append(f"[s{k}_{i}]atrim=start={a / F:.5f}:end={(a + n) / F:.5f},asetpts=PTS-STARTPTS[t{k}_{i}]")
                graph.append("".join(f"[t{k}_{i}]" for i in range(len(cuts))) + f"concat=n={len(cuts)}:v=0:a=1[c{k}]")
                src, ac = f"[c{k}]", []
            elif run:
                src, ac = f"[{k}:a]", [f"atrim=start={cuts[0][0] / F:.5f}:end={(cuts[0][0] + cuts[0][1]) / F:.5f}", "asetpts=PTS-STARTPTS"]
            else:
                src, ac = f"[{k}:a]", ["asetpts=PTS-STARTPTS"] + _atempo(sp)
            ac += ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
            afx = it.get("afx") or {}
            if afx.get("denoise"):
                ac += ["highpass=f=70", "afftdn=nr=12:nf=-40"]
            if afx.get("level"):
                ac.append("dynaudnorm=f=250:g=15:p=0.9:m=12")
            ac.append(f"volume={float(it.get('volume', 1)):.4f}")
            if fi > 0 and it.get("fade_in"):
                ac.append(f"afade=t=in:st=0:d={fi:.3f}")
            if fo > 0 and it.get("fade_out"):
                ac.append(f"afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}")
            ac.append(f"atrim=0:{dur:.3f}")
            if st > 0.0005:
                ac.append(f"adelay={int(round(st * 1000))}:all=1")
            graph.append(src + ",".join(ac) + f"[a{k}]")
            (music if tr.get("duck") else voice).append(f"[a{k}]")

    # ---- what is drawn on the picture: blurred areas, shapes and text that zoom with it, the zoom, then what stays put
    n = 0
    for e in p.get("els") or []:
        if e.get("kind") == "shape" and e.get("shape") == "blur":
            n += 1
            bw, bh = _even(max(8, float(e.get("w", 0.2)) * W)), _even(max(8, float(e.get("h", 0.1)) * H))
            bx = int(min(W - bw, max(0, float(e.get("x", 0.5)) * W - bw / 2)))
            by = int(min(H - bh, max(0, float(e.get("y", 0.5)) * H - bh / 2)))
            a, b = float(e["start"]), float(e["start"]) + float(e.get("dur") or 0)
            r = max(2, min(int(min(bw, bh) / 4) - 1, int(float(e.get("strength") or 0.6) * 30 * H / 1080)))
            graph.append(f"[{last}]split[bA{n}][bB{n}]")
            graph.append(f"[bB{n}]crop={bw}:{bh}:{bx}:{by},boxblur={r}:2[bC{n}]")
            graph.append(f"[bA{n}][bC{n}]overlay={bx}:{by}:enable='between(t,{a:.3f},{b:.3f})'[bD{n}]")
            last = f"bD{n}"
    txt = vfx.overlay_ass(p, False, W, H)
    if txt:
        graph.append(f"[{last}]{ass_file(txt)}[asu]")
        last = "asu"
    keys = vfx.zoom_keys(p.get("els") or [])
    if keys:
        graph += vfx.zoom_filters(keys, W, H, last, "zoomed", F, D)
        last = "zoomed"
    for i, (lab, ov) in enumerate(pinned_ops):
        graph.append(f"[{last}][{lab}]{ov}[pn{i}]")
        last = f"pn{i}"
    txt = vfx.overlay_ass(p, True, W, H)
    if txt:
        graph.append(f"[{last}]{ass_file(txt)}[asp]")
        last = "asp"
    vmap, amap = f"[{last}]", "[aout]"
    if not audio:
        return args + ["-filter_complex", ";".join(graph), "-map", vmap], D
    fin = f"alimiter=limit=0.97:level=0,apad,atrim=0:{D:.3f}[aout]"

    def mix(labels: list[str], out: str) -> str:
        if len(labels) == 1:
            return f"{labels[0]}anull[{out}]"
        return "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{out}]"
    if voice and music:                              # music dips while someone is talking
        graph.append(mix(voice, "vx"))
        graph.append(mix(music, "mx"))
        graph.append("[vx]asplit[vx1][vx2]")
        graph.append("[mx][vx2]sidechaincompress=threshold=0.02:ratio=9:attack=15:release=450:makeup=1[mxd]")
        graph.append(f"[vx1][mxd]amix=inputs=2:normalize=0:dropout_transition=0,{fin}")
    elif voice or music:
        graph.append(mix(voice or music, "ax"))
        graph.append(f"[ax]{fin}")
    else:
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{D:.3f}[aout]")
    enc = pick_encoder(encoder or "auto")
    crf = {"high": 18, "medium": 21, "small": 25}.get(quality, 19)
    ea = encoder_args(enc, crf, "fast")
    if "-g" in ea:
        ea[ea.index("-g") + 1] = str(F * 2)
    if W * H > 2560 * 1440 and "-maxrate" in ea:            # 4K needs more room than the Shorts ceiling
        ea[ea.index("-maxrate") + 1] = "60M"
        ea[ea.index("-bufsize") + 1] = "120M"
    args += ["-filter_complex", ";".join(graph), "-map", vmap, "-map", amap, *ea, "-r", str(F),
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{D:.3f}", "-movflags", "+faststart", out_file]
    return args, D


EXPORTS: dict[str, dict] = {}
_CANCEL: dict[str, threading.Event] = {}


def export(pid: str, out_dir: Path, opts: dict, emit: Callable, encoder: str = "auto") -> dict:
    p = load(pid)
    if p is None:
        raise ValueError("Project not found.")
    size = SIZES.get(str(opts.get("size") or ""), None)
    W, H = p["width"], p["height"]
    if size:                                   # keep the project's shape, fit it inside the chosen size
        f = min(size[0] / W, size[1] / H) if W >= H else min(size[1] / W, size[0] / H)
        W, H = _even(W * f), _even(H * f)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(str(opts.get("name") or p["name"] or "video")) or "video"
    out = out_dir / f"{base}.mp4"
    i = 2
    while out.exists():
        out = out_dir / f"{base} ({i}).mp4"
        i += 1
    eid = _id()
    args, D = build(p, str(out), W, H, int(opts.get("fps") or 0), str(opts.get("quality") or "high"), encoder, tag=eid)
    job = {"id": eid, "project": pid, "state": "running", "frac": 0.0, "file": str(out), "error": "",
           "name": out.name, "width": W, "height": H, "started": time.time()}
    EXPORTS[eid] = job
    cancel = _CANCEL[eid] = threading.Event()

    def tell():
        try:
            emit("edit", {"type": "export", **job})
        except Exception:
            pass

    def work():
        last = [0.0]

        def prog(f):
            job["frac"] = f
            if time.time() - last[0] > 0.4:
                last[0] = time.time()
                tell()
        try:
            run_ffmpeg(args, D, prog, cancel, ffmpeg_cwd())
            job.update(state="done", frac=1.0)
        except Cancelled:
            job.update(state="cancelled")
            Path(out).unlink(missing_ok=True)
        except Exception as e:
            lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
            job.update(state="failed", error=(lines[-1] if lines else "Export failed.")[:300])
            Path(out).unlink(missing_ok=True)
        tell()
    threading.Thread(target=work, daemon=True).start()
    tell()
    return job


def cancel_export(eid: str) -> None:
    ev = _CANCEL.get(eid)
    if ev:
        ev.set()


def window(p: dict, t0: float, t1: float) -> dict:
    """The same project, but only the part between t0 and t1 (moved to start at 0)."""
    q = {**p, "items": []}
    for it in p["items"]:
        sp = max(0.05, float(it.get("speed") or 1))
        a, b = it["start"], it["start"] + (it["out"] - it["in"]) / sp
        if b <= t0 or a >= t1:
            continue
        n = dict(it)
        if a < t0:
            n["in"] = it["in"] + (t0 - a) * sp
            n["fade_in"] = 0.0
        if b > t1:
            n["out"] = it["out"] - (b - t1) * sp
            n["fade_out"] = 0.0
        n["start"] = max(0.0, a - t0)
        q["items"].append(n)
    q["els"] = [{**e, "start": e["start"] - t0} for e in p.get("els") or []
                if e["start"] < t1 and e["start"] + e.get("dur", 0) > t0]
    if p.get("cursor"):
        q["cursor"] = p["cursor"]
    return q


def frame(pid_or_project, t: float, out_file: str, width: int = 960) -> bool:
    """One composed frame of the project at time t (exactly what export draws)."""
    p = pid_or_project if isinstance(pid_or_project, dict) else load(pid_or_project)
    if p is None:
        return False
    W = _even(min(width, p["width"]))
    H = _even(p["height"] * W / p["width"])
    fps = int(p.get("fps") or 30)
    q = window(p, max(0.0, t), t + 3.0 / fps)
    if not q["items"]:
        return False
    args, _ = build(q, out_file, W, H, audio=False, tag="frame")
    r = run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-frames:v", "1", "-q:v", "3", out_file],
            cwd=ffmpeg_cwd(), timeout=120)
    return r.returncode == 0 and Path(out_file).exists()
