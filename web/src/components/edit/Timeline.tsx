// The editor's timeline: tracks, clips with filmstrips and waveforms, trim / move / split, ruler and playhead.
import { useEffect, useMemo, useRef, useState } from "react";
import { mediaUrl } from "../../lib/api";
import { Assets, clamp, cmd, El, EL_COLORS, elEnd, elLabel, EProject, getClock, Item, itemDur, itemEnd, Media, projDur, r3, setClock, settle, snapPoints, snapTime, tc, Track, uid, useClock } from "../../lib/edit";
import { Icon } from "../Icon";

type Props = { p: EProject; assets: Record<string, Assets>; sel: string[]; setSel: (ids: string[]) => void;
  begin: () => void; update: (fn: (p: EProject) => EProject) => void; commit: (fn: (p: EProject) => EProject, tag?: string) => void;
  pps: number; setPps: (v: number) => void; snap: boolean; setSnap: (v: boolean) => void; canUndo: boolean; canRedo: boolean };

const HEAD = 26, VROW = 62, AROW = 46, EROW = 24;
const STEPS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 3600];

export function Timeline({ p, assets, sel, setSel, begin, update, commit, pps, setPps, snap, setSnap, canUndo, canRedo }: Props) {
  const scroll = useRef<HTMLDivElement>(null);
  const heads = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, w: 1200 });
  const media = useMemo(() => Object.fromEntries(p.media.map((m) => [m.id, m])) as Record<string, Media>, [p.media]);
  const rows = useMemo(() => [...p.tracks.filter((t) => t.kind === "video").reverse(), ...p.tracks.filter((t) => t.kind === "audio")], [p.tracks]);
  // rows for what sits on top of the picture: captions, text & shapes (as many lanes as overlap), zooms
  const els = p.els || [];
  const lanes = useMemo(() => {
    const o: Record<string, number> = {}; const ends: number[] = [];
    for (const e of els.filter((x) => x.kind === "text" || x.kind === "shape").sort((a, b) => a.start - b.start)) {
      let l = ends.findIndex((x) => x <= e.start + 0.001); if (l < 0) { l = ends.length; ends.push(0); }
      ends[l] = elEnd(e); o[e.id] = l;
    }
    return { of: o, n: Math.max(1, ends.length) };
  }, [els]);
  const erows = { caption: { y: HEAD, h: EROW }, over: { y: HEAD + EROW, h: lanes.n * EROW }, zoom: { y: HEAD + EROW + lanes.n * EROW, h: EROW } };
  const TOP = HEAD + EROW * (2 + lanes.n);
  const rowTop = useMemo(() => { let y = TOP; const o: Record<string, { y: number; h: number }> = {};
    for (const t of rows) { const h = t.kind === "video" ? VROW : AROW; o[t.id] = { y, h }; y += h; } return o; }, [rows, TOP]);
  const totalH = TOP + rows.reduce((s, t) => s + (t.kind === "video" ? VROW : AROW), 0);
  const D = projDur(p);
  const width = Math.max(view.w, (D + Math.max(10, view.w / pps * 0.5)) * pps);
  const pRef = useRef(p); pRef.current = p;
  const ppsRef = useRef(pps); ppsRef.current = pps;

  useEffect(() => {
    const el = scroll.current!; let raf = 0;
    const on = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { setView({ x: el.scrollLeft, w: el.clientWidth }); if (heads.current) heads.current.scrollTop = el.scrollTop; }); };
    on(); el.addEventListener("scroll", on);
    const ro = new ResizeObserver(on); ro.observe(el);
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const r = el.getBoundingClientRect(), mx = e.clientX - r.left, t = (el.scrollLeft + mx) / ppsRef.current;
      const next = clamp(ppsRef.current * (e.deltaY < 0 ? 1.25 : 0.8), 0.5, 600);
      setPps(next);
      requestAnimationFrame(() => { el.scrollLeft = t * next - mx; });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => { el.removeEventListener("scroll", on); el.removeEventListener("wheel", wheel); ro.disconnect(); };
  }, []);

  const timeAt = (clientX: number) => { const el = scroll.current!; return Math.max(0, (clientX - el.getBoundingClientRect().left + el.scrollLeft) / pps); };
  const trackAt = (clientY: number): Track | undefined => {
    const el = scroll.current!; const y = clientY - el.getBoundingClientRect().top + el.scrollTop;
    return rows.find((t) => y >= rowTop[t.id].y && y < rowTop[t.id].y + rowTop[t.id].h);
  };

  const scrub = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const go = (x: number) => setClock({ t: clamp(snap ? snapTime(timeAt(x), snapPoints(pRef.current, new Set(), -1), 6 / pps) : timeAt(x), 0, Math.max(D, 0)), playing: false });
    go(e.clientX);
    const move = (ev: PointerEvent) => go(ev.clientX);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const dragItem = (e: React.PointerEvent, it: Item) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement, r = el.getBoundingClientRect();
    const edge = Math.min(8, r.width / 4);
    const mode = e.clientX - r.left < edge ? "l" : r.right - e.clientX < edge ? "r" : "move";
    let ids = sel;
    if (e.ctrlKey || e.shiftKey) { ids = sel.includes(it.id) ? sel.filter((x) => x !== it.id) : [...sel, it.id]; setSel(ids); if (!ids.includes(it.id)) return; }
    else if (!sel.includes(it.id)) { ids = [it.id]; setSel(ids); }
    const start = pRef.current, sx = e.clientX, idset = new Set(mode === "move" ? ids : [it.id]);
    const orig = Object.fromEntries(start.items.filter((x) => idset.has(x.id)).map((x) => [x.id, x]));
    const m = media[it.media];
    const others = start.items.filter((x) => x.track === it.track && x.id !== it.id);
    const prevEnd = Math.max(0, ...others.filter((x) => itemEnd(x) <= it.start + 0.001).map(itemEnd));
    const nextStart = Math.min(Infinity, ...others.filter((x) => x.start >= itemEnd(it) - 0.001).map((x) => x.start));
    const pts = snapPoints(start, idset, getClock().t), tol = 8 / pps;
    const minStart = Math.min(...Object.values(orig).map((x) => x.start));
    let moved = false;
    const move = (ev: PointerEvent) => {
      if (!moved) { if (Math.abs(ev.clientX - sx) < 3 && (mode !== "move" || !trackAt(ev.clientY) || trackAt(ev.clientY)!.id === it.track)) return; moved = true; begin(); }
      let dt = (ev.clientX - sx) / pps;
      if (mode === "move") {
        dt = Math.max(dt, -minStart);
        if (snap) {
          const a = snapTime(it.start + dt, pts, tol), b = snapTime(itemEnd(it) + dt, pts, tol);
          if (a !== it.start + dt) dt = a - it.start; else if (b !== itemEnd(it) + dt) dt = b - itemEnd(it);
        }
        const tgt = idset.size === 1 ? trackAt(ev.clientY) : undefined;
        const track = tgt && tgt.kind === (start.tracks.find((t) => t.id === it.track)?.kind) ? tgt.id : undefined;
        update((q) => ({ ...q, items: q.items.map((o) => (orig[o.id] ? { ...o, start: r3(Math.max(0, orig[o.id].start + dt)), track: track || orig[o.id].track } : o)) }));
      } else if (mode === "l") {
        const lo = Math.max(prevEnd, m.kind === "image" ? 0 : it.start - it.in / it.speed), hi = itemEnd(it) - 0.1;
        let ns = it.start + dt; if (snap) ns = snapTime(ns, pts, tol);
        ns = clamp(ns, lo, hi);
        update((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, start: r3(ns), in: m.kind === "image" ? 0 : r3(it.in + (ns - it.start) * it.speed),
          out: m.kind === "image" ? r3((itemEnd(it) - ns) * it.speed) : o.out } : o)) }));
      } else {
        const hi = Math.min(nextStart, m.kind === "image" ? Infinity : it.start + (m.duration - it.in) / it.speed), lo = it.start + 0.1;
        let ne = itemEnd(it) + dt; if (snap) ne = snapTime(ne, pts, tol);
        ne = clamp(ne, lo, hi);
        update((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, out: r3(it.in + (ne - it.start) * it.speed) } : o)) }));
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      if (moved && mode === "move") update((q) => ({ ...q, items: settle(q.items, idset) }));
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const dragEl = (e: React.PointerEvent, el: El) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const node = e.currentTarget as HTMLElement, r = node.getBoundingClientRect(), edge = Math.min(7, r.width / 4);
    const mode = e.clientX - r.left < edge ? "l" : r.right - e.clientX < edge ? "r" : "move";
    let ids = sel;
    if (e.ctrlKey || e.shiftKey) { ids = sel.includes(el.id) ? sel.filter((x) => x !== el.id) : [...sel, el.id]; setSel(ids); if (!ids.includes(el.id)) return; }
    else if (!sel.includes(el.id)) { ids = [el.id]; setSel(ids); }
    if (mode !== "move") setClock({ t: clamp(getClock().t, el.start, elEnd(el) - 0.01), playing: false });
    const start = pRef.current, sx = e.clientX, idset = new Set(mode === "move" ? ids : [el.id]);
    const orig = Object.fromEntries((start.els || []).filter((x) => idset.has(x.id)).map((x) => [x.id, x]));
    const pts = snapPoints(start, idset, getClock().t), tol = 8 / pps;
    let moved = false;
    const move = (ev: PointerEvent) => {
      if (!moved) { if (Math.abs(ev.clientX - sx) < 3) return; moved = true; begin(); }
      let dt = (ev.clientX - sx) / pps;
      if (mode === "move") {
        dt = Math.max(dt, -el.start);
        if (snap) { const a = snapTime(el.start + dt, pts, tol), b = snapTime(elEnd(el) + dt, pts, tol); if (a !== el.start + dt) dt = a - el.start; else if (b !== elEnd(el) + dt) dt = b - elEnd(el); }
        update((q) => ({ ...q, els: (q.els || []).map((o) => (orig[o.id] ? { ...o, start: r3(Math.max(0, orig[o.id].start + dt)) } : o)) }));
      } else if (mode === "l") {
        let ns = el.start + dt; if (snap) ns = snapTime(ns, pts, tol);
        ns = clamp(ns, 0, elEnd(el) - 0.15);
        update((q) => ({ ...q, els: (q.els || []).map((o) => (o.id === el.id ? { ...o, start: r3(ns), dur: r3(elEnd(el) - ns) } : o)) }));
      } else {
        let ne = elEnd(el) + dt; if (snap) ne = snapTime(ne, pts, tol);
        update((q) => ({ ...q, els: (q.els || []).map((o) => (o.id === el.id ? { ...o, dur: r3(Math.max(0.15, ne - el.start)) } : o)) }));
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      const t = getClock().t;                    // a plain click: bring the playhead onto it so it can be seen and edited
      if (!moved && (t < el.start || t >= elEnd(el))) setClock({ t: r3(el.start + Math.min(el.dur / 2, (el.ease || el.anim_in?.dur || 0) + 0.1)), playing: false });
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const drop = (e: React.DragEvent) => {
    const id = e.dataTransfer.getData("text/rr-media"); const m = media[id];
    if (!m) return;
    e.preventDefault();
    const kind = m.kind === "audio" ? "audio" : "video";
    const over = trackAt(e.clientY);
    const tr = over && over.kind === kind ? over : p.tracks.find((t) => t.kind === kind);
    if (!tr) return;
    const nid = uid();
    commit((q) => ({ ...q, items: settle([...q.items, { id: nid, track: tr.id, media: m.id, start: r3(timeAt(e.clientX)), in: 0, out: m.duration, speed: 1, volume: 1,
      muted: false, fade_in: 0, fade_out: 0, x: 0.5, y: 0.5, scale: 1, rot: 0, opacity: 1, crop: [0, 0, 0, 0] }], new Set([nid])) }));
    setSel([nid]);
  };

  const setTrack = (id: string, patch: Partial<Track>) => commit((q) => ({ ...q, tracks: q.tracks.map((t) => (t.id === id ? { ...t, ...patch } : t)) }), patch.name !== undefined ? "trackname" + id : undefined);
  const addTrack = (kind: "video" | "audio") => commit((q) => {
    const n = q.tracks.filter((t) => t.kind === kind).length + 1;
    const t: Track = { id: uid(), kind, name: `${kind === "video" ? "Video" : "Audio"} ${n}`, muted: false, hidden: false };
    return { ...q, tracks: [...q.tracks, t] };
  });
  const removeTrack = (id: string) => commit((q) => ({ ...q, tracks: q.tracks.filter((t) => t.id !== id) }));

  // ruler ticks for what is on screen
  const step = STEPS.find((s) => s * pps >= 84) || 3600;
  const ticks: number[] = [];
  for (let t = Math.floor(view.x / pps / step) * step; t < (view.x + view.w) / pps + step; t += step) ticks.push(r3(t));
  const vis = (it: Item) => itemEnd(it) * pps > view.x - 300 && it.start * pps < view.x + view.w + 300;

  return (
    <div className="tl">
      <div className="tl-bar">
        <div className="tl-tools">
          <button title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={() => cmd("undo")}><Icon name="undo" size={16} /></button>
          <button title="Redo (Ctrl+Y)" disabled={!canRedo} onClick={() => cmd("redo")}><Icon name="redo" size={16} /></button>
          <i />
          <button title="Split at the playhead (S)" onClick={() => cmd("split")}><Icon name="scissors" size={16} /><span>Split</span></button>
          <button title="Delete (Del)" disabled={!sel.length} onClick={() => cmd("delete")}><Icon name="trash" size={16} /></button>
          <button title="Delete and close the gap (Shift+Del)" disabled={!sel.length} onClick={() => cmd("ripple")}><Icon name="ripple" size={16} /><span>Ripple</span></button>
          <button title="Duplicate (Ctrl+D)" disabled={!sel.length} onClick={() => cmd("duplicate")}><Icon name="copy" size={16} /></button>
          <i />
          <button title="Snap clips to edges, playhead and markers" className={snap ? "on" : ""} onClick={() => setSnap(!snap)}><Icon name="magnet" size={16} /><span>Snap</span></button>
          <i />
          <button title="Add a video track" onClick={() => addTrack("video")}><Icon name="plus" size={14} /><span>Video track</span></button>
          <button title="Add an audio track" onClick={() => addTrack("audio")}><Icon name="plus" size={14} /><span>Audio track</span></button>
        </div>
        <div className="tl-zoom">
          <button title="Zoom out (−)" onClick={() => cmd("zoomOut")}><Icon name="minus" size={15} /></button>
          <input type="range" min={0} max={100} value={Math.round(Math.log(pps / 0.5) / Math.log(1200) * 100)} onChange={(e) => setPps(0.5 * Math.pow(1200, Number(e.target.value) / 100))} />
          <button title="Zoom in (+)" onClick={() => cmd("zoomIn")}><Icon name="plus" size={15} /></button>
          <button title="Fit everything (Shift+Z)" onClick={() => cmd("zoomFit")}><Icon name="frame" size={15} /></button>
        </div>
      </div>
      <div className="tl-body">
        <div className="tl-heads" ref={heads}>
          <div className="tl-corner" style={{ height: HEAD }} />
          <div className="tl-head el" style={{ height: erows.caption.h }}><Icon name="type" size={13} /><span>Captions</span></div>
          <div className="tl-head el" style={{ height: erows.over.h }}><Icon name="layout" size={13} /><span>Text &amp; shapes</span></div>
          <div className="tl-head el" style={{ height: erows.zoom.h }}><Icon name="search" size={13} /><span>Zoom</span></div>
          {rows.map((t) => {
            const empty = !p.items.some((it) => it.track === t.id), last = p.tracks.filter((x) => x.kind === t.kind).length <= 1;
            return (
              <div key={t.id} className={`tl-head ${t.kind}`} style={{ height: rowTop[t.id].h }}>
                <Icon name={t.kind === "video" ? "film" : "music"} size={14} />
                <input value={t.name} onChange={(e) => setTrack(t.id, { name: e.target.value })} spellCheck={false} />
                {t.kind === "video" && <button title={t.pin ? "Stays fixed while the picture zooms (click to let it zoom too)" : "Zooms with the picture (click to keep it fixed, e.g. a webcam)"} className={t.pin ? "pinned" : ""} onClick={() => setTrack(t.id, { pin: !t.pin })}><Icon name="shield" size={13} /></button>}
                {t.kind === "audio" && <button title={t.duck ? "Music: gets quieter while someone talks (click to switch off)" : "Make this a music track that gets quieter while someone talks"} className={t.duck ? "pinned" : ""} onClick={() => setTrack(t.id, { duck: !t.duck })}><Icon name="sliders" size={13} /></button>}
                {t.kind === "video" && <button title={t.hidden ? "Show this track" : "Hide this track"} className={t.hidden ? "off" : ""} onClick={() => setTrack(t.id, { hidden: !t.hidden })}><Icon name={t.hidden ? "eyeoff" : "eye"} size={14} /></button>}
                <button title={t.muted ? "Unmute this track" : "Mute this track"} className={t.muted ? "off" : ""} onClick={() => setTrack(t.id, { muted: !t.muted })}><Icon name={t.muted ? "mute" : "volume"} size={14} /></button>
                {empty && !last && <button title="Remove this empty track" onClick={() => removeTrack(t.id)}><Icon name="x" size={13} /></button>}
              </div>
            );
          })}
        </div>
        <div className="tl-scroll" ref={scroll} onDragOver={(e) => { if (e.dataTransfer.types.includes("text/rr-media")) e.preventDefault(); }} onDrop={drop}>
          <div className="tl-inner" style={{ width, height: totalH }} onPointerDown={(e) => { if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains("tl-row")) { setSel([]); scrub(e); } }}>
            <div className="tl-ruler" style={{ height: HEAD }} onPointerDown={scrub}>
              {ticks.map((t) => <span key={t} style={{ left: t * pps }}>{tc(t, p.fps, step < 1)}</span>)}
            </div>
            <div className="tl-row el" style={{ top: erows.caption.y, height: erows.caption.h }} />
            <div className="tl-row el" style={{ top: erows.over.y, height: erows.over.h }} />
            <div className="tl-row el" style={{ top: erows.zoom.y, height: erows.zoom.h }} />
            {els.filter((e) => elEnd(e) * pps > view.x - 200 && e.start * pps < view.x + view.w + 200).map((e) => {
              const row = e.kind === "caption" ? erows.caption : e.kind === "zoom" ? erows.zoom : erows.over;
              const top = row.y + (e.kind === "text" || e.kind === "shape" ? (lanes.of[e.id] || 0) * EROW : 0) + 2;
              return <div key={e.id} className={`tl-el ${e.kind} ${sel.includes(e.id) ? "on" : ""}`} style={{ left: e.start * pps, width: Math.max(4, e.dur * pps), top, height: EROW - 4, ["--c" as any]: EL_COLORS[e.kind] }}
                title={`${elLabel(e)} · ${tc(e.dur, 30, false)}${e.auto ? " · made by Auto Edit" : ""}`} onPointerDown={(ev) => dragEl(ev, e)}><span>{elLabel(e)}</span></div>;
            })}
            {(p.chapters || []).map((c, i) => <i key={"c" + i} className="tl-chapter" style={{ left: c.t * pps }} title={`Chapter: ${c.title}`} onPointerDown={(e) => { e.stopPropagation(); setClock({ t: c.t, playing: false }); }}><b>{c.title}</b></i>)}
            {rows.map((t) => <div key={t.id} className={`tl-row ${t.kind} ${t.hidden ? "hidden" : ""} ${t.muted ? "muted" : ""}`} style={{ top: rowTop[t.id].y, height: rowTop[t.id].h }} />)}
            {(p.markers || []).map((t, i) => <i key={i} className="tl-marker" style={{ left: t * pps }} title={`Marker ${i + 1} · ${tc(t, p.fps, false)}`} onPointerDown={(e) => { e.stopPropagation(); setClock({ t, playing: false }); }} />)}
            {p.items.filter(vis).map((it) => {
              const m = media[it.media], row = rowTop[it.track]; if (!m || !row) return null;
              return <Clip key={it.id} it={it} m={m} a={assets[m.id]} pps={pps} top={row.y + 3} h={row.h - 6} view={view} on={sel.includes(it.id)}
                audioRow={p.tracks.find((t) => t.id === it.track)?.kind === "audio"} onDown={(e) => dragItem(e, it)} />;
            })}
            <Playhead pps={pps} h={totalH} scroll={scroll} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Clip({ it, m, a, pps, top, h, view, on, audioRow, onDown }: { it: Item; m: Media; a?: Assets; pps: number; top: number; h: number;
  view: { x: number; w: number }; on: boolean; audioRow: boolean; onDown: (e: React.PointerEvent) => void }) {
  const left = it.start * pps, w = Math.max(3, itemDur(it) * pps);
  const film = !audioRow && m.kind !== "audio" && a?.strip && a.strip_n > 0;
  const tiles: JSX.Element[] = [];
  if (film) {
    const tw = (h - 2) * (m.width / Math.max(1, m.height)), n = a!.strip_n;
    const i0 = Math.max(0, Math.floor((view.x - left - 200) / tw)), i1 = Math.min(Math.ceil(w / tw), Math.ceil((view.x + view.w - left + 200) / tw));
    for (let i = i0; i < i1 && tiles.length < 80; i++) {
      const st = it.in + ((i + 0.5) * tw / pps) * it.speed;
      const idx = m.kind === "image" ? 0 : clamp(Math.floor(st / m.duration * n), 0, n - 1);
      tiles.push(<i key={i} style={{ left: i * tw, width: tw, backgroundImage: `url("${mediaUrl(a!.strip)}")`, backgroundSize: `${n * tw}px 100%`, backgroundPosition: `${-idx * tw}px 0` }} />);
    }
  }
  const wave = m.has_audio && a?.wave && !it.muted;
  const ws = wave ? { WebkitMaskImage: `url("${mediaUrl(a!.wave)}")`, WebkitMaskSize: `${m.duration * pps / it.speed}px 100%`, WebkitMaskPosition: `${-it.in * pps / it.speed}px 0`,
    WebkitMaskRepeat: "no-repeat" } as React.CSSProperties : undefined;
  return (
    <div className={`tl-clip ${audioRow || m.kind === "audio" ? "audio" : m.kind} ${on ? "on" : ""}`} style={{ left, width: w, top, height: h }} onPointerDown={onDown}
      title={`${m.name} · ${tc(itemDur(it), 30, false)}${it.speed !== 1 ? ` · ${it.speed}×` : ""}`}>
      {film && <div className="tl-film">{tiles}</div>}
      {wave && <div className={`tl-wave ${film ? "over" : ""}`} style={ws} />}
      <span className="tl-name">{it.speed !== 1 ? `${it.speed}× · ` : ""}{it.fx?.look && it.fx.look !== "none" ? "◐ " : ""}{it.enter?.type && it.enter.type !== "none" ? "▸ " : ""}{m.name}</span>
      {it.fade_in > 0 && <em className="tl-fade in" style={{ width: Math.min(w / 2, it.fade_in * pps) }} />}
      {it.fade_out > 0 && <em className="tl-fade out" style={{ width: Math.min(w / 2, it.fade_out * pps) }} />}
      <b className="tl-grip l" /><b className="tl-grip r" />
    </div>
  );
}

function Playhead({ pps, h, scroll }: { pps: number; h: number; scroll: React.RefObject<HTMLDivElement> }) {
  const t = useClock((c) => c.t);
  const playing = useClock((c) => c.playing);
  useEffect(() => {
    const el = scroll.current; if (!el || !playing) return;
    const x = t * pps;
    if (x > el.scrollLeft + el.clientWidth - 40 || x < el.scrollLeft) el.scrollLeft = Math.max(0, x - 80);
  }, [t, playing, pps]);
  return <div className="tl-playhead" style={{ left: t * pps, minHeight: h }}><i /></div>;
}
