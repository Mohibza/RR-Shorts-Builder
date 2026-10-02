"""Editor effects, in the form FFmpeg can render: zoom-and-pan, colour looks, clip transitions, and everything
drawn on top (text with animations, shapes, captions, click ripples, cursor highlight) as ASS subtitles.

The preview in the app (web/src/lib/edit.ts + components/edit/Stage.tsx) mirrors the maths here: when a number
changes in one place it must change in the other.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from . import fonts
from .captions import col, esc, ts

# ================================================================ zoom and pan
def zoom_keys(els: list[dict]) -> list[tuple[float, float, float, float]]:
    """Zoom blocks -> keyframes (t, zoom, cx, cy), eased between. Blocks that follow each other closely pan
    straight from one target to the next instead of zooming out and in again."""
    zs = sorted([e for e in els if e.get("kind") == "zoom" and e.get("dur", 0) > 0.15], key=lambda e: e["start"])
    keys: list[tuple[float, float, float, float]] = []
    prev_end = -1e9
    for i, e in enumerate(zs):
        a = max(float(e["start"]), prev_end)
        b = float(e["start"]) + float(e["dur"])
        if b - a < 0.15:
            continue
        z, cx, cy = max(1.0, float(e.get("z") or 1.6)), float(e.get("cx", 0.5)), float(e.get("cy", 0.5))
        ease = min(float(e.get("ease") or 0.5), (b - a) / 2)
        joined = bool(keys) and a - prev_end < 0.3 and keys[-1][1] > 1.001
        if not joined:
            if keys and keys[-1][1] > 1.001:                       # close the previous block: zoom back out
                pt, pz, pcx, pcy = keys[-1]
                keys.append((pt + min(0.5, max(0.1, a - pt)), 1.0, pcx, pcy))
            keys.append((a, 1.0, cx, cy))
        keys.append((a + ease, z, cx, cy))
        nxt = zs[i + 1] if i + 1 < len(zs) else None
        hold_end = b - ease if not (nxt and float(nxt["start"]) - b < 0.3) else b
        keys.append((max(a + ease, hold_end), z, cx, cy))
        if not (nxt and float(nxt["start"]) - b < 0.3):
            keys.append((b, 1.0, cx, cy))
        prev_end = b
    out: list[tuple[float, float, float, float]] = []
    for k in keys:                                                  # strictly increasing times
        if out and k[0] <= out[-1][0]:
            k = (out[-1][0] + 0.001, k[1], k[2], k[3])
        out.append(k)
    return out


def zoom_at(keys: list, t: float) -> tuple[float, float, float]:
    if not keys or t <= keys[0][0] or t >= keys[-1][0]:
        return 1.0, 0.5, 0.5
    for a, b in zip(keys, keys[1:]):
        if a[0] <= t < b[0]:
            u = (t - a[0]) / (b[0] - a[0])
            s = u * u * (3 - 2 * u)
            return tuple(a[i] + (b[i] - a[i]) * s for i in (1, 2, 3))      # type: ignore[return-value]
    return 1.0, 0.5, 0.5


def _piece(keys: list, idx: int, base: float) -> str:
    """FFmpeg expression: `base` outside the keyframes, smoothly interpolated value `idx` inside."""
    parts = []
    for a, b in zip(keys, keys[1:]):
        va, vb = a[idx] - base, b[idx] - base
        if abs(va) < 1e-6 and abs(vb) < 1e-6:
            continue
        gate = f"gte(t,{a[0]:.3f})*lt(t,{b[0]:.3f})"
        if abs(va - vb) < 1e-6:
            parts.append(f"{gate}*{va:.5f}")
        else:
            u = f"((t-{a[0]:.3f})/{b[0] - a[0]:.3f})"
            parts.append(f"{gate}*({va:.5f}+{vb - va:.5f}*{u}*{u}*(3-2*{u}))")
    return (f"{base:g}+" if base else "") + ("+".join(parts) if parts else "0")


def zoom_filters(keys: list, W: int, H: int, src: str, dst: str, fps: int, D: float) -> list[str]:
    """Graph lines that zoom/pan [src] into [dst] (same size)."""
    Z, CX, CY = _piece(keys, 1, 1.0), _piece(keys, 2, 0.0), _piece(keys, 3, 0.0)
    return [
        f"color=c=black:s={W}x{H}:r={fps}:d={D:.3f},format=yuv420p[zbg]",
        f"[{src}]scale=w='2*trunc({W}*({Z})/2)':h='2*trunc({H}*({Z})/2)':eval=frame:flags=bicubic[zsc]",
        f"[zbg][zsc]overlay=x='-clip(({CX})*{W}*({Z})-{W / 2:g},0,{W}*({Z})-{W})':"
        f"y='-clip(({CY})*{H}*({Z})-{H / 2:g},0,{H}*({Z})-{H})':eof_action=pass[{dst}]",
    ]


# ================================================================ colour looks
LOOKS: dict[str, dict] = {
    "none": {},
    "vivid": {"contrast": 1.08, "sat": 1.35},
    "cinematic": {"contrast": 1.12, "sat": 1.06, "temp": 0.07, "bright": -0.01, "vignette": 0.4},
    "teal_orange": {"contrast": 1.1, "sat": 1.15, "temp": 0.12, "tint": -0.05, "vignette": 0.25},
    "warm": {"temp": 0.14, "sat": 1.1},
    "cool": {"temp": -0.14, "sat": 1.05, "contrast": 1.04},
    "golden": {"temp": 0.2, "sat": 1.18, "bright": 0.02, "lift": 0.03},
    "bw": {"sat": 0.0, "contrast": 1.2},
    "noir": {"sat": 0.0, "contrast": 1.42, "bright": -0.03, "vignette": 0.55},
    "vintage": {"sat": 0.78, "temp": 0.13, "lift": 0.07, "contrast": 0.95, "vignette": 0.4, "grain": 0.35},
    "matte": {"contrast": 0.9, "lift": 0.09, "sat": 0.92},
    "faded": {"contrast": 0.84, "lift": 0.12, "sat": 0.8},
    "punchy": {"contrast": 1.18, "sat": 1.45},
    "dream": {"contrast": 0.95, "bright": 0.04, "sat": 1.1, "blur": 0.6},
    "night": {"temp": -0.2, "bright": -0.07, "sat": 0.85, "contrast": 1.1, "vignette": 0.3},
    "pastel": {"sat": 0.74, "bright": 0.05, "contrast": 0.92, "lift": 0.04},
    "crisp": {"contrast": 1.06, "sat": 1.08, "sharpen": 0.6},
    "sepia": {"sat": 0.25, "temp": 0.3, "contrast": 1.05, "lift": 0.03},
    "sunset": {"temp": 0.26, "tint": 0.08, "sat": 1.25, "contrast": 1.06, "vignette": 0.2},
    "emerald": {"tint": -0.14, "temp": -0.04, "sat": 1.12, "contrast": 1.05},
    "cyber": {"temp": -0.16, "tint": 0.16, "sat": 1.4, "contrast": 1.14},
    "bleach": {"sat": 0.55, "contrast": 1.3, "bright": 0.02},
    "moody": {"bright": -0.06, "contrast": 1.16, "sat": 0.85, "vignette": 0.45},
    "glow": {"bright": 0.05, "contrast": 0.96, "sat": 1.15, "blur": 0.25, "lift": 0.03},
}
FX_DEFAULT = {"bright": 0.0, "contrast": 1.0, "sat": 1.0, "temp": 0.0, "tint": 0.0, "lift": 0.0, "blur": 0.0,
              "vignette": 0.0, "grain": 0.0, "sharpen": 0.0}


def fx_values(fx: Optional[dict]) -> dict:
    v = dict(FX_DEFAULT)
    if fx:
        v.update(LOOKS.get(str(fx.get("look") or "none"), {}))
        for k in FX_DEFAULT:                       # manual sliders sit on top of the look
            if k in fx and fx[k] is not None:
                d = float(fx[k])
                v[k] = v[k] * d if k in ("contrast", "sat") else v[k] + d
    return v


def color_matrix(v: dict) -> tuple[list[float], list[float], list[list[float]]]:
    """(per-channel gain, per-channel offset 0..1, 3x3 saturation matrix). out = SAT * (gain * in + offset)."""
    c, lift = float(v["contrast"]), float(v["lift"])
    gains = [1 + 0.5 * v["temp"], 1 - 0.5 * v["tint"], 1 - 0.5 * v["temp"]]
    S = [c * g * (1 - lift) for g in gains]
    O = [((0.5 - 0.5 * c) + v["bright"]) * g * (1 - lift) + lift for g in gains]
    s = float(v["sat"])
    lw = (0.2126, 0.7152, 0.0722)
    M = [[lw[j] * (1 - s) + (s if i == j else 0.0) for j in range(3)] for i in range(3)]
    return S, O, M


def fx_filters(fx: Optional[dict]) -> list[str]:
    """FFmpeg filters for a clip's look (empty when nothing is set)."""
    if not fx:
        return []
    v = fx_values(fx)
    out: list[str] = []
    S, O, M = color_matrix(v)
    if any(abs(a - 1) > 1e-4 for a in S) or any(abs(a) > 1e-4 for a in O) or abs(v["sat"] - 1) > 1e-4:
        out.append("format=gbrp")
        out.append("lutrgb=" + ":".join(f"{ch}='clip(val*{S[i]:.5f}+{O[i] * 255:.3f},0,255)'" for i, ch in enumerate("rgb")))
        if abs(v["sat"] - 1) > 1e-4:
            out.append("colorchannelmixer=" + ":".join(f"{a}{b}={M[i][j]:.5f}" for i, a in enumerate("rgb")
                                                         for j, b in enumerate("rgb")))
    if v["blur"] > 0.01:
        out.append(f"gblur=sigma={v['blur'] * 6:.2f}")
    if v["sharpen"] > 0.01:
        out.append(f"unsharp=5:5:{v['sharpen'] * 1.5:.2f}")
    if v["grain"] > 0.01:
        out.append(f"noise=alls={int(v['grain'] * 28)}:allf=t")
    if v["vignette"] > 0.01:
        out.append(f"vignette=angle={0.18 + v['vignette'] * 0.5:.3f}")
    return out


