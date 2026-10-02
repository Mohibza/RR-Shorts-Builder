"""Thumbnails (optional): made from a prompt, optionally copying the look of a reference image.

Two ways to make one:
* AI  - Gemini image models (your Gemini key) or OpenAI's image models (your OpenAI key). The AI gets your prompt,
        the reference thumbnail (its layout, colours and type style are replicated - never its logos or people)
        and a clean frame of your own Short (so the person/scene in the thumbnail is yours).
* Frame - no AI key needed: a clean frame of the Short with the title set in the editorial serif.

Nothing here runs unless the user asks for a thumbnail; a Short without one behaves exactly as before.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from . import fonts
from .config import data_dir
from .llm import GEMINI_API, OPENAI_API, LLMError, QuotaError, _http, _version_key
from .utils import ffmpeg_cwd, filter_path, run_ffmpeg

REFS = data_dir() / "thumb_refs"
SIZES = {"9:16": (1080, 1920), "16:9": (1280, 720), "1:1": (1080, 1080), "4:5": (1080, 1350)}
OPENAI_SIZE = {"9:16": "1024x1536", "4:5": "1024x1536", "16:9": "1536x1024", "1:1": "1024x1024"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_IMG_MODELS: dict = {}


class ThumbError(RuntimeError):
    pass


# ------------------------------------------------------------------ small helpers
def _mime(p: Path) -> str:
    return {"png": "image/png", "webp": "image/webp"}.get(p.suffix.lower().lstrip("."), "image/jpeg")


def keep_reference(path: str) -> str:
    """Copy a reference image the user picked into the app's folder (so it stays available and can be shown)."""
    src = Path(path)
    if not src.is_file() or src.suffix.lower() not in IMG_EXT:
        raise ThumbError("Pick a PNG, JPG or WebP image.")
    if src.stat().st_size > 20 * 1024 * 1024:
        raise ThumbError("That image is larger than 20 MB. Pick a smaller one.")
    REFS.mkdir(parents=True, exist_ok=True)
    if src.parent == REFS:
        return str(src)
    dst = REFS / f"{uuid.uuid4().hex[:10]}{src.suffix.lower()}"
    shutil.copy2(src, dst)
    return str(dst)


def _small(path: str, max_side: int = 1280) -> tuple[str, bytes]:
    """(mime, bytes) of the image, shrunk so uploads stay fast."""
    try:
        import cv2
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            k = max_side / max(h, w)
            if k < 1:
                img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                return "image/jpeg", buf.tobytes()
    except Exception:
        pass
    p = Path(path)
    return _mime(p), p.read_bytes()


def fit(image: bytes, aspect: str, out_jpg: str) -> str:
    """Save the image as a JPEG of exactly the thumbnail size (centre-cropped to the aspect, never stretched)."""
    import cv2
    import numpy as np
    W, H = SIZES.get(aspect, SIZES["9:16"])
    img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ThumbError("The AI returned something that isn't an image. Try again.")
    h, w = img.shape[:2]
    k = max(W / w, H / h)
    img = cv2.resize(img, (max(W, int(round(w * k))), max(H, int(round(h * k)))),
                     interpolation=cv2.INTER_LANCZOS4 if k > 1 else cv2.INTER_AREA)
    h, w = img.shape[:2]
    x, y = (w - W) // 2, (h - H) // 2
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_jpg, img[y:y + H, x:x + W], [cv2.IMWRITE_JPEG_QUALITY, 93])
    return out_jpg


def clean_frame(src: str, t: float, out_jpg: str, aspect: str = "9:16", crop_x: Optional[float] = None,
                src_wh: tuple = (0, 0)) -> str:
    """A frame of the source video without captions, framed like the Short (face-follow crop when known)."""
    W, H = SIZES.get(aspect, SIZES["9:16"])
    sw, sh = src_wh
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if sw and sh and sw / sh > W / H:
        cw = int(sh * W / H) // 2 * 2
        x = int(max(0, min(sw - cw, crop_x if (crop_x is not None and aspect == "9:16") else (sw - cw) / 2)))
        vf = f"crop={cw}:{sh}:{x}:0,scale={W}:{H}:flags=lanczos"
    out_jpg = str(Path(out_jpg).resolve())
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{max(0.0, t):.2f}", "-i", str(Path(src).resolve()), "-frames:v", "1", "-vf", vf,
                "-q:v", "2", "-update", "1", out_jpg], 1.0)
    return out_jpg


