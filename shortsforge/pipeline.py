"""End-to-end: source -> download -> transcribe -> pick highlights -> style -> render."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import fonts, joblog, licensing
from .captions import CAPTION_STYLES, CTA_STYLES, HOOK_STYLES, TextPlan, build_ass, chunk_words
from .config import CACHE_DIR, Settings, usable_output_dir
from .downloader import (DOWNLOADS, VIDEO_EXT, SourceItem, align_offset, download, download_audio,
                         download_section)
from .effects import CAMERA_LAYOUTS, COLOR_GRADES, INTROS, LAYOUTS, MOTIONS
from . import sfx
from .facetrack import camera_path, track_faces
from .pacing import tighten
from . import director, music_index, vibe as vibes
from . import story as story_fx
from .llm import AI
from .romanize import romanize_text, romanize_transcript
from .highlights import INTENSE, Clip, words_in_range
from .renderer import RenderJob, preview_frame, render, thumbnail
from . import metadata, music_sources
from .transcriber import ModelDownloadError, energy_curve, extract_audio, transcribe
from .utils import Cancelled, check_free_space, ffmpeg_cwd, probe, safe_name, set_ffmpeg_override

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


AUTO_KEYS = {"motion": "motions", "intro": "intros", "color_grade": "grades"}


def resolve_auto(choice: "StyleChoice", vibe: str, seed: int) -> "StyleChoice":
    """Any look option still set to "auto" becomes the vibe's pick (seeded, so preview == export)."""
    import dataclasses
    ch = {}
    for key, lst in AUTO_KEYS.items():
        if getattr(choice, key) == "auto":
            ch[key] = vibes.pick(vibe or "story", lst, seed)
    return dataclasses.replace(choice, **ch) if ch else choice


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

    def pick(self, key: str, i: int, vibe: str = "", seed: int = 0) -> str:
        val = getattr(self.s, key)
        if val == "random":
            pool = self.pools[key]
            return pool[i % len(pool)]
        if val == "auto" and key in AUTO_KEYS:
            return vibes.pick(vibe or "story", AUTO_KEYS[key], seed + i)
        return val

    def choice(self, i: int, vibe: str = "", seed: int = 0) -> StyleChoice:
        return StyleChoice(
            caption_style=self.pick("caption_style", i),
            hook_style=self.pick("hook_style", i) if self.s.hook_title else None,
            cta_style=self.pick("cta_style", i) if self.s.cta_text.strip() else None,
            color_grade=self.pick("color_grade", i, vibe, seed),
            motion=self.pick("motion", i, vibe, seed),
            intro=self.pick("intro", i, vibe, seed),
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


def abs_to_play(pieces: list, T: float) -> Optional[float]:
    """Absolute source time -> time in the finished Short (None if that moment was cut out)."""
    t = 0.0
    for a, b, keep in pieces:
        for x, y in ([tuple(k) for k in keep] or [(0.0, b - a)]):
            if a + x - 0.02 <= T < a + y:
                return round(t + max(0.0, T - a - x), 3)
            t += y - x
    return None


def focus_punches(words: list[dict], emphasis: set, amt: float) -> list[tuple[float, float]]:
    """Automatic focus zooms on key words, alternating a strong and a lighter push for rhythm."""
    return [(t, round(amt * (1.0 if i % 2 == 0 else 0.7), 4)) for i, t in enumerate(_punch_times(words, emphasis))]


def creative_inputs(c: dict, edits: dict) -> dict:
    """The vibe engine's inputs for a project clip + the user's edits."""
    audio = edits.get("audio") or {}
    return {"vibe": c.get("vibe") or "", "vibe_override": edits.get("vibe") or "",
            "seed": c.get("seed") or vibes.seed_for(c.get("clip", {}).get("start", 0), c.get("index", 0)),
            "reshuffle": int(audio.get("seed") or 0), "zooms_abs": edits.get("zooms"),
            "zoom_mult": edits.get("zoom_mult") or 1.0,
            "pack": audio.get("sfx_pack") if audio.get("sfx_pack") not in (None, "", "auto") else "",
            "music_mood": audio.get("music_mood") if audio.get("music_mood") not in (None, "", "auto") else "",
            "story": edits.get("story") or {}}


@dataclass
class Creative:
    """Everything the vibe decides for one Short. Same inputs -> same result (preview == export)."""
    vibe: str
    seed: int
    punches: list
    pack: str
    music: str = ""
    music_offset: float = 0.0
    music_why: str = ""
    motion: str = ""
    intro: str = ""
    grade: str = ""

    def to_json(self) -> dict:
        from .sfx import PACK_NAMES
        return {"vibe": self.vibe, "vibe_name": vibes.VIBES.get(self.vibe, {}).get("name", self.vibe),
                "seed": self.seed, "punches": [[round(t, 3), a] for t, a in self.punches], "pack": self.pack,
                "pack_name": PACK_NAMES.get(self.pack, self.pack), "music": self.music,
                "music_name": Path(self.music).stem if self.music else "", "music_offset": self.music_offset,
                "music_why": self.music_why, "motion": self.motion, "intro": self.intro, "grade": self.grade}


def _out_fps(setting: int, src_fps: float) -> int:
    if setting:
        return int(setting)
    f = int(round(src_fps or 30))
    return 60 if f >= 48 else (f if f in (24, 25, 30) else 30)


def resolve_model(s: Settings) -> str:
    """'auto' speech model: the fast large-v3-turbo on an NVIDIA GPU, 'small' on the CPU (best speed/accuracy)."""
    m = getattr(s, "whisper_model", "auto") or "auto"
    if m != "auto":
        return m
    from .transcriber import cuda_available
    if cuda_available() and getattr(s, "caption_lang", "roman") != "en":   # turbo can't translate
        return "large-v3-turbo"
    return "small"


_GEN_LOCK = threading.Lock()


def _auto_track(folder: str, log) -> str:
    """No music in the folder yet: make an original, copyright-free track once so Auto music always works."""
    with _GEN_LOCK:
        try:
            files = [p for p in Path(folder).iterdir() if p.suffix.lower() in MUSIC_EXT]
        except OSError:
            files = []
        if files:
            return str(random.choice(files))
        try:
            from . import musicgen
            log("  Music folder is empty: making an original copyright-free track (one time)…")
            return str(musicgen.make_track(random.choice(["lofi", "chill", "upbeat", "motivational"]), folder, 120))
        except Exception as e:
            log(f"  (couldn't make a music track: {e})")
            return ""


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
        self.log = lambda m: (joblog.write(m), log(m))   # the UI panel + the persistent jobs.log
        self.progress = progress
        self.on_short = on_short
        self.cancel = cancel or threading.Event()
        self.failed: list[tuple[int, str]] = []   # (Short number, error) of Shorts that couldn't be rendered
        set_ffmpeg_override(settings.ffmpeg_path)

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _ingest(self, item: SourceItem) -> dict:
        """Steps shared by the classic run and the new project flow: source, transcript, best moments, SEO."""
        s = self.s
        P = self.progress
        self.failed = []
        self.log(f"▶ {item.title}")
        fonts.fonts_dir()

        # 0. trial / license gate (installer builds only): checked before any download so a blocked run
        # doesn't waste time or bandwidth. Raises licensing.LicenseError, handled by the queue worker.
        if licensing.enabled():
            licensing.gate()

        # 1. source. Fast mode (YouTube links): fetch only the audio now (seconds), transcribe and pick the
        #    moments, then download just those parts in HD. Otherwise the whole video.
        t_start = time.time()
        src, info, wav = None, None, None
        fast = (getattr(s, "fast_mode", True) and not item.is_local
                and not any(f.stem == item.vid and f.suffix.lower() in VIDEO_EXT for f in DOWNLOADS.glob(f"{item.vid}.*")))
        if fast:
            try:
                P("Fetching audio", 0.0, "")
                audio, meta = download_audio(item, lambda f, d: P("Fetching audio", 0.10 * f, d), self.cancel,
                                             s.cookies_browser)
                dur = float(meta.get("duration") or 0) or probe(audio)["duration"]
                self._check()
                wav = extract_audio(audio, item.vid, dur, self.cancel, lambda f: P("Preparing audio", 0.10 + 0.04 * f, ""))
                self.log(f"Fast mode: audio ready in {time.time() - t_start:.0f}s ({dur / 60:.1f} min video); "
                         "HD video parts are fetched after the best moments are picked")
            except Cancelled:
                raise
            except Exception as e:
                self.log(f"Fast mode unavailable ({str(e).splitlines()[0][:140]}); downloading the full video")
                fast, wav = False, None
        if not fast:
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
        if wav or info["has_audio"]:
            if not wav:
                P("Extracting audio", 0.25, "")
                wav = extract_audio(src, item.vid, dur, self.cancel,
                                    lambda f: P("Extracting audio", 0.25 + 0.04 * f, ""))
            energy = energy_curve(wav)
            self._check()
            try:
                transcript = transcribe(
                    wav, item.vid, dur, resolve_model(s), s.language, (True if s.use_gpu else None),
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
            self.log(f"Windows won't let Rebels Revolt Shorts write to {s.output_dir} (protected folder). "
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
        # 4. sources for each Short: its own HD section (fast mode) or the full video
        n = len(clips)
        sources = self._sources(item, clips, wav, src, fast) if fast else [(src, 0.0)] * n
        if info is None:
            info = probe(sources[0][0])
            info["duration"] = dur
            info["has_audio"] = True
        return {"item": item, "info": info, "dur": dur, "transcript": transcript, "energy": energy, "clips": clips,
                "sources": sources, "out_dir": out_dir, "t_start": t_start, "fast": fast}

    # ------------------------------------------------------------------ project flow (new UI)
    def analyze(self, item: SourceItem) -> dict:
        """Find the best moments and save them as a project: scored, previewable instantly, nothing rendered."""
        from . import projects, virality
        from .highlights import flatten_words
        P = self.progress
        ctx = self._ingest(item)
        clips, transcript, info = ctx["clips"], ctx["transcript"], ctx["info"]
        all_words = flatten_words(transcript)
        P("Scoring clips", 0.74, "")
        scores = virality.score_clips(clips, all_words, ctx["energy"])
        rot = StyleRotator(self.s, seed=zlib.crc32(item.vid.encode()))
        pid = re.sub(r"[^A-Za-z0-9_-]", "_", item.vid)[:60]
        pdir = projects.PROJECTS / pid
        pdir.mkdir(parents=True, exist_ok=True)
        old = projects.load(pid) or {}
        out = []
        for i, (c, sc) in enumerate(zip(clips, scores)):
            media, off = ctx["sources"][i]
            lo = min(a for a, b in c.parts()) - 1.0
            hi = max(b for a, b in c.parts()) + 1.0
            words = [{"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3)} for w in all_words
                     if w["s"] >= lo and w["e"] <= hi]
            seo = dict((c.reasons or {}).get("seo") or metadata.offline(c, item.title, item.source))
            hooks = [h for h in (seo.get("hooks") or [])]
            for h in virality.alt_hooks(c, all_words, 4):
                if h and h.lower() not in {x.lower() for x in hooks}:
                    hooks.append(h)
            reasons = {k: v for k, v in (c.reasons or {}).items() if k != "seo"}
            en = ctx["energy"]
            try:
                e_slice = list(en[int(lo / 0.1): int(hi / 0.1)]) if en is not None and len(en) else None
            except Exception:
                e_slice = None
            vb = vibes.detect(words, e_slice, str(reasons.get("vibe") or ""))
            cseed = vibes.seed_for(item.vid, i)
            cd = c.to_dict()
            cd["reasons"] = reasons
            out.append({
                "id": f"c{i + 1}", "index": i, "clip": cd, "score": sc["score"], "breakdown": sc["breakdown"],
                "reasons": sc["reasons"], "hooks": hooks[:4], "seo": {k: v for k, v in seo.items() if k != "hooks"},
                "media": {"file": str(media), "offset": float(off)}, "words": words,
                "style": asdict(rot.choice(i, vb["vibe"], cseed)), "vibe": vb["vibe"], "seed": cseed,
                "poster": "", "edits": {}, "exports": [], "status": "new",
                "duration": round(c.duration, 2),
            })
        # keep the user's edits/exports if the same video is analysed again and the moment is the same
        prev = {(round(x["clip"]["start"], 1), round(x["clip"]["end"], 1)): x for x in old.get("clips", [])}
        for cl in out:
            o = prev.get((round(cl["clip"]["start"], 1), round(cl["clip"]["end"], 1)))
            if o:
                cl["edits"], cl["exports"] = o.get("edits") or {}, o.get("exports") or []
                cl["status"] = o.get("status", "new")
        out.sort(key=lambda x: -x["score"])
        project = {
            "id": pid, "source": item.source, "title": item.title, "is_local": item.is_local,
            "created": old.get("created") or time.time(), "duration": ctx["dur"],
            "language": transcript.get("language", "en"), "info": info, "out_dir": str(ctx["out_dir"]),
            "clips": out, "status": "ready",
        }
        P("Making previews", 0.80, "")
        self._posters(project, pdir)
        projects.save(project)
        self.log(f"✔ {len(out)} clips ready to preview (analysed in {time.time() - ctx['t_start']:.0f}s)")
        P("Clips ready", 1.0, f"{len(out)} clips")
        return project

    def _posters(self, project: dict, pdir: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from .utils import run_ffmpeg

        def one(c):
            m = c["media"]
            a = c["clip"]["segments"][0][0] if c["clip"].get("segments") else c["clip"]["start"]
            out = pdir / f"{c['id']}.jpg"
            try:
                run_ffmpeg(["-ss", f"{max(0.0, a - m['offset'] + 0.4):.2f}", "-i", m["file"], "-frames:v", "1",
                            "-vf", "scale=-2:480", "-q:v", "4", "-update", "1", str(out)], 1.0)
                c["poster"] = str(out)
            except Exception:
                c["poster"] = ""
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(one, project["clips"]))

    def track_clip(self, project: dict, c: dict) -> dict:
        """Face tracking for a project clip, once per original part. Gives the live preview its camera moves
        and makes every later export skip the tracking step."""
        info = project["info"]
        if c.get("tracks") is not None:
            return c
        landscape = info["width"] > info["height"] * 0.8
        tracks, camera = [], []
        cw = int(info["height"] * 9 / 16)
        max_x = max(1, info["width"] - cw)
        cov_sum, tot = 0.0, 0.0
        if landscape:
            off = float(c["media"]["offset"])
            for a, b in projects_base_parts(c):
                self._check()
                tr = track_faces(c["media"]["file"], a - off, b - off, cancel=self.cancel)
                tracks.append({"a": a, "b": b, "t": _json_track(tr)})
                cov_sum += tr["coverage"] * (b - a)
                tot += b - a
                for t, x in camera_path(tr, info["width"], cw, b - a):
                    if t <= b - a + 0.01:
                        camera.append([round(a + t, 3), round(min(1.0, max(0.0, x / max_x)), 4)])
        cov = cov_sum / tot if tot else 0.0
        c["tracks"] = tracks
        c["camera"] = camera
        c["coverage"] = round(cov, 3)
        c["framing"] = ("smart_crop" if cov >= 0.45 else "blur_fit") if landscape else "fit"
        return c

    def export_clip(self, project: dict, cid: str, edits: Optional[dict] = None,
                    prog: Callable[[float], None] = lambda f: None) -> "ShortResult":
        """Render one project clip with the user's edits (trim, removed words, fixes, hook, style, audio, meta)."""
        import copy
        from . import projects
        c = projects.clip(project, cid)
        if c is None:
            raise RuntimeError("That clip is no longer in the project.")
        edits = dict(c.get("edits") or {}) if edits is None else edits
        licensing.check(f"{project['id']}/{cid}")      # trial over? say so before doing any work
        media, off = c["media"]["file"], float(c["media"]["offset"])
        if not Path(media).exists():
            raise RuntimeError("The video for this clip is no longer in the cache. Analyse the video again.")
        s = copy.copy(self.s)
        audio = edits.get("audio") or {}
        music = audio.get("music", "auto")
        extra: dict = {"src_offset": off, "project_ref": {"project": project["id"], "clip": cid}}
        if music == "none":
            s.add_music = False
        elif music and music != "auto":
            if Path(music).exists():
                s.add_music = True
                extra["music"] = music
                if audio.get("music_offset") is not None:
                    extra["music_offset"], extra["keep_offset"] = float(audio["music_offset"]), True
        if audio.get("music_volume") is not None:
            s.music_volume = float(audio["music_volume"])
            s.music_auto = False            # the user picked a level: use it instead of auto-levelling
        if audio.get("sfx_level"):
            s.sfx_level = audio["sfx_level"]
        parts = projects.playback_parts(c, edits)
        words = projects.edited_words(c, edits)
        cl = Clip(**c["clip"])
        cl.reasons = dict(cl.reasons or {})
        cl.reasons["seo"] = dict(c.get("seo") or {})
        cl.segments = [list(p) for p in parts] if len(parts) > 1 else []
        cl.start, cl.end = min(a for a, b in parts), max(b for a, b in parts)
        hook = (edits.get("hook") or "").strip()
        if hook:
            cl.title = hook
        style = {**c["style"], **(edits.get("style") or {})}
        if edits.get("place") is not None:
            style["place"] = edits["place"]
        valid = {f for f in StyleChoice.__dataclass_fields__}
        choice = StyleChoice(**{k: v for k, v in style.items() if k in valid})
        transcript = {"language": project.get("language", "en"),
                      "segments": [{"start": cl.start, "end": cl.end, "text": "", "words": words}]}
        # framing: reuse the face tracking done for the preview (only redo it if it's missing)
        info = project["info"]
        if choice.layout in CAMERA_LAYOUTS and info["width"] > info["height"] * 0.8:
            self.track_clip(project, c)
        extra["tracks"] = c.get("tracks") or None
        extra["hook_text"] = hook or None
        extra["creative"] = creative_inputs(c, edits)
        if edits.get("meta"):
            extra["meta_edit"] = {k: v for k, v in edits["meta"].items() if v not in (None, "")}
        extra.update(apply_export_prefs(s, edits.get("export") or getattr(s, "export_prefs", None)))
        extra["settings"] = s
        item = SourceItem(project["source"], project["title"], project["id"], bool(project.get("is_local")))
        out_dir = Path(project.get("out_dir") or usable_output_dir(s.output_dir)[0] / safe_name(project["title"]))
        out_dir.mkdir(parents=True, exist_ok=True)
        work = WORK / (re.sub(r"[^A-Za-z0-9_-]", "_", project["id"]) + "_" + cid)
        work.mkdir(parents=True, exist_ok=True)
        res = self.render_clip(media, info, item, transcript, cl, choice, int(c["index"]), out_dir, work, prog, extra)
        rec = {"path": res.path, "thumb": res.thumb, "plan_file": res.plan_file, "time": time.time(),
               "title": res.meta.get("title", "")}
        with projects._lock:
            p2 = projects.load(project["id"]) or project
            c2 = projects.clip(p2, cid) or c
            c2["exports"] = [e for e in (c2.get("exports") or [])
                             if e.get("path") != res.path and Path(e.get("path", "")).exists()] + [rec]
            c2["status"] = "exported"
            c2["edits"] = edits
            for k in ("tracks", "camera", "coverage", "framing"):
                if k in c:
                    c2[k] = c[k]
            projects.save(p2)
        c["exports"], c["status"] = c2["exports"], "exported"
        return res

    # ------------------------------------------------------------------ vibe: music, sounds, zooms, opening
    def creative(self, s: Settings, cin: dict, words: list, D: float, pieces: list, choice: "StyleChoice",
                 clip: Clip, index: int, emphasis: set, extra: dict, log: bool = True,
                 shift: Optional[Callable[[float], float]] = None) -> tuple["Creative", "StyleChoice"]:
        """Resolve the vibe-driven choices for one Short.

        cin (from the project clip + the user's edits): vibe (detected), vibe_override, seed, reshuffle,
        zooms_abs (manual zoom moments, absolute source seconds; None = automatic), zoom_mult, pack,
        music_mood (a vibe whose music to use instead)."""
        vibe = cin.get("vibe_override") or cin.get("vibe") or ""
        if vibe not in vibes.VIBES:
            vibe = vibes.detect(words, None, str((clip.reasons or {}).get("vibe") or ""))["vibe"]
        seed = int(cin.get("seed") or vibes.seed_for(clip.start, index)) + 7919 * int(cin.get("reshuffle") or 0)
        choice = resolve_auto(choice, vibe, seed)
        amt = vibes.zoom_amount(vibe) * float(getattr(s, "zoom_strength", 1.0) or 1.0) * float(cin.get("zoom_mult") or 1.0)
        zabs = cin.get("zooms_abs")
        if zabs is not None:
            pts = sorted((shift or (lambda x: x))(t) for t in (abs_to_play(pieces, float(z)) for z in zabs)
                         if t is not None)
            punches = [(t, round(amt * (1.0 if i % 2 == 0 else 0.75), 4)) for i, t in enumerate(pts)]
        elif choice.motion == "punch":
            punches = focus_punches(words, emphasis, amt)
        elif choice.motion not in ("none", "") and cin.get("light_zooms", True):
            # a gentle camera move + a lighter focus push on every other key moment
            punches = [(t, round(a * 0.55, 4)) for i, (t, a) in enumerate(focus_punches(words, emphasis, amt)) if i % 2 == 0]
        else:
            punches = []
        punches = [(t, a) for t, a in punches if 0.3 <= t < D - 0.4]   # the zooms that can actually play
        pack = cin.get("pack") or getattr(s, "sfx_pack", "auto") or "auto"
        if pack == "auto":
            pack = vibes.pack_for(vibe)
        cr = Creative(vibe=vibe, seed=seed, punches=punches, pack=pack, motion=choice.motion, intro=choice.intro,
                      grade=choice.color_grade)
        # music
        if extra.get("music") and Path(extra["music"]).exists():
            cr.music, cr.music_offset = extra["music"], float(extra.get("music_offset") or 0.0)
            cr.music_why = "chosen"
            if cr.music_offset <= 0 and not extra.get("keep_offset"):
                f = music_index.features([Path(cr.music)]).get(cr.music) or {}
                cr.music_offset = music_index.offset_for(f, D, random.Random(seed))
        elif s.add_music and not extra.get("no_music") and (s.music_volume > 0 or getattr(s, "music_auto", True)):
            from .config import usable_music_dir
            folder = str(usable_music_dir(s.music_dir)[0])
            only = s.music_selected if getattr(s, "music_mode", "random") == "starred" else None
            want = cin.get("music_mood") or vibe
            if getattr(s, "music_match", True):
                cr.music, cr.music_offset, cr.music_why = music_index.choose(
                    want, seed, index, [s.music_dir, folder], D, only, folder, self.log if log else (lambda m: None))
            if not cr.music:
                rnd = random.Random(seed)
                m = _pick_music(s.music_dir, rnd, only) or _pick_music(folder, rnd, only) or _auto_track(folder, self.log)
                if m:
                    cr.music, cr.music_offset, cr.music_why = m, _music_offset(m, D, rnd), "random"
        return cr, choice

    # ------------------------------------------------------------------ Story FX (beats, titles, streaks, texture)
    @staticmethod
    def _vibe_of(cin: dict, words: list, clip: Clip) -> str:
        vibe = cin.get("vibe_override") or cin.get("vibe") or ""
        if vibe not in vibes.VIBES:
            vibe = vibes.detect(words, None, str((clip.reasons or {}).get("vibe") or ""))["vibe"]
        return vibe

    def _story(self, s: Settings, cin: dict, prep: dict, clip: Clip, index: int, lang: str, choice: "StyleChoice",
               hook_text: Optional[str], fps: int, tracks: Optional[list] = None, media: str = "",
               src_offset: float = 0.0, src_wh: tuple = (0, 0)) -> tuple[dict, dict]:
        """Plan the Story FX for a Short and return (prep re-timed for the freezes, story plan)."""
        words, D, pieces = prep["words"], prep["D"], prep["pieces"]
        try:
            opts = story_fx.options(s, cin.get("story"))
            vibe = self._vibe_of(cin, words, clip)
            seed = int(cin.get("seed") or vibes.seed_for(clip.start, index)) + 7919 * int(cin.get("reshuffle") or 0)
            title = ((hook_text or clip.title) or "").strip() if choice.hook_style else ""
            cap = CAPTION_STYLES.get(choice.caption_style, {})
            sp = story_fx.plan(words, D, pieces, vibe, seed, title, clip.keywords, lang, prep["layout"], opts,
                               bool((clip.reasons or {}).get("cold_open")), getattr(s, "watermark", ""),
                               cap.get("active") or cap.get("primary") or "#FFE400", _seams(pieces), fps,
                               *face_top(tracks, pieces, prep["layout"], media, src_offset, prep.get("camera"), src_wh))
        except Exception as e:      # Story FX is a bonus: never fail a Short over it
            self.log(f"  (story effects skipped: {e})")
            sp = story_fx.plan([], 0, [], "story", 0, "", [], lang, "blur_fit", {"level": "off"}, False)
        fr = sp["freezes"]
        if not fr:
            return prep, sp
        return ({**prep, "words": story_fx.shift_words(words, fr), "camera": story_fx.shift_camera(prep["camera"], fr),
                 "D": sp["D"]}, sp)

    @staticmethod
    def _story_job(sp: dict) -> dict:
        """The part of the story plan the renderer needs."""
        return {"spans": sp.get("spans") or [], "streaks": sp.get("streaks") or [], "leaks": sp.get("leaks") or [],
                "texture": sp.get("texture")}

    def _story_ass(self, sp: dict, path: Path) -> str:
        if not (sp.get("title") or sp.get("beats")):
            return ""
        path.write_text(story_fx.ass(sp), encoding="utf-8")
        return str(path)

    def _cutout(self, job: "RenderJob", sp: dict, work: Path, stem: str) -> None:
        """Speaker cut-out for the title window, so the big title can sit behind the speaker."""
        from . import cutout
        from .renderer import mask_pass_args
        from .utils import run_ffmpeg
        tl = sp.get("title")
        if not (tl and sp.get("behind") and cutout.available()):
            return
        end = tl["t1"] + 0.3
        if sp.get("spans"):
            end = min(end, sp["spans"][0][0])
        frames = max(2, int(end * job.fps))
        fdir = work / f"{stem}_cut"
        try:
            if fdir.exists():
                shutil.rmtree(fdir, ignore_errors=True)
            fdir.mkdir(parents=True, exist_ok=True)
            run_ffmpeg(mask_pass_args(job, str(fdir / "mk_%05d.png"), frames), frames / job.fps,
                       cancel=self.cancel, cwd=ffmpeg_cwd())
            n = cutout.make_masks(fdir, frames, fdir, job.fps, self.cancel)
            if n:
                job.mask_pattern, job.mask_n = str(fdir / "mask_%05d.png"), n
                job.title_win = (tl["t0"], tl["t1"])
            else:
                self.log("  (title stays in front: no clear speaker in the opening frames)")
        except Exception as e:
            self.log(f"  (title cut-out skipped: {str(e).splitlines()[0][:120] if str(e) else type(e).__name__})")

    # ------------------------------------------------------------------ audio helpers (export + live preview)
    def _sfx_track(self, s: Settings, words: list, D: float, cap: dict, choice: "StyleChoice", plan, emphasis: set,
                   pieces: list, punch: list, out: str, cr: Optional["Creative"] = None,
                   sp: Optional[dict] = None) -> str:
        level = sfx.resolve_level(getattr(s, "sfx_level", "auto"), words, D)
        if level == "off":
            return ""
        try:
            chunk_starts = [c[0]["s"] for c in chunk_words(words, cap.get("chunk", 3), cap.get("maxchars", 18))]
            hook_anim = HOOK_STYLES.get(choice.hook_style or "", {}).get("anim") if plan.hook_text else None
            hook_end = 0.0
            if plan.hook_text:
                hook_end = float((choice.place or {}).get("hook_dur")
                                 or HOOK_STYLES.get(choice.hook_style or "", {}).get("dur") or 3.0)
            pts = [p[0] if isinstance(p, (list, tuple)) else p for p in punch]
            fr = (sp or {}).get("freezes") or []
            seams = [(story_fx.to_final(t, fr), r, j) for t, r, j in _seams(pieces)]
            if (sp or {}).get("title"):
                hook_end = max(hook_end, sp["title"]["t1"])
            events = sfx.plan(level, D, words, chunk_starts, cap, hook_anim, choice.intro, choice.motion, pts,
                              bool(choice.cta_style and s.cta_text.strip()), emphasis,
                              seams=seams, hook_end=hook_end,
                              pack=cr.pack if cr else vibes.pack_for("story"), seed=cr.seed if cr else 0,
                              story=story_fx.sfx_events(sp) if sp else None)
            return sfx.mix_track(events, D, out, getattr(s, "sfx_volume", 0.55), level) or ""
        except Exception as e:  # sound effects are a bonus, never fail a render for them
            self.log(f"  (sound effects skipped: {e})")
            return ""

    def preview_audio(self, project: dict, cid: str, edits: dict, out_path: str) -> tuple[str, float, dict]:
        """Sound effects + music for the live preview (the voice comes from the video itself), plus the vibe plan
        (focus zooms, opening, chosen track) so the preview shows exactly what the export will do."""
        import copy
        import dataclasses
        from . import projects
        from .utils import run_ffmpeg
        c = projects.clip(project, cid)
        if c is None:
            raise RuntimeError("That clip is no longer in the project.")
        s = copy.copy(self.s)
        audio = edits.get("audio") or {}
        extra: dict = {}
        music_sel = audio.get("music", "auto")
        if music_sel == "none":
            s.add_music = False
        elif music_sel and music_sel != "auto" and Path(music_sel).exists():
            s.add_music = True
            extra["music"] = music_sel
            if audio.get("music_offset") is not None:
                extra["music_offset"], extra["keep_offset"] = float(audio["music_offset"]), True
        if audio.get("sfx_level"):
            s.sfx_level = audio["sfx_level"]
        parts = projects.playback_parts(c, edits)
        cl = Clip(**c["clip"])
        cl.segments = [list(p) for p in parts] if len(parts) > 1 else []
        cl.start, cl.end = min(a for a, b in parts), max(b for a, b in parts)
        hook = (edits.get("hook") or "").strip()
        if hook:
            cl.title = hook
        style = {**c["style"], **(edits.get("style") or {})}
        if edits.get("place") is not None:
            style["place"] = edits["place"]
        choice = StyleChoice(**{k: v for k, v in style.items() if k in StyleChoice.__dataclass_fields__})
        transcript = {"language": project.get("language", "en"),
                      "segments": [{"start": cl.start, "end": cl.end, "text": "",
                                    "words": projects.edited_words(c, edits)}]}
        off = float(c["media"]["offset"])
        # framing doesn't change the timing: skip face tracking for the sound preview
        prep = self._prepare(c["media"]["file"], project["info"], transcript, cl,
                             dataclasses.replace(choice, layout="blur_fit"), int(c["index"]), off, None, s)
        cin = creative_inputs(c, edits)
        fps = _out_fps(s.fps, project["info"].get("fps", 30))
        prep["layout"] = c.get("framing") if choice.layout == "auto" and c.get("framing") in LAYOUTS else \
            (choice.layout if choice.layout != "auto" else "blur_fit")
        if not (project["info"]["width"] > project["info"]["height"] * 0.8):
            prep["layout"] = "fit"
        fprep, sp = self._story(s, cin, prep, cl, int(c["index"]), project.get("language", "en"), choice,
                                hook or None, fps, c.get("tracks"), c["media"]["file"], off,
                                (project["info"]["width"], project["info"]["height"]))
        words, D, pieces = fprep["words"], fprep["D"], prep["pieces"]
        fr = sp["freezes"]
        work = WORK / "preview"
        work.mkdir(parents=True, exist_ok=True)
        plan, emphasis = self._write_ass(work / f"{cid}_aud.ass", words, D, cl, choice, int(c["index"]),
                                         project.get("language", "en"), hook or None, s, sp)
        cr, choice = self.creative(s, cin, words, D, pieces, choice, cl, int(c["index"]),
                                   emphasis, extra, log=False, shift=lambda t: story_fx.to_final(t, fr))
        cap = CAPTION_STYLES.get(choice.caption_style, {})
        fx = self._sfx_track(s, words, D, cap, choice, plan, emphasis, pieces, cr.punches,
                             str(work / f"{cid}_fx.wav"), cr, sp)
        music, moff = cr.music, cr.music_offset
        info = cr.to_json()
        info["story"] = sp
        # so the live captions match the export exactly: emphasised words and each caption's tilt
        info["emphasis"] = sorted(emphasis)
        if cap.get("tilt"):
            rnd_t = random.Random(plan.seed)
            info["tilts"] = [round(rnd_t.uniform(-4, 4), 1) for _ in chunk_words(words, cap.get("chunk", 3), cap.get("maxchars", 18))]
        args, n = [], 0
        if fx:
            args += ["-i", fx]
            n += 1
        if music:
            args += ["-stream_loop", "-1", "-ss", f"{moff:.2f}", "-i", music]
        if not fx and not music:
            return "", D, info
        # same level as the export: loudness-matched ~13 dB under the voice (+ a little for the ducking)
        if audio.get("music_volume") is None and getattr(s, "music_auto", True):
            lvl = "loudnorm=I=-27:TP=-4:LRA=9,aresample=48000,volume=0.8"
        else:
            v = float(audio.get("music_volume") if audio.get("music_volume") is not None else s.music_volume)
            lvl = f"volume={min(1.0, v):.3f}"
        g = []
        if music:
            g.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,{lvl},atrim=0:{D:.3f},"
                     f"afade=t=in:d=1,afade=t=out:st={max(0, D - 1.5):.3f}:d=1.5[m]")
        if fx and music:
            g.append("[0:a]aresample=48000,aformat=channel_layouts=stereo,apad[f];[f][m]amix=inputs=2:duration=longest:"
                     "normalize=0,alimiter=limit=0.97[o]")
        elif fx:
            g.append("[0:a]anull[o]")
        else:
            g.append("[m]anull[o]")
        run_ffmpeg(args + ["-filter_complex", ";".join(g), "-map", "[o]", "-t", f"{D:.3f}", "-c:a", "libopus",
                           "-b:a", "112k", out_path], D)
        return out_path, D, info

    def preview_clip(self, project: dict, cid: str, edits: dict, t: float, out_png: str) -> tuple[str, float]:
        """Exact still of the edited clip at playback time t (same filters as the export, no audio)."""
        from . import projects
        c = projects.clip(project, cid)
        if c is None:
            raise RuntimeError("That clip is no longer in the project.")
        parts = projects.playback_parts(c, edits)
        cl = Clip(**c["clip"])
        cl.segments = [list(p) for p in parts] if len(parts) > 1 else []
        cl.start, cl.end = min(a for a, b in parts), max(b for a, b in parts)
        hook = (edits.get("hook") or "").strip()
        if hook:
            cl.title = hook
        style = {**c["style"], **(edits.get("style") or {})}
        if edits.get("place") is not None:
            style["place"] = edits["place"]
        choice = StyleChoice(**{k: v for k, v in style.items() if k in StyleChoice.__dataclass_fields__})
        info = project["info"]
        if choice.layout in CAMERA_LAYOUTS and info["width"] > info["height"] * 0.8:
            self.track_clip(project, c)
        transcript = {"language": project.get("language", "en"),
                      "segments": [{"start": cl.start, "end": cl.end, "text": "",
                                    "words": projects.edited_words(c, edits)}]}
        off = float(c["media"]["offset"])
        prep = self._prepare(c["media"]["file"], info, transcript, cl, choice, int(c["index"]), off,
                             c.get("tracks"))
        work = WORK / "preview"
        work.mkdir(parents=True, exist_ok=True)
        ass_file = work / f"{cid}_preview.ass"
        cin = creative_inputs(c, edits)
        fps = _out_fps(self.s.fps, info.get("fps", 30))
        lang = project.get("language", "en")
        fprep, sp = self._story(self.s, cin, prep, cl, int(c["index"]), lang, choice, hook or None, fps,
                                c.get("tracks"), c["media"]["file"], off, (info["width"], info["height"]))
        fr = sp["freezes"]
        _plan, emphasis = self._write_ass(ass_file, fprep["words"], fprep["D"], cl, choice, int(c["index"]),
                                          lang, hook or None, None, sp)
        cr, choice = self.creative(self.s, cin, fprep["words"], fprep["D"], prep["pieces"],
                                   choice, cl, int(c["index"]), emphasis, {"no_music": True}, log=False,
                                   shift=lambda x: story_fx.to_final(x, fr))
        cap = CAPTION_STYLES.get(choice.caption_style, {})
        job = RenderJob(src=c["media"]["file"], start=cl.start, end=cl.end, parts=prep["pieces"], out_path=out_png,
                        src_w=info["width"], src_h=info["height"], ass_file=str(ass_file), layout=prep["layout"],
                        camera=fprep["camera"], grade=choice.color_grade, progress_bar=self.s.progress_bar,
                        accent=cap.get("active") or cap.get("primary") or "#FFE400", has_audio=False,
                        motion=choice.motion, intro=choice.intro, punch_times=cr.punches, fps=fps,
                        src_offset=off, freezes=fr, story=self._story_job(sp),
                        story_ass=self._story_ass(sp, work / f"{cid}_story.ass"), **_frame_args(choice.place))
        return preview_frame(job, max(0.0, min(t, fprep["D"] - 0.05)), out_png), fprep["D"]

    def process(self, item: SourceItem) -> list[ShortResult]:
        """Classic one-click run: analyse, then export every clip (several at once when the machine can)."""
        project = self.analyze(item)
        return self.export_all(project)

    def export_all(self, project: dict, ids: Optional[list] = None) -> list[ShortResult]:
        P = self.progress
        clips = [c for c in project["clips"] if ids is None or c["id"] in ids]
        n = len(clips)
        if not n:
            return []
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .utils import pick_encoder
        s = self.s
        t0 = time.time()
        gpu_enc = pick_encoder(s.encoder) != "libx264"
        workers = min(n, 3 if gpu_enc else (2 if (os.cpu_count() or 4) >= 8 else 1))
        frac = [0.0] * n
        done = [0]
        lock = threading.Lock()

        def prog(i: int, f: float):
            with lock:
                frac[i] = f
                P("Rendering Shorts", sum(frac) / n, f"{done[0]}/{n} ready")

        def job(i: int):
            self._check()
            return self.export_clip(project, clips[i]["id"], None, lambda f, k=i: prog(k, f))

        results_by = {}
        self.log(f"Rendering {n} Shorts ({workers} at a time" + (", GPU encoder)" if gpu_enc else ")"))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(job, i): i for i in range(n)}
            for fu in as_completed(futs):
                res = fu.result()
                results_by[futs[fu]] = res
                with lock:
                    done[0] += 1
                self.on_short(res)
        results = [results_by[i] for i in range(n)]
        self.log(f"⏱ Rendered {n} Shorts in {time.time() - t0:.0f}s")
        P("Done", 1.0, f"{len(results)} Shorts ready")
        if results:
            self.log(f"✔ {len(results)} Shorts saved to {Path(results[0].path).parent}")
        return results

    # ------------------------------------------------------------------
    def _sources(self, item: SourceItem, clips: list, wav: str, src: Optional[str], fast: bool) -> list:
        """Download just each Short's part of the video (in parallel) and find exactly where it starts."""
        s = self.s
        P = self.progress
        spans = []
        for c in clips:
            a = max(0.0, min(p[0] for p in c.parts()) - 2.0)
            b = max(p[1] for p in c.parts()) + 2.0
            spans.append((a, b))
        P("Downloading the chosen parts in HD", 0.70, f"{len(spans)} parts")
        t0 = time.time()
        from concurrent.futures import ThreadPoolExecutor

        def one(span):
            a, b = span
            sec = download_section(item, a, b, self.cancel, s.cookies_browser, int(s.source_quality or 1440))
            off = align_offset(sec, wav, a)
            if off is None:
                raise RuntimeError("couldn't line up the downloaded part with the audio")
            return sec, off
        try:
            with ThreadPoolExecutor(max_workers=4) as ex:
                out = list(ex.map(one, spans))
            total = sum(b - a for a, b in spans)
            self.log(f"Fast mode: downloaded {total / 60:.1f} min of HD video instead of the whole video "
                     f"({time.time() - t0:.0f}s)")
            return out
        except Cancelled:
            raise
        except Exception as e:
            self.log(f"Part download failed ({str(e).splitlines()[0][:140]}); downloading the full video instead")
            full = download(item, lambda f, d: P("Downloading", 0.70 + 0.02 * f, d), self.cancel,
                            s.cookies_browser, max_height=int(s.source_quality or 1440))
            return [(full, 0.0)] * len(clips)

    # ------------------------------------------------------------------
    def _prepare(self, src: str, info: dict, transcript: dict, clip: Clip, choice: StyleChoice, index: int,
                 src_offset: float = 0.0, cached_tracks: Optional[list] = None, settings: Optional[Settings] = None) -> dict:
        """Framing + cuts for a clip: face tracking, jump cuts, re-timed words and camera path.
        Stored in the plan so placement edits and previews don't have to redo it."""
        s = settings or self.s
        parts = clip.parts()          # [(abs_start, abs_end)] in playback order (cuts / cold open)
        # Layout / face tracking (per part, so the camera follows the speaker through every cut)
        layout = choice.layout
        landscape = info["width"] > info["height"] * 0.8
        tracks: list = [None] * len(parts)
        if landscape and layout in CAMERA_LAYOUTS:
            tracks = [(slice_track(cached_tracks, a, b) if cached_tracks else None)
                      or track_faces(src, a - src_offset, b - src_offset, cancel=self.cancel) for a, b in parts]
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
            cam = camera_path(tr, info["width"], cw, b - a) if (tr and layout in CAMERA_LAYOUTS) else []
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
                   lang: str, hook_text: Optional[str], settings: Optional[Settings] = None,
                   sp: Optional[dict] = None) -> tuple[TextPlan, set]:
        s = settings or self.s
        emphasis = {k.lower() for k in clip.keywords[:4]} | {
            re.sub(r"[^\w']", "", w["w"].lower()) for w in words if re.sub(r"[^\w']", "", w["w"].lower()) in INTENSE}
        plan = TextPlan(
            caption_style=choice.caption_style, hook_style=choice.hook_style, cta_style=choice.cta_style,
            hook_text=((hook_text or clip.title) if choice.hook_style else "") if not (sp or {}).get("title") else "",
            cta_text=s.cta_text, watermark=s.watermark,
            part_label="",  # no "Part 1/2" labels
            position=choice.position, language=lang, emphasis=emphasis, seed=index * 7919 + 13,
            place=dict(choice.place or {}),
            blackouts=[tuple(x) for x in (sp or {}).get("blackouts") or []],
        )
        ass_file.write_text(build_ass(words, D, plan), encoding="utf-8")
        return plan, emphasis

    # ------------------------------------------------------------------
    def render_clip(self, src: str, info: dict, item: SourceItem, transcript: dict, clip: Clip,
                    choice: StyleChoice, index: int, out_dir: Path, work: Path,
                    prog: Callable[[float], None], plan_extra: Optional[dict] = None) -> ShortResult:
        extra = plan_extra or {}
        s = extra.get("settings") or self.s
        lang = transcript.get("language", "en")
        off = float(extra.get("src_offset") or 0.0)
        prep = extra.get("prep") or self._prepare(src, info, transcript, clip, choice, index, off,
                                                  extra.get("tracks"), s)
        cin = dict(extra.get("creative") or {})
        cin.setdefault("seed", vibes.seed_for(item.vid, index))
        fps = _out_fps(s.fps, info.get("fps", 30))
        fprep, sp = self._story(s, cin, prep, clip, index, lang, choice, extra.get("hook_text"), fps,
                                extra.get("tracks"), src, off, (info["width"], info["height"]))
        layout, words, camera, pieces = fprep["layout"], fprep["words"], fprep["camera"], prep["pieces"]
        words_abs, D = prep["words_abs"], fprep["D"]
        fr = sp["freezes"]

        stem = f"{index + 1:02d} - {safe_name(clip.title, 50)}"
        ass_file = work / f"{index + 1:02d}.ass"
        plan, emphasis = self._write_ass(ass_file, words, D, clip, choice, index, lang, extra.get("hook_text"), s, sp)

        cap = CAPTION_STYLES.get(choice.caption_style, {})
        accent = cap.get("active") or cap.get("primary") or "#FFE400"
        cr, choice = self.creative(s, cin, words, D, pieces, choice, clip, index, emphasis, extra,
                                   shift=lambda t: story_fx.to_final(t, fr))
        music, music_offset = cr.music, cr.music_offset
        self.log(f"  Short {index + 1}: vibe {vibes.VIBES[cr.vibe]['name']} · sounds {cr.pack} · "
                 f"{len(cr.punches)} focus zooms · opening {cr.intro}"
                 + (f" · music “{Path(music).stem}”" if music else ""))
        if sp["level"] != "off":
            self.log(f"  Short {index + 1}: story FX · {len(fr)} beat(s)"
                     + (f" · {sp['title']['look']} title" if sp.get("title") else "")
                     + f" · {len(sp['streaks'])} streak(s)" + (" · film texture" if sp.get("texture") else ""))
        out_path = out_dir / f"{stem}.mp4"
        # clean up an older render of the same slot (title may differ after re-style)
        keep_cover: dict = {}      # an optional thumbnail made for this Short survives a re-render / new title
        for cand in [out_path] + list(out_dir.glob(f"{index + 1:02d} - *.mp4")):
            try:
                od = json.loads(Path(str(cand)[:-4] + PLAN_SUFFIX).read_text(encoding="utf-8"))
                if od.get("cover") and Path(od["cover"]).exists():
                    new_cover = out_path.with_suffix(".cover.jpg")
                    if Path(od["cover"]) != new_cover:
                        shutil.move(od["cover"], new_cover)
                    keep_cover = {"cover": str(new_cover), "cover_info": od.get("cover_info") or {}}
                    break
            except Exception:
                continue
        for old in out_dir.glob(f"{index + 1:02d} - *.mp4"):
            if old != out_path:
                try:
                    old.unlink()
                    Path(str(old).replace(".mp4", ".jpg")).unlink(missing_ok=True)
                    Path(str(old).replace(".mp4", ".txt")).unlink(missing_ok=True)
                    Path(str(old)[:-4] + PLAN_SUFFIX).unlink(missing_ok=True)
                except OSError:
                    pass
        punch = cr.punches
        sfx_file = self._sfx_track(s, words, D, cap, choice, plan, emphasis, pieces, punch,
                                   str(work / f"{index + 1:02d}_sfx.wav"), cr, sp)
        job = RenderJob(
            src=src, start=clip.start, end=clip.end, parts=pieces, out_path=str(out_path), src_w=info["width"],
            src_h=info["height"], ass_file=str(ass_file), layout=layout, camera=camera,
            grade=choice.color_grade, motion=choice.motion, intro=choice.intro,
            punch_times=punch, progress_bar=s.progress_bar, accent=accent, sfx=sfx_file,
            has_audio=info["has_audio"], loudnorm=s.loudnorm, music=music, music_volume=s.music_volume,
            music_auto=getattr(s, "music_auto", True), music_offset=music_offset,
            fps=fps, crf=s.quality_crf, encoder=s.encoder,
            speed=getattr(s, "encode_speed", "fast"),
            src_offset=off, out_h=int(extra.get("out_h") or 1920), codec=extra.get("codec") or "h264",
            freezes=fr, story=self._story_job(sp), story_ass=self._story_ass(sp, work / f"{index + 1:02d}_story.ass"),
            **_frame_args(choice.place),
        )
        self._cutout(job, sp, work, f"{index + 1:02d}")
        t0 = time.time()
        pr = extra.get("project_ref") or {}
        slot = f"{pr.get('project')}/{pr.get('clip')}" if pr else f"{item.vid}/{index}"
        tok = licensing.reserve(slot)            # trial: counts this Short (raises when the trial is over)
        try:
            render(job, prog, self.cancel)
        except BaseException:
            licensing.release(tok)
            raise
        thumb = thumbnail(str(out_path), str(out_path.with_suffix(".jpg")), at=min(1.2, D / 2)) or ""
        if extra.get("srt"):
            try:
                _write_srt(out_path.with_suffix(".srt"), words, cap)
            except Exception as e:
                self.log(f"  (captions file skipped: {e})")
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
            "src_file": src, "src_offset": off, "info": info, "clip": clip.to_dict(), "index": index,
            "style": asdict(choice), "resolved_layout": layout, "hook_text": plan.hook_text,
            "language": lang, "words_abs": words_abs, "meta": meta, "output": str(out_path), "thumb": thumb,
            "created": time.time(), "prep": prep, "music": music, "music_offset": music_offset,
            "project_ref": extra.get("project_ref"), "creative": cr.to_json(), "story": sp, **keep_cover,
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
        extra = {"hook_text": hook_text or data.get("hook_text", ""), "prep": prep,
                 "src_offset": float(data.get("src_offset") or 0.0), "project_ref": data.get("project_ref")}
        m = data.get("meta") or {}
        if m.get("body") is not None:   # keep upload title/description/tags the user may have edited
            extra["meta_edit"] = {"title": m.get("title", ""), "description": m.get("body", ""),
                                  "tags": m.get("tags") or [], "hashtags": m.get("hashtags") or []}
        if "music" in data:
            extra.update(music=data.get("music") or "", music_offset=data.get("music_offset", 0.0), keep_offset=True)
        if data.get("creative"):
            extra["creative"] = {"vibe": data["creative"].get("vibe", ""), "seed": data["creative"].get("seed")}
        return self.render_clip(src, data["info"], item, transcript, clip, choice, data["index"], out_dir,
                                work, prog, extra)

    def preview(self, plan_file: str, choice: StyleChoice, hook_text: Optional[str], t: float,
                out_png: str) -> tuple[str, float]:
        """Fast still of the Short at time t with the given look/placement. Returns (png, duration)."""
        data, src, clip, transcript, prep = self._load_plan(plan_file, choice)
        if hook_text is not None:
            clip.title = hook_text
        if not prep:
            prep = self._prepare(src, data["info"], transcript, clip, choice, data["index"],
                                 float(data.get("src_offset") or 0.0))
            if choice.layout in ((data.get("style") or {}).get("layout"), data.get("resolved_layout")):
                try:  # remember it so the next preview/re-render is instant
                    data["prep"] = prep
                    Path(plan_file).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError:
                    pass
        work = WORK / "preview"
        work.mkdir(parents=True, exist_ok=True)
        ass_file = work / "preview.ass"
        info = data["info"]
        fps = _out_fps(self.s.fps, info.get("fps", 30))
        cr0 = data.get("creative") or {}
        hk = hook_text or data.get("hook_text", "")
        fprep, sp = self._story(self.s, {"vibe": cr0.get("vibe", ""), "seed": cr0.get("seed")}, prep, clip,
                                data["index"], data.get("language", "en"), choice, hk, fps, None, src,
                                float(data.get("src_offset") or 0.0), (info["width"], info["height"]))
        self._write_ass(ass_file, fprep["words"], fprep["D"], clip, choice, data["index"],
                        data.get("language", "en"), hk, None, sp)
        cap = CAPTION_STYLES.get(choice.caption_style, {})
        job = RenderJob(src=src, start=clip.start, end=clip.end, parts=prep["pieces"], out_path=out_png,
                        src_w=info["width"], src_h=info["height"], ass_file=str(ass_file), layout=prep["layout"],
                        camera=fprep["camera"], grade=choice.color_grade, progress_bar=self.s.progress_bar,
                        accent=cap.get("active") or cap.get("primary") or "#FFE400", has_audio=False, fps=fps,
                        src_offset=float(data.get("src_offset") or 0.0), freezes=sp["freezes"],
                        story=self._story_job(sp), story_ass=self._story_ass(sp, work / "preview_story.ass"),
                        **_frame_args(choice.place))
        return preview_frame(job, t, out_png), fprep["D"]


def make_proxy(project: dict, cid: str) -> dict:
    """Small WebM copy of a clip's part of the video for the live preview, for sources the preview window
    can't decode itself (e.g. HEVC phone videos). Returns {"file", "offset"} in the same form as clip["media"]."""
    from . import projects
    from .utils import run_ffmpeg
    c = projects.clip(project, cid)
    if c is None:
        raise RuntimeError("That clip is no longer in the project.")
    if c.get("proxy") and Path(c["proxy"]["file"]).exists():
        return c["proxy"]
    media, off = c["media"]["file"], float(c["media"]["offset"])
    parts = projects.base_parts(c)
    lo = max(0.0, min(a for a, b in parts) - off - 1.5)
    hi = max(b for a, b in parts) - off + 1.5
    out = projects.PROJECTS / project["id"] / f"{cid}_proxy.webm"
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{lo:.3f}", "-i", media, "-t", f"{hi - lo:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale=-2:'min(540,ih)'", "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8",
                "-b:v", "1600k", "-c:a", "libopus", "-b:a", "96k", str(out)], hi - lo)
    prox = {"file": str(out), "offset": round(off + lo, 3)}
    with projects._lock:
        p2 = projects.load(project["id"])
        if p2 and projects.clip(p2, cid):
            projects.clip(p2, cid)["proxy"] = prox
            projects.save(p2)
    c["proxy"] = prox
    return prox