# ================================================================ drawing: shapes as ASS paths (origin = centre)
def _pt(x: float, y: float) -> str:
    return f"{x:.0f} {y:.0f}"


def _ellipse(rx: float, ry: float, rev: bool = False) -> str:
    k = 0.5523
    p = [(-rx, 0), (-rx, -ry * k), (-rx * k, -ry), (0, -ry), (rx * k, -ry), (rx, -ry * k), (rx, 0),
         (rx, ry * k), (rx * k, ry), (0, ry), (-rx * k, ry), (-rx, ry * k), (-rx, 0)]
    if rev:
        p = p[::-1]
    s = "m " + _pt(*p[0])
    for i in range(1, 13, 3):
        s += " b " + " ".join(_pt(*q) for q in p[i:i + 3])
    return s


def _rect(w: float, h: float, rev: bool = False) -> str:
    p = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    if rev:
        p = p[::-1]
    return "m " + _pt(*p[0]) + " l " + " ".join(_pt(*q) for q in p[1:])


def shape_path(kind: str, w: float, h: float, sw: float) -> str:
    """Path around (0,0) at 8x resolution (use with \\p4)."""
    w, h, sw = w * 8, h * 8, sw * 8
    if kind == "rect":
        return _rect(w, h) + " " + _rect(max(1, w - 2 * sw), max(1, h - 2 * sw), True)
    if kind == "ellipse":
        return _ellipse(w / 2, h / 2) + " " + _ellipse(max(1, w / 2 - sw), max(1, h / 2 - sw), True)
    if kind in ("highlight", "box"):
        return _rect(w, h)
    if kind == "line":
        return _rect(w, sw)
    if kind == "arrow":
        hl, hw = min(w * 0.6, sw * 3.6), sw * 1.9
        p = [(-w / 2, -sw / 2), (w / 2 - hl, -sw / 2), (w / 2 - hl, -hw), (w / 2, 0), (w / 2 - hl, hw),
             (w / 2 - hl, sw / 2), (-w / 2, sw / 2)]
        return "m " + _pt(*p[0]) + " l " + " ".join(_pt(*q) for q in p[1:])
    if kind == "step":
        return _ellipse(h / 2, h / 2)
    return _rect(w, h)


