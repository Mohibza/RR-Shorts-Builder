import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { setState, toast, useStore } from "../lib/store";
import { absAt, baseParts, buildTimeline, fmt } from "../lib/timeline";
import type { Clip, Edits, Place, Project, Style } from "../lib/types";
import { Icon } from "../components/Icon";
import { ClipPlayer, type PlayerHandle } from "../components/ClipPlayer";
import { Btn, Chip, Field, IconBtn, Progress, Score, Seg, Select, Slider, Tags, Text, Toggle } from "../components/ui";
import { useProject } from "./Projects";
import { PostDialog } from "./Library";
import { Steps } from "./Create";

const FILLERS = new Set(["um", "uh", "umm", "uhh", "erm", "hmm", "mm", "ah", "like", "basically", "actually", "literally",
  "so", "matlab", "yaani", "yani", "haan", "acha", "achha", "wo", "woh"]);
const TABS: [string, string, string][] = [["transcript", "Transcript", "scissors"], ["style", "Style", "wand"],
  ["text", "Text", "type"], ["audio", "Audio", "volume"], ["layout", "Layout", "layout"]];

export function Editor({ pid, cid }: { pid: string; cid: string }) {
  const { project: p, err } = useProject(pid);
  const c = p?.clips.find((x) => x.id === cid);
  if (err) return <div className="page center"><p>{err}</p><Btn onClick={() => setState({ editing: null })}>Back</Btn></div>;
  if (!p || !c) return <div className="page center"><span className="spin big" /></div>;
  return <EditorInner p={p} c={c} />;
}

