// Rebels Revolt Shorts license server for Cloudflare Workers + D1 (free plan, always on).
// Same API as license-server/main.py, so the app works with either.
//
// Setup (Cloudflare dashboard): D1 database bound as "DB" (run schema.sql once in its Console),
// secrets ADMIN_TOKEN (required) and SIGNING_KEY (optional, 64 hex from your Key Maker),
// variable TRIAL_LIMIT (default 3).

const EPOCH = 1704067200;
const PLANS = { monthly: 1, annual: 2, lifetime: 3 };
const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function b32encode(bytes) {
  let bits = 0, val = 0, out = "";
  for (const b of bytes) { val = (val << 8) | b; bits += 8; while (bits >= 5) { out += B32[(val >>> (bits - 5)) & 31]; bits -= 5; } }
  if (bits > 0) out += B32[(val << (5 - bits)) & 31];
  return out;
}
function b32decode(s) {
  let bits = 0, val = 0; const out = [];
  for (const ch of s) { const i = B32.indexOf(ch); if (i < 0) throw new Error("bad"); val = (val << 5) | i; bits += 5; if (bits >= 8) { out.push((val >>> (bits - 8)) & 255); bits -= 8; } }
  return new Uint8Array(out);
}
const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (h) => new Uint8Array(h.match(/../g).map((x) => parseInt(x, 16)));
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });
const now = () => Date.now() / 1000;

function devHex(s) {
  const t = String(s || "").toLowerCase().replace(/[^0-9a-f]/g, "");
  if (t.length !== 16) throw new HttpError(400, "bad device");
  return t;
}
class HttpError extends Error { constructor(code, msg) { super(msg); this.code = code; } }

function isAdmin(req, env) {
  const t = req.headers.get("X-Admin-Token") || "";
  const a = env.ADMIN_TOKEN || "";
  if (a.length < 12 || t.length !== a.length) return false;
  let d = 0; for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ t.charCodeAt(i);
  return d === 0;
}

async function signKey(env, payload) {
  const seed = unhex(String(env.SIGNING_KEY || "").trim().toLowerCase());
  const der = new Uint8Array([...unhex("302e020100300506032b657004220420"), ...seed]);
  let key;
  try { key = await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]); }
  catch { key = await crypto.subtle.importKey("pkcs8", der, { name: "NODE-ED25519", namedCurve: "NODE-ED25519" }, false, ["sign"]); }
  let sig;
  try { sig = await crypto.subtle.sign({ name: "Ed25519" }, key, payload); }
  catch { sig = await crypto.subtle.sign({ name: "NODE-ED25519" }, key, payload); }
  return new Uint8Array(sig);
}

