"""YouTube login cookies saved by the in-app sign-in (Netscape cookies.txt format for yt-dlp)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import data_dir

COOKIE_FILE = data_dir() / "youtube_cookies.txt"
LOGIN_NAMES = {"SAPISID", "__Secure-3PAPISID", "LOGIN_INFO", "SID", "__Secure-1PSID"}
BOT_MARKERS = ("not a bot", "sign in to confirm", "use --cookies", "cookies-from-browser", "login required",
               "age-restricted", "confirm your age", "private video")


def needs_login(message: str) -> bool:
    m = (message or "").lower()
    return any(k in m for k in BOT_MARKERS)


def has_login() -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        txt = COOKIE_FILE.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(f"\t{n}\t" in txt for n in LOGIN_NAMES) and "youtube.com" in txt


def write_cookies(cookies: list[dict]) -> int:
    """cookies: [{domain, name, value, path, secure, httponly, expires(int|0)}]. Returns count written."""
    keep = [c for c in cookies if any(d in c["domain"] for d in ("youtube.com", "google.com", "googlevideo.com"))]
    soon = int(time.time()) + 30 * 86400
    lines = ["# Netscape HTTP Cookie File", "# Saved by RR Shorts Builder in-app YouTube sign-in", ""]
    for c in keep:
        domain = c["domain"]
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        exp = int(c.get("expires") or 0) or soon  # session cookies -> keep for 30 days
        prefix = "#HttpOnly_" if c.get("httponly") else ""
        value = str(c["value"]).replace("\t", " ").replace("\n", "")
        lines.append("\t".join([prefix + domain, flag, c.get("path") or "/", "TRUE" if c.get("secure") else "FALSE",
                                str(exp), c["name"], value]))
    tmp = COOKIE_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(COOKIE_FILE)
    return len(keep)


def import_file(path: str) -> bool:
    """Use a cookies.txt exported from a browser extension."""
    txt = Path(path).read_text(encoding="utf-8", errors="ignore")
    if "youtube.com" not in txt:
        raise ValueError("That file doesn't contain YouTube cookies.")
    shutil.copyfile(path, COOKIE_FILE)
    return has_login()


def sign_out() -> None:
    COOKIE_FILE.unlink(missing_ok=True)
