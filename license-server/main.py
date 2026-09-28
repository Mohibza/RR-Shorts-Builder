"""RR Shorts Builder — license & trial server.

Single job: stop a trial reset from being as easy as "delete a folder and reinstall", and give the
app a paid license key to check later. The server is the source of truth for how many videos a
device has used; the app only ever caches a *signed* copy of that state locally.

Endpoints
---------
POST /v1/activate   first run: register (or re-fetch) a device's trial state by hardware fingerprint
POST /v1/consume    the app calls this right before it starts processing a video
POST /v1/validate   cheap heartbeat / refresh of the signed token, no consumption
POST /v1/redeem     turn a paid license key into an active license for this device
POST /v1/admin/keys create paid license keys (protected by ADMIN_TOKEN) — called by your payment
                     webhook later, or by hand for now

Storage: SQLite file (LICENSE_DB env var, default license.db). Good enough for the traffic a trial
gate gets. If you deploy on a host with an ephemeral filesystem (most free tiers), attach a small
persistent volume and point LICENSE_DB at a path inside it — otherwise the database resets on every
redeploy and every device's trial "resets" for free. This is called out again in the README.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

APP_NAME = "RR Shorts Builder"
DB_PATH = os.environ.get("LICENSE_DB", "license.db")
SECRET_KEY = os.environ.get("LICENSE_SECRET", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
TRIAL_VIDEO_LIMIT = int(os.environ.get("TRIAL_VIDEO_LIMIT", "3"))
TOKEN_TTL_DAYS = int(os.environ.get("TOKEN_TTL_DAYS", "7"))          # how long a cached token is trusted offline
# Optional allowlist of trial keys baked into installers. Leave unset to accept any non-empty trial
# key (the real protection is the per-device counter below, not key secrecy) — set this if you ever
# need to kill a leaked/shared build without touching every other installer.
ALLOWED_TRIAL_KEYS = {k.strip() for k in os.environ.get("ALLOWED_TRIAL_KEYS", "").split(",") if k.strip()}

if not SECRET_KEY:
    raise RuntimeError("Set LICENSE_SECRET (a long random string) before starting the server.")

app = FastAPI(title=f"{APP_NAME} License Server")


# ------------------------------------------------------------------------------------------- storage
@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS devices (
            fingerprint     TEXT PRIMARY KEY,
            trial_key       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'trial',   -- trial | active | expired | banned
            videos_used     INTEGER NOT NULL DEFAULT 0,
            videos_allowed  INTEGER NOT NULL DEFAULT 3,
            license_key     TEXT,
            plan            TEXT,
            expires_at      REAL,
            app_version     TEXT,
            created_at      REAL NOT NULL,
            last_seen_at    REAL NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS license_keys (
            license_key       TEXT PRIMARY KEY,
            plan              TEXT NOT NULL,
            max_activations   INTEGER NOT NULL DEFAULT 1,
            activations_used  INTEGER NOT NULL DEFAULT 0,
            expires_at        REAL,
            created_at        REAL NOT NULL,
            note              TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS activations (
            license_key  TEXT NOT NULL,
            fingerprint  TEXT NOT NULL,
            activated_at REAL NOT NULL,
            PRIMARY KEY (license_key, fingerprint)
        )""")


init_db()


