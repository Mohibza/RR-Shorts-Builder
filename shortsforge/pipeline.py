"""End-to-end: source -> download -> transcribe -> pick highlights -> style -> render."""
from __future__ import annotations

import json
import random
import re
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import fonts
from .captions import CAPTION_STYLES, CTA_STYLES, HOOK_STYLES, TextPlan, build_ass, chunk_words
from .config import CACHE_DIR, Settings, usable_output_dir
from .downloader import SourceItem, download
from .effects import COLOR_GRADES, INTROS, LAYOUTS, MOTIONS
from . import sfx
from .facetrack import camera_path, track_faces
from .pacing import tighten
from . import director
from .llm import AI
from .romanize import romanize_text, romanize_transcript
from .highlights import INTENSE, Clip, words_in_range
from .renderer import RenderJob, preview_frame, render, thumbnail
from . import metadata, music_sources
from .transcriber import ModelDownloadError, energy_curve, extract_audio, transcribe
from .utils import Cancelled, probe, safe_name, set_ffmpeg_override

PLAN_SUFFIX = ".sf.json"
WORK = CACHE_DIR / "work"
MUSIC_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}


@dataclass
class StyleChoice:
    caption_style: str
    hook_style: Optional[str]
    cta_style: Optional[str]
    color_grade: str
    motion: str
    intro: str
    layout: str
    position: str = "lower"
    place: dict = field(default_factory=dict)   # manual placement (see captions.TextPlan.place)

    def label(self) -> str:
        return " · ".join([
            CAPTION_STYLES.get(self.caption_style, {}).get("name", self.caption_style),
            COLOR_GRADES.get(self.color_grade, {}).get("name", self.color_grade),
            MOTIONS.get(self.motion, self.motion),
        ])


class StyleRotator:
    """Hands out a different combination for every Short (or the fixed choice)."""

    def __init__(self, s: Settings, seed: int):
        self.s = s
        self.rnd = random.Random(seed)
        self.pools = {}
        for key, options in (
            ("caption_style", list(CAPTION_STYLES)),
            ("hook_style", list(HOOK_STYLES)),
            ("cta_style", list(CTA_STYLES)),
            ("color_grade", [k for k in COLOR_GRADES if k != "none"]),
            ("motion", [k for k in MOTIONS if k != "none"]),
            ("intro", list(INTROS)),
        ):
            chosen = {"caption_style": s.caption_pool, "hook_style": s.hook_pool}.get(key) or []
            options = [o for o in options if o in chosen] or options
            self.rnd.shuffle(options)
            self.pools[key] = options

    def pick(self, key: str, i: int) -> str:
        val = getattr(self.s, key)
        if val == "random":
            pool = self.pools[key]
            return pool[i % len(pool)]
        return val

    def choice(self, i: int) -> StyleChoice:
        return StyleChoice(
            caption_style=self.pick("caption_style", i),
            hook_style=self.pick("hook_style", i) if self.s.hook_title else None,
            cta_style=self.pick("cta_style", i) if self.s.cta_text.strip() else None,
            color_grade=self.pick("color_grade", i),
            motion=self.pick("motion", i),
            intro=self.pick("intro", i),
            layout=self.s.layout,
            position=self.s.caption_position,
            place=dict(getattr(self.s, "placement", {}) or {}),
        )


@dataclass
class ShortResult:
    path: str
    thumb: str
    title: str
    start: float
    end: float
    score: float
    style: str
    plan_file: str
    meta: dict = field(default_factory=dict)


def _hashtags(keywords: list[str], lang: str) -> list[str]:
    tags = ["#shorts"]
    for k in keywords[:4]:
        k = re.sub(r"[^\w]", "", k)
        if len(k) > 2:
            tags.append("#" + k.lower())
    return tags


