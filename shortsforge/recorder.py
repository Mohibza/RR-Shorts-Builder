"""Screen recorder: a tiny separate process, so the big app can close while you record.

Start:  app.py --record <session folder>     (the app writes config.json there, starts this, then quits)

While recording there is no window at all. Everything is driven by the user's own global hotkeys (stop, pause,
resume, marker, cancel). When the recording stops it is saved, and the app is started again with the recording
ready to use.

How it stays safe and light:
* Video is written by FFmpeg straight to Matroska segments (one per pause/resume). Matroska stays readable if the
  PC crashes, so `finalize()` can always stitch whatever was written.
* Capture tries the cheapest path first (desktop duplication + GPU encoder), then falls back step by step to the
  most compatible one (GDI capture + software encoder). The path that works is remembered in the session.
* The microphone goes into the same FFmpeg process (always in sync). System sound is recorded separately as WAV
  through WASAPI loopback (optional component: "soundcard").
* Mouse position, clicks, wheel and "a key was pressed" moments are logged with their time (never which key), so
  the editor can zoom to where the action is.

Only the standard library (+ ctypes on Windows) is imported here on purpose: this process must start instantly and
use almost no memory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Optional

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
DEFAULT_HOTKEYS = {"stop": "ctrl+s", "pause": "ctrl+p", "resume": "ctrl+r", "marker": "ctrl+m",
                   "cancel": "ctrl+shift+alt+x"}
EMERGENCY_STOP = "ctrl+shift+alt+f12"      # always registered too, in case the user's stop key is taken


# ================================================================ small file helpers
def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(p: Path, data) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _log(sdir: Path, msg: str) -> None:
    try:
        with open(sdir / "recorder.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def beep(kind: str) -> None:
    """Audible feedback, since there is no window: start / pause / resume / stop / error."""
    if not IS_WIN:
        return
    try:
        import winsound
        for f, d in {"start": [(880, 90), (1175, 130)], "pause": [(660, 140)], "resume": [(880, 140)],
                     "stop": [(1175, 90), (880, 150)], "marker": [(1320, 60)],
                     "error": [(300, 250), (300, 250)]}.get(kind, []):
            winsound.Beep(f, d)
    except Exception:
        pass


# ================================================================ hotkeys (Windows)
VK = {**{chr(c): c for c in range(0x30, 0x3A)}, **{chr(c).lower(): c for c in range(0x41, 0x5B)},
      **{f"f{i}": 0x6F + i for i in range(1, 25)}, "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
      "escape": 0x1B, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "delete": 0x2E,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "pause": 0x13, "printscreen": 0x2C, "`": 0xC0,
      "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC}
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(text: str) -> Optional[tuple[int, int]]:
    """'ctrl+shift+s' -> (modifier flags, virtual key). None if it isn't a usable combination."""
    parts = [p.strip().lower() for p in (text or "").replace(" ", "").split("+") if p.strip()]
    mods, vk = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif p in VK and vk is None:
            vk = VK[p]
        else:
            return None
    if vk is None:
        return None
    if not mods and not (0x70 <= vk <= 0x87):     # a bare letter would break typing; bare F-keys are fine
        return None
    return mods, vk


def hotkey_problems(hotkeys: dict) -> dict:
    """{action: why it can't be used} for the settings screen."""
    out, seen = {}, {}
    for action, text in (hotkeys or {}).items():
        hk = parse_hotkey(text)
        if not hk:
            out[action] = "Use Ctrl, Alt or Shift with a key (for example Ctrl+S), or an F-key."
        elif hk in seen:
            out[action] = f"Same keys as “{seen[hk]}”."
        else:
            seen[hk] = action
    return out


# ================================================================ FFmpeg capture commands
def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _enc_args(enc: str, quality: str, fps: int) -> list[str]:
    q = {"high": 18, "medium": 22, "small": 26}.get(quality, 20)
    g = ["-g", str(fps * 2)]
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", str(q), "-b:v", "0"] + g
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "balanced", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)] + g
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", str(q), "-preset", "faster"] + g
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(q), "-pix_fmt", "yuv420p"] + g


