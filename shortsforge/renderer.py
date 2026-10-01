"""Build and run the FFmpeg graph that turns a time range into a finished vertical Short."""
from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import fonts
from .effects import COLOR_GRADES, intro_overlay, motion_filters
from .facetrack import x_expression
from .pacing import select_expr
from .utils import encoder_args, ffmpeg_cwd, filter_path, pick_encoder, pick_hevc_encoder, run_ffmpeg

W, H = 1080, 1920


@dataclass
class RenderJob:
    src: str
    start: float
    end: float
    out_path: str
    src_w: int
    src_h: int
    ass_file: str
    layout: str = "blur_fit"              # smart_crop | blur_fit | center_crop | split
    camera: list = field(default_factory=list)   # [(t, x_px)] for smart_crop / split
    grade: str = "none"
    motion: str = "none"
    intro: str = "none"
    punch_times: list = field(default_factory=list)
    progress_bar: bool = True
    accent: str = "#FFE400"
    has_audio: bool = True
    loudnorm: bool = True
    music: str = ""
    music_volume: float = 0.12
    music_auto: bool = True          # auto-level the track against the voice (loudness matched)
    music_offset: float = 0.0        # start this many seconds into the track
    fps: int = 30
    crf: int = 20
    encoder: str = "auto"
    speed: str = "fast"
    out_h: int = 1920                    # export height (720 / 1080p=1920 / 1440p=2560 / 4K=3840); width follows 9:16
    codec: str = "h264"                  # h264 (plays everywhere) | hevc (smaller files)
    keep: list = field(default_factory=list)     # jump-cut ranges [(a, b)] relative to start; empty = all
    sfx: str = ""                                # pre-mixed sound-effects WAV
    # stitched Shorts: [(abs_start, abs_end, keep_ranges_relative)] in playback order. Empty = one piece.
    parts: list = field(default_factory=list)
    # manual framing from the placement editor: shift (-1..1 of the free room) and zoom (1 = none)
    frame_x: float = 0.0
    frame_y: float = 0.0
    frame_zoom: float = 1.0
    # fast mode: `src` is a downloaded section that starts at this absolute source time
    src_offset: float = 0.0
    # Story FX (see story.py): freezes [{t (cut timeline), d}], and the final-timeline plan
    # {spans, streaks, leaks, texture}; story_ass = editorial title + beat text; mask_* = speaker cut-out frames
    # for the title window (PNG sequence at the output fps), so the title can sit behind the speaker
    freezes: list = field(default_factory=list)
    story: dict = field(default_factory=dict)
    story_ass: str = ""
    mask_pattern: str = ""
    mask_n: int = 0
    title_win: tuple = ()

    def pieces(self) -> list:
        return [tuple(p) for p in self.parts] if self.parts else [(self.start, self.end, self.keep)]

    @property
    def cut_duration(self) -> float:
        """Length after jump cuts and stitching, before Story FX freezes."""
        total = 0.0
        for a, b, keep in self.pieces():
            total += sum(y - x for x, y in keep) if keep else (b - a)
        return max(0.1, total)

    @property
    def duration(self) -> float:
        """Length of the finished Short (after jump cuts, stitching and freezes)."""
        return self.cut_duration + sum(self._frozen())

    def _frozen(self) -> list:
        return [max(1, round(float(f["d"]) * self.fps)) / self.fps for f in self.freezes or []]


def _en(windows: list) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)


def freeze_video(job: "RenderJob") -> str:
    """loop filters that hold one frame at each freeze (the rest of the Short moves later)."""
    out, added = [], 0
    for f in sorted(job.freezes or [], key=lambda x: x["t"]):
        n = max(1, round(float(f["d"]) * job.fps))
        start = int(round(float(f["t"]) * job.fps)) + added
        out.append(f"loop=loop={n}:size=1:start={start}")
        added += n
    return (",".join(out) + ",setpts=N/FRAME_RATE/TB") if out else ""


