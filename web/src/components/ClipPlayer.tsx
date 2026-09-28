// Instant live preview: plays the source video through the edit list (trim, removed words, pause cuts),
// frames it 9:16 like the export (face-follow / blurred fit / zoom) and draws the captions, hook, CTA,
// grade and motion on top in the browser. Nothing is rendered until you export.
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, mediaUrl } from "../lib/api";
import { drawLayout, type FrameParams } from "../lib/frame";
import { buildTimeline, cameraAt, chunkWords, cleanWord, fmt, segAt, type Timeline } from "../lib/timeline";
import type { CapStyle, Catalog, Clip, CtaStyle, Edits, HookStyle, Place, Style } from "../lib/types";
import { Icon } from "./Icon";

export type PlayerHandle = { seek: (T: number) => void; play: () => void; pause: () => void; toggle: () => void; time: () => number };

type Props = {
  clip: Clip; pid: string; edits: Edits; style: Style; catalog: Catalog | null;
  settings: { progress_bar?: boolean; watermark?: string; cta_text?: string; remove_pauses?: boolean };
  autoPlay?: boolean; muted?: boolean; controls?: boolean; loop?: boolean; hookText?: string;
  camera?: [number, number][]; framing?: string; srcWH?: [number, number];
  onTime?: (T: number, D: number) => void; className?: string; fill?: boolean;
  withAudio?: boolean;   // editor: play the sound effects + music under the voice, like the export
};

const CHAR_W: Record<string, number> = {
  "Anton": 0.47, "Bebas Neue": 0.4, "Luckiest Guy": 0.66, "Bangers": 0.5, "Permanent Marker": 0.6,
  "Poppins": 0.6, "Poppins Black": 0.68, "Archivo Black": 0.7, "Rubik Mono One": 0.85,
};
const GRADE_CSS: Record<string, string> = {
  none: "", vibrant: "saturate(1.35) contrast(1.06)", cinematic: "contrast(1.1) saturate(1.12) sepia(.08) hue-rotate(-6deg)",
  warm: "sepia(.18) saturate(1.2)", cool: "hue-rotate(10deg) saturate(1.08) contrast(1.05)",
  bw: "grayscale(1) contrast(1.28) brightness(1.02)", vintage: "sepia(.35) contrast(.95) saturate(.9)",
  moody: "brightness(.93) contrast(1.16) saturate(.85)", hdr: "saturate(1.25) contrast(1.1)",
  dream: "brightness(1.05) saturate(1.1) contrast(.96)",
};
const VIGNETTE = new Set(["cinematic", "vintage", "moody"]);
const POS: Record<string, number> = { upper: 0.33, middle: 0.52, lower: 0.67 };

function fitSize(font: string, text: string, size: number, st: { pop?: boolean; emph?: string; bord?: number; hl_box?: string; box?: boolean }) {
  const cw = CHAR_W[font] ?? 0.6;
  const grow = st.pop || st.emph ? 1.12 : 1;
  const pad = 2 * ((st.bord || 6) + (st.hl_box ? 16 : 0) + (st.box ? st.bord || 0 : 0));
  const unit = text.length * cw * grow;
  return unit > 0 ? Math.min(size, (952 - pad) / unit) : size;
}

function motionZoom(motion: string, T: number, D: number): [number, number, number] {
  const d = Math.max(D, 0.1);
  switch (motion) {
    case "slow_zoom": return [1 + 0.1 * T / d, 0, 0];
    case "zoom_out": return [1.12 - 0.1 * T / d, 0, 0];
    case "ken_burns": { const z = 1.04 + 0.08 * T / d; return [z, (1 - 1 / z) / 2 * 0.6 * (T / d - 0.5) * 2, 0]; }
    case "breathe": return [1.035 + 0.025 * Math.sin(T * 2.2), 0, 0];
    case "sway": return [1.08, (1 - 1 / 1.08) / 2 * 0.8 * Math.sin(T * 0.55), (1 - 1 / 1.08) / 2 * 0.6 * Math.sin(T * 0.37 + 1)];
    case "punch": return [1.02 + 0.06 * (Math.sin(T * 1.7) > 0.93 ? 1 : 0), 0, 0];
    default: return [1, 0, 0];
  }
}

