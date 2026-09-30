"""Trial + subscription keys (works fully offline; an online check can be switched on later).

* Trial: 3 free Shorts per PC. Re-exporting the same clip doesn't use another one.
* After that: a Monthly or Annual key (or Lifetime) made with the owner's Key Maker (tools/keymaker).
  Keys are Ed25519-signed and locked to one PC's Device ID, so they can't be forged, edited or shared:
  only the public key is inside the app, the private key never leaves the owner's computer.
* The trial counter and the activated key are stored encrypted + signed (keyed to this PC) in three places
  (settings folder, local app data, registry). Deleting one copy or reinstalling doesn't reset the trial,
  editing a copy is detected (tampering = trial over), and turning the PC clock back is detected.

Honest limit: no offline check can stop someone who modifies the program itself. The signed keys make
forging impossible and the encrypted counter stops the easy resets; turning on the online check
(ONLINE_URL, once the website is live) is what shuts out cracked copies for good.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import ed25519
from .config import data_dir

FREE_SHORTS = 3
PLANS = {1: ("monthly", "Monthly"), 2: ("annual", "Annual"), 3: ("lifetime", "Lifetime")}
EPOCH = 1704067200            # 2024-01-01: key dates are days since then
GRACE_DAYS = 3                # a subscription keeps working this long after its end date
ONLINE_CHECK_HOURS = 12      # a subscription is re-checked with the license server this often
ONLINE_GRACE_DAYS = 7        # ...and keeps working this long without internet


def online_url() -> str:
    """Your license server (license-server/). Set ONLINE_URL in license_pub.py; empty = offline only."""
    env = os.environ.get("RRSF_LICENSE_SERVER", "").strip()
    if env:
        return env.rstrip("/")
    try:
        from .license_pub import ONLINE_URL
        return (ONLINE_URL or "").strip().rstrip("/")
    except Exception:
        return ""


def _post(path: str, body: dict, timeout: float = 6.0) -> Optional[dict]:
    """None = server not reachable (then the offline rules apply)."""
    url = online_url()
    if not url:
        return None
    import urllib.request
    try:
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None
_SALT = bytes.fromhex("5b1e7a0c93d24f6e8a41c0de77f3a219")   # part of the state key (with this PC's id)
_lock = threading.Lock()


class LicenseError(RuntimeError):
    """Raised when a Short may not be made. .kind: "blocked" (needs a key) | "offline"."""

    def __init__(self, message: str, kind: str = "blocked"):
        super().__init__(message)
        self.kind = kind


def enabled() -> bool:
    """Enforced in the installer build (the .exe you sell). Running from the project folder (run.bat) skips
    it so your own work never stops; set RRSF_LICENSE_ENFORCE=1 to test the trial from source."""
    import sys
    force = os.environ.get("RRSF_LICENSE_ENFORCE", "")
    if force in ("0", "1"):
        return force == "1"
    return bool(getattr(sys, "frozen", False))


# ------------------------------------------------------------------------------------------- this PC
def _machine_guid() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
            return winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        return None


def _board_uuid() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
            timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        v = out.decode(errors="ignore").strip()
        return v if len(v) > 8 else None
    except Exception:
        return None


_FP: Optional[str] = None


def fingerprint() -> str:
    """Stable id of this PC (Windows MachineGuid; motherboard UUID or a stored random id as fallback)."""
    global _FP
    if _FP:
        return _FP
    raw = _machine_guid() or (_board_uuid() if os.name == "nt" else None)
    if not raw:
        marker = data_dir() / ".device_id"
        try:
            raw = marker.read_text().strip()
        except OSError:
            raw = secrets.token_hex(16)
            try:
                marker.write_text(raw, encoding="utf-8")
            except OSError:
                pass
    _FP = hashlib.sha256(f"rrsf::{raw}".encode()).hexdigest()
    return _FP


def _device_digest() -> bytes:
    return hashlib.sha256(("rrs-device:" + fingerprint()).encode()).digest()


def device_bytes() -> bytes:
    return _device_digest()[:8]


def device_id() -> str:
    """What a customer sends you to get a key: RRD-XXXX-XXXX-XXXX-XXXX."""
    b = base64.b32encode(_device_digest()[:10]).decode()
    return "RRD-" + "-".join(b[i:i + 4] for i in range(0, 16, 4))


def parse_device_id(s: str) -> bytes:
    t = "".join(ch for ch in s.upper() if ch.isalnum())
    if t.startswith("RRD"):
        t = t[3:]
    if len(t) != 16:
        raise ValueError("A Device ID looks like RRD-XXXX-XXXX-XXXX-XXXX")
    try:
        return base64.b32decode(t)[:8]
    except Exception:
        raise ValueError("That Device ID has a typo. It looks like RRD-XXXX-XXXX-XXXX-XXXX")


# ------------------------------------------------------------------------------------------- keys
@dataclass
class Key:
    plan: int
    issued: int          # days since EPOCH
    expires: int         # days since EPOCH (0xFFFF = never)
    device: bytes        # 8 bytes, all zero = any PC
    kid: int             # key number (for revoking later)

    @property
    def plan_name(self) -> str:
        return PLANS.get(self.plan, ("?", "?"))[1]

    @property
    def expires_at(self) -> Optional[float]:
        return None if self.expires == 0xFFFF else EPOCH + self.expires * 86400.0


_MAGIC = b"R1"


def pack(k: Key) -> bytes:
    return _MAGIC + struct.pack(">BHH8sI", k.plan, k.issued, k.expires, k.device, k.kid) + b"\0"


def unpack(b: bytes) -> Key:
    plan, issued, exp, dev, kid = struct.unpack(">BHH8sI", b[2:19])
    return Key(plan, issued, exp, dev, kid)


def encode_key(payload: bytes, sig: bytes) -> str:
    t = base64.b32encode(payload + sig).decode().rstrip("=")
    return "RRS-" + "-".join(t[i:i + 5] for i in range(0, len(t), 5))


def decode_key(text: str) -> tuple[bytes, bytes]:
    t = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    if t.startswith("RRS"):
        t = t[3:]
    try:
        raw = base64.b32decode(t + "=" * (-len(t) % 8))
    except Exception:
        raise LicenseError("That doesn't look like a Rebels Revolt Shorts key. Copy the whole key and try again.")
    if len(raw) != 20 + 64 or raw[:2] != _MAGIC:
        raise LicenseError("That doesn't look like a Rebels Revolt Shorts key. Copy the whole key and try again.")
    return raw[:20], raw[20:]


def _public_key() -> bytes:
    from .license_pub import PUBLIC_KEY_HEX
    return bytes.fromhex(PUBLIC_KEY_HEX) if PUBLIC_KEY_HEX else b""


def check_key(text: str, now: Optional[float] = None) -> Key:
    """Validate a key for THIS PC. Raises LicenseError with a message for the customer."""
    payload, sig = decode_key(text)
    pub = _public_key()
    if not pub or not ed25519.verify(pub, payload, sig):
        raise LicenseError("This key isn't valid. Check you copied all of it, or contact support.")
    k = unpack(payload)
    if k.device != b"\0" * 8 and not hmac.compare_digest(k.device, device_bytes()):
        raise LicenseError(f"This key was made for a different PC. Your Device ID is {device_id()}.")
    now = now or time.time()
    if k.expires_at and now > k.expires_at + GRACE_DAYS * 86400:
        raise LicenseError(f"This {k.plan_name.lower()} key ended on {time.strftime('%d %b %Y', time.localtime(k.expires_at))}. "
                           "Renew your subscription to keep making Shorts.")
    return k


# ------------------------------------------------------------------------------------------- stored state
def _state_key() -> bytes:
    return hashlib.sha256(b"rrs-state-v2" + _SALT + fingerprint().encode()).digest()


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + i.to_bytes(4, "big")).digest()
        i += 1
    return out[:n]


def _seal(obj: dict) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode()
    k = _state_key()
    nonce = secrets.token_bytes(12)
    ct = bytes(a ^ b for a, b in zip(data, _stream(k, nonce, len(data))))
    mac = hmac.new(k, nonce + ct, hashlib.sha256).digest()
    return _dpapi(b"RS2" + nonce + ct + mac, True)


def _open(blob: bytes) -> Optional[dict]:
    """dict if genuine, {"_bad": True} if tampered, None if unreadable/empty."""
    try:
        raw = _dpapi(blob, False)
    except Exception:
        return {"_bad": True}
    if raw[:1] == b"{":           # the old (v2.2) license cache: not ours to judge, ignore it
        return None
    if not raw.startswith(b"RS2") or len(raw) < 3 + 12 + 32:
        return {"_bad": True} if raw else None
    nonce, ct, mac = raw[3:15], raw[15:-32], raw[-32:]
    k = _state_key()
    if not hmac.compare_digest(mac, hmac.new(k, nonce + ct, hashlib.sha256).digest()):
        return {"_bad": True}
    try:
        return json.loads(bytes(a ^ b for a, b in zip(ct, _stream(k, nonce, len(ct)))).decode())
    except Exception:
        return {"_bad": True}


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI: only this Windows user on this PC can decrypt. Pass-through elsewhere (dev/tests)."""
    if os.name != "nt" or not data:
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
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _files() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache"))
    return [data_dir() / "license.dat", local / "RRShorts" / "cache" / "idx.bin"]


