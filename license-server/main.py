"""Rebels Revolt Shorts - license server (optional online layer on top of the offline keys).

What it adds to the offline system in the app:
* Trial per PC is counted HERE too, so wiping the app's files doesn't reset the 3 free Shorts.
* You can REVOKE a key (refund, chargeback, key posted online): the app stops accepting it.
* You can see every key and every PC that activated it, and make keys from a web page (/admin),
  which is also what a payment webhook can call later.

Endpoints (the app calls these):
  GET  /v1/health
  POST /v1/trial      {device, slot}           -> may this PC make this free Short?
  POST /v1/check      {device, kid, version}   -> is this key still good? (revoked / wrong PC)
Admin (header X-Admin-Token, or the /admin web page):
  GET  /admin                                 simple dashboard
  POST /v1/admin/issue  {device_id, plan, days?, customer?}   make a key (needs SIGNING_KEY)
  POST /v1/admin/revoke {kid, revoked: true|false}
  GET  /v1/admin/list

Environment variables:
  ADMIN_TOKEN   long random password for /admin (required)
  LICENSE_DB    path of the SQLite file, e.g. /data/license.db on a persistent volume (required on hosts)
  SIGNING_KEY   optional: the 64-hex private key from your Key Maker, only if you want /admin to make keys
  TRIAL_LIMIT   free Shorts per PC (default 3)
"""
from __future__ import annotations

import base64
import hmac
import os
import secrets
import sqlite3
import struct
import time
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import ed25519

DB_PATH = os.environ.get("LICENSE_DB", "license.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
SIGNING_KEY = os.environ.get("SIGNING_KEY", "").strip().lower()
TRIAL_LIMIT = int(os.environ.get("TRIAL_LIMIT", "3"))
EPOCH = 1704067200
PLANS = {"monthly": 1, "annual": 2, "lifetime": 3}
PLAN_NAMES = {1: "Monthly", 2: "Annual", 3: "Lifetime"}

if len(ADMIN_TOKEN) < 12:
    raise RuntimeError("Set ADMIN_TOKEN (a long random password, 12+ characters) before starting the server.")

app = FastAPI(title="Rebels Revolt Shorts License Server")


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
        con.executescript("""
        CREATE TABLE IF NOT EXISTS trial (device TEXT, slot TEXT, at REAL, PRIMARY KEY (device, slot));
        CREATE TABLE IF NOT EXISTS keys (kid INTEGER PRIMARY KEY, plan INTEGER, device TEXT, expires REAL,
            customer TEXT, created REAL, revoked INTEGER DEFAULT 0, key TEXT);
        CREATE TABLE IF NOT EXISTS seen (kid INTEGER, device TEXT, first REAL, last REAL, version TEXT,
            PRIMARY KEY (kid, device));
        """)


init_db()


def _dev(s: str) -> str:
    s = "".join(ch for ch in (s or "").lower() if ch in "0123456789abcdef")
    if len(s) != 16:
        raise HTTPException(400, "bad device")
    return s


def _admin(token: Optional[str]):
    if not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "wrong admin token")


# ------------------------------------------------------------------ app endpoints
@app.get("/v1/health")
def health():
    return {"ok": True, "trial_limit": TRIAL_LIMIT, "can_issue": bool(SIGNING_KEY)}


class TrialIn(BaseModel):
    device: str
    slot: str


@app.post("/v1/trial")
def trial(b: TrialIn):
    dev = _dev(b.device)
    slot = b.slot[:40]
    with db() as con:
        used = {r["slot"] for r in con.execute("SELECT slot FROM trial WHERE device=?", (dev,))}
        if slot in used:
            return {"ok": True, "used": len(used), "limit": TRIAL_LIMIT}
        if len(used) >= TRIAL_LIMIT:
            return {"ok": False, "used": len(used), "limit": TRIAL_LIMIT,
                    "message": f"This PC already used its {TRIAL_LIMIT} free Shorts."}
        con.execute("INSERT INTO trial VALUES (?,?,?)", (dev, slot, time.time()))
        return {"ok": True, "used": len(used) + 1, "limit": TRIAL_LIMIT}


