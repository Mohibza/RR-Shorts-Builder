"""Per-account proxies for the app's browsers.

Chrome can't take a proxy username/password on the command line, and it has no SOCKS5 login at all. So every
account with a proxy gets a tiny relay on 127.0.0.1 inside the app: Chrome talks to the relay, the relay logs in
to your real proxy (HTTP or SOCKS5, with or without a password) and passes the traffic through.

* The relay never falls back to your own connection: if the proxy is down, pages simply don't load (so accounts
  are never linked by your home IP), and the upload waits and retries.
* WebRTC is limited to the proxy too, so sites can't see your real IP that way.

Accepted formats (what proxy shops hand out):
    host:port                 host:port:user:pass          user:pass@host:port
    http://user:pass@host:port                socks5://user:pass@host:port
"""
from __future__ import annotations

import base64
import json
import select
import socket
import socketserver
import struct
import threading
import time
import urllib.parse
from typing import Optional

_RELAYS: dict[str, "_Relay"] = {}
_GUARD = threading.Lock()
IDLE = 300          # close a tunnel after this many seconds without traffic


class ProxyError(ValueError):
    pass


# ---------------------------------------------------------------- parsing
def parse(text: str) -> Optional[dict]:
    """Proxy text -> {scheme, host, port, user, pw}; None for empty. Raises ProxyError with a friendly message."""
    t = (text or "").strip()
    if not t:
        return None
    import re
    m = re.match(r"^(?:(https?|socks5h?|socks)://)?([^:@/\s]+):(\d{1,5}):([^:]*):(.*)$", t)
    if m:                                                    # host:port:user:pass (password may contain @ or :)
        sch = {"https": "http", "socks5h": "socks5", "socks": "socks5"}.get((m.group(1) or "http").lower(),
                                                                          (m.group(1) or "http").lower())
        if not 0 < int(m.group(3)) < 65536:
            raise ProxyError("The proxy port must be a number (for example 8000).")
        return {"scheme": sch, "host": m.group(2), "port": int(m.group(3)), "user": m.group(4).strip(),
                "pw": m.group(5).strip()}
    scheme = "http"
    if "://" in t:
        scheme, t = t.split("://", 1)
        scheme = scheme.lower().strip()
        scheme = {"https": "http", "socks5h": "socks5", "socks": "socks5"}.get(scheme, scheme)
        if scheme not in ("http", "socks5"):
            raise ProxyError("Only HTTP and SOCKS5 proxies are supported (SOCKS4 isn't).")
    t = t.strip().rstrip("/")
    user = pw = ""
    if "@" in t:
        cred, t = t.rsplit("@", 1)
        user, _, pw = cred.partition(":")
        user, pw = urllib.parse.unquote(user), urllib.parse.unquote(pw)
    parts = t.split(":")
    if len(parts) == 2:
        host, port = parts
    elif len(parts) >= 4 and not user:
        if parts[1].strip().isdigit():                       # host:port:user:pass
            host, port, user, pw = parts[0], parts[1], parts[2], ":".join(parts[3:])
        elif parts[-1].strip().isdigit():                    # user:pass:host:port
            user, pw, host, port = parts[0], ":".join(parts[1:-2]), parts[-2], parts[-1]
        else:
            raise ProxyError("Couldn't read that proxy. Use host:port or host:port:user:pass.")
    else:
        raise ProxyError("Couldn't read that proxy. Use host:port or host:port:user:pass.")
    host, port = host.strip().strip("[]"), port.strip()
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ProxyError("The proxy port must be a number (for example 8000).")
    if " " in host:
        raise ProxyError("The proxy address can't contain spaces.")
    return {"scheme": scheme, "host": host, "port": int(port), "user": user.strip(), "pw": pw.strip()}


def normalize(text: str) -> str:
    """Canonical form saved with the account ('' = no proxy)."""
    p = parse(text)
    if not p:
        return ""
    cred = ""
    if p["user"]:
        cred = urllib.parse.quote(p["user"], safe="") + ":" + urllib.parse.quote(p["pw"], safe="") + "@"
    return f"{p['scheme']}://{cred}{p['host']}:{p['port']}"


