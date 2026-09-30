# License server on Cloudflare (free plan, always on)

Same features as the Railway version (trial per PC, revoke keys, `/admin` page, make keys). It runs on Cloudflare Workers + D1, and the app works with it unchanged.

Only two files are needed: `worker.js` and `schema.sql`.

## Step by step (everything in the browser, no installs)

1. **Sign up** at **dash.cloudflare.com** (free).

2. **Create the database**
   - Left menu **Storage & Databases → D1 SQL Database → Create**.
   - Name it `rr_license` and click **Create**.
   - Open it → **Console** tab. Paste everything from `schema.sql` and click **Execute**.

3. **Create the Worker**
   - Left menu **Compute (Workers) → Workers & Pages → Create → Start with Hello World**.
   - Name it `rr-license` and click **Deploy**.
   - Click **Edit code**. Delete everything in the editor, paste all of `worker.js`, and click **Deploy**.

4. **Connect the database**
   - Open the worker → **Settings → Bindings → Add → D1 database**.
   - Variable name `DB` (exactly this), database `rr_license`. Click **Add** / **Deploy**.

5. **Set the password (and optional signing key)**
   - Worker → **Settings → Variables and Secrets → Add**.
   - Type **Secret**, name `ADMIN_TOKEN`, value: a long password you make up (20+ characters).
   - Optional: type **Secret**, name `SIGNING_KEY`, value: the 64-character line from `%USERPROFILE%\RR-License-Keys\signing_key.txt`. Only add it if you want to make keys on the website.
   - Optional: type **Text**, name `TRIAL_LIMIT`, value `3`.
   - Click **Deploy**.

6. **Test it**
   - The worker's address is shown at the top of its page, like `https://rr-license.YOURNAME.workers.dev`.
   - Open that address with `/v1/health` on the end. You should see `{"ok":true,...}`.

7. **Connect the app**
   - Open `shortsforge\license_pub.py` and set `ONLINE_URL = "https://rr-license.YOURNAME.workers.dev"`.
   - Rebuild the installer with `build_installer.bat`.

8. **Use it:** open your address with `/admin` on the end and paste your `ADMIN_TOKEN`.

## Free plan limits

- Workers: about 100,000 requests a day.
- D1: 5 million rows read and 100,000 rows written a day, and 5 GB storage.
- The app calls the server roughly once per free Short and once every 12 hours per subscriber, so thousands of customers fit easily.