_REG = r"Software\RRShortsBuilder\Cache"


def _reg_read() -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG) as k:
            return bytes(winreg.QueryValueEx(k, "s")[0])
    except Exception:
        return None


def _reg_write(blob: bytes) -> None:
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG) as k:
            winreg.SetValueEx(k, "s", 0, winreg.REG_BINARY, blob)
    except Exception:
        pass


@dataclass
class State:
    used: list = field(default_factory=list)    # clip ids that used a free Short
    first: float = 0.0
    last: float = 0.0                            # latest time the app saw (clock-rollback check)
    key: str = ""
    tampered: bool = False
    srv_ok: float = 0.0                          # last time the license server confirmed the key
    revoked: str = ""                            # key cancelled on the server


def _load() -> State:
    blobs = []
    for f in _files():
        try:
            blobs.append(f.read_bytes())
        except OSError:
            pass
    r = _reg_read()
    if r:
        blobs.append(r)
    st = State()
    firsts, kt = [], -1.0
    for b in blobs:
        d = _open(b)
        if not d:
            continue
        if d.get("_bad"):
            st.tampered = True
            continue
        st.used = sorted(set(st.used) | set(d.get("used") or []))
        if d.get("first"):
            firsts.append(float(d["first"]))
        st.last = max(st.last, float(d.get("last") or 0))
        st.srv_ok = max(st.srv_ok, float(d.get("srv") or 0))
        st.revoked = st.revoked or str(d.get("rev") or "")
        if d.get("key") and float(d.get("kt") or 0) >= kt:
            st.key, kt = d["key"], float(d.get("kt") or 0)
    st.first = min(firsts) if firsts else 0.0
    st._kt = kt if kt > 0 else 0.0   # type: ignore[attr-defined]
    return st


