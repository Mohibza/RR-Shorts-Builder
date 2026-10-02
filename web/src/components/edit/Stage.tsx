// The editor's canvas: plays every layer of the timeline in sync and lets you move, resize and rotate the selected one.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { mediaUrl } from "../../lib/api";
import { Assets, clamp, EProject, fadeAt, getClock, Item, itemBox, itemEnd, Media, projDur, setClock, tc, useClock } from "../../lib/edit";
import { Icon } from "../Icon";

type Props = { p: EProject; assets: Record<string, Assets>; sel: string[]; setSel: (ids: string[]) => void;
  begin: () => void; update: (fn: (p: EProject) => EProject) => void };

export function Stage({ p, assets, sel, setSel, begin, update }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [k, setK] = useState(0.4);                    // canvas pixels -> screen pixels
  const els = useRef(new Map<string, HTMLMediaElement>());
  const boxes = useRef(new Map<string, HTMLDivElement>());
  const [live, setLive] = useState<string[]>([]);
  const [guides, setGuides] = useState<{ v: boolean; h: boolean }>({ v: false, h: false });
  const pRef = useRef(p); pRef.current = p;
  const media = useMemo(() => Object.fromEntries(p.media.map((m) => [m.id, m])) as Record<string, Media>, [p.media]);
  const tracks = useMemo(() => Object.fromEntries(p.tracks.map((t, i) => [t.id, { ...t, z: i }])), [p.tracks]);

  useLayoutEffect(() => {
    const el = wrap.current; if (!el) return;
    const fit = () => setK(Math.max(0.02, Math.min((el.clientWidth - 28) / p.width, (el.clientHeight - 28) / p.height)));
    fit();
    const ro = new ResizeObserver(fit); ro.observe(el);
    return () => ro.disconnect();
  }, [p.width, p.height]);

  // ---- the play loop: one clock, every media element follows it
  useEffect(() => {
    let raf = 0, base = { t: getClock().t, wall: performance.now() }, lastT = getClock().t, wasPlaying = false, liveKey = "";
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const pr = pRef.current, c = getClock(), D = projDur(pr);
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
      const near = pr.items.filter((it) => trk[it.track] && med[it.media] && itemEnd(it) > t - 0.5 && it.start < t + 2.5
        && !(trk[it.track].kind === "video" && trk[it.track].hidden));
      const key = near.map((x) => x.id).join(",");
      if (key !== liveKey) { liveKey = key; setLive(near.map((x) => x.id)); }
      for (const it of near) {
        const m = med[it.media] as Media, tr = trk[it.track];
        const active = t >= it.start - 1e-4 && t < itemEnd(it);
        const fade = active ? fadeAt(it, t) : 0;
        const box = boxes.current.get(it.id);
        if (box) { box.style.opacity = String(active ? clamp(it.opacity ?? 1, 0, 1) * fade : 0); box.style.pointerEvents = active ? "auto" : "none"; }
        const el = els.current.get(it.id);
        if (!el || m.kind === "image" || el.readyState < 1) continue;
        const want = clamp(it.in + (t - it.start) * it.speed, 0, Math.max(0, m.duration - 0.02));
        const vol = tr.muted || it.muted ? 0 : clamp((it.volume ?? 1) * fade, 0, 1);
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
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); els.current.forEach((e) => e.pause()); };
  }, []);

  const liveItems = live.map((id) => p.items.find((x) => x.id === id)).filter(Boolean) as Item[];
  liveItems.sort((a, b) => (tracks[a.track]?.z ?? 0) - (tracks[b.track]?.z ?? 0));
  const selItem = sel.length === 1 ? p.items.find((x) => x.id === sel[0]) : undefined;
  const selVisual = selItem && tracks[selItem.track]?.kind === "video" && media[selItem.media]?.kind !== "audio" ? selItem : undefined;
  const selActive = useClock((c) => !!selVisual && c.t >= selVisual.start - 1e-4 && c.t < itemEnd(selVisual));

  // ---- move / resize / rotate on the canvas
  const drag = (e: React.PointerEvent, it: Item, mode: "move" | "scale" | "rot") => {
    e.preventDefault(); e.stopPropagation();
    const canvas = (wrap.current!.querySelector(".ve-canvas") as HTMLElement).getBoundingClientRect();
    const cx = canvas.left + it.x * canvas.width, cy = canvas.top + it.y * canvas.height;
    const sx = e.clientX, sy = e.clientY, d0 = Math.hypot(sx - cx, sy - cy) || 1;
    const a0 = Math.atan2(sy - cy, sx - cx) * 180 / Math.PI;
    let started = false;
    const move = (ev: PointerEvent) => {
      if (!started) { if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 3) return; started = true; begin(); }
      if (mode === "move") {
        let x = it.x + (ev.clientX - sx) / canvas.width, y = it.y + (ev.clientY - sy) / canvas.height;
        const gv = Math.abs(x - 0.5) < 0.012, gh = Math.abs(y - 0.5) < 0.012;
        if (gv) x = 0.5; if (gh) y = 0.5;
        setGuides({ v: gv, h: gh });
        update((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, x, y } : o)) }));
      } else if (mode === "scale") {
        const s = clamp(it.scale * Math.hypot(ev.clientX - cx, ev.clientY - cy) / d0, 0.03, 10);
        const snap = Math.abs(s - 1) < 0.02 ? 1 : s;
        update((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, scale: snap } : o)) }));
      } else {
        let r = it.rot + Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180 / Math.PI - a0;
        r = ((r + 540) % 360) - 180;
        const near = Math.round(r / 45) * 45;
        if (Math.abs(r - near) < 3) r = near;
        update((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, rot: Math.round(r * 10) / 10 } : o)) }));
      }
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); setGuides({ v: false, h: false }); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const geo = (it: Item) => {
    const b = itemBox(p, it, media[it.media]);
    return { b, style: { left: (b.cx - b.w / 2) * k, top: (b.cy - b.h / 2) * k, width: b.w * k, height: b.h * k, transform: it.rot ? `rotate(${it.rot}deg)` : undefined } };
  };

  return (
    <div className="ve-stage">
      <div className="ve-stage-area" ref={wrap} onPointerDown={() => setSel([])}>
        <div className="ve-canvas" style={{ width: p.width * k, height: p.height * k, background: p.bg || "#000" }}>
          {liveItems.map((it) => {
            const m = media[it.media]; if (!m) return null;
            const a = assets[m.id];
            const audioOnly = m.kind === "audio" || tracks[it.track]?.kind === "audio";
            const src = m.kind === "image" ? mediaUrl(m.path) : a?.need_proxy ? (a.proxy ? mediaUrl(a.proxy) : "") : a ? mediaUrl(m.path) : "";
            const ref = (el: HTMLMediaElement | null) => { if (el) els.current.set(it.id, el); else els.current.delete(it.id); };
            if (audioOnly) return src ? <audio key={it.id} ref={ref} src={src} preload="auto" /> : null;
            const { b, style } = geo(it);
            const inner = { width: b.fullW * k, height: b.fullH * k, left: -b.l * b.fullW * k, top: -b.t * b.fullH * k };
            return (
              <div key={it.id} className="ve-layer" style={{ ...style, zIndex: tracks[it.track]?.z ?? 0, opacity: 0 }}
                ref={(el) => { if (el) boxes.current.set(it.id, el); else boxes.current.delete(it.id); }}
                onPointerDown={(e) => { setSel([it.id]); drag(e, it, "move"); }}>
                {m.kind === "image" ? <img src={src} style={inner} draggable={false} alt="" />
                  : src ? <video ref={ref as any} src={src} style={inner} preload="auto" playsInline />
                  : <div className="ve-wait" style={inner}><span className="spin" /> {a?.error || "Preparing preview…"}</div>}
              </div>
            );
          })}
          {guides.v && <i className="ve-guide v" />}
          {guides.h && <i className="ve-guide h" />}
          {selVisual && selActive && (() => {
            const { style } = geo(selVisual);
            return (
              <div className="ve-sel" style={style}>
                {["nw", "ne", "sw", "se"].map((c) => <b key={c} className={`ve-h ${c}`} onPointerDown={(e) => drag(e, selVisual, "scale")} />)}
                <b className="ve-rot" title="Rotate" onPointerDown={(e) => drag(e, selVisual, "rot")} />
              </div>
            );
          })()}
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
