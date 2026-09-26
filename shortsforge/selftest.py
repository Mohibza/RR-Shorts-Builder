"""`RR Shorts Builder.exe --selftest`: verifies a packaged build can do real work, writes a report file.

Checks every heavy dependency, FFmpeg, the face detector, fonts, the speech-model runtime and renders a
short test clip end-to-end with captions, sound effects and a camera move.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run() -> int:
    from .config import data_dir
    report = data_dir() / "selftest.txt"
    lines, ok = [], True

    def check(name, fn):
        nonlocal ok
        t = time.time()
        try:
            detail = fn() or ""
            lines.append(f"PASS  {name}  {detail}  ({time.time() - t:.1f}s)")
        except Exception as e:
            ok = False
            lines.append(f"FAIL  {name}: {e}\n{traceback.format_exc(limit=3)}")

    for mod in ("PySide6.QtWidgets", "PySide6.QtMultimedia", "numpy", "cv2", "faster_whisper", "ctranslate2",
                "onnxruntime", "av", "yt_dlp", "websockets", "mutagen", "huggingface_hub"):
        check(f"import {mod}", lambda m=mod: getattr(importlib.import_module(m), "__version__", ""))

    def ffmpeg():
        from .utils import find_ffmpeg, pick_encoder
        return f"{find_ffmpeg()} · encoder {pick_encoder('auto')}"
    check("ffmpeg", ffmpeg)

    def deno():
        import shutil
        return shutil.which("deno") or "not found (YouTube downloads may be limited)"
    check("deno (YouTube JS runtime)", deno)

    def faces():
        from .facetrack import _detectors
        if _detectors() is None:
            raise RuntimeError("face detector files missing")
        return "haar cascades ok"
    check("face detector", faces)

    def fonts_ok():
        from . import fonts
        miss = fonts.missing_fonts()
        if miss:
            raise RuntimeError(f"missing {miss}")
        return f"{len(fonts.FONT_SOURCES)} fonts"
    check("caption fonts", fonts_ok)

    def vad():
        import faster_whisper
        assets = Path(faster_whisper.__file__).parent / "assets"
        found = list(assets.glob("*.onnx"))
        if not found:
            raise RuntimeError(f"no VAD model in {assets}")
        return found[0].name
    check("speech VAD model", vad)

    def render():
        from .captions import TextPlan, build_ass
        from .renderer import RenderJob, render as do_render
        from .sfx import mix_track, plan
        from .utils import run_ffmpeg
        tmp = Path(tempfile.mkdtemp(prefix="sf_selftest_"))
        src = tmp / "src.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=4", "-f", "lavfi", "-i", "sine=f=220:d=4",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], 4)
        words = [{"w": w, "s": 0.3 + i * 0.4, "e": 0.6 + i * 0.4} for i, w in enumerate("kya kar rahe ho yaar".split())]
        ass = tmp / "c.ass"
        ass.write_text(build_ass(words, 3.5, TextPlan(caption_style="pill", hook_style="yellow_impact",
                                                      hook_text="Self test", cta_style=None)), encoding="utf-8")
        fx = mix_track(plan("medium", 3.5, words, [0.3, 1.5], {"pop": True}, "pop", "flash", "punch", [1.0],
                            False, set()), 3.5, str(tmp / "fx.wav"))
        out = tmp / "out.mp4"
        do_render(RenderJob(src=str(src), start=0.2, end=3.7, out_path=str(out), src_w=1280, src_h=720,
                            ass_file=str(ass), layout="blur_fit", motion="punch", punch_times=[1.0], intro="flash",
                            sfx=fx or "", fps=30, crf=23))
        size = out.stat().st_size
        if size < 20000:
            raise RuntimeError("rendered file is too small")
        return f"{size // 1024} KB test Short"
    check("render a test Short", render)

    lines.insert(0, f"RR Shorts Builder self-test · {'ALL PASSED' if ok else 'PROBLEMS FOUND'} · python {sys.version.split()[0]}"
                    f" · frozen={getattr(sys, 'frozen', False)}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        print("\n".join(lines))
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(run())