def post_fx(job: "RenderJob", g: list, cur: str) -> str:
    """Story FX on the framed picture: freeze look, streak blur, film texture. Returns the new label."""
    st = job.story or {}
    spans = [tuple(x) for x in st.get("spans") or []]
    chain = []
    if spans:
        en = _en(spans)
        flash = _en([(b, b + 0.05) for _a, b in spans])
        chain += [f"eq=saturation=0.38:brightness=-0.045:contrast=1.06:enable='{en}'",
                  f"vignette=angle=PI/3.2:enable='{en}'", f"eq=brightness=0.3:enable='{flash}'"]
    streaks = st.get("streaks") or []
    if streaks:
        inner = _en([(t - 0.05, t + 0.07) for t in streaks])
        outer = _en([(t - 0.13, t + 0.16) for t in streaks])
        chain += [f"avgblur=sizeX=22:sizeY=1:enable='{outer}'", f"avgblur=sizeX=56:sizeY=1:enable='{inner}'"]
    if chain:
        g.append(f"[{cur}]{','.join(chain)}[sfx1]")
        cur = "sfx1"
    tex = st.get("texture") or None
    if tex:
        D = job.duration
        bloom = float(tex.get("bloom") or 0)
        if bloom > 0.01:
            g.append(f"[{cur}]format=yuv420p,split=2[blA][blB]")
            g.append(f"[blB]scale=270:480:flags=bilinear,gblur=sigma=9,scale={W}:{H}:flags=bicubic[blG]")
            g.append(f"[blA][blG]blend=c0_mode=screen:c0_opacity={bloom:.3f}:c1_mode=normal:c1_opacity=1:"
                     f"c2_mode=normal:c2_opacity=1[blm]")
            cur = "blm"
        leaks = st.get("leaks") or []
        if tex.get("leak") and leaks:
            # a short warm light leak after the opening and after each beat: rendered only for its 2.4 s window
            # (tiny frame, scaled up) and laid over the picture there, so it costs almost nothing
            for i, L in enumerate(leaks[:4]):
                a = ("255*0.5*exp(-pow((T-0.8)/0.7,2))*exp(-(pow(X-W*(0.8-0.1*sin((T+" + f"{L:.2f}" + ")*0.9)),2)"
                     "/(2*pow(W*0.42,2))+pow(Y-H*0.17,2)/(2*pow(H*0.3,2))))")
                g.append(f"color=c=black:s=108x192:r={job.fps}:d=2.4,format=rgba,"
                         f"geq=r='255':g='150':b='72':a='{a}',scale={W}:{H}:flags=bilinear,"
                         f"setpts=PTS+{max(0.0, L):.3f}/TB[lk{i}]")
                g.append(f"[{cur}][lk{i}]overlay=0:0:format=yuv420:eof_action=pass[lkd{i}]")
                cur = f"lkd{i}"
        last = []
        grain = float(tex.get("grain") or 0)
        if grain > 0.01:
            last.append(f"noise=c0s={int(round(2 + 4 * grain))}:c0f=t+u")   # fine film grain (cheap to encode)
        if tex.get("vignette") and "vignette" not in (COLOR_GRADES.get(job.grade, {}).get("vf") or ""):
            last.append("vignette=angle=PI/4.8")
        if last:
            g.append(f"[{cur}]{','.join(last)}[tx]")
            cur = "tx"
    return cur