# ------------------------------------------------------------------------------------------- token signing
def sign_token(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    sig = hmac.new(SECRET_KEY.encode(), body, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return (body + b"." + sig_b64).decode()


def verify_token(token: str) -> Optional[dict]:
    try:
        body_b64, sig_b64 = token.encode().split(b".")
        expected = base64.urlsafe_b64encode(
            hmac.new(SECRET_KEY.encode(), body_b64, hashlib.sha256).digest()).rstrip(b"=")
        if not hmac.compare_digest(expected, sig_b64):
            return None
        pad = b"=" * (-len(body_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(body_b64 + pad))
    except Exception:
        return None


def _state_token(row: sqlite3.Row) -> str:
    return sign_token({
        "fp": row["fingerprint"], "status": row["status"], "used": row["videos_used"],
        "allowed": row["videos_allowed"], "plan": row["plan"], "expires_at": row["expires_at"],
        "issued_at": time.time(), "ttl_days": TOKEN_TTL_DAYS,
    })


def _state_dict(row: sqlite3.Row, token: str) -> dict:
    return {
        "status": row["status"], "videos_used": row["videos_used"], "videos_allowed": row["videos_allowed"],
        "plan": row["plan"], "expires_at": row["expires_at"], "token": token,
    }


# ------------------------------------------------------------------------------------------- models
class ActivateReq(BaseModel):
    fingerprint: str
    trial_key: str
    app_version: str = ""


class ConsumeReq(BaseModel):
    fingerprint: str
    token: str
    count: int = 1


class ValidateReq(BaseModel):
    fingerprint: str
    token: str


class RedeemReq(BaseModel):
    fingerprint: str
    license_key: str


class CreateKeysReq(BaseModel):
    plan: str
    count: int = 1
    max_activations: int = 1
    expires_in_days: Optional[int] = None
    note: str = ""


# ------------------------------------------------------------------------------------------- endpoints
@app.post("/v1/activate")
def activate(req: ActivateReq):
    if ALLOWED_TRIAL_KEYS and req.trial_key not in ALLOWED_TRIAL_KEYS:
        raise HTTPException(403, "This build's trial key is no longer valid. Please download the latest installer.")
    now = time.time()
    with db() as con:
        row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO devices (fingerprint, trial_key, status, videos_used, videos_allowed, "
                "app_version, created_at, last_seen_at) VALUES (?, ?, 'trial', 0, ?, ?, ?, ?)",
                (req.fingerprint, req.trial_key, TRIAL_VIDEO_LIMIT, req.app_version, now, now))
            row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
        else:
            con.execute("UPDATE devices SET last_seen_at = ?, app_version = ? WHERE fingerprint = ?",
                        (now, req.app_version, req.fingerprint))
        if row["status"] == "banned":
            raise HTTPException(403, "This device is blocked. Contact support if you think that's a mistake.")
    return _state_dict(row, _state_token(row))


@app.post("/v1/consume")
def consume(req: ConsumeReq):
    claim = verify_token(req.token)
    if not claim or claim.get("fp") != req.fingerprint:
        raise HTTPException(401, "Invalid or tampered license token. Reconnect to re-activate.")
    with db() as con:
        row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
        if row is None:
            raise HTTPException(404, "Device not found. Reconnect to re-activate.")
        if row["status"] == "banned":
            raise HTTPException(403, "This device is blocked.")
        if row["status"] == "active":
            if row["expires_at"] and row["expires_at"] < time.time():
                con.execute("UPDATE devices SET status = 'expired' WHERE fingerprint = ?", (req.fingerprint,))
                row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
            else:
                con.execute("UPDATE devices SET last_seen_at = ? WHERE fingerprint = ?", (time.time(), req.fingerprint))
                return _state_dict(row, _state_token(row))
        if row["status"] in ("trial", "expired"):
            if row["status"] == "expired" or row["videos_used"] + req.count > row["videos_allowed"]:
                raise HTTPException(402, "Trial videos used up. Buy a license to keep making Shorts.")
            con.execute("UPDATE devices SET videos_used = videos_used + ?, last_seen_at = ? WHERE fingerprint = ?",
                        (req.count, time.time(), req.fingerprint))
            row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
    return _state_dict(row, _state_token(row))


@app.post("/v1/validate")
def validate(req: ValidateReq):
    claim = verify_token(req.token)
    if not claim or claim.get("fp") != req.fingerprint:
        raise HTTPException(401, "Invalid or tampered license token.")
    with db() as con:
        row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
        if row is None:
            raise HTTPException(404, "Device not found. Reconnect to re-activate.")
        con.execute("UPDATE devices SET last_seen_at = ? WHERE fingerprint = ?", (time.time(), req.fingerprint))
    return _state_dict(row, _state_token(row))


@app.post("/v1/redeem")
def redeem(req: RedeemReq):
    now = time.time()
    with db() as con:
        key = con.execute("SELECT * FROM license_keys WHERE license_key = ?", (req.license_key,)).fetchone()
        if key is None:
            raise HTTPException(404, "That license key isn't recognised. Check for typos.")
        if key["expires_at"] and key["expires_at"] < now:
            raise HTTPException(410, "That license key has expired.")
        already = con.execute(
            "SELECT 1 FROM activations WHERE license_key = ? AND fingerprint = ?",
            (req.license_key, req.fingerprint)).fetchone()
        if not already:
            if key["activations_used"] >= key["max_activations"]:
                raise HTTPException(409, "This license key is already activated on its device limit. "
                                          "Deactivate one elsewhere, or buy another seat.")
            con.execute("INSERT INTO activations (license_key, fingerprint, activated_at) VALUES (?, ?, ?)",
                        (req.license_key, req.fingerprint, now))
            con.execute("UPDATE license_keys SET activations_used = activations_used + 1 WHERE license_key = ?",
                        (req.license_key,))
        row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO devices (fingerprint, trial_key, status, videos_used, videos_allowed, license_key, "
                "plan, expires_at, created_at, last_seen_at) VALUES (?, '', 'active', 0, 0, ?, ?, ?, ?, ?)",
                (req.fingerprint, req.license_key, key["plan"], key["expires_at"], now, now))
        else:
            con.execute(
                "UPDATE devices SET status = 'active', license_key = ?, plan = ?, expires_at = ?, "
                "last_seen_at = ? WHERE fingerprint = ?",
                (req.license_key, key["plan"], key["expires_at"], now, req.fingerprint))
        row = con.execute("SELECT * FROM devices WHERE fingerprint = ?", (req.fingerprint,)).fetchone()
    return _state_dict(row, _state_token(row))


# ------------------------------------------------------------------------------------------- admin (payment webhook calls this later)
@app.post("/v1/admin/keys")
def create_keys(req: CreateKeysReq, x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Bad admin token.")
    now = time.time()
    expires_at = now + req.expires_in_days * 86400 if req.expires_in_days else None
    keys = []
    with db() as con:
        for _ in range(max(1, req.count)):
            k = "RRSF-" + secrets.token_hex(8).upper()
            con.execute(
                "INSERT INTO license_keys (license_key, plan, max_activations, activations_used, expires_at, "
                "created_at, note) VALUES (?, ?, ?, 0, ?, ?, ?)",
                (k, req.plan, req.max_activations, expires_at, now, req.note))
            keys.append(k)
    return {"keys": keys}


@app.get("/v1/health")
def health():
    return {"ok": True, "app": APP_NAME}
