"""Speaker cut-out for the editorial title (text behind the person).

A tiny salient-object model (U²-Net small, 4.6 MB, Apache-2.0) runs through OpenCV's DNN module, so nothing extra
has to be installed. It only runs on the few seconds the title is on screen, on small frames that were rendered
exactly as the export frames them, then the masks are smoothed in space and time and handed to FFmpeg.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .config import ASSETS

MODEL = ASSETS / "models" / "u2netp.onnx"
_NET = None
_LOCK = threading.Lock()          # one inference at a time (OpenCV nets aren't thread-safe)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except Exception:
        return False
    return MODEL.exists()


def _net():
    global _NET
    if _NET is None:
        import cv2
        _NET = cv2.dnn.readNetFromONNX(str(MODEL))
    return _NET


def segment(rgb: np.ndarray) -> np.ndarray:
    """Soft person/subject mask (0..1, float32) for an RGB frame of any size."""
    import cv2
    h, w = rgb.shape[:2]
    side = max(h, w)
    pad = np.zeros((side, side, 3), dtype=np.uint8)       # keep the aspect: pad to a square, don't stretch
    y0, x0 = (side - h) // 2, (side - w) // 2
    pad[y0:y0 + h, x0:x0 + w] = rgb
    x = cv2.resize(pad, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    blob = x.transpose(2, 0, 1)[None].astype(np.float32)
    with _LOCK:
        net = _net()
        net.setInput(blob)
        out = net.forward()
    m = out[0, 0]
    m = (m - m.min()) / max(1e-6, float(m.max() - m.min()))
    m = cv2.resize(m, (side, side), interpolation=cv2.INTER_LINEAR)[y0:y0 + h, x0:x0 + w]
    return m.astype(np.float32)


def _refine(m: np.ndarray) -> np.ndarray:
    import cv2
    m = np.clip((m - 0.38) / 0.32, 0.0, 1.0)                           # soft threshold
    m = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=1)          # tuck in the halo
    return cv2.GaussianBlur(m, (0, 0), 1.1)


def person_like(m: np.ndarray) -> bool:
    """Does the mask look like a speaker? One main shape, reaching the bottom of the frame (shoulders/torso),
    not too small or too big. Stops the title hiding behind a random background blob."""
    import cv2
    b = (m > 0.5).astype(np.uint8)
    cover = float(b.mean())
    if not 0.05 <= cover <= 0.6:
        return False
    n, lab, stats, _c = cv2.connectedComponentsWithStats(b, connectivity=8)
    if n < 2:
        return False
    areas = stats[1:, cv2.CC_STAT_AREA]
    k = int(np.argmax(areas)) + 1
    if areas[k - 1] < 0.75 * b.sum():
        return False
    top, h = stats[k, cv2.CC_STAT_TOP], stats[k, cv2.CC_STAT_HEIGHT]
    if top + h < b.shape[0] * 0.97:          # must reach the bottom edge
        return False
    x, w = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_WIDTH]
    cx = x + w / 2
    return b.shape[1] * 0.15 <= cx <= b.shape[1] * 0.85


def make_masks(frames_dir: Path, n: int, out_dir: Path, fps: int, cancel=None) -> int:
    """Read frames mk_00000.png… (n of them), write mask_00000.png… Returns how many masks were written
    (0 = the cut-out isn't usable for this Short, e.g. nobody in frame)."""
    import cv2
    step = max(1, int(round(fps / 10)))                 # ~10 inferences a second, the rest interpolated
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    keys: dict[int, np.ndarray] = {}
    for i in idx:
        if cancel is not None and cancel.is_set():
            return 0
        bgr = cv2.imread(str(frames_dir / f"mk_{i:05d}.png"), cv2.IMREAD_COLOR)
        if bgr is None:
            return 0
        keys[i] = _refine(segment(bgr[:, :, ::-1].copy()))
    if sum(person_like(k) for k in keys.values()) < 0.7 * len(keys):
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    ks = sorted(keys)
    for j in range(n):
        a = max([k for k in ks if k <= j], default=ks[0])
        b = min([k for k in ks if k >= j], default=ks[-1])
        if a == b:
            m = keys[a]
        else:
            f = (j - a) / (b - a)
            m = keys[a] * (1 - f) + keys[b] * f
        cv2.imwrite(str(out_dir / f"mask_{j:05d}.png"), (np.clip(m, 0, 1) * 255).astype(np.uint8))
    return n


def head_top(mask_png: Path) -> Optional[float]:
    """Topmost row of the person (fraction of the height), or None."""
    import cv2
    m = cv2.imread(str(mask_png), cv2.IMREAD_GRAYSCALE)
    if m is None:
        return None
    rows = np.where((m > 128).mean(axis=1) > 0.02)[0]
    return float(rows[0]) / m.shape[0] if len(rows) else None


_HEAD_CACHE: dict = {}


def head_top_at(video: str, t: float, x0: float, x1: float) -> Optional[float]:
    """Top of the person's head (fraction of the frame height) in the source frame at `t`, looking only at the
    columns x0..x1 (fractions of the width: the 9:16 window). One small inference, cached. None if unclear."""
    key = (str(video), round(t, 2), round(x0, 3), round(x1, 3))
    if key in _HEAD_CACHE:
        return _HEAD_CACHE[key]
    res = None
    try:
        import cv2
        cap = cv2.VideoCapture(str(video))
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            a, b = max(0, int(x0 * w)), min(w, int(x1 * w))
            if b - a > 16:
                crop = frame[:, a:b]
                sc = 480 / crop.shape[0]
                small = cv2.resize(crop, (max(16, int(crop.shape[1] * sc)), 480), interpolation=cv2.INTER_AREA)
                m = _refine(segment(small[:, :, ::-1].copy()))
                if person_like(m):
                    rows = np.where((m > 0.5).mean(axis=1) > 0.04)[0]
                    if len(rows):
                        res = float(rows[0]) / m.shape[0]
    except Exception:
        res = None
    if len(_HEAD_CACHE) > 256:
        _HEAD_CACHE.clear()
    _HEAD_CACHE[key] = res
    return res
