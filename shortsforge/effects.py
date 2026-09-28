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
    "flash": "White Flash",
    "fade_black": "Fade From Black",
    "shake": "Impact Shake",
    "fade_white": "Fade From White",
}

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


def motion_filters(motion: str, dur: float, punch_times: list[float], intro: str, fps: int = 30) -> str | None:
    """Sub-pixel smooth camera motion as a `perspective` filter (fixed output size, no jitter).

    The zoom factor z(t) picks a window of size W/z x H/z (plus an offset) in the frame and stretches it to
    the full frame with cubic interpolation, so zooms glide instead of stepping in whole pixels."""
    D = max(dur, 0.1)
    t = f"(in/{fps})"
    dx = dy = "0"
    if motion == "slow_zoom":
        z = f"(1+0.10*{t}/{D:.2f})"
    elif motion == "zoom_out":
        z = f"(1.12-0.10*{t}/{D:.2f})"
    elif motion == "ken_burns":
        z = f"(1.04+0.08*{t}/{D:.2f})"
        dx = f"(W-W/{z})/2*0.6*({t}/{D:.2f}-0.5)*2"
    elif motion == "punch":
        pts = [p for p in punch_times if 0.5 < p < D - 0.5][:16]
        if not pts:
            pts = [D * k / 6 for k in range(1, 6)]
        terms = []
        for i, p in enumerate(pts):
            nxt = pts[i + 1] if i + 1 < len(pts) else D
            if i % 2 == 0:  # zoom in on this key word, ease back out at the next one
                terms.append(f"clip(({t}-{p:.2f})/0.14,0,1)*clip(({nxt:.2f}-{t})/0.2,0,1)")
        z = "(1.02+0.12*(" + ("+".join(terms) or "0") + "))"
    elif motion == "breathe":
        z = f"(1.035+0.025*sin({t}*2.2))"
    elif motion in ("pan_left", "pan_right"):
        z = "1.1"
        sgn = -1 if motion == "pan_left" else 1
        dx = f"{sgn}*(W-W/1.1)/2*0.9*(2*{t}/{D:.2f}-1)"
    elif motion == "drift_up":
        z = "1.08"
        dy = f"-(H-H/1.08)/2*0.9*(2*{t}/{D:.2f}-1)"
    elif motion == "zoom_pulse":
        z = f"(1.04+0.03*pow(abs(sin({t}*3.14159*1.0)),6))"
    elif motion == "sway":
        z = "1.08"
        dx = f"(W-W/1.08)/2*0.8*sin({t}*0.55)"
        dy = f"(H-H/1.08)/2*0.6*sin({t}*0.37+1)"
    else:
        if intro != "shake":
            return None
        z = "1.05"
    if intro == "shake":
        dx = f"({dx})+16*sin({t}*95)*lt({t},0.45)"
        dy = f"({dy})+12*cos({t}*83)*lt({t},0.45)"
    x0 = f"(W-W/{z})/2+{dx}"
    x1 = f"(W+W/{z})/2+{dx}"
    y0 = f"(H-H/{z})/2+{dy}"
    y1 = f"(H+H/{z})/2+{dy}"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}'"
            f":interpolation=cubic:eval=frame")
