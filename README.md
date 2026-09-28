# Rebels Revolt Shorts — AI Shorts Generator (Windows)

Paste a YouTube link and Rebels Revolt Shorts turns the long video into ready-to-upload vertical Shorts, automatically:

1. **Downloads** the video (single video, playlist, or a whole channel — or use a local file / folder)
2. **Transcribes** the speech on your own PC with Whisper (word-level timing, 90+ languages, auto-detect)
3. **Finds the best moments**: an AI score based on hooks, questions, pace, energy, emotional words, topic focus and complete sentences. It skips intros, sponsor reads and "subscribe" segments.
4. **Reframes to 9:16**: face-follow smart crop, blurred-background fit, center crop, or split screen
5. **Styles every Short differently**: animated captions, hook titles, end cards, color grades, camera motion, intro effects, progress bar, watermark, part labels, and optional background music that gets quieter under speech
6. **Saves** MP4 + thumbnail + title/description/hashtags text for each Short, ready for you to review and upload

## What's new in 2.1.2

- **ChatGPT as the AI editor.** Paste an OpenAI API key in Settings → AI editor and pick "ChatGPT AI editor". Model "auto" uses the best GPT model your key can use (a fast "mini" model first). Gemini (free), Claude and the offline director still work as before.

## What's new in 2.1.1

- Music downloads and **Make music** work even when Windows protects your Music folder (Controlled folder access). The app switches to a folder it may write to (`%USERPROFILE%\RR Shorts\Music`) and copies your existing tracks over. The folder is shown on the Music page and can be changed in Settings.
- The app window shows the Rebels Revolt icon (title bar and taskbar) instead of Python's.
- `update_github.bat` uploads the latest version to your private GitHub repo in one click.

## What's new in 2.1

- **11 layouts** (Editor → Layout, shown as pictures):
  - Auto, Follow face, Center crop, Full + blur
  - **Full + black bars**, Face / frame, **Frame / face** (full video on top, speaker below)
  - **Two speakers** (side-by-side podcasts stacked: left person on top, right person below)
  - **4:5 zoom**, **Square**, **Framed card**

  The live preview draws every layout exactly like the export. There's also a new **Podcast Duo** look on the Create page.
- **Sound effects on Auto** (new default): each clip gets the intensity that fits it. Fast, loud talk gets more hits; calm clips get fewer.
  - Six new generated sounds: sparkle, zap, click, thud, bass drop and a downward swoosh. Words like "fast / jaldi" and "magic / perfect" get matching sounds.
  - **The editor preview now plays the sound effects and music**, so you hear the Short before exporting.
- **Music search fixed and expanded**: one search covers **ccMixter, Internet Archive, Wikimedia Commons and Openverse** (plus Jamendo if you add its free key).
  - If one service is down or refuses the search, the others still return results. No more raw error pages.
  - Only licences that allow monetized videos are shown, and credits are added to descriptions automatically.
- **Make music**: original tracks generated on your PC. Pick a mood (Lo-fi, Chill, Upbeat, Happy, Motivational, Epic, Trap, Dark) and a length. They contain nothing from anyone else's recordings, so there's no copyright claim. Works offline.
  - If background music is on and your music folder is empty, the app makes a track by itself.
- **Faster**:
  - Speech model **Auto** (new default) uses large-v3-turbo on an NVIDIA GPU and "small" on the CPU
  - About 40% less face-tracking work
  - 4 HD parts download at once

## What's new in 2.0: the new interface

A brand-new interface built around three steps: **Paste a link → Pick clips → Edit & post**.

