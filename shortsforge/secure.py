"""Encrypt secrets at rest with Windows DPAPI (tied to this Windows user; other users/PCs can't read them)."""
from __future__ import annotations

import base64
import os

PREFIX = "dpapi:"


def dpapi(data: bytes, protect: bool) -> bytes:
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
        raise OSError("Windows could not encrypt/decrypt the saved data")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def seal(text: str) -> str:
    """'dpapi:<base64>' for a secret string (empty stays empty)."""
    if not text or os.name != "nt":
        return text
    return PREFIX + base64.b64encode(dpapi(text.encode("utf-8"), True)).decode("ascii")


def unseal(value: str) -> str:
    """Inverse of seal(). Plain (older) values pass through; unreadable ones (copied from another PC) -> ''."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    try:
        return dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except Exception:
        return ""