# ================================================================ animations (text and shapes)
def _a(alpha: float) -> str:
    return f"&H{int(round(max(0.0, min(1.0, alpha)) * 255)):02X}&"


def anim_tags(kind: str, d_ms: int, leaving: bool, x: float, y: float, H: int, alphas: tuple[float, float, float]) -> str:
    """Override tags for one entrance (or exit) of `d_ms`. alphas = resting transparency of (fill, outline, shadow).
    Every animation also fades, so nothing pops in on its first frame."""
    a1, a3, a4 = alphas
    vis = f"\\1a{_a(a1)}\\3a{_a(a3)}\\4a{_a(a4)}"
    hid = "\\1a&HFF&\\3a&HFF&\\4a&HFF&"
    off = 0.06 * H
    half = max(1, int(d_ms * 0.6))
    if kind in ("", "none"):
        return f"\\pos({x:.1f},{y:.1f})"
    if not leaving:
        fade = f"{hid}\\t(0,{half},{vis})"
        if kind == "fade":
            return f"\\pos({x:.1f},{y:.1f}){hid}\\t(0,{d_ms},{vis})"
        if kind in ("up", "down", "left", "right"):
            dx, dy = {"up": (0, off), "down": (0, -off), "left": (off, 0), "right": (-off, 0)}[kind]
            return f"\\move({x + dx:.1f},{y + dy:.1f},{x:.1f},{y:.1f},0,{d_ms}){fade}"
        if kind == "pop":
            return (f"\\pos({x:.1f},{y:.1f})\\fscx55\\fscy55\\t(0,{int(d_ms * 0.65)},\\fscx108\\fscy108)"
                    f"\\t({int(d_ms * 0.65)},{d_ms},\\fscx100\\fscy100){fade}")
        if kind == "zoom":
            return f"\\pos({x:.1f},{y:.1f})\\fscx165\\fscy165\\t(0,{d_ms},0.5,\\fscx100\\fscy100){fade}"
        if kind == "blur":
            return f"\\pos({x:.1f},{y:.1f})\\blur14\\t(0,{d_ms},\\blur0){hid}\\t(0,{d_ms},{vis})"
        if kind == "spin":
            return f"\\pos({x:.1f},{y:.1f})\\frz{{R+14}}\\fscx80\\fscy80\\t(0,{d_ms},0.5,\\frz{{R}}\\fscx100\\fscy100){fade}"
        if kind == "drop":
            return f"\\move({x:.1f},{y - 2.2 * off:.1f},{x:.1f},{y:.1f},0,{d_ms}){fade}"
        if kind == "stretch":
            return f"\\pos({x:.1f},{y:.1f})\\fscx0\\t(0,{d_ms},0.5,\\fscx100){fade}"
        if kind == "flip":
            return f"\\pos({x:.1f},{y:.1f})\\fscy0\\t(0,{d_ms},0.5,\\fscy100){fade}"
        if kind == "bounce":
            a, b, c = int(d_ms * 0.45), int(d_ms * 0.7), int(d_ms * 0.85)
            return (f"\\pos({x:.1f},{y:.1f})\\fscx30\\fscy30\\t(0,{a},\\fscx118\\fscy118)\\t({a},{b},\\fscx92\\fscy92)"
                    f"\\t({b},{c},\\fscx104\\fscy104)\\t({c},{d_ms},\\fscx100\\fscy100){hid}\\t(0,{int(d_ms * 0.3)},{vis})")
        if kind == "type":                              # typewriter: the letters themselves are timed in staged()
            return f"\\pos({x:.1f},{y:.1f}){vis}\\2a&HFF&\\4a&HFF&"
        return f"\\pos({x:.1f},{y:.1f}){hid}\\t(0,{d_ms},{vis})"
    start = d_ms - half
    fade = f"{vis}\\t({start},{d_ms},{hid})"
    if kind == "fade":
        return f"\\pos({x:.1f},{y:.1f}){vis}\\t(0,{d_ms},{hid})"
    if kind in ("up", "down", "left", "right"):
        dx, dy = {"up": (0, -off), "down": (0, off), "left": (-off, 0), "right": (off, 0)}[kind]
        return f"\\move({x:.1f},{y:.1f},{x + dx:.1f},{y + dy:.1f},0,{d_ms}){fade}"
    if kind == "pop":
        return f"\\pos({x:.1f},{y:.1f})\\t(0,{d_ms},\\fscx55\\fscy55){fade}"
    if kind == "zoom":
        return f"\\pos({x:.1f},{y:.1f})\\t(0,{d_ms},2,\\fscx165\\fscy165){fade}"
    if kind == "blur":
        return f"\\pos({x:.1f},{y:.1f}){vis}\\t(0,{d_ms},\\blur14{hid})"
    if kind == "spin":
        return f"\\pos({x:.1f},{y:.1f})\\frz{{R}}\\t(0,{d_ms},2,\\frz{{R-14}}\\fscx80\\fscy80){fade}"
    if kind == "drop":
        return f"\\move({x:.1f},{y:.1f},{x:.1f},{y + 2.2 * off:.1f},0,{d_ms}){fade}"
    if kind == "stretch":
        return f"\\pos({x:.1f},{y:.1f})\\t(0,{d_ms},2,\\fscx0){fade}"
    if kind == "flip":
        return f"\\pos({x:.1f},{y:.1f})\\t(0,{d_ms},2,\\fscy0){fade}"
    return f"\\pos({x:.1f},{y:.1f}){vis}\\t(0,{d_ms},{hid})"