def capture_attempts(cfg: dict) -> list[dict]:
    """Capture commands to try, cheapest first. Each: {'name', 'input': [...], 'vf': str|None, 'enc': [...]}."""
    src = cfg.get("source") or {}
    fps = int(cfg.get("fps") or 30)
    quality = cfg.get("quality") or "high"
    enc = cfg.get("encoder") or "libx264"
    if cfg.get("test_source"):                      # development / self-test (no real screen)
        return [{"name": "test", "input": ["-f", "lavfi", "-i", f"testsrc2=s=1280x720:r={fps}"], "vf": None,
                 "enc": _enc_args("libx264", quality, fps)}]
    kind = src.get("kind") or "screen"
    mon = src.get("monitor") or {"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "idx": 0}
    rect = dict(mon)
    if kind == "region" and src.get("region"):
        rect = src["region"]
    rect = {"x": int(rect["x"]), "y": int(rect["y"]), "w": _even(rect["w"]), "h": _even(rect["h"])}
    cursor = 1 if cfg.get("cursor", True) else 0
    gdi = ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", str(cursor)]
    out: list[dict] = []
    if kind == "window" and src.get("title"):
        out.append({"name": "window", "input": gdi + ["-i", f"title={src['title']}"],
                    "vf": "crop=trunc(iw/2)*2:trunc(ih/2)*2", "enc": _enc_args(enc if enc != "libx264" else "libx264", quality, fps)})
        out.append({"name": "window-soft", "input": gdi + ["-i", f"title={src['title']}"],
                    "vf": "crop=trunc(iw/2)*2:trunc(ih/2)*2", "enc": _enc_args("libx264", quality, fps)})
        return out
    method = cfg.get("method") or "auto"
    single = int(cfg.get("monitors") or 1) <= 1
    if method == "auto" and mon.get("primary", True) and single:
        # desktop duplication: by far the lightest way to grab the screen on Windows 10/11
        dd = f"ddagrab=output_idx=0:framerate={fps}:draw_mouse={cursor}"
        if kind == "region":
            dd += f":offset_x={rect['x'] - int(mon['x'])}:offset_y={rect['y'] - int(mon['y'])}:video_size={rect['w']}x{rect['h']}"
        if enc in ("h264_nvenc", "h264_amf"):
            out.append({"name": f"gpu ({enc})", "input": ["-f", "lavfi", "-i", dd], "vf": None,
                        "enc": _enc_args(enc, quality, fps)})
        out.append({"name": "duplication + software", "input": ["-f", "lavfi", "-i", dd + ",hwdownload,format=bgra"],
                    "vf": None, "enc": _enc_args(enc if enc == "h264_qsv" else "libx264", quality, fps)})
    if kind == "screen" and single:        # the whole (only) screen: let the capture find its own size
        g = gdi + ["-i", "desktop"]
    else:
        g = gdi + ["-offset_x", str(rect["x"]), "-offset_y", str(rect["y"]), "-video_size", f"{rect['w']}x{rect['h']}",
                   "-i", "desktop"]
    even = "crop=trunc(iw/2)*2:trunc(ih/2)*2"
    if enc != "libx264":
        out.append({"name": f"compatible ({enc})", "input": g, "vf": even, "enc": _enc_args(enc, quality, fps)})
    out.append({"name": "compatible", "input": g, "vf": even, "enc": _enc_args("libx264", quality, fps)})
    return out


def build_cmd(ffmpeg: str, attempt: dict, cfg: dict, out_file: str, with_mic: bool) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-thread_queue_size", "1024", *attempt["input"]]
    mic = cfg.get("mic") or ""
    if cfg.get("test_source"):
        if with_mic and mic:
            cmd += ["-f", "lavfi", "-i", "sine=f=330:r=48000"]
    elif with_mic and mic:
        cmd += ["-thread_queue_size", "1024", "-f", "dshow", "-audio_buffer_size", "50", "-i", f"audio={mic}"]
    if attempt.get("vf"):
        cmd += ["-vf", attempt["vf"]]
    cmd += attempt["enc"]
    if with_mic and mic:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-f", "matroska", "-cluster_time_limit", "1000", out_file]
    return cmd


def webcam_cmd(ffmpeg: str, cfg: dict, out_file: str) -> Optional[list[str]]:
    cam = cfg.get("webcam") or ""
    if not cam:
        return None
    if cfg.get("test_source"):
        src = ["-f", "lavfi", "-i", "testsrc=s=640x360:r=30"]
    else:
        src = ["-f", "dshow", "-framerate", "30", "-video_size", "1280x720", "-i", f"video={cam}"]
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *src, "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-g", "60", "-f", "matroska", "-cluster_time_limit", "1000", out_file]