# ------------------------------------------------------------------ prompt
def build_prompt(user_prompt: str, title: str, aspect: str, has_ref: bool, has_frame: bool) -> str:
    kind = {"9:16": "vertical 9:16 YouTube Shorts / TikTok / Reels cover", "16:9": "16:9 YouTube thumbnail",
            "1:1": "square thumbnail", "4:5": "4:5 Instagram cover"}.get(aspect, "thumbnail")
    lines = [f"Create one eye-catching {kind} image. Output only the image."]
    n = 1
    if has_ref:
        lines.append(f"Image {n} is a REFERENCE thumbnail. Replicate its layout, composition, colour palette, lighting, "
                     "typography style, text placement and overall mood as closely as you can. Do NOT copy its logos, "
                     "watermarks, brand names or the people in it; only the design.")
        n += 1
    if has_frame:
        lines.append(f"Image {n} is a frame from MY video. Use the person and scene from it as the subject of the "
                     "thumbnail. Keep the person's real face, hair and clothing recognisable; you may relight, cut "
                     "out and reposition them to fit the design.")
    if title.strip():
        lines.append(f'The headline text on the thumbnail must read exactly: "{title.strip()}". Spell it exactly, '
                     "make it large, bold and readable on a phone, and keep it inside the safe centre area.")
    else:
        lines.append("Do not add any text unless my instructions ask for it.")
    if user_prompt.strip():
        lines.append("My instructions: " + user_prompt.strip())
    lines.append("High contrast, sharp, professional, no borders, no watermark, no UI elements.")
    return "\n".join(lines)


# ------------------------------------------------------------------ Gemini
def gemini_image_models(key: str) -> list[str]:
    """Image-generation models this key can use, best value first."""
    ck = key[-6:]
    c = _IMG_MODELS.get(ck)
    if c and time.time() - c[0] < 3600:
        return c[1]
    names, token = [], ""
    try:
        for _ in range(5):
            url = f"{GEMINI_API}/models?pageSize=200&key={key}" + (f"&pageToken={token}" if token else "")
            d = _http(url, timeout=30)
            for m in d.get("models", []):
                n = m["name"].split("/")[-1]
                if "generateContent" in m.get("supportedGenerationMethods", []) and "image" in n and "gemini" in n:
                    names.append(n)
            token = d.get("nextPageToken", "")
            if not token:
                break
    except LLMError:
        names = []

    def rank(n: str) -> tuple:
        tier = 2 if ("flash" in n and "lite" not in n) else (1 if "lite" in n else 0)   # flash, then lite, then pro
        return (tier, _version_key(n))
    out = sorted(set(names), key=rank, reverse=True) or ["gemini-3.1-flash-image", "gemini-2.5-flash-image",
                                                         "gemini-2.5-flash-image-preview"]
    _IMG_MODELS[ck] = (time.time(), out)
    return out


def gemini_image(prompt: str, images: list[tuple[str, bytes]], key: str, aspect: str, log=None) -> bytes:
    key = key.strip()
    parts: list = [{"text": prompt}]
    for mime, data in images:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    last: Exception = ThumbError("No Gemini image model is available for this key.")
    for m in gemini_image_models(key)[:4]:
        # newer models take the aspect ratio in the request; if a model rejects that, ask again without it
        for cfg in ({"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": aspect}},
                    {"responseModalities": ["TEXT", "IMAGE"]}):
            try:
                d = _http(f"{GEMINI_API}/models/{m}:generateContent?key={key}",
                          {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}, timeout=240)
            except QuotaError as e:
                last = e
                break
            except LLMError as e:
                last = e
                if "HTTP 400" in str(e) and "imageConfig" in cfg:
                    continue
                break
            cand = (d.get("candidates") or [{}])[0]
            for p in cand.get("content", {}).get("parts", []):
                blob = p.get("inlineData") or p.get("inline_data")
                if blob and blob.get("data"):
                    if log:
                        log(f"Thumbnail made with {m}")
                    return base64.b64decode(blob["data"])
            why = cand.get("finishReason") or (d.get("promptFeedback") or {}).get("blockReason") or "no image"
            last = ThumbError(f"The AI didn't return an image ({why}). Try rewording the prompt.")
            break
    raise last


