# RR Shorts Builder — AI Shorts Generator (Windows)

Paste a YouTube link and RR Shorts Builder turns the long video into ready-to-upload vertical Shorts, automatically:

1. **Downloads** the video (single video, playlist, or a whole channel — or use a local file / folder)
2. **Transcribes** the speech on your own PC with Whisper (word-level timing, 90+ languages, auto-detect)
3. **Finds the best moments**: an AI score based on hooks, questions, pace, energy, emotional words, topic focus and complete sentences. It skips intros, sponsor reads and "subscribe" segments.
4. **Reframes to 9:16**: face-follow smart crop, blurred-background fit, center crop, or split screen
5. **Styles every Short differently**: animated captions, hook titles, end cards, color grades, camera motion, intro effects, progress bar, watermark, part labels, and optional background music that gets quieter under speech
6. **Saves** MP4 + thumbnail + title/description/hashtags text for each Short, ready for you to review and upload

## What's new in 1.6: Autopilot

- **Sound effects all the way through**: besides the hook, zooms and captions, every jump cut and joined part gets a transition sound. Matching words get a matching sound ("paisa/money" → ding, "ghalti/never" → hit, "zabardast/secret" → pop), and questions get a riser. A light pattern-interrupt sound is added whenever it would otherwise go quiet for too long (about 4.5 s on Energetic)
- **Viral title, description and tags** for every Short, written by Gemini/Claude in your caption language (offline fallback without a key). Edit them in Library → Upload details
- **Auto-watch channels** (Settings → Auto-watch): add channel links and turn it on. While the app is open it checks for new uploads, downloads them and builds Shorts automatically. When you add a channel it builds only its newest video(s), not the whole back-catalogue
- **Auto-upload** (new **Publish** page) through the official YouTube, Facebook Page and TikTok APIs:
  - Click *Setup keys* once per platform. It walks you through creating a free developer app and pasting its keys
  - Click *Connect* and sign in in your normal browser. Tokens are stored encrypted on this PC
  - Every new Short is queued for each connected account at a **random time**, with a configurable gap, daily cap and quiet hours. Uploads resume after network errors, retry automatically, and never post the same Short twice
  - Platform rules: YouTube keeps videos from un-audited API projects locked Private until you pass Google's free API audit (about 6 uploads/day on the free quota). TikTok posts "Only me" to a private account until TikTok audits your app. Facebook posts Reels to Pages (not personal profiles)
  - The app must be running to upload. Overdue uploads go out, still spaced, the next time it's open

## What's new in 1.5

- **New name: RR Shorts Builder**, with a new logo. Your settings, YouTube login, downloaded AI models, music and existing Shorts carry over automatically on first start
- **Find free music inside the app** (Music → *Find free music*):
  - **Openverse**: Creative-Commons music from Jamendo, ccMixter, Freesound, Wikimedia and more. No sign-up. Search, preview, then **Download & add**
  - **Jamendo**: 600k+ indie tracks. Needs a free Client ID (the button takes you there)
  - **Pixabay Music, YouTube Audio Library, Mixkit, Free Music Archive**: these sites have no public music API, so they open in the app's browser. Every track you download there lands in your library automatically
  - **Monetization-safe licences only** is on by default, which hides NonCommercial/NoDerivatives tracks
  - The licence is saved with every track. When a track needs credit (CC BY), the credit line is added to the Short's description automatically
- **Edit placement after building** (Library → **Placement**): drag or slide the captions, the hook heading (height, size, seconds on screen), the end card and the watermark (6 positions, size). You can also re-frame the video (left/right, up/down, zoom). A live preview frame updates in about a second. **Apply & Re-render** keeps the same music and cuts, and *Use these positions for new Shorts too* makes the change your default
- Re-renders are faster: face tracking is saved with each Short
- **Full-screen layout**: the app opens maximized and every page fits the screen without scrolling, from small laptops (1366×768) to large monitors. Settings uses a category list like pro apps, style previews resize to fit, and the placement editor opens full-size

## What's new in 1.4

