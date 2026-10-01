import { useMemo, useState } from "react";
import { api, mediaUrl } from "../lib/api";
import { go, saveSettings, toast, useStore } from "../lib/store";
import type { ExportTask, Job } from "../lib/types";
import { Icon } from "../components/Icon";
import { Btn, Field, IconBtn, Progress, Seg, Select, Slider, Text, Toggle, timeAgo } from "../components/ui";
import { YouTubeSignIn } from "./SignIn";

export const LANGS: [string, string][] = [["auto", "Auto-detect"], ["ur", "Urdu"], ["en", "English"], ["hi", "Hindi"], ["pa", "Punjabi"],
  ["ar", "Arabic"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["tr", "Turkish"], ["id", "Indonesian"],
  ["pt", "Portuguese"], ["ru", "Russian"], ["bn", "Bengali"]];
export const CAPTION_LANGS: [string, string][] = [["roman", "Roman Urdu / English"], ["en", "English (translate)"],
  ["auto", "Original script"], ["ur", "Urdu script"], ["hi", "Hindi script"]];
export const QUALITY: [string, string][] = [["auto", "Auto (turbo on NVIDIA GPU, small on CPU)"], ["tiny", "Fastest (tiny)"], ["base", "Fast (base)"], ["small", "Balanced (small)"],
  ["medium", "Accurate (medium)"], ["large-v3-turbo", "Best (large-v3 turbo, GPU)"]];
export const PICKERS: [string, string][] = [["gemini", "Gemini AI editor (free key)"], ["openai", "ChatGPT AI editor"], ["claude", "Claude AI editor"], ["local", "Offline director"]];
const LENGTHS: [string, string][] = [["short", "15–30s"], ["mid", "20–45s"], ["long", "30–59s"], ["max", "45–90s"]];
const LEN_VALUES: Record<string, [number, number]> = { short: [15, 30], mid: [20, 45], long: [30, 59], max: [45, 90] };

function lenKey(a: number, b: number) {
  return Object.entries(LEN_VALUES).find(([, v]) => v[0] === a && v[1] === b)?.[0] || "custom";
}

export function CreatePage() {
  const s = useStore((x) => x.settings);
  const cat = useStore((x) => x.catalog);
  const st = useStore((x) => x.status);
  const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [adv, setAdv] = useState(false);
  const [login, setLogin] = useState(false);
  const set = (patch: Record<string, any>) => saveSettings(patch).catch((e) => toast(e.message, "error"));

  const packKey = Object.entries(cat?.packs || {}).find(([, p]) =>
    Object.entries(p.set).every(([k, v]) => s[k] === v))?.[0];

  const start = async () => {
    if (!src.trim()) { toast("Paste a YouTube link or choose a video file first.", "info"); return; }
    setBusy(true);
    try {
      await api("/api/analyze", { sources: src });
      setSrc("");
      toast("Finding the best moments… you can keep working.", "ok");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const pickFile = async () => {
    try {
      const files = await api<string[]>("/api/pick", { kind: "video", multi: true });
      if (files.length) setSrc((x) => (x.trim() ? x.trim() + "\n" : "") + files.join("\n"));
    } catch (e: any) { toast(e.message, "error"); }
  };
  const lk = lenKey(s.min_duration, s.max_duration);

  return (
    <div className="page create">
      <div className="create-main">
        <Steps active={1} />
        <section className="hero glass depth">
          <div className="hero-text">
            <h1>Turn any video into <span className="grad">viral Shorts</span></h1>
            <p>Paste a YouTube video, playlist or channel link, or drop in your own file. The AI finds the best moments, scores them and makes them ready to post.</p>
          </div>
          <div className="paste"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); const t = e.dataTransfer.getData("text"); if (t) setSrc(t); }}>
            <Icon name="link" size={20} />
            <textarea value={src} rows={Math.min(4, Math.max(1, src.split("\n").length))} placeholder="https://youtube.com/watch?v=…   (one or more links)"
              onChange={(e) => setSrc(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start(); } }} />
            <Btn kind="ghost" icon="folder" onClick={pickFile}>File</Btn>
            <Btn kind="glow" icon="spark" busy={busy} onClick={start}>Get clips</Btn>
          </div>
          <div className="hero-foot">
            {st?.yt_login ? <span className="ok-txt"><Icon name="check" size={14} /> Signed in to YouTube</span>
              : <button className="linkbtn" onClick={() => setLogin(true)}><Icon name="user" size={14} /> Sign in to YouTube (avoids “not a bot” blocks)</button>}
            <span className="sep" />
            <Toggle on={!!s.auto_export} onChange={(v) => set({ auto_export: v })} label="Export all clips automatically" />
            <Toggle on={!!s.auto_upload} onChange={(v) => set({ auto_upload: v })} label="Auto-post" hint="Posts every exported Short to your connected accounts with random gaps (Publish page)." />
          </div>
        </section>

        <section className="packs">
          <div className="sec-head"><h2>Look</h2><span className="muted">one tap, change anything later in the editor</span></div>
          <div className="pack-row">
            {Object.entries(cat?.packs || {}).map(([k, p]) => (
              <button key={k} className={`pack glass-2 ${packKey === k ? "on" : ""}`} onClick={() => set(p.set)}>
                <PackSwatch pack={k} set={p.set} />
                <b>{p.name}</b><small>{p.desc}</small>
                {packKey === k && <span className="pack-check"><Icon name="check" size={13} /></span>}
              </button>
            ))}
          </div>
        </section>

        <section className="quick glass depth">
          <Field label="Clips per video"><Slider value={s.shorts_per_video ?? 5} min={1} max={15} onChange={(v) => set({ shorts_per_video: v })} /></Field>
          <Field label="Length"><Seg value={lk} options={LENGTHS} onChange={(v) => { const [a, b] = LEN_VALUES[v]; set({ min_duration: a, max_duration: b }); }} /></Field>
          <Field label="Captions"><Select value={s.caption_lang || "roman"} options={CAPTION_LANGS} onChange={(v) => set({ caption_lang: v })} /></Field>
          <Field label="Sound effects"><Seg value={s.sfx_level || "auto"} options={[["auto", "Auto"], ["off", "Off"], ["subtle", "Subtle"], ["medium", "Energetic"], ["high", "Max"]]} onChange={(v) => set({ sfx_level: v })} /></Field>
          <Field label="Sound pack" hint="Auto picks the pack from each clip's vibe (hype, emotional, funny…)."><Select value={s.sfx_pack || "auto"} options={Object.entries(cat?.sfx_packs || { auto: "Auto" }) as [string, string][]} onChange={(v) => set({ sfx_pack: v })} /></Field>
          <Field label="Music"><div className="row gap"><Toggle on={!!s.add_music} onChange={(v) => set({ add_music: v })} /><button className="linkbtn" onClick={() => go("music")}>Choose tracks</button></div></Field>
          <Field label="Story FX" hint="Pro storytelling: a dramatic pause before the payoff, a magazine title behind the speaker, motion-blur transitions and a film look.">
            <Seg value={s.story_fx || "auto"} options={[["off", "Off"], ["auto", "Auto"], ["strong", "Strong"]]} onChange={(v) => set({ story_fx: v })} /></Field>
          <button className="adv-btn" onClick={() => setAdv(!adv)}><Icon name="sliders" size={16} /> Advanced <Icon name={adv ? "down" : "right"} size={14} /></button>
        </section>
        {adv && (
          <section className="advanced glass depth">
            <Field label="Viral moment picker" hint="Gemini / Claude read the whole transcript like a human editor. Offline works without any key.">
              <Select value={s.clip_picker} options={PICKERS} onChange={(v) => set({ clip_picker: v })} /></Field>
            <Field label="Spoken language"><Select value={s.language} options={LANGS} onChange={(v) => set({ language: v })} /></Field>
            <Field label="Transcription quality"><Select value={s.whisper_model} options={QUALITY} onChange={(v) => set({ whisper_model: v })} /></Field>
            <Field label="Framing"><Select value={s.layout} options={Object.entries(cat?.layouts || {}) as [string, string][]} onChange={(v) => set({ layout: v })} /></Field>
            <Field label="Caption position"><Seg value={s.caption_position} options={[["upper", "Upper"], ["middle", "Center"], ["lower", "Lower"]]} onChange={(v) => set({ caption_position: v })} /></Field>
            <Field label="Custom length (seconds)"><div className="row gap"><Text value={String(s.min_duration)} onChange={(v) => set({ min_duration: Number(v) || 15 })} /><span className="muted">to</span><Text value={String(s.max_duration)} onChange={(v) => set({ max_duration: Number(v) || 59 })} /></div></Field>
            <Field label="End card text"><Text value={s.cta_text || ""} placeholder="Empty = no end card" onChange={(v) => set({ cta_text: v })} /></Field>
            <Field label="Watermark"><Text value={s.watermark || ""} placeholder="@yourchannel" onChange={(v) => set({ watermark: v })} /></Field>
            <div className="adv-toggles">
              <Toggle on={s.remove_pauses !== false} onChange={(v) => set({ remove_pauses: v })} label="Remove pauses" />
              <Toggle on={!!s.cold_open} onChange={(v) => set({ cold_open: v })} label="Teaser opening" />
              <Toggle on={!!s.hook_title} onChange={(v) => set({ hook_title: v })} label="Hook heading" />
              <Toggle on={!!s.progress_bar} onChange={(v) => set({ progress_bar: v })} label="Progress bar" />
              <Toggle on={s.fast_mode !== false} onChange={(v) => set({ fast_mode: v })} label="Fast mode" hint="Downloads audio first, then only the chosen parts in HD." />
              <Toggle on={!!s.use_gpu} onChange={(v) => set({ use_gpu: v })} label="NVIDIA GPU for speech" />
            </div>
            {(s.story_fx || "auto") !== "off" && <div className="adv-toggles">
              <Toggle on={s.story_pauses !== false} onChange={(v) => set({ story_pauses: v })} label="Dramatic beats" hint="Freeze-frame pause before the payoff line, with a riser and a hit" />
              <Toggle on={s.story_titles !== false} onChange={(v) => set({ story_titles: v })} label="Editorial title" hint="The hook as a magazine-style title (replaces the hook banner) + text during each beat" />
              <Toggle on={s.story_behind !== false} onChange={(v) => set({ story_behind: v })} label="Title behind speaker" hint="Automatic person cut-out so the big word sits behind them" />
              <Toggle on={s.story_transitions !== false} onChange={(v) => set({ story_transitions: v })} label="Streak transitions" />
              <Toggle on={s.story_textures !== false} onChange={(v) => set({ story_textures: v })} label="Film texture" hint="Grain, glow, light leaks" />
            </div>}
          </section>
        )}
        <Recent />
      </div>
      <Activity />
      {login && <YouTubeSignIn onClose={() => setLogin(false)} />}
    </div>
  );
}