# ================================================================ system sound (WASAPI loopback, optional)
class SystemAudio(threading.Thread):
    """Records what the PC is playing to a WAV. Silence is padded from the clock so it stays in sync."""

    def __init__(self, path: str, sdir: Path):
        super().__init__(daemon=True)
        self.path, self.sdir = path, sdir
        self.stop_ev = threading.Event()
        self.ok = False

    def run(self):
        try:
            if IS_WIN:                      # every thread that touches Windows audio needs this once
                import ctypes
                try:
                    ctypes.windll.ole32.CoInitializeEx(None, 0)
                except Exception:
                    pass
            import numpy as np
            import soundcard as sc
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            sr = 48000
            with wave.open(self.path, "wb") as w, mic.recorder(samplerate=sr, channels=2) as rec:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(sr)
                self.ok = True
                t0, written = time.time(), 0
                while not self.stop_ev.is_set():
                    data = rec.record(numframes=sr // 10)
                    want = int((time.time() - t0) * sr) - written - len(data)
                    if want > sr // 5:                      # nothing was playing for a while: keep the timeline
                        w.writeframes(b"\x00" * (want * 4))
                        written += want
                    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2").tobytes()
                    w.writeframes(pcm)
                    written += len(data)
                want = int((time.time() - t0) * sr) - written
                if want > 0:
                    w.writeframes(b"\x00" * (want * 4))
        except Exception as e:
            _log(self.sdir, f"system sound not recorded: {type(e).__name__}: {e}")

    def stop(self):
        self.stop_ev.set()


def system_audio_available() -> bool:
    try:
        import soundcard  # noqa: F401
        return True
    except Exception:
        return False


# ================================================================ mouse / key activity log (Windows)
class Events:
    """Appends {t, k, x, y} lines. t = seconds on the recording's own clock (pauses don't count)."""

    def __init__(self, path: Path, rect: dict):
        self.f = open(path, "a", encoding="utf-8", buffering=1)
        self.rect = rect
        self.base = 0.0           # recorded seconds before the current segment
        self.t0: Optional[float] = None
        self.lock = threading.Lock()
        self.last_key = 0.0

    def now(self) -> Optional[float]:
        return None if self.t0 is None else self.base + (time.time() - self.t0)

    def add(self, kind: str, x: Optional[float] = None, y: Optional[float] = None, **extra) -> None:
        t = self.now()
        if t is None:
            return
        rec = {"t": round(t, 3), "k": kind, **extra}
        if x is not None and y is not None:
            r = self.rect
            rec["x"] = round((x - r["x"]) / max(1, r["w"]), 4)
            rec["y"] = round((y - r["y"]) / max(1, r["h"]), 4)
        with self.lock:
            try:
                self.f.write(json.dumps(rec) + "\n")
            except Exception:
                pass

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


# ================================================================ the recorder
class Recorder:
    def __init__(self, sdir: Path):
        self.sdir = sdir
        self.cfg = _read(sdir / "config.json", {}) or {}
        self.state = {"state": "starting", "pid": os.getpid(), "segments": [], "markers": [], "notes": [],
                      "started": time.time(), "method": ""}
        self.ffmpeg = self.cfg.get("ffmpeg") or "ffmpeg"
        self.proc: Optional[subprocess.Popen] = None
        self.cam: Optional[subprocess.Popen] = None
        self.sys: Optional[SystemAudio] = None
        self.attempt: Optional[dict] = None
        self.with_mic = bool(self.cfg.get("mic"))
        self.seg_t0 = 0.0
        self.recorded = 0.0
        src = self.cfg.get("source") or {}
        mon = src.get("monitor") or {"x": 0, "y": 0, "w": 1920, "h": 1080}
        self.rect = src.get("region") if (src.get("kind") == "region" and src.get("region")) else mon
        self.events = Events(sdir / "events.jsonl", self.rect)
        self.done = False
        self._save()

    # -- state
    def _save(self):
        self.state["beat"] = time.time()          # "still alive" mark the app checks
        try:
            _write(self.sdir / "state.json", self.state)
        except OSError:
            pass

    def note(self, msg: str):
        self.state["notes"].append(msg)
        _log(self.sdir, msg)

    # -- segments
    def _spawn(self, attempt: dict, idx: int) -> Optional[subprocess.Popen]:
        out = str(self.sdir / f"seg_{idx:03d}.mkv")
        cmd = build_cmd(self.ffmpeg, attempt, self.cfg, out, self.with_mic)
        _log(self.sdir, "start: " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=open(self.sdir / "ffmpeg.log", "ab"), creationflags=NO_WINDOW)
        except OSError as e:
            _log(self.sdir, f"couldn't start ffmpeg: {e}")
            return None
        time.sleep(float(self.cfg.get("settle", 1.6)))          # a capture that can't start dies right away
        if p.poll() is not None:
            return None
        return p

    def start_segment(self) -> bool:
        idx = len(self.state["segments"])
        attempts = [self.attempt] if self.attempt else capture_attempts(self.cfg)
        p = None
        for a in attempts:
            p = self._spawn(a, idx)
            if p is None and self.with_mic:            # a busy/missing microphone must not stop the recording
                self.with_mic = False
                p = self._spawn(a, idx)
                if p is not None:
                    self.note("The microphone couldn't be opened, so this recording has no microphone sound.")
                else:
                    self.with_mic = bool(self.cfg.get("mic"))
            if p is not None:
                if self.attempt is None:
                    self.attempt = a
                    self.state["method"] = a["name"]
                    _log(self.sdir, f"capture method: {a['name']}")
                break
        if p is None:
            return False
        self.proc = p
        self.seg_t0 = time.time()
        seg = {"video": f"seg_{idx:03d}.mkv", "start": self.seg_t0, "sys": "", "cam": ""}
        c = webcam_cmd(self.ffmpeg, self.cfg, str(self.sdir / f"cam_{idx:03d}.mkv"))
        if c:
            try:
                self.cam = subprocess.Popen(c, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
                seg["cam"] = f"cam_{idx:03d}.mkv"
            except OSError:
                self.cam = None
        if self.cfg.get("system_audio"):
            self.sys = SystemAudio(str(self.sdir / f"sys_{idx:03d}.wav"), self.sdir)
            self.sys.start()
            seg["sys"] = f"sys_{idx:03d}.wav"
        self.events.base = self.recorded
        self.events.t0 = self.seg_t0
        self.state["segments"].append(seg)
        self.state["state"] = "recording"
        self._save()
        return True

    @staticmethod
    def _quit(p: Optional[subprocess.Popen]):
        if not p or p.poll() is not None:
            return
        try:
            p.stdin.write(b"q")
            p.stdin.flush()
        except Exception:
            pass
        try:
            p.wait(timeout=10)
        except Exception:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def stop_segment(self):
        if self.state["segments"] and "end" not in self.state["segments"][-1]:
            now = time.time()
            self.state["segments"][-1]["end"] = now
            self.recorded += now - self.seg_t0
        self.events.t0 = None
        if self.sys:
            self.sys.stop()
        self._quit(self.proc)
        self._quit(self.cam)
        if self.sys:
            self.sys.join(timeout=6)
            if not self.sys.ok and self.state["segments"]:
                self.state["segments"][-1]["sys"] = ""
        self.proc = self.cam = self.sys = None
        self._save()

    # -- actions (hotkeys / control file)
    def pause(self):
        if self.state["state"] != "recording":
            return
        self.stop_segment()
        self.state["state"] = "paused"
        self._save()
        beep("pause")

    def resume(self):
        if self.state["state"] != "paused":
            return
        beep("resume")                      # before the capture starts, so the beep isn't in the recording
        if not self.start_segment():
            self.note("Recording couldn't be resumed.")
            beep("error")

    def marker(self):
        t = self.events.now()
        if t is not None:
            self.state["markers"].append(round(t, 2))
            self.events.add("marker")
            self._save()
            beep("marker")

    def stop(self, cancelled: bool = False):
        if self.done:
            return
        self.done = True
        self.stop_segment()
        self.events.close()
        self.state["state"] = "cancelled" if cancelled else "finishing"
        self._save()
        beep("stop")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


# ================================================================ after recording: stitch + describe
def _concat(ffmpeg: str, files: list[Path], out: Path) -> bool:
    files = [f for f in files if f.exists() and f.stat().st_size > 2000]
    if not files:
        return False
    if len(files) == 1:
        try:
            out.unlink(missing_ok=True)
            files[0].replace(out)
            return True
        except OSError:
            pass
    lst = out.with_suffix(".txt")
    lst.write_text("".join("file '" + str(f.resolve()).replace("\\", "/").replace("'", "'\\''") + "'\n" for f in files),
                   encoding="utf-8")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, creationflags=NO_WINDOW)
    lst.unlink(missing_ok=True)
    if r.returncode == 0 and out.exists():
        for f in files:
            f.unlink(missing_ok=True)
        return True
    return False


def _concat_wav(files: list[Path], out: Path) -> bool:
    files = [f for f in files if f.exists() and f.stat().st_size > 100]
    if not files:
        return False
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            for f in files:
                with wave.open(str(f), "rb") as r:
                    while True:
                        b = r.readframes(48000)
                        if not b:
                            break
                        w.writeframes(b)
        for f in files:
            f.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _probe(ffmpeg: str, path: Path) -> dict:
    """{duration, width, height, fps, audio} read from ffmpeg's own report (no ffprobe needed)."""
    import re
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace",
                       creationflags=NO_WINDOW)
    txt = r.stderr or ""
    out = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "audio": "Audio:" in txt}
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+)\s*fps", txt)
    if m:
        out["fps"] = float(m.group(1))
    return out


