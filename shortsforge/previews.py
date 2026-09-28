"""Render still previews of caption / hook / CTA templates and color grades for the Styles gallery."""
from __future__ import annotations

from pathlib import Path

from . import fonts
from .captions import TextPlan, build_ass
from .config import CACHE_DIR
from .effects import COLOR_GRADES
from .utils import ffmpeg_cwd, filter_path, run_ffmpeg

PREV = CACHE_DIR / "previews"
PREV.mkdir(parents=True, exist_ok=True)
SAMPLE = ["This", "changed", "everything", "for", "me", "in", "2026"]
BG = "gradients=s=540x960:c0=0x1d2671:c1=0xc33764:c2=0x0f2027:x0=0:y0=0:x1=540:y1=960:speed=0.0001:duration=12"


def _words():
    t, out = 0.2, []
    for w in SAMPLE:
        out.append({"w": w, "s": t, "e": t + 0.3})
        t += 0.34
    return out


def _render(ass_text: str, out: Path, at: float, grade: str = "") -> Path:
    ass = out.with_suffix(".ass")
    ass.write_text(ass_text, encoding="utf-8")
    vf = (grade + "," if grade else "") + f"ass=filename={filter_path(ass)}:fontsdir={filter_path(fonts.fonts_dir())}"
    run_ffmpeg(["-f", "lavfi", "-i", BG, "-ss", f"{at:.2f}", "-vf", vf, "-frames:v", "1", "-update", "1",
                str(out)], 1.0, cwd=ffmpeg_cwd())
    ass.unlink(missing_ok=True)
    return out


def _tag() -> str:
    return ("f" if not fonts.missing_fonts() else "n") + "3"  # bump when caption looks change


def caption_preview(style: str) -> Path:
    out = PREV / f"cap_{style}_{_tag()}.png"
    if out.exists():
        return out
    plan = TextPlan(caption_style=style, hook_style=None, cta_style=None, position="middle",
                    emphasis={"2026", "everything"})
    return _render(build_ass(_words(), 3.0, plan), out, 0.62)


def hook_preview(style: str) -> Path:
    out = PREV / f"hook_{style}_{_tag()}.png"
    if out.exists():
        return out
    plan = TextPlan(caption_style="none", hook_style=style, cta_style=None, hook_text="The secret nobody tells you")
    return _render(build_ass([], 5.0, plan), out, 1.2)


def cta_preview(style: str) -> Path:
    out = PREV / f"cta_{style}_{_tag()}.png"
    if out.exists():
        return out
    plan = TextPlan(caption_style="none", hook_style=None, cta_style=style, cta_text="Follow for more")
    return _render(build_ass([], 10.0, plan), out, 8.6)


def grade_preview(grade: str) -> Path:
    out = PREV / f"grade_{grade}.png"
    if out.exists():
        return out
    plan = TextPlan(caption_style="none", hook_style=None, cta_style=None,
                    hook_text="")
    plan.watermark = COLOR_GRADES[grade]["name"]
    vf = COLOR_GRADES[grade]["vf"]
    # colourful test card so grades are visible
    ass = out.with_suffix(".ass")
    ass.write_text(build_ass([], 2.0, plan), encoding="utf-8")
    graph = "testsrc2=s=540x960:d=1," + (vf + "," if vf else "") + \
        f"ass=filename={filter_path(ass)}:fontsdir={filter_path(fonts.fonts_dir())}"
    run_ffmpeg(["-f", "lavfi", "-i", graph, "-frames:v", "1", "-update", "1", str(out)], 1.0, cwd=ffmpeg_cwd())
    ass.unlink(missing_ok=True)
    return out


def clear() -> None:
    for f in PREV.glob("*.png"):
        f.unlink(missing_ok=True)
