import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { setState, toast, useStore } from "../lib/store";
import { absAt, baseParts, buildTimeline, fmt } from "../lib/timeline";
import { playTime, usePlayTime } from "../lib/playtime";
import type { FxPlan } from "../components/ClipPlayer";
import { toFinal, toPre } from "../lib/fx";

type Fr = { t: number; d: number }[];
// the editor's bars and word list work in the cut timeline; the player runs in the finished timeline (+ freezes)
const pre = (T: number, fr: Fr) => toPre(T, fr as any)[0];
import type { Clip, Edits, Place, Project, Style } from "../lib/types";
import { Icon } from "../components/Icon";
import { ClipPlayer, type PlayerHandle } from "../components/ClipPlayer";
import { Btn, Chip, Field, IconBtn, Modal, Progress, Score, Seg, Select, Slider, Tags, Text, Toggle } from "../components/ui";
import { useProject } from "./Projects";
import { PostDialog } from "./Library";
import { Steps } from "./Create";

const FILLERS = new Set(["um", "uh", "umm", "uhh", "erm", "hmm", "mm", "ah", "like", "basically", "actually", "literally",
  "so", "matlab", "yaani", "yani", "haan", "acha", "achha", "wo", "woh"]);
const TABS: [string, string, string][] = [["transcript", "Transcript", "scissors"], ["style", "Style", "wand"],
  ["vibe", "Vibe & FX", "spark"], ["text", "Text", "type"], ["audio", "Audio", "volume"], ["layout", "Layout", "layout"]];

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
  const [plan, setPlan] = useState<FxPlan | null>(null);
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
  const fr: Fr = plan?.story?.freezes || [];
  const seekPre = (t: number) => player.current?.seek(toFinal(t, fr as any));
  const task = Object.values(exports).filter((e) => e.project === p.id && e.clip === c.id).sort((a, b) => b.created - a.created)[0];
  const exporting = task && (task.state === "running" || task.state === "queued");
  const lastExport = task?.state === "done" ? { path: task.path, plan_file: task.plan_file } : c.exports[c.exports.length - 1];

  const [expDlg, setExpDlg] = useState(false);
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
      const r = await api<{ path: string }>("/api/clip/frame", { project: p.id, clip: c.id, edits, t: player.current?.time() ?? playTime() });
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
          : <Btn icon="download" onClick={() => setExpDlg(true)}>Export</Btn>}
        {lastExport && !exporting && <IconBtn icon="folder" title="Show exported file" onClick={() => api("/api/open", { path: lastExport.path, select: true })} />}
        <Btn kind="glow" icon="rocket" disabled={!!exporting} onClick={() => doExport(true)}>Export & post</Btn>
      </div>
      <div className="ed-body">
        <div className="ed-left">
          <div className="ed-player">
            <ClipPlayer ref={player} clip={c} pid={p.id} edits={edits} style={style} catalog={cat} settings={settings} controls
              camera={c.camera} framing={c.framing} srcWH={[p.info.width, p.info.height]} hookText={hookText}
              onPlan={setPlan} fill withAudio
              onPlace={(patch) => change((e) => ({ ...e, place: { ...(e.place ?? style.place ?? {}), ...patch } }))} />
            {frame && <div className="exact" onClick={() => setFrame("")}><img src={frame} alt="Exact frame" /><span className="chip dark"><Icon name="frame" size={13} /> Exact frame · click to go back to live</span></div>}
          </div>
          <TrimBar c={c} edits={edits} tl={tl} plan={plan} onTrim={(tr) => change((e) => ({ ...e, trim: tr }))} onSeek={seekPre} fr={fr} />
        </div>
        <div className="ed-right glass depth">
          <div className="tabs">
            {TABS.map(([k, l, ic]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={ic} size={16} />{l}</button>)}
          </div>
          <div className="tab-body scroll">
            {tab === "transcript" && <TranscriptTab c={c} edits={edits} change={change} tl={tl} seek={seekPre} fr={fr} />}
            {tab === "style" && <StyleTab style={style} change={change} />}
            {tab === "vibe" && <VibeTab c={c} edits={edits} change={change} style={style} plan={plan} tl={tl} seek={seekPre} fr={fr} />}
            {tab === "text" && <TextTab c={c} edits={edits} change={change} style={style} />}
            {tab === "audio" && <AudioTab edits={edits} change={change} plan={plan} />}
            {tab === "layout" && <LayoutTab style={style} change={change} landscape={p.info.width > p.info.height * 0.8} />}
          </div>
        </div>
      </div>
      {post && <PostDialog plans={[post]} onClose={() => setPost(null)} />}
      {expDlg && <ExportDialog onClose={() => setExpDlg(false)} onExport={() => doExport(false)} />}
    </div>
  );
}