function EditorInner({ p, c }: { p: Project; c: Clip }) {
  const cat = useStore((s) => s.catalog);
  const settings = useStore((s) => s.settings);
  const exports = useStore((s) => s.exports);
  const [edits, setEdits] = useState<Edits>(() => JSON.parse(JSON.stringify(c.edits || {})));
  const [hist, setHist] = useState<Edits[]>([]);
  const [tab, setTab] = useState("transcript");
  const [T, setT] = useState(0);
  const [frame, setFrame] = useState<string>("");
  const [frameBusy, setFrameBusy] = useState(false);
  const [post, setPost] = useState<string | null>(null);
  const player = useRef<PlayerHandle>(null);
  const first = useRef(true);

  // autosave (debounced) so nothing is lost if the window closes
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const id = setTimeout(() => api("/api/clip/edits", { project: p.id, clip: c.id, edits }).catch(() => {}), 500);
    return () => clearTimeout(id);
  }, [edits]);

  const change = (fn: (e: Edits) => Edits) => {
    setHist((h) => [...h.slice(-49), edits]);
    setEdits((e) => fn(JSON.parse(JSON.stringify(e))));
    setFrame("");
  };
  const undo = () => {
    if (!hist.length) return;
    setEdits(hist[hist.length - 1]);
    setHist((h) => h.slice(0, -1));
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
      else if (e.key === " ") { e.preventDefault(); player.current?.toggle(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const style: Style = useMemo(() => ({ ...c.style, ...(edits.style || {}), place: edits.place ?? c.style.place ?? {} } as Style), [c.style, edits.style, edits.place]);
  const tl = useMemo(() => buildTimeline(c, edits, settings.remove_pauses !== false), [c, edits.trim, edits.cut, edits.fix, settings.remove_pauses]);
  const task = Object.values(exports).filter((e) => e.project === p.id && e.clip === c.id).sort((a, b) => b.created - a.created)[0];
  const exporting = task && (task.state === "running" || task.state === "queued");
  const lastExport = task?.state === "done" ? { path: task.path, plan_file: task.plan_file } : c.exports[c.exports.length - 1];

  const doExport = async (andPost = false) => {
    try {
      await api("/api/clip/export", { project: p.id, clip: c.id, edits });
      toast(andPost ? "Exporting… posting opens when it's ready." : "Exporting in the background…", "ok");
      if (andPost) setWantPost(true);
    } catch (e: any) { toast(e.message, "error"); }
  };
  const [wantPost, setWantPost] = useState(false);
  useEffect(() => { if (wantPost && task?.state === "done") { setWantPost(false); setPost(task.plan_file); } }, [task?.state, wantPost]);

  const exact = async () => {
    setFrameBusy(true);
    try {
      const r = await api<{ path: string }>("/api/clip/frame", { project: p.id, clip: c.id, edits, t: T });
      setFrame(mediaUrl(r.path, Date.now()));
    } catch (e: any) { toast(e.message, "error"); }
    setFrameBusy(false);
  };
  const hookText = (edits.hook ?? "") || c.clip.title;

  return (
    <div className="page editor">
      <div className="ed-head">
        <IconBtn icon="left" title="Back to clips" onClick={() => setState({ editing: null, page: "projects", openProject: p.id })} />
        <Score value={c.score} size={42} />
        <div className="ed-title">
          <b title={hookText}>{hookText}</b>
          <span className="muted">{p.title} · {fmt(c.clip.start)}–{fmt(c.clip.end)} · {c.reasons[0]}</span>
        </div>
        <div className="grow" />
        <Steps active={3} />
        <IconBtn icon="undo" title="Undo (Ctrl+Z)" onClick={undo} />
        <Btn icon="frame" busy={frameBusy} onClick={exact} title="Render this exact frame with the real export engine">Exact frame</Btn>
        {exporting ? <div className="ed-prog"><Progress frac={task.state === "running" ? task.frac : 0.02} label={task.state === "running" ? "Exporting" : "Queued"} tone="green" /></div>
          : <Btn icon="download" onClick={() => doExport(false)}>Export</Btn>}
        {lastExport && !exporting && <IconBtn icon="folder" title="Show exported file" onClick={() => api("/api/open", { path: lastExport.path, select: true })} />}
        <Btn kind="glow" icon="rocket" disabled={!!exporting} onClick={() => doExport(true)}>Export & post</Btn>
      </div>
      <div className="ed-body">
        <div className="ed-left">
          <div className="ed-player">
            <ClipPlayer ref={player} clip={c} pid={p.id} edits={edits} style={style} catalog={cat} settings={settings} controls
              camera={c.camera} framing={c.framing} srcWH={[p.info.width, p.info.height]} hookText={hookText}
              onTime={(t) => setT(t)} fill withAudio />
            {frame && <div className="exact" onClick={() => setFrame("")}><img src={frame} alt="Exact frame" /><span className="chip dark"><Icon name="frame" size={13} /> Exact frame · click to go back to live</span></div>}
          </div>
          <TrimBar c={c} edits={edits} T={T} tl={tl} onTrim={(tr) => change((e) => ({ ...e, trim: tr }))} onSeek={(t) => player.current?.seek(t)} />
        </div>
        <div className="ed-right glass depth">
          <div className="tabs">
            {TABS.map(([k, l, ic]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={ic} size={16} />{l}</button>)}
          </div>
          <div className="tab-body scroll">
            {tab === "transcript" && <TranscriptTab c={c} edits={edits} change={change} T={T} tl={tl} seek={(t) => player.current?.seek(t)} />}
            {tab === "style" && <StyleTab style={style} change={change} />}
            {tab === "text" && <TextTab c={c} edits={edits} change={change} style={style} />}
            {tab === "audio" && <AudioTab edits={edits} change={change} />}
            {tab === "layout" && <LayoutTab style={style} change={change} landscape={p.info.width > p.info.height * 0.8} />}
          </div>
        </div>
      </div>
      {post && <PostDialog plans={[post]} onClose={() => setPost(null)} />}
    </div>
  );
}

// ------------------------------------------------------------------ trim bar
function TrimBar({ c, edits, T, tl, onTrim, onSeek }: { c: Clip; edits: Edits; T: number; tl: ReturnType<typeof buildTimeline>; onTrim: (t: [number, number]) => void; onSeek: (T: number) => void }) {
  const parts = baseParts(c);
  const L = parts.reduce((x, [a, b]) => x + b - a, 0);
  const bar = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<null | 0 | 1>(null);
  const [tr, setTr] = useState<[number, number]>(edits.trim || [0, L]);
  useEffect(() => setTr(edits.trim || [0, L]), [edits.trim, L]);
  const baseOf = (abs: number) => {
    let acc = 0;
    for (const [a, b] of parts) { if (abs >= a && abs <= b) return acc + abs - a; acc += b - a; }
    return 0;
  };
  const absOf = (bt: number) => {
    let acc = 0;
    for (const [a, b] of parts) { if (bt <= acc + b - a) return a + bt - acc; acc += b - a; }
    return parts[parts.length - 1][1];
  };
  const TofAbs = (abs: number) => {
    for (const g of tl.segs) { if (abs < g.b) return g.t0 + Math.max(0, abs - g.a); }
    return tl.D;
  };
  const head = baseOf(absAt(tl, T));
  const x2t = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect();
    return Math.max(0, Math.min(L, ((clientX - r.left) / r.width) * L));
  };
  useEffect(() => {
    if (drag === null) return;
    const mv = (e: MouseEvent) => {
      const t = x2t(e.clientX);
      setTr((cur) => drag === 0 ? [Math.min(t, cur[1] - 3), cur[1]] : [cur[0], Math.max(t, cur[0] + 3)]);
    };
    const up = () => { setDrag(null); setTr((cur) => { onTrim([Math.round(cur[0] * 100) / 100, Math.round(cur[1] * 100) / 100]); return cur; }); };
    window.addEventListener("mousemove", mv);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
  }, [drag]);
  const cut = new Set(edits.cut || []);
  const words = c.words || [];
  return (
    <div className="trim glass-2">
      <div className="trim-top"><span><Icon name="scissors" size={14} /> Drag the ends to trim</span><span className="muted">Short: {fmt(tl.D)} · {tl.D.toFixed(1)}s</span>
        {edits.trim && <button className="linkbtn" onClick={() => onTrim([0, L])}>Reset</button>}</div>
      <div className="trim-bar" ref={bar} onMouseDown={(e) => { if ((e.target as HTMLElement).dataset.h) return; onSeek(TofAbs(absOf(x2t(e.clientX)))); }}>
        {words.map((w, i) => {
          const bt = baseOf(w.s);
          if (w.s < parts[0][0] - 0.01 || !parts.some(([a, b]) => w.s >= a && w.s <= b)) return null;
          return <span key={i} className={`tw ${cut.has(i) ? "cut" : ""}`} style={{ left: `${(bt / L) * 100}%`, width: `${Math.max(0.3, ((w.e - w.s) / L) * 100)}%` }} />;
        })}
        {parts.length > 1 && parts.slice(0, -1).reduce<number[]>((acc, [a, b]) => [...acc, (acc[acc.length - 1] || 0) + b - a], []).map((x, i) =>
          <span key={"j" + i} className="join" style={{ left: `${(x / L) * 100}%` }} title="Cut between parts" />)}
        <div className="trim-out" style={{ left: 0, width: `${(tr[0] / L) * 100}%` }} />
        <div className="trim-out" style={{ left: `${(tr[1] / L) * 100}%`, right: 0 }} />
        <div className="trim-h" data-h="1" style={{ left: `${(tr[0] / L) * 100}%` }} onMouseDown={() => setDrag(0)} />
        <div className="trim-h r" data-h="1" style={{ left: `${(tr[1] / L) * 100}%` }} onMouseDown={() => setDrag(1)} />
        <div className="playhead" style={{ left: `${(head / L) * 100}%` }} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ transcript: click to seek, select to remove
function TranscriptTab({ c, edits, change, T, tl, seek }: { c: Clip; edits: Edits; change: (f: (e: Edits) => Edits) => void; T: number; tl: ReturnType<typeof buildTimeline>; seek: (T: number) => void }) {
  const words = c.words || [];
  const cut = new Set(edits.cut || []);
  const fix = edits.fix || {};
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [val, setVal] = useState("");
  const parts = baseParts(c);
  const inClip = (w: { s: number; e: number }) => parts.some(([a, b]) => w.s >= a - 0.05 && w.e <= b + 0.3);
  const playing = tl.words.find((w) => T >= w.T && T < w.TE)?.i;
  const Tof = (i: number) => tl.words.find((w) => w.i === i)?.T;
  const range = sel ? [Math.min(...sel), Math.max(...sel)] : null;
  const selIdx = range ? words.map((_, i) => i).filter((i) => i >= range[0] && i <= range[1]) : [];
  const allCut = selIdx.length > 0 && selIdx.every((i) => cut.has(i));
  const apply = (remove: boolean) => {
    change((e) => {
      const s = new Set(e.cut || []);
      selIdx.forEach((i) => (remove ? s.add(i) : s.delete(i)));
      return { ...e, cut: [...s].sort((a, b) => a - b) };
    });
    setSel(null);
  };
  const fillers = words.map((w, i) => [w, i] as const).filter(([w, i]) => inClip(w) && !cut.has(i) && FILLERS.has(w.w.toLowerCase().replace(/[^\p{L}']/gu, "")));
  return (
    <div className="transcript">
      <div className="tr-tools">
        <span className="muted small">Click a word to jump there. Drag or Shift-click to select, then remove. Double-click to fix spelling.</span>
        <div className="row gap">
          {fillers.length > 0 && <Btn small icon="wand" onClick={() => change((e) => ({ ...e, cut: [...new Set([...(e.cut || []), ...fillers.map(([, i]) => i)])].sort((a, b) => a - b) }))}>Remove {fillers.length} filler word{fillers.length > 1 ? "s" : ""}</Btn>}
          {(edits.cut?.length || 0) > 0 && <Btn small kind="ghost" icon="undo" onClick={() => change((e) => ({ ...e, cut: [] }))}>Restore all</Btn>}
        </div>
      </div>
      {range && (
        <div className="sel-bar glass-2">
          <span>{selIdx.length} word{selIdx.length > 1 ? "s" : ""} selected</span>
          {allCut ? <Btn small icon="undo" onClick={() => apply(false)}>Restore</Btn> : <Btn small kind="danger" icon="scissors" onClick={() => apply(true)}>Remove from Short</Btn>}
          <IconBtn icon="x" title="Clear selection" onClick={() => setSel(null)} />
        </div>
      )}
      <div className="words" onMouseLeave={() => { if (dragging.current !== null) dragging.current = null; }}>
        {words.map((w, i) => {
          if (!inClip(w)) return null;
          const inSel = range && i >= range[0] && i <= range[1];
          if (editing === i) {
            return <input key={i} className="word-edit" autoFocus value={val} onChange={(e) => setVal(e.target.value)}
              onBlur={() => { commitFix(i); }} onKeyDown={(e) => { if (e.key === "Enter") commitFix(i); if (e.key === "Escape") setEditing(null); }}
              style={{ width: Math.max(3, val.length + 1) + "ch" }} />;
          }
          return (
            <span key={i} className={`word ${cut.has(i) ? "cut" : ""} ${inSel ? "sel" : ""} ${playing === i ? "now" : ""} ${fix[i] !== undefined ? "fixed" : ""}`}
              onMouseDown={(e) => { if (e.shiftKey && sel) setSel([sel[0], i]); else { dragging.current = i; setSel(null); } }}
              onMouseEnter={() => { if (dragging.current !== null && dragging.current !== i) setSel([dragging.current, i]); }}
              onMouseUp={() => {
                if (dragging.current === i && !sel) { const t = Tof(i); if (t !== undefined) seek(t + 0.01); }
                dragging.current = null;
              }}
              onDoubleClick={() => { setEditing(i); setVal(fix[i] ?? w.w); setSel(null); }}
              title={cut.has(i) ? "Removed (select and Restore)" : fmt(w.s)}>
              {fix[i] ?? w.w}
            </span>
          );
        })}
      </div>
    </div>
  );
  function commitFix(i: number) {
    const v = val.trim();
    setEditing(null);
    if (v === (fix[i] ?? words[i].w)) return;
    change((e) => { const f = { ...(e.fix || {}) }; if (!v || v === words[i].w) delete f[i]; else f[i] = v; return { ...e, fix: f }; });
  }
}
const dragging = { current: null as number | null };

// ------------------------------------------------------------------ style
function StyleTab({ style, change }: { style: Style; change: (f: (e: Edits) => Edits) => void }) {
  const cat = useStore((s) => s.catalog)!;
  const set = (patch: Partial<Style>) => change((e) => ({ ...e, style: { ...(e.style || {}), ...patch } }));
  const saveDefault = async () => {
    const { place, ...rest } = style as any;
    await api("/api/settings", { caption_style: rest.caption_style, hook_style: rest.hook_style || "random", color_grade: rest.color_grade, motion: rest.motion, intro: rest.intro });
    toast("Saved as the look for new videos", "ok");
  };
  return (
    <div className="style-tab">
      <div className="sec-head"><h3>Captions</h3><button className="linkbtn" onClick={saveDefault}>Use this look for new videos</button></div>
      <div className="cap-grid">
        {Object.entries(cat.captions).map(([k, cs]) => (
          <button key={k} className={`cap-card ${style.caption_style === k ? "on" : ""}`} onClick={() => set({ caption_style: k })}>
            <span className="cap-sample" style={{ fontFamily: `"${cs.font}"`, color: cs.primary, WebkitTextStroke: cs.box || cs.mode === "hollow" ? `1px ${cs.outline}` : `2px ${cs.outline}`,
              background: cs.box ? cs.outline : undefined, padding: cs.box ? "1px 6px" : undefined, borderRadius: 4, paintOrder: "stroke fill",
              textShadow: cs.glow ? `0 0 8px ${cs.glow}` : "0 2px 0 rgba(0,0,0,.6)", textTransform: cs.upper ? "uppercase" : "none" } as React.CSSProperties}>
              Go <span style={{ color: cs.active || cs.primary, background: cs.hl_box, padding: cs.hl_box ? "0 4px" : undefined }}>viral</span>
            </span>
            <small>{cs.name}</small>
          </button>
        ))}
      </div>
      <div className="sec-head"><h3>Hook heading</h3></div>
      <div className="chips-row">
        <button className={`chipbtn ${!style.hook_style ? "on" : ""}`} onClick={() => set({ hook_style: null })}>None</button>
        {Object.entries(cat.hooks).map(([k, h]) => (
          <button key={k} className={`chipbtn hookchip ${style.hook_style === k ? "on" : ""}`} onClick={() => set({ hook_style: k })}
            style={{ fontFamily: `"${h.font}"` }}>
            <span style={{ color: h.color, background: h.box, WebkitTextStroke: h.outline ? `1px ${h.outline}` : undefined, padding: "0 5px", borderRadius: 3 }}>Aa</span> {h.name}
          </button>
        ))}
      </div>
      <div className="sec-head"><h3>Colour</h3></div>
      <div className="chips-row">
        {Object.entries(cat.grades).map(([k, g]) => <button key={k} className={`chipbtn grade g-${k} ${style.color_grade === k ? "on" : ""}`} onClick={() => set({ color_grade: k })}><i />{g.name}</button>)}
      </div>
      <div className="two">
        <Field label="Camera motion"><Select value={style.motion} options={Object.entries(cat.motions) as [string, string][]} onChange={(v) => set({ motion: v })} /></Field>
        <Field label="Intro"><Select value={style.intro} options={Object.entries(cat.intros) as [string, string][]} onChange={(v) => set({ intro: v })} /></Field>
        <Field label="End card"><Select value={style.cta_style || "none"} options={[["none", "None"], ...Object.entries(cat.ctas).map(([k, v]) => [k, v.name] as [string, string])]} onChange={(v) => set({ cta_style: v === "none" ? null : v })} /></Field>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ text: hook + upload details
function TextTab({ c, edits, change, style }: { c: Clip; edits: Edits; change: (f: (e: Edits) => Edits) => void; style: Style }) {
  const cur = edits.hook ?? "";
  const opts = [c.clip.title, ...c.hooks.filter((h) => h && h !== c.clip.title)].slice(0, 5);
  const meta = { title: c.seo.title || c.clip.title, description: c.seo.description || "", tags: c.seo.tags || [], hashtags: c.seo.hashtags || ["#shorts"], ...(edits.meta || {}) };
  const setMeta = (patch: Partial<typeof meta>) => change((e) => ({ ...e, meta: { ...(e.meta || {}), ...patch } }));
  const active = cur || c.clip.title;
  return (
    <div className="text-tab">
      <div className="sec-head"><h3>Hook heading</h3>{!style.hook_style && <span className="muted small">turned off in Style</span>}</div>
      <div className="hook-opts">
        {opts.map((h, i) => (
          <button key={i} className={`hook-opt ${active === h ? "on" : ""}`} onClick={() => change((e) => ({ ...e, hook: i === 0 ? "" : h }))}>
            <span className="radio" />{h}{i === 0 && <Chip>AI pick</Chip>}
          </button>
        ))}
        <div className="hook-custom">
          <Text value={cur && !opts.includes(cur) ? cur : ""} placeholder="Write your own hook…" onChange={(v) => change((e) => ({ ...e, hook: v }))} />
        </div>
      </div>
      <Field label="Show hook for"><Slider value={style.place?.hook_dur ?? 0} min={0} max={10} step={0.5} fmt={(v) => (v ? `${v}s` : "Style default")}
        onChange={(v) => change((e) => ({ ...e, place: { ...(e.place ?? style.place ?? {}), hook_dur: v || undefined } }))} /></Field>
      <div className="sec-head"><h3>Upload details</h3><span className="muted small">used when posting</span></div>
      <Field label="Title"><Text value={meta.title} onChange={(v) => setMeta({ title: v })} /><div className="count">{meta.title.length}/100</div></Field>
      <Field label="Description"><textarea className="input" rows={4} value={meta.description} onChange={(e) => setMeta({ description: e.target.value })} /></Field>
      <Field label="Hashtags"><Tags value={meta.hashtags} onChange={(v) => setMeta({ hashtags: v.map((t) => (t.startsWith("#") ? t : "#" + t)) })} placeholder="#shorts" /></Field>
      <Field label="Tags"><Tags value={meta.tags} onChange={(v) => setMeta({ tags: v })} placeholder="keyword, keyword" /></Field>
    </div>
  );
}

// ------------------------------------------------------------------ audio
function AudioTab({ edits, change }: { edits: Edits; change: (f: (e: Edits) => Edits) => void }) {
  const [lib, setLib] = useState<any>(null);
  const settings = useStore((s) => s.settings);
  const audio = edits.audio || {};
  const music = audio.music ?? (settings.add_music ? "auto" : "none");
  const [play, setPlay] = useState("");
  const aud = useRef<HTMLAudioElement>(null);
  useEffect(() => { get("/api/music").then(setLib).catch(() => {}); }, []);
  const set = (patch: Edits["audio"]) => change((e) => ({ ...e, audio: { ...(e.audio || {}), ...patch } }));
  useEffect(() => { if (aud.current) { if (play) aud.current.play().catch(() => {}); else aud.current.pause(); } }, [play]);
  return (
    <div className="audio-tab">
      <div className="sec-head"><h3>Background music</h3></div>
      <Seg value={music === "auto" || music === "none" ? music : "pick"} options={[["auto", "Auto pick"], ["none", "No music"], ["pick", "Choose track"]]}
        onChange={(v) => set({ music: v === "pick" ? (lib?.tracks?.[0]?.path || "auto") : v })} />
      {music !== "none" && (
        <Field label="Music level"><div className="row gap">
          <Toggle on={audio.music_volume == null} onChange={(on) => set({ music_volume: on ? null : 0.12 })} label="Auto-level" />
          {audio.music_volume != null && <Slider value={audio.music_volume} min={0} max={0.5} step={0.01} fmt={(v) => `${Math.round(v * 200)}%`} onChange={(v) => set({ music_volume: v })} />}
        </div></Field>
      )}
      {music !== "none" && music !== "auto" && (
        <div className="track-list">
          {(lib?.tracks || []).map((t: any) => (
            <div key={t.path} className={`track ${music === t.path ? "on" : ""}`} onClick={() => set({ music: t.path })}>
              <button className="pbtn" onClick={(e) => { e.stopPropagation(); setPlay(play === t.path ? "" : t.path); }}><Icon name={play === t.path ? "pause" : "play"} size={13} /></button>
              <div><b>{t.title}</b><small>{t.artist || t.source || ""}{t.license ? ` · ${t.license}` : ""}</small></div>
              {t.starred && <Icon name="star" size={13} fill />}
            </div>
          ))}
          {!lib?.tracks?.length && <p className="muted small">Your music folder is empty. Add tracks on the Music page.</p>}
        </div>
      )}
      <audio ref={aud} src={play ? mediaUrl(play) : undefined} onEnded={() => setPlay("")} />
      <div className="sec-head"><h3>Sound effects</h3><span className="muted small">copyright-free, generated by the app</span></div>
      <Seg value={audio.sfx_level || settings.sfx_level || "auto"} options={[["auto", "Auto"], ["off", "Off"], ["subtle", "Subtle"], ["medium", "Energetic"], ["high", "Max"]]} onChange={(v) => set({ sfx_level: v })} />
      <p className="muted small">Auto matches the sound design to the clip: more hits for fast, loud talk, fewer for calm clips. The preview plays the sound effects and music too; the export mixes them under the voice.</p>
    </div>
  );
}

// ------------------------------------------------------------------ layout / placement
function LayoutTab({ style, change, landscape }: { style: Style; change: (f: (e: Edits) => Edits) => void; landscape: boolean }) {
  const pl: Place = style.place || {};
  const setPl = (patch: Place) => change((e) => ({ ...e, place: { ...(e.place ?? style.place ?? {}), ...patch } }));
  const cat = useStore((s) => s.catalog)!;
  const capY = pl.cap_y ?? ({ upper: 0.33, middle: 0.52, lower: 0.67 } as any)[style.position] ?? 0.67;
  const hookY = pl.hook_y ?? ((style.hook_style && cat.hooks[style.hook_style]?.y) || 330) / 1920;
  return (
    <div className="layout-tab">
      {landscape && (<>
        <div className="sec-head"><h3>Framing</h3></div>
        <LayoutPicker value={style.layout} onChange={(v) => change((e) => ({ ...e, style: { ...(e.style || {}), layout: v } }))} />
      </>)}
      <div className="two">
        <Field label="Zoom"><Slider value={pl.frame_zoom ?? 1} min={1} max={2} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ frame_zoom: v })} /></Field>
        <Field label="Move left / right"><Slider value={pl.frame_x ?? 0} min={-1} max={1} step={0.05} fmt={(v) => (v ? (v > 0 ? "→" : "←") + Math.round(Math.abs(v) * 100) : "0")} onChange={(v) => setPl({ frame_x: v })} /></Field>
        <Field label="Move up / down"><Slider value={pl.frame_y ?? 0} min={-1} max={1} step={0.05} fmt={(v) => (v ? (v > 0 ? "↓" : "↑") + Math.round(Math.abs(v) * 100) : "0")} onChange={(v) => setPl({ frame_y: v })} /></Field>
      </div>
      <div className="sec-head"><h3>Captions</h3></div>
      <div className="two">
        <Field label="Height on screen"><Slider value={capY} min={0.15} max={0.9} step={0.01} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ cap_y: v })} /></Field>
        <Field label="Size"><Slider value={pl.cap_scale ?? 1} min={0.5} max={1.6} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ cap_scale: v })} /></Field>
      </div>
      <div className="sec-head"><h3>Hook heading</h3></div>
      <div className="two">
        <Field label="Height on screen"><Slider value={hookY} min={0.08} max={0.8} step={0.01} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ hook_y: v })} /></Field>
        <Field label="Size"><Slider value={pl.hook_scale ?? 1} min={0.5} max={1.6} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ hook_scale: v })} /></Field>
      </div>
      <div className="sec-head"><h3>End card & watermark</h3></div>
      <div className="two">
        <Field label="End card height"><Slider value={pl.cta_y ?? 0.42} min={0.1} max={0.9} step={0.01} fmt={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPl({ cta_y: v })} /></Field>
        <Field label="Watermark"><Select value={pl.wm_pos || "top"} options={Object.entries(cat.wm_positions) as [string, string][]} onChange={(v) => setPl({ wm_pos: v })} /></Field>
      </div>
      <div className="row gap"><Btn small kind="ghost" icon="undo" onClick={() => change((e) => ({ ...e, place: {} }))}>Reset positions</Btn></div>
    </div>
  );
}

