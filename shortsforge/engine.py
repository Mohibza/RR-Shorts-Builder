"""The app's engine for the new interface: job queues, background workers and events (no Qt).

Everything the interface does goes through here: analyse videos into projects, export clips, the upload queue,
channel auto-watch and the browser sign-ins. The interface listens to `bus` for live updates.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from . import __version__, cookies, licensing, projects
from .config import Settings
from .downloader import SourceItem, expand
from .utils import Cancelled, set_ffmpeg_override


# ================================================================ events
class Bus:
    """Fan-out of events to every open event stream (Server-Sent Events)."""

    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.logs: deque = deque(maxlen=500)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def emit(self, kind: str, data) -> None:
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((kind, data))
            except queue.Full:
                pass

    def log(self, msg: str) -> None:
        line = {"t": time.time(), "msg": str(msg)}
        self.logs.append(line)
        self.emit("log", line)


def _first_line(e: BaseException) -> str:
    return (str(e).strip().split("\n")[0] or type(e).__name__)[:300]


# ================================================================ engine
class Engine:
    def __init__(self):
        self.bus = Bus()
        self.jobs: dict[str, dict] = {}              # analyse jobs (one per video)
        self.exports: dict[str, dict] = {}           # export tasks (one per clip render)
        self._cancels: dict[str, threading.Event] = {}
        self._aq: "queue.Queue[Optional[str]]" = queue.Queue()
        self._eq: "queue.Queue[Optional[str]]" = queue.Queue()
        self._upload_kick = threading.Event()
        self._stop = threading.Event()
        self._signins: dict[str, dict] = {}
        self._watching = threading.Lock()
        self._tracking: set = set()
        self._last_prog: dict[str, float] = {}
        self.lock = threading.RLock()
        self.started = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.started:
            return
        self.started = True
        s = Settings.load()
        set_ffmpeg_override(s.ffmpeg_path)
        threading.Thread(target=self._analyze_loop, name="analyze", daemon=True).start()
        for i in range(self._export_workers(s)):
            threading.Thread(target=self._export_loop, name=f"export{i}", daemon=True).start()
        threading.Thread(target=self._upload_loop, name="upload", daemon=True).start()
        threading.Thread(target=self._watch_loop, name="watch", daemon=True).start()
        threading.Thread(target=self._startup, name="startup", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for ev in self._cancels.values():
            ev.set()
        self._aq.put(None)
        self._eq.put(None)
        self._upload_kick.set()

    @staticmethod
    def _export_workers(s: Settings) -> int:
        try:
            from .utils import pick_encoder
            gpu = pick_encoder(s.encoder) != "libx264"
        except Exception:
            gpu = False
        return 3 if gpu else (2 if (os.cpu_count() or 4) >= 8 else 1)

    def _startup(self) -> None:
        from . import fonts
        try:
            if fonts.missing_fonts():
                fonts.ensure_fonts(self.bus.log)
        except Exception:
            pass
        self.bus.emit("status", self.status())

    def busy(self) -> bool:
        return any(j["state"] in ("queued", "running") for j in self.jobs.values()) or \
            any(e["state"] in ("queued", "running") for e in self.exports.values())

    # ------------------------------------------------------------ status
    def status(self) -> dict:
        from .utils import ToolMissing, find_ffmpeg
        from . import fonts
        try:
            find_ffmpeg()
            ff = True
        except ToolMissing:
            ff = False
        except Exception:
            ff = False
        try:
            from . import uploadqueue
            waiting = sum(1 for j in uploadqueue.load() if j["status"] in ("waiting", "uploading"))
        except Exception:
            waiting = 0
        s = Settings.load()
        try:
            miss = len(fonts.missing_fonts())
        except Exception:
            miss = 0
        return {"version": __version__, "ffmpeg": ff, "fonts_missing": miss, "yt_login": cookies.has_login(),
                "running": sum(1 for j in self.jobs.values() if j["state"] in ("queued", "running")),
                "exporting": sum(1 for e in self.exports.values() if e["state"] in ("queued", "running")),
                "uploads_waiting": waiting, "watching": len(s.watch_channels or []) if s.watch_enabled else 0,
                "auto_upload": bool(s.auto_upload), "ai": s.clip_picker if (
                    (s.clip_picker == "gemini" and s.gemini_api_key) or
                    (s.clip_picker == "claude" and s.anthropic_api_key) or
                    (s.clip_picker == "openai" and getattr(s, "openai_api_key", ""))) else "offline",
                "license": licensing.enabled()}

    # ------------------------------------------------------------ analyse
    def add_sources(self, text: str, auto_export: Optional[bool] = None) -> dict:
        """Paste box: one or more links/paths (lines or spaces). Expands playlists/channels in the background."""
        srcs = [x.strip() for x in text.replace(",", "\n").split() if x.strip()]
        if not srcs:
            raise ValueError("Paste a YouTube link (video, playlist or channel) or choose a video file.")
        tmp = {"id": "x" + uuid.uuid4().hex[:8], "state": "expanding", "sources": srcs}
        self.bus.emit("expanding", tmp)

        def work():
            s = Settings.load()
            added = 0
            for src in srcs:
                try:
                    items = expand(src, s.cookies_browser)
                except Exception as e:
                    msg = _first_line(e)
                    self.bus.log(f"✖ {src}: {msg}")
                    self.bus.emit("toast", {"kind": "error", "text": msg, "login": cookies.needs_login(msg)})
                    continue
                for it in items:
                    self.enqueue(it, auto_export)
                    added += 1
            self.bus.emit("expanded", {"id": tmp["id"], "added": added})
        threading.Thread(target=work, daemon=True).start()
        return tmp

    def enqueue(self, item: SourceItem, auto_export: Optional[bool] = None) -> dict:
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "title": item.title, "source": item.source, "vid": item.vid, "is_local": item.is_local,
               "state": "queued", "stage": "Waiting", "frac": 0.0, "detail": "", "error": "", "login": False,
               "project": "", "created": time.time(), "auto_export": auto_export}
        with self.lock:
            self.jobs[jid] = job
            self._cancels[jid] = threading.Event()
        self._aq.put(jid)
        self.bus.emit("job", job)
        self.bus.emit("status", self.status())
        return job

    def cancel_job(self, jid: str) -> None:
        ev = self._cancels.get(jid)
        if ev:
            ev.set()
        j = self.jobs.get(jid)
        if j and j["state"] == "queued":
            j["state"] = "cancelled"
            self.bus.emit("job", j)
        e = self.exports.get(jid)
        if e and e["state"] == "queued":
            e["state"] = "cancelled"
            self.bus.emit("export", e)

    def retry_job(self, jid: str) -> Optional[dict]:
        j = self.jobs.get(jid)
        if not j:
            return None
        it = SourceItem(j["source"], j["title"], j["vid"], j["is_local"])
        with self.lock:
            self.jobs.pop(jid, None)
        self.bus.emit("job_removed", {"id": jid})
        return self.enqueue(it, j.get("auto_export"))

    def clear_jobs(self) -> None:
        with self.lock:
            for k in [k for k, j in self.jobs.items() if j["state"] in ("done", "failed", "cancelled")]:
                self.jobs.pop(k, None)
            for k in [k for k, e in self.exports.items() if e["state"] in ("done", "failed", "cancelled")]:
                self.exports.pop(k, None)
        self.bus.emit("jobs_cleared", {})

    def _progress(self, key: str, kind: str, obj: dict, stage: str, f: float, detail: str) -> None:
        obj["stage"], obj["frac"], obj["detail"] = stage, max(0.0, min(1.0, float(f))), detail
        now = time.time()
        if now - self._last_prog.get(key, 0) > 0.25 or f >= 1:
            self._last_prog[key] = now
            self.bus.emit(kind, obj)

    def _analyze_loop(self) -> None:
        from .pipeline import Pipeline
        while not self._stop.is_set():
            jid = self._aq.get()
            if jid is None:
                break
            job = self.jobs.get(jid)
            ev = self._cancels.get(jid)
            if not job or not ev or job["state"] == "cancelled":
                continue
            if ev.is_set():
                job["state"] = "cancelled"
                self.bus.emit("job", job)
                continue
            job["state"] = "running"
            self.bus.emit("job", job)
            s = Settings.load()
            item = SourceItem(job["source"], job["title"], job["vid"], job["is_local"])
            try:
                p = Pipeline(s, log=self.bus.log,
                             progress=lambda st, f, d, j=job: self._progress(j["id"], "job", j, st, f, d),
                             cancel=ev)
                proj = p.analyze(item)
                job.update(state="done", stage="Clips ready", frac=1.0, project=proj["id"],
                           detail=f"{len(proj['clips'])} clips")
                self.bus.emit("job", job)
                self.bus.emit("project", projects.summary(proj))
                self._track_async(proj["id"])
                auto = job.get("auto_export")
                if auto is None:
                    auto = getattr(s, "auto_export", True)
                if auto:
                    self.export(proj["id"], [c["id"] for c in proj["clips"]])
            except Cancelled:
                job.update(state="cancelled", stage="Cancelled")
                self.bus.log(f"✖ Cancelled: {item.title}")
                self.bus.emit("job", job)
            except licensing.LicenseError as e:
                job.update(state="failed", error=str(e))
                self.bus.log(f"✖ {item.title}: {e}")
                self.bus.emit("job", job)
                if getattr(e, "kind", "") == "blocked":
                    self.bus.emit("license", {"text": str(e)})
            except Exception as e:
                msg = _first_line(e)
                job.update(state="failed", error=msg, login=cookies.needs_login(msg))
                self.bus.log(f"✖ {item.title}: {e}\n{traceback.format_exc(limit=3)}")
                self.bus.emit("job", job)
            self.bus.emit("status", self.status())

    # ------------------------------------------------------------ face tracking for live previews
    def _track_async(self, pid: str) -> None:
        if pid in self._tracking:
            return
        self._tracking.add(pid)

        def work():
            from .pipeline import Pipeline
            try:
                proj = projects.load(pid)
                if not proj:
                    return
                p = Pipeline(Settings.load(), log=lambda m: None)
                for c in proj["clips"]:
                    if self._stop.is_set():
                        return
                    if c.get("tracks") is not None:
                        continue
                    try:
                        p.track_clip(proj, c)
                    except Exception as e:
                        c["tracks"], c["camera"], c["framing"] = [], [], "blur_fit"
                        self.bus.log(f"(face tracking skipped for a clip: {_first_line(e)})")
                    with projects._lock:
                        cur = projects.load(pid)
                        if not cur:
                            return
                        cc = projects.clip(cur, c["id"])
                        if cc is not None and cc.get("tracks") is None:
                            for k in ("tracks", "camera", "coverage", "framing"):
                                cc[k] = c.get(k)
                            projects.save(cur)
                    self.bus.emit("clip_camera", {"project": pid, "clip": c["id"], "camera": c.get("camera") or [],
                                                  "framing": c.get("framing"), "coverage": c.get("coverage", 0)})
            finally:
                self._tracking.discard(pid)
        threading.Thread(target=work, name="track", daemon=True).start()

    # ------------------------------------------------------------ export
    def export(self, pid: str, cids: list, edits: Optional[dict] = None) -> list[dict]:
        out = []
        proj = projects.load(pid)
        if not proj:
            raise ValueError("Project not found.")
        for cid in cids:
            c = projects.clip(proj, cid)
            if not c:
                continue
            if edits is not None and len(cids) == 1:
                projects.update_clip(pid, cid, edits=edits)
            # one pending export per clip: a newer request replaces a queued one
            for e in self.exports.values():
                if e["project"] == pid and e["clip"] == cid and e["state"] == "queued":
                    e["state"] = "cancelled"
                    self.bus.emit("export", e)
            eid = "e" + uuid.uuid4().hex[:9]
            title = ((c.get("edits") or {}).get("hook") or c["clip"].get("title") or "Clip")
            task = {"id": eid, "project": pid, "clip": cid, "title": title, "state": "queued", "stage": "Waiting",
                    "frac": 0.0, "detail": "", "error": "", "path": "", "thumb": "", "plan_file": "",
                    "created": time.time()}
            with self.lock:
                self.exports[eid] = task
                self._cancels[eid] = threading.Event()
            projects.update_clip(pid, cid, status="queued")
            self._eq.put(eid)
            self.bus.emit("export", task)
            out.append(task)
        self.bus.emit("status", self.status())
        return out

    def _export_loop(self) -> None:
        from .pipeline import Pipeline
        while not self._stop.is_set():
            eid = self._eq.get()
            if eid is None:
                self._eq.put(None)          # let the other export workers stop too
                break
            task = self.exports.get(eid)
            ev = self._cancels.get(eid)
            if not task or task["state"] != "queued" or not ev or ev.is_set():
                if task and task["state"] == "queued":
                    task["state"] = "cancelled"
                    self.bus.emit("export", task)
                continue
            task["state"], task["stage"] = "running", "Rendering"
            self.bus.emit("export", task)
            projects.update_clip(task["project"], task["clip"], status="rendering")
            s = Settings.load()
            try:
                proj = projects.load(task["project"])
                if not proj:
                    raise RuntimeError("The project was deleted.")
                p = Pipeline(s, log=self.bus.log, cancel=ev)
                res = p.export_clip(proj, task["clip"], None,
                                    lambda f, t=task: self._progress(t["id"], "export", t, "Rendering", f, ""))
                task.update(state="done", stage="Ready", frac=1.0, path=res.path, thumb=res.thumb,
                            plan_file=res.plan_file, title=res.title)
                self.bus.emit("export", task)
                self.bus.emit("library", {"changed": res.plan_file})
                self._auto_upload(res.plan_file, s)
            except Cancelled:
                task.update(state="cancelled", stage="Cancelled")
                projects.update_clip(task["project"], task["clip"], status="new")
                self.bus.emit("export", task)
            except Exception as e:
                task.update(state="failed", error=_first_line(e))
                projects.update_clip(task["project"], task["clip"], status="failed")
                self.bus.log(f"✖ Export failed: {e}\n{traceback.format_exc(limit=3)}")
                self.bus.emit("export", task)
            p2 = projects.load(task["project"])
            if p2:
                self.bus.emit("project", projects.summary(p2))
            self.bus.emit("status", self.status())

    # ------------------------------------------------------------ uploads
    def _auto_upload(self, plan_file: str, s: Settings) -> None:
        if not getattr(s, "auto_upload", False):
            return
        from . import publish, uploadqueue
        try:
            jobs = uploadqueue.schedule(plan_file, s)
        except Exception as e:
            self.bus.log(f"Auto-upload: couldn't schedule: {e}")
            return
        for j in jobs:
            self.bus.log(f"Auto-upload: “{j['title'][:50]}” → {j['account']} ({j['platform']}) at "
                         f"{time.strftime('%a %H:%M', time.localtime(j['due']))}")
        if not jobs and not any(publish.enabled_accounts(p) for p in s.upload_platforms or []):
            self.bus.log("Auto-upload is on, but no account is connected (Publish page).")
        self.bus.emit("queue", {})
        self._upload_kick.set()

    def upload_now(self, plan_file: str, platforms: Optional[list] = None) -> list[dict]:
        from . import publish, uploadqueue
        s = Settings.load()
        plats = platforms or [p for p in (s.upload_platforms or []) if publish.enabled_accounts(p)] or \
            [p for p in publish.PLATFORMS if publish.enabled_accounts(p)]
        if not plats:
            raise ValueError("Connect an account on the Publish page first.")
        jobs = uploadqueue.schedule(plan_file, s, plats)
        self.bus.emit("queue", {})
        self._upload_kick.set()
        return jobs

    def kick_uploads(self) -> None:
        self.bus.emit("queue", {})
        self._upload_kick.set()

    def _upload_loop(self) -> None:
        from . import uploadqueue
        try:
            uploadqueue.recover()
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                job = uploadqueue.take_due()
            except Exception:
                job = None
            if job:
                self.bus.emit("upload", {**job, "frac": 0.0})
                self.bus.emit("queue", {})
                s = Settings.load()
                res = uploadqueue.run_job(job, s, lambda f, j=job: self._upload_prog(j, f))
                j = res or job
                name = f"{j.get('platform', '')} · {j.get('account', '')}"
                if j.get("status") == "done":
                    self.bus.log(f"✔ Uploaded “{j.get('title', '')[:50]}” to {name}"
                                 + (f" ({j['note']})" if j.get("note") else ""))
                elif j.get("status") == "failed":
                    self.bus.log(f"✕ Upload to {name} failed: {j.get('error', '')}")
                elif j.get("error"):
                    self.bus.log(f"Upload to {name} will retry: {j.get('error', '')}")
                self.bus.emit("queue", {})
                self.bus.emit("status", self.status())
                continue
            self._upload_kick.wait(20)
            self._upload_kick.clear()

    def _upload_prog(self, job: dict, f: float) -> None:
        now = time.time()
        if now - self._last_prog.get("u" + job["id"], 0) > 0.4 or f >= 1:
            self._last_prog["u" + job["id"]] = now
            self.bus.emit("upload", {**job, "frac": f})

    # ------------------------------------------------------------ channel auto-watch
    def _watch_loop(self) -> None:
        self._stop.wait(20)
        while not self._stop.is_set():
            try:
                self.watch_tick()
            except Exception as e:
                self.bus.log(f"Auto-watch error: {e}")
            self._stop.wait(60)

    def watch_tick(self, force: bool = False) -> bool:
        from . import autowatch
        s = Settings.load()
        if not s.watch_channels or (not s.watch_enabled and not force):
            return False
        if not force and time.time() - autowatch.last_check() < max(15, s.watch_interval_min) * 60:
            return False
        if not self._watching.acquire(blocking=False):
            return False

        def work():
            try:
                items = autowatch.check(s.watch_channels, s.watch_first_n, s.watch_per_check, s.cookies_browser,
                                        self.bus.log)
                for it in items:
                    self.enqueue(it, None)
                if items:
                    self.bus.log(f"Auto-watch: queued {len(items)} new video(s)")
                self.bus.emit("watch", {"last": autowatch.last_check(), "found": len(items)})
            except Exception as e:
                self.bus.log(f"Auto-watch error: {e}")
            finally:
                self._watching.release()
        threading.Thread(target=work, daemon=True).start()
        return True

    # ------------------------------------------------------------ sign-ins (browser windows)
    def signin_start(self, platform: str, acc_id: str = "", label: str = "") -> dict:
        """platform: youtube|tiktok|facebook|instagram (upload accounts) or 'ytdl' (YouTube download login)."""
        from . import publish, webupload
        sid = uuid.uuid4().hex[:10]
        ses = {"id": sid, "platform": platform, "state": "checking", "msg": "Checking whether you're already signed in…",
               "label": label, "key": "", "new": True, "ok": False}
        if platform == "ytdl":
            ses.update(state="ready", msg="Click “Open sign-in window”, sign in to YouTube, then click Finish.")
            ses["ok"] = cookies.has_login()
            if ses["ok"]:
                ses.update(state="ok", msg="✓ Signed in to YouTube for downloads.")
            self._signins[sid] = ses
            self.bus.emit("signin", self._pub(ses))
            return self._pub(ses)
        if platform not in webupload.LOGIN:
            raise ValueError("Unknown platform.")
        accs = publish.load_accounts().get(platform, [])
        acc = next((a for a in accs if a.get("id") == acc_id), None) if acc_id else None
        name = webupload.NICE[platform]
        if acc:
            ses.update(key=acc.get("profile", ""), new=False, label=label or acc.get("name", ""))
        elif platform == "youtube" and not any(a.get("id") == "browser" for a in accs):
            ses.update(key="", label=label or "YouTube (main)")
        else:
            ses.update(key=webupload.new_profile_key(platform),
                       label=label or f"{name} {sum(1 for a in accs if a.get('mode') == 'browser') + 1}")
        self._signins[sid] = ses
        self.bus.emit("signin", self._pub(ses))
        self._signin_check(ses)
        return self._pub(ses)

    @staticmethod
    def _pub(ses: dict) -> dict:
        return {k: v for k, v in ses.items() if k not in ("proc",)}

    def _profile(self, ses: dict):
        from . import webupload
        return webupload.PROFILES / ses["key"] if ses["key"] else None

    def _signin_check(self, ses: dict) -> None:
        def work():
            from . import browser_login, webupload
            ses["state"], ses["msg"] = "checking", "Checking your login…"
            self.bus.emit("signin", self._pub(ses))
            try:
                if ses["platform"] == "ytdl":
                    n = browser_login.harvest()
                    ok = n > 0 and cookies.has_login()
                else:
                    ok = webupload.check_login(ses["platform"], self._profile(ses))
                err = ""
            except Exception as e:
                ok, err = False, _first_line(e)
            if ok:
                if ses["platform"] != "ytdl":
                    webupload.register(ses["platform"], ses["key"], ses.get("label", ""))
                    self.bus.emit("accounts", {})
                ses.update(ok=True, state="ok", msg="✓ Signed in. The app will use this login from now on.")
            elif err:
                ses.update(state="error", msg=err)
            else:
                ses.update(state="ready", msg="Not signed in yet. Click “Open sign-in window”, log in, then click Finish.")
            self.bus.emit("signin", self._pub(ses))
            self.bus.emit("status", self.status())
        threading.Thread(target=work, daemon=True).start()

    def signin_open(self, sid: str) -> dict:
        from . import browser_login, webupload
        ses = self._signins[sid]
        if ses["platform"] == "ytdl":
            proc = browser_login.open_sign_in()
        else:
            proc = webupload.open_sign_in(ses["platform"], self._profile(ses))
        ses["proc"] = proc
        ses.update(state="open", msg="Log in in the browser window, then click Finish (or just close that window).")
        self.bus.emit("signin", self._pub(ses))

        def watch():
            while proc.poll() is None and not self._stop.is_set():
                time.sleep(0.8)
            if ses.get("proc") is proc and ses["state"] == "open":
                ses["proc"] = None
                self._signin_check(ses)
        threading.Thread(target=watch, daemon=True).start()
        return self._pub(ses)

    def signin_finish(self, sid: str) -> dict:
        ses = self._signins[sid]
        proc = ses.get("proc")
        if proc and proc.poll() is None:
            ses.update(state="closing", msg="Closing the browser and saving your login…")
            self.bus.emit("signin", self._pub(ses))
            try:
                if os.name == "nt":        # polite close so the browser writes the login to disk
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T"], capture_output=True,
                                   creationflags=0x08000000)
                else:
                    proc.terminate()
            except Exception:
                pass

            def force():
                time.sleep(9)
                if proc.poll() is None:
                    proc.kill()
            threading.Thread(target=force, daemon=True).start()
        else:
            self._signin_check(ses)
        return self._pub(ses)

    def signin_label(self, sid: str, label: str) -> dict:
        from . import webupload
        ses = self._signins[sid]
        ses["label"] = label.strip() or ses.get("label", "")
        if ses["ok"] and ses["platform"] != "ytdl":
            webupload.register(ses["platform"], ses["key"], ses["label"])
            self.bus.emit("accounts", {})
        return self._pub(ses)

    def signin_cancel(self, sid: str) -> None:
        from . import webupload
        ses = self._signins.pop(sid, None)
        if not ses:
            return
        proc = ses.get("proc")
        ses["state"] = "cancelled"
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        if ses["platform"] != "ytdl" and ses["new"] and ses["key"] and not ses["ok"]:
            webupload.remove_profile({"profile": ses["key"]})

    # ------------------------------------------------------------ official-API connect (developer keys)
    def api_connect(self, platform: str) -> None:
        from . import publish

        def work():
            s = Settings.load()
            self.bus.emit("connect", {"platform": platform, "state": "open",
                                      "msg": "Approve access in the browser tab that just opened…"})
            try:
                publish.CANCEL.clear()
                res = publish.connect(platform, s)
                n = len(res) if isinstance(res, list) else 1
                self.bus.emit("connect", {"platform": platform, "state": "ok", "msg": f"✓ Connected {n} account(s)."})
            except Exception as e:
                self.bus.emit("connect", {"platform": platform, "state": "error", "msg": _first_line(e)})
            self.bus.emit("accounts", {})
        threading.Thread(target=work, daemon=True).start()


def accounts_view() -> dict:
    """Connected accounts without tokens (safe to send to the interface)."""
    from . import publish
    out = {}
    for p, lst in publish.load_accounts().items():
        out[p] = [{"id": a.get("id"), "name": a.get("name", ""), "mode": a.get("mode", "api"),
                   "enabled": a.get("enabled", True), "profile": bool(a.get("profile"))} for a in lst]
    return out