EXPORT_PRESETS = {
    # key: (label, height, fps (0 = like the source), quality, codec)
    "youtube": ("YouTube Shorts", 1920, 0, "high", "h264"),
    "tiktok": ("TikTok", 1920, 0, "high", "h264"),
    "reels": ("Instagram / Facebook Reels", 1920, 30, "high", "h264"),
    "hq": ("Best quality (1440p)", 2560, 0, "max", "h264"),
    "4k": ("4K", 3840, 0, "max", "h264"),
    "small": ("Small file (WhatsApp, 720p)", 1280, 30, "small", "h264"),
}
QUALITY_CRF = {"small": 26, "balanced": 21, "high": 18, "max": 16}


def apply_export_prefs(s: "Settings", ex: Optional[dict]) -> dict:
    """Export choices (preset or custom) -> changes the settings copy; returns render extras (size, codec, srt)."""
    ex = dict(ex or {})
    pre = EXPORT_PRESETS.get(ex.get("preset", ""))
    h, fps, q, codec = (pre[1], pre[2], pre[3], pre[4]) if pre else (1920, s.fps, "", "h264")
    h = int(ex.get("height") or h)
    fps = int(ex.get("fps") if ex.get("fps") is not None else fps)
    q = ex.get("quality") or q
    codec = ex.get("codec") or codec
    s.fps = fps
    if q in QUALITY_CRF:
        s.quality_crf = QUALITY_CRF[q]
        if q == "max":
            s.encode_speed = "quality"
    return {"out_h": h if h in (1280, 1920, 2560, 3840) else 1920, "codec": codec if codec in ("h264", "hevc") else "h264",
            "srt": bool(ex.get("srt"))}


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _write_srt(path: Path, words: list, cap: dict) -> None:
    """Subtitle file matching the Short's caption lines (for uploading captions separately)."""
    chunks = chunk_words(words, max(3, cap.get("chunk", 3)), max(24, cap.get("maxchars", 18)))
    lines = []
    for i, ch in enumerate(chunks, 1):
        lines.append(f"{i}\n{_srt_time(ch[0]['s'])} --> {_srt_time(ch[-1]['e'] + 0.15)}\n"
                     + " ".join(w["w"] for w in ch).strip() + "\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def projects_base_parts(c: dict) -> list:
    from .projects import base_parts
    return base_parts(c)


def _json_track(tr: dict) -> dict:
    """Face track in JSON-safe form (NaN -> None), rounded to keep project files small."""
    def f(v, nd=4):
        return None if v is None or v != v else round(float(v), nd)
    return {"times": [round(t, 3) for t in tr.get("times", [])], "cx": [f(v) for v in tr.get("cx", [])],
            "cy": [f(v) for v in tr.get("cy", [])], "size": [f(v) for v in tr.get("size", [])],
            "coverage": tr.get("coverage", 0.0), "faces_max": tr.get("faces_max", 0)}


def slice_track(cached: list, a: float, b: float) -> Optional[dict]:
    """Face track for source range [a, b] cut from a cached track of the original part containing it."""
    nan = float("nan")
    for ent in cached or []:
        A, B, tr = ent["a"], ent["b"], ent["t"]
        if a >= A - 0.05 and b <= B + 0.05:
            out = {"times": [], "cx": [], "cy": [], "size": [], "faces_max": tr.get("faces_max", 0)}
            for i, t in enumerate(tr.get("times", [])):
                ta = A + t
                if a - 0.3 <= ta <= b + 0.3:
                    out["times"].append(max(0.0, ta - a))
                    for k in ("cx", "cy", "size"):
                        v = (tr.get(k) or [None] * (i + 1))[i] if i < len(tr.get(k) or []) else None
                        out[k].append(nan if v is None else float(v))
            if not out["times"]:
                return None
            hits = sum(1 for v in out["cx"] if v == v)
            out["coverage"] = hits / len(out["cx"]) if out["cx"] else 0.0
            return out
    return None


def face_top(cached: Optional[list], pieces: list, layout: str, media: str = "", src_offset: float = 0.0,
             camera: Optional[list] = None, src_wh: tuple = (0, 0)) -> tuple[Optional[float], bool]:
    """(top of the speaker's head in the finished frame in px of 1920, measured by the cut-out model?) over the
    opening, for the face-following framings. The cut-out measurement is exact and also means the title can go
    behind the head; the face-tracker estimate is the fallback."""
    if not pieces or layout not in ("smart_crop", "center_crop", "zoom45"):
        return None, False
    a, b = float(pieces[0][0]), float(pieces[0][1])
    sw, sh = src_wh
    if media and sw and sh:
        from . import cutout
        if cutout.available():
            cw = sh * 9 / 16
            x = (camera[0][1] if camera else (sw - cw) / 2)
            top = cutout.head_top_at(media, max(0.0, a - src_offset + 0.6), x / sw, (x + cw) / sw)
            if top is not None:
                if layout == "zoom45":
                    return (1920 - 1350) / 2 - 1920 * 0.03 + top * 1350, True
                return top * 1920, True
    if not cached:
        return None, False
    tr = slice_track(cached, a, min(b, a + 4.0))
    if not tr:
        return None, False
    tops = sorted(cy - 0.62 * sz for t, cy, sz in zip(tr["times"], tr["cy"], tr["size"])
                  if t <= 3.5 and cy == cy and sz == sz)
    if len(tops) < 2:
        return None, False
    top = tops[len(tops) // 2]
    if layout == "zoom45":
        return (1920 - 1350) / 2 - 1920 * 0.03 + top * 1350, False
    return top * 1920, False


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