// mini diagrams of each layout: [x, y, w, h, kind] boxes in a 9x16 frame (kind: v = video, f = face, b = blur, k = black)
const LAYOUT_ART: Record<string, [number, number, number, number, string][]> = {
  auto: [[0, 0, 9, 16, "b"], [0, 0, 9, 16, "f"]],
  smart_crop: [[0, 0, 9, 16, "f"]],
  center_crop: [[0, 0, 9, 16, "v"]],
  blur_fit: [[0, 0, 9, 16, "b"], [0, 5.5, 9, 5, "v"]],
  black_fit: [[0, 0, 9, 16, "k"], [0, 5.5, 9, 5, "v"]],
  split: [[0, 0, 9, 8, "f"], [0, 8, 9, 8, "b"], [0, 9.5, 9, 5, "v"]],
  split_reverse: [[0, 0, 9, 8, "b"], [0, 1.5, 9, 5, "v"], [0, 8, 9, 8, "f"]],
  two_speakers: [[0, 0, 9, 7.9, "f"], [0, 8.1, 9, 7.9, "f"]],
  zoom45: [[0, 0, 9, 16, "b"], [0, 2.1, 9, 11.2, "f"]],
  square: [[0, 0, 9, 16, "b"], [0, 3.1, 9, 9, "f"]],
  framed: [[0, 0, 9, 16, "b"], [0.6, 5.6, 7.8, 4.6, "w"], [0.8, 5.8, 7.4, 4.2, "v"]],
};