def finalize(sdir: Path) -> dict:
    """Stitch the segments of a session (finished or interrupted) and write session.json. Safe to call again."""
    cfg = _read(sdir / "config.json", {}) or {}
    st = _read(sdir / "state.json", {}) or {}
    ffmpeg = cfg.get("ffmpeg") or "ffmpeg"
    segs = st.get("segments") or []
    if not segs:           # crashed before the state was written: use whatever files are there
        segs = [{"video": f.name, "sys": "", "cam": ""} for f in sorted(sdir.glob("seg_*.mkv"))]
    mkv, video = sdir / "screen.mkv", sdir / "screen.mp4"
    have = video.exists() and video.stat().st_size > 2000 and not mkv.exists()
    if not have:
        have = (mkv.exists() and mkv.stat().st_size > 2000) or _concat(ffmpeg, [sdir / s["video"] for s in segs], mkv)
        if have:                    # MP4 plays everywhere (the recording itself stays crash-safe Matroska until here)
            tmp = sdir / "screen.part.mp4"
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mkv), "-map", "0",
                                "-c", "copy", str(tmp)], capture_output=True, creationflags=NO_WINDOW)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 2000:
                video.unlink(missing_ok=True)
                tmp.replace(video)
                mkv.unlink(missing_ok=True)
            else:
                tmp.unlink(missing_ok=True)
                video = mkv
    meta = {"id": sdir.name, "created": st.get("started") or sdir.stat().st_mtime, "name": cfg.get("name") or "",
            "state": "done" if have else "failed", "notes": list(st.get("notes") or []), "markers": st.get("markers") or [],
            "method": st.get("method") or "", "seen": False, "video": "", "system": "", "webcam": "", "events": "",
            "poster": "", "duration": 0.0, "width": 0, "height": 0, "fps": int(cfg.get("fps") or 30), "mic": False,
            "recovered": st.get("state") in ("recording", "paused", "starting")}
    if st.get("state") == "failed" and not have:
        meta["recovered"] = False
    if st.get("state") == "cancelled":
        meta["state"] = "cancelled"
    if have:
        info = _probe(ffmpeg, video)
        meta.update(video=str(video), duration=round(info["duration"], 2), width=info["width"], height=info["height"],
                    mic=bool(info["audio"]))
        if info["duration"] < 0.3:
            meta["state"] = "failed"
            meta["notes"].append("The recording is empty.")
        sysw = sdir / "system.wav"
        if sysw.exists() or _concat_wav([sdir / s["sys"] for s in segs if s.get("sys")], sysw):
            meta["system"] = str(sysw)
        cam, cam4 = sdir / "webcam.mkv", sdir / "webcam.mp4"
        if cam4.exists() and not cam.exists():
            meta["webcam"] = str(cam4)
        elif cam.exists() or _concat(ffmpeg, [sdir / s["cam"] for s in segs if s.get("cam")], cam):
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(cam), "-c", "copy",
                                str(sdir / "webcam.part.mp4")], capture_output=True, creationflags=NO_WINDOW)
            if r.returncode == 0 and (sdir / "webcam.part.mp4").exists():
                (sdir / "webcam.part.mp4").replace(cam4)
                cam.unlink(missing_ok=True)
                meta["webcam"] = str(cam4)
            else:
                (sdir / "webcam.part.mp4").unlink(missing_ok=True)
                meta["webcam"] = str(cam)
        if (sdir / "events.jsonl").exists():
            meta["events"] = str(sdir / "events.jsonl")
        poster = sdir / "poster.jpg"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(2.0, info['duration'] / 2):.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(poster)],
                       capture_output=True, creationflags=NO_WINDOW)
        if poster.exists():
            meta["poster"] = str(poster)
    else:
        meta["notes"].append("Nothing was recorded. Open recorder.log in the recording's folder for the reason.")
    _write(sdir / "session.json", meta)
    st["state"] = meta["state"]
    _write(sdir / "state.json", st)
    return meta


