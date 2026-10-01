"""Visual effects: color grades, camera motion, intro flashes (pure FFmpeg filters)."""
from __future__ import annotations

COLOR_GRADES: dict[str, dict] = {
    "none": dict(name="Original", vf=""),
    "vibrant": dict(name="Vibrant", vf="eq=saturation=1.35:contrast=1.06:gamma=1.02"),
    "cinematic": dict(name="Cinematic Teal & Orange",
                      vf="colorbalance=rs=-0.06:gs=-0.01:bs=0.09:rh=0.09:gh=0.01:bh=-0.07,"
                         "eq=contrast=1.1:saturation=1.12,vignette=angle=PI/5"),
    "warm": dict(name="Warm Sunset", vf="colorbalance=rs=0.05:bs=-0.05:rm=0.08:bm=-0.08:rh=0.04,eq=saturation=1.18"),
    "cool": dict(name="Cool Blue", vf="colorbalance=rs=-0.04:bs=0.06:rm=-0.06:bm=0.08,eq=saturation=1.08:contrast=1.05"),
    "bw": dict(name="Black & White Punch", vf="hue=s=0,eq=contrast=1.28:brightness=0.02"),
    "vintage": dict(name="Vintage Film", vf="curves=preset=vintage,noise=alls=9:allf=t,vignette=angle=PI/4.5"),
    "moody": dict(name="Moody Dark", vf="eq=brightness=-0.03:contrast=1.16:saturation=0.85,vignette=angle=PI/4"),
    "hdr": dict(name="Crisp HDR Pop", vf="unsharp=5:5:0.7:5:5:0.0,eq=saturation=1.25:contrast=1.08"),
    "dream": dict(name="Soft Dream", vf="eq=brightness=0.03:saturation=1.1:contrast=0.96,"
                                        "colorbalance=rh=0.04:bh=0.05"),
    "teal": dict(name="Teal Pop", vf="colorbalance=rs=-0.08:bs=0.1:rm=-0.05:bm=0.08,eq=saturation=1.1:contrast=1.05"),
    "golden": dict(name="Golden Hour", vf="colorbalance=rs=0.08:gs=0.03:bs=-0.1:rm=0.06:bm=-0.06,eq=saturation=1.15:gamma=1.03"),
    "matte": dict(name="Matte Film", vf="curves=all='0/0.06 0.5/0.5 1/0.94',eq=saturation=0.9"),
    "cyberpunk": dict(name="Cyberpunk", vf="colorbalance=rs=0.1:gs=-0.08:bs=0.15:rh=0.05:bh=0.1,eq=saturation=1.3:contrast=1.1"),
    "noir": dict(name="Noir", vf="hue=s=0,eq=contrast=1.45:brightness=-0.03,vignette=angle=PI/3.5"),
    "sepia": dict(name="Sepia", vf="colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,eq=contrast=1.05"),
    "pastel": dict(name="Pastel", vf="eq=saturation=0.75:brightness=0.05:contrast=0.92,colorbalance=rh=0.03:bh=0.04"),
    "night": dict(name="Night Blue", vf="colorbalance=rs=-0.05:bs=0.12:bm=0.08,eq=brightness=-0.06:saturation=0.85:contrast=1.1"),
    "sunrise": dict(name="Sunrise", vf="colorbalance=rs=0.1:gs=0.02:bs=-0.06:rh=0.06,eq=saturation=1.2:brightness=0.02"),
    "punchy": dict(name="Extra Punchy", vf="eq=saturation=1.5:contrast=1.15,unsharp=5:5:0.6:5:5:0.0"),
    "faded": dict(name="Faded", vf="curves=all='0/0.1 1/0.9',eq=saturation=0.8"),
    "emerald": dict(name="Emerald", vf="colorbalance=rs=-0.04:gs=0.08:gm=0.06,eq=saturation=1.15"),
}

MOTIONS: dict[str, str] = {
    "none": "Static",
    "ken_burns": "Ken Burns (zoom + drift)",
    "slow_zoom": "Slow Zoom In",
    "zoom_out": "Slow Zoom Out",
    "punch": "Punch Zooms (on key words)",
    "breathe": "Breathing Pulse",
    "sway": "Handheld Sway",
    "pan_left": "Slow Pan Left",
    "pan_right": "Slow Pan Right",
    "drift_up": "Drift Up",
    "zoom_pulse": "Beat Pulse",
}

