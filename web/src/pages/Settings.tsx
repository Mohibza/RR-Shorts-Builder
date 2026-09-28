import { useEffect, useState } from "react";
import { api, get } from "../lib/api";
import { saveSettings, toast, useStore } from "../lib/store";
import { Icon } from "../components/Icon";
import { Btn, Chip, Field, IconBtn, Seg, Select, Text, Toggle, timeAgo } from "../components/ui";
import { YouTubeSignIn } from "./SignIn";
import { PICKERS, QUALITY } from "./Create";

export function SettingsPage() {
  const s = useStore((x) => x.settings);
  const st = useStore((x) => x.status);
  const [login, setLogin] = useState(false);
  const [sec, setSec] = useState("ai");
  const set = (patch: Record<string, any>) => saveSettings(patch).catch((e) => toast(e.message, "error"));
  const SECS: [string, string, string][] = [["ai", "AI editor", "spark"], ["youtube", "YouTube sign-in", "youtube"],
    ["watch", "Channel auto-watch", "eye"], ["output", "Output & speed", "zap"], ...(st?.license ? [["license", "License", "key"] as [string, string, string]] : []),
    ["about", "About", "info"]];
  return (
    <div className="page settings">
      <nav className="set-nav glass depth">
        <h1>Settings</h1>
        {SECS.map(([k, l, ic]) => <button key={k} className={`nav ${sec === k ? "on" : ""}`} onClick={() => setSec(k)}><Icon name={ic} size={17} />{l}</button>)}
      </nav>
      <div className="set-body glass depth scroll">
        {sec === "ai" && <AiSection s={s} set={set} />}
        {sec === "youtube" && (
          <section>
            <h2>YouTube download sign-in</h2>
            <p className="muted">YouTube sometimes blocks downloads with “Sign in to confirm you're not a bot”. Signing in once in the app's own browser fixes that; the app keeps the login fresh by itself.</p>
            <div className="status-line">{st?.yt_login ? <Chip tone="green" icon="check">Signed in</Chip> : <Chip tone="amber" icon="alert">Not signed in</Chip>}</div>
            <div className="row gap">
              <Btn kind="primary" icon="user" onClick={() => setLogin(true)}>{st?.yt_login ? "Sign in again" : "Sign in to YouTube"}</Btn>
              <Btn icon="upload" onClick={() => api("/api/youtube/import", {}).then((ok) => toast(ok ? "Imported" : "No YouTube login in that file", ok ? "ok" : "error")).catch((e) => toast(e.message, "error"))}>Import cookies.txt</Btn>
              {st?.yt_login && <Btn kind="ghost" icon="x" onClick={() => api("/api/youtube/signout", {})}>Sign out</Btn>}
            </div>
            <Field label="Fallback: read cookies from a browser" hint="Firefox works best; close Chrome/Edge first.">
              <Select value={s.cookies_browser || ""} options={[["", "Don't use browser cookies"], ["chrome", "Chrome"], ["edge", "Edge"], ["firefox", "Firefox"], ["brave", "Brave"]]} onChange={(v) => set({ cookies_browser: v })} />
            </Field>
          </section>
        )}
        {sec === "watch" && <WatchSection s={s} set={set} />}
        {sec === "output" && (
          <section>
            <h2>Output & speed</h2>
            <Field label="Save Shorts to"><div className="row gap"><Text value={s.output_dir || ""} onChange={(v) => set({ output_dir: v })} />
              <Btn icon="folder" onClick={async () => { const r = await api<string[]>("/api/pick", { kind: "folder" }); if (r[0]) set({ output_dir: r[0] }); }}>Choose</Btn>
              <IconBtn icon="external" title="Open" onClick={() => api("/api/open", { path: "output" })} /></div></Field>
            <Field label="Music folder"><div className="row gap"><Text value={s.music_dir || ""} onChange={(v) => set({ music_dir: v })} />
              <Btn icon="folder" onClick={async () => { const r = await api<string[]>("/api/pick", { kind: "folder" }); if (r[0]) set({ music_dir: r[0] }); }}>Choose</Btn>
              <IconBtn icon="external" title="Open" onClick={() => api("/api/open", { path: "music" })} /></div></Field>
            <div className="two">
              <Field label="Export speed"><Seg value={s.encode_speed || "fast"} options={[["fast", "Fast (recommended)"], ["quality", "Max quality"]]} onChange={(v) => set({ encode_speed: v })} /></Field>
              <Field label="Video encoder"><Select value={s.encoder || "auto"} options={[["auto", "Auto (GPU if available)"], ["libx264", "CPU x264"], ["h264_nvenc", "NVIDIA NVENC"], ["h264_qsv", "Intel QuickSync"], ["h264_amf", "AMD AMF"]]} onChange={(v) => set({ encoder: v })} /></Field>
              <Field label="Download quality"><Select value={s.source_quality || 1440} options={[[1080, "1080p (faster)"], [1440, "1440p (sharper, recommended)"], [2160, "4K (big download)"]]} onChange={(v) => set({ source_quality: v })} /></Field>
              <Field label="Frame rate"><Select value={s.fps || 0} options={[[0, "Match source (up to 60)"], [30, "30 fps"], [60, "60 fps"], [24, "24 fps"]]} onChange={(v) => set({ fps: v })} /></Field>
              <Field label="Quality (lower = sharper)"><Select value={s.quality_crf || 18} options={[[16, "16 · very high"], [18, "18 · HD (recommended)"], [20, "20 · good"], [23, "23 · small files"]]} onChange={(v) => set({ quality_crf: v })} /></Field>
              <Field label="Speech recognition"><Select value={s.whisper_model} options={QUALITY} onChange={(v) => set({ whisper_model: v })} /></Field>
            </div>
            <div className="adv-toggles">
              <Toggle on={s.fast_mode !== false} onChange={(v) => set({ fast_mode: v })} label="Fast mode" hint="YouTube links: audio first, then only the chosen parts in HD" />
              <Toggle on={!!s.use_gpu} onChange={(v) => set({ use_gpu: v })} label="NVIDIA GPU for speech" hint="Auto-falls back to CPU if CUDA isn't installed" />
              <Toggle on={!!s.loudnorm} onChange={(v) => set({ loudnorm: v })} label="Even out loudness" />
            </div>
            <Field label="FFmpeg path (empty = automatic)"><Text value={s.ffmpeg_path || ""} onChange={(v) => set({ ffmpeg_path: v })} placeholder="auto" /></Field>
            <p className="muted small">{st?.ffmpeg ? "✓ FFmpeg found" : "✗ FFmpeg not found. Run setup.bat or set the path above."} · {st?.fonts_missing ? `${st.fonts_missing} caption fonts still downloading` : "✓ Caption fonts ready"}</p>
          </section>
        )}
        {sec === "license" && <LicenseSection />}
        {sec === "about" && (
          <section>
            <h2>Rebels Revolt Shorts</h2>
            <p>Version {st?.version}</p>
            <p className="muted">Everything runs on this PC: downloads, speech recognition, editing and rendering. Your accounts' logins stay in the app's private browser profiles and an encrypted file on this computer.</p>
            <p className="muted">Prefer the previous interface? Start the app with <code>run.bat --classic</code>.</p>
          </section>
        )}
      </div>
      {login && <YouTubeSignIn onClose={() => setLogin(false)} />}
    </div>
  );
}