def _save(st: State) -> None:
    blob = _seal({"used": st.used[-50:], "first": st.first, "last": st.last, "key": st.key,
                  "srv": st.srv_ok, "rev": st.revoked,
                  "kt": getattr(st, "_kt", time.time()) if st.key else 0, "v": 2})
    for f in _files():
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, f)
        except OSError:
            pass
    _reg_write(blob)


# ------------------------------------------------------------------------------------------- public API
@dataclass
class LicenseState:
    status: str = "trial"            # trial | active | expired | clock | tampered
    videos_used: int = 0
    videos_allowed: int = FREE_SHORTS
    plan: Optional[str] = None
    expires_at: Optional[float] = None
    fingerprint: str = ""
    device_id: str = ""
    message: str = ""

    @property
    def videos_left(self) -> int:
        return 10 ** 9 if self.status == "active" else max(0, self.videos_allowed - self.videos_used)


def _evaluate(st: State) -> LicenseState:
    now = time.time()
    ls = LicenseState(videos_used=min(FREE_SHORTS, len(st.used)), device_id=device_id(), fingerprint=device_id())
    if st.tampered and not st.key:
        ls.status, ls.videos_used = "tampered", FREE_SHORTS
        ls.message = "The trial data on this PC was changed, so the free Shorts are used up."
    if st.last and now < st.last - 36 * 3600:
        ls.status = "clock"
        ls.message = "Your PC's date looks wrong (it went back in time). Set the correct date and time to continue."
        return ls
    if st.key and st.revoked and st.revoked == st.key[-12:]:
        ls.status, ls.message = "expired", "This key was cancelled. Contact support for a new one."
        return ls
    if st.key:
        try:
            k = check_key(st.key, now)
            ls.status, ls.plan, ls.expires_at = "active", k.plan_name, k.expires_at
            return ls
        except LicenseError as e:
            ls.status, ls.message = "expired", str(e)
            try:
                k = unpack(decode_key(st.key)[0])
                ls.plan, ls.expires_at = k.plan_name, k.expires_at
            except Exception:
                pass
    return ls


def current_state() -> LicenseState:
    with _lock:
        st = _load()
        return _evaluate(st)


def _touch(st: State) -> None:
    now = time.time()
    if not st.first:
        st.first = now
    if now > st.last:
        st.last = now


def check(slot: str) -> None:
    """Raise early (before any work) if this Short couldn't be made. Uses nothing up."""
    reserve(slot, consume=False)