INTROS: dict[str, str] = {
    "none": "None",
    "zoom_slam": "Zoom Slam (starts close, snaps back)",
    "punch_in": "Punch In",
    "whip": "Whip Pan",
    "rgb_glitch": "RGB Glitch",
    "shake": "Impact Shake",
    "flash": "White Flash",
    "fade_black": "Fade From Black",
    "fade_white": "Fade From White",
}

FOCUS_RISE, FOCUS_HOLD, FOCUS_FALL = 0.16, 1.35, 0.35

LAYOUTS: dict[str, str] = {
    "auto": "Auto (face → smart crop, else blur fit)",
    "smart_crop": "Smart Crop (follows face)",
    "blur_fit": "Full Frame + Blurred Background",
    "center_crop": "Center Crop",
    "split": "Split: Speaker + Full Frame",
    "split_reverse": "Split: Full Frame + Speaker",
    "two_speakers": "Two Speakers (podcast, stacked)",
    "zoom45": "4:5 Zoom + Blurred Background",
    "square": "Square + Blurred Background",
    "framed": "Framed Card",
    "black_fit": "Full Frame + Black Bars",
}

# layouts that follow the speaker's face (need face tracking)
CAMERA_LAYOUTS = ("auto", "smart_crop", "split", "split_reverse", "zoom45", "square")
# layouts that crop a landscape frame (a vertical source just gets the blurred fit)
CROP_LAYOUTS = ("smart_crop", "center_crop", "split", "split_reverse", "two_speakers", "zoom45", "square")


def focus_windows(punches: list, dur: float) -> list[tuple[float, float, float]]:
    """Punch/focus zooms as (start, end, amount): zoom in fast on the word, hold, ease back out before the next.
    The live preview (web/src/lib/fx.ts) uses exactly the same numbers."""
    norm = [(p, 0.12) if isinstance(p, (int, float)) else (p[0], p[1]) for p in punches or []]
    pts = sorted((float(t), float(a)) for t, a in norm if 0.3 <= float(t) < dur - 0.4)
    out = []
    for i, (t, amt) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else dur
        end = min(t + FOCUS_RISE + FOCUS_HOLD, nxt - 0.05, dur)
        if end - t >= 0.3:
            out.append((round(t, 3), round(end, 3), round(amt, 4)))
    return out[:40]


def _smooth(x: str) -> str:
    return f"(({x})*({x})*(3-2*({x})))"


FREEZE_PUSH, FREEZE_BACK, STREAK_Z, STREAK_W = 0.075, 0.22, 0.07, 0.2