- **Clips appear before anything is rendered.** After analysis each clip gets a **virality score (0–99)** with a breakdown (hook, emotion, value, flow) and plain reasons ("Opens with a question…", "Emotional words: …").
- **Instant live previews.** Hover a clip card, or open it in the editor, and it plays straight away with its captions, hook, colour, zoom, face-follow framing and pause cuts drawn live. No waiting for a render. **Exact frame** renders one frame with the real export engine when you want to check it pixel for pixel.
- **Editor**
  - **Transcript editing**: click a word to jump there; drag-select words and remove them from the Short; double-click a word to fix its spelling; **Remove filler words** in one click
  - **Trim** by dragging the ends of the timeline
  - **Style**: caption styles, hook heading, colour, motion, intro, end card
  - **Text**: AI hook options or your own; the upload title, description, hashtags and tags
  - **Audio**: auto music, no music or a chosen track, music level, sound-effect level
  - **Layout**: framing, zoom, move, and where the captions, hook, end card and watermark go
  - Undo (Ctrl+Z); edits save automatically
- **One-tap looks** on the Create page (Viral Bold, Beast Energy, Podcast Clean, Neon Night, Storyteller, Minimal, Mix). Everything else is under **Advanced**.
- **Export all automatically** (on by default) renders every clip in the background right after analysis. With **Auto-post** on, they're posted too.
- **Library, Publish, Music and Settings** are all in the new interface. It includes a posting queue (Upcoming / Posted / Failed) with Post now, Retry and Remove.
- Videos your preview window can't play (for example HEVC phone videos) get a small preview copy automatically.
- **Previous interface:** start with `run.bat --classic`.
- **Window:** the app opens in a native window (Microsoft Edge WebView2, built into Windows 10/11). If that component is missing, it opens in an Edge app window instead.

## What's new in 1.7: Rebels Revolt Shorts

- **New name: Rebels Revolt Shorts.** Your settings, logins and Shorts are kept
- **Fast mode (YouTube links)**: fetches only the audio first (seconds), transcribes and picks the best moments, then downloads **only those parts** in HD instead of the whole video. Each part is lined up with the audio to the millisecond, so captions stay in sync. If anything goes wrong it falls back to a full download automatically
- **Faster everywhere**:
  - Speech recognition uses an NVIDIA GPU automatically when one is present, and falls back to the CPU if the GPU drivers are missing
  - Faster encoder settings with the same look on phones (Settings can switch back to maximum quality)
  - 2–3 Shorts render at the same time on capable PCs
  - The log shows the total time for each video
- **Many accounts per platform**: every account you add gets its own private browser, e.g. 3 YouTube channels, 2 TikToks, a Facebook Page and Instagram. Posting to all of them is fully automatic; each has Open / Sign in again / pause / remove
- **Instagram Reels**: sign in to any account (automatic posting on instagram.com, full 9:16 frame), or use the official API for Business/Creator accounts

## What's new in 1.6: Autopilot

- **Sound effects all the way through**: besides the hook, zooms and captions, every jump cut and joined part gets a transition sound. Matching words get a matching sound ("paisa/money" → ding, "ghalti/never" → hit, "zabardast/secret" → pop), and questions get a riser. A light pattern-interrupt sound is added whenever it would otherwise go quiet for too long (about 4.5 s on Energetic)
- **Viral title, description and tags** for every Short, written by Gemini/Claude in your caption language (offline fallback without a key). Edit them in Library → Upload details
- **Auto-watch channels** (Settings → Auto-watch): add channel links and turn it on. While the app is open it checks for new uploads, downloads them and builds Shorts automatically. When you add a channel it builds only its newest video(s), not the whole back-catalogue
- **Auto-upload with direct sign-in** (new **Publish** page): click **Sign in** on YouTube, TikTok or Facebook and log in once in the app's own Chrome window. No developer keys are needed. The app then uploads through YouTube Studio, TikTok Studio and Facebook Reels in a minimized window, with title, description, tags, "not made for kids" and visibility filled in.
  - If a site asks for a security check, the app stops (it never bypasses those). Click **Open**, complete the check, then **Retry**
  - If a step fails, a screenshot is saved in `%APPDATA%\RRShortsBuilder\upload_errors`. Keep the sites in English in that browser. Website uploads depend on each site's layout, and automated website uploading is outside the platforms' official API route, so keep daily limits sensible
