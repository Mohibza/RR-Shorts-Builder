# Rebels Revolt Shorts: license server

Optional. Keys already work fully offline. The server adds three things:

- **Trial by PC:** each PC gets 3 free Shorts, counted on the server, so wiping the app's files doesn't reset them.
- **Cancel a key:** you can revoke a key (refund, chargeback, key shared online) and the app stops accepting it.
- **Web dashboard (`/admin`):** see every key and PC, make keys, and revoke keys.

## Deploy on Railway, step by step

1. **Make a new private GitHub repo** called `rr-license-server`. Upload only these files from this folder: `main.py`, `ed25519.py`, `requirements.txt`, `Procfile`, `railway.json`. Use GitHub's **Add file → Upload files** button.
2. Go to **railway.app** and sign in with GitHub. Choose **New Project → Deploy from GitHub repo**, then pick `rr-license-server`. The first deploy fails because no password is set yet. That's normal.
3. **Add storage** so the data survives restarts. Click the service → **Settings → Volumes → Add volume** → mount path `/data`.
4. **Variables** (service → Variables → New variable):
   - `ADMIN_TOKEN`: a long random password (20+ characters). You'll type it on the admin page.
   - `LICENSE_DB`: `/data/license.db`
   - `TRIAL_LIMIT`: `3`
   - `SIGNING_KEY` (optional): the 64-letter line from `%USERPROFILE%\RR-License-Keys\signing_key.txt`. Only add it if you want to make keys on the website. Without it, keep using the Key Maker.
5. **Get your address.** Go to service → **Settings → Networking → Generate Domain**. You get something like `https://rr-license-server-production.up.railway.app`.
6. **Test it.** Open `https://YOUR-ADDRESS/v1/health` in a browser. You should see `{"ok":true,...}`.
7. **Connect the app.** In the app folder, open `shortsforge\license_pub.py` and set `ONLINE_URL = "https://YOUR-ADDRESS"`. Then rebuild the installer with `build_installer.bat`.
8. **Admin page.** Open `https://YOUR-ADDRESS/admin` and paste your `ADMIN_TOKEN`:
   - **Make a key:** paste the customer's Device ID, choose the plan, click **Make key**. The key is copied to your clipboard.
   - **Revoke a key:** use **Revoke** in the list, or type a Key Maker key id (from `issued_keys.csv`).

## What the app does with the server

- **Trial Short:** before making a free Short, the app asks the server whether this PC has free Shorts left. If the server can't be reached, the offline counter decides.
- **Subscribers:** every 12 hours the app checks that the key isn't cancelled. Without internet it keeps working for up to 7 days.
- **Server down:** the app keeps working with the offline rules.

## Test on your own PC first (optional)

```
pip install -r requirements.txt
set ADMIN_TOKEN=my-long-test-password
python -m uvicorn main:app --port 8811
```

Then run the app with `set RRSF_LICENSE_SERVER=http://127.0.0.1:8811` and `set RRSF_LICENSE_ENFORCE=1` in the same window before `run.bat`.