def display(text: str) -> str:
    """Safe to show: no password."""
    try:
        p = parse(text)
    except ProxyError:
        return "invalid proxy"
    if not p:
        return ""
    return f"{'SOCKS5 ' if p['scheme'] == 'socks5' else ''}{p['host']}:{p['port']}" + (f" ({p['user']})" if p["user"] else "")


def endpoint(text: str) -> str:
    """host:port of a proxy (to spot two accounts sharing one)."""
    try:
        p = parse(text)
    except ProxyError:
        return ""
    return f"{p['host'].lower()}:{p['port']}" if p else ""


# ---------------------------------------------------------------- relay
def _read_head(sock: socket.socket, limit: int = 65536) -> tuple[bytes, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
        if len(buf) > limit:
            raise ConnectionError("header too large")
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head, rest


def _pipe(a: socket.socket, b: socket.socket) -> None:
    socks = [a, b]
    last = time.time()
    while time.time() - last < IDLE:
        r, _, x = select.select(socks, [], socks, 10)
        if x:
            return
        for s in r:
            try:
                data = s.recv(65536)
            except OSError:
                return
            if not data:
                return
            (b if s is a else a).sendall(data)
            last = time.time()


def _recvn(s: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = s.recv(n - len(out))
        if not chunk:
            raise ConnectionError("the proxy closed the connection")
        out += chunk
    return out


def _socks5_connect(p: dict, host: str, port: int, timeout: float = 20) -> socket.socket:
    s = socket.create_connection((p["host"], p["port"]), timeout=timeout)
    try:
        s.sendall(b"\x05\x02\x00\x02" if p["user"] else b"\x05\x01\x00")
        ver, method = _recvn(s, 2)
        if ver != 5:
            raise ConnectionError("that address isn't a SOCKS5 proxy (try http:// instead)")
        if method == 0x02:
            u, pw = p["user"].encode(), p["pw"].encode()
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(pw)]) + pw)
            if _recvn(s, 2)[1:2] != b"\x00":
                raise ConnectionError("SOCKS5 proxy rejected the username/password")
        elif method != 0x00:
            raise ConnectionError("SOCKS5 proxy wants a login" if method == 0xFF else "SOCKS5 proxy refused us")
        h = host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", port))
        rep = _recvn(s, 4)
        if rep[1] != 0:
            raise ConnectionError(f"SOCKS5 proxy couldn't reach {host} (code {rep[1]})")
        atyp = rep[3]
        _recvn(s, {1: 4, 4: 16}.get(atyp, 0) or _recvn(s, 1)[0])   # bound address
        _recvn(s, 2)                                                # bound port
        s.settimeout(None)
        return s
    except Exception:
        s.close()
        raise


def _split_hostport(target: str, default: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        return target, default
    return host, int(port)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        p = self.server.proxy            # type: ignore[attr-defined]
        c = self.request
        up = None
        try:
            c.settimeout(30)
            head, rest = _read_head(c)
            lines = head.decode("latin-1").split("\r\n")
            method, target, ver = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = [ln for ln in lines[1:] if ln and not ln.lower().startswith(("proxy-authorization:",
                                                                                   "proxy-connection:"))]
            if p["scheme"] == "http":
                up = socket.create_connection((p["host"], p["port"]), timeout=20)
                if p["user"]:
                    tok = base64.b64encode(f"{p['user']}:{p['pw']}".encode()).decode()
                    headers.append(f"Proxy-Authorization: Basic {tok}")
                if method.upper() != "CONNECT":   # one request per connection, so every request carries the login
                    headers = [h for h in headers if not h.lower().startswith("connection:")] + ["Connection: close"]
                up.sendall(("\r\n".join([f"{method} {target} {ver}"] + headers) + "\r\n\r\n").encode("latin-1") + rest)
            else:
                if method.upper() == "CONNECT":
                    host, port = _split_hostport(target, 443)
                    up = _socks5_connect(p, host, port)
                    c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    if rest:
                        up.sendall(rest)
                else:
                    u = urllib.parse.urlsplit(target)
                    up = _socks5_connect(p, u.hostname or "", u.port or 80)
                    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                    headers = [h for h in headers if not h.lower().startswith("connection:")] + ["Connection: close"]
                    up.sendall(("\r\n".join([f"{method} {path} {ver}"] + headers) + "\r\n\r\n").encode("latin-1")
                               + rest)
            up.settimeout(None)
            c.settimeout(None)
            _pipe(c, up)
        except Exception as e:
            try:
                msg = f"Proxy problem: {e}".encode("utf-8", "replace")
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n"
                          b"Content-Length: " + str(len(msg)).encode() + b"\r\n\r\n" + msg)
            except Exception:
                pass
        finally:
            for s in (up, c):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass


class _Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, proxy: dict):
        self.proxy = proxy
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, name="proxy-relay", daemon=True).start()

    @property
    def port(self) -> int:
        return self.server_address[1]


def relay(text: str) -> Optional[str]:
    """Start (once) the local relay for this proxy; returns 'http://127.0.0.1:PORT' or None for no proxy."""
    key = normalize(text)
    if not key:
        return None
    with _GUARD:
        r = _RELAYS.get(key)
        if r is None:
            r = _RELAYS[key] = _Relay(parse(key))
        return f"http://127.0.0.1:{r.port}"


def chrome_flags(text: str) -> list[str]:
    """Browser flags for an account's proxy ([] = no proxy)."""
    url = relay(text)
    if not url:
        return []
    return [f"--proxy-server={url}", "--proxy-bypass-list=<-loopback>",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"]


def check(text: str, timeout: float = 20) -> dict:
    """Test a proxy: {'ok', 'ip', 'country', 'city', 'ms', 'error'}."""
    try:
        url = relay(text)
    except ProxyError as e:
        return {"ok": False, "error": str(e)}
    if not url:
        return {"ok": False, "error": "No proxy entered."}
    p = parse(text)
    t0 = time.time()
    try:                                  # precise errors first: can we reach the proxy, does the login work?
        if p["scheme"] == "socks5":
            _socks5_connect(p, "api.ipify.org", 443, timeout=min(timeout, 15)).close()
        else:
            socket.create_connection((p["host"], p["port"]), timeout=min(timeout, 15)).close()
    except Exception as e:
        return {"ok": False, "error": _friendly(str(e)), "ms": int((time.time() - t0) * 1000)}
    port = int(url.rsplit(":", 1)[1])
    t = time.time()
    last = ""
    import http.client
    for host, path in (("ipinfo.io", "/json"), ("api.ipify.org", "/?format=json")):
        conn = None
        try:     # tunnel through the relay by hand, so no system "bypass proxy for …" rule can skip it
            conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout)
            conn.set_tunnel(host, 443)
            conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            r = conn.getresponse()
            body = r.read().decode("utf-8", "replace")
            if r.status != 200:
                raise OSError(f"HTTP {r.status}")
            d = json.loads(body)
            return {"ok": True, "ip": d.get("ip", ""), "country": d.get("country", ""), "city": d.get("city", ""),
                    "ms": int((time.time() - t) * 1000), "error": ""}
        except Exception as e:
            last = str(e)
            if "407" in last:
                last = "The proxy rejected the username/password (407)."
            elif "502" in last:
                last = "The proxy couldn't open the website (it may block it, or it's overloaded)."
        finally:
            if conn:
                conn.close()
    return {"ok": False, "error": _friendly(last), "ms": int((time.time() - t) * 1000)}


def _friendly(e: str) -> str:
    low = e.lower()
    if "timed out" in low:
        return "The proxy didn't answer in time (wrong address, or it's offline)."
    if "refused" in low:
        return "The proxy refused the connection (wrong port, or it's offline)."
    if low.startswith("http ") or "tunnel connection failed" in low:
        return f"The proxy works, but the test website couldn't be opened through it ({e}). It may block that site."
    if "getaddrinfo" in low or "name or service" in low or "nodename" in low:
        return "The proxy address couldn't be found. Check the host name."
    return e[:220] or "The proxy didn't work."