- **Optional: official APIs** (Publish → *API keys (advanced)*):
  - Click *Setup keys* once per platform. It walks you through creating a free developer app and pasting its keys
  - Click *Connect* and sign in in your normal browser. Tokens are stored encrypted on this PC
  - Every new Short is queued for each connected account at a **random time**, with a configurable gap, daily cap and quiet hours. Uploads resume after network errors, retry automatically, and never post the same Short twice
  - Platform rules: YouTube keeps videos from un-audited API projects locked Private until you pass Google's free API audit (about 6 uploads/day on the free quota). TikTok posts "Only me" to a private account until TikTok audits your app. Facebook posts Reels to Pages (not personal profiles)
  - The app must be running to upload. Overdue uploads go out, still spaced, the next time it's open

## What's new in 1.5

- **New name: Rebels Revolt Shorts**, with a new logo. Your settings, YouTube login, downloaded AI models, music and existing Shorts carry over automatically on first start
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
- **Share with a friend**: double-click `build_installer.bat`. It makes `installer_output\Rebels-Revolt-Shorts-Setup-1.6.0.exe`, a single installer with Python, FFmpeg and everything else inside, so your friend needs nothing else. Windows may show "Windows protected your PC" because the app isn't code-signed; click *More info → Run anyway*

## Install (one time)

1. Unzip this folder somewhere, for example `C:\RRShortsBuilder`
2. Double-click **`setup.bat`**. It installs Python 3.12 if needed, the Python packages, FFmpeg and Deno (through winget), creates a desktop shortcut, and can add NVIDIA GPU support if you have an NVIDIA card
3. Start **Rebels Revolt Shorts** from the desktop shortcut (or `run.bat`)

All caption fonts are bundled. On first use the app downloads the Whisper model you picked (Balanced is about 480 MB). This only happens once.

## YouTube sign-in (one time)

Sometimes YouTube asks downloaders to "Sign in to confirm you're not a bot". Google blocks sign-in inside app-embedded browsers, so Rebels Revolt Shorts uses your real browser instead:

1. When the app asks (or from **Settings → YouTube account**), click **Sign in with Google Chrome** (Edge is used if Chrome isn't installed)
2. A normal Chrome window opens with a separate Rebels Revolt Shorts profile. Your usual Chrome profile isn't touched. Sign in with your channel's account
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

## Trial & license

Installer builds you give to others include a 3-Short free trial per PC, tracked by a small license server (see
`license-server/`), not just a local counter, so reinstalling doesn't reset it. Users check their trial or activate
a paid key in **Settings → License**. `shortsforge/licensing.py` is the client side. **Before you share an installer,
deploy the server and set `LICENSE_SERVER_URL`** in that file (see `license-server/README.md`), otherwise the
installer can't verify trials. Your own copy run from the project folder skips the check (set
`RRSF_LICENSE_ENFORCE=1` to test it).

## Command line (optional)

```
.venv\Scripts\python -m shortsforge "https://youtube.com/watch?v=..." --count 5 --min 20 --max 59
.venv\Scripts\python -m shortsforge "D:\Videos\podcast.mp4" --style hormozi
```

## Build a standalone .exe (optional)

Run `build_installer.bat`. It creates `dist\RRShortsBuilder\RRShortsBuilder.exe` (FFmpeg bundled) and the single installer `installer_output\Rebels-Revolt-Shorts-Setup-1.6.0.exe`.

## Troubleshooting

- **"Sign in to confirm you're not a bot"**: see *YouTube sign-in* above. Also keep the downloader updated with `.venv\Scripts\python -m pip install -U "yt-dlp[default]"`
- **FFmpeg missing**: run `setup.bat` again, or download it from gyan.dev and point Settings to `ffmpeg.exe`
- **Transcription is slow**: choose *Fast (base)* quality, or turn on the NVIDIA GPU option
- **Wrong language detected**: pick the spoken language instead of Auto-detect
- **Logs**: `%APPDATA%\RRShortsBuilder\rrshorts.log`. The Create page also has a *Show log* button

Only process videos you own or have the rights to reuse. Use royalty-free music, and check each track's licence (the Music page shows it). Even free tracks can occasionally get an automated Content ID claim; if one does, dispute it with the licence link.