# ================================================================ Windows message loop: hotkeys + input hooks
def _win_loop(rec: Recorder) -> None:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)        # real pixels on scaled displays
    except Exception:
        pass
    hk = {**DEFAULT_HOTKEYS, **(rec.cfg.get("hotkeys") or {})}
    actions: dict[int, str] = {}
    MOD_NOREPEAT = 0x4000
    failed = []
    for i, (action, text) in enumerate(list(hk.items()) + [("stop", EMERGENCY_STOP)], start=1):
        parsed = parse_hotkey(text)
        if parsed and user32.RegisterHotKey(None, i, parsed[0] | MOD_NOREPEAT, parsed[1]):
            actions[i] = action
        else:
            failed.append(f"{action} ({text})")
    if failed:
        rec.note("These shortcut keys couldn't be used (another program has them): " + ", ".join(failed)
                 + f". Emergency stop: {EMERGENCY_STOP.upper()}.")

    # low-level hooks: where the mouse clicks, when the wheel turns, when a key is pressed (never which key)
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class MSLL(ctypes.Structure):
        _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    ev = rec.events

    def mouse_proc(n, w, l):
        if n >= 0:
            try:
                if w in (0x201, 0x204, 0x207):                     # left / right / middle button down
                    m = ctypes.cast(l, ctypes.POINTER(MSLL)).contents
                    ev.add("click", m.pt.x, m.pt.y, b={0x201: "l", 0x204: "r", 0x207: "m"}[w])
                elif w == 0x20A:
                    m = ctypes.cast(l, ctypes.POINTER(MSLL)).contents
                    ev.add("wheel", m.pt.x, m.pt.y)
            except Exception:
                pass
        return user32.CallNextHookEx(None, n, w, l)

    def key_proc(n, w, l):
        if n >= 0 and w in (0x100, 0x104):
            now = time.time()
            if now - ev.last_key > 0.12:                           # typing activity only, a few marks a second
                ev.last_key = now
                ev.add("key")
        return user32.CallNextHookEx(None, n, w, l)
    mp, kp = HOOKPROC(mouse_proc), HOOKPROC(key_proc)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    stop_ev = threading.Event()
    hook_tid = [0]

    def hook_thread():
        # Its own thread with its own message pump: low-level hooks must answer within milliseconds, and the main
        # thread is busy for seconds while a segment starts or stops (that would make the mouse stutter).
        hook_tid[0] = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        hs = [user32.SetWindowsHookExW(14, mp, hmod, 0), user32.SetWindowsHookExW(13, kp, hmod, 0)]
        if not all(hs):
            _log(rec.sdir, "input tracking unavailable (hooks refused)")
        m = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(m))
            user32.DispatchMessageW(ctypes.byref(m))
        for h in hs:
            if h:
                user32.UnhookWindowsHookEx(ctypes.c_void_p(h))

    def cursor_sampler():
        pt, last = wintypes.POINT(), (None, None)
        while not stop_ev.is_set():
            if user32.GetCursorPos(ctypes.byref(pt)) and (pt.x, pt.y) != last:
                last = (pt.x, pt.y)
                ev.add("move", pt.x, pt.y)
            time.sleep(1 / 30)
    if rec.cfg.get("track_input", True):
        threading.Thread(target=hook_thread, daemon=True).start()
        threading.Thread(target=cursor_sampler, daemon=True).start()

    msg = wintypes.MSG()
    last_check = 0.0
    try:
        while not rec.done:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == 0x0312:                           # WM_HOTKEY
                    _act(rec, actions.get(int(msg.wParam), ""))
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            now = time.time()
            if now - last_check > 0.3:
                last_check = now
                _poll(rec)
            time.sleep(0.01)
    finally:
        stop_ev.set()
        if hook_tid[0]:
            user32.PostThreadMessageW(hook_tid[0], 0x0012, 0, 0)      # WM_QUIT: the hook thread unhooks and ends
        for i in actions:
            user32.UnregisterHotKey(None, i)