async function makeKey(env, plan, deviceId, days, customer) {
  if (String(env.SIGNING_KEY || "").trim().length !== 64) throw new HttpError(400, "SIGNING_KEY isn't set, so keys can't be made here. Use the Key Maker.");
  const pid = PLANS[plan];
  if (!pid) throw new HttpError(400, "plan must be monthly, annual or lifetime");
  let t = String(deviceId || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (t.startsWith("RRD")) t = t.slice(3);
  let dev;
  try { dev = t ? b32decode(t).slice(0, 8) : new Uint8Array(8); } catch { throw new HttpError(400, "That Device ID has a typo (RRD-XXXX-XXXX-XXXX-XXXX)."); }
  if (dev.length !== 8) throw new HttpError(400, "That Device ID has a typo (RRD-XXXX-XXXX-XXXX-XXXX).");
  const issued = Math.floor((now() - EPOCH) / 86400);
  const exp = pid === 3 ? 0xffff : issued + Number(days || (pid === 1 ? 31 : 366));
  const kid = crypto.getRandomValues(new Uint32Array(1))[0];
  const p = new Uint8Array(20);
  const v = new DataView(p.buffer);
  p[0] = 82; p[1] = 49; p[2] = pid; v.setUint16(3, issued); v.setUint16(5, exp); p.set(dev, 7); v.setUint32(15, kid); p[19] = 0;
  const sig = await signKey(env, p);
  const raw = b32encode(new Uint8Array([...p, ...sig]));
  const key = "RRS-" + raw.match(/.{1,5}/g).join("-");
  const expires = pid === 3 ? null : EPOCH + exp * 86400;
  await env.DB.prepare("INSERT INTO keys (kid, plan, device, expires, customer, created, key) VALUES (?,?,?,?,?,?,?)")
    .bind(kid, pid, hex(dev), expires, customer || "", now(), key).run();
  return { key, kid, expires };
}

async function route(req, env) {
  const url = new URL(req.url);
  const p = url.pathname;
  const limit = Number(env.TRIAL_LIMIT || 3);
  const body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
  if (p === "/" ) return json({ ok: true, service: "Rebels Revolt Shorts license server" });
  if (p === "/v1/health") return json({ ok: true, trial_limit: limit, can_issue: String(env.SIGNING_KEY || "").trim().length === 64 });
  if (p === "/admin") return new Response(ADMIN_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });

  if (p === "/v1/trial" && req.method === "POST") {
    const dev = devHex(body.device);
    const slot = String(body.slot || "").slice(0, 40);
    const rows = (await env.DB.prepare("SELECT slot FROM trial WHERE device=?").bind(dev).all()).results || [];
    const used = new Set(rows.map((r) => r.slot));
    if (used.has(slot)) return json({ ok: true, used: used.size, limit });
    if (used.size >= limit) return json({ ok: false, used: used.size, limit, message: `This PC already used its ${limit} free Shorts.` });
    await env.DB.prepare("INSERT OR IGNORE INTO trial (device, slot, at) VALUES (?,?,?)").bind(dev, slot, now()).run();
    return json({ ok: true, used: used.size + 1, limit });
  }
  if (p === "/v1/check" && req.method === "POST") {
    const dev = devHex(body.device);
    const kid = Number(body.kid) >>> 0;
    const k = await env.DB.prepare("SELECT revoked FROM keys WHERE kid=?").bind(kid).first();
    if (k && k.revoked) return json({ ok: false, status: "revoked", message: "This key was cancelled. Contact support." });
    const t = now();
    await env.DB.prepare("INSERT INTO seen (kid, device, first, last, version) VALUES (?,?,?,?,?) ON CONFLICT(kid, device) DO UPDATE SET last=excluded.last, version=excluded.version")
      .bind(kid, dev, t, t, String(body.version || "")).run();
    return json({ ok: true, status: "active" });
  }
  if (p.startsWith("/v1/admin/")) {
    if (!isAdmin(req, env)) return json({ detail: "wrong admin token" }, 401);
    if (p === "/v1/admin/issue") return json(await makeKey(env, body.plan || "monthly", body.device_id, body.days, body.customer));
    if (p === "/v1/admin/revoke") {
      const kid = Number(body.kid) >>> 0;
      const r = body.revoked === false ? 0 : 1;
      await env.DB.prepare("INSERT INTO keys (kid, customer, created, revoked) VALUES (?,?,?,?) ON CONFLICT(kid) DO UPDATE SET revoked=excluded.revoked")
        .bind(kid, String(body.customer || ""), now(), r).run();
      return json({ ok: true });
    }
    if (p === "/v1/admin/list") {
      const keys = (await env.DB.prepare("SELECT * FROM keys ORDER BY created DESC LIMIT 500").all()).results || [];
      const seen = (await env.DB.prepare("SELECT * FROM seen ORDER BY last DESC LIMIT 500").all()).results || [];
      const tp = await env.DB.prepare("SELECT COUNT(DISTINCT device) AS n FROM trial").first();
      return json({ keys, activations: seen, trial_pcs: tp ? tp.n : 0, can_issue: String(env.SIGNING_KEY || "").trim().length === 64 });
    }
  }
  return json({ detail: "not found" }, 404);
}

export default {
  async fetch(req, env) {
    try { return await route(req, env); }
    catch (e) { return json({ detail: e.message || "error" }, e.code || 500); }
  },
};

const ADMIN_HTML = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
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
document.getElementById('sum').textContent=\`\${j.keys.length} keys · \${j.trial_pcs} PCs tried the app\`+(j.can_issue?'':' · (set SIGNING_KEY to make keys here)');
document.getElementById('keys').innerHTML='<tr><th>Customer</th><th>Plan</th><th>Ends</th><th>Key id</th><th></th></tr>'+j.keys.map(k=>\`<tr><td>\${k.customer||''}</td><td>\${({1:'Monthly',2:'Annual',3:'Lifetime'})[k.plan]||''}</td><td>\${d(k.expires)}</td><td>\${(k.kid>>>0).toString(16)}</td><td><button onclick="rev(\${k.kid},\${k.revoked?0:1})">\${k.revoked?'Restore':'Revoke'}</button></td></tr>\`).join('');
document.getElementById('seen').innerHTML='<tr><th>Key id</th><th>PC</th><th>First</th><th>Last seen</th><th>Version</th></tr>'+j.activations.map(s=>\`<tr><td>\${(s.kid>>>0).toString(16)}</td><td>\${s.device}</td><td>\${d(s.first)}</td><td>\${d(s.last)}</td><td>\${s.version||''}</td></tr>\`).join('');
}catch(e){alert(e.message)}}
async function issue(){try{const j=await call('/v1/admin/issue',{customer:cust.value,device_id:dev.value,plan:plan.value,days:days.value?+days.value:null});out.textContent=j.key;out.classList.remove('muted');navigator.clipboard&&navigator.clipboard.writeText(j.key);load()}catch(e){alert(e.message)}}
async function revHex(){const k=parseInt(rkid.value.trim(),16);if(isNaN(k))return alert('key id is 8 letters/numbers');await call('/v1/admin/revoke',{kid:k,revoked:true,customer:rcust.value});load()}
async function rev(kid,on){if(on&&!confirm('Revoke this key? The app stops accepting it at its next check.'))return;await call('/v1/admin/revoke',{kid,revoked:!!on});load()}
</script></body></html>`;
