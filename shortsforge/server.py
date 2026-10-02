"""Local web server for the new interface (127.0.0.1 only).

* Serves the prebuilt interface (web/dist), caption fonts and media files (with Range support so previews seek).
* JSON API under /api/... and a live event stream at /api/events (Server-Sent Events).
* Every API/media request must carry the per-launch token (header X-RR-Token or ?t=), and the Host header
  must be this machine, so other websites open in a browser can't talk to the app.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import traceback
import urllib.parse
from dataclasses import asdict, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _QuietServer(ThreadingHTTPServer):
    """The preview player cancels video range requests all the time (seeking): that's normal, don't log it."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)
from pathlib import Path
from typing import Callable, Optional

from . import __version__, cookies, projects
from .config import CACHE_DIR, FONTS_DIR, Settings, app_root, data_dir, music_folder, usable_output_dir
from .engine import Engine, accounts_view

WEB_DIST = app_root() / "web" / "dist"
FRAMES = CACHE_DIR / "frames"
FRAMES.mkdir(parents=True, exist_ok=True)
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("video/mp4", ".mp4")

# optional native file dialog provided by the desktop window (pywebview); see app.py
FILE_DIALOG: Optional[Callable[[str, bool], list]] = None
FOCUS: Optional[Callable[[], None]] = None     # brings the app window to the front (second start)
SECRET_KEYS = set()          # settings are local to this PC; nothing is hidden from the owner's own UI


class ApiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


def _allowed_roots() -> list[Path]:
    s = Settings.load()
    roots = [data_dir(), Path(s.music_dir or "."), usable_output_dir(s.output_dir)[0]]
    for p in projects.list_all():
        if p.get("out_dir"):
            roots.append(Path(p["out_dir"]))
        for c in p.get("clips", []):
            f = (c.get("media") or {}).get("file")
            if f:
                roots.append(Path(f).parent)
    out = []
    for r in roots:
        try:
            out.append(r.expanduser().resolve())
        except OSError:
            pass
    return out


def _is_allowed(p: Path) -> bool:
    try:
        rp = p.expanduser().resolve()
    except OSError:
        return False
    for r in _allowed_roots():
        try:
            rp.relative_to(r)
            return True
        except ValueError:
            continue
    return False


# ================================================================ native pickers
def pick_files(kind: str = "video", multi: bool = False) -> list[str]:
    if FILE_DIALOG:
        try:
            return [str(x) for x in (FILE_DIALOG(kind, multi) or [])]
        except Exception:
            pass
    filters = {"video": "Videos|*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.flv;*.wmv|All files|*.*",
               "music": "Audio|*.mp3;*.m4a;*.wav;*.ogg;*.aac;*.flac|All files|*.*",
               "cookies": "Cookies|*.txt|All files|*.*", "exe": "Programs|*.exe|All files|*.*",
               "image": "Images|*.png;*.jpg;*.jpeg;*.webp|All files|*.*"}.get(kind, "All files|*.*")
    if os.name == "nt":
        if kind == "folder":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.SelectedPath}")
        else:
            ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.OpenFileDialog;"
                  f"$d.Filter='{filters}';$d.Multiselect=${'true' if multi else 'false'};"
                  "if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8;$d.FileNames -join \"`n\"}")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", creationflags=0x08000000)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()]
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        if kind == "folder":
            d = filedialog.askdirectory()
            res = [d] if d else []
        elif multi:
            res = list(filedialog.askopenfilenames())
        else:
            f = filedialog.askopenfilename()
            res = [f] if f else []
        root.destroy()
        return res
    except Exception:
        raise ApiError("File picking isn't available here; paste the file path instead.")


def open_path(p: str, select: bool = False) -> None:
    path = Path(p)
    if not path.exists():
        raise ApiError("That file or folder no longer exists.")
    if os.name == "nt":
        if select and path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select and path.is_file() else path)])


# ================================================================ catalog (styles etc.)
def catalog() -> dict:
    from .captions import CAPTION_STYLES, CTA_STYLES, HOOK_STYLES, WM_POSITIONS
    from .effects import COLOR_GRADES, INTROS, LAYOUTS, MOTIONS
    from .fonts import FONT_SOURCES
    from .music_sources import BROWSER_SOURCES
    from .publish import PLATFORMS
    return {
        "captions": CAPTION_STYLES, "hooks": HOOK_STYLES, "ctas": CTA_STYLES,
        "grades": {k: {"name": v["name"]} for k, v in COLOR_GRADES.items()},
        "motions": MOTIONS, "intros": INTROS, "layouts": LAYOUTS, "wm_positions": WM_POSITIONS,
        "fonts": {fam: fn for fam, (fn, _u) in FONT_SOURCES.items() if (FONTS_DIR / fn).exists()},
        "metrics": _font_metrics(),
        "export_presets": _export_presets(),
        "music_sources": {k: {"name": v[0], "note": v[2]} for k, v in BROWSER_SOURCES.items()},
        "platforms": PLATFORMS,
        "packs": STYLE_PACKS,
        "vibes": _vibes(),
        "sfx_packs": _sfx_packs(),
    }


def _vibes() -> dict:
    from .vibe import catalog as vc
    return vc()


def _sfx_packs() -> dict:
    from .sfx import PACK_NAMES
    return PACK_NAMES


def _export_presets() -> dict:
    from .pipeline import EXPORT_PRESETS
    return {k: {"name": v[0], "height": v[1], "fps": v[2], "quality": v[3], "codec": v[4]} for k, v in EXPORT_PRESETS.items()}


def _font_metrics() -> dict:
    from .fonts import FONT_SOURCES, _metrics
    m = _metrics()
    return {fam: m[fn] for fam, (fn, _u) in FONT_SOURCES.items() if fn in m}