def _act(rec: Recorder, action: str) -> None:
    if action == "stop":
        rec.stop()
    elif action == "cancel":
        rec.stop(cancelled=True)
    elif action == "pause":
        rec.pause()
    elif action == "resume":
        rec.resume()
    elif action == "marker":
        rec.marker()


def _poll(rec: Recorder) -> None:
    """Commands from the app (control file) and health checks."""
    ctl = rec.sdir / "control"
    if ctl.exists():
        try:
            cmd = ctl.read_text(encoding="utf-8").strip()
            ctl.unlink()
        except OSError:
            cmd = ""
        _act(rec, cmd)
        if rec.done:
            return
    if time.time() - float(rec.state.get("beat") or 0) > 3:
        rec._save()
    if rec.state["state"] == "recording" and not rec.alive():
        rec.note("The screen capture stopped by itself. What was recorded until then is saved.")
        beep("error")
        rec.stop()
    limit = float(rec.cfg.get("max_minutes") or 0) * 60
    if limit and rec.recorded + (time.time() - rec.seg_t0 if rec.state["state"] == "recording" else 0) > limit:
        rec.note("Stopped at the time limit you set.")
        rec.stop()


def _plain_loop(rec: Recorder) -> None:
    """No hotkeys (non-Windows / tests): driven by the control file only."""
    while not rec.done:
        _poll(rec)
        time.sleep(0.1)


