"""Rebels Revolt Shorts desktop app entry point.

Start it with run.bat, the desktop shortcut, or by double-clicking this file.
CLI:  .venv\\Scripts\\python -m shortsforge <url-or-file> [--count 5]
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _log_path() -> Path:
    try:
        sys.path.insert(0, str(HERE))
        from shortsforge.config import data_dir   # stdlib only; migrates the old ShortsForge folder
        base = data_dir()
    except Exception:
        base = Path(os.environ.get("APPDATA", Path.home())) / "RRShortsBuilder"
        base.mkdir(parents=True, exist_ok=True)
    return base / "rrshorts.log"


def _alert(title: str, msg: str) -> None:
    """Show an error even when there is no console (pythonw)."""
    try:
        print(f"{title}: {msg}", file=sys.stderr)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)
        except Exception:
            pass


def _relaunch_in_venv() -> bool:
    """If started with the wrong Python (e.g. double-clicking app.py), restart with the app's own .venv."""
    venv = HERE / ".venv" / "Scripts"
    target = venv / "pythonw.exe" if (venv / "pythonw.exe").exists() else venv / "python.exe"
    if os.name != "nt" or not target.exists():
        return False
    try:
        if Path(sys.executable).resolve().parent == venv.resolve():
            return False  # already inside the venv
    except OSError:
        pass
    subprocess.Popen([str(target), str(HERE / "app.py"), *sys.argv[1:]], cwd=str(HERE),
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    return True


def _fix_std_streams():
    """pythonw has no console: send output to a log file so libraries that print progress don't crash."""
    if sys.stdout is None or sys.stderr is None:
        f = open(_log_path(), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f


def _bundle_paths():
    """Make bundled tools (ffmpeg, deno) in <app>/bin visible to everything, incl. yt-dlp."""
    dirs = [Path(sys.executable).resolve().parent / "bin", HERE / "bin"]
    if getattr(sys, "frozen", False):
        dirs.insert(0, Path(getattr(sys, "_MEIPASS", HERE)) / "bin")
    extra = [str(d) for d in dirs if d.is_dir()]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def _rename_old_shortcut() -> None:
    """v1.5 rebrand: the desktop shortcut made by an older setup.bat was called 'ShortsForge'."""
    if os.name != "nt":
        return
    try:
        desk = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        new = desk / "Rebels Revolt Shorts.lnk"
        for old in (desk / "ShortsForge.lnk", desk / "RR Shorts Builder.lnk"):
            if old.exists() and not new.exists():
                old.rename(new)
    except OSError:
        pass


def _web_ui_available() -> bool:
    from shortsforge.config import app_root
    return (app_root() / "web" / "dist" / "index.html").exists()


def _already_running() -> bool:
    """Second start: bring the open window to the front instead of starting another engine."""
    import json
    import urllib.request
    from shortsforge.config import data_dir
    f = data_dir() / "ui.lock"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{d['port']}/api/focus", data=b"{}", method="POST",
                                     headers={"X-RR-Token": d["token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def run_web() -> int:
    """New interface: the engine + local server, shown in a native window (WebView2 / Edge)."""
    import json
    import threading
    from shortsforge import server
    from shortsforge.config import data_dir

    if _already_running():
        return 0
    app = server.App()
    app.serve_background()
    lock = data_dir() / "ui.lock"
    try:
        lock.write_text(json.dumps({"port": app.port, "token": app.token, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    url = app.url
    print(f"Rebels Revolt Shorts engine on {url.split('#')[0]}", flush=True)
    try:
        try:
            import webview  # pywebview (Microsoft Edge WebView2 on Windows)
        except Exception:
            webview = None
        if webview is not None and "--browser" not in sys.argv:
            try:
                return _run_webview(webview, app, url)
            except Exception:
                traceback.print_exc()
        return _run_app_window(app, url)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass
        app.shutdown()


def _run_webview(webview, app, url: str) -> int:
    from shortsforge import server
    from shortsforge.config import data_dir
    types = {"video": ("Video files (*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.flv;*.wmv)", "All files (*.*)"),
             "music": ("Audio files (*.mp3;*.m4a;*.wav;*.ogg;*.aac;*.flac)", "All files (*.*)"),
             "cookies": ("Cookies (*.txt)", "All files (*.*)")}
    kw = dict(width=1440, height=900, min_size=(1024, 620), background_color="#07070D", text_select=True)
    try:
        win = webview.create_window("Rebels Revolt Shorts", url, maximized=True, **kw)
    except TypeError:                     # older pywebview
        win = webview.create_window("Rebels Revolt Shorts", url, **kw)

    def dialog(kind: str, multi: bool):
        if kind == "folder":
            r = win.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            r = win.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=multi,
                                       file_types=types.get(kind, ("All files (*.*)",)))
        return list(r or [])
    server.FILE_DIALOG = dialog

    def focus():
        try:
            win.restore()
            win.show()
        except Exception:
            pass
    server.FOCUS = focus

    def on_closing():
        if app.engine.busy():
            return win.create_confirmation_dialog(
                "Quit Rebels Revolt Shorts", "Clips are still being made. Quit anyway?")
        return True
    try:
        win.events.closing += on_closing
    except Exception:
        pass

    def on_shown():
        try:
            win.maximize()
        except Exception:
            pass
        _set_window_icon("Rebels Revolt Shorts")
    try:
        win.events.shown += on_shown
    except Exception:
        pass
    webview.start(gui="edgechromium" if os.name == "nt" else None, private_mode=False,
                  storage_path=str(data_dir() / "webview"))
    return 0


def _set_window_icon(title: str) -> None:
    """Show the app's own icon in the title bar and taskbar (instead of python.exe's)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import time as _t
        from ctypes import wintypes
        from shortsforge.config import ASSETS
        ico = str(ASSETS / "icon.ico")
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        hwnd = None
        for _ in range(50):                      # the window may take a moment to appear
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            _t.sleep(0.1)
        if not hwnd:
            return
        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x10, 1, 0x80
        big = user32.LoadImageW(None, ico, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, small)
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, big)
    except Exception:
        traceback.print_exc()


def _run_app_window(app, url: str) -> int:
    """Fallback without pywebview: Edge/Chrome in app mode (its own profile, no tabs or address bar)."""
    from shortsforge import browser_login
    from shortsforge.config import data_dir
    b = None
    if os.name == "nt":
        for p in (Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe"):
            if p.exists():
                b = str(p)
                break
    if not b:
        found = browser_login.find_browser()
        b = found[1] if found else None
    if not b:
        import webbrowser
        webbrowser.open(url)
        _alert("Rebels Revolt Shorts", "The app opened in your web browser. Keep this message open while you "
               "use it; click OK to quit the app.")
        return 0
    prof = data_dir() / "appwindow"
    prof.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([b, f"--app={url}", f"--user-data-dir={prof}", "--start-maximized",
                             "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"])
    from shortsforge import server
    server.FOCUS = lambda: None
    proc.wait()
    return 0


def main():
    _bundle_paths()
    if "--selftest" in sys.argv:
        _fix_std_streams()
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from shortsforge.selftest import run
        sys.exit(run())
    os.chdir(HERE)
    _rename_old_shortcut()
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        import PySide6  # noqa: F401
    except ImportError:
        if _relaunch_in_venv():
            return
        _alert("Rebels Revolt Shorts", "The app's packages are not installed.\n\nRun setup.bat first, "
                              "then start Rebels Revolt Shorts with run.bat or the desktop shortcut.")
        return
    _fix_std_streams()
    if os.name == "nt":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EagleEyeCodes.RRShortsBuilder")
        except Exception:
            pass
    if "--classic" not in sys.argv and _web_ui_available():
        sys.exit(run_web())

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if os.name == "nt":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EagleEyeCodes.RRShortsBuilder")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Rebels Revolt Shorts")
    app.setStyle("Fusion")
    from ui import theme
    from ui.main_window import MainWindow

    def _excepthook(t, v, tb):
        from PySide6.QtWidgets import QMessageBox
        traceback.print_exception(t, v, tb)
        QMessageBox.critical(None, "Unexpected error", f"{t.__name__}: {v}")

    sys.excepthook = _excepthook
    scr = app.primaryScreen().availableGeometry()
    app.setStyleSheet(theme.scaled_qss(theme.screen_scale(scr.width(), scr.height())))
    app.setWindowIcon(theme.app_icon())
    w = MainWindow()
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        err = traceback.format_exc()
        try:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(err + "\n")
        except Exception:
            pass
        _alert("Rebels Revolt Shorts failed to start", err[-1500:] + f"\n\nFull log: {_log_path()}")
