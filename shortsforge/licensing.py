"""Trial & license gate.

Three video Shorts on the house, then a license key. The server (license-server/) is the source of
truth for how many videos a *device* has used — identified by a hardware fingerprint, not by this
app's own settings folder — so deleting %APPDATA%\\RRShortsBuilder or reinstalling does not reset the
trial. This module only keeps a local, best-effort cached copy of the last state the server gave us,
for two reasons: so the app can show "2 of 3 used" instantly without a network round trip, and so a
paid, already-activated user can keep working through a short internet outage.

Call `gate()` once, right before a video starts processing (pipeline.py does this). It either returns
quietly (go ahead) or raises LicenseError with a message fit to show the user.

Nothing here is unbeatable — a determined person can always patch a local .exe. The goal is to stop
the trivial case (reinstall, or hand-edit a JSON file) from being a free unlimited trial, which is
what "genuine" means for a small paid product, not enterprise DRM.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import data_dir

# Set this to your deployed server once license-server/ is live (see license-server/README.md).
# Can be overridden without rebuilding by setting the RRSF_LICENSE_SERVER environment variable.
LICENSE_SERVER_URL = os.environ.get("RRSF_LICENSE_SERVER", "https://license.rrshortsbuilder.com").rstrip("/")

# Baked into this build. Every copy of a given installer ships the same trial key; the server's
# per-device counter is what actually stops trial abuse, not this key's secrecy (see the server's
# ALLOWED_TRIAL_KEYS option if you ever need to kill a leaked build).
TRIAL_KEY = "RRSF-TRIAL-2026"

STATE_FILE = data_dir() / "license.dat"
REQUEST_TIMEOUT = 10


def enabled() -> bool:
    """The trial/license check runs in installer builds (the .exe you give to others) or when forced with
    RRSF_LICENSE_ENFORCE=1. Running from the project folder (run.bat, your own copy) skips it, so your own
    work never stops while the license server isn't deployed yet."""
    import sys
    force = os.environ.get("RRSF_LICENSE_ENFORCE", "")
    if force in ("0", "1"):
        return force == "1"
    return bool(getattr(sys, "frozen", False))
OFFLINE_GRACE_DAYS = 3   # an already-activated PAID device can work this long without reaching the server


class LicenseError(RuntimeError):
    """Raised by gate() when a video should NOT be processed. .kind tells the UI what to offer."""

    def __init__(self, message: str, kind: str = "blocked"):
        super().__init__(message)
        self.kind = kind   # "blocked" (buy/activate) | "offline" (can't verify right now)


@dataclass
class LicenseState:
    status: str = "unknown"          # trial | active | expired | banned | unknown
    videos_used: int = 0
    videos_allowed: int = 3
    plan: Optional[str] = None
    expires_at: Optional[float] = None
    token: str = ""
    fingerprint: str = ""
    cached_at: float = 0.0

    @property
    def videos_left(self) -> int:
        if self.status == "active":
            return 10 ** 9
        return max(0, self.videos_allowed - self.videos_used)


# ------------------------------------------------------------------------------------------- fingerprint
def _windows_machine_guid() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
            return winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        return None


