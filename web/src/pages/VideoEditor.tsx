// Editor: open any video (or a screen recording), cut it on a multi-track timeline, arrange layers on the canvas, export.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { go, setState, toast, useStore } from "../lib/store";
import { Assets, clamp, cmd, Cmd, EExport, El, elEnd, EProject, Events, getClock, Item, itemBox, itemDur, itemEnd, Media, onCmd, projDur, r3, removeItems, rippleEls, setClock, settle, splitAt, tc, uid } from "../lib/edit";
import { AutoJob, ElProps, LeftPanel, Num } from "../components/edit/Panels";
import { Icon } from "../components/Icon";
import { Btn, Field, IconBtn, Modal, Progress, Seg, Select, Slider, Toggle, timeAgo } from "../components/ui";
import { Stage } from "../components/edit/Stage";
import { Timeline } from "../components/edit/Timeline";

type Listed = { id: string; name: string; updated: number; duration: number; width: number; height: number; poster: string; items: number; recording: string };

export function VideoEditorPage() {
  const pid = useStore((s) => s.editProject);
  return pid ? <Workspace key={pid} pid={pid} /> : <Home />;
}

// ================================================================ start screen
function Home() {
  const [list, setList] = useState<Listed[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => get<Listed[]>("/api/edit/list").then(setList).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); }, []);
  const open = async () => {
    setBusy(true);
    try { const p = await api<EProject | null>("/api/edit/new", { pick: true }); if (p) setState({ editProject: p.id }); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  useEffect(() => onCmd((c) => { if (c === "open" || c === "new" || c === "import") open(); }), []);
  return (
    <div className="page ve-home">
      <div className="page-head">
        <div><h1>Editor</h1><span className="muted">Open any video or screen recording, cut it on the timeline, arrange it on the canvas and export.</span></div>
        <div className="row gap">
          <Btn icon="record" onClick={() => go("studio")}>Record screen</Btn>
          <Btn kind="primary" icon="folder" busy={busy} onClick={open}>Open video…</Btn>
        </div>
      </div>
      {list && !list.length && (
        <div className="ve-empty glass depth">
          <div className="ve-empty-ic"><Icon name="timeline" size={34} /></div>
          <h2>Start your first edit</h2>
          <p className="muted">Open one or more video, audio or image files. They are placed on the timeline, ready to cut. Screen recordings open here from the Studio page with screen, webcam and sound already on their own tracks.</p>
          <div className="row gap center"><Btn kind="primary" icon="folder" busy={busy} onClick={open}>Open video…</Btn><Btn icon="record" onClick={() => go("studio")}>Record screen</Btn></div>
        </div>
      )}
      {!list && <div className="center grow"><span className="spin big" /></div>}
      {list && list.length > 0 && (
        <>
          <div className="sec-head"><h2>Projects</h2><span className="muted small">{list.length} saved · every change is saved automatically</span></div>
          <div className="ve-projects">
            {list.map((x) => (
              <div key={x.id} className="ve-pcard glass-2" onDoubleClick={() => setState({ editProject: x.id })}>
                <div className="ve-pthumb" onClick={() => setState({ editProject: x.id })}>
                  {x.poster ? <img src={mediaUrl(x.poster)} alt="" /> : <Icon name="film" size={26} />}
                  <span className="dur">{tc(x.duration, 30, false)}</span>
                </div>
                <div className="ve-pbody">
                  <b title={x.name}>{x.name}</b>
                  <span className="muted small">{timeAgo(x.updated)} · {x.width}×{x.height}{x.recording ? " · recording" : ""}</span>
                  <div className="row gap">
                    <Btn small kind="primary" icon="edit" onClick={() => setState({ editProject: x.id })}>Open</Btn>
                    <IconBtn icon="trash" danger title="Delete this project (your video files are kept)" onClick={() => { if (confirm("Delete this project? Your original video files stay on your PC.")) api("/api/edit/delete", { id: x.id }).then(load); }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ================================================================ workspace
function Workspace({ pid }: { pid: string }) {
  const [p, setP] = useState<EProject | null>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const [assets, setAssets] = useState<Record<string, Assets>>({});
  const [pps, setPpsRaw] = useState(40);
  const [snap, setSnap] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [events, setEvents] = useState<Events | null>(null);
  const [auto, setAuto] = useState<AutoJob | null>(null);
  const [tlH, setTlH] = useState(() => { try { return Number(localStorage.getItem("rr_tlh")) || 0; } catch { return 0; } });
  const [, force] = useState(0);
  const undo = useRef<EProject[]>([]), redo = useRef<EProject[]>([]);
  const pRef = useRef<EProject | null>(null); pRef.current = p;
  const selRef = useRef(sel); selRef.current = sel;
  const clip = useRef<{ items: Item[]; els: El[] }>({ items: [], els: [] });
  const lastTag = useRef({ tag: "", at: 0 });
  const dirty = useRef(false), saveTimer = useRef<any>(null), tlBox = useRef<HTMLDivElement>(null);
  const setPps = (v: number) => setPpsRaw(clamp(v, 0.5, 600));

  const save = useCallback(() => {
    clearTimeout(saveTimer.current);
    if (!dirty.current || !pRef.current) return Promise.resolve();
    dirty.current = false;
    setState({ editDirty: false });
    return api("/api/edit/save", { project: pRef.current }).catch((e) => { dirty.current = true; toast("Couldn't save: " + e.message, "error"); });
  }, []);
  const touch = () => { dirty.current = true; setState({ editDirty: true }); clearTimeout(saveTimer.current); saveTimer.current = setTimeout(save, 900); };
  const begin = useCallback(() => { if (pRef.current) { undo.current.push(pRef.current); if (undo.current.length > 120) undo.current.shift(); redo.current = []; force((n) => n + 1); } }, []);
  const update = useCallback((fn: (q: EProject) => EProject) => { setP((q) => (q ? fn(q) : q)); touch(); }, []);
  const commit = useCallback((fn: (q: EProject) => EProject, tag?: string) => {
    const now = Date.now();
    if (!tag || tag !== lastTag.current.tag || now - lastTag.current.at > 900) begin();
    lastTag.current = { tag: tag || "", at: now };
    update(fn);
  }, []);

  const loadAssets = useCallback(() => get<Record<string, Assets>>("/api/edit/assets", { id: pid }).then(setAssets).catch(() => {}), [pid]);
  useEffect(() => {
    setClock({ t: 0, playing: false });
    get<EProject>("/api/edit/project", { id: pid }).then((q) => { setP(q); setState({ editName: q.name, editDirty: false }); loadAssets(); })
      .catch((e) => setErr(e.message));
    const loadEvents = () => get<Events>("/api/edit/events", { id: pid }).then(setEvents).catch(() => {});
    loadEvents();
    const onEdit = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (d?.type === "media") loadAssets();
      if (d?.type === "auto" && d.id === pid) {
        setAuto(d);
        if (d.state === "done") get<{ project: EProject; summary: Record<string, any> }>("/api/edit/auto", { id: pid }).then((r) => {
          if (!r.project) return;
          begin(); setP(r.project); setSel([]); touch(); setClock({ t: 0, playing: false });
          const sm = r.summary || {};
          toast(`Auto Edit finished: ${sm.cuts || 0} cuts, ${sm.zooms || 0} zooms, ${sm.captions || 0} captions, ${Math.round(sm.saved || 0)} s shorter. Ctrl+Z undoes it.`, "ok");
          setTimeout(fit, 80);
        }).catch((er) => toast(er.message, "error"));
        if (d.state === "failed") toast("Auto Edit: " + d.error, "error");
      }
    };
    window.addEventListener("rr-edit", onEdit);
    return () => { window.removeEventListener("rr-edit", onEdit); save(); setClock({ t: 0, playing: false }); setState({ editName: "", editDirty: false }); };
  }, [pid]);
  useEffect(() => {          // previews still being prepared: look again until everything is ready
    if (!Object.values(assets).some((a) => !a.ready)) return;
    const t = setInterval(loadAssets, 2500); return () => clearInterval(t);
  }, [assets]);
  useEffect(() => { if (p) setState({ editName: p.name }); }, [p?.name]);

  const cursorOffset = p?.cursor?.offset;
  useEffect(() => { if (p?.cursor?.events) { const t = setTimeout(() => save().then(() => get<Events>("/api/edit/events", { id: pid }).then(setEvents).catch(() => {})), 500); return () => clearTimeout(t); } }, [cursorOffset]);
  const runAuto = async (opts: Record<string, any>) => {
    setClock({ playing: false });
    dirty.current = true; await save();
    setAuto({ state: "running", stage: "Starting", frac: 0 });
    api("/api/edit/auto", { id: pid, opts }).catch((e) => { setAuto({ state: "failed", stage: "", frac: 0, error: e.message }); });
  };
  const addEl = (part: Partial<El>) => {
    const q = pRef.current; if (!q) return;
    const t = getClock().t, id = uid();
    const dur = part.kind === "caption" ? 2.5 : part.kind === "zoom" ? 3 : part.shape === "blur" ? Math.max(3, projDur(q) - t) : 3;
    const step = part.shape === "step" ? { n: (q.els || []).filter((e) => e.shape === "step").length + 1 } : {};
    commit((x) => ({ ...x, els: [...(x.els || []), { id, kind: "text", start: r3(t), dur, ...part, ...step } as El] }));
    setSel([id]);
  };
  const fit = () => { const q = pRef.current; const w = (tlBox.current?.clientWidth || 1200) - 216; if (q) setPps(clamp(w / Math.max(4, projDur(q) * 1.04), 0.5, 600)); };
  useEffect(() => { if (p && pps === 40) fit(); }, [!!p]);

  const importMedia = async () => {
    try {
      const ms = await api<Media[]>("/api/edit/import", { id: pid });
      if (!ms.length) return;
      commit((q) => ({ ...q, media: [...q.media, ...ms.filter((m) => !q.media.some((x) => x.id === m.id))] }));
      loadAssets();
      toast(`${ms.length} file${ms.length > 1 ? "s" : ""} added. Drag ${ms.length > 1 ? "them" : "it"} onto the timeline.`, "ok");
    } catch (e: any) { toast(e.message, "error"); }
  };
  const addToTimeline = (m: Media) => {
    const q = pRef.current!; const kind = m.kind === "audio" ? "audio" : "video";
    const tr = q.tracks.find((t) => t.kind === kind)!; const id = uid();
    const start = r3(Math.max(0, ...q.items.filter((it) => it.track === tr.id).map(itemEnd)));
    commit((x) => ({ ...x, items: [...x.items, { id, track: tr.id, media: m.id, start, in: 0, out: m.duration, speed: 1, volume: 1, muted: false, fade_in: 0, fade_out: 0,
      x: 0.5, y: 0.5, scale: 1, rot: 0, opacity: 1, crop: [0, 0, 0, 0] }] }));
    setSel([id]); setClock({ t: start, playing: false });
  };

  const run = useCallback((c: Cmd) => {
    const q = pRef.current; if (!q) return;
    const s = selRef.current, t = getClock().t, D = projDur(q);
    switch (c) {
      case "undo": if (undo.current.length) { redo.current.push(q); setP(undo.current.pop()!); touch(); force((n) => n + 1); } break;
      case "redo": if (redo.current.length) { undo.current.push(q); setP(redo.current.pop()!); touch(); force((n) => n + 1); } break;
      case "split": { const r = splitAt(q, t, s); if (r.made.length) { commit(() => ({ ...q, items: r.items })); setSel(r.made); } else toast("Move the playhead over a clip to split it.", "info"); break; }
      case "delete": case "ripple": if (s.length) {
        commit((x) => ({ ...x, items: removeItems(x, s, c === "ripple"), els: (c === "ripple" ? rippleEls(x, s) : x.els || []).filter((e) => !s.includes(e.id)) })); setSel([]);
      } break;
      case "duplicate": if (s.length) {
        const made = q.items.filter((it) => s.includes(it.id)).map((it) => ({ ...it, id: uid(), start: r3(itemEnd(it)) }));
        const madeE = (q.els || []).filter((e) => s.includes(e.id)).map((e) => ({ ...e, id: uid(), start: r3(elEnd(e)), auto: false }));
        commit((x) => ({ ...x, items: settle([...x.items, ...made], new Set(made.map((m) => m.id))), els: [...(x.els || []), ...madeE] })); setSel([...made, ...madeE].map((m) => m.id));
      } break;
      case "copy": { clip.current = { items: q.items.filter((it) => s.includes(it.id)), els: (q.els || []).filter((e) => s.includes(e.id)) };
        const n = clip.current.items.length + clip.current.els.length; if (n) toast(`${n} item${n > 1 ? "s" : ""} copied`, "info"); break; }
      case "paste": if (clip.current.items.length + clip.current.els.length) {
        const t0 = Math.min(...clip.current.items.map((it) => it.start), ...clip.current.els.map((e) => e.start));
        const made = clip.current.items.filter((it) => q.tracks.some((tr) => tr.id === it.track)).map((it) => ({ ...it, id: uid(), start: r3(t + it.start - t0) }));
        const madeE = clip.current.els.map((e) => ({ ...e, id: uid(), start: r3(t + e.start - t0), auto: false }));
        commit((x) => ({ ...x, items: settle([...x.items, ...made], new Set(made.map((m) => m.id))), els: [...(x.els || []), ...madeE] })); setSel([...made, ...madeE].map((m) => m.id));
      } break;
      case "selectAll": setSel([...q.items.map((it) => it.id), ...(q.els || []).map((e) => e.id)]); break;
      case "zoomIn": setPpsRaw((v) => clamp(v * 1.4, 0.5, 600)); break;
      case "zoomOut": setPpsRaw((v) => clamp(v / 1.4, 0.5, 600)); break;
      case "zoomFit": fit(); break;
      case "play": setClock({ playing: !getClock().playing }); break;
      case "save": dirty.current = true; save()?.then(() => toast("Project saved", "ok")); break;
      case "export": if (D > 0.05) { setClock({ playing: false }); setExporting(true); } else toast("The timeline is empty. Add a video first.", "info"); break;
      case "import": importMedia(); break;
      case "home": case "open": case "new": save(); setState({ editProject: "" }); break;
    }
  }, []);
  useEffect(() => onCmd(run), [run]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const tg = e.target as HTMLElement;
      if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT") && (tg as HTMLInputElement).type !== "range") return;
      if (document.querySelector(".modal-back")) return;
      const q = pRef.current; if (!q) return;
      const k = e.key.toLowerCase(), ctrl = e.ctrlKey || e.metaKey, f = 1 / (q.fps || 30), t = getClock().t, D = projDur(q);
      const seek = (v: number) => setClock({ t: clamp(v, 0, D), playing: false });
      let hit = true;
      if (ctrl && k === "z") run(e.shiftKey ? "redo" : "undo");
      else if (ctrl && k === "y") run("redo");
      else if (ctrl && k === "d") run("duplicate");
      else if (ctrl && k === "c") run("copy");
      else if (ctrl && k === "v") run("paste");
      else if (ctrl && k === "a") run("selectAll");
      else if (ctrl && k === "s") run("save");
      else if (ctrl && k === "e") run("export");
      else if (ctrl && k === "i") run("import");
      else if (ctrl) hit = false;
      else if (k === " ") run("play");
      else if (k === "s") run("split");
      else if (k === "delete" || k === "backspace") run(e.shiftKey ? "ripple" : "delete");
      else if (k === "arrowleft") seek(t - (e.shiftKey ? 1 : f));
      else if (k === "arrowright") seek(t + (e.shiftKey ? 1 : f));
      else if (k === "home") seek(0);
      else if (k === "end") seek(D);
      else if (k === "j") seek(t - 5);
      else if (k === "k") setClock({ playing: false });
      else if (k === "l") setClock({ playing: true });
      else if (k === "+" || k === "=") run("zoomIn");
      else if (k === "-") run("zoomOut");
      else if (k === "z" && e.shiftKey) run("zoomFit");
      else if (k === "escape") setSel([]);
      else hit = false;
      if (hit) e.preventDefault();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [run]);

  const splitter = (e: React.PointerEvent) => {
    e.preventDefault();
    const y0 = e.clientY, h0 = tlBox.current!.clientHeight;
    const move = (ev: PointerEvent) => setTlH(clamp(h0 + y0 - ev.clientY, 150, window.innerHeight - 260));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); try { localStorage.setItem("rr_tlh", String(tlBox.current!.clientHeight)); } catch { /* storage may be off */ } };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  if (err) return <div className="page center"><p>{err}</p><Btn onClick={() => setState({ editProject: "" })}>Back to projects</Btn></div>;
  if (!p) return <div className="page center"><span className="spin big" /></div>;
  const selItems = p.items.filter((it) => sel.includes(it.id));
  const selEls = (p.els || []).filter((e) => sel.includes(e.id));

  return (
    <div className="ve">
      <div className="ve-head">
        <button className="ve-back" title="All projects" onClick={() => run("home")}><Icon name="left" size={16} /></button>
        <input className="ve-title" value={p.name} spellCheck={false} onChange={(e) => commit((q) => ({ ...q, name: e.target.value }), "name")} />
        <span className="grow" />
        <Btn small icon="wand" busy={auto?.state === "running"} onClick={() => runAuto({})}>Auto Edit</Btn>
        <Btn small icon="plus" onClick={importMedia}>Import media</Btn>
        <Btn small kind="primary" icon="download" onClick={() => run("export")}>Export</Btn>
      </div>
      <div className="ve-top">
        <LeftPanel p={p} assets={assets} items={selItems} commit={commit} onImport={importMedia} onAdd={addToTimeline} addEl={addEl} auto={auto} runAuto={runAuto}
          cancelAuto={() => api("/api/edit/auto/cancel", { id: pid }).catch(() => {})} />
        <Stage p={p} assets={assets} events={events} sel={sel} setSel={setSel} begin={begin} update={update} />
        {selEls.length === 1 && !selItems.length
          ? <aside className="ve-panel ve-props"><div className="ve-ptitle"><span>{selEls[0].kind === "zoom" ? "Zoom" : selEls[0].kind === "caption" ? "Caption" : selEls[0].kind === "text" ? "Text" : "Shape"}</span></div><ElProps p={p} e={selEls[0]} commit={commit} /></aside>
          : <PropsPanel p={p} items={selItems} commit={commit} />}
      </div>
      <div className="ve-split" onPointerDown={splitter} title="Drag to resize the timeline" />
      <div className="ve-tl" ref={tlBox} style={tlH ? { height: tlH } : undefined}>
        <Timeline p={p} assets={assets} sel={sel} setSel={setSel} begin={begin} update={update} commit={commit} pps={pps} setPps={setPps} snap={snap} setSnap={setSnap}
          canUndo={undo.current.length > 0} canRedo={redo.current.length > 0} />
      </div>
      <StatusBar p={p} sel={selItems} pps={pps} />
      {exporting && <ExportDialog p={p} beforeStart={save} onClose={() => setExporting(false)} />}
    </div>
  );
}

function StatusBar({ p, sel, pps }: { p: EProject; sel: Item[]; pps: number }) {
  const dirty = useStore((s) => s.editDirty);
  return (
    <div className="ve-status">
      <span className={dirty ? "warn" : "ok"}><i className="dot-s" />{dirty ? "Saving…" : "All changes saved"}</span>
      <span>{p.items.length} clip{p.items.length === 1 ? "" : "s"} · {p.tracks.length} tracks · {tc(projDur(p), p.fps, false)}</span>
      {sel.length > 0 && <span>{sel.length} selected · {tc(sel.reduce((s, it) => s + itemDur(it), 0), p.fps)}</span>}
      <span className="grow" />
      <span>Zoom {pps >= 10 ? Math.round(pps) : pps.toFixed(1)} px/s</span>
      <span>{p.width} × {p.height} · {p.fps} fps</span>
    </div>
  );
}

// ================================================================ properties
const SIZES: [string, string, number, number][] = [["1920x1080", "Full HD 16:9 · 1920×1080", 1920, 1080], ["2560x1440", "QHD 16:9 · 2560×1440", 2560, 1440],
  ["3840x2160", "4K 16:9 · 3840×2160", 3840, 2160], ["1280x720", "HD 16:9 · 1280×720", 1280, 720], ["1080x1920", "Vertical 9:16 · 1080×1920", 1080, 1920], ["1080x1080", "Square 1:1 · 1080×1080", 1080, 1080]];

function PropsPanel({ p, items, commit }: { p: EProject; items: Item[]; commit: (fn: (q: EProject) => EProject, tag?: string) => void }) {
  const it = items.length === 1 ? items[0] : null;
  const m = it ? p.media.find((x) => x.id === it.media) : undefined;
  const set = (patch: Partial<Item>, tag: string) => commit((q) => ({ ...q, items: q.items.map((o) => (items.some((s) => s.id === o.id) ? { ...o, ...patch } : o)) }), tag + (it?.id || "multi"));
  if (!items.length) {
    const cur = `${p.width}x${p.height}`;
    return (
      <aside className="ve-panel ve-props">
        <div className="ve-ptitle"><span>Project</span></div>
        <div className="ve-pbodyscroll">
          <Field label="Canvas size"><Select value={SIZES.some((s) => s[0] === cur) ? cur : cur} options={[...(SIZES.some((s) => s[0] === cur) ? [] : [[cur, `Custom · ${p.width}×${p.height}`] as [string, string]]), ...SIZES.map((s) => [s[0], s[1]] as [string, string])]}
            onChange={(v) => { const s = SIZES.find((x) => x[0] === v); if (s) commit((q) => ({ ...q, width: s[2], height: s[3] })); }} /></Field>
          <Field label="Frame rate"><Seg value={String(p.fps)} options={[["30", "30 fps"], ["60", "60 fps"]]} onChange={(v) => commit((q) => ({ ...q, fps: Number(v) }))} /></Field>
          <Field label="Background"><div className="ve-color"><input type="color" value={p.bg || "#000000"} onChange={(e) => commit((q) => ({ ...q, bg: e.target.value }), "bg")} /><span>{(p.bg || "#000000").toUpperCase()}</span></div></Field>
          <p className="muted small ve-tip">Select a clip, text, shape or zoom to change it. Looks and transitions for clips are under the sliders tab on the left.</p>
        </div>
      </aside>
    );
  }
  const visual = !!it && !!m && m.kind !== "audio" && p.tracks.find((t) => t.id === it.track)?.kind === "video";
  const first = items[0], hasAudio = items.some((x) => p.media.find((mm) => mm.id === x.media)?.has_audio);
  const fill = () => { if (!it || !m) return; const b = itemBox(p, { ...it, scale: 1 }, m); set({ scale: Math.max(p.width / b.w, p.height / b.h), x: 0.5, y: 0.5 }, "fill"); };
  return (
    <aside className="ve-panel ve-props">
      <div className="ve-ptitle"><span>{items.length > 1 ? `${items.length} clips` : m?.name || "Clip"}</span></div>
      <div className="ve-pbodyscroll">
        {visual && it && (
          <>
            <h4>Transform</h4>
            <div className="ve-quick">
              <button onClick={() => set({ scale: 1, x: 0.5, y: 0.5, rot: 0 }, "fit")}>Fit</button>
              <button onClick={fill}>Fill</button>
              <button onClick={() => set({ x: 0.5, y: 0.5 }, "centre")}>Centre</button>
              <button onClick={() => set({ scale: 1, x: 0.5, y: 0.5, rot: 0, opacity: 1, crop: [0, 0, 0, 0] }, "reset")}>Reset</button>
            </div>
            <Num label="Scale" unit="%" min={5} max={400} value={it.scale * 100} onChange={(v) => set({ scale: v / 100 }, "scale")} />
            <Num label="X" unit="%" min={-50} max={150} value={it.x * 100} onChange={(v) => set({ x: v / 100 }, "x")} />
            <Num label="Y" unit="%" min={-50} max={150} value={it.y * 100} onChange={(v) => set({ y: v / 100 }, "y")} />
            <Num label="Rotate" unit="°" min={-180} max={180} value={it.rot} onChange={(v) => set({ rot: v }, "rot")} />
            <Num label="Opacity" unit="%" min={0} max={100} value={it.opacity * 100} onChange={(v) => set({ opacity: v / 100 }, "op")} />
            <h4>Crop</h4>
            {(["Left", "Top", "Right", "Bottom"] as const).map((name, i) => (
              <Num key={name} label={name} unit="%" min={0} max={45} value={(it.crop?.[i] || 0) * 100}
                onChange={(v) => { const c = [...(it.crop || [0, 0, 0, 0])] as [number, number, number, number]; c[i] = v / 100; set({ crop: c }, "crop" + i); }} />
            ))}
          </>
        )}
        <h4>Timing</h4>
        {m?.kind !== "image" && <Num label="Speed" unit="×" min={0.25} max={4} step={0.05} value={first.speed} onChange={(v) => commit((q) => { const tr = new Set(items.map((s) => s.track)), t0 = Math.min(...items.map((s) => s.start));
          return { ...q, items: settle(q.items.map((o) => (items.some((s) => s.id === o.id) ? { ...o, speed: v } : o)), new Set(q.items.filter((o) => tr.has(o.track) && o.start > t0 && !items.some((s) => s.id === o.id)).map((o) => o.id))) }; }, "speed" + first.id)} />}
        <Num label="Fade in" unit="s" min={0} max={5} step={0.1} value={first.fade_in} onChange={(v) => set({ fade_in: v }, "fi")} />
        <Num label="Fade out" unit="s" min={0} max={5} step={0.1} value={first.fade_out} onChange={(v) => set({ fade_out: v }, "fo")} />
        {it && <div className="ve-facts"><span>Starts</span><b>{tc(it.start, p.fps)}</b><span>Length</span><b>{tc(itemDur(it), p.fps)}</b><span>Source</span><b>{tc(it.in, p.fps)} – {tc(it.out, p.fps)}</b></div>}
        {hasAudio && (
          <>
            <h4>Sound</h4>
            <Num label="Volume" unit="%" min={0} max={200} value={first.volume * 100} onChange={(v) => set({ volume: v / 100 }, "vol")} />
            <Toggle on={!!first.muted} onChange={(v) => set({ muted: v }, "mute")} label="Mute this clip" />
            <Toggle on={!!first.afx?.denoise} onChange={(v) => set({ afx: { ...first.afx, denoise: v } }, "dn")} label="Remove background noise" hint="Heard in the export" />
            <Toggle on={!!first.afx?.level} onChange={(v) => set({ afx: { ...first.afx, level: v } }, "lv")} label="Even out the volume" hint="Heard in the export" />
          </>
        )}
      </div>
    </aside>
  );
}

// ================================================================ export
function ExportDialog({ p, beforeStart, onClose }: { p: EProject; beforeStart: () => Promise<any> | undefined; onClose: () => void }) {
  const [size, setSize] = useState("");
  const [fps, setFps] = useState("0");
  const [quality, setQuality] = useState("high");
  const [name, setName] = useState(p.name);
  const [job, setJob] = useState<EExport | null>(null);
  const [busy, setBusy] = useState(false);
  const jobId = useRef("");
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d?.type === "export" && d.id === jobId.current) setJob(d); };
    window.addEventListener("rr-edit", on); return () => window.removeEventListener("rr-edit", on);
  }, []);
  const start = async () => {
    setBusy(true);
    try { await beforeStart(); const j = await api<EExport>("/api/edit/export", { id: p.id, size, fps: Number(fps), quality, name }); jobId.current = j.id; setJob(j); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const wide = p.width >= p.height;
  const sizes: [string, string][] = [["", `Same as project · ${p.width}×${p.height}`], ["4k", wide ? "4K · 3840×2160" : "4K · 2160×3840"], ["1440p", wide ? "1440p · 2560×1440" : "1440p · 1440×2560"],
    ["1080p", wide ? "1080p · 1920×1080" : "1080p · 1080×1920"], ["720p", wide ? "720p · 1280×720" : "720p · 720×1280"]];
  const running = job?.state === "running";
  return (
    <Modal title="Export video" width={540} onClose={() => { if (!running) onClose(); }}
      footer={!job ? <><Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" icon="download" busy={busy} onClick={start}>Export</Btn></>
        : running ? <Btn kind="danger" onClick={() => api("/api/edit/export/cancel", { export: job.id })}>Stop export</Btn>
        : <Btn kind="primary" onClick={onClose}>Done</Btn>}>
      {!job && (
        <div className="ve-export">
          <Field label="File name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Resolution"><Select value={size} options={sizes} onChange={setSize} /></Field>
          <div className="two">
            <Field label="Frame rate"><Seg value={fps} options={[["0", `Project (${p.fps})`], ["30", "30"], ["60", "60"]]} onChange={setFps} /></Field>
            <Field label="Quality"><Seg value={quality} options={[["high", "High"], ["medium", "Medium"], ["small", "Small file"]]} onChange={setQuality} /></Field>
          </div>
          <p className="muted small">MP4 (H.264 + AAC), {tc(projDur(p), p.fps, false)} long. Saved to your output folder under “Edited videos”.</p>
        </div>
      )}
      {job && (
        <div className="ve-export">
          <b>{job.name}</b>
          {running && <Progress frac={job.frac} label={`${Math.round(job.frac * 100)}%`} />}
          {job.state === "done" && (
            <>
              <p className="ok-line"><Icon name="check" size={16} /> Export finished · {job.width}×{job.height}</p>
              <div className="row gap wrap">
                <Btn icon="folder" onClick={() => api("/api/open", { path: job.file, select: true })}>Show in folder</Btn>
                <Btn icon="play" onClick={() => api("/api/open", { path: job.file })}>Play</Btn>
                <Btn icon="scissors" onClick={() => api("/api/edit/to_shorts", { file: job.file }).then(() => { toast("Finding the best moments for Shorts…", "ok"); onClose(); go("create"); }).catch((e) => toast(e.message, "error"))}>Make Shorts from it</Btn>
              </div>
            </>
          )}
          {job.state === "failed" && <p className="bad-line"><Icon name="alert" size={16} /> {job.error || "Export failed."}</p>}
          {job.state === "cancelled" && <p className="muted">Export stopped.</p>}
        </div>
      )}
    </Modal>
  );
}