class CheckIn(BaseModel):
    device: str
    kid: int
    version: str = ""


@app.post("/v1/check")
def check(b: CheckIn):
    dev = _dev(b.device)
    now = time.time()
    with db() as con:
        k = con.execute("SELECT * FROM keys WHERE kid=?", (b.kid,)).fetchone()
        if k and k["revoked"]:
            return {"ok": False, "status": "revoked", "message": "This key was cancelled. Contact support."}
        con.execute("INSERT INTO seen VALUES (?,?,?,?,?) ON CONFLICT(kid, device) DO UPDATE SET last=?, version=?",
                    (b.kid, dev, now, now, b.version, now, b.version))
        return {"ok": True, "status": "active"}


# ------------------------------------------------------------------ key making (same format as the Key Maker)
def make_key(plan: str, device_id: str, days: Optional[int], customer: str) -> tuple[str, int, Optional[float]]:
    if len(SIGNING_KEY) != 64:
        raise HTTPException(400, "SIGNING_KEY isn't set on the server, so it can't make keys. Use the Key Maker.")
    pid = PLANS.get(plan)
    if not pid:
        raise HTTPException(400, "plan must be monthly, annual or lifetime")
    t = "".join(ch for ch in device_id.upper() if ch.isalnum())
    t = t[3:] if t.startswith("RRD") else t
    try:
        dev = base64.b32decode(t)[:8] if t else b"\0" * 8
    except Exception:
        raise HTTPException(400, "That Device ID has a typo (RRD-XXXX-XXXX-XXXX-XXXX).")
    issued = int((time.time() - EPOCH) // 86400)
    exp = 0xFFFF if pid == 3 else issued + int(days or (31 if pid == 1 else 366))
    kid = secrets.randbits(32)
    payload = b"R1" + struct.pack(">BHH8sI", pid, issued, exp, dev, kid) + b"\0"
    sig = ed25519.sign(bytes.fromhex(SIGNING_KEY), payload)
    raw = base64.b32encode(payload + sig).decode().rstrip("=")
    key = "RRS-" + "-".join(raw[i:i + 5] for i in range(0, len(raw), 5))
    expires = None if pid == 3 else EPOCH + exp * 86400.0
    with db() as con:
        con.execute("INSERT INTO keys (kid, plan, device, expires, customer, created, key) VALUES (?,?,?,?,?,?,?)",
                    (kid, pid, dev.hex(), expires, customer, time.time(), key))
    return key, kid, expires


class IssueIn(BaseModel):
    device_id: str
    plan: str = "monthly"
    days: Optional[int] = None
    customer: str = ""


@app.post("/v1/admin/issue")
def admin_issue(b: IssueIn, x_admin_token: Optional[str] = Header(None)):
    _admin(x_admin_token)
    key, kid, expires = make_key(b.plan, b.device_id, b.days, b.customer)
    return {"key": key, "kid": kid, "expires": expires}


class RevokeIn(BaseModel):
    kid: int
    revoked: bool = True
    customer: str = ""


@app.post("/v1/admin/revoke")
def admin_revoke(b: RevokeIn, x_admin_token: Optional[str] = Header(None)):
    _admin(x_admin_token)
    with db() as con:
        con.execute("INSERT INTO keys (kid, customer, created, revoked) VALUES (?,?,?,?) "
                    "ON CONFLICT(kid) DO UPDATE SET revoked=?", (b.kid, b.customer, time.time(), int(b.revoked),
                                                                   int(b.revoked)))
    return {"ok": True}


@app.get("/v1/admin/list")
def admin_list(x_admin_token: Optional[str] = Header(None)):
    _admin(x_admin_token)
    with db() as con:
        keys = [dict(r) for r in con.execute("SELECT * FROM keys ORDER BY created DESC LIMIT 500")]
        seen = [dict(r) for r in con.execute("SELECT * FROM seen ORDER BY last DESC LIMIT 500")]
        trials = con.execute("SELECT COUNT(DISTINCT device) FROM trial").fetchone()[0]
    return {"keys": keys, "activations": seen, "trial_pcs": trials, "can_issue": bool(SIGNING_KEY)}


ADMIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>RR Shorts licenses</title><style>
body{font-family:system-ui,sans-serif;background:#0f0d18;color:#eee;margin:0;padding:24px;max-width:1100px}
h1{margin:0 0 16px}input,select,button{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #444;background:#1c1928;color:#eee}
button{background:#e2366f;border:0;cursor:pointer}section{background:#1a1726;padding:16px;border-radius:12px;margin:14px 0}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px;border-bottom:1px solid #2c2840;text-align:left}
code{word-break:break-all;background:#000;padding:8px;display:block;border-radius:8px;margin-top:8px}.muted{color:#999}
</style></head><body><h1>Rebels Revolt Shorts · licenses</h1>
<section><input id="tok" type="password" placeholder="Admin token" size="40"> <button onclick="load()">Open</button></section>
<section id="mk"><b>Make a key</b><br><br>
<input id="cust" placeholder="Customer name / email" size="28"> <input id="dev" placeholder="RRD-XXXX-XXXX-XXXX-XXXX" size="28">
<select id="plan"><option>monthly</option><option>annual</option><option>lifetime</option></select>
<input id="days" placeholder="days (optional)" size="10"> <button onclick="issue()">Make key</button><code id="out" class="muted">…</code></section>
<section><b>Revoke a key made with the Key Maker</b> <span class="muted">(key id is in issued_keys.csv)</span><br><br>
<input id="rkid" placeholder="key id, e.g. 057ebc77" size="16"> <input id="rcust" placeholder="note" size="24"> <button onclick="revHex()">Revoke</button></section>
<section><b>Keys</b> <span class="muted" id="sum"></span><table id="keys"></table></section>
<section><b>PCs using keys</b><table id="seen"></table></section>
<script>
const T=()=>document.getElementById('tok').value;
async function call(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'X-Admin-Token':T(),'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});const j=await r.json();if(!r.ok)throw new Error(j.detail||r.status);return j}
const d=t=>t?new Date(t*1000).toLocaleDateString():'never';
async function load(){try{const j=await call('/v1/admin/list');
document.getElementById('sum').textContent=`${j.keys.length} keys · ${j.trial_pcs} PCs tried the app`+(j.can_issue?'':' · (set SIGNING_KEY to make keys here)');
document.getElementById('keys').innerHTML='<tr><th>Customer</th><th>Plan</th><th>Ends</th><th>Key id</th><th></th></tr>'+j.keys.map(k=>`<tr><td>${k.customer||''}</td><td>${({1:'Monthly',2:'Annual',3:'Lifetime'})[k.plan]||''}</td><td>${d(k.expires)}</td><td>${(k.kid>>>0).toString(16)}</td><td><button onclick="rev(${k.kid},${k.revoked?0:1})">${k.revoked?'Restore':'Revoke'}</button></td></tr>`).join('');
document.getElementById('seen').innerHTML='<tr><th>Key id</th><th>PC</th><th>First</th><th>Last seen</th><th>Version</th></tr>'+j.activations.map(s=>`<tr><td>${(s.kid>>>0).toString(16)}</td><td>${s.device}</td><td>${d(s.first)}</td><td>${d(s.last)}</td><td>${s.version||''}</td></tr>`).join('');
}catch(e){alert(e.message)}}
async function issue(){try{const j=await call('/v1/admin/issue',{customer:cust.value,device_id:dev.value,plan:plan.value,days:days.value?+days.value:null});out.textContent=j.key;out.classList.remove('muted');navigator.clipboard&&navigator.clipboard.writeText(j.key);load()}catch(e){alert(e.message)}}
async function revHex(){const k=parseInt(rkid.value.trim(),16);if(isNaN(k))return alert('key id is 8 letters/numbers');await call('/v1/admin/revoke',{kid:k,revoked:true,customer:rcust.value});load()}
async function rev(kid,on){if(on&&!confirm('Revoke this key? The app stops accepting it at its next check.'))return;await call('/v1/admin/revoke',{kid,revoked:!!on});load()}
</script></body></html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return ADMIN_HTML


@app.get("/")
def root():
    return {"ok": True, "service": "Rebels Revolt Shorts license server"}