def _wmic_uuid() -> Optional[str]:
    try:
        out = subprocess.check_output(["wmic", "csproduct", "get", "uuid"], timeout=5,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [l.strip() for l in out.decode(errors="ignore").splitlines() if l.strip()]
        return lines[1] if len(lines) > 1 and lines[1].upper() != "UUID" else None
    except Exception:
        return None


def fingerprint() -> str:
    """A stable per-PC id. Prefers the Windows install's own MachineGuid (survives reinstalling this
    app; changes only on a Windows reinstall), falls back to the motherboard UUID, then to a random
    id persisted in the app's settings folder (dev machines / non-Windows)."""
    raw = _windows_machine_guid() or _wmic_uuid()
    if not raw:
        marker = data_dir() / ".device_id"
        try:
            raw = marker.read_text().strip()
        except OSError:
            import secrets
            raw = secrets.token_hex(16)
            try:
                marker.write_text(raw, encoding="utf-8")
            except OSError:
                pass
    salted = f"rrsf::{raw}::{platform.node()}"
    return hashlib.sha256(salted.encode()).hexdigest()[:32]


# ------------------------------------------------------------------------------------------- local cache
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI, tied to this Windows user+machine — copying the file elsewhere won't decrypt.
    No-op passthrough off Windows (dev/testing only; the real product targets Windows)."""
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not encrypt/decrypt the license cache")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _load_cached() -> Optional[LicenseState]:
    try:
        raw = STATE_FILE.read_bytes()
        data = json.loads(_dpapi(raw, False).decode("utf-8"))
        return LicenseState(**data)
    except Exception:
        return None


def _save_cached(state: LicenseState) -> None:
    try:
        state.cached_at = time.time()
        STATE_FILE.write_bytes(_dpapi(json.dumps(asdict(state)).encode("utf-8"), True))
    except OSError:
        pass  # caching is best-effort; the server call still went through


# ------------------------------------------------------------------------------------------- server calls
def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        LICENSE_SERVER_URL + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", str(e))
        except Exception:
            detail = str(e)
        raise LicenseError(str(detail), kind="blocked" if e.code in (402, 403, 409, 410) else "offline") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LicenseError(f"Can't reach the license server ({e}).", kind="offline") from e


def _state_from(resp: dict, fp: str) -> LicenseState:
    return LicenseState(status=resp["status"], videos_used=resp["videos_used"], videos_allowed=resp["videos_allowed"],
                        plan=resp.get("plan"), expires_at=resp.get("expires_at"), token=resp["token"], fingerprint=fp)


def activate() -> LicenseState:
    """First run (or whenever there's no usable local cache): register/refetch this device's state."""
    from . import __version__
    fp = fingerprint()
    resp = _post("/v1/activate", {"fingerprint": fp, "trial_key": TRIAL_KEY, "app_version": __version__})
    state = _state_from(resp, fp)
    _save_cached(state)
    return state


def redeem(license_key: str) -> LicenseState:
    """User pastes a paid key (Settings -> License). Raises LicenseError with a user-facing message on failure."""
    fp = fingerprint()
    resp = _post("/v1/redeem", {"fingerprint": fp, "license_key": license_key.strip()})
    state = _state_from(resp, fp)
    _save_cached(state)
    return state


def current_state() -> LicenseState:
    """For the UI: what we currently believe, without talking to the server. Call gate()/refresh() first
    if you need it to be authoritative."""
    return _load_cached() or LicenseState()


def refresh() -> LicenseState:
    """Heartbeat: re-sync the cached state with the server without consuming a video. Safe to call on
    app startup / Settings page open. Falls back to the cache on a network error."""
    cached = _load_cached()
    fp = cached.fingerprint if cached and cached.fingerprint else fingerprint()
    if not cached or not cached.token:
        return activate()
    try:
        resp = _post("/v1/validate", {"fingerprint": fp, "token": cached.token})
    except LicenseError:
        return cached
    state = _state_from(resp, fp)
    _save_cached(state)
    return state


def gate() -> LicenseState:
    """Call this right before processing a video. Returns the state if allowed; raises LicenseError if not.

    Trial: always asks the server (it's the only source of truth for the count) and blocks outright if
    the server can't be reached — a trial claim we can't verify doesn't get the benefit of the doubt.
    Paid: tries the server first, but if it's unreachable and the last confirmed state (within
    OFFLINE_GRACE_DAYS) was an unexpired paid license, lets it through so a network blip doesn't stop
    someone who already paid.
    """
    cached = _load_cached()
    fp = cached.fingerprint if cached and cached.fingerprint else fingerprint()
    if not cached or not cached.token:
        cached = activate()
    try:
        resp = _post("/v1/consume", {"fingerprint": fp, "token": cached.token, "count": 1})
        state = _state_from(resp, fp)
        _save_cached(state)
        return state
    except LicenseError as e:
        if e.kind == "blocked":
            raise
        # network/server unreachable
        grace_ok = cached.status == "active" and time.time() - cached.cached_at < OFFLINE_GRACE_DAYS * 86400
        if grace_ok and (not cached.expires_at or cached.expires_at > time.time()):
            return cached
        if cached.status == "active":
            raise LicenseError(
                "Couldn't verify your license (offline too long). Connect to the internet once to re-sync.",
                kind="offline") from e
        raise LicenseError(
            "Couldn't verify your trial — check your internet connection and try again.", kind="offline") from e
