"""Speech-to-text with word timestamps (faster-whisper, runs locally)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import CACHE_DIR, MODELS_DIR
from .utils import Cancelled, run_ffmpeg

TRANSCRIPTS = CACHE_DIR / "transcripts"
AUDIO = CACHE_DIR / "audio"
TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)


def extract_audio(video: str, vid: str, duration: float, cancel=None, on_progress=None) -> str:
    out = AUDIO / f"{vid}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return str(out)
    tmp = out.with_suffix(".tmp.wav")
    run_ffmpeg(["-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)],
               duration, on_progress, cancel)
    os.replace(tmp, out)
    return str(out)


def energy_curve(wav_path: str, hop: float = 0.1) -> np.ndarray:
    """Loudness (dB, RMS) every `hop` seconds."""
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        n = int(sr * hop)
        vals = []
        while True:
            buf = w.readframes(n * 600)
            if not buf:
                break
            a = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            k = len(a) // n
            if k == 0:
                break
            blocks = a[: k * n].reshape(k, n)
            rms = np.sqrt(np.mean(blocks ** 2, axis=1) + 1e-10)
            vals.append(20 * np.log10(rms + 1e-9))
    return np.concatenate(vals) if vals else np.zeros(1)


def _add_cuda_dll_dirs() -> None:
    """Make pip-installed NVIDIA libs (cuBLAS/cuDNN) discoverable on Windows."""
    if os.name != "nt":
        return
    for base in sys.path:
        nv = Path(base) / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                try:
                    os.add_dll_directory(str(b))  # type: ignore[attr-defined]
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass


_model_lock = threading.Lock()
_model_cache: dict = {}

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

MODEL_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3090, "large-v3-turbo": 1620,
            "turbo": 1620}
ALLOW = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


RETRY_WAITS = [20, 60, 120, 180]   # seconds between model download attempts (covers HF's 5 min rate window)


class ModelDownloadError(RuntimeError):
    """The Whisper model couldn't be downloaded (the job must stop: Shorts without captions aren't usable)."""


def _transient(e: Exception) -> bool:
    """Worth retrying: rate limit (429), server error (5xx) or a network hiccup."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code is not None:
        return code == 429 or code >= 500
    msg = str(e).lower()
    return any(k in msg for k in ("429", "too many requests", "timed out", "timeout", "connection",
                                  "temporarily", "503", "502"))


def _repo(size: str) -> str:
    from faster_whisper.utils import _MODELS
    return size if "/" in size else _MODELS[size]


def model_path(size: str) -> Optional[str]:
    """Local folder of an already-downloaded model, else None."""
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(_repo(size), cache_dir=str(MODELS_DIR), allow_patterns=ALLOW, local_files_only=True)
    except Exception:
        return None


def ensure_model(size: str, on_progress: Optional[Callable[[float, str], None]] = None,
                 cancel: Optional[threading.Event] = None) -> str:
    """Download the Whisper model once (resumable), reporting MB progress. Returns its folder."""
    p = model_path(size)
    if p and (Path(p) / "model.bin").exists():
        return p
    from huggingface_hub import snapshot_download

    repo = _repo(size)
    total = MODEL_MB.get(size, 500) * 1024 * 1024
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True, timeout=10)
        t = sum((f.size or 0) for f in info.siblings if f.rfilename in ("model.bin", "tokenizer.json", "config.json")
                or f.rfilename.startswith("vocabulary") or f.rfilename == "preprocessor_config.json")
        total = t or total
    except Exception:
        pass
    blobs = MODELS_DIR / ("models--" + repo.replace("/", "--")) / "blobs"

    def _size() -> int:
        try:
            return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file()) if blobs.exists() else 0
        except OSError:
            return 0

    for attempt, wait in enumerate(RETRY_WAITS + [None]):
        result: dict = {}

        def work():
            try:
                result["path"] = snapshot_download(repo, cache_dir=str(MODELS_DIR), allow_patterns=ALLOW)
            except Exception as e:  # noqa: BLE001
                result["error"] = e

        th = threading.Thread(target=work, daemon=True)
        th.start()
        t0, start_bytes = time.time(), _size()  # resumes a partial download: measure speed from here
        last = start_bytes
        while th.is_alive():
            if cancel is not None and cancel.is_set():
                raise Cancelled()  # the download keeps going in the background and resumes next time
            th.join(0.5)
            done = _size() or last
            last = done
            speed = (done - start_bytes) / max(time.time() - t0, 0.5)
            if on_progress:
                left = (total - done) / speed if speed > 0 else 0
                eta = f" · ~{int(left // 60)} min {int(left % 60)} s left" if speed > 0 and done > 0 else ""
                on_progress(min(0.99, done / total),
                            f"{done / 1048576:.0f} / {total / 1048576:.0f} MB · {speed / 1048576:.1f} MB/s{eta}")
        if "error" not in result:
            break
        err = result["error"]
        if wait is None or not _transient(err):
            busy = " Hugging Face is limiting downloads right now; try again in a few minutes." if _transient(err) else ""
            raise ModelDownloadError(f"Couldn't download the speech model.{busy} ({str(err)[:200]})")
        # Hugging Face rate-limits anonymous downloads (HTTP 429) in ~5 minute windows: wait it out, then resume
        end = time.time() + wait
        while time.time() < end:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if on_progress:
                on_progress(min(0.99, _size() / total), f"Download server busy · retry {attempt + 1}/{len(RETRY_WAITS)}"
                                                        f" in {int(end - time.time())} s")
            time.sleep(0.5)
    if on_progress:
        on_progress(1.0, "Model ready")
    return result["path"]