def reserve(slot: str, consume: bool = True) -> Optional[str]:
    """Call right before rendering a Short. Returns a token for release() if a free Short was used;
    raises LicenseError when the trial is over / the subscription ended."""
    if not enabled():
        return None
    with _lock:
        st = _load()
        ls = _evaluate(st)
        if ls.status == "clock":
            raise LicenseError(ls.message)
        if ls.status == "active":
            if consume:
                _online_key_check(st)
                _touch(st)
                _save(st)
            return None
        h = hashlib.sha256(("slot:" + slot).encode()).hexdigest()[:16]
        if h in st.used and not st.tampered:          # re-export of a clip that already used a free Short
            if consume:
                _touch(st)
                _save(st)
            return None
        if ls.status in ("expired",):
            raise LicenseError(ls.message + f"  Enter a new key in Settings → License (Device ID {device_id()}).")
        if len(st.used) >= FREE_SHORTS or st.tampered:
            raise LicenseError(f"Your {FREE_SHORTS} free Shorts are used up. Subscribe (monthly or annual) and enter "
                               f"your key in Settings → License to keep going. Your Device ID: {device_id()}")
        if not consume:
            return None
        r = _post("/v1/trial", {"device": device_bytes().hex(), "slot": h})
        if r is not None and not r.get("ok", True):
            while len(st.used) < FREE_SHORTS:                 # the server says this PC's trial is used up
                st.used.append(secrets.token_hex(8))
            _touch(st)
            _save(st)
            raise LicenseError(f"Your {FREE_SHORTS} free Shorts on this PC are used up. Subscribe (monthly or annual) "
                               f"and enter your key in Settings → License. Your Device ID: {device_id()}")
        st.used.append(h)
        _touch(st)
        _save(st)
        return h


def _online_key_check(st: State) -> None:
    """With a license server: confirm the key isn't cancelled (every ONLINE_CHECK_HOURS); without
    internet it keeps working for ONLINE_GRACE_DAYS since the last confirmation."""
    if not online_url() or not st.key:
        return
    now = time.time()
    if now - st.srv_ok < ONLINE_CHECK_HOURS * 3600:
        return
    try:
        k = unpack(decode_key(st.key)[0])
    except LicenseError:
        return
    from . import __version__
    r = _post("/v1/check", {"device": device_bytes().hex(), "kid": k.kid, "version": __version__})
    if r is None:
        since = max(st.srv_ok, getattr(st, "_kt", 0.0) or 0.0, st.first)
        if now - since > ONLINE_GRACE_DAYS * 86400:
            raise LicenseError("Please connect to the internet once so your subscription can be confirmed.", "offline")
        return
    if not r.get("ok", True):
        st.revoked = st.key[-12:]
        _save(st)
        raise LicenseError(r.get("message") or "This key was cancelled. Contact support.")
    st.srv_ok = now


def release(token: Optional[str]) -> None:
    """The render failed or was cancelled: give the free Short back."""
    if not token:
        return
    with _lock:
        st = _load()
        if token in st.used:
            st.used.remove(token)
            _save(st)


def redeem(text: str) -> LicenseState:
    """Activate a key from Settings → License."""
    k = check_key(text)
    with _lock:
        st = _load()
        st.key = encode_key(*decode_key(text))
        st._kt = time.time()   # type: ignore[attr-defined]
        st.tampered = False
        st.revoked = ""
        st.srv_ok = 0.0
        _online_key_check(st)              # a cancelled key is refused right away when a server is set
        _touch(st)
        _save(st)
        ls = _evaluate(st)
    if ls.status != "active":
        raise LicenseError(ls.message or "Couldn't activate this key.")
    return ls


def gate() -> None:
    """Kept for older callers: analysing a video is always free; making Shorts is what's counted."""
    return None


def make_key(private_hex: str, plan: str, device: str = "", days: Optional[int] = None,
             kid: Optional[int] = None, start: Optional[float] = None) -> str:
    """Used by the Key Maker (tools/keymaker). plan: monthly | annual | lifetime."""
    pid = {"monthly": 1, "annual": 2, "lifetime": 3}[plan]
    start = start or time.time()
    issued = int((start - EPOCH) // 86400)
    if pid == 3:
        exp = 0xFFFF
    else:
        exp = issued + int(days or (31 if pid == 1 else 366))
    dev = parse_device_id(device) if device.strip() else b"\0" * 8
    k = Key(pid, issued, exp, dev, kid if kid is not None else secrets.randbits(32))
    payload = pack(k)
    sig = ed25519.sign(bytes.fromhex(private_hex), payload)
    return encode_key(payload, sig)