# One-tap looks for the Create page (each is a full style choice)
STYLE_PACKS = {
    "vibe": {"name": "Auto Vibe", "desc": "Zooms, opening, colour, music & sounds matched to each clip",
             "set": {"color_grade": "auto", "motion": "auto", "intro": "auto", "layout": "auto"}},
    "viral": {"name": "Viral Bold", "desc": "Big yellow word pop, punch zooms",
              "set": {"caption_style": "hormozi", "hook_style": "yellow_impact", "color_grade": "vibrant",
                      "motion": "punch", "intro": "flash", "layout": "auto"}},
    "beast": {"name": "Beast Energy", "desc": "Red highlights, shake intro",
              "set": {"caption_style": "beast", "hook_style": "red_tag", "color_grade": "hdr",
                      "motion": "punch", "intro": "shake", "layout": "auto"}},
    "podcast": {"name": "Podcast Clean", "desc": "Karaoke sweep, calm camera",
                "set": {"caption_style": "karaoke", "hook_style": "headline", "color_grade": "cinematic",
                        "motion": "slow_zoom", "intro": "fade_black", "layout": "auto"}},
    "neon": {"name": "Neon Night", "desc": "Glow captions, moody grade",
             "set": {"caption_style": "neon", "hook_style": "neon_sign", "color_grade": "moody",
                     "motion": "breathe", "intro": "flash", "layout": "auto"}},
    "story": {"name": "Storyteller", "desc": "Highlight box, warm film",
              "set": {"caption_style": "pill", "hook_style": "banner_white", "color_grade": "warm",
                      "motion": "ken_burns", "intro": "fade_black", "layout": "auto"}},
    "minimal": {"name": "Minimal", "desc": "Clean small captions, no effects",
                "set": {"caption_style": "minimal", "hook_style": "headline", "color_grade": "none",
                        "motion": "none", "intro": "none", "layout": "auto"}},
    "duo": {"name": "Podcast Duo", "desc": "Two speakers stacked, clean captions",
            "set": {"caption_style": "karaoke", "hook_style": "headline", "color_grade": "cinematic",
                    "motion": "none", "intro": "fade_black", "layout": "two_speakers"}},
    "mix": {"name": "Mix it up", "desc": "Every Short gets a different look",
            "set": {"caption_style": "random", "hook_style": "random", "color_grade": "auto",
                    "motion": "auto", "intro": "auto", "layout": "auto"}},
}


