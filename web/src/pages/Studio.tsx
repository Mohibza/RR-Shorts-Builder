// Studio: the screen recorder. Starting a recording closes the app completely (a tiny recorder process keeps
// running), your own shortcut keys control it, and stopping saves the video and reopens the app here.
import { useEffect, useMemo, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { go, saveSettings, toast, useStore } from "../lib/store";
import { Icon } from "../components/Icon";
import { Btn, Field, IconBtn, Modal, Seg, Select, Toggle, timeAgo } from "../components/ui";
import { fmt } from "../lib/timeline";

type Mon = { idx: number; x: number; y: number; w: number; h: number; primary: boolean; name: string };
type Devices = { monitors: Mon[]; windows: string[]; mics: string[]; cams: string[]; system_audio: boolean; encoder: string;
  windows_os: boolean; defaults: Record<string, string>; emergency_stop: string; active: { id: string; state: string } | null };
type Session = { id: string; name: string; state: string; created: number; duration: number; width: number; height: number; video: string;
  poster: string; system: string; webcam: string; mic: boolean; notes: string[]; markers: number[]; seen: boolean; recovered?: boolean; dir: string; method: string };

const KEYS: [string, string, string][] = [
  ["stop", "Stop and save", "Saves the video and reopens the app"],
  ["pause", "Pause", "Stops recording until you resume"],
  ["resume", "Resume", "Continues after a pause"],
  ["marker", "Mark this moment", "Drops a marker you can jump to later"],
  ["cancel", "Cancel and discard", "Throws the recording away"],
];
const pretty = (k: string) => (k || "").split("+").map((p) => (p.length <= 1 ? p.toUpperCase() : p[0].toUpperCase() + p.slice(1))).join(" + ");

/** Click, then press the keys you want. */
function HotkeyInput({ value, onChange, bad }: { value: string; onChange: (v: string) => void; bad?: string }) {
  const [listening, setListening] = useState(false);
  useEffect(() => {
    if (!listening) return;
    const down = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") { setListening(false); return; }
      if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
      const parts: string[] = [];
      if (e.ctrlKey) parts.push("ctrl");
      if (e.shiftKey) parts.push("shift");
      if (e.altKey) parts.push("alt");
      let k = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase().replace("arrow", "");
      if (e.code.startsWith("Key")) k = e.code.slice(3).toLowerCase();
      else if (e.code.startsWith("Digit")) k = e.code.slice(5);
      else if (k === " ") k = "space";
      parts.push(k);
      onChange(parts.join("+"));
      setListening(false);
    };
    window.addEventListener("keydown", down, true);
    return () => window.removeEventListener("keydown", down, true);
  }, [listening]);
  return (
    <div className="hk">
      <button className={`hk-btn ${listening ? "on" : ""} ${bad ? "bad" : ""}`} onClick={() => setListening(!listening)}>
        {listening ? "Press the keys… (Esc to cancel)" : pretty(value) || "Click to set"}
      </button>
      {bad && <small className="hk-bad">{bad}</small>}
    </div>
  );
}

