"""Face-follow reframing: find where the speaker is and move a 9:16 window smoothly."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


def _detectors():
    """Haar face detectors, or None if this OpenCV build doesn't ship them (OpenCV 5 dropped them)."""
    try:
        base = cv2.data.haarcascades
        front = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        prof = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
        if front.empty():
            return None
        return front, prof
    except Exception:
        return None


def track_faces(video: str, start: float, end: float, sample_fps: float = 4.0,
                cancel: Optional[threading.Event] = None) -> dict:
    """Return {'times': [...], 'cx': [... or nan], 'size': [...], 'coverage': float, 'faces_max': int}.
    cx is the normalised (0..1) horizontal center of the tracked face."""
    res = {"times": [], "cx": [], "cy": [], "size": [], "coverage": 0.0, "faces_max": 0}
    if cv2 is None:
        return res
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return res
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    step = max(1, int(round(fps / sample_fps)))
    det = _detectors()
    if det is None:
        cap.release()
        return res  # no face detector available -> caller falls back to blurred-background framing
    front, prof = det
    prev = None
    frame_idx = 0
    hits = 0
    t = start
    while t < end:
        if cancel is not None and cancel.is_set():
            break
        ok = cap.grab()
        if not ok:
            break
        t = start + frame_idx / fps
        pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if pos > 0:
            t = pos
        if frame_idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            SW = 640
            scale = SW / w
            small = cv2.resize(frame, (SW, max(2, int(h * scale))), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            minsz = max(20, int(small.shape[0] * 0.07))
            faces = list(front.detectMultiScale(gray, 1.1, 5, minSize=(minsz, minsz)))
            if not faces:
                faces = list(prof.detectMultiScale(gray, 1.1, 5, minSize=(minsz, minsz))) if not prof.empty() else []
                if not faces and not prof.empty():
                    flipped = cv2.flip(gray, 1)
                    for (x, y, fw, fh) in prof.detectMultiScale(flipped, 1.1, 5, minSize=(minsz, minsz)):
                        faces.append((SW - x - fw, y, fw, fh))
            res["faces_max"] = max(res["faces_max"], len(faces))
            cx = cy = sz = float("nan")
            if faces:
                def pick_score(f):
                    x, y, fw, fh = f
                    c = (x + fw / 2) / SW
                    cont = -abs(c - prev) * 2 if prev is not None else 0
                    return fw * fh / (SW * small.shape[0]) * 10 + cont
                x, y, fw, fh = max(faces, key=pick_score)
                cx = (x + fw / 2) / SW
                cy = (y + fh / 2) / small.shape[0]
                sz = fh / small.shape[0]
                prev = cx
                hits += 1
            res["times"].append(t - start)
            res["cx"].append(cx)
            res["cy"].append(cy)
            res["size"].append(sz)
        frame_idx += 1
    cap.release()
    n = len(res["times"])
    res["coverage"] = hits / n if n else 0.0
    return res


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        x0, y0 = points[i]
        # vertical distance from the line (time on x axis)
        if x2 == x1:
            d = abs(y0 - y1)
        else:
            d = abs(y0 - (y1 + (y2 - y1) * (x0 - x1) / (x2 - x1)))
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(points[: idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


def camera_path(track: dict, src_w: int, crop_w: int, duration: float,
                deadzone: float = 0.07, ease: float = 0.6) -> list[tuple[float, float]]:
    """Convert face centers into smooth crop-left positions (pixels) with a 'camera operator' feel."""
    max_x = max(0, src_w - crop_w)
    times = track.get("times", [])
    cx = np.array(track.get("cx", []), dtype=float)
    if not len(times) or np.all(np.isnan(cx)):
        return [(0.0, max_x / 2), (duration, max_x / 2)]
    # fill gaps: hold last known, back-fill the beginning
    idx = np.where(~np.isnan(cx))[0]
    cx = np.interp(np.arange(len(cx)), idx, cx[idx])
    # median filter to kill false detections
    k = 5
    pad = np.pad(cx, (k // 2, k // 2), mode="edge")
    cx = np.array([np.median(pad[i:i + k]) for i in range(len(cx))])
    # dead-zone camera: only move when the subject leaves the comfort zone
    dt = (times[1] - times[0]) if len(times) > 1 else 0.25
    target = cx[0]
    held = []
    still, last = 0.0, cx[0]
    for v in cx:
        still = still + dt if abs(v - last) < 0.012 else 0.0
        last = v
        # re-frame when the subject leaves the comfort zone, or re-center once they settle
        if abs(v - target) > deadzone or (still >= 1.0 and abs(v - target) > 0.015):
            target = v
        held.append(target)
    # ease (exponential smoothing) so moves are glides not jumps
    alpha = 1 - np.exp(-dt / max(ease / 3, 0.05))
    sm, cur = [], held[0]
    for v in held:
        cur = cur + alpha * (v - cur)
        sm.append(cur)
    pts = []
    for t, c in zip(times, sm):
        x = float(np.clip(c * src_w - crop_w / 2, 0, max_x))
        pts.append((float(t), x))
    if pts[0][0] > 0:
        pts.insert(0, (0.0, pts[0][1]))
    pts.append((duration + 1, pts[-1][1]))
    return _rdp(pts, eps=max(2.0, src_w * 0.004))


def x_expression(path: list[tuple[float, float]]) -> str:
    """Piecewise-linear ffmpeg expression of t for the crop x position (flat, no nesting)."""
    if len(path) == 1 or all(abs(p[1] - path[0][1]) < 1 for p in path):
        return str(int(path[0][1]))
    terms = []
    for (t0, x0), (t1, x1) in zip(path, path[1:]):
        if t1 <= t0:
            continue
        slope = (x1 - x0) / (t1 - t0)
        terms.append(f"gte(t,{t0:.3f})*lt(t,{t1:.3f})*({x0:.1f}{slope:+.3f}*(t-{t0:.3f}))")
    last_x = path[-1][1]
    terms.append(f"gte(t,{path[-1][0]:.3f})*{last_x:.1f}")
    return "+".join(terms)