function AiSection({ s, set }: { s: any; set: (p: any) => void }) {
  const [gk, setGk] = useState(s.gemini_api_key || "");
  const [ck, setCk] = useState(s.anthropic_api_key || "");
  const [test, setTest] = useState<Record<string, string>>({});
  const run = async (provider: string, key: string, model: string) => {
    setTest((t) => ({ ...t, [provider]: "…" }));
    try { const r = await api<string>("/api/settings/test-key", { provider, key, model }); setTest((t) => ({ ...t, [provider]: "✓ " + r })); }
    catch (e: any) { setTest((t) => ({ ...t, [provider]: "✗ " + e.message })); }
  };
  return (
    <section>
      <h2>AI editor</h2>
      <p className="muted">The AI reads the whole transcript like a human editor and picks the moments most likely to go viral, then writes hooks, titles and tags. Without a key the offline director still works.</p>
      <Field label="Who picks the clips"><Select value={s.clip_picker} options={PICKERS} onChange={(v) => set({ clip_picker: v })} /></Field>
      <div className="key-card glass-2">
        <div className="row gap"><b>Gemini</b><Chip tone="green">free key</Chip><a className="linkbtn small" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">Get a free key</a></div>
        <div className="row gap"><Text type="password" value={gk} onChange={setGk} placeholder="Paste Gemini API key" />
          <Btn onClick={() => { set({ gemini_api_key: gk.trim() }); run("gemini", gk.trim(), s.gemini_model || "auto"); }}>Save & test</Btn></div>
        {test.gemini && <p className="small">{test.gemini}</p>}
        <Field label="Model"><Text value={s.gemini_model || "auto"} onChange={(v) => set({ gemini_model: v || "auto" })} /></Field>
      </div>
      <div className="key-card glass-2">
        <div className="row gap"><b>Claude</b><a className="linkbtn small" href="https://console.anthropic.com/" target="_blank" rel="noreferrer">Get a key</a></div>
        <div className="row gap"><Text type="password" value={ck} onChange={setCk} placeholder="Paste Anthropic API key" />
          <Btn onClick={() => { set({ anthropic_api_key: ck.trim() }); run("claude", ck.trim(), s.claude_model); }}>Save & test</Btn></div>
        {test.claude && <p className="small">{test.claude}</p>}
        <Field label="Model"><Text value={s.claude_model || "claude-sonnet-5"} onChange={(v) => set({ claude_model: v })} /></Field>
      </div>
    </section>
  );
}