- **Gemini AI editor (free)**: paste a free Gemini key (Settings → AI editor → "Get a free Gemini key"). Gemini picks the most viral moments and writes the hook headings, using about 2 requests per video, which is well inside the free daily limit. If the limit is reached or the key fails, the app uses the offline director automatically
- **Roman Urdu captions**: Urdu or Hindi speech is written as "kya kar rahe ho" (Caption text → Roman Urdu / English). English speech stays English
- **Hook heading**: the bold heading shows only in the first ~4 seconds. There are no Part labels any more
- **Music page**: search, preview and star tracks, choose "random" or "starred only", and set Auto level (recommended) or a manual volume. YouTube Studio's Audio Library opens in the app's browser, and downloads land in the app automatically
- **Share with a friend**: double-click `build_installer.bat`. It makes `installer_output\RR-Shorts-Builder-Setup-1.6.0.exe`, a single installer with Python, FFmpeg and everything else inside, so your friend needs nothing else. Windows may show "Windows protected your PC" because the app isn't code-signed; click *More info → Run anyway*

## Install (one time)

1. Unzip this folder somewhere, for example `C:\RRShortsBuilder`
2. Double-click **`setup.bat`**. It installs Python 3.12 if needed, the Python packages, FFmpeg and Deno (through winget), creates a desktop shortcut, and can add NVIDIA GPU support if you have an NVIDIA card
3. Start **RR Shorts Builder** from the desktop shortcut (or `run.bat`)

All caption fonts are bundled. On first use the app downloads the Whisper model you picked (Balanced is about 480 MB). This only happens once.

## YouTube sign-in (one time)

Sometimes YouTube asks downloaders to "Sign in to confirm you're not a bot". Google blocks sign-in inside app-embedded browsers, so RR Shorts Builder uses your real browser instead:

1. When the app asks (or from **Settings → YouTube account**), click **Sign in with Google Chrome** (Edge is used if Chrome isn't installed)
2. A normal Chrome window opens with a separate RR Shorts Builder profile. Your usual Chrome profile isn't touched. Sign in with your channel's account
3. When YouTube's home page appears, click **Finish** or close that window

After that, the app renews the login from that profile in the background whenever it gets old or YouTube asks again, and it retries the download automatically. The login stays on your PC. **Sign out** in Settings deletes the profile.

Another option is **Import cookies.txt**, exported from any browser with an extension such as "Get cookies.txt LOCALLY". For your own videos, downloading the originals from YouTube Studio and dropping the file or folder onto the app needs no sign-in at all.

## Using it

**Create page**
- Paste a link and press **Generate Shorts**. You can also drag a video file onto the input box
- For a channel link (`youtube.com/@name`) or a playlist, the app asks how many of the newest videos to process
- Options: how many Shorts per video, the length range (for example 20–59 s), caption style, framing, spoken language, transcription quality, caption position, end-card text, watermark (`@yourchannel`), hook title, progress bar, part labels and music
- You can queue several jobs. Each Short shows up under **Fresh Shorts** as soon as it finishes

**Library page**
- Preview every Short (it loops) and copy the suggested title and description with hashtags
- **Restyle**: choose different captions, hook, end card, grade, motion, intro, framing or caption position, then press **Re-render**. **Surprise me** picks a random look
- Edit the title text, and it is re-rendered as the on-screen hook

**Styles page**
- Live previews of all templates, made by the real render engine
- Turn templates in or out of the **random mix**, or choose **Always use** to lock in one style

**Settings page**: output folder, encoder (it uses your GPU encoder automatically when it can), quality, fps, NVIDIA Whisper, loudness normalization, music folder and volume, FFmpeg path, browser cookies for YouTube sign-in or age-restricted videos, font download and cache cleanup.

## How the viral moments are picked

- **Smart director (offline, default)**: splits the video into complete sections (question → answer, story → payoff, claim → proof). Each Short is one whole section that starts on a line that works without context and ends on the payoff. Long sections become a "best of" with the weak sentences cut out. Filler, rambling, intros, outros and sponsor reads are skipped
- **Teaser opening**: when a section's strongest line comes later, the Short can open on that line (a cold open) and then play the full moment
- **Claude AI editor (optional)**: add your Anthropic API key in Settings. Claude reads the whole transcript like a human editor and picks the most viral, self-contained moments, with cuts and teasers. It usually costs a few cents per video. If the key fails, the app uses the offline director automatically

## Engagement features

- **Big auto-fitting captions**: each caption line is measured with the real font and scaled to fill the screen width, the way viral Shorts look. Longer lines stack onto two big lines instead of shrinking
- **Caption text**: *Same as spoken*, *English* (translates Urdu or any other language to English), *Urdu · اردو* or *Hindi*
- **Sound effects** (Off / Subtle / Energetic / Maximum): whooshes, swipes, pops, keyboard typing, dings, bass hits, a camera shutter, glitches and risers, timed to hooks, zooms, captions, key words and the end card. Every sound is synthesized by the app itself, so there's no third-party audio to be claimed
- **Remove pauses**: jump-cuts dead air between sentences for fast pacing. Captions, zooms and face tracking are re-timed to match
- **Full-video link**: each Short's description includes a link back to the original long video

## Templates included

| Type | Templates |
|---|---|
| Captions (14) | Bold Highlight, Beast Mode, Karaoke Sweep, Boxed Pop, Clean Minimal, Neon Glow, Typewriter, One Word Punch, Comic Burst, Marker Hand, Slide Up, Highlight Box, Caption Card, Hollow Fill |
| Hook titles (8) | White Banner, Yellow Impact, Red Tag, Headline (whole clip), Glitch RGB, Comic Burst, Marker Note, Neon Sign |
| End cards (4) | Accent Pill, Pulse Text, Red Subscribe, Clean |
| Color grades (10) | Original, Vibrant, Cinematic Teal & Orange, Warm Sunset, Cool Blue, B&W Punch, Vintage Film, Moody Dark, Crisp HDR Pop, Soft Dream |
| Motion (7) | Static, Ken Burns, Slow Zoom In, Slow Zoom Out, Punch Zooms (eased, on key words), Breathing Pulse, Handheld Sway. All zooms are sub-pixel smooth, with no stepping or jitter |
| Intros (4) | None, White Flash, Fade From Black, Impact Shake |
| Framing (5) | Auto, Smart Crop (face-follow), Blurred Background Fit, Center Crop, Split Speaker + Full |

That is over 600,000 possible looks. In random mode each Short from a video gets a different combination.

Important words (numbers, key topics, strong words) are highlighted automatically. Urdu, Arabic and Hindi captions switch to Noto Nastaliq, Naskh or Devanagari fonts and are not converted to uppercase.

## Output

```
RR Shorts\<video title>\   (existing users keep their current output folder)
   01 - <hook title>.mp4     ← 1080x1920 HD, H.264 High (CRF 18), source fps up to 60, AAC 256k, -14 LUFS
   01 - <hook title>.jpg     ← thumbnail
   01 - <hook title>.txt     ← title, description, hashtags, source timestamp
   01 - <hook title>.sf.json ← used by the app for restyling
```

## Command line (optional)

```
.venv\Scripts\python -m shortsforge "https://youtube.com/watch?v=..." --count 5 --min 20 --max 59
.venv\Scripts\python -m shortsforge "D:\Videos\podcast.mp4" --style hormozi
```

## Build a standalone .exe (optional)

Run `build_installer.bat`. It creates `dist\RRShortsBuilder\RRShortsBuilder.exe` (FFmpeg bundled) and the single installer `installer_output\RR-Shorts-Builder-Setup-1.6.0.exe`.

## Troubleshooting

- **"Sign in to confirm you're not a bot"**: see *YouTube sign-in* above. Also keep the downloader updated with `.venv\Scripts\python -m pip install -U "yt-dlp[default]"`
- **FFmpeg missing**: run `setup.bat` again, or download it from gyan.dev and point Settings to `ffmpeg.exe`
- **Transcription is slow**: choose *Fast (base)* quality, or turn on the NVIDIA GPU option
- **Wrong language detected**: pick the spoken language instead of Auto-detect
- **Logs**: `%APPDATA%\RRShortsBuilder\logs\jobs.log` has every job, error and failed FFmpeg command (startup crashes: `rrshorts.log`). The Create page also has a *Show log* button

Only process videos you own or have the rights to reuse. Use royalty-free music, and check each track's licence (the Music page shows it). Even free tracks can occasionally get an automated Content ID claim; if one does, dispute it with the licence link.