def _punch_times(words: list[dict], emphasis: set) -> list[float]:
    times, last = [], -10.0
    for w in words:
        k = re.sub(r"[^\w']", "", w["w"].lower())
        strong = k in emphasis or w["w"].rstrip().endswith(("?", "!"))
        if (strong and w["s"] - last > 2.2) or w["s"] - last > 5.5:
            times.append(w["s"])
            last = w["s"]
    return times


def _out_fps(setting: int, src_fps: float) -> int:
    if setting:
        return int(setting)
    f = int(round(src_fps or 30))
    return 60 if f >= 48 else (f if f in (24, 25, 30) else 30)


def _pick_music(folder: str, rnd: random.Random, only: Optional[list] = None) -> str:
    try:
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in MUSIC_EXT)
    except Exception:
        return ""
    if only:
        starred = [p for p in files if p.name in set(only)]
        files = starred or files
    return str(rnd.choice(files)) if files else ""


def _music_offset(path: str, need: float, rnd: random.Random) -> float:
    """Start somewhere in the first part of the track (skips quiet intros, varies Shorts)."""
    try:
        import mutagen
        length = float(mutagen.File(path).info.length)
    except Exception:
        try:
            length = probe(path)["duration"]
        except Exception:
            return 0.0
    room = length - need - 1.0
    return round(rnd.uniform(min(3.0, max(0.0, room)), max(0.0, min(room, length * 0.45))), 2) if room > 3 else 0.0