def title_layer(job: "RenderJob", g: list, cur: str, fontsdir: str, mask_input: Optional[int]) -> str:
    """The editorial title + beat text. With a speaker cut-out the title sits behind the speaker and the
    background softens a touch (portrait look) while the title is up."""
    if not job.story_ass:
        return cur
    af = f"ass=filename={filter_path(job.story_ass)}:fontsdir={filter_path(fontsdir)}"
    if mask_input is None or not job.mask_n or not job.title_win:
        g.append(f"[{cur}]{af}[ttl]")
        return "ttl"
    t0, t1 = job.title_win
    g.append(f"[{cur}]split=2[tlA][tlB]")
    g.append(f"[tlB]trim=end_frame={job.mask_n},setpts=PTS-STARTPTS[tlF]")
    g.append(f"[{mask_input}:v]format=gray,scale={W}:{H}:flags=bicubic,setpts=PTS-STARTPTS[tlM]")
    g.append("[tlF][tlM]alphamerge[tlP]")
    g.append(f"[tlA]gblur=sigma=3.5:enable='between(t,{t0:.3f},{t1:.3f})',{af}[tlT]")
    g.append("[tlT][tlP]overlay=0:0:eof_action=pass:format=auto[ttl]")
    return "ttl"


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _blur_bg(label_in: str, label_out: str, w: int, h: int) -> str:
    sw, sh = max(2, w // 4 // 2 * 2), max(2, h // 4 // 2 * 2)
    return (f"[{label_in}]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"boxblur=12:4,scale={w}:{h}:flags=bicubic,eq=brightness=-0.10:saturation=1.3,vignette=angle=PI/4[{label_out}]")


def _sharpen(upscale: float) -> str:
    """Restore crispness lost when a crop is enlarged (e.g. 1080p -> 9:16 at 1920 tall)."""
    if upscale >= 1.6:
        return ",unsharp=5:5:0.8:3:3:0.0"
    if upscale >= 1.15:
        return ",unsharp=5:5:0.5:3:3:0.0"
    return ""


def layout_graph(job: RenderJob) -> list[str]:
    """Filters from the stitched source [vsrc] to [base] (W x H)."""
    sw, sh = job.src_w or 1920, job.src_h or 1080
    pre = "[vsrc]setsar=1"
    layout = job.layout
    from .effects import CROP_LAYOUTS
    if sw / max(sh, 1) <= 0.62 and layout in CROP_LAYOUTS:
        layout = "blur_fit"  # source already vertical-ish
    z = min(2.0, max(1.0, float(job.frame_zoom or 1.0)))
    fx = min(1.0, max(-1.0, float(job.frame_x or 0.0)))
    fy = min(1.0, max(-1.0, float(job.frame_y or 0.0)))
    if layout in ("smart_crop", "center_crop"):
        cw0 = _even(sh * 9 / 16)
        if cw0 > sw:
            layout = "blur_fit"
        else:
            cw, ch = _even(cw0 / z), _even(sh / z)
            x = x_expression(job.camera) if (layout == "smart_crop" and job.camera) else f"{(sw - cw0) / 2:.0f}"
            if cw != cw0 or fx:
                # keep the tracked centre, then nudge by the manual offset; clamp inside the frame
                dx = (cw0 - cw) / 2 + fx * (sw - cw) / 2
                x = f"min(max(0,({x})+{dx:.1f}),{sw - cw})"
            y = f"{(sh - ch) / 2 * (1 + fy):.0f}"
            # camera motion runs on the small source crop before upscaling: same look, ~3x less work
            mo = getattr(job, "_pre_motion", None)
            if mo:
                job._motion_done = True
            mv = f",{mo}" if mo else ""
            # sharpen once, on the small crop (3x3 at source size ~ the old 5x5 passes at 1080x1920, 3x faster)
            up = H / ch
            amt = (1.0 if up >= 1.6 else 0.65 if up >= 1.15 else 0.0) + float(getattr(job, "_grade_sharp", 0.0))
            job._sharp_used = True
            sh_ = f",unsharp=3:3:{min(1.6, amt):.2f}:3:3:0.0" if amt > 0.05 else ""
            return [f"{pre},crop=w={cw}:h={ch}:x='{x}':y={y}{sh_}{mv},scale={W}:{H}:flags=lanczos,setsar=1[base]"]
    def cam_for(cw: int) -> str:
        """Crop-left expression for a window `cw` wide, following the face (camera is for a 9:16 window)."""
        cam = job.camera
        if cam:
            k = (sh * 9 / 16 - cw) / 2
            cam = [(t, min(max(0.0, x + k), max(0.0, sw - cw))) for t, x in cam]
        x = x_expression(cam) if cam else f"{max(0, (sw - cw) / 2):.0f}"
        if fx:
            x = f"min(max(0,({x})+{fx * (sw - cw) / 2:.1f}),{max(0, sw - cw)})"
        return x

    if layout in ("zoom45", "square"):
        # a 4:5 or 1:1 window of the frame (face-follow) on the blurred background
        ow, oh = (W, _even(W * 5 / 4)) if layout == "zoom45" else (W, W)
        ch = _even(sh / z)
        cw = _even(min(sw, ch * ow / oh))
        ch = _even(cw * oh / ow)
        y = f"{(sh - ch) / 2 * (1 + fy):.0f}"
        oy = int((H - oh) / 2 - H * 0.03)
        return [
            f"{pre},split=2[s1][s2]",
            _blur_bg("s1", "bg", W, H),
            f"[s2]crop=w={cw}:h={ch}:x='{cam_for(cw)}':y={y},scale={ow}:{oh}:flags=lanczos{_sharpen(oh / ch)},setsar=1[fg]",
            f"[bg][fg]overlay=0:{oy},setsar=1[base]",
        ]
    if layout == "two_speakers":
        # side-by-side podcast: left person on top, right person below
        half_h = H // 2
        cw = _even(sw / 2)
        ch = _even(min(sh, cw * half_h / W))
        cw = _even(ch * W / half_h)
        y = f"{max(0, (sh - ch) * 0.35 * (1 + fy)):.0f}"
        lx = max(0, int(sw / 4 - cw / 2 + fx * sw / 8))
        rx = min(sw - cw, int(3 * sw / 4 - cw / 2 + fx * sw / 8))
        sc = f"scale={W}:{half_h}:flags=lanczos{_sharpen(half_h / ch)},setsar=1"
        return [
            f"{pre},split=2[s1][s2]",
            f"[s1]crop=w={cw}:h={ch}:x={lx}:y={y},{sc}[top]",
            f"[s2]crop=w={cw}:h={ch}:x={rx}:y={y},{sc}[bot]",
            "[top][bot]vstack=inputs=2,drawbox=x=0:y=ih/2-3:w=iw:h=6:color=black@0.9:t=fill,setsar=1[base]",
        ]
    if layout == "black_fit":
        fit = min(W / sw, H / sh) * z
        fw, fh = _even(min(W, sw * fit)), _even(min(H, sh * fit))
        return [f"{pre},scale={_even(sw * fit)}:{_even(sh * fit)}:flags=lanczos{_sharpen(fit)},"
                f"crop={fw}:{fh},pad={W}:{H}:{(W - fw) / 2 * (1 + fx):.0f}:{(H - fh) / 2 * (1 + fy):.0f}:black,setsar=1[base]"]
    if layout == "framed":
        # the video as a card with a white border on the blurred background
        fw = _even(W * 0.9 * min(z, 1.1))
        fh = _even(fw * sh / sw)
        if fh > H * 0.7:
            fh = _even(H * 0.7)
            fw = _even(fh * sw / sh)
        bd = 10
        ox, oy = (W - fw - 2 * bd) // 2, int((H - fh - 2 * bd) / 2 * (1 + fy * 0.8))
        return [
            f"{pre},split=2[s1][s2]",
            _blur_bg("s1", "bg", W, H),
            f"[s2]scale={fw}:{fh}:flags=lanczos{_sharpen(fw / sw)},pad={fw + 2 * bd}:{fh + 2 * bd}:{bd}:{bd}:white,setsar=1[fg]",
            f"[bg]drawbox=x={ox + 14}:y={oy + 22}:w={fw + 2 * bd}:h={fh + 2 * bd}:color=black@0.45:t=fill[bgs]",
            f"[bgs][fg]overlay={ox}:{oy},setsar=1[base]",
        ]
    if layout == "split_reverse":
        top_h = H // 2
        cw = _even(sh * W / top_h)
        if cw > sw:
            cw = _even(sw)
        return [
            f"{pre},split=3[s1][s2][s3]",
            f"[s1]crop=w={cw}:h={sh}:x='{cam_for(cw)}':y=0,scale={W}:{top_h}:flags=lanczos{_sharpen(top_h / sh)},setsar=1[bot]",
            _blur_bg("s2", "tbg", W, H - top_h),
            f"[s3]scale={W}:-2:flags=lanczos[tfg]",
            "[tbg][tfg]overlay=(W-w)/2:(H-h)/2[top]",
            "[top][bot]vstack=inputs=2,setsar=1[base]",
        ]
    if layout == "split":
        top_h = H // 2
        cw = _even(sh * W / top_h)
        if cw > sw:
            cw = _even(sw)
        cam = job.camera
        if cam:
            # camera was computed for a 9:16 window; re-center for the wider split window
            k = (sh * 9 / 16 - cw) / 2
            cam = [(t, min(max(0.0, x + k), sw - cw)) for t, x in cam]
        x = x_expression(cam) if cam else f"{(sw - cw) / 2:.0f}"
        if fx:
            x = f"min(max(0,({x})+{fx * (sw - cw) / 2:.1f}),{sw - cw})"
        return [
            f"{pre},split=3[s1][s2][s3]",
            f"[s1]crop=w={cw}:h={sh}:x='{x}':y=0,scale={W}:{top_h}:flags=lanczos{_sharpen(top_h / sh)},setsar=1[top]",
            _blur_bg("s2", "bbg", W, H - top_h),
            f"[s3]scale={W}:-2:flags=lanczos[bfg]",
            "[bbg][bfg]overlay=(W-w)/2:(H-h)/2[bot]",
            "[top][bot]vstack=inputs=2,setsar=1[base]",
        ]
    # blur_fit (zoom enlarges the sharp video, the offsets slide it left/right and up/down)
    fit = min(W / sw, H / sh)
    fw, fh = _even(sw * fit * z), _even(sh * fit * z)
    fg = f"[s2]scale={fw}:{fh}:flags=lanczos{_sharpen(fit * z)},setsar=1"
    if fw > W:
        fg += f",crop={W}:{fh}:{(fw - W) / 2 * (1 + fx):.0f}:0"
        fw = W
    ox = f"{(W - fw) / 2 * (1 + fx):.0f}" if fw < W else "0"
    oy = f"{(H - fh) / 2 * (1 + fy):.0f}" if fh < H else "(H-h)/2"
    return [
        f"{pre},split=2[s1][s2]",
        _blur_bg("s1", "bg", W, H),
        fg + "[fg]",
        f"[bg][fg]overlay={ox}:{oy},setsar=1[base]",
    ]


def _inputs_and_core(job: RenderJob, with_audio: bool = True) -> tuple[list, list, str]:
    """Inputs + the picture up to (and including) Story FX looks. Returns (args, graph, label)."""
    D = job.duration
    job.src = str(Path(job.src).resolve())
    pieces = job.pieces()
    args: list[str] = []
    g: list[str] = []
    for i, (a, b, keep) in enumerate(pieces):
        args += ["-ss", f"{max(0.0, a - job.src_offset):.3f}", "-t", f"{max(0.1, b - a):.3f}", "-i", job.src]
        v = f"[{i}:v]fps={job.fps},setsar=1"
        v += f",select='{select_expr(keep)}',setpts=N/FRAME_RATE/TB" if keep else ",setpts=PTS-STARTPTS"
        g.append(v + f"[pv{i}]")
        if job.has_audio and with_audio:
            au = f"[{i}:a]"
            au += f"aselect='{select_expr(keep)}',asetpts=N/SR/TB," if keep else "asetpts=PTS-STARTPTS,"
            g.append(au + f"aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[pa{i}]")
    k = len(pieces)
    au_on = job.has_audio and with_audio
    fz = freeze_video(job)
    vlabel = "vsrc0" if fz else "vsrc"
    if k == 1:
        g.append(f"[pv0]null[{vlabel}]")
        if au_on:
            g.append("[pa0]anull[asrc0]")
    else:
        ins = "".join(f"[pv{i}]" + (f"[pa{i}]" if au_on else "") for i in range(k))
        g.append(f"{ins}concat=n={k}:v=1:a={1 if au_on else 0}[{vlabel}]" + ("[asrc0]" if au_on else ""))
    if fz:
        g.append(f"[vsrc0]{fz}[vsrc]")
    if au_on:
        fr = sorted(job.freezes or [], key=lambda x: x["t"])
        if fr:
            # the voice stops for each freeze (silence of the same length), then carries on
            n = len(fr)
            g.append(f"[asrc0]asplit={n + 1}" + "".join(f"[as{i}]" for i in range(n + 1)))
            seq, prev = [], 0.0
            for i, f in enumerate(fr):
                t = float(f["t"])
                g.append(f"[as{i}]atrim={prev:.4f}:{t:.4f},asetpts=PTS-STARTPTS[aseg{i}]")
                d = max(1, round(float(f["d"]) * job.fps)) / job.fps
                g.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.4f},aformat=sample_fmts=fltp:sample_rates=48000:"
                         f"channel_layouts=stereo[az{i}]")
                seq += [f"[aseg{i}]", f"[az{i}]"]
                prev = t
            g.append(f"[as{n}]atrim=start={prev:.4f},asetpts=PTS-STARTPTS[aseg{n}]")
            seq.append(f"[aseg{n}]")
            g.append(f"{''.join(seq)}concat=n={len(seq)}:v=0:a=1[asrc]")
        else:
            g.append("[asrc0]anull[asrc]")

    mo = motion_filters(job.motion, D, job.punch_times, job.intro, job.fps, story=job.story or None)
    grade = COLOR_GRADES.get(job.grade, COLOR_GRADES["none"])["vf"]
    gs = re.search(r"unsharp=5:5:([\d.]+):5:5:0\.0,?", grade)
    job._pre_motion, job._motion_done, job._sharp_used = mo, False, False
    job._grade_sharp = float(gs.group(1)) * 0.6 if gs else 0.0
    g += layout_graph(job)
    job._pre_motion = None
    if gs and job._sharp_used:          # the grade's sharpening already happened on the crop
        grade = grade.replace(gs.group(0), "").strip(",")
    cur = "base"
    if grade:
        g.append(f"[{cur}]{grade}[graded]")
        cur = "graded"
    if mo and not job._motion_done:
        g.append(f"[{cur}]{mo}[moved]")
        cur = "moved"
    cur = post_fx(job, g, cur)
    return args, g, cur