LOOPS = {"pulse": 0.9, "heartbeat": 1.2, "wiggle": 0.5, "swing": 1.6, "blink": 0.8}


def loop_tags(kind: str, dur: float, rot: float, alphas: tuple[float, float, float]) -> str:
    """Tags that keep an element moving while it is on screen (repeated \\t steps, straight lines between them:
    the preview draws the same zig-zag)."""
    P = LOOPS.get(kind)
    if not P or dur < P:
        return ""
    steps: list[tuple[float, str]]
    r = -rot
    if kind == "pulse":
        steps = [(0.5, "\\fscx106\\fscy106"), (1.0, "\\fscx100\\fscy100")]
    elif kind == "heartbeat":
        steps = [(0.1, "\\fscx112\\fscy112"), (0.25, "\\fscx100\\fscy100"), (0.35, "\\fscx108\\fscy108"), (0.5, "\\fscx100\\fscy100")]
    elif kind in ("wiggle", "swing"):
        amp = 3.0 if kind == "wiggle" else 7.0
        steps = [(0.25, f"\\frz{r + amp:.2f}"), (0.75, f"\\frz{r - amp:.2f}"), (1.0, f"\\frz{r:.2f}")]
    else:                                               # blink
        dim = "".join(f"\\{n}a{_a(1 - (1 - al) * 0.3)}" for n, al in zip("134", alphas))
        full = "".join(f"\\{n}a{_a(al)}" for n, al in zip("134", alphas))
        steps = [(0.5, dim), (1.0, full)]
    out, n = [], min(150, int(dur / P))
    for i in range(n):
        prev = 0.0
        for frac, tags in steps:
            out.append(f"\\t({int((i + prev) * P * 1000)},{int((i + frac) * P * 1000)},{tags})")
            prev = frac
    return "".join(out)