# ================================================================ server
class App:
    def __init__(self, engine: Optional[Engine] = None, port: int = 0):
        self.engine = engine or Engine()
        self.token = secrets.token_urlsafe(24)
        self.httpd = _QuietServer(("127.0.0.1", port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#t={self.token}"

    def serve_background(self) -> threading.Thread:
        self.engine.start()
        t = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        t.start()
        return t

    def shutdown(self) -> None:
        self.engine.stop()
        self.httpd.shutdown()


def _json_default(o):
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    try:
        return asdict(o)
    except Exception:
        return str(o)


def _make_handler(app: App):
    E = app.engine

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"RRShorts/{__version__}"

        def log_message(self, *a):
            pass

        # ---------------------------------------------------------------- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

        def _token_ok(self, qs: dict) -> bool:
            t = self.headers.get("X-RR-Token") or (qs.get("t") or [""])[0]
            return secrets.compare_digest(str(t), app.token)

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=_json_default).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20_000_000:
                raise ApiError("Request too large.", 413)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                raise ApiError("Bad JSON.")

        # ---------------------------------------------------------------- routing
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method: str):
            u = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(u.query)
            path = u.path
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            try:
                if path.startswith("/api/") or path == "/media":
                    if not self._token_ok(qs):
                        return self._send(401, b"unauthorized", "text/plain")
                    if path == "/api/events":
                        return self._events()
                    if path == "/media":
                        return self._media(qs.get("path", [""])[0])
                    fn = ROUTES.get((method, path))
                    if not fn:
                        raise ApiError("Not found", 404)
                    arg = self._body() if method == "POST" else {k: v[0] for k, v in qs.items()}
                    res = fn(arg)
                    if isinstance(res, tuple) and res and res[0] == "__file__":
                        return self._file(Path(res[1]))
                    return self._json({"ok": True, "data": res})
                if method != "GET":
                    raise ApiError("Not found", 404)
                if path.startswith("/fonts/"):
                    name = Path(urllib.parse.unquote(path[7:])).name
                    f = FONTS_DIR / name
                    return self._file(f, cache=True) if f.exists() else self._send(404, b"", "text/plain")
                return self._static(path)
            except ApiError as e:
                return self._json({"ok": False, "error": str(e)}, e.code)
            except (ValueError, KeyError) as e:
                msg = f"Missing field: {e}" if isinstance(e, KeyError) else str(e)
                return self._json({"ok": False, "error": msg}, 400)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                traceback.print_exc()
                return self._json({"ok": False, "error": (str(e).split("\n")[0] or type(e).__name__)[:400]}, 500)

        def _static(self, path: str):
            rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
            f = (WEB_DIST / rel).resolve()
            try:
                f.relative_to(WEB_DIST.resolve())
            except ValueError:
                return self._send(403, b"", "text/plain")
            if not f.is_file():
                f = WEB_DIST / "index.html"
            if not f.exists():
                return self._send(500, b"Interface files are missing (web/dist). Reinstall the app or run "
                                       b"with --classic.", "text/plain")
            return self._file(f, cache=rel.startswith("assets/"))

        def _file(self, f: Path, cache: bool = False):
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            data = f.read_bytes()
            extra = {"Cache-Control": "public, max-age=31536000, immutable"} if cache else {}
            if f.name == "index.html":
                extra["Content-Security-Policy"] = (
                    "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
                    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; connect-src 'self'")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if "Cache-Control" not in extra:
                self.send_header("Cache-Control", "no-store")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _media(self, p: str):
            f = Path(urllib.parse.unquote(p))
            if not p or not f.is_file() or not _is_allowed(f):
                return self._send(404, b"not found", "text/plain")
            size = f.stat().st_size
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            code = 200
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                        if m.group(2):
                            end = min(size - 1, int(m.group(2)))
                    elif m.group(2):
                        start = max(0, size - int(m.group(2)))
                    if start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    code = 206
            length = end - start + 1
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(f, "rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(262144, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    left -= len(chunk)

        def _events(self):
            q = E.bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(b"retry: 1500\n\n")
                self.wfile.flush()
                while True:
                    try:
                        kind, data = q.get(timeout=15)
                        msg = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=_json_default)}\n\n"
                    except queue.Empty:
                        msg = ": ping\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                E.bus.unsubscribe(q)

    # ================================================================ API functions
    def _settings_dict() -> dict:
        return asdict(Settings.load())

    def state(_a):
        from . import uploadqueue
        return {"version": __version__, "status": E.status(), "settings": _settings_dict(),
                "jobs": list(E.jobs.values()), "exports": list(E.exports.values()),
                "projects": [projects.summary(p) for p in projects.list_all()], "accounts": accounts_view(),
                "queue": uploadqueue.load(), "logs": list(E.bus.logs), "catalog": catalog()}

    def analyze(a):
        if a.get("settings"):
            save_settings(a["settings"])
        return E.add_sources(str(a.get("sources", "")), a.get("auto_export"))

    def job_cancel(a):
        E.cancel_job(str(a.get("id")))
        return True

    def job_retry(a):
        return E.retry_job(str(a.get("id")))

    def jobs_clear(_a):
        E.clear_jobs()
        return True

    def project_get(a):
        p = projects.load(str(a.get("id", "")))
        if not p:
            raise ApiError("Project not found.", 404)
        for c in p["clips"]:
            c.pop("tracks", None)
            c.pop("prep", None)
        return p

    def project_delete(a):
        pid = str(a.get("id", ""))
        projects.delete(pid)
        shutil.rmtree(projects.PROJECTS / pid, ignore_errors=True)
        return True

    def clip_edits(a):
        c = projects.update_clip(str(a["project"]), str(a["clip"]), edits=a.get("edits") or {})
        if c is None:
            raise ApiError("Clip not found.", 404)
        return True

    def clip_export(a):
        return E.export(str(a["project"]), [str(a["clip"])], a.get("edits"))

    def export_all(a):
        p = projects.load(str(a["project"]))
        if not p:
            raise ApiError("Project not found.", 404)
        ids = a.get("clips") or [c["id"] for c in p["clips"]]
        return E.export(p["id"], ids)

    def clip_frame(a):
        from .pipeline import Pipeline
        p = projects.load(str(a["project"]))
        if not p:
            raise ApiError("Project not found.", 404)
        out = FRAMES / f"{p['id']}_{a['clip']}_{int(time.time() * 1000) % 100000}.png"
        for old in FRAMES.glob(f"{p['id']}_{a['clip']}_*.png"):
            old.unlink(missing_ok=True)
        png, D = Pipeline(Settings.load(), log=lambda m: None).preview_clip(
            p, str(a["clip"]), a.get("edits") or {}, float(a.get("t", 1.0)), str(out))
        return {"path": png, "duration": D}

    def _music_sig(st) -> str:
        from . import music_index
        from .config import usable_music_dir
        try:
            ps = music_index.tracks_in([st.music_dir, str(usable_music_dir(st.music_dir)[0])])
            return f"{len(ps)}:{max((int(p.stat().st_mtime) for p in ps), default=0)}"
        except Exception:
            return ""

    def clip_audio(a):
        import hashlib
        from .pipeline import Pipeline
        p = projects.load(str(a["project"]))
        if not p:
            raise ApiError("Project not found.", 404)
        edits = a.get("edits") or {}
        st = Settings.load()
        key = hashlib.md5(json.dumps([p["id"], a["clip"], edits, st.sfx_level, st.add_music, st.music_mode,
                                      st.music_selected, st.cta_text, st.sfx_pack, st.music_match, st.zoom_strength,
                                      st.motion, st.intro, st.color_grade, 8, _music_sig(st),
                                      st.story_fx, st.story_pauses, st.story_titles, st.story_transitions,
                                      st.story_textures, st.story_behind, st.watermark, st.remove_pauses,
                                      len((projects.clip(p, str(a["clip"])) or {}).get("camera") or [])],
                                     sort_keys=True, default=str).encode()).hexdigest()[:14]
        out = FRAMES / f"aud_{key}.ogg"
        side = FRAMES / f"aud_{key}.json"
        if side.exists():
            try:
                cached = json.loads(side.read_text(encoding="utf-8"))
                if not cached.get("path") or Path(cached["path"]).exists():
                    return cached
            except Exception:
                pass
        for old in FRAMES.glob("aud_*.*"):
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink(missing_ok=True)
        path, D, plan = Pipeline(st, log=lambda m: None).preview_audio(p, str(a["clip"]), edits, str(out))
        res = {"path": path, "duration": D, "plan": plan}
        try:
            side.write_text(json.dumps(res), encoding="utf-8")
        except OSError:
            pass
        return res

    def clip_proxy(a):
        from .pipeline import make_proxy
        p = projects.load(str(a["project"]))
        if not p:
            raise ApiError("Project not found.", 404)
        return make_proxy(p, str(a["clip"]))

    def library(_a):
        from .pipeline import load_library
        s = Settings.load()
        items = load_library(str(usable_output_dir(s.output_dir)[0]))
        seen = {i["plan_file"] for i in items}
        for p in projects.list_all():          # Shorts saved to older output folders
            if p.get("out_dir") and Path(p["out_dir"]).parent != usable_output_dir(s.output_dir)[0]:
                for i in load_library(str(Path(p["out_dir"]).parent)):
                    if i["plan_file"] not in seen:
                        items.append(i)
                        seen.add(i["plan_file"])
        out = []
        for d in items:
            out.append({"plan_file": d["plan_file"], "output": d["output"], "thumb": d.get("thumb", ""),
                        "meta": d.get("meta") or {}, "source": d.get("source", ""),
                        "source_title": d.get("source_title", ""), "created": d.get("created", 0),
                        "duration": (d.get("prep") or {}).get("D", 0), "project_ref": d.get("project_ref"),
                        "style": d.get("style") or {}, "hook_text": d.get("hook_text", ""),
                        "cover": d.get("cover") if d.get("cover") and Path(d["cover"]).exists() else "",
                        "cover_info": d.get("cover_info") or {}})
        out.sort(key=lambda d: d["created"], reverse=True)
        return out

    def library_meta(a):
        from .pipeline import save_meta
        return save_meta(a["plan_file"], a.get("title", ""), a.get("body", ""), a.get("tags") or [],
                         a.get("hashtags"))

    def _delete_short(pf: Path) -> bool:
        """Delete a finished Short and its waiting uploads. False if it is being posted right now."""
        from . import uploadqueue
        if not _is_allowed(pf):
            raise ApiError("Not allowed.", 403)
        if uploadqueue.drop_plan(str(pf)):
            return False
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
            out = Path(d["output"])
            for f in (out, out.with_suffix(".jpg"), out.with_suffix(".txt"), out.with_suffix(".cover.jpg"), pf):
                for _ in range(3):
                    try:
                        f.unlink(missing_ok=True)
                        break
                    except PermissionError:   # a preview may still hold the file for a moment
                        time.sleep(0.4)
        except FileNotFoundError:
            pass
        return True

    def library_delete(a):
        ok = _delete_short(Path(a["plan_file"]))
        E.bus.emit("queue", {})
        if not ok:
            raise ApiError("This Short is being posted right now. Delete it when the upload finishes.")
        return True

    def library_delete_many(a):
        done, busy, failed = 0, 0, 0
        for pf in a.get("plan_files") or []:
            try:
                if _delete_short(Path(str(pf))):
                    done += 1
                else:
                    busy += 1
            except ApiError:
                raise
            except Exception:
                failed += 1
        E.bus.emit("queue", {})
        return {"deleted": done, "posting": busy, "failed": failed}

    # ---- thumbnails (optional): prompt + reference -> AI image, or a clean frame + title
    def _thumb_plan(a):
        pf = Path(str(a["plan_file"]))
        if not _is_allowed(pf) or not pf.is_file():
            raise ApiError("That Short is no longer in the library.", 404)
        return pf, json.loads(pf.read_text(encoding="utf-8"))

    def thumb_info(_a):
        from . import thumbnail
        s = Settings.load()
        return {"providers": thumbnail.providers(s), "aspects": list(thumbnail.SIZES)}

    def thumb_ref(a):
        from . import thumbnail
        path = str(a.get("path") or "")
        if not path:
            got = pick_files("image", False)
            if not got:
                return {"path": ""}
            path = got[0]
        try:
            return {"path": thumbnail.keep_reference(path)}
        except thumbnail.ThumbError as e:
            raise ApiError(str(e))

    def thumb_make(a):
        from . import thumbnail
        from .renderer import _camera_at
        pf, d = _thumb_plan(a)
        s = Settings.load()
        aspect = str(a.get("aspect") or "9:16")
        if aspect not in thumbnail.SIZES:
            aspect = "9:16"
        mode = str(a.get("mode") or "ai")
        title = str(a.get("title") if a.get("title") is not None else (d.get("hook_text") or d.get("meta", {}).get("title", "")))
        prompt = str(a.get("prompt") or "")[:2000]
        ref = str(a.get("ref") or "")
        if ref and not (_is_allowed(Path(ref)) and Path(ref).is_file()):
            ref = ""
        out = Path(d["output"])
        work = data_dir() / "work" / "thumbs"
        work.mkdir(parents=True, exist_ok=True)
        # a clean frame of the Short (no captions): from the source when it's still cached, else from the export
        frame = ""
        if mode == "frame" or a.get("use_frame", True):
            prep = d.get("prep") or {}
            D = float(prep.get("D") or 10)
            pos = min(max(0.0, float(a.get("at") if a.get("at") is not None else 0.3)), 0.98)
            fpath = str(work / (out.stem[:40].replace(" ", "_") + "_frame.jpg"))
            try:
                src, info = d.get("src_file") or "", d.get("info") or {}
                if src and Path(src).exists():
                    from .renderer import RenderJob, source_time
                    cl = d["clip"]
                    job = RenderJob(src=src, start=cl["start"], end=cl["end"], out_path="", src_w=info.get("width", 0),
                                    src_h=info.get("height", 0), ass_file="", parts=prep.get("pieces") or [])
                    tp = pos * job.cut_duration
                    cam = prep.get("camera") or []
                    cx = _camera_at([tuple(c) for c in cam], tp) if cam and prep.get("layout") == "smart_crop" else None
                    frame = thumbnail.clean_frame(src, source_time(job, tp) - float(d.get("src_offset") or 0.0), fpath,
                                                  aspect, cx, (info.get("width", 0), info.get("height", 0)))
                else:
                    frame = thumbnail.clean_frame(str(out), pos * D, fpath, aspect)
            except Exception as e:
                if mode == "frame":
                    raise ApiError(f"Couldn't grab a frame from the video: {str(e).splitlines()[0][:160]}")
                frame = ""
        dst = str(out.with_suffix(".cover.jpg"))
        try:
            if mode == "frame":
                res = thumbnail.generate_frame(title, aspect, frame, dst)
            else:
                res = thumbnail.generate_ai(s, prompt, title, aspect, ref, frame, dst,
                                            str(a.get("provider") or "auto"), E.bus.log)
        except thumbnail.ThumbError as e:
            raise ApiError(str(e))
        d2 = json.loads(pf.read_text(encoding="utf-8"))
        d2["cover"] = res["path"]
        d2["cover_info"] = {"mode": mode, "prompt": prompt, "ref": ref, "aspect": aspect, "title": title,
                            "provider": res["provider"], "use_frame": bool(a.get("use_frame", True)),
                            "time": time.time()}
        pf.write_text(json.dumps(d2, ensure_ascii=False, indent=1), encoding="utf-8")
        E.bus.emit("library", {})
        return {"cover": res["path"], "provider": res["provider"], "cover_info": d2["cover_info"]}

    def _thumb_frame(d: dict, aspect: str, pos: float, tag: str) -> str:
        """Clean frame of the Short (no captions) for the thumbnail tools."""
        from . import thumbnail
        from .renderer import RenderJob, _camera_at, source_time
        out = Path(d["output"])
        work = data_dir() / "work" / "thumbs"
        work.mkdir(parents=True, exist_ok=True)
        prep = d.get("prep") or {}
        fpath = str(work / f"{abs(hash(str(out))) % 10**10}_{tag}_{aspect.replace(':', 'x')}_{int(pos * 1000)}.jpg")
        src, info = d.get("src_file") or "", d.get("info") or {}
        if src and Path(src).exists():
            cl = d["clip"]
            job = RenderJob(src=src, start=cl["start"], end=cl["end"], out_path="", src_w=info.get("width", 0),
                            src_h=info.get("height", 0), ass_file="", parts=prep.get("pieces") or [])
            tp = pos * job.cut_duration
            cam = prep.get("camera") or []
            cx = _camera_at([tuple(c) for c in cam], tp) if cam and prep.get("layout") == "smart_crop" else None
            return thumbnail.clean_frame(src, source_time(job, tp) - float(d.get("src_offset") or 0.0), fpath, aspect,
                                         cx, (info.get("width", 0), info.get("height", 0)))
        return thumbnail.clean_frame(str(out), pos * float(prep.get("D") or 10), fpath, aspect)

    def thumb_assets(a):
        """Designer: a clean frame + the speaker cut out of it (transparent PNG)."""
        from . import thumbnail
        pf, d = _thumb_plan(a)
        aspect = str(a.get("aspect") or "9:16")
        if aspect not in thumbnail.SIZES:
            aspect = "9:16"
        pos = min(max(0.0, float(a.get("at") if a.get("at") is not None else 0.3)), 0.98)
        try:
            frame = _thumb_frame(d, aspect, pos, "d")
        except Exception as e:
            raise ApiError(f"Couldn't grab a frame from the video: {str(e).splitlines()[0][:160]}")
        cut = frame[:-4] + "_cut.png"
        try:
            box = thumbnail.cutout_png(frame, cut)
        except Exception:
            box = None
        w, h = thumbnail.SIZES[aspect]
        return {"frame": frame, "cutout": cut if box else "", "box": box, "w": w, "h": h,
                "design": (d.get("cover_info") or {}).get("design"), "title": d.get("hook_text") or (d.get("meta") or {}).get("title", "")}

    def thumb_analyze(a):
        from . import thumbnail
        ref = str(a.get("ref") or "")
        if not ref or not (_is_allowed(Path(ref)) and Path(ref).is_file()):
            raise ApiError("Add a reference image first.")
        try:
            return thumbnail.analyze_reference(ref)
        except thumbnail.ThumbError as e:
            raise ApiError(str(e))

    def thumb_save(a):
        from . import thumbnail
        pf, d = _thumb_plan(a)
        aspect = str(a.get("aspect") or "9:16")
        if aspect not in thumbnail.SIZES:
            aspect = "9:16"
        dst = str(Path(d["output"]).with_suffix(".cover.jpg"))
        try:
            thumbnail.save_design(str(a.get("image") or ""), aspect, dst)
        except thumbnail.ThumbError as e:
            raise ApiError(str(e))
        d2 = json.loads(pf.read_text(encoding="utf-8"))
        d2["cover"] = dst
        d2["cover_info"] = {"mode": "design", "aspect": aspect, "provider": "designer", "design": a.get("design"),
                            "ref": str(a.get("ref") or ""), "time": time.time()}
        pf.write_text(json.dumps(d2, ensure_ascii=False, indent=1), encoding="utf-8")
        E.bus.emit("library", {})
        return {"cover": dst, "cover_info": d2["cover_info"]}

    def thumb_remove(a):
        pf, d = _thumb_plan(a)
        c = d.pop("cover", "")
        d.pop("cover_info", None)
        if c:
            Path(c).unlink(missing_ok=True)
        pf.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        E.bus.emit("library", {})
        return True

    def library_upload(a):
        return E.upload_now(a["plan_file"], a.get("platforms"),
                            None if a.get("direct") is None else bool(a.get("direct")))

    def open_any(a):
        p = str(a.get("path", ""))
        if p == "output":
            p = str(usable_output_dir(Settings.load().output_dir)[0])
        elif p == "music":
            p = str(music_folder())
        elif p == "errors":
            p = str(data_dir() / "upload_errors")
            Path(p).mkdir(parents=True, exist_ok=True)
        elif not _is_allowed(Path(p)):
            raise ApiError("Not allowed.", 403)
        open_path(p, bool(a.get("select")))
        return True

    # ---- accounts
    def accounts(_a):
        return accounts_view()

    def signin_start(a):
        return E.signin_start(str(a["platform"]), str(a.get("account") or ""), str(a.get("label") or ""))

    def signin_open(a):
        try:
            if a.get("label") is not None:
                E.signin_label(str(a["id"]), str(a["label"]))
            return E.signin_open(str(a["id"]))
        except KeyError:
            raise ApiError("That sign-in was closed. Start again.")

    def signin_finish(a):
        try:
            if a.get("label") is not None:
                E.signin_label(str(a["id"]), str(a["label"]))
            return E.signin_finish(str(a["id"]))
        except KeyError:
            raise ApiError("That sign-in was closed. Start again.")

    def signin_label(a):
        try:
            return E.signin_label(str(a["id"]), str(a.get("label", "")))
        except KeyError:
            raise ApiError("That sign-in was closed. Start again.")

    def signin_cancel(a):
        E.signin_cancel(str(a["id"]))
        return True

    def api_connect(a):
        from . import publish
        plat = str(a["platform"])
        if not publish.credentials_ok(plat, Settings.load()):
            raise ApiError("Add the developer keys for this platform first (Advanced).")
        E.api_connect(plat)
        return True

    def api_cancel(_a):
        from . import publish
        publish.CANCEL.set()
        return True

    def account_enable(a):
        from . import publish
        publish.set_enabled(a["platform"], a["id"], bool(a.get("on", True)))
        E.bus.emit("accounts", {})
        return True

    def account_remove(a):
        from . import publish
        publish.remove_account(a["platform"], a["id"])
        E.bus.emit("accounts", {})
        return True

    def account_open(a):
        from . import publish, webupload
        acc = next((x for x in publish.load_accounts().get(a["platform"], []) if x.get("id") == a["id"]), None)
        if not acc:
            raise ApiError("Account not found.")
        webupload.open_page(a["platform"], webupload.profile_dir(acc), acc.get("proxy", ""))
        return True

    def _acc(a):
        from . import publish
        acc = next((x for x in publish.load_accounts().get(str(a["platform"]), []) if x.get("id") == a["id"]), None)
        if not acc:
            raise ApiError("Account not found.")
        return acc

    def account_proxy(a):
        from . import proxy as px, webupload
        acc = _acc(a)
        if acc.get("mode") != "browser":
            raise ApiError("Proxies work with direct sign-in accounts (Add account), not developer-key accounts.")
        text = px.normalize(str(a.get("proxy") or ""))       # ProxyError (ValueError) -> friendly 400
        webupload.register(str(a["platform"]), acc.get("profile", ""), acc.get("name", ""), text)
        E.bus.emit("accounts", {})
        return {"proxy": px.display(text)}

    def account_proxy_test(a):
        from . import proxy as px
        text = str(a.get("proxy") or "").strip()
        if not text and a.get("id"):
            text = _acc(a).get("proxy", "")
        if not text:
            raise ApiError("Enter a proxy first.")
        px.parse(text)
        return px.check(text)

    def signin_proxy(a):
        try:
            return E.signin_proxy(str(a["id"]), str(a.get("proxy") or ""))
        except KeyError:
            raise ApiError("That sign-in was closed. Start again.")

    # ---- upload queue
    def queue_get(_a):
        from . import uploadqueue
        return uploadqueue.load()

    def queue_action(a):
        from . import uploadqueue
        jid, act = str(a.get("id") or ""), str(a["action"])
        if act == "now":
            uploadqueue.set_status(jid, due=time.time(), status="waiting", error="", waits=0)
        elif act == "retry":
            uploadqueue.set_status(jid, due=time.time(), status="waiting", attempts=0, waits=0, error="")
        elif act == "remove":
            try:
                E.stop_upload(jid)        # removing a job that is posting also closes its browser
            except Exception:
                pass
            uploadqueue.remove(jid)
        elif act == "stop":
            if not E.stop_upload(jid):
                raise ApiError("That upload isn't running any more.")
        elif act == "when":
            uploadqueue.set_status(jid, due=float(a["due"]))
        elif act == "later":
            uploadqueue.set_status(jid, due=time.time() + float(a.get("minutes", 60)) * 60, status="waiting")
        elif act == "retry_failed":
            uploadqueue.retry_failed()
        elif act == "post_all":
            uploadqueue.post_all_now()
        elif act == "clear_failed":
            uploadqueue.clear_failed()
        else:
            raise ApiError(f"Unknown queue action: {act}")
        E.kick_uploads()
        return uploadqueue.load()

    def queue_clear(_a):
        from . import uploadqueue
        uploadqueue.clear_finished()
        E.kick_uploads()
        return uploadqueue.load()

    # ---- music
    def music_list(_a):
        from . import music_index, music_sources
        s = Settings.load()
        folder = music_folder()
        trending = folder / "Trending"
        try:
            trending.mkdir(exist_ok=True)
        except OSError:
            pass
        meta = music_sources.load_meta(str(folder))
        tmeta = music_sources.load_meta(str(trending)) if trending.exists() else {}
        starred = set(s.music_selected or [])
        try:
            feel = {d["name"]: d for d in music_index.describe([str(folder)])}
        except Exception:
            feel = {}
        out = []
        files = [f for f in music_index.tracks_in([str(folder)])]
        for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            m = (tmeta if f.parent == trending else meta).get(f.name) or {}
            fe = feel.get(f.name) or {}
            out.append({"name": f.name, "path": str(f), "title": m.get("title") or f.stem,
                        "artist": m.get("artist", ""), "license": m.get("license", ""),
                        "source": m.get("source", ""), "credit": m.get("credit", ""),
                        "starred": f.name in starred, "size": f.stat().st_size,
                        "trending": f.parent == trending, "vibes": fe.get("vibes", []), "bpm": fe.get("bpm")})
        return {"folder": str(folder), "trending_folder": str(trending), "tracks": out, "mode": s.music_mode,
                "add_music": s.add_music, "volume": s.music_volume, "auto": s.music_auto,
                "match": getattr(s, "music_match", True)}

    def music_search(a):
        from . import music_sources
        s = Settings.load()
        src = a.get("source", "all")
        failed: list = []
        try:
            if src == "all":
                tracks, more, failed = music_sources.search_all(a.get("query", ""), int(a.get("page", 1)),
                                                                bool(a.get("safe", s.music_safe_only)), s.jamendo_client_id)
            else:
                tracks, more = music_sources.search(src, a.get("query", ""), int(a.get("page", 1)),
                                                    bool(a.get("safe", s.music_safe_only)), s.jamendo_client_id)
        except music_sources.SourceError as e:
            raise ApiError(str(e))
        return {"tracks": [music_sources.to_dict(t) | {"credit": t.credit(), "safe": t.monetization_ok}
                           for t in tracks], "more": more, "failed": failed}

    def music_generate(a):
        from . import musicgen
        s = Settings.load()
        mood = str(a.get("mood", "lofi"))
        if mood not in musicgen.MOODS:
            raise ApiError("Unknown mood.")
        secs = max(30, min(240, float(a.get("seconds", 90))))
        try:
            f = musicgen.make_track(mood, str(music_folder()), secs)
        except Exception as e:
            raise ApiError(f"Couldn't make the track: {str(e).splitlines()[0][:200] if str(e) else type(e).__name__}")
        if a.get("star"):
            _star(f.name, True)
        return {"name": f.name, "path": str(f)}

    def music_moods(_a):
        from . import musicgen
        return {k: v["name"] for k, v in musicgen.MOODS.items()}

    def music_download(a):
        from . import music_sources
        t = a["track"]
        valid = {f.name for f in fields(music_sources.Track)}
        tr = music_sources.Track(**{k: v for k, v in t.items() if k in valid})
        s = Settings.load()
        try:
            f = music_sources.download(tr, str(music_folder()))
        except music_sources.SourceError as e:
            raise ApiError(str(e))
        if a.get("star"):
            _star(f.name, True)
        return {"name": f.name, "path": str(f)}

    def _star(name: str, on: bool):
        s = Settings.load()
        sel = [x for x in (s.music_selected or []) if x != name]
        if on:
            sel.append(name)
        s.music_selected = sel
        s.save()

    def music_star(a):
        _star(str(a["name"]), bool(a.get("on", True)))
        return True

    def music_remove(a):
        base = music_folder()
        f = base / Path(str(a["name"])).name
        if a.get("path"):
            cand = Path(str(a["path"])).resolve()
            if cand.parent in (base.resolve(), (base / "Trending").resolve()):
                f = cand
        f.unlink(missing_ok=True)
        _star(f.name, False)
        return True

    def music_import(a):
        s = Settings.load()
        paths = a.get("paths") or pick_files("music", True)
        n = 0
        for p in paths:
            src = Path(p)
            if src.is_file():
                shutil.copy2(src, music_folder() / src.name)
                n += 1
        return n

    def music_open_source(a):
        from . import browser_login
        from .music_sources import BROWSER_SOURCES
        s = Settings.load()
        key = str(a["key"])
        if key == "youtube":
            browser_login.open_audio_library(str(music_folder()))
        else:
            browser_login.open_in_browser(BROWSER_SOURCES[key][1], str(music_folder()))
        return True

    def music_open_page(a):
        url = str(a.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ApiError("This track has no web page.")
        from . import browser_login
        browser_login.open_in_browser(url, str(music_folder()))
        return True

    def music_tag(a):
        from . import music_sources
        s = Settings.load()
        music_sources.tag_new_downloads(str(music_folder()), list(a.get("names") or []), str(a.get("key", "")))
        return True

    # ---- settings
    def save_settings(patch: dict):
        if any(k.endswith("_api_key") for k in (patch or {})):
            from . import llm
            llm._DEAD.clear()          # a new key gets a fresh chance
        s = Settings.load()
        names = {f.name for f in fields(Settings)}
        for k, v in (patch or {}).items():
            if k in names and k != "settings_version":
                cur = getattr(s, k)
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    v = int(float(v))
                elif isinstance(cur, float):
                    v = float(v)
                setattr(s, k, v)
        s.save()
        from .utils import set_ffmpeg_override
        set_ffmpeg_override(s.ffmpeg_path)
        E.bus.emit("settings", asdict(s))
        E.bus.emit("status", E.status())
        if any(k.startswith("upload_") for k in (patch or {})):
            E.kick_uploads()              # e.g. queue resumed or more parallel uploads allowed
        return asdict(s)

    def settings_set(a):
        return save_settings(a)

    def test_key(a):
        from .llm import test_key as tk
        return tk(str(a["provider"]), str(a["key"]).strip(), str(a.get("model") or "auto"))

    def yt_signout(_a):
        from . import browser_login
        cookies.sign_out()
        try:
            browser_login.sign_out()
        except Exception:
            pass
        E.bus.emit("status", E.status())
        return True

    def yt_import(a):
        paths = [a["path"]] if a.get("path") else pick_files("cookies")
        if not paths:
            return False
        ok = cookies.import_file(paths[0])
        E.bus.emit("status", E.status())
        return ok

    def watch_check(_a):
        return E.watch_tick(force=True)

    def watch_state(_a):
        from . import autowatch
        return {"last": autowatch.last_check()}

    def watch_forget(a):
        from . import autowatch
        autowatch.forget(str(a["channel"]))
        return True

    def style_preview(a):
        from . import previews
        fn = {"cap": previews.caption_preview, "hook": previews.hook_preview, "cta": previews.cta_preview,
              "grade": previews.grade_preview}[a.get("kind", "cap")]
        return ("__file__", str(fn(a["key"])))

    def pick(a):
        return pick_files(str(a.get("kind", "video")), bool(a.get("multi")))

    def logs(_a):
        return list(E.bus.logs)

    def license_info(_a):
        from . import licensing
        st = licensing.current_state()
        return {"enabled": licensing.enabled(), "status": st.status, "used": st.videos_used,
                "allowed": st.videos_allowed, "left": None if st.status == "active" else st.videos_left,
                "plan": st.plan, "expires_at": st.expires_at, "device_id": st.device_id, "message": st.message,
                "keys_ready": bool(licensing._public_key())}

    def license_activate(a):
        from . import licensing
        try:
            licensing.redeem(str(a.get("key", "")).strip())
        except licensing.LicenseError as e:
            raise ApiError(str(e))
        E.bus.emit("status", E.status())
        return license_info({})

    def focus(_a):
        if FOCUS:
            FOCUS()
        return True

    ROUTES = {
        ("POST", "/api/focus"): focus,
        ("GET", "/api/state"): state,
        ("POST", "/api/analyze"): analyze,
        ("POST", "/api/job/cancel"): job_cancel,
        ("POST", "/api/job/retry"): job_retry,
        ("POST", "/api/jobs/clear"): jobs_clear,
        ("GET", "/api/project"): project_get,
        ("POST", "/api/project/delete"): project_delete,
        ("POST", "/api/clip/edits"): clip_edits,
        ("POST", "/api/clip/export"): clip_export,
        ("POST", "/api/clip/frame"): clip_frame,
        ("POST", "/api/clip/proxy"): clip_proxy,
        ("POST", "/api/clip/audio"): clip_audio,
        ("POST", "/api/project/export"): export_all,
        ("GET", "/api/library"): library,
        ("POST", "/api/library/meta"): library_meta,
        ("POST", "/api/library/delete"): library_delete,
        ("POST", "/api/library/delete_many"): library_delete_many,
        ("POST", "/api/library/upload"): library_upload,
        ("GET", "/api/thumb/info"): thumb_info,
        ("POST", "/api/thumb/ref"): thumb_ref,
        ("POST", "/api/thumb/make"): thumb_make,
        ("POST", "/api/thumb/remove"): thumb_remove,
        ("POST", "/api/thumb/assets"): thumb_assets,
        ("POST", "/api/thumb/analyze"): thumb_analyze,
        ("POST", "/api/thumb/save"): thumb_save,
        ("POST", "/api/open"): open_any,
        ("GET", "/api/accounts"): accounts,
        ("POST", "/api/signin/start"): signin_start,
        ("POST", "/api/signin/open"): signin_open,
        ("POST", "/api/signin/finish"): signin_finish,
        ("POST", "/api/signin/cancel"): signin_cancel,
        ("POST", "/api/signin/label"): signin_label,
        ("POST", "/api/accounts/connect"): api_connect,
        ("POST", "/api/accounts/connect/cancel"): api_cancel,
        ("POST", "/api/accounts/enable"): account_enable,
        ("POST", "/api/accounts/remove"): account_remove,
        ("POST", "/api/accounts/open"): account_open,
        ("POST", "/api/accounts/proxy"): account_proxy,
        ("POST", "/api/accounts/proxy/test"): account_proxy_test,
        ("POST", "/api/signin/proxy"): signin_proxy,
        ("GET", "/api/queue"): queue_get,
        ("POST", "/api/queue/action"): queue_action,
        ("POST", "/api/queue/clear"): queue_clear,
        ("GET", "/api/music"): music_list,
        ("POST", "/api/music/search"): music_search,
        ("POST", "/api/music/download"): music_download,
        ("POST", "/api/music/open_page"): music_open_page,
        ("POST", "/api/music/generate"): music_generate,
        ("GET", "/api/music/moods"): music_moods,
        ("POST", "/api/music/star"): music_star,
        ("POST", "/api/music/remove"): music_remove,
        ("POST", "/api/music/import"): music_import,
        ("POST", "/api/music/source"): music_open_source,
        ("POST", "/api/music/tag"): music_tag,
        ("POST", "/api/settings"): settings_set,
        ("POST", "/api/settings/test-key"): test_key,
        ("POST", "/api/youtube/signout"): yt_signout,
        ("POST", "/api/youtube/import"): yt_import,
        ("POST", "/api/watch/check"): watch_check,
        ("GET", "/api/watch"): watch_state,
        ("POST", "/api/watch/forget"): watch_forget,
        ("GET", "/api/style-preview"): style_preview,
        ("POST", "/api/pick"): pick,
        ("GET", "/api/logs"): logs,
        ("GET", "/api/license"): license_info,
        ("POST", "/api/license/activate"): license_activate,
    }
    return H


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
