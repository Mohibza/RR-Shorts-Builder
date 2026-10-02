// The editor's canvas: plays every layer of the timeline in sync, shows zooms, looks, transitions, text, shapes, captions and
// cursor effects the way the export draws them, and lets you move / resize / rotate whatever is selected.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { mediaUrl } from "../../lib/api";
import { animAt, Assets, clamp, El, elEnd, EProject, Events, fadeAt, fxMatrix, fxValues, getClock, Item, itemBox, itemDur, itemEnd, Media, projDur, setClock, shapePath, tc, transAt, useClock, zoomAt, zoomKeys } from "../../lib/edit";
import { dyOf, emOf } from "../ClipPlayer";
import { Icon } from "../Icon";

type Props = { p: EProject; assets: Record<string, Assets>; events: Events | null; sel: string[]; setSel: (ids: string[]) => void;
  begin: () => void; update: (fn: (p: EProject) => EProject) => void };

export function textCss(e: El, H: number, k: number): React.CSSProperties {
  const fam = e.font || "Poppins", c = (e.size || 0.06) * H * k, q = (H / 1080) * k;
  const sw = e.box ? 0 : (e.stroke || 0) * q, sh = e.box ? 0 : (e.shadow || 0) * q;
  const hex = e.box_color || "#000000", a = e.box_alpha ?? 0.6;
  const rgba = `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;
  return {
    fontFamily: `"${fam}", "Segoe UI", sans-serif`, fontSize: c, lineHeight: `${c / emOf(fam)}px`, color: e.color || "#fff", fontWeight: e.bold ? 700 : 400,
    fontStyle: e.italic ? "italic" : undefined, textTransform: e.upper ? "uppercase" : undefined, letterSpacing: (e.spacing || 0) * q, whiteSpace: "pre", textAlign: "center",
    WebkitTextStroke: sw > 0 ? `${sw * 2}px ${e.stroke_color || "#000"}` : undefined, paintOrder: "stroke fill",
    textShadow: sh > 0 ? `${sh}px ${sh}px 0 rgba(0,0,0,.45)` : undefined,
    background: e.box ? rgba : undefined, padding: e.box ? (e.box_pad ?? 14) * q : 0, marginTop: dyOf(fam) * c / emOf(fam),
  };
}

export function Stage({ p, assets, events, sel, setSel, begin, update }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [k, setK] = useState(0.4);                    // canvas pixels -> screen pixels
  const els = useRef(new Map<string, HTMLMediaElement>());
  const boxes = useRef(new Map<string, HTMLDivElement>());
  const elBoxes = useRef(new Map<string, HTMLElement>());
  const zoomRef = useRef<HTMLDivElement>(null), fxRef = useRef<HTMLDivElement>(null), capRef = useRef<HTMLDivElement>(null);
  const [live, setLive] = useState<string[]>([]);
  const [liveEls, setLiveEls] = useState<string[]>([]);
  const [guides, setGuides] = useState<{ v: boolean; h: boolean }>({ v: false, h: false });
  const pRef = useRef(p); pRef.current = p;
  const kRef = useRef(k); kRef.current = k;
  const evRef = useRef(events); evRef.current = events;
  const selRef = useRef(sel); selRef.current = sel;
  const media = useMemo(() => Object.fromEntries(p.media.map((m) => [m.id, m])) as Record<string, Media>, [p.media]);
  const tracks = useMemo(() => Object.fromEntries(p.tracks.map((t, i) => [t.id, { ...t, z: i }])), [p.tracks]);
  const keysRef = useRef(zoomKeys(p.els || []));
  useMemo(() => { keysRef.current = zoomKeys(p.els || []); }, [p.els]);

  useLayoutEffect(() => {
    const el = wrap.current; if (!el) return;
    const fit = () => setK(Math.max(0.02, Math.min((el.clientWidth - 28) / p.width, (el.clientHeight - 28) / p.height)));
    fit();
    const ro = new ResizeObserver(fit); ro.observe(el);
    return () => ro.disconnect();
  }, [p.width, p.height]);

  // ---- the play loop: one clock, everything follows it
  useEffect(() => {
    let raf = 0, base = { t: getClock().t, wall: performance.now() }, lastT = getClock().t, wasPlaying = false, liveKey = "", elKey = "";
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const pr = pRef.current, c = getClock(), D = projDur(pr), kk = kRef.current;
      let t = c.t;
      if (c.playing) {
        if (!wasPlaying || Math.abs(c.t - lastT) > 1e-6) base = { t: c.t >= D - 0.02 ? 0 : c.t, wall: performance.now() };
        t = base.t + (performance.now() - base.wall) / 1000;
        if (t >= D) { t = D; setClock({ t, playing: false }); } else setClock({ t });
        lastT = t;
      }
      wasPlaying = getClock().playing;
      const playing = wasPlaying;
      const trk = Object.fromEntries(pr.tracks.map((x) => [x.id, x]));
      const med = Object.fromEntries(pr.media.map((x) => [x.id, x]));
      const near = pr.items.filter((it) => trk[it.track] && med[it.media] && itemEnd(it) + (it.tail || 0) > t - 0.5 && it.start < t + 2.5
        && !(trk[it.track].kind === "video" && trk[it.track].hidden));
      const key = near.map((x) => x.id).join(",");
      if (key !== liveKey) { liveKey = key; setLive(near.map((x) => x.id)); }
      const all = pr.els || [];
      const nearEls = all.filter((e) => e.kind !== "zoom" && elEnd(e) > t - 0.1 && e.start < t + 0.5);
      const ek = nearEls.map((x) => x.id).join(",");
      if (ek !== elKey) { elKey = ek; setLiveEls(nearEls.map((x) => x.id)); }

      // zoom and pan (switched off while a zoom block is being edited, so its frame can be seen on the whole picture)
      const editingZoom = all.some((e) => e.kind === "zoom" && selRef.current.includes(e.id));
      const [z, zx, zy] = editingZoom ? [1, 0.5, 0.5] : zoomAt(keysRef.current, t);
      if (zoomRef.current) {
        const ox = clamp(zx * pr.width * z - pr.width / 2, 0, pr.width * z - pr.width), oy = clamp(zy * pr.height * z - pr.height / 2, 0, pr.height * z - pr.height);
        zoomRef.current.style.transform = z > 1.0005 ? `translate(${-ox * kk}px, ${-oy * kk}px) scale(${z})` : "";
      }
      let curItem: Item | null = null;
      for (const it of near) {
        const m = med[it.media] as Media, tr = trk[it.track];
        const end = itemEnd(it), active = t >= it.start - 1e-4 && t < end + (it.tail || 0);
        const fade = active ? (t < end ? fadeAt(it, t) : 1) : 0;
        const box = boxes.current.get(it.id);
        if (box) {
          const ts = transAt(it, Math.min(t, end));
          box.style.opacity = String(active ? clamp(it.opacity ?? 1, 0, 1) * fade * ts.op : 0);
          box.style.pointerEvents = active ? "auto" : "none";
          const tint = box.querySelector<HTMLElement>(".ve-tint");
          if (tint) { tint.style.opacity = String(active ? ts.ta : 0); if (ts.tint) tint.style.background = ts.tint; }
          box.style.transform = `translate(${ts.dx * pr.width * kk}px, ${ts.dy * pr.height * kk}px)${it.rot ? ` rotate(${it.rot}deg)` : ""}${ts.sc !== 1 ? ` scale(${ts.sc})` : ""}`;
        }
        if (active && pr.cursor?.media === it.media && tr.kind === "video" && t < end) curItem = it;
        const el = els.current.get(it.id);
        if (!el || m.kind === "image" || el.readyState < 1) continue;
        const want = clamp(it.in + (t - it.start) * it.speed, 0, Math.max(0, m.duration - 0.02));
        const vol = tr.muted || it.muted || t >= end ? 0 : clamp((it.volume ?? 1) * (it.fade_in || it.fade_out ? fadeAt(it, t) : 1), 0, 1);
        if (el.muted !== (vol === 0)) el.muted = vol === 0;
        if (Math.abs(el.volume - vol) > 0.01) el.volume = vol;
        const rate = clamp(it.speed || 1, 0.1, 8);
        if (el.playbackRate !== rate) el.playbackRate = rate;
        if (active && playing) {
          if (el.paused) { if (Math.abs(el.currentTime - want) > 0.05) el.currentTime = want; el.play().catch(() => {}); }
          else if (Math.abs(el.currentTime - want) > 0.3 * rate) el.currentTime = want;
        } else {
          if (!el.paused) el.pause();
          const target = active ? want : it.in;
          if (!el.seeking && Math.abs(el.currentTime - target) > 0.035) el.currentTime = target;
        }
      }
      // text and shapes: entrance / exit animation
      let cap: El | null = null;
      for (const e of nearEls) {
        const on = t >= e.start && t < elEnd(e);
        if (e.kind === "caption") { if (on) cap = e; continue; }
        const node = elBoxes.current.get(e.id); if (!node) continue;
        if (!on) { node.style.opacity = "0"; node.style.pointerEvents = "none"; continue; }
        const a = animAt(e, t, pr.height);
        node.style.pointerEvents = "auto";
        node.style.opacity = String(a.op);
        node.style.transform = `translate(-50%, -50%) translate(${a.dx * kk}px, ${a.dy * kk}px) rotate(${(e.rot || 0) + a.rot}deg) scale(${a.sc * a.sx}, ${a.sc * a.sy})`;
        node.style.filter = a.blur > 0.05 ? `blur(${a.blur * kk}px)` : "";
        if (e.kind === "text" && e.anim_in?.type === "type") {            // typewriter: letters appear one by one
          const sp = node.querySelectorAll<HTMLElement>("span[data-c]"), n = a.chars < 0 ? sp.length : Math.min(sp.length, Math.floor(a.chars * sp.length) + 1);
          if (node.dataset.n !== String(n)) { node.dataset.n = String(n); sp.forEach((c, i) => { c.style.visibility = i < n ? "visible" : "hidden"; }); }
        }
      }
      if (capRef.current) {
        const node = capRef.current, st = pr.captions || ({} as El);
        if (cap) {
          const txt = (st.upper ? (cap.text || "").toUpperCase() : cap.text || "");
          if (node.dataset.t !== txt) { node.dataset.t = txt; node.textContent = txt; }
          const di = st.anim_in?.type && st.anim_in.type !== "none" ? Math.min(st.anim_in.dur || 0.12, cap.dur / 2) : 0;
          node.style.opacity = String(di > 0 ? clamp((t - cap.start) / di, 0, 1) : 1);
        } else node.style.opacity = "0";
      }
      // cursor effects on the screen clip
      const fx = fxRef.current, ev = evRef.current, cur = pr.cursor;
      if (fx) {
        const kids = fx.children as HTMLCollectionOf<HTMLElement>;
        for (let i = 0; i < kids.length; i++) kids[i].style.display = "none";
        if (curItem && ev && cur && med[curItem.media]) {
          const b = itemBox(pr, curItem, med[curItem.media] as Media), ts = curItem.in + (t - curItem.start) * curItem.speed;
          const [cl, ct, cr, cb] = curItem.crop || [0, 0, 0, 0];
          const X = (x: number) => (b.cx - b.w / 2 + ((x - cl) / Math.max(0.01, 1 - cl - cr)) * b.w) * kk;
          const Y = (y: number) => (b.cy - b.h / 2 + ((y - ct) / Math.max(0.01, 1 - ct - cb)) * b.h) * kk;
          if (cur.ripple !== false) {
            let n = 0;
            for (let i = ev.clicks.length - 1; i >= 0 && n < 4; i--) {
              const age = ts - ev.clicks[i][0];
              if (age < 0) continue;
              if (age > 0.55) break;
              const u = Math.pow(age / 0.55, 0.6), r = 0.024 * b.fullH * kk * (0.45 + 1.85 * u), node = kids[n++];
              node.style.display = "block";
              node.style.left = `${X(ev.clicks[i][1]) - r}px`; node.style.top = `${Y(ev.clicks[i][2]) - r}px`;
              node.style.width = node.style.height = `${2 * r}px`; node.style.borderWidth = `${r * 0.22}px`;
              node.style.borderColor = cur.ripple_color || "#FFD400"; node.style.opacity = String(0.94 * (1 - u));
            }
          }
          if (cur.highlight || cur.spotlight) {
            let i = ev.moves.length - 1;
            while (i > 0 && ev.moves[i][0] > ts) i--;
            const a = ev.moves[i], nx = ev.moves[i + 1];
            if (a) {
              let x = a[1], y = a[2];
              if (nx && ts >= a[0]) { const span = nx[0] - a[0], u = span <= 0.25 ? (ts - a[0]) / span : clamp((ts - (nx[0] - 0.1)) / 0.1, 0, 1); x += (nx[1] - x) * clamp(u, 0, 1); y += (nx[2] - y) * clamp(u, 0, 1); }
              if (cur.highlight) {
                const r = 0.034 * b.fullH * kk * (cur.size || 1), node = kids[4];
                node.style.display = "block"; node.style.left = `${X(x) - r}px`; node.style.top = `${Y(y) - r}px`; node.style.width = node.style.height = `${2 * r}px`;
                node.style.background = cur.highlight_color || "#FFD400";
              }
              if (cur.spotlight) {
                const r = 0.2 * b.fullH * kk * (cur.size || 1), node = kids[5];
                node.style.display = "block";
                node.style.background = `radial-gradient(circle ${r}px at ${X(x)}px ${Y(y)}px, transparent 96%, rgba(0,0,0,.56) 104%)`;
              }
            }
          }
        }
      }
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); els.current.forEach((e) => e.pause()); };
  }, []);

  const liveItems = live.map((id) => p.items.find((x) => x.id === id)).filter(Boolean) as Item[];
  liveItems.sort((a, b) => (tracks[a.track]?.z ?? 0) - (tracks[b.track]?.z ?? 0));
  const shown = (p.els || []).filter((e) => liveEls.includes(e.id) && (e.kind === "text" || e.kind === "shape"));
  const selItem = sel.length === 1 ? p.items.find((x) => x.id === sel[0]) : undefined;
  const selVisual = selItem && tracks[selItem.track]?.kind === "video" && media[selItem.media]?.kind !== "audio" ? selItem : undefined;
  const selEl = sel.length === 1 ? (p.els || []).find((x) => x.id === sel[0]) : undefined;
  const selActive = useClock((c) => (!!selVisual && c.t >= selVisual.start - 1e-4 && c.t < itemEnd(selVisual)) || (!!selEl && c.t >= selEl.start - 1e-4 && c.t < elEnd(selEl)));

  // ---- move / resize / rotate on the canvas (clips, text, shapes, zoom frames)
  const patch = (id: string, v: Record<string, any>) => update((q) => ({ ...q, items: q.items.map((o) => (o.id === id ? { ...o, ...v } : o)), els: (q.els || []).map((o) => (o.id === id ? { ...o, ...v } : o)) }));
  const drag = (e: React.PointerEvent, o: { id: string; x: number; y: number; rot?: number; scale?: number; size?: number; w?: number; h?: number; z?: number }, mode: "move" | "scale" | "rot", kind: "item" | "text" | "shape" | "zoom") => {
    e.preventDefault(); e.stopPropagation();
    const canvas = (wrap.current!.querySelector(".ve-canvas") as HTMLElement).getBoundingClientRect();
    const cx = canvas.left + o.x * canvas.width, cy = canvas.top + o.y * canvas.height;
    const sx = e.clientX, sy = e.clientY, d0 = Math.hypot(sx - cx, sy - cy) || 1;
    const a0 = Math.atan2(sy - cy, sx - cx) * 180 / Math.PI;
    let started = false;
    const move = (ev: PointerEvent) => {
      if (!started) { if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 3) return; started = true; begin(); }
      if (mode === "move") {
        let x = o.x + (ev.clientX - sx) / canvas.width, y = o.y + (ev.clientY - sy) / canvas.height;
        const gv = Math.abs(x - 0.5) < 0.012, gh = Math.abs(y - 0.5) < 0.012;
        if (gv) x = 0.5; if (gh) y = 0.5;
        setGuides({ v: gv, h: gh });
        if (kind === "zoom") { const z = o.z || 1.6; patch(o.id, { cx: clamp(x, 0.5 / z, 1 - 0.5 / z), cy: clamp(y, 0.5 / z, 1 - 0.5 / z) }); }
        else patch(o.id, { x, y });
      } else if (mode === "scale") {
        const f = Math.hypot(ev.clientX - cx, ev.clientY - cy) / d0;
        if (kind === "item") { const s = clamp((o.scale || 1) * f, 0.03, 10); patch(o.id, { scale: Math.abs(s - 1) < 0.02 ? 1 : s }); }
        else if (kind === "text") patch(o.id, { size: clamp((o.size || 0.06) * f, 0.012, 0.6) });
        else if (kind === "zoom") { const z = clamp((o.z || 1.6) / f, 1.1, 4); patch(o.id, { z: Math.round(z * 20) / 20, cx: clamp(o.x, 0.5 / z, 1 - 0.5 / z), cy: clamp(o.y, 0.5 / z, 1 - 0.5 / z) }); }
        else {
          const r = -(o.rot || 0) * Math.PI / 180, dx = ev.clientX - cx, dy = ev.clientY - cy;
          const lx = Math.abs(dx * Math.cos(r) - dy * Math.sin(r)), ly = Math.abs(dx * Math.sin(r) + dy * Math.cos(r));
          patch(o.id, { w: clamp(2 * lx / canvas.width, 0.01, 2), h: clamp(2 * ly / canvas.height, 0.01, 2) });
        }
      } else {
        let r = (o.rot || 0) + Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180 / Math.PI - a0;
        r = ((r + 540) % 360) - 180;
        const near = Math.round(r / 45) * 45;
        if (Math.abs(r - near) < 3) r = near;
        patch(o.id, { rot: Math.round(r * 10) / 10 });
      }
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); setGuides({ v: false, h: false }); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const geo = (it: Item) => {
    const b = itemBox(p, it, media[it.media]);
    return { b, style: { left: (b.cx - b.w / 2) * k, top: (b.cy - b.h / 2) * k, width: b.w * k, height: b.h * k } };
  };
  const layer = (it: Item) => {
    const m = media[it.media]; if (!m) return null;
    const a = assets[m.id];
    const audioOnly = m.kind === "audio" || tracks[it.track]?.kind === "audio";
    const src = m.kind === "image" ? mediaUrl(m.path) : a?.need_proxy ? (a.proxy ? mediaUrl(a.proxy) : "") : a ? mediaUrl(m.path) : "";
    const ref = (el: HTMLMediaElement | null) => { if (el) els.current.set(it.id, el); else els.current.delete(it.id); };
    if (audioOnly) return src ? <audio key={it.id} ref={ref} src={src} preload="auto" /> : null;
    const { b, style } = geo(it);
    const v = fxValues(it.fx), mat = fxMatrix(it.fx);
    const filter = [mat ? `url(#fx-${it.id})` : "", v.blur > 0.01 ? `blur(${v.blur * 6 * (b.fullH / m.height) * k}px)` : ""].filter(Boolean).join(" ");
    const inner: React.CSSProperties = { width: b.fullW * k, height: b.fullH * k, left: -b.l * b.fullW * k, top: -b.t * b.fullH * k, filter: filter || undefined };
    return (
      <div key={it.id} className="ve-layer" style={{ ...style, zIndex: tracks[it.track]?.z ?? 0, opacity: 0 }}
        ref={(el) => { if (el) boxes.current.set(it.id, el); else boxes.current.delete(it.id); }}
        onPointerDown={(e) => { setSel([it.id]); drag(e, { id: it.id, x: it.x, y: it.y, rot: it.rot, scale: it.scale }, "move", "item"); }}>
        {mat && <svg width="0" height="0" style={{ position: "absolute" }}><filter id={`fx-${it.id}`} colorInterpolationFilters="sRGB"><feColorMatrix type="matrix" values={mat} /></filter></svg>}
        {m.kind === "image" ? <img src={src} style={inner} draggable={false} alt="" />
          : src ? <video ref={ref as any} src={src} style={inner} preload="auto" playsInline />
          : <div className="ve-wait" style={inner}><span className="spin" /> {a?.error || "Preparing preview…"}</div>}
        <i className="ve-vig ve-tint" style={{ opacity: 0 }} />
        {v.vignette > 0.01 && <i className="ve-vig" style={{ background: `radial-gradient(ellipse at center, transparent ${62 - v.vignette * 22}%, rgba(0,0,0,${0.25 + v.vignette * 0.5}) 118%)` }} />}
      </div>
    );
  };
  const overlay = (e: El) => {
    const ref = (n: HTMLElement | null) => { if (n) elBoxes.current.set(e.id, n); else elBoxes.current.delete(e.id); };
    const base: React.CSSProperties = { left: (e.x ?? 0.5) * p.width * k, top: (e.y ?? 0.5) * p.height * k, opacity: 0 };
    const down = (ev: React.PointerEvent) => { setSel([e.id]); drag(ev, { id: e.id, x: e.x ?? 0.5, y: e.y ?? 0.5, rot: e.rot }, "move", e.kind === "text" ? "text" : "shape"); };
    if (e.kind === "text") return <div key={e.id + (e.anim_in?.type === "type" ? "t" : "")} ref={ref} className="ve-el ve-text" style={{ ...base, ...textCss(e, p.height, k) }} onPointerDown={down}>
      {e.anim_in?.type === "type" ? [...(e.text || "")].map((c, i) => (c === "\n" ? "\n" : <span key={i} data-c="">{c}</span>)) : e.text}</div>;
    const w = (e.w ?? 0.2) * p.width * k, h = (e.h ?? 0.2) * p.height * k, sw = (e.width || 6) * (p.height / 1080) * k;
    if (e.shape === "blur") return <div key={e.id} ref={ref} className="ve-el ve-blur" style={{ ...base, width: w, height: h, backdropFilter: `blur(${(e.strength ?? 0.6) * 22 * (p.height / 1080) * k}px)` }} onPointerDown={down} />;
    const pad = sw * 2 + 4;
    return (
      <div key={e.id} ref={ref} className="ve-el" style={{ ...base, width: w + pad * 2, height: h + pad * 2 }} onPointerDown={down}>
        <svg width={w + pad * 2} height={h + pad * 2} viewBox={`${-w / 2 - pad} ${-h / 2 - pad} ${w + pad * 2} ${h + pad * 2}`}>
          <path d={shapePath(e.shape || "rect", w, h, sw)} fill={e.color || "#FF3D6E"} fillOpacity={e.alpha ?? (e.shape === "highlight" ? 0.45 : 1)} />
          {e.shape === "step" && <text x={0} y={0} textAnchor="middle" dominantBaseline="central" fill={e.text_color || "#fff"} fontFamily='"Poppins Black", sans-serif' fontSize={h * 0.52}>{e.n || 1}</text>}
        </svg>
      </div>
    );
  };
  const selBox = () => {
    if (selVisual && selActive) {
      const { style } = geo(selVisual);
      const o = { id: selVisual.id, x: selVisual.x, y: selVisual.y, rot: selVisual.rot, scale: selVisual.scale };
      return <div className="ve-sel" style={{ ...style, transform: selVisual.rot ? `rotate(${selVisual.rot}deg)` : undefined }}>
        {["nw", "ne", "sw", "se"].map((c) => <b key={c} className={`ve-h ${c}`} onPointerDown={(e) => drag(e, o, "scale", "item")} />)}
        <b className="ve-rot" title="Rotate" onPointerDown={(e) => drag(e, o, "rot", "item")} /></div>;
    }
    if (!selEl) return null;
    if (selEl.kind === "zoom") {
      const z = selEl.z || 1.6, cx = clamp(selEl.cx ?? 0.5, 0.5 / z, 1 - 0.5 / z), cy = clamp(selEl.cy ?? 0.5, 0.5 / z, 1 - 0.5 / z);
      const w = p.width / z * k, h = p.height / z * k, o = { id: selEl.id, x: cx, y: cy, z };
      return <div className="ve-sel ve-zoomframe" style={{ left: cx * p.width * k - w / 2, top: cy * p.height * k - h / 2, width: w, height: h, pointerEvents: "auto", cursor: "move" }} onPointerDown={(e) => drag(e, o, "move", "zoom")}>
        <span>Zoom {z.toFixed(1)}× · drag to aim, corners to change</span>
        {["nw", "ne", "sw", "se"].map((c) => <b key={c} className={`ve-h ${c}`} onPointerDown={(e) => drag(e, o, "scale", "zoom")} />)}</div>;
    }
    if (!selActive || selEl.kind === "caption") return null;
    const node = elBoxes.current.get(selEl.id);
    const w = selEl.kind === "text" ? (node?.offsetWidth || 120) : (selEl.w ?? 0.2) * p.width * k, h = selEl.kind === "text" ? (node?.offsetHeight || 40) : (selEl.h ?? 0.2) * p.height * k;
    const kind = selEl.kind === "text" ? "text" : "shape";
    const o = { id: selEl.id, x: selEl.x ?? 0.5, y: selEl.y ?? 0.5, rot: selEl.rot, size: selEl.size, w: selEl.w, h: selEl.h };
    return <div className="ve-sel" style={{ left: (selEl.x ?? 0.5) * p.width * k - w / 2, top: (selEl.y ?? 0.5) * p.height * k - h / 2, width: w, height: h, transform: selEl.rot ? `rotate(${selEl.rot}deg)` : undefined }}>
      {["nw", "ne", "sw", "se"].map((c) => <b key={c} className={`ve-h ${c}`} onPointerDown={(e) => drag(e, o, "scale", kind)} />)}
      {selEl.shape !== "blur" && <b className="ve-rot" title="Rotate" onPointerDown={(e) => drag(e, o, "rot", kind)} />}</div>;
  };
  const selPinned = selEl ? !!selEl.pin || selEl.kind === "zoom" : selVisual ? !!tracks[selVisual.track]?.pin : true;
  const capStyle = p.captions || ({} as El);

  return (
    <div className="ve-stage">
      <div className="ve-stage-area" ref={wrap} onPointerDown={() => setSel([])}>
        <div className="ve-canvas" style={{ width: p.width * k, height: p.height * k, background: p.bg || "#000" }}>
          <div className="ve-zoom" ref={zoomRef}>
            {liveItems.filter((it) => !tracks[it.track]?.pin).map(layer)}
            {shown.filter((e) => !e.pin).map(overlay)}
            <div className="ve-cfx" ref={fxRef}><i className="rip" /><i className="rip" /><i className="rip" /><i className="rip" /><i className="hl" /><i className="spot" /></div>
            {!selPinned && selBox()}
          </div>
          {liveItems.filter((it) => tracks[it.track]?.pin).map(layer)}
          {shown.filter((e) => e.pin).map(overlay)}
          <div className="ve-cap" ref={capRef} style={{ ...textCss(capStyle, p.height, k), left: p.width * k / 2, top: (capStyle.y ?? 0.88) * p.height * k, opacity: 0 }}
            onPointerDown={(e) => { e.stopPropagation(); const c = (p.els || []).find((x) => x.kind === "caption" && getClock().t >= x.start && getClock().t < elEnd(x)); if (c) setSel([c.id]); }} />
          {guides.v && <i className="ve-guide v" />}
          {guides.h && <i className="ve-guide h" />}
          {selPinned && selBox()}
        </div>
      </div>
      <Transport p={p} />
    </div>
  );
}

function Transport({ p }: { p: EProject }) {
  const t = useClock((c) => c.t);
  const playing = useClock((c) => c.playing);
  const D = projDur(p), f = 1 / (p.fps || 30);
  const seek = (v: number) => setClock({ t: clamp(v, 0, D), playing: false });
  return (
    <div className="ve-transport">
      <span className="ve-tc"><b>{tc(t, p.fps)}</b><i>/ {tc(D, p.fps)}</i></span>
      <div className="ve-tbtns">
        <button title="Go to the start (Home)" onClick={() => seek(0)}><Icon name="skipstart" size={16} /></button>
        <button title="One frame back (←)" onClick={() => seek(t - f)}><Icon name="stepb" size={16} /></button>
        <button className="play" title="Play / pause (Space)" onClick={() => setClock({ playing: !playing })}><Icon name={playing ? "pause" : "play"} size={18} /></button>
        <button title="One frame forward (→)" onClick={() => seek(t + f)}><Icon name="stepf" size={16} /></button>
        <button title="Go to the end (End)" onClick={() => seek(D)}><Icon name="skipend" size={16} /></button>
      </div>
      <span className="ve-meta">{p.width} × {p.height} · {p.fps} fps</span>
    </div>
  );
}