class Ass:
    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.styles = ["Style: D,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"]
        self.events: list[str] = []

    def ev(self, a: float, b: float, style: str, text: str, layer: int = 0) -> None:
        if b - a >= 0.02:
            self.events.append(f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{text}")

    def staged(self, a: float, b: float, style: str, body: str, x: float, y: float, rot: float, e: dict,
               alphas: tuple[float, float, float], layer: int, extra: str = "", drawing: bool = False) -> None:
        """One element as up to three events: entrance, hold, exit."""
        d = b - a
        ain, aout = (e.get("anim_in") or {}), (e.get("anim_out") or {})
        di = min(float(ain.get("dur") or 0.4), d / 2) if ain.get("type") not in (None, "", "none") else 0.0
        do = min(float(aout.get("dur") or 0.4), d / 2) if aout.get("type") not in (None, "", "none") else 0.0
        base = f"\\an{7 if drawing else 5}{extra}" + (f"\\frz{-rot:.2f}" if abs(rot) > 0.01 else "")
        fin = "\\p4}" if drawing else "}"
        rest = f"\\1a{_a(alphas[0])}\\3a{_a(alphas[1])}\\4a{_a(alphas[2])}"

        def tags(kind, ms, leaving):
            return (anim_tags(kind, ms, leaving, x, y, self.H, alphas).replace("{R+14}", f"{-rot + 14:.2f}")
                    .replace("{R-14}", f"{-rot - 14:.2f}").replace("{R}", f"{-rot:.2f}"))
        if di > 0:
            first = body
            if ain["type"] == "type" and not drawing:          # letter by letter, evenly over the entrance
                chars = [c for c in body.replace("\\N", "\n")]
                n = max(1, sum(1 for c in chars if c != "\n"))
                cs, used, first = di * 100 / n, 0, ""
                for i, c in enumerate([c for c in chars]):
                    if c == "\n":
                        first += "\\N"
                        continue
                    k = int(round(cs * (sum(1 for q in chars[:i + 1] if q != "\n")))) - used
                    used += k
                    first += "{\\ko" + str(max(0, k)) + "}" + c
            self.ev(a, a + di, style, "{" + base + tags(ain["type"], int(di * 1000), False) + fin + first, layer)
        loop = loop_tags(str((e.get("anim_loop") or {}).get("type") or ""), b - do - a - di, rot, alphas)
        self.ev(a + di, b - do, style, "{" + base + f"\\pos({x:.1f},{y:.1f}){rest}{loop}" + fin + body, layer)
        if do > 0:
            self.ev(b - do, b, style, "{" + base + tags(aout["type"], int(do * 1000), True) + fin + body, layer)

    def render(self) -> str:
        return ("[Script Info]\nScriptType: v4.00+\n"
                f"PlayResX: {self.W}\nPlayResY: {self.H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
                "YCbCr Matrix: TV.709\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n" + "\n".join(self.styles)
                + "\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                + "\n".join(self.events) + "\n")


def _em(family: str) -> float:
    fn = fonts.FONT_SOURCES.get(family, ("", ""))[0]
    return float((fonts._metrics().get(fn) or {}).get("em") or 0.8)


def _text_style(ass: Ass, name: str, e: dict) -> tuple[float, float, float]:
    """Add a style for a text element; returns its resting alphas."""
    H = ass.H
    fam = str(e.get("font") or "Poppins")
    px = float(e.get("size") or 0.06) * H                        # size is the CSS font size as a share of the height
    k = H / 1080
    box = bool(e.get("box"))
    stroke = float(e.get("stroke") or 0) * k
    shadow = float(e.get("shadow") or 0) * k
    if box:
        outline_c, a3 = str(e.get("box_color") or "#000000"), 1 - float(e.get("box_alpha", 0.6))
        bord = float(e.get("box_pad", 14)) * k
    else:
        outline_c, a3, bord = str(e.get("stroke_color") or "#000000"), 0.0, stroke
    a4 = 0.55 if shadow > 0 else 1.0
    ass.styles.append(
        f"Style: {name},{fonts.resolve(fam)},{px / _em(fam):.1f},{col(str(e.get('color') or '#FFFFFF'), 0, True)},"
        f"&H00FFFFFF,{col(outline_c, a3, True)},{col('#000000', a4, True)},{-1 if e.get('bold') else 0},"
        f"{-1 if e.get('italic') else 0},0,0,100,100,{float(e.get('spacing') or 0) * k:.1f},0,{3 if box else 1},"
        f"{bord:.1f},{0 if box else shadow:.1f},5,0,0,0,1")
    return 0.0, a3, a4


def _txt(e: dict) -> str:
    t = str(e.get("text") or "")
    if e.get("upper"):
        t = t.upper()
    return "\\N".join(esc(line) for line in t.split("\n"))


def overlay_ass(p: dict, pinned: bool, W: int, H: int) -> str:
    """Text, shapes and captions of the project as ASS ('' when there is nothing to draw). pinned=False are the
    elements that zoom with the picture; pinned=True stay put (titles, captions)."""
    ass = Ass(W, H)
    k = H / 1080
    n = 0
    for e in sorted(p.get("els") or [], key=lambda x: (x.get("lane") or 0, x["start"])):
        kind = e.get("kind")
        a, b = float(e["start"]), float(e["start"]) + float(e.get("dur") or 0)
        if kind == "caption":
            if not pinned:
                continue
            st = {**(p.get("captions") or {}), "text": e.get("text") or ""}
            n += 1
            al = _text_style(ass, f"C{n}", st)
            ass.staged(a, b, f"C{n}", _txt(st), 0.5 * W, float(st.get("y", 0.86)) * H, 0.0,
                       {"anim_in": st.get("anim_in")}, al, 50)
            continue
        if kind not in ("text", "shape") or bool(e.get("pin")) != pinned:
            continue
        x, y, rot = float(e.get("x", 0.5)) * W, float(e.get("y", 0.5)) * H, float(e.get("rot") or 0)
        n += 1
        layer = 10 + int(e.get("lane") or 0)
        if kind == "text":
            al = _text_style(ass, f"T{n}", e)
            ass.staged(a, b, f"T{n}", _txt(e), x, y, rot, e, al, layer)
        else:
            sh = str(e.get("shape") or "rect")
            if sh == "blur":
                continue                                           # a video filter, not a drawing
            w, h = float(e.get("w", 0.2)) * W, float(e.get("h", 0.2)) * H
            sw = float(e.get("width") or 6) * k
            alpha = 1 - float(e.get("alpha", 0.45 if sh == "highlight" else 1.0))
            ass.staged(a, b, "D", shape_path(sh, w, h, sw) + "{\\p0}", x, y, rot, e, (alpha, 1.0, 1.0), layer,
                       extra=f"\\1c{col(str(e.get('color') or '#FF3D6E'))}", drawing=True)
            if sh == "step":
                n += 1
                num = {"text": str(e.get("n") or 1), "font": "Poppins Black", "size": float(e.get("h", 0.08)) * 0.52,
                       "color": str(e.get("text_color") or "#FFFFFF")}
                al = _text_style(ass, f"T{n}", num)
                ass.staged(a, b, f"T{n}", _txt(num), x, y, 0.0, e, al, layer + 1)
    return ass.render() if ass.events else ""


# ================================================================ cursor effects (drawn on the screen clip itself)
def load_events(path: str, offset: float = 0.0) -> dict:
    """{clicks: [[t, x, y, button]], moves: [[t, x, y]] (about 12 a second), keys: [t]} in video time."""
    out = {"clicks": [], "moves": [], "keys": []}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    last = -1.0
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if "t" not in e:
            continue
        t = round(float(e["t"]) + offset, 3)
        k = e.get("k")
        if k == "click" and "x" in e:
            out["clicks"].append([t, e["x"], e["y"], e.get("b", "l")])
            out["moves"].append([t, e["x"], e["y"]])
            last = t
        elif k == "move" and "x" in e and t - last >= 0.08:
            out["moves"].append([t, e["x"], e["y"]])
            last = t
        elif k == "key":
            out["keys"].append(t)
    return out


def cursor_ass(cur: dict, ev: dict, s0: float, s1: float, w: int, h: int) -> str:
    """Click ripples / cursor highlight / spotlight for the source window [s0, s1] of the screen clip, timed from
    the start of that window. '' when nothing is switched on or nothing happens in the window."""
    ass = Ass(w, h)
    if cur.get("ripple", True):
        r = 0.024 * h
        ring = "{\\p0}".join(["{\\p4}" + _ellipse(r * 8, r * 8) + " " + _ellipse(r * 8 * 0.78, r * 8 * 0.78, True), ""])
        c = col(str(cur.get("ripple_color") or "#FFD400"))
        for t, x, y, _b in ev["clicks"]:
            if s0 - 0.6 <= t <= s1:
                ass.ev(t - s0, t - s0 + 0.55, "D", "{\\an7\\pos(%.1f,%.1f)\\1c%s\\1a&H10&\\fscx45\\fscy45"
                       "\\t(0,550,0.6,\\fscx230\\fscy230\\1a&HFF&)}" % (x * w, y * h, c) + ring, 5)
    follow = []
    if cur.get("highlight"):
        r = 0.034 * h * float(cur.get("size") or 1)
        follow.append(("{\\p4}" + _ellipse(r * 8, r * 8) + "{\\p0}",
                       f"\\1c{col(str(cur.get('highlight_color') or '#FFD400'))}\\1a&H8C&", 3))
    if cur.get("spotlight"):
        r = 0.2 * h * float(cur.get("size") or 1)
        big = max(w, h) * 3 * 8
        follow.append(("{\\p4}" + _rect(big, big) + " " + _ellipse(r * 8, r * 8, True) + "{\\p0}",
                       "\\1c&H000000&\\1a&H70&\\blur6", 2))
    if follow:
        mv = [m for m in ev["moves"] if s0 - 1 <= m[0] <= s1 + 1]
        for i, m in enumerate(mv):
            a = m[0] - s0
            nx = mv[i + 1] if i + 1 < len(mv) else None
            b = (nx[0] - s0) if nx else (s1 - s0)
            if b <= 0 or a >= s1 - s0:
                continue
            x, y = m[1] * w, m[2] * h
            for body, tags, layer in follow:
                if nx and b - a <= 0.25:                         # moving: glide to the next position
                    ass.ev(a, b, "D", "{\\an7\\move(%.1f,%.1f,%.1f,%.1f)%s}" % (x, y, nx[1] * w, nx[2] * h, tags) + body, layer)
                else:                                              # resting: stay, then glide in the last moment
                    hold = b - 0.1 if nx else b
                    ass.ev(a, hold, "D", "{\\an7\\pos(%.1f,%.1f)%s}" % (x, y, tags) + body, layer)
                    if nx:
                        ass.ev(hold, b, "D", "{\\an7\\move(%.1f,%.1f,%.1f,%.1f)%s}" % (x, y, nx[1] * w, nx[2] * h, tags) + body, layer)
    return ass.render() if ass.events else ""


# ================================================================ clip transitions (enter / exit)
def enter_exit(it: dict, dur: float, W: int, H: int) -> tuple[str, str, bool]:
    """(extra x offset expr, extra y offset expr, needs animated scale) for an item's entrance and exit. The
    expressions use overlay's `t`, which is timeline time."""
    st = float(it["start"])
    xs, ys, zoom = [], [], False
    for key, leaving in (("enter", False), ("exit", True)):
        tr = it.get(key) or {}
        kind, d = str(tr.get("type") or ""), min(float(tr.get("dur") or 0.5), dur / 2)
        if kind in ("", "none", "fade", "flash", "dip") or d <= 0:
            continue
        # p: 1 -> 0 while entering (eased), 0 -> 1 while leaving
        if not leaving:
            p = f"pow(clip(1-(t-{st:.3f})/{d:.3f},0,1),2)"
        else:
            p = f"pow(clip((t-{st + dur - d:.3f})/{d:.3f},0,1),2)"
        if kind in ("zoom", "grow"):
            zoom = True
            continue
        sign = {"left": (1, 0), "right": (-1, 0), "up": (0, 1), "down": (0, -1)}.get(kind.replace("slide-", ""), (0, 0))
        if leaving:
            sign = (-sign[0], -sign[1])
        if sign[0]:
            xs.append(f"{sign[0] * W}*{p}")
        if sign[1]:
            ys.append(f"{sign[1] * H}*{p}")
    if str(it.get("motion") or "") in ("push", "pull"):
        zoom = True
    return ("+" + "+".join(xs)) if xs else "", ("+" + "+".join(ys)) if ys else "", zoom


def scale_anim(it: dict, dur: float, t: str = "t") -> str:
    """Size multiplier expression for a 'zoom' entrance/exit; `t` is the name of the clip's own clock (0 = start)."""
    parts = []
    for key, leaving in (("enter", False), ("exit", True)):
        tr = it.get(key) or {}
        d = min(float(tr.get("dur") or 0.5), dur / 2)
        amp = {"zoom": 0.3, "grow": -0.45}.get(str(tr.get("type") or ""))
        if amp is None or d <= 0:
            continue
        parts.append(f"{amp}*pow(clip(1-{t}/{d:.3f},0,1),2)" if not leaving else f"{amp}*pow(clip(({t}-{dur - d:.3f})/{d:.3f},0,1),2)")
    mo = str(it.get("motion") or "")
    if mo == "push":                                    # slow push-in over the whole clip
        parts.append(f"0.12*clip({t}/{dur:.3f},0,1)")
    elif mo == "pull":
        parts.append(f"0.12*(1-clip({t}/{dur:.3f},0,1))")
    return "(1+" + "+".join(parts) + ")" if parts else "1"
