"""App side of the screen recorder: what can be recorded (screens, windows, microphones, webcams), picking a
region, starting the recorder process, listing/recovering recordings."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from . import recorder
from .config import app_root, data_dir
from .utils import NO_WINDOW, find_ffmpeg

REC_DIR = data_dir() / "recordings"
IS_WIN = os.name == "nt"


# ------------------------------------------------------------------ what can be recorded
def monitors() -> list[dict]:
    if not IS_WIN:
        return [{"idx": 0, "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "name": "Screen 1"}]
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    out: list[dict] = []

    class MI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT),
                              ctypes.c_void_p)

    def cb(hmon, _hdc, _rc, _data):
        mi = MI()
        mi.cbSize = ctypes.sizeof(MI)
        if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                        "primary": bool(mi.dwFlags & 1)})
        return 1
    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    except Exception:
        pass
    out.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    for i, m in enumerate(out):
        m["idx"] = i
        m["name"] = f"Screen {i + 1}" + (" (main)" if m["primary"] else "") + f" · {m['w']}×{m['h']}"
    return out or [{"idx": 0, "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "name": "Screen 1"}]


def windows() -> list[str]:
    if not IS_WIN:
        return []
    import ctypes
    user32 = ctypes.windll.user32
    titles: list[str] = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _l):
        if user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and not user32.IsIconic(ctypes.c_void_p(hwnd)):
            n = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, n + 1)
                t = buf.value.strip()
                if t and t not in ("Program Manager", "Rebels Revolt Shorts") and t not in titles:
                    titles.append(t)
        return 1
    try:
        user32.EnumWindows(PROC(cb), 0)
    except Exception:
        pass
    return titles[:60]


def dshow_devices() -> dict:
    """{'mics': [...], 'cams': [...]} as FFmpeg (DirectShow) names them."""
    out = {"mics": [], "cams": []}
    if not IS_WIN:
        return out
    try:
        r = subprocess.run([find_ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                           creationflags=NO_WINDOW)
    except Exception:
        return out
    kind = ""
    for line in (r.stderr or "").splitlines():
        low = line.lower()
        if "directshow video devices" in low:
            kind = "cams"
            continue
        if "directshow audio devices" in low:
            kind = "mics"
            continue
        m = re.search(r'"([^"]+)"', line)
        if not m or "alternative name" in low:
            continue
        name = m.group(1)
        k = "mics" if "(audio)" in low else "cams" if "(video)" in low else kind
        if k and name not in out[k] and not name.startswith("@device"):
            out[k].append(name)
    return out


def devices() -> dict:
    from .utils import pick_encoder
    d = dshow_devices()
    try:
        enc = pick_encoder("auto")
    except Exception:
        enc = "libx264"
    return {"monitors": monitors(), "windows": windows(), "mics": d["mics"], "cams": d["cams"],
            "system_audio": recorder.system_audio_available(), "encoder": enc, "windows_os": IS_WIN,
            "defaults": recorder.DEFAULT_HOTKEYS, "emergency_stop": recorder.EMERGENCY_STOP}


def pick_region() -> Optional[dict]:
    """Let the user drag a rectangle on the screen. Returns {x, y, w, h} in real pixels, or None if cancelled."""
    if not IS_WIN:
        return None
    ps = r'''
Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class Dpi { [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v); }
"@
try { [Dpi]::SetProcessDpiAwareness(2) | Out-Null } catch {}
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$f = New-Object System.Windows.Forms.Form
$f.FormBorderStyle = 'None'; $f.StartPosition = 'Manual'; $f.Bounds = $vs; $f.TopMost = $true
$f.BackColor = [System.Drawing.Color]::Black; $f.Opacity = 0.35; $f.Cursor = [System.Windows.Forms.Cursors]::Cross
$f.ShowInTaskbar = $false; $f.KeyPreview = $true
$script:a = $null; $script:r = $null
$f.Add_KeyDown({ if ($_.KeyCode -eq 'Escape') { $f.Close() } })
$f.Add_MouseDown({ $script:a = $_.Location })
$f.Add_MouseMove({ if ($script:a) {
  $x = [Math]::Min($script:a.X, $_.X); $y = [Math]::Min($script:a.Y, $_.Y)
  $script:r = New-Object System.Drawing.Rectangle($x, $y, [Math]::Abs($_.X - $script:a.X), [Math]::Abs($_.Y - $script:a.Y)); $f.Invalidate() } })
$f.Add_Paint({ if ($script:r) { $p = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 3)
  $_.Graphics.FillRectangle([System.Drawing.Brushes]::Gray, $script:r); $_.Graphics.DrawRectangle($p, $script:r) } })
$f.Add_MouseUp({ $f.Close() })
[void]$f.ShowDialog()
if ($script:r -and $script:r.Width -gt 40 -and $script:r.Height -gt 40) {
  "$($script:r.X + $vs.X),$($script:r.Y + $vs.Y),$($script:r.Width),$($script:r.Height)" }
'''
    import base64
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-EncodedCommand", enc], capture_output=True, text=True,
                           timeout=180, creationflags=NO_WINDOW)
    except Exception:
        return None
    m = re.search(r"(-?\d+),(-?\d+),(\d+),(\d+)", r.stdout or "")
    if not m:
        return None
    x, y, w, h = (int(v) for v in m.groups())
    return {"x": x, "y": y, "w": w // 2 * 2, "h": h // 2 * 2}


# ------------------------------------------------------------------ start
def relaunch_cmd() -> tuple[list[str], str]:
    """How to start the app again after recording (and the folder to start it in)."""
    root = app_root()
    if getattr(sys, "frozen", False):
        return [sys.executable], str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return [str(pyw if pyw.exists() else exe), str(root / "app.py")], str(root)


def start(cfg: dict) -> dict:
    """Write the session and start the recorder process (detached: it outlives the app)."""
    probs = recorder.hotkey_problems({**recorder.DEFAULT_HOTKEYS, **(cfg.get("hotkeys") or {})})
    if probs:
        raise ValueError("Fix the shortcut keys first: " + "; ".join(f"{k}: {v}" for k, v in probs.items()))
    active = active_session()
    if active:
        raise ValueError("A recording is already running. Stop it first.")
    sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    sdir = REC_DIR / sid
    sdir.mkdir(parents=True, exist_ok=True)
    cmd, cwd = relaunch_cmd()
    from .utils import pick_encoder
    try:
        enc = pick_encoder("auto")
    except Exception:
        enc = "libx264"
    full = {**cfg, "id": sid, "ffmpeg": find_ffmpeg(), "encoder": enc, "relaunch": cmd, "cwd": cwd,
            "hotkeys": {**recorder.DEFAULT_HOTKEYS, **(cfg.get("hotkeys") or {})},
            "start_delay": float(cfg.get("start_delay", 1.2))}
    (sdir / "config.json").write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    if getattr(sys, "frozen", False):
        launch = [sys.executable, "--record", str(sdir)]
    else:
        launch = [cmd[0], str(app_root() / "app.py"), "--record", str(sdir)]
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")     # packaged app: the recorder is its own instance
    env.pop("_MEIPASS2", None)
    subprocess.Popen(launch, cwd=cwd, close_fds=True, env=env,
                     creationflags=(0x00000008 | 0x00000200) if IS_WIN else 0,
                     start_new_session=not IS_WIN)
    return {"id": sid, "dir": str(sdir)}


# ------------------------------------------------------------------ sessions
def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _running(st: Optional[dict]) -> bool:
    """Is this session's recorder process really still at work? (The process id alone isn't proof: Windows hands
    old ids to new programs, so the recorder also leaves a fresh time mark every few seconds.)"""
    if not st or st.get("state") not in ("starting", "recording", "paused", "finishing"):
        return False
    if not _pid_alive(st.get("pid", 0)):
        return False
    age = time.time() - float(st.get("beat") or st.get("started") or 0)
    return age < (3600 if st["state"] == "finishing" else 25)


def active_session() -> Optional[dict]:
    """The recording that is running right now (the recorder process is alive), if any."""
    if not REC_DIR.exists():
        return None
    for d in sorted(REC_DIR.iterdir(), reverse=True)[:8]:
        st = recorder._read(d / "state.json")
        if _running(st):
            return {"id": d.name, "state": st["state"], "started": st.get("started", 0)}
    return None


def control(sid: str, cmd: str) -> bool:
    d = REC_DIR / Path(sid).name
    if cmd not in ("stop", "pause", "resume", "cancel", "marker") or not d.is_dir():
        return False
    (d / "control").write_text(cmd, encoding="utf-8")
    return True


def sessions(recover: bool = True) -> list[dict]:
    """Finished recordings, newest first. Interrupted ones (crash / power cut) are stitched and included."""
    out = []
    if not REC_DIR.exists():
        return out
    for d in sorted(REC_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = recorder._read(d / "session.json")
        if meta is None and recover:
            st = recorder._read(d / "state.json") or {}
            if _running(st):
                continue                      # still recording, or the recorder is saving it right now
            if (d / "config.json").exists():
                try:
                    meta = recorder.finalize(d)
                except Exception:
                    meta = None
        if meta and meta.get("state") in ("done", "failed"):
            meta["dir"] = str(d)
            out.append(meta)
    return out


def update(sid: str, **patch) -> Optional[dict]:
    d = REC_DIR / Path(sid).name
    meta = recorder._read(d / "session.json")
    if meta is None:
        return None
    meta.update(patch)
    recorder._write(d / "session.json", meta)
    return meta


def delete(sid: str) -> None:
    d = REC_DIR / Path(sid).name
    if d.is_dir() and d.parent == REC_DIR:
        shutil.rmtree(d, ignore_errors=True)
