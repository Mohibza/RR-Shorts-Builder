# RR Shorts Builder — license server

Small FastAPI service with one job: remember how many trial videos each *physical device* has used,
so deleting the app's settings folder or reinstalling doesn't give a free unlimited trial. It also
issues and checks paid license keys, ready for a payment webhook to call later.

It is intentionally boring — one file, SQLite, no queue, no external services — because a trial gate
gets light traffic and doesn't need more than that. You can grow into Postgres later without changing
the app; only `DB_PATH`/the connection string in `main.py` would need to change.

## Deploy to Railway (recommended — free tier is enough to start)

1. Push this `license-server/` folder to a **new, separate** GitHub repo (or a subfolder Railway can
   point at) — keep it out of the public app repo, since `LICENSE_SECRET` and `ADMIN_TOKEN` must stay
   private and app source code is usually public or at least widely shared.
2. On [railway.app](https://railway.app): **New Project → Deploy from GitHub repo** → pick this repo.
3. **Add a volume**: Project → your service → Settings → Volumes → mount a volume at `/data`. This is
   the step people skip and then wonder why every device's trial "resets" after a deploy — without a
   persistent volume, `license.db` lives on the container's ephemeral disk and is wiped on every
   redeploy/restart.
4. Set environment variables (Settings → Variables):
   - `LICENSE_SECRET` — a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`). This signs every token; changing it later invalidates every cached token until devices re-sync (harmless — `gate()` re-activates automatically).
   - `ADMIN_TOKEN` — another random string. Required in the `X-Admin-Token` header to mint paid license keys via `/v1/admin/keys`. Keep this one especially private — it's what your future payment webhook will use.
   - `LICENSE_DB` — `/data/license.db` (matches the volume mount from step 3).
   - `TRIAL_VIDEO_LIMIT` — `3` (default; set explicitly if you want to be sure).
   - Optional `ALLOWED_TRIAL_KEYS` — comma-separated trial keys to accept. Leave unset while there's only one build in the wild; set it later if you ever need to invalidate a leaked installer without touching every other copy.
5. Railway builds and deploys automatically. It gives you a URL like `https://rr-shorts-license.up.railway.app`.
6. Check it's alive: `curl https://<your-url>/v1/health` → `{"ok":true,"app":"RR Shorts Builder"}`.
7. Put that URL into the app: edit `shortsforge/licensing.py`'s `LICENSE_SERVER_URL` default (or set
   the `RRSF_LICENSE_SERVER` environment variable when building the installer) and rebuild.

Render.com and Fly.io work the same way — the only Railway-specific files here are `railway.json`
(safe to ignore on other hosts) and the general "attach a persistent volume" step, which every host
calls something slightly different.

## Local testing

```
pip install -r requirements.txt
LICENSE_SECRET=devsecret ADMIN_TOKEN=devadmin uvicorn main:app --reload --port 8811
```

Then point the app at it for testing: `set RRSF_LICENSE_SERVER=http://127.0.0.1:8811` (Windows) or
`export RRSF_LICENSE_SERVER=http://127.0.0.1:8811` before running `app.py`.

## Issuing a paid license key (manual, until payment is wired up)

```
curl -X POST https://<your-url>/v1/admin/keys \
  -H "X-Admin-Token: <your ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"plan":"lifetime","count":1,"max_activations":1,"note":"manual sale to X"}'
```

Returns `{"keys":["RRSF-XXXXXXXXXXXXXXXX"]}` — email that key to the buyer. They paste it into
Settings → License → Manage license, or the dialog that appears when their trial runs out.

`max_activations` is how many different PCs one key can activate on (1 for a single-seat license, more
for a family/team key). `expires_in_days` makes a subscription-style key instead of a lifetime one —
omit it for lifetime.

## When you wire up payment later

Whatever processor you pick (Lemon Squeezy / Paddle recommended for a Pakistan-based seller doing
cross-border card payments — both act as merchant of record and handle VAT/sales tax for you; Stripe
leaves that on you), its "payment succeeded" webhook should call `POST /v1/admin/keys` with the
`ADMIN_TOKEN` header (server-side only — never ship `ADMIN_TOKEN` inside the app) and email the
returned key to the buyer. Nothing else here needs to change.

## What this does and doesn't protect against

A local-only trial check (a counter file on the user's PC) is beaten by deleting that file. This
server closes that specific hole: the counter lives here, keyed to a hardware fingerprint, so
reinstalling the app doesn't reset it. It does not stop a determined, technically skilled person from
patching the compiled app to skip the check entirely — no client-side scheme does. That's a
deliberate, reasonable line to draw for a small indie product; don't spend more engineering on
DRM than the revenue at stake justifies.
