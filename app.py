"""RR Shorts Builder desktop app entry point.

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
    subprocess.Popen([str(target), str(HERE / "app.py")], cwd=str(HERE),
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
        old, new = desk / "ShortsForge.lnk", desk / "RR Shorts Builder.lnk"
        if old.exists() and not new.exists():
            old.rename(new)
    except OSError:
        pass


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
        _alert("RR Shorts Builder", "The app's packages are not installed.\n\nRun setup.bat first, "
                              "then start RR Shorts Builder with run.bat or the desktop shortcut.")
        return
    _fix_std_streams()

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
    app.setApplicationName("RR Shorts Builder")
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
        _alert("RR Shorts Builder failed to start", err[-1500:] + f"\n\nFull log: {_log_path()}")