function Recent() {
  const projects = useStore((x) => x.projects);
  if (!projects.length) return null;
  return (
    <section className="recent">
      <div className="sec-head"><h2>Recent videos</h2><button className="linkbtn" onClick={() => go("projects")}>All clips <Icon name="right" size={14} /></button></div>
      <div className="recent-row">
        {projects.slice(0, 6).map((p) => {
          const top = [...p.clips].sort((a, b) => b.score - a.score)[0];
          return (
            <button key={p.id} className="recent-card glass-2 depth-hover" onClick={() => go("projects", { openProject: p.id })}>
              {top?.poster ? <img src={mediaUrl(top.poster)} alt="" /> : <div className="ph" />}
              <div><b title={p.title}>{p.title}</b><small>{p.clips.length} clips · best {top?.score ?? "–"} · {timeAgo(p.created)}</small></div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PackSwatch({ pack, set }: { pack: string; set: Record<string, string> }) {
  const cat = useStore((x) => x.catalog);
  const cap = cat?.captions[set.caption_style];
  const word = pack === "mix" ? "MIX" : pack === "vibe" ? "VIBE" : "VIRAL";
  return (
    <div className={`swatch sw-${pack}`}>
      <span style={cap ? { fontFamily: `"${cap.font}"`, color: cap.active || cap.primary, WebkitTextStroke: `1.5px ${cap.outline}`,
        background: cap.box ? cap.outline : cap.hl_box, padding: cap.box || cap.hl_box ? "0 6px" : undefined,
        textShadow: cap.glow ? `0 0 8px ${cap.glow}` : "0 2px 0 rgba(0,0,0,.5)" } : { fontFamily: "Anton" }}>{cap?.upper === false ? "Viral" : word}</span>
    </div>
  );
}

export function Steps({ active }: { active: number }) {
  const items = ["Paste a link", "Pick clips", "Edit & post"];
  return (
    <div className="steps">
      {items.map((t, i) => (
        <div key={t} className={`step ${i + 1 === active ? "on" : i + 1 < active ? "done" : ""}`}>
          <span>{i + 1 < active ? <Icon name="check" size={13} /> : i + 1}</span>{t}
        </div>
      ))}
    </div>
  );
}

function Activity() {
  const jobsMap = useStore((s) => s.jobs);
  const expMap = useStore((s) => s.exports);
  const jobs = useMemo(() => Object.values(jobsMap).sort((a, b) => b.created - a.created), [jobsMap]);
  const exps = useMemo(() => Object.values(expMap).sort((a, b) => b.created - a.created), [expMap]);
  const logs = useStore((s) => s.logs);
  const [showLog, setShowLog] = useState(false);
  const active = exps.filter((e) => e.state === "running" || e.state === "queued");
  const doneExp = exps.filter((e) => e.state === "done").length;
  return (
    <aside className="activity glass depth">
      <div className="sec-head"><h2>Activity</h2>
        <div className="row">
          <IconBtn icon="log" title="Show log" active={showLog} onClick={() => setShowLog(!showLog)} />
          <IconBtn icon="trash" title="Clear finished" onClick={() => api("/api/jobs/clear", {})} />
        </div>
      </div>
      {showLog ? (
        <div className="logbox">{logs.slice(-250).map((l, i) => <div key={i}>{l.msg}</div>)}</div>
      ) : (
        <div className="act-list">
          {!jobs.length && !exps.length && <div className="act-empty"><Icon name="spark" size={26} /><p>Paste a link and press <b>Get clips</b>. Progress shows up here.</p></div>}
          {jobs.map((j) => <JobCard key={j.id} j={j} />)}
          {(active.length > 0 || doneExp > 0) && (
            <div className="act-exports">
              <div className="act-sub">Exports {doneExp > 0 && <span className="muted">· {doneExp} ready</span>}</div>
              {active.slice(0, 6).map((e) => <ExportRow key={e.id} e={e} />)}
              {active.length > 6 && <div className="muted small">+{active.length - 6} more waiting</div>}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function JobCard({ j }: { j: Job }) {
  const open = () => j.project && go("projects", { openProject: j.project });
  return (
    <div className={`job glass-2 st-${j.state}`}>
      <div className="job-top">
        <div className="job-title" title={j.source}>{j.title}</div>
        {(j.state === "running" || j.state === "queued") && <IconBtn icon="x" title="Cancel" onClick={() => api("/api/job/cancel", { id: j.id })} />}
      </div>
      {j.state === "running" && <Progress frac={j.frac} label={`${j.stage}${j.detail ? " · " + j.detail : ""}`} />}
      {j.state === "queued" && <div className="muted small">Waiting…</div>}
      {j.state === "done" && <div className="job-foot"><span className="ok-txt"><Icon name="check" size={14} /> {j.detail}</span><Btn small kind="primary" icon="grid" onClick={open}>Open clips</Btn></div>}
      {j.state === "failed" && <div className="job-err"><span>{j.error}</span>
        <div className="row gap">{j.login && <Btn small kind="primary" icon="user" onClick={() => go("settings")}>Sign in</Btn>}<Btn small icon="refresh" onClick={() => api("/api/job/retry", { id: j.id })}>Retry</Btn></div></div>}
      {j.state === "cancelled" && <div className="muted small">Cancelled · {timeAgo(j.created)}</div>}
    </div>
  );
}

function ExportRow({ e }: { e: ExportTask }) {
  return (
    <div className="exp-row">
      <span className="exp-title">{e.title}</span>
      {e.state === "running" ? <Progress frac={e.frac} tone="green" /> : <span className="muted small">queued</span>}
    </div>
  );
}