_GPU_BROKEN = False


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def load_model(size: str, use_gpu: bool, log: Optional[Callable[[str], None]] = None, path: Optional[str] = None):
    from faster_whisper import WhisperModel

    key = (size, use_gpu)
    with _model_lock:
        if key in _model_cache:
            return _model_cache[key]
        src = path or size
        threads = max(4, os.cpu_count() or 4)
        model = None
        on_gpu = False
        if use_gpu:
            _add_cuda_dll_dirs()
            try:
                model = WhisperModel(src, device="cuda", compute_type="float16", download_root=str(MODELS_DIR))
                on_gpu = True
                if log:
                    log("Whisper running on NVIDIA GPU")
            except Exception as e:
                if log:
                    log(f"GPU not available ({e}); using CPU")
        if model is None:
            model = WhisperModel(src, device="cpu", compute_type="int8", cpu_threads=threads,
                                 download_root=str(MODELS_DIR))
        _model_cache.clear()
        _model_cache[key] = (model, on_gpu)
        return _model_cache[key]


def transcribe(
    wav: str,
    vid: str,
    duration: float,
    model_size: str = "small",
    language: str = "auto",
    use_gpu: Optional[bool] = False,
    on_progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[threading.Event] = None,
    log: Optional[Callable[[str], None]] = None,
    on_stage: Optional[Callable[[str, float, str], None]] = None,
    caption_lang: str = "auto",
) -> dict:
    """Return {'language': str, 'segments': [{start,end,text,words:[{w,s,e,p}]}]}.

    on_stage(stage, frac, detail) reports the one-time model download and loading separately."""
    # caption_lang: "auto" = same as spoken, "en" = translate any speech to English,
    # "ur"/"hi" = write the speech in Urdu / Hindi script (Urdu & Hindi speech sound the same to Whisper)
    task, force_lang = "transcribe", (None if language in ("", "auto") else language)
    if caption_lang == "en":
        task = "translate"
    elif caption_lang in ("ur", "hi", "ar", "pa"):
        force_lang = caption_lang
    # "roman" transcribes in the spoken language's own script (most accurate) and converts later
    tag = language if caption_lang in ("", "auto", "roman") else f"{language}-to-{caption_lang}"
    cache = TRANSCRIPTS / f"{vid}_{model_size}_{tag}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    stage = on_stage or (lambda *a: None)
    path = model_path(model_size)
    if not path or not (Path(path) / "model.bin").exists():
        if log:
            log(f"Downloading the '{model_size}' speech model (one time only)…")
        path = ensure_model(model_size, lambda f, d: stage("Downloading AI speech model (one time)", f, d), cancel)
    stage("Loading AI speech model", 0.0, "")
    global _GPU_BROKEN
    if use_gpu is None:                     # auto: use an NVIDIA GPU when one is present and working
        use_gpu = cuda_available() and not _GPU_BROKEN
    model, on_gpu = load_model(model_size, use_gpu, log, path)
    if cancel is not None and cancel.is_set():
        raise Cancelled()

    def run(model, on_gpu) -> dict:
        from faster_whisper import BatchedInferencePipeline
        batched = BatchedInferencePipeline(model=model)
        if on_progress:
            on_progress(0.0, "Starting…" + (" (GPU)" if on_gpu else ""))
        segments, info = batched.transcribe(
            wav,
            language=force_lang,
            task=task,
            word_timestamps=True,
            vad_filter=True,
            batch_size=16 if on_gpu else 8,
            beam_size=5 if on_gpu else 1,   # greedy on CPU is ~2x faster with nearly the same accuracy
        )
        if log:
            log(f"Detected language: {info.language} ({info.language_probability:.0%})")
        out = {"language": "en" if task == "translate" else info.language, "spoken": info.language, "segments": []}
        t0 = time.time()
        for seg in segments:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            words = [
                {"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}
                for w in (seg.words or []) if w.word.strip()
            ]
            out["segments"].append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                                    "text": seg.text.strip(), "words": words})
            if on_progress and duration > 0:
                f = min(1.0, seg.end / duration)
                el = time.time() - t0
                eta = ""
                if f > 0.03:
                    left = el / f * (1 - f)
                    eta = f" · ~{int(left // 60)} min {int(left % 60)} s left"
                on_progress(f, f"{int(seg.end) // 60}:{int(seg.end) % 60:02d} / {int(duration) // 60}:"
                               f"{int(duration) % 60:02d}{eta}")
        return out

    try:
        out = run(model, on_gpu)
    except Cancelled:
        raise
    except Exception as e:
        if not on_gpu:
            raise
        _GPU_BROKEN = True                  # e.g. CUDA/cuDNN libraries missing: finish on the CPU instead
        if log:
            log(f"GPU speech recognition failed ({str(e)[:120]}); continuing on the CPU")
        model, on_gpu = load_model(model_size, False, log, path)
        out = run(model, on_gpu)
    out["segments"].sort(key=lambda s: s["start"])
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out