export const ClipPlayer = forwardRef<PlayerHandle, Props>(function ClipPlayer(p, ref) {
  const { clip, edits, style, catalog, settings } = p;
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const segIdx = useRef(0);
  const audio = useRef<HTMLAudioElement>(null);
  const [audioSrc, setAudioSrc] = useState("");
  const [audioBusy, setAudioBusy] = useState(false);
  const frameRef = useRef<{ p: FrameParams; camera?: [number, number][]; v: number }>({ p: { layout: "blur_fit", cam: null, z: 1, fx: 0, fy: 0 }, v: 0 });
  const drawn = useRef({ t: -1, v: -1, w: 0 });
  const [T, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(p.muted ?? false);
  const [W, setW] = useState(300);
  const [err, setErr] = useState("");
  const [proxy, setProxy] = useState<{ file: string; offset: number } | null>(clip.proxy || null);
  const [making, setMaking] = useState(false);
  const media = proxy || clip.media;
  const off = media.offset;
  const tl: Timeline = useMemo(() => buildTimeline(clip, edits, settings.remove_pauses !== false),
    [clip.id, clip.words, JSON.stringify(edits.trim), JSON.stringify(edits.cut), JSON.stringify(edits.fix), settings.remove_pauses]);
  const tlRef = useRef(tl);
  tlRef.current = tl;
  const D = tl.D;

  const audioKey = p.withAudio ? JSON.stringify([clip.id, edits.trim, edits.cut, edits.fix, edits.hook, edits.audio, edits.style, edits.place]) : "";
  useEffect(() => {
    if (!p.withAudio) return;
    let live = true;
    const id = setTimeout(() => {
      setAudioBusy(true);
      api<{ path: string }>("/api/clip/audio", { project: p.pid, clip: clip.id, edits })
        .then((r) => { if (live) setAudioSrc(r.path ? mediaUrl(r.path) : ""); })
        .catch(() => { if (live) setAudioSrc(""); })
        .finally(() => live && setAudioBusy(false));
    }, 700);
    return () => { live = false; clearTimeout(id); };
  }, [audioKey]);

  // size tracking (captions scale with the player)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 300));
    ro.observe(el);
    setW(el.clientWidth || 300);
    return () => ro.disconnect();
  }, []);

  const seekAbsFor = useCallback((Tq: number) => {
    const t = tlRef.current;
    const i = segAt(t, Tq);
    const g = t.segs[i];
    if (!g || !video.current) return;
    segIdx.current = i;
    video.current.currentTime = Math.max(0, g.a + Math.max(0, Tq - g.t0) - off);
    setT(Tq);
  }, [off]);

  // restart at the new first frame when the edit list changes
  useEffect(() => { seekAbsFor(Math.min(T, Math.max(0, D - 0.1))); }, [tl]);

  // playback driver: jump over removed parts, loop at the end, draw the blurred background
  useEffect(() => {
    let raf = 0;
    let last = -1;
    const tick = () => {
      const v = video.current;
      const t = tlRef.current;
      if (v && t.segs.length) {
        const abs = v.currentTime + off;
        let i = segIdx.current;
        let g = t.segs[i];
        if (!g || abs < g.a - 0.4 || abs > g.b + 0.4) {             // external seek -> find where we are
          i = t.segs.findIndex((s) => abs >= s.a - 0.05 && abs < s.b);
          if (i < 0) i = 0;
          segIdx.current = i;
          g = t.segs[i];
        }
        if (!v.paused && abs >= g.b - 0.03) {
          if (i + 1 < t.segs.length) {
            segIdx.current = i + 1;
            v.currentTime = t.segs[i + 1].a - off;
          } else if (p.loop !== false) {
            segIdx.current = 0;
            v.currentTime = t.segs[0].a - off;
          } else {
            v.pause();
          }
        }
        const Tn = g.t0 + Math.max(0, Math.min(g.b, abs) - g.a);
        const au = audio.current;
        if (au && au.src) {
          if (!v.paused) {
            if (au.paused) au.play().catch(() => {});
            if (Math.abs(au.currentTime - Tn) > 0.12) au.currentTime = Tn;
          } else if (!au.paused) au.pause();
        }
        if (Math.abs(Tn - last) > 0.015) {
          last = Tn;
          setT(Tn);
          p.onTime?.(Tn, t.D);
        }
        const c = canvas.current;
        const fp = frameRef.current;
        if (c && v.readyState >= 2 && (v.currentTime !== drawn.current.t || fp.v !== drawn.current.v || c.width !== drawn.current.w)) {
          const ctx = c.getContext("2d");
          const camLay = ["smart_crop", "split", "split_reverse", "zoom45", "square"].includes(fp.p.layout);
          const cam = camLay ? cameraAt(fp.camera, abs) : null;
          if (ctx) try { drawLayout(ctx, v, c.width, c.height, { ...fp.p, cam }); drawn.current = { t: v.currentTime, v: fp.v, w: c.width }; } catch { /* not ready */ }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [off, p.loop]);

  useImperativeHandle(ref, () => ({
    seek: (x) => seekAbsFor(Math.max(0, Math.min(x, D - 0.05))),
    play: () => { video.current?.play().catch(() => {}); },
    pause: () => video.current?.pause(),
    toggle: () => { const v = video.current; if (!v) return; if (v.paused) v.play().catch(() => {}); else v.pause(); },
    time: () => T,
  }), [seekAbsFor, D, T]);

  // ------------------------------------------------------------------ framing
  const [sw, sh] = p.srcWH || [1920, 1080];
  const landscape = sw > sh * 0.8;
  let layout = style.layout || "auto";
  const framing = p.framing || clip.framing;
  if (!landscape) layout = "fit";
  else if (layout === "auto") layout = framing === "blur_fit" ? "blur_fit" : "smart_crop";
  if (landscape && sw / Math.max(sh, 1) < 9 / 16 * 1.02) layout = "blur_fit";
  const place: Place = { ...(style.place || {}) };
  const z = Math.min(2, Math.max(1, place.frame_zoom ?? 1));
  const fx = Math.min(1, Math.max(-1, place.frame_x ?? 0));
  const fy = Math.min(1, Math.max(-1, place.frame_y ?? 0));
  const [mz, mdx, mdy] = motionZoom(style.motion, T, D);
  const H = (W * 16) / 9;
  const fpKey = `${layout}|${z}|${fx}|${fy}|${(p.camera || clip.camera || []).length}`;
  if (frameRef.current.p.layout + "|" + frameRef.current.p.z + "|" + frameRef.current.p.fx + "|" + frameRef.current.p.fy + "|" + (frameRef.current.camera || []).length !== fpKey) {
    frameRef.current = { p: { layout, cam: null, z, fx, fy }, camera: p.camera || clip.camera, v: frameRef.current.v + 1 };
  }
  const dpr = Math.min(1.5, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);
  const k = W / 1080;
  const grade = GRADE_CSS[style.color_grade] ?? "";

  // ------------------------------------------------------------------ captions
  const cap: CapStyle | undefined = catalog?.captions[style.caption_style];
  const chunks = useMemo(() => cap ? chunkWords(tl.words, cap.chunk, cap.maxchars) : [], [tl, cap]);
  let capEl: React.ReactNode = null;
  if (cap && chunks.length) {
    let ci = -1;
    for (let i = 0; i < chunks.length; i++) {
      const ch = chunks[i];
      const nxt = i + 1 < chunks.length ? chunks[i + 1][0].T : D;
      let ce = Math.min(nxt, ch[ch.length - 1].TE + 0.6);
      if (nxt - ch[ch.length - 1].TE < 0.3) ce = nxt;
      if (T >= ch[0].T && T < ce) { ci = i; break; }
    }
    if (ci >= 0) {
      const ch = chunks[ci];
      const toks = ch.map((w) => cleanWord(w.w, cap.upper));
      const capScale = Math.min(1.6, Math.max(0.5, place.cap_scale ?? 1));
      const size = fitSize(cap.font, toks.join(" "), cap.size * capScale, cap) * k;
      const y = (place.cap_y ?? POS[style.position] ?? 0.7) * H;
      const stroke = cap.bord * k * 1.6;
      const shadow = cap.shadow ? `${cap.shadow * k}px ${cap.shadow * k}px 0 rgba(0,0,0,.55)` : "none";
      const glow = cap.glow ? `, 0 0 ${14 * k}px ${cap.glow}, 0 0 ${28 * k}px ${cap.glow}` : "";
      const activeIdx = ch.findIndex((w) => T >= w.T && T < w.TE + 0.02);
      const words = toks.map((t, j) => {
        const w = ch[j];
        const spoken = T >= w.T;
        const active = j === activeIdx || (activeIdx < 0 && j === ch.length - 1 && T >= w.T);
        let color = cap.primary;
        let bg: string | undefined;
        let hidden = false;
        let fill = cap.primary;
        if (cap.mode === "active") { if (active) { color = cap.active || cap.primary; if (cap.hl_box) { bg = cap.hl_box; color = cap.primary; } } }
        else if (cap.mode === "karaoke") color = spoken ? cap.primary : cap.secondary || "#fff";
        else if (cap.mode === "typewriter") { hidden = !spoken; if (active) color = cap.active || cap.primary; }
        else if (cap.mode === "oneword") { color = (cap.palette || [cap.primary])[(ci) % (cap.palette?.length || 1)]; }
        else if (cap.mode === "hollow") { fill = active ? cap.primary : "transparent"; color = fill; }
        const pop = active && (cap.pop || cap.bounce);
        return (
          <span key={j + ":" + ci} className={`cw ${pop ? "cw-pop" : ""}`} style={{
            color, visibility: hidden ? "hidden" : undefined, background: bg,
            padding: bg ? `0 ${10 * k}px` : undefined, borderRadius: bg ? 8 * k : undefined,
            WebkitTextStroke: cap.mode === "hollow" ? `${stroke}px ${cap.outline}` : undefined,
          }}>{t}</span>
        );
      });
      const shown = cap.mode === "oneword" ? [words[Math.max(0, activeIdx)]] : words;
      capEl = (
        <div className={`cap ${cap.box ? "cap-box" : ""} ${cap.slide ? "cap-slide" : ""}`} key={"c" + ci} style={{
          top: y, fontFamily: `"${cap.font}", Impact, sans-serif`, fontSize: size, lineHeight: 1.08,
          WebkitTextStroke: cap.box || cap.mode === "hollow" ? undefined : `${stroke}px ${cap.outline}`,
          textShadow: cap.box ? "none" : shadow + glow,
          background: cap.box ? hexA(cap.outline, 1 - (cap.box_alpha ?? 0)) : undefined,
          padding: cap.box ? `${cap.bord * k * 0.6}px ${cap.bord * k}px` : undefined,
          borderRadius: cap.box ? 10 * k : undefined,
          transform: `translate(-50%, -50%) rotate(${cap.tilt ? (ci % 2 ? 2 : -2) : 0}deg)`,
          opacity: cap.fade ? Math.min(1, (T - ch[0].T) / 0.15) : 1,
        }}>{shown}</div>
      );
    }
  }

  // ------------------------------------------------------------------ hook / CTA / watermark
  const hookKey = style.hook_style;
  const hook: HookStyle | undefined = hookKey ? catalog?.hooks[hookKey] : undefined;
  const hookText = (p.hookText ?? edits.hook ?? clip.clip.title ?? "").trim();
  let hookEl: React.ReactNode = null;
  if (hook && hookText) {
    const dur = place.hook_dur ? Math.max(1, place.hook_dur) : hook.dur || D;
    if (T < Math.min(D, dur)) {
      const hs = Math.min(1.6, Math.max(0.5, place.hook_scale ?? 1));
      const size = hook.size * hs * k;
      const y = (place.hook_y ?? hook.y / 1920) * H;
      const outline = hook.outline ? `${(hook.bord || 6) * k * 1.6}px ${hook.outline}` : undefined;
      hookEl = (
        <div className={`hook anim-${hook.anim}`} style={{
          top: y, fontFamily: `"${hook.font}", Impact, sans-serif`, fontSize: size, color: hook.color,
          background: hook.box ? hexA(hook.box, 1 - (hook.box_alpha ?? 0)) : undefined,
          padding: hook.box ? `${14 * k}px ${22 * k}px` : undefined, WebkitTextStroke: hook.box ? undefined : outline,
          textShadow: hook.glow ? `0 0 ${16 * k}px ${hook.glow}, 0 0 ${30 * k}px ${hook.glow}` : hook.box ? "none" : `0 ${4 * k}px 0 rgba(0,0,0,.5)`,
          maxWidth: 880 * k, textTransform: hook.upper ? "uppercase" : "none",
          transform: `translate(-50%, -50%) rotate(${hook.tilt || 0}deg)`,
        }}>{hookText}</div>
      );
    }
  }
  const cta: CtaStyle | undefined = style.cta_style ? catalog?.ctas[style.cta_style] : undefined;
  const ctaText = settings.cta_text || "";
  const ctaEl = cta && ctaText.trim() && D >= 8 && T >= D - 2.6 ? (
    <div className={`cta anim-${cta.anim}`} style={{
      top: (place.cta_y ?? 0.42) * H, fontFamily: `"${cta.font}", sans-serif`, fontSize: cta.size * k, color: cta.color,
      background: cta.box, padding: cta.box ? `${10 * k}px ${22 * k}px` : undefined, borderRadius: cta.box ? 999 : undefined,
      WebkitTextStroke: cta.outline ? `${(cta.bord || 5) * k * 1.4}px ${cta.outline}` : undefined,
      textTransform: cta.font !== "Poppins" ? "uppercase" : "none",
    }}>{ctaText}</div>
  ) : null;
  const wm = settings.watermark?.trim();
  const wmPos = place.wm_pos || "top";

  // intro effects
  let introEl: React.ReactNode = null;
  let shake = "";
  if (style.intro === "flash" && T < 0.3 && playing) introEl = <div className="intro" style={{ background: "#fff", opacity: 1 - T / 0.3 }} />;
  if (style.intro === "fade_black" && T < 0.5 && playing) introEl = <div className="intro" style={{ background: "#000", opacity: 1 - T / 0.5 }} />;
  if (style.intro === "shake" && T < 0.45) shake = `translate(${16 * k * Math.sin(T * 95)}px, ${12 * k * Math.cos(T * 83)}px)`;

  const accent = cap?.active || cap?.primary || "#FFE400";
  const src = mediaUrl(media.file);
  const onVideoError = () => {
    if (proxy || making) { if (!making) setErr("This video can't play in the preview. Use “Exact frame” or export to see it."); return; }
    setMaking(true);                      // e.g. HEVC phone video: make a small preview copy once
    api<{ file: string; offset: number }>("/api/clip/proxy", { project: p.pid, clip: clip.id })
      .then((r) => { setProxy(r); setErr(""); })
      .catch((e) => setErr(e.message || "Preview unavailable"))
      .finally(() => setMaking(false));
  };

  return (
    <div className={`player ${p.fill ? "player-fill" : ""} ${p.className || ""}`} ref={box}
      onClick={() => p.controls && (video.current?.paused ? video.current.play().catch(() => {}) : video.current?.pause())}>
      <div className="stage" style={{ height: H, transform: `${shake} scale(${mz}) translate(${-mdx * 100}%, ${-mdy * 100}%)`, filter: grade || undefined }}>
        <canvas ref={canvas} width={Math.round(W * dpr)} height={Math.round(H * dpr)} className="frame-canvas" />
        <video ref={video} src={src} className="hidden-video" playsInline muted={muted} preload="auto" autoPlay={p.autoPlay}
          poster={clip.poster ? mediaUrl(clip.poster) : undefined}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onError={onVideoError}
          onLoadedMetadata={() => seekAbsFor(T)} />
        {VIGNETTE.has(style.color_grade) && <div className="vignette" />}
      </div>
      <div className="overlay">
        {hookEl}{capEl}{ctaEl}
        {wm && <div className={`wm wm-${wmPos}`} style={{ fontSize: 40 * k * (place.wm_scale ?? 1) }}>{wm}</div>}
        {settings.progress_bar !== false && <div className="pbar" style={{ width: `${(T / Math.max(0.1, D)) * 100}%`, background: accent, height: Math.max(3, 10 * k) }} />}
        {introEl}
      </div>
      {p.withAudio && <audio ref={audio} src={audioSrc || undefined} muted={muted} preload="auto" />}
      {p.withAudio && audioBusy && p.controls && <div className="aud-busy" title="Updating sound effects and music"><span className="spin" /></div>}
      {err && <div className="player-err"><Icon name="alert" size={16} /> {err}</div>}
      {making && <div className="player-err"><span className="spin" /> Preparing a preview copy of this video…</div>}
      {p.controls && (
        <div className="pctl" onClick={(e) => e.stopPropagation()}>
          <button className="pbtn" onClick={() => video.current?.paused ? video.current.play().catch(() => {}) : video.current?.pause()}>
            <Icon name={playing ? "pause" : "play"} size={16} />
          </button>
          <input type="range" min={0} max={D} step={0.01} value={Math.min(T, D)} onChange={(e) => seekAbsFor(parseFloat(e.target.value))}
            style={{ ["--p" as any]: `${(T / Math.max(0.1, D)) * 100}%` }} />
          <span className="ptime">{fmt(T)} / {fmt(D)}</span>
          <button className="pbtn" onClick={() => setMuted(!muted)} title={muted ? "Unmute" : "Mute"}><Icon name="volume" size={15} /></button>
        </div>
      )}
      {p.controls && !playing && !err && <div className="bigplay"><Icon name="play" size={34} /></div>}
    </div>
  );
});

function hexA(hex: string, a: number) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(1, a))})`;
}
