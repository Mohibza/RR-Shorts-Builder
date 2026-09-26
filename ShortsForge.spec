# PyInstaller build spec: python -m PyInstaller ShortsForge.spec --noconfirm --clean
# Produces dist/ShortsForge/ (ShortsForge.exe + everything it needs, no Python install required).
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for pkg in ("yt_dlp", "yt_dlp_ejs", "websockets", "mutagen", "huggingface_hub"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
for pkg in ("cv2", "yt_dlp_ejs", "certifi"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass
datas += [("assets", "assets")]
hiddenimports += ["shortsforge.selftest", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSvg"]

excludes = ["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
            "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick3D",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtDesigner",
            "PySide6.QtBluetooth", "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors",
            "PySide6.QtSerialPort", "PySide6.QtTest", "torch", "tensorflow", "scipy", "pandas",
            "imageio_ffmpeg", "PIL", "cryptography", "onnxruntime.quantization", "onnxruntime.tools"]

a = Analysis(["app.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ShortsForge",
          icon="assets/icon.ico" if sys.platform == "win32" else None,
          console=False, upx=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ShortsForge")