function WatchSection({ s, set }: { s: any; set: (p: any) => void }) {
  const [url, setUrl] = useState("");
  const [last, setLast] = useState(0);
  useEffect(() => { get<any>("/api/watch").then((r) => setLast(r.last)).catch(() => {}); }, []);
  const channels: string[] = s.watch_channels || [];
  const add = () => {
    const u = url.trim();
    if (!u) return;
    if (!/youtube\.com|youtu\.be/.test(u)) { toast("Paste a YouTube channel link (youtube.com/@name)", "error"); return; }
    set({ watch_channels: [...new Set([...channels, u])] });
    setUrl("");
  };
  return (
    <section>
      <h2>Channel auto-watch</h2>
      <p className="muted">The app checks these channels for new uploads and turns each new video into Shorts by itself (and posts them if Auto-post is on).</p>
      <Toggle on={!!s.watch_enabled} onChange={(v) => set({ watch_enabled: v })} label={s.watch_enabled ? "Watching" : "Off"} />
      <div className="row gap"><Text value={url} onChange={setUrl} onEnter={add} placeholder="https://youtube.com/@channel" /><Btn icon="plus" onClick={add}>Add channel</Btn></div>
      <div className="chan-list">
        {channels.map((c) => (
          <div key={c} className="chan glass-2"><Icon name="youtube" size={16} /><span>{c}</span>
            <IconBtn icon="trash" danger title="Remove" onClick={() => { set({ watch_channels: channels.filter((x) => x !== c) }); api("/api/watch/forget", { channel: c }).catch(() => {}); }} /></div>
        ))}
        {!channels.length && <p className="muted small">No channels yet.</p>}
      </div>
      <div className="two">
        <Field label="Check every"><Select value={s.watch_interval_min || 60} options={[[15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"], [180, "3 hours"], [360, "6 hours"], [720, "12 hours"]]} onChange={(v) => set({ watch_interval_min: v })} /></Field>
        <Field label="When a channel is added, also make its newest"><Select value={s.watch_first_n ?? 1} options={[[0, "0 videos"], [1, "1 video"], [2, "2 videos"], [3, "3 videos"], [5, "5 videos"]]} onChange={(v) => set({ watch_first_n: v })} /></Field>
        <Field label="Max new videos per channel per check"><Select value={s.watch_per_check ?? 2} options={[[1, "1"], [2, "2"], [3, "3"], [5, "5"]]} onChange={(v) => set({ watch_per_check: v })} /></Field>
      </div>
      <div className="row gap"><Btn icon="refresh" onClick={() => api("/api/watch/check", {}).then((ok) => toast(ok ? "Checking channels…" : "Add a channel first (or a check is already running)", "info"))}>Check now</Btn>
        <span className="muted small">{last ? `Last checked ${timeAgo(last)}` : "Not checked yet"}</span></div>
    </section>
  );
}

function LicenseSection() {
  const [info, setInfo] = useState<any>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { get("/api/license").then(setInfo).catch(() => {}); }, []);
  return (
    <section>
      <h2>License</h2>
      {info && <p>Status: <b>{info.status}</b>{info.status !== "active" && info.left != null ? ` · ${info.left} free videos left` : ""}{info.plan ? ` · ${info.plan}` : ""}</p>}
      <div className="row gap"><Text value={key} onChange={setKey} placeholder="Paste your license key" />
        <Btn kind="primary" busy={busy} onClick={async () => { setBusy(true); try { setInfo(await api("/api/license/activate", { key })); toast("License activated", "ok"); } catch (e: any) { toast(e.message, "error"); } setBusy(false); }}>Activate</Btn></div>
    </section>
  );
}
