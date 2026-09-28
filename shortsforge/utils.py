"""FFmpeg helpers: locating binaries, probing, running with progress, escaping."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from .config import app_root

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW on Windows


class Cancelled(Exception):
    pass


class ToolMissing(RuntimeError):
    pass


_ffmpeg_override: str = ""


def set_ffmpeg_override(path: str) -> None:
    global _ffmpeg_override
    _ffmpeg_override = path or ""
    find_ffmpeg.cache_clear()
    find_ffprobe.cache_clear()
    pick_encoder.cache_clear()


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


@lru_cache(maxsize=1)
def find_ffmpeg() -> str:
    cands = []
    if _ffmpeg_override:
        p = Path(_ffmpeg_override)
        cands.append(p / _exe("ffmpeg") if p.is_dir() else p)
    cands.append(app_root() / "bin" / _exe("ffmpeg"))
    cands.append(Path(sys.executable).parent / "bin" / _exe("ffmpeg"))
    for c in cands:
        if c and Path(c).is_file():
            return str(c)
    w = shutil.which("ffmpeg")
    if w:
        return w
    if os.name == "nt":  # installed by winget but PATH not refreshed yet
        la = Path(os.environ.get("LOCALAPPDATA", ""))
        link = la / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if link.is_file():
            return str(link)
        for hit in sorted((la / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"),
                          reverse=True):
            return str(hit)
        for base in (Path("C:/ffmpeg/bin"), Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg" / "bin"):
            if (base / "ffmpeg.exe").is_file():
                return str(base / "ffmpeg.exe")
    try:  # optional pip fallback
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise ToolMissing(
        "FFmpeg was not found. Run setup.bat (it installs FFmpeg with winget) "
        "or set the FFmpeg path in Settings."
    )


@lru_cache(maxsize=1)
def find_ffprobe() -> Optional[str]:
    ff = Path(find_ffmpeg())
    cand = ff.with_name(_exe("ffprobe"))
    if cand.is_file():
        return str(cand)
    return shutil.which("ffprobe")


def run(cmd: list[str], cwd: Optional[str] = None, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW,
    )


def probe(path: str) -> dict:
    """Return {duration, width, height, fps, has_audio}."""
    fp = find_ffprobe()
    if fp:
        r = run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
        if r.returncode == 0:
            d = json.loads(r.stdout)
            v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
            a = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
            fps = 30.0
            try:
                num, den = v.get("avg_frame_rate", "30/1").split("/")
                fps = float(num) / float(den) if float(den) else 30.0
            except Exception:
                pass
            # Respect rotation metadata (phone footage)
            w, h = int(v.get("width", 0)), int(v.get("height", 0))
            rot = 0
            for sd in v.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = abs(int(sd["rotation"]))
            if rot in (90, 270):
                w, h = h, w
            return {
                "duration": float(d.get("format", {}).get("duration", 0) or 0),
                "width": w, "height": h, "fps": fps or 30.0, "has_audio": a,
                "vcodec": v.get("codec_name", ""),
            }
    # Fallback: parse `ffmpeg -i`
    r = run([find_ffmpeg(), "-hide_banner", "-i", path])
    txt = r.stderr
    dur = 0.0
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", txt)
    if m:
        dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    w, h = (int(m[1]), int(m[2])) if m else (1920, 1080)
    m = re.search(r"(\d+(?:\.\d+)?) fps", txt)
    fps = float(m[1]) if m else 30.0
    return {"duration": dur, "width": w, "height": h, "fps": fps, "has_audio": "Audio:" in txt}


def run_ffmpeg(
    args: list[str],
    duration: float,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[threading.Event] = None,
    cwd: Optional[str] = None,
) -> None:
    """Run ffmpeg with args (without the binary), reporting progress 0..1."""
    cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"] + args
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    err_lines: list[str] = []

    def _drain():
        assert proc.stderr
        for line in proc.stderr:
            err_lines.append(line)
            if len(err_lines) > 400:
                del err_lines[:200]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    assert proc.stdout
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1].strip())
                if on_progress and duration > 0:
                    on_progress(min(1.0, us / 1_000_000 / duration))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        tail = "".join(err_lines[-25:])
        raise RuntimeError(f"FFmpeg failed (code {proc.returncode}):\n{tail}")
    if on_progress:
        on_progress(1.0)


def filter_path(p: str | Path) -> str:
    """Path for use inside an ffmpeg filter argument.

    Files under the app data folder are referenced relative to it (ffmpeg runs with that folder as
    its working directory), which sidesteps Windows drive-letter and quote escaping entirely."""
    from .config import data_dir
    full = Path(p).resolve()
    try:
        rel = full.relative_to(data_dir().resolve()).as_posix()
        if re.fullmatch(r"[A-Za-z0-9_./-]+", rel):
            return rel
    except ValueError:
        pass
    s = str(full).replace("\\", "/")
    s = s.replace(":", r"\:").replace("'", r"'\\\''")
    return f"'{s}'"


def ffmpeg_cwd() -> str:
    from .config import data_dir
    return str(data_dir())


@lru_cache(maxsize=4)
def pick_encoder(preference: str = "auto") -> str:
    """Pick a working H.264 encoder, preferring GPU when available (tested once, then remembered)."""
    if preference in _ENC_CACHE:
        return _ENC_CACHE[preference]
    _ENC_CACHE[preference] = _pick_encoder(preference)
    return _ENC_CACHE[preference]


def _pick_encoder(preference: str = "auto") -> str:
    order = ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"] if preference == "auto" else [preference, "libx264"]
    ff = find_ffmpeg()
    for enc in order:
        try:
            r = run([ff, "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                     "-c:v", enc, "-f", "null", "-"], timeout=20)
            if r.returncode == 0:
                return enc
        except Exception:
            continue
    return "libx264"


def encoder_args(enc: str, crf: int, speed: str = "fast") -> list[str]:
    """H.264 settings tuned for Shorts. "fast" keeps the look (YouTube re-encodes anyway) at 2-3x the speed."""
    fast = speed != "quality"
    common = ["-profile:v", "high", "-pix_fmt", "yuv420p", "-g", "60"]
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p4" if fast else "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(crf),
                "-b:v", "0", "-maxrate", "24M", "-bufsize", "48M", "-spatial-aq", "1", "-temporal-aq", "1",
                "-bf", "3"] + common
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", str(crf), "-preset", "faster" if fast else "veryslow",
                "-look_ahead", "0" if fast else "1"] + common
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "balanced" if fast else "quality", "-rc", "cqp", "-qp_i", str(crf),
                "-qp_p", str(crf + 2)] + common
    return ["-c:v", "libx264", "-preset", "veryfast" if fast else "medium", "-crf", str(crf - 1 if fast else crf),
            "-tune", "film", "-x264-params", "aq-mode=3:aq-strength=0.9:deblock=-1,-1"] + common


_ENC_CACHE: dict = {}


def safe_name(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:maxlen].rstrip(" .") or "video")


def open_path(p: str | Path) -> None:
    p = str(p)
    if os.name == "nt":
        os.startfile(p)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])