// ------------------------------------------------------------------ trim bar
function TrimBar({ c, edits, tl, plan, onTrim, onSeek, fr }: { c: Clip; edits: Edits; tl: ReturnType<typeof buildTimeline>; plan: FxPlan | null; onTrim: (t: [number, number]) => void; onSeek: (T: number) => void; fr: Fr }) {
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
      <div className="trim-top"><span><Icon name="scissors" size={14} /> Drag the ends to trim</span><span className="muted">Short: {fmt(tl.D + fr.reduce((x, f) => x + f.d, 0))} · {(tl.D + fr.reduce((x, f) => x + f.d, 0)).toFixed(1)}s{fr.length ? ` (${fr.length} beat${fr.length > 1 ? "s" : ""})` : ""}</span>
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
        {(plan?.punches || []).map(([t], i) => <span key={"z" + i} className="zoom-mark" title={`Focus zoom at ${fmt(t)}`} style={{ left: `${(baseOf(absAt(tl, pre(t, fr))) / L) * 100}%` }} />)}
        {fr.map((f, i) => <span key={"b" + i} className="beat-mark" title={`Story beat (freeze ${f.d.toFixed(1)}s)`} style={{ left: `${(baseOf(absAt(tl, f.t)) / L) * 100}%` }} />)}
        <Playhead tl={tl} L={L} baseOf={baseOf} fr={fr} />
      </div>
    </div>
  );
}

function Playhead({ tl, L, baseOf, fr }: { tl: ReturnType<typeof buildTimeline>; L: number; baseOf: (abs: number) => number; fr: Fr }) {
  const t = usePlayTime((x) => Math.round(pre(x, fr) * 30) / 30);
  return <div className="playhead" style={{ left: `${(baseOf(absAt(tl, t)) / L) * 100}%` }} />;
}