export function LayoutPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const cat = useStore((s) => s.catalog)!;
  const short: Record<string, string> = { auto: "Auto", smart_crop: "Follow face", center_crop: "Center crop", blur_fit: "Full + blur",
    black_fit: "Full + black", split: "Face / frame", split_reverse: "Frame / face", two_speakers: "Two speakers",
    zoom45: "4:5 zoom", square: "Square", framed: "Framed card" };
  return (
    <div className="layout-grid">
      {Object.entries(cat.layouts).map(([k, name]) => (
        <button key={k} className={`layout-card ${value === k ? "on" : ""}`} onClick={() => onChange(k)} title={name}>
          <svg viewBox="0 0 9 16" className="layout-art">
            {(LAYOUT_ART[k] || [[0, 0, 9, 16, "v"]]).map(([x, y, w, h, kind], i) => <g key={i}>
              <rect x={x} y={y} width={w} height={h} className={`la-${kind}`} />
              {kind === "f" && <circle cx={x + w / 2} cy={y + h * 0.42} r={Math.min(w, h) * 0.16} className="la-head" />}
              {kind === "f" && <path d={`M${x + w / 2 - w * 0.28} ${y + h} Q${x + w / 2} ${y + h * 0.6} ${x + w / 2 + w * 0.28} ${y + h}`} className="la-head" />}
            </g>)}
          </svg>
          <small>{short[k] || name}</small>
        </button>
      ))}
    </div>
  );
}