def mask_pass_args(job: RenderJob, out_pattern: str, frames: int, width: int = 270) -> list[str]:
    """Render just the title window, small, exactly as framed in the export (for the speaker cut-out)."""
    st = dict(job.story or {})
    st["texture"] = None                      # grain/leaks would only confuse the cut-out
    j = copy.copy(job)
    j.story = st
    args, g, cur = _inputs_and_core(j, with_audio=False)
    h = _even(width * H / W)
    g.append(f"[{cur}]trim=end_frame={frames},scale={width}:{h}:flags=bilinear,format=rgb24[mk]")
    return args + ["-filter_complex", ";".join(g), "-map", "[mk]", "-an", "-frames:v", str(frames),
                   "-start_number", "0", str(Path(out_pattern).resolve())]


def build_args(job: RenderJob, fontsdir: str) -> list[str]:
    D = job.duration                 # finished length
    # ffmpeg runs from the data folder, so every file argument must be absolute
    job.out_path = str(Path(job.out_path).resolve())
    if job.music:
        job.music = str(Path(job.music).resolve())
    args, g, cur = _inputs_and_core(job)
    k = len(job.pieces())
    nin = k
    music_idx = None
    if job.music and (job.music_volume > 0 or job.music_auto) and Path(job.music).exists():
        args += ["-stream_loop", "-1", "-ss", f"{max(0.0, job.music_offset):.2f}", "-i", job.music]
        music_idx = nin
        nin += 1
    mask_idx = None
    if job.mask_pattern and job.mask_n and job.story_ass:
        args += ["-framerate", str(job.fps), "-start_number", "0", "-i", str(Path(job.mask_pattern).resolve())]
        mask_idx = nin
        nin += 1
    # music level: Auto = loudness-normalise the track ~13 dB under the voice; manual = slider gain
    mus_level = ("loudnorm=I=-27:TP=-4:LRA=9,aresample=48000" + (f",volume={job.music_volume / 0.12:.3f}"
                                                                if abs(job.music_volume - 0.12) > 0.005 else "")
                 if job.music_auto else f"volume={job.music_volume:.3f}")
    cur = title_layer(job, g, cur, fontsdir, mask_idx)
    if job.progress_bar:
        acc = job.accent.lstrip("#")
        g.append(f"color=c=0x{acc}:s={W}x12:r={job.fps}:d={D:.3f}[pb]")
        g.append(f"[{cur}][pb]overlay=x='-w+w*t/{D:.3f}':y=0:eval=frame:shortest=1[pbd]")
        cur = "pbd"
    g.append(f"[{cur}]ass=filename={filter_path(job.ass_file)}:fontsdir={filter_path(fontsdir)}[subd]")
    cur = "subd"
    io = intro_overlay(job.intro)
    if io:
        g.append(f"[{cur}]{io}[intro]")
        cur = "intro"
    if job.out_h and int(job.out_h) != H:
        oh = _even(job.out_h)
        g.append(f"[{cur}]scale={_even(oh * W / H)}:{oh}:flags=lanczos[sized]")
        cur = "sized"
    g.append(f"[{cur}]format=yuv420p[vout]")

    # Audio: voice (jump-cut, loudness-normalised) + optional ducked music + optional sound effects
    sfx_idx = None
    if job.sfx and Path(job.sfx).exists():
        sfx_idx = nin
        nin += 1
        args += ["-i", str(Path(job.sfx).resolve())]
    duck = ""
    if (job.story or {}).get("spans"):       # the music drops for every freeze, then the hit lands
        duck = f",volume=0.22:enable='{_en([tuple(x) for x in job.story['spans']])}'"
    main = None
    if job.has_audio:
        a = "[asrc]aresample=48000,aformat=channel_layouts=stereo"
        if job.loudnorm:
            a += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        a += f",afade=t=in:d=0.05,afade=t=out:st={max(0, D - 0.25):.3f}:d=0.25"
        if music_idx is not None:
            g.append(a + ",asplit=2[voice][key]")
            g.append(f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,{mus_level},"
                     f"atrim=0:{D:.3f},afade=t=in:d=1,afade=t=out:st={max(0, D - 1.5):.3f}:d=1.5[mus]")
            g.append(f"[mus][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400{duck}[duck]")
            g.append("[voice][duck]amix=inputs=2:duration=first:normalize=0[amain]")
        else:
            g.append(a + "[amain]")
        main = "[amain]"
    elif music_idx is not None:
        g.append(f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                 f"{'loudnorm=I=-16:TP=-2,aresample=48000' if job.music_auto else f'volume={max(job.music_volume * 3, 0.3):.3f}'},"
                 f"atrim=0:{D:.3f},"
                 f"afade=t=out:st={max(0, D - 1.5):.3f}:d=1.5{duck}[amain]")
        main = "[amain]"
    amap = None
    if sfx_idx is not None:
        g.append(f"[{sfx_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{D:.3f}[fx]")
        if main:
            g.append(f"{main}[fx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[aout]")
        else:
            g.append("[fx]anull[aout]")
        amap = "[aout]"
    elif main:
        g.append(f"{main}alimiter=limit=0.97[aout]")
        amap = "[aout]"

    enc = pick_encoder(job.encoder) if job.codec != "hevc" else pick_hevc_encoder(job.encoder)
    args += ["-filter_complex", ";".join(g), "-map", "[vout]"]
    if amap:
        args += ["-map", amap, "-c:a", "aac", "-b:a", "256k"]
    args += encoder_args(enc, job.crf, job.speed) + ["-r", str(job.fps), "-t", f"{D:.3f}",
                                          "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                                          "-movflags", "+faststart", job.out_path]
    return args


def render(job: RenderJob, on_progress: Optional[Callable[[float], None]] = None,
           cancel: Optional[threading.Event] = None) -> str:
    Path(job.out_path).parent.mkdir(parents=True, exist_ok=True)
    args = build_args(job, str(fonts.fonts_dir()))
    try:
        run_ffmpeg(args, job.duration, on_progress, cancel, cwd=ffmpeg_cwd())
    except RuntimeError:
        if job.encoder != "libx264" and pick_encoder(job.encoder) != "libx264":
            # GPU encoder hiccup -> retry on CPU
            job.encoder = "libx264"
            args = build_args(job, str(fonts.fonts_dir()))
            run_ffmpeg(args, job.duration, on_progress, cancel, cwd=ffmpeg_cwd())
        else:
            raise
    return job.out_path


def thumbnail(video: str, out_jpg: str, at: float = 1.0) -> Optional[str]:
    video, out_jpg = str(Path(video).resolve()), str(Path(out_jpg).resolve())
    try:
        run_ffmpeg(["-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4",
                    "-update", "1", out_jpg], 1.0)
        return out_jpg
    except Exception:
        return None


def source_time(job: RenderJob, t: float) -> float:
    """Map a time in the finished Short back to the source video (through cuts and stitching)."""
    acc = 0.0
    last = job.start
    for a, b, keep in job.pieces():
        for x, y in (keep or [(0.0, b - a)]):
            seg = y - x
            if t < acc + seg:
                return a + x + max(0.0, t - acc)
            acc += seg
            last = a + y
    return max(job.start, last - 0.05)


def _camera_at(camera: list, t: float) -> Optional[float]:
    if not camera:
        return None
    if t <= camera[0][0]:
        return camera[0][1]
    for (t0, x0), (t1, x1) in zip(camera, camera[1:]):
        if t0 <= t < t1 and t1 > t0:
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return camera[-1][1]


def preview_frame(job: RenderJob, t: float, out_png: str, width: int = 720) -> str:
    """One finished frame of the Short at time `t` (layout, grade, captions, hook, Story FX looks and titles)
    in about a second. The intro flash is left out; the full render adds it."""
    import copy
    from .story import to_pre
    j = copy.copy(job)
    D = j.duration
    t = min(max(0.0, t), max(0.0, D - 0.05))
    pre, _fi = to_pre(t, [{"t": f["t"], "d": d} for f, d in zip(j.freezes or [], j._frozen())])
    src = str(Path(j.src).resolve())
    cx = _camera_at(j.camera, t)
    j.camera = [(0.0, cx)] if cx is not None else []
    mo = motion_filters(j.motion, D, j.punch_times, j.intro, j.fps, at=t, story=j.story or None)
    grade = COLOR_GRADES.get(j.grade, COLOR_GRADES["none"])["vf"]
    gs = re.search(r"unsharp=5:5:([\d.]+):5:5:0\.0,?", grade)
    j._pre_motion, j._motion_done, j._sharp_used = mo, False, False
    j._grade_sharp = float(gs.group(1)) * 0.6 if gs else 0.0
    g = [f"[0:v]setpts=PTS-STARTPTS+{t:.3f}/TB[vsrc]"] + layout_graph(j)
    if gs and j._sharp_used:
        grade = grade.replace(gs.group(0), "").strip(",")
    cur = "base"
    if grade:
        g.append(f"[{cur}]{grade}[graded]")
        cur = "graded"
    if mo and not j._motion_done:
        g.append(f"[{cur}]{mo}[moved]")
        cur = "moved"
    st = dict(j.story or {})
    st["leaks"] = []                    # the light leak needs a moving clock; skip it in a still
    j.story = st
    cur = post_fx(j, g, cur)
    fdir = str(fonts.fonts_dir())
    if j.story_ass:
        g.append(f"[{cur}]ass=filename={filter_path(j.story_ass)}:fontsdir={filter_path(fdir)}[ttl]")
        cur = "ttl"
    if j.progress_bar and t > 0.05:
        g.append(f"[{cur}]drawbox=x=0:y=0:w={max(2, int(W * t / D))}:h=12:color=0x{j.accent.lstrip('#')}:t=fill[pbd]")
        cur = "pbd"
    g.append(f"[{cur}]ass=filename={filter_path(j.ass_file)}:fontsdir={filter_path(fdir)},"
             f"scale={width}:-2:flags=bicubic[vout]")
    out_png = str(Path(out_png).resolve())
    run_ffmpeg(["-ss", f"{max(0.0, source_time(j, pre) - j.src_offset):.3f}", "-i", src, "-filter_complex", ";".join(g),
                "-map", "[vout]", "-frames:v", "1", "-update", "1", out_png], 1.0, cwd=ffmpeg_cwd())
    return out_png