# ================================================================ entry point
def run(session_dir: str) -> int:
    sdir = Path(session_dir)
    rec = Recorder(sdir)
    try:
        delay = float(rec.cfg.get("start_delay") or 0)
        if delay > 0:
            time.sleep(delay)                 # lets the app window finish closing before the first frame
        beep("start")                         # before the capture starts, so the beep isn't in the recording
        if not rec.start_segment():
            rec.note("The screen couldn't be captured on this PC with any method.")
            beep("error")
            rec.state["state"] = "failed"
            rec._save()
        else:
            (_win_loop if IS_WIN and not rec.cfg.get("test_source") else _plain_loop)(rec)
    except Exception as e:      # whatever happens, save what exists and bring the app back
        _log(sdir, f"recorder error: {type(e).__name__}: {e}")
        try:
            rec.stop()
        except Exception:
            pass
    relaunch = rec.cfg.get("relaunch") or []
    if relaunch:                  # the app comes back right away; it shows "saving" until the file is ready
        try:
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
            env.pop("_MEIPASS2", None)
            subprocess.Popen(relaunch, cwd=rec.cfg.get("cwd") or None, env=env,
                             creationflags=(0x00000008 | 0x00000200) if IS_WIN else 0)
        except Exception as e:
            _log(sdir, f"couldn't reopen the app: {e}")
    meta = {}
    try:
        meta = finalize(sdir)
    except Exception as e:
        _log(sdir, f"finalize error: {type(e).__name__}: {e}")
    if meta.get("state") == "cancelled":
        for f in sdir.glob("*"):
            if f.name not in ("recorder.log", "session.json", "state.json", "config.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