def motion_exprs(motion: str, dur: float, punches: list, intro: str, fps: int = 30, at: float | None = None,
                 story: dict | None = None):
    """(z, dx, dy) as FFmpeg expressions of t. dx/dy are fractions of the frame width/height.
    `at` = evaluate for one fixed time (exact-frame stills). `story` (Story FX, final timeline): the camera keeps
    pushing in through every freeze and snaps back after it; every streak gets a quick whip zoom.
    Mirrored number for number in web/src/lib/fx.ts (motionAt)."""
    D = max(dur, 0.1)
    t = f"(in/{fps})" if at is None else f"({at:.4f})"
    dx = dy = "0"
    z = "1"
    if motion == "slow_zoom":
        z = f"(1+0.10*{t}/{D:.2f})"
    elif motion == "zoom_out":
        z = f"(1.12-0.10*{t}/{D:.2f})"
    elif motion == "ken_burns":
        z = f"(1.04+0.08*{t}/{D:.2f})"
        dx = f"(1-1/{z})/2*0.6*({t}/{D:.2f}-0.5)*2"
    elif motion == "breathe":
        z = f"(1.035+0.025*sin({t}*2.2))"
    elif motion in ("pan_left", "pan_right"):
        z = "1.1"
        sgn = -1 if motion == "pan_left" else 1
        dx = f"{sgn}*(1-1/1.1)/2*0.9*(2*{t}/{D:.2f}-1)"
    elif motion == "drift_up":
        z = "1.08"
        dy = f"-(1-1/1.08)/2*0.9*(2*{t}/{D:.2f}-1)"
    elif motion == "zoom_pulse":
        z = f"(1.04+0.03*pow(abs(sin({t}*3.14159*1.0)),6))"
    elif motion == "sway":
        z = "1.08"
        dx = f"(1-1/1.08)/2*0.8*sin({t}*0.55)"
        dy = f"(1-1/1.08)/2*0.6*sin({t}*0.37+1)"
    terms = []
    for a, e, amt in focus_windows(punches, D):
        r = _smooth(f"clip(({t}-{a:.3f})/{FOCUS_RISE},0,1)")
        f = _smooth(f"clip(({e:.3f}-{t})/{FOCUS_FALL},0,1)")
        terms.append(f"{amt:.4f}*min({r},{f})")
    extra = list(terms)
    for a, b in (story or {}).get("spans") or []:
        d = max(0.05, b - a)
        push = _smooth(f"clip(({t}-{a:.3f})/{d:.3f},0,1)")
        back = _smooth(f"clip(({b + FREEZE_BACK:.3f}-{t})/{FREEZE_BACK},0,1)")
        extra.append(f"{FREEZE_PUSH}*{push}*{back}")
    for T in (story or {}).get("streaks") or []:
        extra.append(f"{STREAK_Z}*pow(max(0,1-abs({t}-{T:.3f})/{STREAK_W}),2)")
        dx = f"({dx})+0.018*pow(max(0,1-abs({t}-{T:.3f})/{STREAK_W}),2)*({t}-{T:.3f})/{STREAK_W}"
    if intro == "zoom_slam":
        extra.append(f"0.38*(1-{_smooth(f'clip({t}/0.42,0,1)')})")
    elif intro == "punch_in":
        extra.append(f"0.13*min(clip({t}/0.1,0,1),{_smooth(f'clip((0.62-{t})/0.5,0,1)')})")
    elif intro == "whip":
        extra.append(f"0.22*(1-{_smooth(f'clip({t}/0.32,0,1)')})")
        dx = f"({dx})+0.085*(1-{_smooth(f'clip({t}/0.32,0,1)')})"
    elif intro == "rgb_glitch":
        extra.append("0.06*lt(" + t + ",0.5)")
        dx = f"({dx})+0.009*sin({t}*170)*lt({t},0.5)"
    elif intro == "shake":
        extra.append("0.05*lt(" + t + ",0.45)")
        dx = f"({dx})+0.0148*sin({t}*95)*lt({t},0.45)"
        dy = f"({dy})+0.00625*cos({t}*83)*lt({t},0.45)"
    if extra:
        z = f"({z})*(1+" + "+".join(extra) + ")"
    if z == "1" and dx == "0" and dy == "0":
        return None
    return z, dx, dy


def motion_filters(motion: str, dur: float, punches: list, intro: str, fps: int = 30,
                   at: float | None = None, story: dict | None = None) -> str | None:
    """Sub-pixel smooth camera motion as a `perspective` filter (fixed output size, no jitter).

    The zoom factor z(t) picks a window of size W/z x H/z (plus an offset) in the frame and stretches it to
    the full frame with cubic interpolation, so zooms glide instead of stepping in whole pixels. The window
    is kept inside the frame (offsets are clamped), so no edges ever show. Works at any resolution, so the
    renderer runs it on the small source crop before upscaling (much faster)."""
    ex = motion_exprs(motion, dur, punches, intro, fps, at, story)
    if not ex:
        return None
    z, dx, dy = ex
    hx = f"(W-W/({z}))/2"
    hy = f"(H-H/({z}))/2"
    ox = f"max(-{hx},min({hx},({dx})*W))"
    oy = f"max(-{hy},min({hy},({dy})*H))"
    x0, x1 = f"{hx}+{ox}", f"W-{hx}+{ox}"
    y0, y1 = f"{hy}+{oy}", f"H-{hy}+{oy}"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}'"
            f":interpolation=cubic:eval={'frame' if at is None else 'init'}")


def intro_overlay(intro: str) -> str | None:
    """Colour/flash part of an opening (runs after captions)."""
    if intro == "flash":
        return "fade=t=in:st=0:d=0.35:color=white"
    if intro == "fade_white":
        return "fade=t=in:st=0:d=0.7:color=white"
    if intro == "fade_black":
        return "fade=t=in:st=0:d=0.4"
    if intro == "rgb_glitch":
        return "rgbashift=rh=-14:bh=14:gv=4:enable='lt(t,0.5)*lt(mod(t,0.14),0.08)'"
    if intro == "zoom_slam":
        return "fade=t=in:st=0:d=0.12:color=white"
    return None