// ------------------------------------------------------------------ transcript: click to seek, select to remove
function TranscriptTab({ c, edits, change, tl, seek, fr }: { c: Clip; edits: Edits; change: (f: (e: Edits) => Edits) => void; tl: ReturnType<typeof buildTimeline>; seek: (T: number) => void; fr: Fr }) {
  const words = c.words || [];
  const cut = new Set(edits.cut || []);
  const fix = edits.fix || {};
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [val, setVal] = useState("");
  const parts = baseParts(c);
  const inClip = (w: { s: number; e: number }) => parts.some(([a, b]) => w.s >= a - 0.05 && w.e <= b + 0.3);
  const playing = usePlayTime((T0) => { const T = pre(T0, fr); return tl.words.find((w) => T >= w.T && T < w.TE)?.i ?? -1; });
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
const CAP_CATS: [string, string][] = [["all", "All"], ["bold", "Bold"], ["boxed", "Boxed"], ["karaoke", "Karaoke"], ["word", "Word by word"],
  ["fun", "Fun"], ["neon", "Neon"], ["retro", "Retro"], ["script", "Script"], ["clean", "Clean"]];
const HOOK_CATS: [string, string][] = [["all", "All"], ["bold", "Bold"], ["banner", "Banners"], ["fun", "Fun"], ["neon", "Neon & retro"], ["script", "Script"]];

function StyleTab({ style, change }: { style: Style; change: (f: (e: Edits) => Edits) => void }) {
  const cat = useStore((s) => s.catalog)!;
  const [sub, setSub] = useState<"captions" | "headings" | "end" | "look">("captions");
  const [cc, setCc] = useState("all");
  const [hc, setHc] = useState("all");
  const [q, setQ] = useState("");
  const set = (patch: Partial<Style>) => change((e) => ({ ...e, style: { ...(e.style || {}), ...patch } }));
  const saveDefault = async () => {
    const { place, ...rest } = style as any;
    await api("/api/settings", { caption_style: rest.caption_style, hook_style: rest.hook_style || "random", color_grade: rest.color_grade, motion: rest.motion, intro: rest.intro, cta_style: rest.cta_style || "random" });
    toast("Saved as the look for new videos", "ok");
  };
  const match = (name: string, c?: string, want?: string) => (want === "all" || c === want) && (!q || name.toLowerCase().includes(q.toLowerCase()));
  const caps = Object.entries(cat.captions).filter(([, v]) => match(v.name, (v as any).cat, cc));
  const hooks = Object.entries(cat.hooks).filter(([, v]) => match(v.name, (v as any).cat, hc));
  return (
    <div className="style-tab">
      <div className="row gap wrap">
        <div className="seg small">
          {([["captions", `Captions ${Object.keys(cat.captions).length}`], ["headings", `Headings ${Object.keys(cat.hooks).length}`], ["end", `End cards ${Object.keys(cat.ctas).length}`], ["look", `Colour & motion ${Object.keys(cat.grades).length}`]] as [typeof sub, string][]).map(([k, l]) =>
            <button key={k} className={sub === k ? "on" : ""} onClick={() => setSub(k)}>{l}</button>)}
        </div>
        <div className="grow" />
        {(sub === "captions" || sub === "headings") && <div className="search sm"><Icon name="search" size={14} /><input value={q} placeholder="Search templates" onChange={(e) => setQ(e.target.value)} /></div>}
        <button className="linkbtn small" onClick={saveDefault}>Use this look for new videos</button>
      </div>
      {sub === "captions" && (<>
        <div className="chips-row">{CAP_CATS.map(([k, l]) => <button key={k} className={`chipbtn sm ${cc === k ? "on" : ""}`} onClick={() => setCc(k)}>{l}</button>)}</div>
        <div className="cap-grid">
          {caps.map(([k, cs]) => (
            <button key={k} className={`cap-card ${style.caption_style === k ? "on" : ""}`} onClick={() => set({ caption_style: k })} title={cs.name}>
              <span className="cap-sample" style={{ fontFamily: `"${cs.font}"`, color: cs.mode === "hollow" ? "transparent" : cs.primary,
                WebkitTextStroke: cs.box ? undefined : `${cs.mode === "hollow" ? 1.5 : 2}px ${cs.outline}`,
                background: cs.box ? cs.outline : undefined, padding: cs.box ? "2px 7px" : undefined, borderRadius: 4, paintOrder: "stroke fill",
                textShadow: cs.glow ? `0 0 8px ${cs.glow}, 0 0 14px ${cs.glow}` : cs.box ? "none" : "0 2px 0 rgba(0,0,0,.6)", textTransform: cs.upper ? "uppercase" : "none" } as React.CSSProperties}>
                {cs.mode === "oneword" ? <span style={{ color: (cs.palette || [cs.primary])[1] || cs.primary }}>viral</span> : <>Go <span style={{
                  color: cs.mode === "karaoke" ? cs.primary : cs.mode === "hollow" ? cs.primary : (cs.active || cs.primary), background: cs.hl_box, padding: cs.hl_box ? "0 4px" : undefined, borderRadius: 3 }}>viral</span></>}
              </span>
              <small>{cs.name}</small>
            </button>
          ))}
          {!caps.length && <p className="muted small">No caption template matches.</p>}
        </div>
      </>)}
      {sub === "headings" && (<>
        <div className="chips-row">{HOOK_CATS.map(([k, l]) => <button key={k} className={`chipbtn sm ${hc === k ? "on" : ""}`} onClick={() => setHc(k)}>{l}</button>)}</div>
        <div className="hook-grid">
          <button className={`hook-card ${!style.hook_style ? "on" : ""}`} onClick={() => set({ hook_style: null })}><span className="muted">No heading</span><small>None</small></button>
          {hooks.map(([k, h]) => (
            <button key={k} className={`hook-card ${style.hook_style === k ? "on" : ""}`} onClick={() => set({ hook_style: k })} title={h.name}>
              <span style={{ fontFamily: `"${h.font}"`, color: h.color, background: h.box, WebkitTextStroke: h.outline ? `1.5px ${h.outline}` : undefined, paintOrder: "stroke fill",
                padding: h.box ? "3px 8px" : undefined, borderRadius: 4, textTransform: h.upper ? "uppercase" : "none", transform: `rotate(${h.tilt || 0}deg)`,
                textShadow: h.glow ? `0 0 10px ${h.glow}` : undefined } as React.CSSProperties}>Watch this</span>
              <small>{h.name}</small>
            </button>
          ))}
        </div>
      </>)}
      {sub === "end" && (
        <div className="hook-grid">
          <button className={`hook-card ${!style.cta_style ? "on" : ""}`} onClick={() => set({ cta_style: null })}><span className="muted">No end card</span><small>None</small></button>
          {Object.entries(cat.ctas).map(([k, c]) => (
            <button key={k} className={`hook-card ${style.cta_style === k ? "on" : ""}`} onClick={() => set({ cta_style: k })}>
              <span style={{ fontFamily: `"${c.font}"`, color: c.color, background: c.box, WebkitTextStroke: c.outline ? `1.5px ${c.outline}` : undefined,
                paintOrder: "stroke fill", padding: c.box ? "3px 10px" : undefined, borderRadius: 999, textTransform: c.font !== "Poppins" ? "uppercase" : "none" } as React.CSSProperties}>Follow for more</span>
              <small>{c.name}</small>
            </button>
          ))}
        </div>
      )}
      {sub === "look" && (<>
        <div className="sec-head"><h3>Colour</h3></div>
        <div className="chips-row">
          <button className={`chipbtn ${style.color_grade === "auto" ? "on" : ""}`} onClick={() => set({ color_grade: "auto" })}><Icon name="spark" size={12} /> Auto (vibe)</button>
          {Object.entries(cat.grades).map(([k, g]) => <button key={k} className={`chipbtn grade g-${k} ${style.color_grade === k ? "on" : ""}`} onClick={() => set({ color_grade: k })}><i />{g.name}</button>)}
        </div>
        <div className="sec-head"><h3>Camera motion</h3></div>
        <div className="chips-row">
          <button className={`chipbtn ${style.motion === "auto" ? "on" : ""}`} onClick={() => set({ motion: "auto" })}><Icon name="spark" size={12} /> Auto (vibe)</button>
          {Object.entries(cat.motions).map(([k, n]) => <button key={k} className={`chipbtn ${style.motion === k ? "on" : ""}`} onClick={() => set({ motion: k })}>{n}</button>)}
        </div>
        <div className="sec-head"><h3>Intro</h3></div>
        <div className="chips-row">
          <button className={`chipbtn ${style.intro === "auto" ? "on" : ""}`} onClick={() => set({ intro: "auto" })}><Icon name="spark" size={12} /> Auto (vibe)</button>
          {Object.entries(cat.intros).map(([k, n]) => <button key={k} className={`chipbtn ${style.intro === k ? "on" : ""}`} onClick={() => set({ intro: k })}>{n}</button>)}
        </div>
      </>)}
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
function AudioTab({ edits, change, plan }: { edits: Edits; change: (f: (e: Edits) => Edits) => void; plan: FxPlan | null }) {
  const [lib, setLib] = useState<any>(null);
  const settings = useStore((s) => s.settings);
  const cat = useStore((s) => s.catalog);
  const audio = edits.audio || {};
  const music = audio.music ?? (settings.add_music ? "auto" : "none");
  const [play, setPlay] = useState("");
  const aud = useRef<HTMLAudioElement>(null);
  useEffect(() => { get("/api/music").then(setLib).catch(() => {}); }, []);
  const set = (patch: Edits["audio"]) => change((e) => ({ ...e, audio: { ...(e.audio || {}), ...patch } }));
  useEffect(() => { if (aud.current) { if (play) aud.current.play().catch(() => {}); else aud.current.pause(); } }, [play]);
  const vibes = cat?.vibes || {};
  const packs = cat?.sfx_packs || {};
  return (
    <div className="audio-tab">
      <div className="sec-head"><h3>Background music</h3>
        <Btn small kind="ghost" icon="refresh" onClick={() => set({ seed: (audio.seed || 0) + 1 })} title="Pick a different matching track and new sound variations">Shuffle</Btn></div>
      <Seg value={music === "auto" || music === "none" ? music : "pick"} options={[["auto", "Auto (fits the vibe)"], ["none", "No music"], ["pick", "Choose track"]]}
        onChange={(v) => set({ music: v === "pick" ? (plan?.music || lib?.tracks?.[0]?.path || "auto") : v, music_offset: null })} />
      {music === "auto" && (
        <div className="auto-pick glass-2">
          <Icon name="music" size={16} />
          <div className="grow"><b>{plan?.music_name || (settings.add_music ? "Choosing…" : "Music is off in Settings")}</b>
            <small className="muted">{plan ? `Matched to the ${plan.vibe_name} vibe${plan.music_offset ? ` · starts at ${fmt(plan.music_offset)} so the beat drops after the hook` : ""}` : ""}</small></div>
          <select className="mini-select" value={audio.music_mood || "auto"} onChange={(e) => set({ music_mood: e.target.value })} title="Use music for a different mood">
            <option value="auto">Mood: auto</option>
            {Object.entries(vibes).map(([k, v]) => <option key={k} value={k}>Mood: {v.name}</option>)}
          </select>
        </div>
      )}
      {music !== "none" && (
        <Field label="Music level"><div className="row gap">
          <Toggle on={audio.music_volume == null} onChange={(on) => set({ music_volume: on ? null : 0.12 })} label="Auto-level" />
          {audio.music_volume != null && <Slider value={audio.music_volume} min={0} max={0.5} step={0.01} fmt={(v) => `${Math.round(v * 200)}%`} onChange={(v) => set({ music_volume: v })} />}
        </div></Field>
      )}
      {music !== "none" && music !== "auto" && (
        <div className="track-list">
          {(lib?.tracks || []).map((t: any) => (
            <div key={t.path} className={`track ${music === t.path ? "on" : ""}`} onClick={() => set({ music: t.path, music_offset: null })}>
              <button className="pbtn" onClick={(e) => { e.stopPropagation(); setPlay(play === t.path ? "" : t.path); }}><Icon name={play === t.path ? "pause" : "play"} size={13} /></button>
              <div><b>{t.title}</b><small>{(t.vibes || []).map((v: string) => vibes[v]?.name || v).join(" · ") || t.artist || t.source || ""}{t.trending ? " · Trending" : ""}</small></div>
              {t.starred && <Icon name="star" size={13} fill />}
            </div>
          ))}
          {!lib?.tracks?.length && <p className="muted small">Your music folder is empty. Add tracks on the Music page.</p>}
        </div>
      )}
      <audio ref={aud} src={play ? mediaUrl(play) : undefined} onEnded={() => setPlay("")} />
      <div className="sec-head"><h3>Sound effects</h3><span className="muted small">copyright-free, generated by the app</span></div>
      <Seg value={audio.sfx_level || settings.sfx_level || "auto"} options={[["auto", "Auto"], ["off", "Off"], ["subtle", "Subtle"], ["medium", "Energetic"], ["high", "Max"]]} onChange={(v) => set({ sfx_level: v })} />
      <Field label={`Sound pack${plan && (audio.sfx_pack || "auto") === "auto" ? ` · now ${plan.pack_name}` : ""}`}>
        <div className="chips">
          {Object.entries(packs).map(([k, n]) => <button key={k} className={`chipbtn ${(audio.sfx_pack || "auto") === k ? "on" : ""}`} onClick={() => set({ sfx_pack: k })}>{n}</button>)}
        </div>
      </Field>
      <p className="muted small">Every Short picks its own sounds and variations from the pack, so no two sound the same. Each focus zoom gets its own hit, and the opening effect gets a matching sound. Shuffle for a new mix.</p>
    </div>
  );
}

// ------------------------------------------------------------------ vibe, opening and focus zooms
function VibeTab({ c, edits, change, style, plan, tl, seek, fr }: { c: Clip; edits: Edits; change: (f: (e: Edits) => Edits) => void; style: Style; plan: FxPlan | null; tl: ReturnType<typeof buildTimeline>; seek: (t: number) => void; fr: Fr }) {
  const cat = useStore((s) => s.catalog)!;
  const vibes = cat.vibes || {};
  const setStyle = (patch: Partial<Style>) => change((e) => ({ ...e, style: { ...(e.style || {}), ...patch } }));
  const detected = c.vibe && vibes[c.vibe] ? vibes[c.vibe].name : "";
  const custom = Array.isArray(edits.zooms);
  const zoomsAbs: number[] = custom ? (edits.zooms as number[]) : (plan?.punches || []).map(([t]) => Math.round(absAt(tl, pre(t, fr)) * 100) / 100);
  const setZooms = (z: number[] | null) => change((e) => ({ ...e, zooms: z ? [...new Set(z.map((x) => Math.round(x * 100) / 100))].sort((a, b) => a - b) : undefined }));
  const TofAbs = (abs: number) => { for (const g of tl.segs) { if (abs < g.b) return g.t0 + Math.max(0, abs - g.a); } return tl.D; };
  const addHere = () => setZooms([...zoomsAbs, absAt(tl, pre(playTime(), fr))]);
  const opening = style.intro === "auto" ? plan?.intro : style.intro;
  return (
    <div className="vibe-tab">
      <div className="sec-head"><h3>Vibe</h3>{detected && <span className="muted small">detected: {detected}</span>}</div>
      <p className="muted small">The vibe picks the music, sound pack, camera moves and opening. Change it and everything follows.</p>
      <div className="chips">
        <button className={`chipbtn ${!edits.vibe ? "on" : ""}`} onClick={() => change((e) => ({ ...e, vibe: undefined }))}><Icon name="spark" size={12} /> Auto{detected ? ` (${detected})` : ""}</button>
        {Object.entries(vibes).map(([k, v]) => <button key={k} className={`chipbtn ${edits.vibe === k ? "on" : ""}`} onClick={() => change((e) => ({ ...e, vibe: k }))}>{v.name}</button>)}
      </div>

      <StorySection edits={edits} change={change} plan={plan} seek={seek} />

      <div className="sec-head"><h3>Opening (hook) effect</h3>{style.intro === "auto" && plan && <span className="muted small">now: {cat.intros[plan.intro] || plan.intro}</span>}</div>
      <div className="chips">
        <button className={`chipbtn ${style.intro === "auto" ? "on" : ""}`} onClick={() => setStyle({ intro: "auto" })}><Icon name="spark" size={12} /> Auto (vibe)</button>
        {Object.entries(cat.intros).map(([k, n]) => <button key={k} className={`chipbtn ${style.intro === k ? "on" : ""}`} onClick={() => { setStyle({ intro: k }); seek(0); }}>{n}</button>)}
      </div>
      {opening && opening !== "none" && <p className="muted small">Plays at the very start with its own sound. Press play from 0:00 to see it.</p>}

      <div className="sec-head"><h3>Focus zooms</h3>
        <span className="muted small">{zoomsAbs.length} zoom{zoomsAbs.length === 1 ? "" : "s"} · {custom ? "your own" : "automatic on key words"}</span></div>
      <div className="row gap wrap">
        <Btn small icon="plus" onClick={addHere}>Add zoom at playhead</Btn>
        {custom && <Btn small kind="ghost" icon="undo" onClick={() => setZooms(null)}>Back to automatic</Btn>}
        {zoomsAbs.length > 0 && <Btn small kind="ghost" icon="x" onClick={() => setZooms([])}>No zooms</Btn>}
      </div>
      <Field label="Zoom strength">
        <Slider value={edits.zoom_mult ?? 1} min={0.4} max={1.8} step={0.05} fmt={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => change((e) => ({ ...e, zoom_mult: v }))} />
      </Field>
      {style.motion !== "punch" && style.motion !== "auto" && !custom && <p className="muted small">Camera motion is “{cat.motions[style.motion] || style.motion}”: automatic zooms are lighter. Choose Punch Zooms in Style → Colour & motion for full focus zooms.</p>}
      <div className="zoom-list">
        {zoomsAbs.map((z, i) => (
          <span key={i} className="zoom-chip">
            <button className="linkbtn" onClick={() => seek(TofAbs(z) + 0.01)} title="Jump there">{fmt(TofAbs(z))}</button>
            <button className="x" title="Remove this zoom" onClick={() => setZooms(zoomsAbs.filter((_, j) => j !== i))}><Icon name="x" size={11} /></button>
          </span>
        ))}
      </div>
      <p className="muted small">Each zoom pushes in on the speaker's face, holds, then eases out, with a matching sound. The yellow marks on the timeline under the player show where they are.</p>
    </div>
  );
}

// ------------------------------------------------------------------ Story FX: beats, editorial title, streaks, texture
function StorySection({ edits, change, plan, seek }: { edits: Edits; change: (f: (e: Edits) => Edits) => void; plan: FxPlan | null; seek: (t: number) => void }) {
  const settings = useStore((s) => s.settings);
  const se = edits.story || {};
  const val = <K extends keyof NonNullable<Edits["story"]>>(k: K, def: any) => (se[k] ?? def);
  const level = String(val("level", settings.story_fx || "auto"));
  const set = (patch: NonNullable<Edits["story"]>) => change((e) => ({ ...e, story: { ...(e.story || {}), ...patch } }));
  const st = plan?.story;
  const on = level !== "off";
  const sw = (k: "pauses" | "titles" | "transitions" | "textures" | "behind", label: string, hint: string) => (
    <Toggle on={!!val(k, settings[`story_${k}`] ?? true)} onChange={(v) => set({ [k]: v })} label={label} hint={hint} />
  );
  return (
    <>
      <div className="sec-head"><h3>Story FX</h3>
        {st && st.level !== "off" && <span className="muted small">{st.freezes.length} beat{st.freezes.length === 1 ? "" : "s"}{st.title ? ` · ${st.title.look} title` : ""} · {st.streaks.length} streak{st.streaks.length === 1 ? "" : "s"}{st.texture ? " · film texture" : ""}</span>}</div>
      <p className="muted small">Tells the clip like a pro edit: a dramatic pause before the payoff, a magazine-style title behind the speaker, motion-blur transitions and a film look.</p>
      <Seg value={level as any} options={[["off", "Off"], ["auto", "Auto"], ["strong", "Strong"]]} onChange={(v) => set({ level: v })} />
      {on && <div className="story-sw">
        {sw("pauses", "Dramatic beats", "The picture freezes for a moment before the payoff line (and a “rewind” after a cold open), with a riser and a hit")}
        {sw("titles", "Editorial title & beat text", "The hook becomes a magazine-style title; a short line lands during each beat. Replaces the hook banner.")}
        {sw("behind", "Title behind the speaker", "On export the big word sits behind the person (automatic cut-out)")}
        {sw("transitions", "Streak transitions", "Directional motion blur + whip zoom on every join and beat")}
        {sw("textures", "Film texture", "Grain, soft glow, light leaks and vignette, matched to the vibe")}
      </div>}
      {on && st && st.freezes.length > 0 && <div className="zoom-list">
        {st.spans.map(([a], i) => (
          <span key={i} className="zoom-chip beat"><button className="linkbtn" onClick={() => seek(st.freezes[i].t - 1.2)} title="Play into this beat">beat {fmt(a)}</button></span>
        ))}
      </div>}
      {on && st && st.freezes.length === 0 && st.level !== "off" && <p className="muted small">No natural payoff moment found for a beat in this clip. “Strong” looks harder.</p>}
    </>
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
      <div className="drag-hint"><Icon name="edit" size={14} /> <span>Drag any text on the video to move it (captions, hook, title, end card). Mouse wheel over it resizes.</span>
        {["cap_x", "cap_y", "hook_x", "hook_y", "cta_x", "cta_y", "title_dx", "title_dy", "title_scale", "beat_dx", "beat_dy", "beat_scale", "cap_scale", "hook_scale"].some((k) => (pl as any)[k] != null) &&
          <button className="linkbtn small" onClick={() => change((e) => { const n: any = { ...(e.place ?? style.place ?? {}) }; ["cap_x", "cap_y", "hook_x", "hook_y", "cta_x", "cta_y", "title_dx", "title_dy", "title_scale", "beat_dx", "beat_dy", "beat_scale", "cap_scale", "hook_scale"].forEach((k) => delete n[k]); return { ...e, place: n }; })}>Reset text positions</button>}</div>
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

// ------------------------------------------------------------------ export options (platform presets or custom)
const RES: [string, string][] = [["1280", "720p"], ["1920", "1080p"], ["2560", "1440p"], ["3840", "4K"]];
const FPS: [string, string][] = [["0", "Like source"], ["24", "24"], ["30", "30"], ["60", "60"]];
const QUAL: [string, string][] = [["small", "Small file"], ["balanced", "Balanced"], ["high", "High"], ["max", "Maximum"]];

export function ExportDialog({ onClose, onExport, title = "Export" }: { onClose: () => void; onExport: (prefs: any) => void; title?: string }) {
  const cat = useStore((s) => s.catalog) as any;
  const settings = useStore((s) => s.settings);
  const presets: Record<string, any> = cat?.export_presets || {};
  const init = settings.export_prefs || { preset: "youtube" };
  const [p, setP] = useState<any>(() => {
    const base = presets[init.preset] || {};
    return { preset: init.preset || "custom", height: init.height ?? base.height ?? 1920, fps: init.fps ?? base.fps ?? 0,
      quality: init.quality ?? base.quality ?? "high", codec: init.codec ?? base.codec ?? "h264", srt: !!init.srt };
  });
  const pick = (k: string) => { const b = presets[k]; setP({ ...p, preset: k, height: b.height, fps: b.fps, quality: b.quality, codec: b.codec }); };
  const custom = (patch: any) => setP({ ...p, ...patch, preset: "custom" });
  const go = async () => {
    await api("/api/settings", { export_prefs: p }).catch(() => {});
    onExport(p);
    onClose();
  };
  return (
    <Modal title={title} onClose={onClose} width={640} footer={<><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" icon="download" onClick={go}>Export</Btn></>}>
      <div className="preset-grid">
        {Object.entries(presets).map(([k, v]) => (
          <button key={k} className={`preset ${p.preset === k ? "on" : ""}`} onClick={() => pick(k)}>
            <b>{v.name}</b><small>{RES.find((r) => r[0] === String(v.height))?.[1]} · {v.fps ? `${v.fps} fps` : "source fps"} · {QUAL.find((q) => q[0] === v.quality)?.[1]}</small>
          </button>
        ))}
      </div>
      <div className="two">
        <Field label="Resolution"><Seg value={String(p.height)} options={RES} onChange={(v) => custom({ height: Number(v) })} /></Field>
        <Field label="Frame rate"><Seg value={String(p.fps)} options={FPS} onChange={(v) => custom({ fps: Number(v) })} /></Field>
        <Field label="Quality"><Seg value={p.quality} options={QUAL} onChange={(v) => custom({ quality: v })} /></Field>
        <Field label="Format" hint="H.264 plays everywhere. H.265 makes files about 40% smaller."><Seg value={p.codec} options={[["h264", "MP4 · H.264"], ["hevc", "MP4 · H.265"]]} onChange={(v) => custom({ codec: v })} /></Field>
      </div>
      <Toggle on={p.srt} onChange={(v) => setP({ ...p, srt: v })} label="Also save a captions file (.srt)" />
      <p className="muted small">These choices are remembered for “Export all” and automatic exports too.</p>
    </Modal>
  );
}
