"""Upload queue: every finished Short gets a slot per connected account, spaced by a random gap.

Rules (all set on the Publish page):
  * random gap between uploads to the same account: upload_gap_min … upload_gap_max minutes
  * at most upload_daily_cap uploads per account per day (extra ones roll to the next day)
  * no uploads in quiet hours (e.g. 01:00–08:00); they move to the end of the quiet window
  * temporary problems (network, rate limit, quota) retry automatically with growing waits
The queue lives in upload_queue.json, so nothing is lost when the app closes; overdue items go out
(still spaced) the next time the app is running.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import publish
from .config import data_dir

QUEUE_FILE = data_dir() / "upload_queue.json"
_lock = threading.RLock()
MAX_ATTEMPTS = 6


def load() -> list[dict]:
    with _lock:
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []


def save(q: list[dict]) -> None:
    with _lock:
        tmp = QUEUE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(QUEUE_FILE)


def _quiet(t: float, s) -> Optional[float]:
    """If t falls in quiet hours, return the time quiet hours end (plus a little randomness)."""
    a, b = int(getattr(s, "upload_quiet_start", 1)), int(getattr(s, "upload_quiet_end", 8))
    if a == b:
        return None
    d = dt.datetime.fromtimestamp(t)
    h = d.hour
    inside = (a <= h < b) if a < b else (h >= a or h < b)
    if not inside:
        return None
    end = d.replace(hour=b, minute=0, second=0, microsecond=0)
    if end <= d:
        end += dt.timedelta(days=1)
    return end.timestamp() + random.uniform(5, 50) * 60


def _day(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def next_slot(q: list[dict], platform: str, acc_id: str, s, now: Optional[float] = None) -> float:
    now = now or time.time()
    gmin = max(1, int(getattr(s, "upload_gap_min", 45)))
    gmax = max(gmin, int(getattr(s, "upload_gap_max", 180)))
    cap = max(1, int(getattr(s, "upload_daily_cap", 6)))
    mine = [j for j in q if j["platform"] == platform and j["account_id"] == acc_id and j["status"] != "failed"]
    last = max([j["due"] for j in mine if j["status"] in ("waiting", "uploading")] +
               [j.get("done_at", j["due"]) for j in mine if j["status"] == "done"] + [0])
    if last and now - last < gmax * 60 or last > now:
        t = max(now, last) + random.uniform(gmin, gmax) * 60
    else:
        t = now + random.uniform(2, 12) * 60            # first one: a few minutes after the build
    for _ in range(30):                                   # respect quiet hours and the daily cap
        qe = _quiet(t, s)
        if qe:
            t = qe
            continue
        same_day = sum(1 for j in mine if _day(j["due"]) == _day(t))
        if same_day >= cap:
            nxt = dt.datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0) + dt.timedelta(days=1)
            t = nxt.timestamp() + random.uniform(0, 30) * 60
            continue
        break
    return t


def schedule(plan_file: str, s, platforms: Optional[list] = None, now: Optional[float] = None,
             direct: bool = False) -> list[dict]:
    """Queue one Short for every enabled account of the chosen platforms. Returns the new jobs.
    direct=True: post right away (no random gap, quiet hours or daily cap); each account still posts one
    Short at a time, in the order they were queued."""
    data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    platforms = platforms if platforms is not None else list(getattr(s, "upload_platforms", []) or [])
    new = []
    with _lock:
        q = load()
        for p in platforms:
            if p not in publish.PLATFORMS:
                continue
            for acc in publish.enabled_accounts(p):
                if any(j["plan_file"] == plan_file and j["platform"] == p and j["account_id"] == acc["id"]
                       and j["status"] in ("waiting", "uploading", "done") for j in q):
                    continue                              # never post the same Short twice to one account
                job = {"id": uuid.uuid4().hex[:12], "plan_file": plan_file, "video": data["output"],
                       "title": (data.get("meta") or {}).get("title", ""), "platform": p,
                       "account_id": acc["id"], "account": acc.get("name", ""), "status": "waiting",
                       "due": (time.time() + 0.001 * len(q)) if direct else next_slot(q, p, acc["id"], s, now),
                       "direct": direct, "attempts": 0, "error": "", "url": "",
                       "created": time.time()}
                q.append(job)
                new.append(job)
        save(q)
    return new


def meta_for(plan_file: str) -> dict:
    data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    m = data.get("meta") or {}
    return {"title": m.get("title", ""), "description": m.get("description", ""), "tags": m.get("tags") or [],
            "hashtags": m.get("hashtags") or ["#shorts"]}


def _lane(j: dict, cache: dict) -> str:
    k = (j["platform"], j["account_id"])
    if k not in cache:
        try:
            cache[k] = publish.lane(*k)
        except Exception:
            cache[k] = f"api:{k[0]}:{k[1]}"
    return cache[k]


def take_due(now: Optional[float] = None, busy: Optional[set] = None) -> Optional[dict]:
    """Claim the next job that is due (marks it 'uploading'), skipping lanes that are busy right now
    and accounts that are switched off. The job gets a 'lane' key (see publish.lane)."""
    now = now or time.time()
    busy = busy or set()
    with _lock:
        q = load()
        due = sorted((j for j in q if j["status"] == "waiting" and j["due"] <= now), key=lambda j: j["due"])
        if not due:
            return None
        try:
            off = {(p, a.get("id")) for p, lst in publish.load_accounts().items() for a in lst
                   if not a.get("enabled", True)}
        except Exception:
            off = set()
        cache: dict = {}
        for j in due:
            if (j["platform"], j["account_id"]) in off:
                continue
            ln = _lane(j, cache)
            if ln in busy:
                continue
            j.update(status="uploading", started=now, lane=ln)
            save(q)
            return dict(j)
        return None


def next_due() -> Optional[float]:
    """When the next waiting job is due (for the status line)."""
    ts = [j["due"] for j in load() if j["status"] == "waiting"]
    return min(ts) if ts else None


def finish(job_id: str, ok: bool, result: Optional[dict] = None, error: str = "", retry: bool = False,
           wait: float = 0) -> dict:
    with _lock:
        q = load()
        for j in q:
            if j["id"] != job_id:
                continue
            j.pop("lane", None)
            if ok:
                j.update(status="done", done_at=time.time(), url=(result or {}).get("url", ""),
                         note=(result or {}).get("note", ""), error="", waits=0)
            elif wait and retry and j.get("waits", 0) < 60:
                # "busy right now" (browser window open, another upload on the same account): try again soon
                # without using up one of the real attempts
                j.update(status="waiting", due=time.time() + wait, error=error, waits=j.get("waits", 0) + 1)
            else:
                j["attempts"] = j.get("attempts", 0) + 1
                j["error"] = error
                if retry and j["attempts"] < MAX_ATTEMPTS:
                    wait = min(6 * 3600, 600 * (2 ** (j["attempts"] - 1)))
                    if "quota" in error.lower():
                        wait = max(wait, 6 * 3600)
                    j.update(status="waiting", due=time.time() + wait)
                else:
                    j["status"] = "failed"
            save(q)
            return j
    return {}


def recover() -> None:
    """After a crash/close mid-upload, put 'uploading' jobs back in line."""
    with _lock:
        q = load()
        changed = False
        for j in q:
            if j["status"] == "uploading":
                j["status"] = "waiting"
                j["due"] = time.time() + 60
                j.pop("lane", None)
                changed = True
        if changed:
            save(q)


def set_status(job_id: str, **kw) -> None:
    with _lock:
        q = load()
        for j in q:
            if j["id"] == job_id:
                j.update(kw)
        save(q)


def remove(job_id: str) -> None:
    with _lock:
        save([j for j in load() if j["id"] != job_id])


def retry_failed() -> int:
    """Put every failed job back in line (spaced a few minutes apart per account)."""
    with _lock:
        q = load()
        n, per = 0, {}
        now = time.time()
        for j in sorted(q, key=lambda x: x.get("due", 0)):
            if j["status"] == "failed":
                k = (j["platform"], j["account_id"])
                j.update(status="waiting", attempts=0, waits=0, error="", due=now + 5 + per.get(k, 0))
                per[k] = per.get(k, 0) + random.uniform(3, 8) * 60
                n += 1
        save(q)
        return n


def post_all_now() -> int:
    """Make every waiting job due now (each account still posts one at a time)."""
    with _lock:
        q = load()
        n = 0
        for j in q:
            if j["status"] == "waiting":
                j["due"] = time.time()
                n += 1
        save(q)
        return n


def drop_plan(plan_file: str) -> list[dict]:
    """A Short was deleted: take its not-yet-posted jobs out of the queue. Returns jobs still posting."""
    with _lock:
        q = load()
        keep, posting = [], []
        for j in q:
            if j["plan_file"] == plan_file and j["status"] in ("waiting", "failed"):
                continue
            if j["plan_file"] == plan_file and j["status"] == "uploading":
                posting.append(j)
            keep.append(j)
        save(keep)
        return posting


def clear_failed() -> None:
    with _lock:
        save([j for j in load() if j["status"] != "failed"])


def clear_finished() -> None:
    with _lock:
        save([j for j in load() if j["status"] not in ("done",)])


def run_job(job: dict, s, progress=lambda f: None) -> dict:
    """Upload one claimed job and record the outcome. Never raises."""
    try:
        res = publish.upload(job["platform"], job["account_id"], job["video"], meta_for(job["plan_file"]), s,
                             progress)
        return finish(job["id"], True, res)
    except publish.PublishError as e:
        return finish(job["id"], False, error=str(e), retry=e.retry, wait=getattr(e, "wait", 0))
    except FileNotFoundError:
        return finish(job["id"], False, error="The Short's files were moved or deleted.", retry=False)
    except Exception as e:  # unexpected: keep the queue alive, retry a few times
        return finish(job["id"], False, error=f"{type(e).__name__}: {e}", retry=True)