class Pipeline:
    def __init__(self, settings: Settings,
                 log: Callable[[str], None] = print,
                 progress: Callable[[str, float, str], None] = lambda *a: None,
                 on_short: Callable[[ShortResult], None] = lambda r: None,
                 cancel: Optional[threading.Event] = None):
        self.s = settings
        self.log = log
        self.progress = progress
        self.on_short = on_short
        self.cancel = cancel or threading.Event()
        self.failed: list[tuple[int, str]] = []   # (Short number, error) of Shorts that couldn't be rendered
        set_ffmpeg_override(settings.ffmpeg_path)

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def process(self, item: SourceItem) -> list[ShortResult]:
        s = self.s
        P = self.progress
        self.failed = []
        self.log(f"▶ {item.title}")
        fonts.fonts_dir()

        # 1. download
        P("Downloading", 0.0, "")
        src = download(item, lambda f, d: P("Downloading", 0.25 * f, d), self.cancel, s.cookies_browser,
                       max_height=int(s.source_quality or 1440))
        self._check()
        info = probe(src)
        dur = info["duration"]
        self.log(f"Source: {info['width']}x{info['height']}, {dur / 60:.1f} min")

        # 2. audio + transcript
        transcript = {"language": s.language if s.language != "auto" else "en", "segments": []}
        energy = None
        if info["has_audio"]:
            P("Extracting audio", 0.25, "")
            wav = extract_audio(src, item.vid, dur, self.cancel, lambda f: P("Extracting audio", 0.25 + 0.04 * f, ""))
            energy = energy_curve(wav)
            self._check()
            try:
                transcript = transcribe(
                    wav, item.vid, dur, s.whisper_model, s.language, s.use_gpu,
                    lambda f, d: P("Transcribing speech", 0.30 + 0.35 * f, d), self.cancel, self.log,
                    on_stage=lambda st, f, d: P(st, 0.29 + 0.01 * f, d),
                    caption_lang=getattr(s, "caption_lang", "auto"))
            except (Cancelled, ModelDownloadError):
                raise  # no speech model = no captions and blind picks: stop with a clear message instead
            except Exception as e:
                self.log(f"Transcription failed ({e}). Falling back to audio-energy highlights without captions.")
        import numpy as np
        if energy is None:
            energy = np.zeros(max(1, int(dur * 10)))
        self._check()

        # 3. highlights
        P("Finding best moments", 0.66, "")
        ai = AI.from_settings(s, self.log)
        clips = director.pick(transcript, energy, dur, s, self.log, ai=ai)
        # Roman Urdu / Roman Hindi captions: convert just the words inside the chosen Shorts
        if getattr(s, "caption_lang", "roman") == "roman" and clips:
            P("Writing Roman Urdu captions", 0.68, "")
            ranges = [p for c in clips for p in c.parts()]
            n_conv = romanize_transcript(transcript, ranges, item.vid, ai, self.log)
            if n_conv:
                self.log(f"Converted {n_conv} words to Roman script" + (f" with {ai.label}" if ai else ""))
                for c in clips:
                    c.title = romanize_text(c.title, transcript)
                    c.hook = romanize_text(c.hook, transcript)
                    c.text = romanize_text(c.text, transcript)
                    c.keywords = [romanize_text(k, transcript) for k in c.keywords]
        if not clips:
            raise RuntimeError("No usable moments found in this video.")
        P("Writing titles, descriptions and tags", 0.69, "")
        seo = metadata.generate(ai, clips, item.title, director.title_language(s, transcript), item.source, self.log)
        for c, m in zip(clips, seo):
            c.reasons["seo"] = m
        self.log(f"Selected {len(clips)} moments: " + ", ".join(
            f"{int(c.start // 60)}:{int(c.start % 60):02d}" for c in clips))

        base, moved = usable_output_dir(s.output_dir)
        if moved:
            self.log(f"Windows won't let RR Shorts Builder write to {s.output_dir} (protected folder). "
                     f"Saving to {base} instead — you can change this in Settings.")
            s.output_dir = str(base)
            try:
                saved = Settings.load()
                saved.output_dir = str(base)
                saved.save()
            except Exception:
                pass
        out_dir = base / safe_name(item.title)
        out_dir.mkdir(parents=True, exist_ok=True)
        work = WORK / re.sub(r"[^A-Za-z0-9_-]", "_", item.vid)
        work.mkdir(parents=True, exist_ok=True)
        rot = StyleRotator(s, seed=zlib.crc32(item.vid.encode()))
        results = []
        n = len(clips)
        for i, clip in enumerate(clips):
            self._check()
            base = 0.70 + 0.30 * i / n
            span = 0.30 / n
            P(f"Rendering Short {i + 1}/{n}", base, clip.title)
            choice = rot.choice(i)
            try:
                res = self.render_clip(src, info, item, transcript, clip, choice, i, out_dir, work,
                                       lambda f, b=base, sp=span, k=i: P(f"Rendering Short {k + 1}/{n}", b + sp * f, ""))
            except Cancelled:
                raise
            except Exception as e:  # one bad Short must not cost the user the others
                self.failed.append((i + 1, str(e).strip().split("\n")[0][:200]))
                self.log(f"  ✖ Short {i + 1} failed, continuing with the rest: {e}")
                continue
            results.append(res)
            self.on_short(res)
        if not results and self.failed:
            raise RuntimeError(f"All {n} Shorts failed to render. First error: {self.failed[0][1]}")
        summary = f"{len(results)} Shorts ready" + (f" ({len(self.failed)} failed, see log)" if self.failed else "")
        P("Done", 1.0, summary)
        self.log(f"✔ {summary} · saved to {out_dir}")
        return results

    # ------------------------------------------------------------------
    def _prepare(self, src: str, info: dict, transcript: dict, clip: Clip, choice: StyleChoice, index: int) -> dict:
        """Framing + cuts for a clip: face tracking, jump cuts, re-timed words and camera path.
        Stored in the plan so placement edits and previews don't have to redo it."""
        s = self.s
        parts = clip.parts()          # [(abs_start, abs_end)] in playback order (cuts / cold open)
        # Layout / face tracking (per part, so the camera follows the speaker through every cut)
        layout = choice.layout
        landscape = info["width"] > info["height"] * 0.8
        tracks: list = [None] * len(parts)
        if landscape and layout in ("auto", "smart_crop", "split"):
            tracks = [track_faces(src, a, b, cancel=self.cancel) for a, b in parts]
            total = sum(b - a for a, b in parts) or 1
            cov = sum(t["coverage"] * (b - a) for t, (a, b) in zip(tracks, parts)) / total
            if layout == "auto":
                layout = "smart_crop" if cov >= 0.45 else "blur_fit"
            self.log(f"  Short {index + 1}: face coverage {cov:.0%} → {LAYOUTS.get(layout, layout)}")
        elif layout == "auto":
            layout = "blur_fit"
        cw = int(info["height"] * 9 / 16)

        # stitch the parts; inside each part drop dead air (jump cuts). Words + camera are re-timed to match.
        words, camera, pieces, words_abs = [], [], [], []
        offset = 0.0
        for (a, b), tr in zip(parts, tracks):
            pw = words_in_range(transcript, a, b)
            words_abs += [{**w, "s": round(w["s"] + a, 3), "e": round(w["e"] + a, 3)} for w in pw]
            cam = camera_path(tr, info["width"], cw, b - a) if (tr and layout in ("smart_crop", "split")) else []
            keep, pw2, cam2, pd = tighten(pw, b - a, cam, enabled=getattr(s, "remove_pauses", True))
            pieces.append((a, b, keep or []))
            words += [{**w, "s": round(w["s"] + offset, 3), "e": round(w["e"] + offset, 3)} for w in pw2]
            camera += [(t + offset, x) for t, x in cam2]
            offset += pd
        D = offset
        seen = set()  # a cold open repeats a sentence: store each source word once
        words_abs = [w for w in sorted(words_abs, key=lambda w: w["s"])
                     if (w["s"], w["w"]) not in seen and not seen.add((w["s"], w["w"]))]
        cut = sum(b - a for a, b in parts) - D
        if cut > 0.3 or len(parts) > 1:
            self.log(f"  Short {index + 1}: {len(parts)} part(s)"
                     f"{', cold open' if clip.reasons.get('cold_open') else ''}, removed {cut:.1f}s of pauses")

        return {"layout": layout, "words": words, "camera": [list(c) for c in camera],
                "pieces": [[a, b, [list(k) for k in keep]] for a, b, keep in pieces],
                "words_abs": words_abs, "D": D}

    def _write_ass(self, ass_file: Path, words: list, D: float, clip: Clip, choice: StyleChoice, index: int,
                   lang: str, hook_text: Optional[str]) -> tuple[TextPlan, set]:
        s = self.s
        emphasis = {k.lower() for k in clip.keywords[:4]} | {
            re.sub(r"[^\w']", "", w["w"].lower()) for w in words if re.sub(r"[^\w']", "", w["w"].lower()) in INTENSE}
        plan = TextPlan(
            caption_style=choice.caption_style, hook_style=choice.hook_style, cta_style=choice.cta_style,
            hook_text=(hook_text or clip.title) if choice.hook_style else "",
            cta_text=s.cta_text, watermark=s.watermark,
            part_label="",  # no "Part 1/2" labels
            position=choice.position, language=lang, emphasis=emphasis, seed=index * 7919 + 13,
            place=dict(choice.place or {}),
        )
        ass_file.write_text(build_ass(words, D, plan), encoding="utf-8")
        return plan, emphasis

    # ------------------------------------------------------------------
    def render_clip(self, src: str, info: dict, item: SourceItem, transcript: dict, clip: Clip,
                    choice: StyleChoice, index: int, out_dir: Path, work: Path,
                    prog: Callable[[float], None], plan_extra: Optional[dict] = None) -> ShortResult:
        s = self.s
        lang = transcript.get("language", "en")
        extra = plan_extra or {}
        prep = extra.get("prep") or self._prepare(src, info, transcript, clip, choice, index)
        layout, words, camera, pieces = prep["layout"], prep["words"], prep["camera"], prep["pieces"]
        words_abs, D = prep["words_abs"], prep["D"]

        stem = f"{index + 1:02d} - {safe_name(clip.title, 50)}"
        ass_file = work / f"{index + 1:02d}.ass"
        plan, emphasis = self._write_ass(ass_file, words, D, clip, choice, index, lang, extra.get("hook_text"))

        cap = CAPTION_STYLES.get(choice.caption_style, {})
        accent = cap.get("active") or cap.get("primary") or "#FFE400"
        music, music_offset = "", 0.0
        if s.add_music and extra.get("music") and Path(extra["music"]).exists():   # re-render keeps the track
            music, music_offset = extra["music"], float(extra.get("music_offset", 0.0))
        elif s.add_music and (s.music_volume > 0 or getattr(s, "music_auto", True)):
            rnd = random.Random(index * 31 + len(item.vid) + int(time.time() // 86400))
            only = s.music_selected if getattr(s, "music_mode", "random") == "starred" else None
            music = _pick_music(s.music_dir, rnd, only)
            if music:
                music_offset = _music_offset(music, D, rnd)
                self.log(f"  Short {index + 1}: music “{Path(music).stem}”")
        out_path = out_dir / f"{stem}.mp4"
        # clean up an older render of the same slot (title may differ after re-style)
        for old in out_dir.glob(f"{index + 1:02d} - *.mp4"):
            if old != out_path:
                try:
                    old.unlink()
                    Path(str(old).replace(".mp4", ".jpg")).unlink(missing_ok=True)
                    Path(str(old).replace(".mp4", ".txt")).unlink(missing_ok=True)
                    Path(str(old)[:-4] + PLAN_SUFFIX).unlink(missing_ok=True)
                except OSError:
                    pass
        punch = _punch_times(words, emphasis)
        sfx_file = ""
        level = getattr(s, "sfx_level", "medium")
        if level != "off":
            try:
                chunk_starts = [c[0]["s"] for c in chunk_words(words, cap.get("chunk", 3), cap.get("maxchars", 18))]
                hook_anim = HOOK_STYLES.get(choice.hook_style or "", {}).get("anim") if plan.hook_text else None
                hook_end = 0.0
                if plan.hook_text:
                    hook_end = float((choice.place or {}).get("hook_dur")
                                     or HOOK_STYLES.get(choice.hook_style or "", {}).get("dur") or 3.0)
                events = sfx.plan(level, D, words, chunk_starts, cap, hook_anim, choice.intro, choice.motion, punch,
                                  bool(choice.cta_style and s.cta_text.strip()), emphasis,
                                  seams=_seams(pieces), hook_end=hook_end)
                sfx_file = sfx.mix_track(events, D, str(work / f"{index + 1:02d}_sfx.wav"),
                                         getattr(s, "sfx_volume", 0.55), level) or ""
            except Exception as e:  # sound effects are a bonus, never fail a render for them
                self.log(f"  (sound effects skipped: {e})")
        job = RenderJob(
            src=src, start=clip.start, end=clip.end, parts=pieces, out_path=str(out_path), src_w=info["width"],
            src_h=info["height"], ass_file=str(ass_file), layout=layout, camera=camera,
            grade=choice.color_grade, motion=choice.motion, intro=choice.intro,
            punch_times=punch, progress_bar=s.progress_bar, accent=accent, sfx=sfx_file,
            has_audio=info["has_audio"], loudnorm=s.loudnorm, music=music, music_volume=s.music_volume,
            music_auto=getattr(s, "music_auto", True), music_offset=music_offset,
            fps=_out_fps(s.fps, info.get("fps", 30)), crf=s.quality_crf, encoder=s.encoder,
            **_frame_args(choice.place),
        )
        t0 = time.time()
        render(job, prog, self.cancel)
        thumb = thumbnail(str(out_path), str(out_path.with_suffix(".jpg")), at=min(1.2, D / 2)) or ""
        self.log(f"  ✔ {out_path.name}  ({D:.0f}s, rendered in {time.time() - t0:.0f}s)")

        credit = music_sources.credit_for(music) if music else ""
        seo = dict((clip.reasons or {}).get("seo") or metadata.offline(clip, item.title, item.source))
        if extra.get("meta_edit"):          # the user's own edits in the Library win
            seo.update(extra["meta_edit"])
        meta = {
            "title": seo["title"],
            "body": seo.get("description", ""),
            "description": metadata.compose(seo, item.source, credit),
            "hashtags": seo.get("hashtags") or _hashtags(clip.keywords, lang),
            "tags": seo.get("tags") or [],
            "credit": credit,
        }
        out_path.with_suffix(".txt").write_text(
            f"TITLE:\n{meta['title']}\n\nDESCRIPTION:\n{meta['description']}\n\nTAGS:\n{', '.join(meta['tags'])}\n\n"
            f"SOURCE: {item.source}\nTIME: {clip.start:.1f}s - {clip.end:.1f}s\n", encoding="utf-8")

        plan_file = str(out_path)[:-4] + PLAN_SUFFIX
        Path(plan_file).write_text(json.dumps({
            "version": 1, "source": item.source, "source_title": item.title, "vid": item.vid,
            "src_file": src, "info": info, "clip": clip.to_dict(), "index": index,
            "style": asdict(choice), "resolved_layout": layout, "hook_text": plan.hook_text,
            "language": lang, "words_abs": words_abs, "meta": meta, "output": str(out_path), "thumb": thumb,
            "created": time.time(), "prep": prep, "music": music, "music_offset": music_offset,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return ShortResult(str(out_path), thumb, plan.hook_text or meta["title"], clip.start, clip.end, clip.score,
                           choice.label(), plan_file, meta)

    # ------------------------------------------------------------------
    def _load_plan(self, plan_file: str, choice: StyleChoice):
        data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
        src = data["src_file"]
        if not Path(src).exists():
            raise RuntimeError("The original video is no longer in the cache. Process the video again.")
        clip = Clip(**data["clip"])
        if "words_abs" in data:
            words_abs = [{"w": w["w"], "s": w["s"], "e": w["e"], "p": 1.0} for w in data["words_abs"]]
        else:  # plans made by older versions stored words relative to the clip start
            words_abs = [{"w": w["w"], "s": w["s"] + clip.start, "e": w["e"] + clip.start, "p": 1.0}
                         for w in data.get("words", [])]
        transcript = {"language": data.get("language", "en"), "segments": [{"start": clip.start,
                                                                                "end": clip.end, "text": "",
                                                                                "words": words_abs}]}
        # framing/cuts can be reused when the framing choice didn't change (skips face tracking)
        prep = data.get("prep")
        old_layout = (data.get("style") or {}).get("layout")
        if prep and choice.layout not in (old_layout, data.get("resolved_layout")):
            prep = None
        return data, src, clip, transcript, prep

    def restyle(self, plan_file: str, choice: StyleChoice, hook_text: Optional[str] = None,
                prog: Callable[[float], None] = lambda f: None) -> ShortResult:
        """Re-render an existing Short with a different look, edited hook text or new placement."""
        data, src, clip, transcript, prep = self._load_plan(plan_file, choice)
        item = SourceItem(data["source"], data.get("source_title", "video"), data["vid"])
        out_dir = Path(data["output"]).parent
        if hook_text is not None:
            clip.title = hook_text
        work = WORK / re.sub(r"[^A-Za-z0-9_-]", "_", item.vid)
        work.mkdir(parents=True, exist_ok=True)
        extra = {"hook_text": hook_text or data.get("hook_text", ""), "prep": prep}
        m = data.get("meta") or {}
        if m.get("body") is not None:   # keep upload title/description/tags the user may have edited
            extra["meta_edit"] = {"title": m.get("title", ""), "description": m.get("body", ""),
                                  "tags": m.get("tags") or [], "hashtags": m.get("hashtags") or []}
        if "music" in data:
            extra.update(music=data.get("music") or "", music_offset=data.get("music_offset", 0.0))
        return self.render_clip(src, data["info"], item, transcript, clip, choice, data["index"], out_dir,
                                work, prog, extra)

    def preview(self, plan_file: str, choice: StyleChoice, hook_text: Optional[str], t: float,
                out_png: str) -> tuple[str, float]:
        """Fast still of the Short at time t with the given look/placement. Returns (png, duration)."""
        data, src, clip, transcript, prep = self._load_plan(plan_file, choice)
        if hook_text is not None:
            clip.title = hook_text
        if not prep:
            prep = self._prepare(src, data["info"], transcript, clip, choice, data["index"])
            if choice.layout in ((data.get("style") or {}).get("layout"), data.get("resolved_layout")):
                try:  # remember it so the next preview/re-render is instant
                    data["prep"] = prep
                    Path(plan_file).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError:
                    pass
        work = WORK / "preview"
        work.mkdir(parents=True, exist_ok=True)
        ass_file = work / "preview.ass"
        self._write_ass(ass_file, prep["words"], prep["D"], clip, choice, data["index"],
                        data.get("language", "en"), hook_text or data.get("hook_text", ""))
        cap = CAPTION_STYLES.get(choice.caption_style, {})
        info = data["info"]
        job = RenderJob(src=src, start=clip.start, end=clip.end, parts=prep["pieces"], out_path=out_png,
                        src_w=info["width"], src_h=info["height"], ass_file=str(ass_file), layout=prep["layout"],
                        camera=prep["camera"], grade=choice.color_grade, progress_bar=self.s.progress_bar,
                        accent=cap.get("active") or cap.get("primary") or "#FFE400", has_audio=False,
                        **_frame_args(choice.place))
        return preview_frame(job, t, out_png), prep["D"]


def _seams(pieces: list) -> list:
    """Where the finished Short jumps: [(t, removed_seconds, is_part_join)]."""
    out, t = [], 0.0
    for pi, (a, b, keep) in enumerate(pieces):
        segs = [tuple(k) for k in keep] or [(0.0, b - a)]
        for j, (x, y) in enumerate(segs):
            if j == 0 and pi > 0:
                out.append((round(t, 3), 0.0, True))
            elif j > 0:
                out.append((round(t, 3), round(x - segs[j - 1][1], 3), False))
            t += y - x
    return out


def _frame_args(place: Optional[dict]) -> dict:
    place = place or {}
    try:
        return {"frame_x": float(place.get("frame_x", 0.0)), "frame_y": float(place.get("frame_y", 0.0)),
                "frame_zoom": float(place.get("frame_zoom", 1.0))}
    except (TypeError, ValueError):
        return {}


def load_library(output_dir: str) -> list[dict]:
    out = []
    root = Path(output_dir)
    if not root.exists():
        return out
    for pf in root.glob(f"*/*{PLAN_SUFFIX}"):
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
            if Path(d["output"]).exists():
                d["plan_file"] = str(pf)
                out.append(d)
        except Exception:
            continue
    out.sort(key=lambda d: d.get("created", 0), reverse=True)
    return out


def save_meta(plan_file: str, title: str, body: str, tags: list, hashtags: Optional[list] = None) -> dict:
    """Store edited upload details in the Short's plan (+ .txt) without re-rendering."""
    data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    m = data.get("meta") or {}
    m["title"], m["body"] = title.strip(), body.strip()
    m["tags"] = [t.strip() for t in tags if t.strip()]
    if hashtags is not None:
        m["hashtags"] = hashtags
    m["description"] = metadata.compose({"description": m["body"], "hashtags": m.get("hashtags") or ["#shorts"]},
                                        data.get("source", ""), m.get("credit", ""))
    data["meta"] = m
    Path(plan_file).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        Path(data["output"]).with_suffix(".txt").write_text(
            f"TITLE:\n{m['title']}\n\nDESCRIPTION:\n{m['description']}\n\nTAGS:\n{', '.join(m['tags'])}\n",
            encoding="utf-8")
    except OSError:
        pass
    return m