export function StudioPage() {
  const s = useStore((x) => x.settings);
  const [dev, setDev] = useState<Devices | null>(null);
  const [list, setList] = useState<Session[] | null>(null);
  const [active, setActive] = useState<{ id: string; state: string } | null>(null);
  const [probs, setProbs] = useState<Record<string, string>>({});
  const [count, setCount] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [play, setPlay] = useState<Session | null>(null);
  const set = (patch: Record<string, any>) => saveSettings(patch).catch((e) => toast(e.message, "error"));
  const src = s.rec_source || { kind: "screen", monitor: 0 };
  const hk: Record<string, string> = { ...(dev?.defaults || {}), ...(s.rec_hotkeys || {}) };
  const load = () => get<{ sessions: Session[]; active: any }>("/api/record/sessions").then((r) => { setList(r.sessions); setActive(r.active); }).catch((e) => toast(e.message, "error"));
  useEffect(() => { get<Devices>("/api/record/devices").then(setDev).catch((e) => toast(e.message, "error")); load(); }, []);
  useEffect(() => { if (!active) return; const t = setInterval(load, 2000); return () => clearInterval(t); }, [active?.id]);
  useEffect(() => { api<Record<string, string>>("/api/record/keys", { hotkeys: s.rec_hotkeys || {} }).then(setProbs).catch(() => {}); }, [JSON.stringify(s.rec_hotkeys)]);
  // a recording that just finished: mark it as seen
  useEffect(() => { (list || []).filter((x) => !x.seen).forEach((x) => api("/api/record/update", { id: x.id, seen: true }).catch(() => {})); }, [list]);

  const clash = useMemo(() => Object.entries(hk).filter(([, v]) => ["ctrl+s", "ctrl+c", "ctrl+v", "ctrl+z", "ctrl+x", "ctrl+a", "ctrl+p", "ctrl+r", "ctrl+f", "ctrl+t", "ctrl+w", "ctrl+n"].includes(v)).map(([, v]) => pretty(v)), [JSON.stringify(hk)]);
  const begin = async () => {
    if (Object.keys(probs).length) { toast("Fix the shortcut keys marked in red first.", "error"); return; }
    const n = Math.max(0, Math.min(10, Number(s.rec_countdown ?? 3)));
    for (let i = n; i > 0; i--) { setCount(i); await new Promise((r) => setTimeout(r, 1000)); }
    setCount(0);
    try { await api("/api/record/start", { name }); }
    catch (e: any) { setCount(null); toast(e.message, "error"); }
  };
  const ctl = (cmd: string) => active && api("/api/record/control", { id: active.id, cmd }).then(() => setTimeout(load, 1200)).catch((e) => toast(e.message, "error"));
  const pickRegion = async () => {
    toast("Drag a rectangle on your screen. Press Esc to cancel.", "info");
    try { const r = await api<{ region: any }>("/api/record/region", {}); if (r.region) set({ rec_source: { ...src, kind: "region", region: r.region } }); }
    catch (e: any) { toast(e.message, "error"); }
  };

  if (count !== null) return (
    <div className="page center rec-count">
      {count > 0 ? <><b>{count}</b><p>Recording starts, and this window closes.</p></> : <><span className="spin big" /><p>Starting… the app is closing.</p></>}
      <p className="muted">Stop and save with <b>{pretty(hk.stop)}</b> · pause <b>{pretty(hk.pause)}</b> · resume <b>{pretty(hk.resume)}</b></p>
      {count > 0 && <Btn kind="ghost" onClick={() => location.reload()}>Cancel</Btn>}
    </div>
  );

  return (
    <div className="page studio">
      <div className="page-head"><div><h1>Studio</h1><span className="muted">Record your screen. The app closes while you record, so only the recorder is running.</span></div></div>
      {active && active.state === "finishing" && <div className="q-banner rec-active"><span className="spin" /> <span>Saving your recording… it appears below in a moment.</span></div>}
      {active && active.state !== "finishing" && <div className="q-banner warn rec-active"><span className="dot bad" /> <span>A recording is {active.state === "paused" ? "paused" : "running"} right now.</span>
        {active.state === "paused" ? <Btn small icon="play" onClick={() => ctl("resume")}>Resume</Btn> : <Btn small icon="pause" onClick={() => ctl("pause")}>Pause</Btn>}
        <Btn small kind="primary" icon="check" onClick={() => ctl("stop")}>Stop and save</Btn></div>}
      <div className="studio-grid">
        <section className="glass depth rec-setup">
          <div className="sec-head"><h2><Icon name="frame" size={18} /> What to record</h2></div>
          <Seg value={src.kind || "screen"} options={[["screen", "Full screen"], ["region", "Part of the screen"], ["window", "One window"]]} onChange={(v) => set({ rec_source: { ...src, kind: v } })} />
          {(src.kind || "screen") !== "window" && (dev?.monitors.length || 0) > 1 && <Field label="Screen"><Select value={Number(src.monitor || 0)} options={(dev?.monitors || []).map((m) => [m.idx, m.name] as [number, string])} onChange={(v) => set({ rec_source: { ...src, monitor: v } })} /></Field>}
          {src.kind === "region" && <div className="row gap"><Btn small icon="frame" onClick={pickRegion}>{src.region ? "Change region" : "Pick region"}</Btn>{src.region && <span className="muted small">{src.region.w}×{src.region.h} at {src.region.x}, {src.region.y}</span>}</div>}
          {src.kind === "window" && <Field label="Window" hint="The window must stay visible and not be minimised while recording."><Select value={src.title || ""} options={[["", "Choose a window…"], ...(dev?.windows || []).map((w) => [w, w.slice(0, 60)] as [string, string])]} onChange={(v) => set({ rec_source: { ...src, title: v } })} /></Field>}
          <div className="two">
            <Field label="Smoothness"><Seg value={String(s.rec_fps || 30)} options={[["30", "30 fps"], ["60", "60 fps"]]} onChange={(v) => set({ rec_fps: Number(v) })} /></Field>
            <Field label="Quality"><Seg value={s.rec_quality || "high"} options={[["high", "High"], ["medium", "Medium"], ["small", "Small file"]]} onChange={(v) => set({ rec_quality: v })} /></Field>
          </div>
          <div className="sec-head"><h2><Icon name="volume" size={18} /> Sound and camera</h2></div>
          <Field label="Microphone"><Select value={s.rec_mic ?? "default"} options={[["default", dev?.mics.length ? `Default (${dev.mics[0].slice(0, 40)})` : "Default"], ["", "No microphone"], ...(dev?.mics || []).map((m) => [m, m.slice(0, 60)] as [string, string])]} onChange={(v) => set({ rec_mic: v })} /></Field>
          <Toggle on={!!s.rec_system_audio && !!dev?.system_audio} onChange={(v) => set({ rec_system_audio: v })} label="Record the PC's own sound" hint="What you hear from the speakers, as a separate track" />
          {dev && !dev.system_audio && <p className="muted small">PC sound needs one small component. It installs by itself the next time you open the app while online. Screen and microphone recording work without it.</p>}
          <Field label="Webcam (separate layer you can move later)"><Select value={s.rec_webcam || ""} options={[["", "No webcam"], ...(dev?.cams || []).map((c) => [c, c.slice(0, 60)] as [string, string])]} onChange={(v) => set({ rec_webcam: v })} /></Field>
          <div className="adv-toggles">
            <Toggle on={s.rec_cursor !== false} onChange={(v) => set({ rec_cursor: v })} label="Show the mouse cursor" />
            <Toggle on={s.rec_track_input !== false} onChange={(v) => set({ rec_track_input: v })} label="Track clicks for auto-zoom" hint="Logs where you click and when you type (never which keys), so the editor can zoom to the action" />
          </div>
        </section>
        <section className="glass depth rec-keys">
          <div className="sec-head"><h2><Icon name="key" size={18} /> Your shortcut keys</h2></div>
          <p className="muted small">There is no window while recording. These keys are the controls. Click a box, then press the keys you want.</p>
          {KEYS.map(([k, label, hint]) => (
            <div className="hk-row" key={k} title={hint}><div><b>{label}</b><small className="muted">{hint}</small></div>
              <HotkeyInput value={hk[k] || ""} bad={probs[k]} onChange={(v) => set({ rec_hotkeys: { ...hk, [k]: v } })} /></div>
          ))}
          {clash.length > 0 && <div className="rec-warn"><Icon name="alert" size={15} /><span>While recording, <b>{clash.join(", ")}</b> will control the recorder and will <b>not</b> reach the program you are showing (for example Ctrl + S won't save there).</span></div>}
          <p className="muted small">If another program already uses one of your keys, the emergency stop <b>{pretty(dev?.emergency_stop || "")}</b> always works. You hear a short beep on start, pause, resume and stop.</p>
          <div className="two">
            <Field label="Countdown"><Seg value={String(s.rec_countdown ?? 3)} options={[["0", "None"], ["3", "3 s"], ["5", "5 s"]]} onChange={(v) => set({ rec_countdown: Number(v) })} /></Field>
            <Field label="Capture method" hint="Use Compatible only if a recording comes out black or stuttering."><Seg value={s.rec_method || "auto"} options={[["auto", "Fastest"], ["compatible", "Compatible"]]} onChange={(v) => set({ rec_method: v })} /></Field>
          </div>
          <Field label="Name (optional)"><input className="input" value={name} placeholder="e.g. Tutorial part 1" onChange={(e) => setName(e.target.value)} /></Field>
          <Btn kind="primary" icon="play" className="rec-go" disabled={!dev || !!active || (dev && !dev.windows_os)} onClick={begin}>Start recording</Btn>
          {dev && !dev.windows_os && <p className="muted small">Screen recording works on Windows.</p>}
        </section>
      </div>
      <div className="sec-head rec-list-head"><h2>Recordings</h2><span className="muted small">{list ? `${list.length} saved` : ""}</span></div>
      <div className="rec-list">
        {list && !list.length && <p className="muted">No recordings yet. Your recordings appear here, safe even if the PC crashes mid-recording.</p>}
        {(list || []).map((r) => (
          <div key={r.id} className={`rec-card glass-2 ${r.state === "failed" ? "bad" : ""}`}>
            <div className="rec-thumb" onClick={() => r.video && setPlay(r)}>{r.poster ? <img src={mediaUrl(r.poster, r.created)} alt="" /> : <Icon name="film" />}{r.duration > 0 && <span className="dur">{fmt(r.duration)}</span>}</div>
            <div className="rec-body">
              <b>{r.name || `Recording ${new Date(r.created * 1000).toLocaleString()}`}</b>
              <span className="muted small">{timeAgo(r.created)}{r.width ? ` · ${r.width}×${r.height}` : ""}{r.mic ? " · mic" : ""}{r.system ? " · PC sound" : ""}{r.webcam ? " · webcam" : ""}{r.markers?.length ? ` · ${r.markers.length} marker${r.markers.length > 1 ? "s" : ""}` : ""}{r.recovered ? " · recovered" : ""}</span>
              {r.notes?.map((n, i) => <small key={i} className="rec-note">{n}</small>)}
              <div className="row gap wrap">
                {r.video && <Btn small kind="primary" icon="scissors" onClick={() => api("/api/record/to_shorts", { id: r.id }).then(() => { toast("Finding the best moments for Shorts…", "ok"); go("create"); }).catch((e) => toast(e.message, "error"))}>Make Shorts</Btn>}
                {r.video && <Btn small icon="play" onClick={() => setPlay(r)}>Play</Btn>}
                <IconBtn icon="folder" title="Show files" onClick={() => api("/api/open", { path: r.video || r.dir, select: !!r.video })} />
                <IconBtn icon="trash" danger title="Delete" onClick={() => { if (confirm("Delete this recording from your computer?")) api("/api/record/delete", { id: r.id }).then(load); }} />
              </div>
            </div>
          </div>
        ))}
      </div>
      {play && <Modal title={play.name || "Recording"} onClose={() => setPlay(null)} width={980}><video src={mediaUrl(play.video)} controls autoPlay className="rec-video" /></Modal>}
    </div>
  );
}