# ------------------------------------------------------------------ OpenAI
def _openai_models(key: str) -> list[str]:
    try:
        d = _http(f"{OPENAI_API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        ids = [m["id"] for m in d.get("data", []) if re.match(r"^gpt-image-[\d.]+(-mini)?$", m.get("id", ""))]
    except LLMError:
        ids = []
    full = sorted([i for i in ids if "mini" not in i], key=_version_key, reverse=True)
    mini = sorted([i for i in ids if "mini" in i], key=_version_key, reverse=True)
    return (full[:1] + mini[:1] + full[1:2]) or ["gpt-image-1", "gpt-image-1-mini"]


def _multipart(fields: dict, files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    b = "----rr" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, fn, mime, data in files:
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{fn}"\r\n'
                   f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def openai_image(prompt: str, images: list[tuple[str, bytes]], key: str, aspect: str, log=None) -> bytes:
    key = key.strip()
    size = OPENAI_SIZE.get(aspect, "1024x1536")
    last: Exception = ThumbError("No OpenAI image model is available for this key.")
    for m in _openai_models(key):
        try:
            if images:
                body, ctype = _multipart(
                    {"model": m, "prompt": prompt, "size": size, "n": "1"},
                    [("image[]", f"image{i}.{'png' if mime == 'image/png' else 'jpg'}", mime, data)
                     for i, (mime, data) in enumerate(images)])
                req = urllib.request.Request(f"{OPENAI_API}/images/edits", data=body, method="POST",
                                             headers={"Authorization": f"Bearer {key}", "Content-Type": ctype})
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        d = json.loads(r.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")
                    try:
                        msg = json.loads(detail).get("error", {}).get("message", detail)
                    except Exception:
                        msg = detail
                    if e.code == 429:
                        raise QuotaError(f"quota reached: {str(msg)[:200]}") from None
                    raise LLMError(f"HTTP {e.code}: {str(msg)[:300]}") from None
                except urllib.error.URLError as e:
                    raise LLMError(f"network error: {e.reason}") from None
            else:
                d = _http(f"{OPENAI_API}/images/generations", {"model": m, "prompt": prompt, "size": size, "n": 1},
                          headers={"Authorization": f"Bearer {key}"}, timeout=300)
        except QuotaError as e:
            last = e
            continue
        except LLMError as e:
            last = e
            if any(c in str(e) for c in ("HTTP 404", "HTTP 400", "HTTP 403")):
                continue
            raise
        item = (d.get("data") or [{}])[0]
        if item.get("b64_json"):
            if log:
                log(f"Thumbnail made with {m}")
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=120) as r:
                return r.read()
        last = ThumbError("The AI didn't return an image. Try rewording the prompt.")
    raise last


# ------------------------------------------------------------------ public entry points
def providers(s) -> list[str]:
    out = []
    if getattr(s, "gemini_api_key", "").strip():
        out.append("gemini")
    if getattr(s, "openai_api_key", "").strip():
        out.append("openai")
    return out


def generate_ai(s, user_prompt: str, title: str, aspect: str, ref: str, frame: str, out_jpg: str,
                provider: str = "auto", log=None) -> dict:
    """Make the thumbnail with AI. Returns {'path', 'provider'}; raises ThumbError with a message for the user."""
    avail = providers(s)
    if not avail:
        raise ThumbError("AI thumbnails need a Gemini or OpenAI key (Settings → AI). "
                         "Without a key, use “From video frame”.")
    order = [provider] if provider in avail else avail
    images: list = []
    if ref and Path(ref).is_file():
        images.append(_small(ref))
    if frame and Path(frame).is_file():
        images.append(_small(frame))
    prompt = build_prompt(user_prompt, title, aspect, bool(ref and Path(ref).is_file()),
                          bool(frame and Path(frame).is_file()))
    last: Exception = ThumbError("Couldn't make the thumbnail.")
    for p in order:
        try:
            raw = (gemini_image(prompt, images, s.gemini_api_key, aspect, log) if p == "gemini"
                   else openai_image(prompt, images, s.openai_api_key, aspect, log))
            return {"path": fit(raw, aspect, out_jpg), "provider": p}
        except QuotaError as e:
            last = ThumbError(f"{'Gemini' if p == 'gemini' else 'OpenAI'}: the image limit for this key is used up "
                              f"for now ({str(e)[:120]}).")
        except (LLMError, ThumbError) as e:
            last = e if isinstance(e, ThumbError) else ThumbError(
                f"{'Gemini' if p == 'gemini' else 'OpenAI'} couldn't make the image: {str(e)[:220]}")
    raise last


def generate_frame(title: str, aspect: str, frame: str, out_jpg: str, accent: str = "#FFE400") -> dict:
    """No-AI thumbnail: the clean frame, slightly graded, with the title in the editorial serif."""
    from . import story
    W, H = SIZES.get(aspect, SIZES["9:16"])
    words = [w for w in re.split(r"\s+", (title or "").strip()) if w]
    out_jpg = str(Path(out_jpg).resolve())
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
          "eq=contrast=1.08:saturation=1.18", "unsharp=5:5:0.6:5:5:0.0"]
    ass_file = None
    if words and story.LATIN.match(title):
        # two lines at most: the last strong word big, the rest above it
        big = max(words[-3:], key=lambda w: len(re.sub(r"\W", "", w))).upper()
        rest = " ".join(w for w in words if w.upper() != big)
        sx = W / 1080.0
        tall = H > W
        size = int(min(330 * min(sx * 1.2, 1.0), (H * 0.2) if not tall else 999,
                       W * (0.9 if tall else 0.6) / max(1e-3, fonts.text_width("Abril Fatface", big, 1.0))))
        y = int(H * (0.24 if tall else 0.8))          # wide: a lower-third headline, clear of the face
        ks = int(max(30, min(64 if tall else 46, W * 0.86 / max(1e-3, fonts.text_width("Lato", rest or " ", 1.0)))))
        head = ("[Script Info]\nScriptType: v4.00+\n" f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n"
                "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
                "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
                "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: S,Lato,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        ev = []
        shadow = "\\c&H000000&\\alpha&H60&\\blur" + str(max(4, size // 22))
        kcol = "\\c" + story._ass_color(accent)
        for layer in (0, 1):
            off = 4 if layer == 0 else 0
            ev.append("Dialogue: %d,0:00:00.00,0:00:05.00,S,,0,0,0,,{\\an5\\pos(%d,%d)\\fnAbril Fatface\\fs%d"
                      "\\bord0\\shad0%s}%s" % (layer, W // 2 + off, y + off * 2, size, shadow if layer == 0 else "",
                                              story_esc(big)))
            if rest:
                ev.append("Dialogue: %d,0:00:00.00,0:00:05.00,S,,0,0,0,,{\\an5\\pos(%d,%d)\\fnLato\\b1\\fs%d"
                          "\\bord0\\shad0%s}%s" % (layer, W // 2 + off, int(y - size * 0.58) + off * 2, ks,
                                                  shadow if layer == 0 else kcol, story_esc(rest)))
        ass_file = Path(out_jpg).with_suffix(".ass")
        ass_file.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
        vf.append(f"ass=filename={filter_path(str(ass_file))}:fontsdir={filter_path(str(fonts.fonts_dir()))}")
    try:
        run_ffmpeg(["-i", str(Path(frame).resolve()), "-frames:v", "1", "-vf", ",".join(vf), "-q:v", "2",
                    "-update", "1", out_jpg], 1.0, cwd=ffmpeg_cwd())
    finally:
        if ass_file:
            ass_file.unlink(missing_ok=True)
    return {"path": out_jpg, "provider": "frame"}


def story_esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


# ------------------------------------------------------------------ built-in designer (free, offline, no watermark)
def cutout_png(frame_jpg: str, out_png: str) -> Optional[list]:
    """The speaker cut out of the frame as a transparent PNG (same size as the frame).
    Returns the speaker's box [x, y, w, h] as fractions of the frame, or None when nobody is clearly in it."""
    import cv2
    import numpy as np
    from . import cutout
    if not cutout.available():
        return None
    img = cv2.imread(frame_jpg, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    k = 640 / max(h, w)
    small = cv2.resize(img, (max(16, int(w * k)), max(16, int(h * k))), interpolation=cv2.INTER_AREA)
    m = cutout._refine(cutout.segment(small[:, :, ::-1].copy()))
    if not cutout.person_like(m):
        return None
    # second pass, zoomed in on the person: the model sees them much larger, so hair and shoulders come out clean
    ys, xs = np.where(m > 0.5)
    sx, sy = w / m.shape[1], h / m.shape[0]
    bx0, bx1, by0, by1 = xs.min() * sx, xs.max() * sx, ys.min() * sy, ys.max() * sy
    px, py = (bx1 - bx0) * 0.18, (by1 - by0) * 0.12
    x0, x1 = int(max(0, bx0 - px)), int(min(w, bx1 + px))
    y0, y1 = int(max(0, by0 - py)), int(min(h, by1 + py))
    a = np.zeros((h, w), np.float32)
    crop = img[y0:y1, x0:x1]
    if crop.shape[0] > 40 and crop.shape[1] > 40:
        kc = 640 / max(crop.shape[:2])
        cs = cv2.resize(crop, (max(16, int(crop.shape[1] * kc)), max(16, int(crop.shape[0] * kc))),
                        interpolation=cv2.INTER_AREA)
        m2 = cutout.segment(cs[:, :, ::-1].copy())
        m2 = cv2.resize(m2, (x1 - x0, y1 - y0), interpolation=cv2.INTER_CUBIC)
        coarse = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
        coarse_raw = coarse.copy()
        coarse = cv2.dilate(coarse, np.ones((1, 1), np.uint8) if w < 200 else np.ones((max(3, w // 40), max(3, w // 40)), np.uint8))
        # fine detail from the zoomed pass, but never lose something the first pass was sure about (dark hats, hair)
        a[y0:y1, x0:x1] = np.maximum(m2 * (coarse > 0.15), 0.72 * coarse_raw + 0.28 * m2)
    else:
        a = cv2.resize(m, (w, h), interpolation=cv2.INTER_CUBIC)
    a = np.clip((a - 0.45) / 0.3, 0.0, 1.0)
    a = cv2.erode(a, np.ones((3, 3), np.uint8), iterations=max(1, w // 700))
    a = cv2.GaussianBlur(a, (0, 0), max(1.0, w / 800))
    ys, xs = np.where(a > 0.5)
    if len(xs) < 50:
        return None
    rgba = np.dstack([img, (a * 255).astype(np.uint8)])
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_png, rgba)
    return [round(float(xs.min()) / w, 4), round(float(ys.min()) / h, 4),
            round(float(xs.max() - xs.min()) / w, 4), round(float(ys.max() - ys.min()) / h, 4)]


def _hex(bgr) -> str:
    b, g, r = [int(max(0, min(255, round(float(v))))) for v in bgr]
    return f"#{r:02X}{g:02X}{b:02X}"


def analyze_reference(path: str) -> dict:
    """What makes the reference look the way it does, so the designer can rebuild it with the user's own speaker
    and words: the colour palette, where the headline sits (and its colours), where the subject sits."""
    import cv2
    import numpy as np
    from . import cutout
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ThumbError("That reference image couldn't be read.")
    H0, W0 = img.shape[:2]
    k = 480 / max(H0, W0)
    im = cv2.resize(img, (max(16, int(W0 * k)), max(16, int(H0 * k))), interpolation=cv2.INTER_AREA)
    h, w = im.shape[:2]
    # palette: 6 dominant colours, biggest first
    px = im.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _c, labels, centers = cv2.kmeans(px, 6, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=6)
    order = np.argsort(-counts)
    palette = [_hex(centers[i]) for i in order]
    hsv = cv2.cvtColor(centers.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV)[0]
    vivid = max(range(6), key=lambda i: float(hsv[i][1]) * (0.4 + float(hsv[i][2]) / 255) * (1 if counts[i] > 0.01 * len(px) else 0.2))
    dark = float(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).mean()) < 110
    # subject
    subject = None
    try:
        if cutout.available():
            m = cutout._refine(cutout.segment(im[:, :, ::-1].copy()))
            b = m > 0.5
            if 0.04 <= b.mean() <= 0.75:
                ys, xs = np.where(b)
                subject = [round(float(xs.min()) / w, 3), round(float(ys.min()) / h, 3),
                           round(float(xs.max() - xs.min()) / w, 3), round(float(ys.max() - ys.min()) / h, 3)]
    except Exception:
        subject = None
    # headline: wide bands of dense, strong edges (letters), outside the subject's middle
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 90, 200)
    band = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, w // 22), max(3, h // 90))))
    band = cv2.morphologyEx(band, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w // 40), max(3, h // 120))))
    cnts, _h = cv2.findContours(band, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    sx0 = sy0 = sx1 = sy1 = 0
    if subject:
        sx0, sy0 = subject[0] * w, subject[1] * h
        sx1, sy1 = sx0 + subject[2] * w, sy0 + subject[3] * h
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < w * 0.18 or bh < h * 0.025 or bh > h * 0.3 or bw / max(bh, 1) < 1.3:
            continue
        dens = float(edges[y:y + bh, x:x + bw].mean()) / 255
        if dens < 0.06:
            continue
        if subject:      # mostly on top of the subject: that's the person, not a headline
            ox = max(0.0, min(x + bw, sx1) - max(x, sx0)) * max(0.0, min(y + bh, sy1) - max(y, sy0))
            if ox > 0.55 * bw * bh:
                continue
        # letters are two flat colours far apart; foliage and faces are not
        g = gray[y:y + bh, x:x + bw]
        lo, hi = np.percentile(g, 12), np.percentile(g, 88)
        contrast = float(hi - lo)
        if contrast < 70:
            continue
        boxes.append((bw * bh * dens * (contrast / 255) ** 2, x, y, bw, bh))
    text = None
    if boxes:
        boxes.sort(reverse=True)
        _s, x, y, bw, bh = boxes[0]
        x0, y0, x1, y1 = x, y, x + bw, y + bh
        for _s2, xx, yy, ww, hh in boxes[1:4]:       # other lines of the same headline: close above/below, overlapping
            gap = max(yy - y1, y0 - (yy + hh))
            if gap < 0.7 * bh and min(x1, xx + ww) - max(x0, xx) > 0.3 * min(bw, ww) and hh < bh * 2.2:
                x0, y0, x1, y1 = min(x0, xx), min(y0, yy), max(x1, xx + ww), max(y1, yy + hh)
        roi = im[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
        tcol, tstroke = "#FFFFFF", "#000000"
        if len(roi) > 20:
            _c2, l2, c2 = cv2.kmeans(roi, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
            n2 = np.bincount(l2.ravel(), minlength=2)
            lum = [0.114 * c2[i][0] + 0.587 * c2[i][1] + 0.299 * c2[i][2] for i in (0, 1)]
            sat = [float(max(c2[i]) - min(c2[i])) for i in (0, 1)]
            # letters: the brighter / more colourful of the two, unless it clearly dominates the box (then it's the plate)
            ti = max((0, 1), key=lambda i: lum[i] + sat[i] * 0.8)
            if n2[ti] > 0.72 * len(roi):
                ti = 1 - ti
            tcol, tstroke = _hex(c2[ti]), _hex(c2[1 - ti])
        cxf = (x0 + x1) / 2 / w
        text = {"x": round(float(cxf), 3), "y": round(float(y0 + y1) / 2 / h, 3), "w": round(float(x1 - x0) / w, 3),
                "h": round(float(y1 - y0) / h, 3), "align": "left" if cxf < 0.4 else ("right" if cxf > 0.6 else "center"),
                "color": tcol, "stroke": tstroke}
    return {"palette": palette, "accent": _hex(centers[vivid]), "dark": bool(dark), "subject": subject, "text": text,
            "aspect": round(float(W0) / H0, 3)}


def save_design(image_b64: str, aspect: str, out_jpg: str) -> str:
    """Save the thumbnail the designer drew (a JPEG/PNG data URL) at exactly the thumbnail size."""
    raw = image_b64.split(",", 1)[1] if image_b64.startswith("data:") else image_b64
    try:
        data = base64.b64decode(raw)
    except Exception:
        raise ThumbError("The thumbnail image was damaged on the way. Try saving again.")
    if len(data) > 25 * 1024 * 1024:
        raise ThumbError("That thumbnail is too large.")
    return fit(data, aspect, out_jpg)
