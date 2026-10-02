// Instant live preview: plays the source video through the edit list (trim, removed words, pause cuts),
// frames it 9:16 like the export (face-follow / blurred fit / zoom) and draws the captions, hook, CTA,
// grade and motion on top in the browser. Nothing is rendered until you export.
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, mediaUrl } from "../lib/api";
import { drawLayout, type FrameParams } from "../lib/frame";
import { buildTimeline, cameraAt, chunkWords, cleanWord, fmt, segAt, type Timeline } from "../lib/timeline";
import { focusWindows, introAt, motionAt, storyAt, toFinal, toPre, type FocusWin, type StoryEl, type StoryPlan } from "../lib/fx";
import { publishTime } from "../lib/playtime";
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
  onPlan?: (plan: FxPlan) => void;
  onPlace?: (patch: Place) => void;   // editor: text on the video can be dragged (and resized with the wheel)
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
  teal: "hue-rotate(-8deg) saturate(1.1) contrast(1.05)", golden: "sepia(.25) saturate(1.2) brightness(1.03)",
  matte: "contrast(.88) saturate(.9) brightness(1.04)", cyberpunk: "hue-rotate(-20deg) saturate(1.35) contrast(1.1)",
  noir: "grayscale(1) contrast(1.45) brightness(.97)", sepia: "sepia(.9) contrast(1.05)", pastel: "saturate(.75) brightness(1.06) contrast(.92)",
  night: "hue-rotate(15deg) brightness(.9) saturate(.85) contrast(1.1)", sunrise: "sepia(.15) saturate(1.25) brightness(1.02)",
  punchy: "saturate(1.5) contrast(1.15)", faded: "contrast(.82) saturate(.8) brightness(1.05)", emerald: "hue-rotate(-12deg) saturate(1.15)",
};
const VIGNETTE = new Set(["cinematic", "vintage", "moody", "noir"]);
const POS: Record<string, number> = { upper: 0.33, middle: 0.52, lower: 0.67 };

type FM = { w: Record<string, number>; other: number; em?: number; dy?: number };
let METRICS: Record<string, FM> = {};
export function setFontMetrics(m: typeof METRICS | undefined) { if (m) METRICS = m; }

// ASS/libass font sizes measure the whole font height (win ascent+descent); CSS sizes measure the em square.
// emOf() converts, so a size-190 caption in the export and in the preview are the same pixels.
const emOf = (font: string) => METRICS[font]?.em ?? 0.8;
const dyOf = (font: string) => METRICS[font]?.dy ?? 0;    // baseline difference libass vs browser, in font sizes
function textW(font: string, text: string, size: number) {
  const m = METRICS[font];
  if (!m) return text.length * size * (CHAR_W[font] ?? 0.6);
  return size * [...text].reduce((a, c) => a + (m.w[c] ?? m.other), 0);
}
const SAFE_W = 1080 - 2 * 64;

/** captions.fit_size: largest size (up to max) at which the line fits the safe width, never below 62%. */
function fitSize(font: string, text: string, maxSize: number, st: { pop?: boolean; emph?: string; bord?: number; hl_box?: string; box?: boolean }, rtl = false) {
  const grow = st.pop || st.emph ? 1.12 : 1;
  const pad = 2 * ((st.bord ?? 6) + (st.hl_box ? 16 : 0) + (st.box ? st.bord || 0 : 0));
  const unit = textW(font, text, 1) * grow;
  if (unit <= 0) return maxSize;
  const fs = Math.floor((SAFE_W - pad) / unit);
  const lo = Math.floor(maxSize * (rtl ? 0.55 : 0.62));
  return Math.max(lo, Math.min(maxSize, fs));
}

/** captions.wrap_measured: greedy wrap with real widths; null if more than maxLines. */
function wrapMeasured(font: string, text: string, size: number, maxLines: number, width = 920): string[] | null {
  const lines: string[] = [];
  let cur = "";
  for (const w of text.split(/\s+/).filter(Boolean)) {
    const cand = (cur + " " + w).trim();
    if (cur && textW(font, cand, size) > width) { lines.push(cur); cur = w; } else cur = cand;
    if (textW(font, cur, size) > width) return null;
  }
  if (cur) lines.push(cur);
  return lines.length <= maxLines ? lines : null;
}

const isEmph = (w: string, emph: Set<string>) => {
  const k = w.toLowerCase().replace(/[^\p{L}\p{N}_']/gu, "");
  return !!k && (emph.has(k) || /^\$?\d[\d,.%]*[kmb%]?$/.test(k));
};

export type FxPlan = {
  emphasis?: string[]; tilts?: number[];
  vibe: string; vibe_name: string; seed: number; punches: [number, number][]; pack: string; pack_name: string;
  music: string; music_name: string; music_offset: number; music_why: string; motion: string; intro: string; grade: string;
  story?: StoryPlan | null;
};

// ASS \an alignment -> where the anchor point sits on the text box
const AN: Record<number, [string, string]> = {
  1: ["0", "-100%"], 2: ["-50%", "-100%"], 3: ["-100%", "-100%"], 4: ["0", "-50%"], 5: ["-50%", "-50%"],
  6: ["-100%", "-50%"], 7: ["0", "0"], 8: ["-50%", "0"], 9: ["-100%", "0"],
};

export const ClipPlayer = forwardRef<PlayerHandle, Props>(function ClipPlayer(p, ref) {
  const { clip, edits, style, catalog, settings } = p;
  // two video elements take turns: while one plays, the other already waits at the start of the next part,
  // so jumping over removed pauses/words is seamless instead of a stall on every seek
  const vA = useRef<HTMLVideoElement>(null);
  const vB = useRef<HTMLVideoElement>(null);
  const act = useRef(0);
  const vid = (i: number) => (i === 0 ? vA.current : vB.current);
  const cur = () => vid(act.current);
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const stageEl = useRef<HTMLDivElement>(null);
  const pbarEl = useRef<HTMLDivElement>(null);
  const introEl = useRef<HTMLDivElement>(null);
  const rangeEl = useRef<HTMLInputElement>(null);
  const timeEl = useRef<HTMLSpanElement>(null);
  const segIdx = useRef(0);
  const prepped = useRef(-1);         // segment index the standby video is waiting at
  const audio = useRef<HTMLAudioElement>(null);
  const [audioSrc, setAudioSrc] = useState("");
  const [audioBusy, setAudioBusy] = useState(false);
  const [plan, setPlan] = useState<FxPlan | null>(null);
  const frameRef = useRef<{ p: FrameParams; camera?: [number, number][]; v: number }>({ p: { layout: "blur_fit", cam: null, z: 1, fx: 0, fy: 0 }, v: 0 });
  const drawn = useRef({ t: -1, v: -1, w: 0, el: 0 });
  const Tref = useRef(0);
  const [T, setT] = useState(0);       // only changes when the captions/hook/CTA on screen change
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
  // Story FX: freezes hold the picture (the clock, music and effects keep going), so the Short gets longer
  const [story, setStory] = useState<StoryPlan | null>(null);
  const freezes = story?.freezes || [];
  const frRef = useRef(freezes);
  frRef.current = freezes;
  const spans = story?.spans || [];
  const D = tl.D + freezes.reduce((x, f) => x + f.d, 0);
  const Dref = useRef(D);
  Dref.current = D;
  const tlF = useMemo(() => freezes.length ? { ...tl, words: tl.words.map((w) => ({ ...w, T: toFinal(w.T, freezes), TE: toFinal(w.TE, freezes) })) } : tl,
    [tl, JSON.stringify(freezes)]);
  // the freeze being held right now: elapsed0 + running clock
  const frozen = useRef<{ i: number; base: number; d: number; el0: number; at: number; run: boolean } | null>(null);
  const handled = useRef(new Set<number>());
  const lastPre = useRef(0);
  const streakBlur = useRef<SVGFEGaussianBlurElement>(null);
  const leakEl = useRef<HTMLDivElement>(null);
  const flashEl = useRef<HTMLDivElement>(null);
  const filterId = useMemo(() => "hb" + Math.random().toString(36).slice(2, 8), []);
  // dragging text on the video: shown live from this offset, saved once when the mouse is released
  type DragKind = "cap" | "hook" | "cta" | "title" | "beat";
  const [dragOff, setDragOff] = useState<{ kind: DragKind; dx: number; dy: number } | null>(null);
  const curPos = useRef<Record<string, [number, number]>>({});
  useEffect(() => { setDragOff((d) => (d && (d.kind === "title" || d.kind === "beat") ? null : d)); }, [story]);

  const audioKey = p.withAudio ? JSON.stringify([clip.id, edits.trim, edits.cut, edits.fix, edits.hook, edits.audio, edits.style, edits.place, edits.zooms, edits.zoom_mult, edits.vibe]) : "";
  useEffect(() => {
    if (!p.withAudio) return;
    let live = true;
    const id = setTimeout(() => {
      setAudioBusy(true);
      api<{ path: string; plan?: FxPlan }>("/api/clip/audio", { project: p.pid, clip: clip.id, edits })
        .then((r) => { if (!live) return; setAudioSrc(r.path ? mediaUrl(r.path) : ""); if (r.plan) { setPlan(r.plan); setStory(r.plan.story && r.plan.story.level !== "off" ? r.plan.story : null); p.onPlan?.(r.plan); } })
        .catch(() => { if (live) setAudioSrc(""); })
        .finally(() => live && setAudioBusy(false));
    }, 450);
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

  const prepStandby = useCallback((next: number) => {
    const t = tlRef.current;
    const sb = vid(1 - act.current);
    const g = t.segs[next];
    if (!sb || !g) { prepped.current = -1; return; }
    if (prepped.current === next && Math.abs(sb.currentTime - (g.a - off)) < 0.08) return;
    prepped.current = next;
    try { sb.pause(); sb.currentTime = Math.max(0, g.a - off); } catch { /* not loaded yet */ }
  }, [off]);

  const seekAbsFor = useCallback((Tq: number) => {
    const t = tlRef.current;
    const fr = frRef.current;
    const [pre, fi] = toPre(Tq, fr);
    const i = segAt(t, pre);
    const g = t.segs[i];
    const v = cur();
    if (!g || !v) return;
    segIdx.current = i;
    v.currentTime = Math.max(0, g.a + Math.max(0, pre - g.t0) - off);
    handled.current = new Set(fr.map((f, k) => (f.t < pre - 1e-3 ? k : -1)).filter((k) => k >= 0));
    if (fi >= 0) {
      const sorted = [...fr].sort((a, b) => a.t - b.t);
      const base = toFinal(sorted[fi].t, fr);
      frozen.current = { i: fi, base, d: sorted[fi].d, el0: Tq - base, at: performance.now(), run: false };
      handled.current.add(fi);
    } else frozen.current = null;
    lastPre.current = pre;
    Tref.current = Tq;
    setT(Tq);
    publishTime(Tq);
    prepStandby(i + 1 < t.segs.length ? i + 1 : 0);
  }, [off, prepStandby]);

  // restart at the new first frame when the edit list changes
  useEffect(() => { prepped.current = -1; seekAbsFor(Math.min(Tref.current, Math.max(0, D - 0.1))); }, [tl, JSON.stringify(freezes)]);

  // everything that changes every frame is written straight to the DOM (no React re-render)
  const fxRef = useRef<{ motion: string; intro: string; wins: FocusWin[]; D: number; keyFn: (t: number) => string; story: StoryPlan | null }>({ motion: "none", intro: "none", wins: [], D: 1, keyFn: () => "", story: null });
  const applyFrame = (Tn: number) => {
    const f = fxRef.current;
    const [z, dx, dy] = motionAt(f.motion, f.intro, f.wins, Tn, f.D, f.story);
    const st = stageEl.current;
    if (st) {
      st.style.transform = `scale(${z}) translate(${-dx * 100}%, ${-dy * 100}%)`;
      const io = introAt(f.intro, Tn);
      const live = !cur()?.paused || !!frozen.current?.run;
      if (!live) { io.alpha = 0; io.rgb = false; }   // flashes only while playing (no white still frame)
      const sx = storyAt(f.story, Tn);
      const grade = st.dataset.grade || "";
      const k = (st.clientWidth || 300) / 1080;
      if (streakBlur.current) streakBlur.current.setAttribute("stdDeviation", `${(sx.blur * k).toFixed(2)} 0`);
      st.style.filter = (grade + (io.rgb ? " drop-shadow(6px 0 rgba(255,0,60,.8)) drop-shadow(-6px 0 rgba(0,220,255,.8))" : "")
        + (sx.frozen ? " saturate(.38) brightness(.95) contrast(1.06)" : "") + (sx.blur > 0 ? ` url(#${filterId})` : "")).trim() || "";
      st.classList.toggle("frozen", sx.frozen);
      if (leakEl.current) leakEl.current.style.opacity = String(sx.leak);
      if (flashEl.current) flashEl.current.style.opacity = String(live ? sx.flash : 0);
      const ie = introEl.current;
      if (ie) { ie.style.opacity = String(io.alpha); if (io.color) ie.style.background = io.color; }
    }
    if (pbarEl.current) pbarEl.current.style.width = `${(Tn / Math.max(0.1, f.D)) * 100}%`;
    if (rangeEl.current && document.activeElement !== rangeEl.current) {
      rangeEl.current.value = String(Math.min(Tn, f.D));
      rangeEl.current.style.setProperty("--p", `${(Tn / Math.max(0.1, f.D)) * 100}%`);
    }
    if (timeEl.current) timeEl.current.textContent = `${fmt(Tn)} / ${fmt(f.D)}`;
  };

  // playback driver: jump over removed parts (seamless swap), loop at the end, draw the frame
  useEffect(() => {
    let raf = 0;
    let lastKey = "";
    let lastPub = -1;
    const tick = () => {
      const v = cur();
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
        const nextI = i + 1 < t.segs.length ? i + 1 : (p.loop !== false ? 0 : -1);
        if (!v.paused && abs >= g.b - 0.035) {
          const sb = vid(1 - act.current);
          const ng = nextI >= 0 ? t.segs[nextI] : null;
          if (!ng) {
            v.pause();
          } else if (sb && prepped.current === nextI && sb.readyState >= 2 && !sb.seeking) {
            // swap: the standby is already sitting on the next part's first frame
            sb.muted = muted;
            sb.play().catch(() => {});
            v.pause();
            v.muted = true;
            act.current = 1 - act.current;
            segIdx.current = nextI;
            prepped.current = -1;
            const after = nextI + 1 < t.segs.length ? nextI + 1 : 0;
            setTimeout(() => prepStandby(after), 30);
          } else {
            segIdx.current = nextI;
            v.currentTime = ng.a - off;
          }
        } else if (prepped.current !== nextI && nextI >= 0 && !v.paused) {
          prepStandby(nextI);
        }
        const a = cur()!;
        const gg = t.segs[segIdx.current];
        const absA = a.currentTime + off;
        const Tp = gg.t0 + Math.max(0, Math.min(gg.b, absA) - gg.a);     // cut timeline
        // Story FX freezes: hold the frame, keep the clock running, then carry on
        const fr = frRef.current;
        let Tn = toFinal(Tp, fr);
        const now = performance.now();
        if (Tp < lastPre.current - 0.3) handled.current = new Set([...handled.current].filter((k) => fr[k] && fr[k].t < Tp));
        const fz = frozen.current;
        if (fz) {
          const el = fz.el0 + (fz.run ? (now - fz.at) / 1000 : 0);
          if (el >= fz.d) {
            frozen.current = null;
            if (fz.run) a.play().catch(() => {});
            Tn = fz.base + fz.d;
          } else Tn = fz.base + el;
        } else if (!a.paused) {
          const sorted = [...fr].sort((x, y) => x.t - y.t);
          for (let k = 0; k < sorted.length; k++) {
            const f = sorted[k];
            if (!handled.current.has(k) && lastPre.current < f.t + 1e-3 && Tp >= f.t - 0.01) {
              handled.current.add(k);
              const base = toFinal(f.t, fr);
              frozen.current = { i: k, base, d: f.d, el0: 0, at: now, run: true };
              a.pause();
              Tn = base;
              break;
            }
          }
        }
        lastPre.current = Tp;
        Tref.current = Tn;
        const au = audio.current;
        const running = !a.paused || !!frozen.current?.run;
        if (au && au.src) {
          if (running) {
            if (au.paused) au.play().catch(() => {});
            if (Math.abs(au.currentTime - Tn) > 0.15) au.currentTime = Tn;
          } else if (!au.paused) au.pause();
        }
        applyFrame(Tn);
        const k = fxRef.current.keyFn(Tn);
        if (k !== lastKey) { lastKey = k; setT(Tn); }
        if (Math.abs(Tn - lastPub) > 0.04) { lastPub = Tn; publishTime(Tn); p.onTime?.(Tn, Dref.current); }
        const c = canvas.current;
        const fp = frameRef.current;
        if (c && a.readyState >= 2 && (a.currentTime !== drawn.current.t || fp.v !== drawn.current.v || c.width !== drawn.current.w || act.current !== drawn.current.el)) {
          const ctx = c.getContext("2d");
          const camLay = ["smart_crop", "split", "split_reverse", "zoom45", "square"].includes(fp.p.layout);
          const cam = camLay ? cameraAt(fp.camera, absA) : null;
          if (ctx) try { drawLayout(ctx, a, c.width, c.height, { ...fp.p, cam }); drawn.current = { t: a.currentTime, v: fp.v, w: c.width, el: act.current }; } catch { /* not ready */ }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [off, p.loop, muted, prepStandby]);

  const playPause = () => {
    const v = cur();
    if (!v) return;
    const fz = frozen.current;
    if (fz) {                                   // paused or playing inside a freeze: run/stop its clock
      if (fz.run) { fz.el0 += (performance.now() - fz.at) / 1000; fz.run = false; setPlaying(false); }
      else { fz.at = performance.now(); fz.run = true; setPlaying(true); }
      return;
    }
    if (v.paused) v.play().catch(() => {}); else v.pause();
  };
  useImperativeHandle(ref, () => ({
    seek: (x) => seekAbsFor(Math.max(0, Math.min(x, D - 0.05))),
    play: () => { const fz = frozen.current; if (fz) { if (!fz.run) { fz.at = performance.now(); fz.run = true; setPlaying(true); } } else cur()?.play().catch(() => {}); },
    pause: () => { const fz = frozen.current; if (fz?.run) { fz.el0 += (performance.now() - fz.at) / 1000; fz.run = false; setPlaying(false); } cur()?.pause(); },
    toggle: playPause,
    time: () => Tref.current,
  }), [seekAbsFor, D]);

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
  // the vibe engine's resolved choices (from the server) — "auto" looks follow them exactly like the export
  const motion = style.motion === "auto" ? (plan?.motion || "none") : style.motion;
  const intro = style.intro === "auto" ? (plan?.intro || "none") : style.intro;
  const gradeKey = style.color_grade === "auto" ? (plan?.grade || "none") : style.color_grade;
  const H = (W * 16) / 9;
  const fpKey = `${layout}|${z}|${fx}|${fy}|${(p.camera || clip.camera || []).length}`;
  if (frameRef.current.p.layout + "|" + frameRef.current.p.z + "|" + frameRef.current.p.fx + "|" + frameRef.current.p.fy + "|" + (frameRef.current.camera || []).length !== fpKey) {
    frameRef.current = { p: { layout, cam: null, z, fx, fy }, camera: p.camera || clip.camera, v: frameRef.current.v + 1 };
  }
  const dpr = Math.min(1.5, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);
  const k = W / 1080;
  const grade = GRADE_CSS[gradeKey] ?? "";

  // ------------------------------------------------------------------ drag text on the video (editor only)
  const dragProps = (kind: DragKind) => {
    if (!p.onPlace) return {};
    const SCALE: Record<DragKind, keyof Place | null> = { cap: "cap_scale", hook: "hook_scale", cta: null, title: "title_scale", beat: "beat_scale" };
    return {
      title: "Drag to move · mouse wheel to resize",
      onClick: (e: React.MouseEvent) => e.stopPropagation(),
      onWheel: (e: React.WheelEvent) => {
        const key = SCALE[kind];
        if (!key) return;
        e.stopPropagation();
        const cur = (place[key] as number | undefined) ?? 1;
        const lim: [number, number] = kind === "title" || kind === "beat" ? [0.4, 2] : [0.5, 1.6];
        p.onPlace!({ [key]: Math.round(Math.min(lim[1], Math.max(lim[0], cur + (e.deltaY < 0 ? 0.05 : -0.05))) * 100) / 100 });
      },
      onPointerDown: (e: React.PointerEvent) => {
        if (e.button !== 0) return;
        e.preventDefault(); e.stopPropagation();
        const x0 = e.clientX, y0 = e.clientY;
        const bw = box.current?.clientWidth || W, bh = (bw * 16) / 9;
        let last = { dx: 0, dy: 0 };
        const mv = (ev: PointerEvent) => {
          last = { dx: (ev.clientX - x0) / bw, dy: (ev.clientY - y0) / bh };
          setDragOff({ kind, ...last });
        };
        const up = () => {
          window.removeEventListener("pointermove", mv);
          window.removeEventListener("pointerup", up);
          if (Math.abs(last.dx) + Math.abs(last.dy) < 0.004) { setDragOff(null); return; }
          const r = (v: number) => Math.round(v * 1000) / 1000;
          if (kind === "title" || kind === "beat") {
            // the server lays these out again from the offset; keep showing the dragged spot until it answers
            p.onPlace!({ [`${kind}_dx`]: r(((place as any)[`${kind}_dx`] ?? 0) + last.dx), [`${kind}_dy`]: r(((place as any)[`${kind}_dy`] ?? 0) + last.dy) });
          } else {
            const [bx, by] = curPos.current[kind] || [0.5, 0.5];
            p.onPlace!({ [`${kind}_x`]: r(Math.min(0.93, Math.max(0.07, bx + last.dx))), [`${kind}_y`]: r(Math.min(0.93, Math.max(0.08, by + last.dy))) });
            setDragOff(null);
          }
        };
        window.addEventListener("pointermove", mv);
        window.addEventListener("pointerup", up);
      },
    };
  };

  // ------------------------------------------------------------------ captions
  const cap: CapStyle | undefined = catalog?.captions[style.caption_style];
  const chunks = useMemo(() => cap ? chunkWords(tlF.words, cap.chunk, cap.maxchars) : [], [tlF, cap]);
  const chunkAt = (t: number) => {
    for (let i = 0; i < chunks.length; i++) {
      const ch = chunks[i];
      const nxt = i + 1 < chunks.length ? chunks[i + 1][0].T : D;
      let ce = Math.min(nxt, ch[ch.length - 1].TE + 0.6);
      if (nxt - ch[ch.length - 1].TE < 0.3) ce = nxt;
      ce = Math.min(ce, D);
      for (const [ba] of spans) if (ch[ch.length - 1].TE - 0.05 <= ba && ba < ce) ce = Math.max(ch[0].T + 0.05, ba);
      if (t >= ch[0].T && t < ce) return i;
    }
    return -1;
  };
  const hookKey0 = story?.title ? "" : style.hook_style;      // the editorial title replaces the hook banner
  const hook0: HookStyle | undefined = hookKey0 ? catalog?.hooks[hookKey0] : undefined;
  const hookEnd = hook0 ? Math.min(D, place.hook_dur ? Math.max(1, place.hook_dur) : hook0.dur || D) : 0;
  const wins = useMemo(() => focusWindows((plan?.punches || []) as [number, number][], D), [plan, D]);
  // what's on screen at time t: re-render only when this changes (word by word), not every frame
  const storyEls = useMemo(() => [...(story?.title?.els || []), ...(story?.beats || []).flatMap((b) => b.els)], [story]);
  fxRef.current = {
    motion, intro, wins, D, story,
    keyFn: (t: number) => {
      const ci = chunkAt(t);
      const ch = ci >= 0 ? chunks[ci] : null;
      const ai = ch ? ch.findIndex((w) => t >= w.T && t < w.TE + 0.02) : -1;
      const sp = ch ? ch.filter((w) => t >= w.T).length : 0;
      const se = storyEls.map((e) => (t >= e.t0 && t < e.t1 ? (t > e.t1 - 0.26 ? 2 : 1) : 0)).join("");
      return `${ci}|${ai}|${sp}|${t < hookEnd ? 1 : 0}|${t >= D - 2.6 ? 1 : 0}|${se}`;
    },
  };
  const emph = useMemo(() => new Set(plan?.emphasis || []), [plan]);
  let capEl: React.ReactNode = null;
  if (cap && chunks.length) {
    const ci = chunkAt(T);
    if (ci >= 0) {
      const ch = chunks[ci];
      const toks = ch.map((w) => cleanWord(w.w, cap.upper));
      const capScale = Math.min(1.6, Math.max(0.5, place.cap_scale ?? 1));
      const size0 = Math.floor(cap.size * capScale);
      // same size / two-line decision as the export (captions.build_captions)
      let fs = fitSize(cap.font, toks.join(" "), size0, cap);
      let split = -1;
      if (toks.length >= 3 && fs < size0 * 0.85) {
        let best: [number, number] | null = null;
        for (let j = 1; j < toks.length; j++) {
          const f2 = Math.min(fitSize(cap.font, toks.slice(0, j).join(" "), size0, cap), fitSize(cap.font, toks.slice(j).join(" "), size0, cap));
          if (!best || f2 > best[0]) best = [f2, j];
        }
        if (best && best[0] > fs * 1.15) { fs = best[0]; split = best[1]; }
      }
      const em = emOf(cap.font);
      const px = fs * k;                          // ASS size in preview pixels
      const capYf = place.cap_y != null ? Math.min(1790, Math.max(140, place.cap_y * 1920)) / 1920 : POS[style.position] ?? 0.7;
      const capXf = place.cap_x != null ? Math.min(1000, Math.max(80, place.cap_x * 1080)) / 1080 : 0.5;
      curPos.current.cap = [capXf, capYf];
      const dC = dragOff?.kind === "cap" ? dragOff : null;
      const y = (capYf + (dC?.dy ?? 0)) * H;
      const capLeft = (capXf + (dC?.dx ?? 0)) * W;
      const stroke = cap.bord * k * 2;
      const shadow = cap.shadow ? `${cap.shadow * k}px ${cap.shadow * k}px 0 rgba(0,0,0,.55)` : "none";
      const glow = cap.glow ? `, 0 0 ${14 * k}px ${cap.glow}, 0 0 ${28 * k}px ${cap.glow}` : "";
      const activeIdx = ch.findIndex((w) => T >= w.T && T < w.TE + 0.02);
      const space = (METRICS[cap.font]?.w[" "] ?? 0.25) * px;
      const act = cap.active || "#FFE400";
      const words = toks.map((t, j) => {
        const w = ch[j];
        const spoken = T >= w.T;
        const active = j === activeIdx || (activeIdx < 0 && j === ch.length - 1 && T >= w.T);
        let color = cap.primary;
        let hl: string | undefined;
        let hidden = false;
        let scale = 1;
        if (cap.mode === "karaoke") color = spoken ? cap.primary : cap.secondary || "#fff";
        else if (cap.mode === "typewriter" && !spoken) hidden = true;
        if (cap.mode === "oneword") {
          color = cap.palette?.length ? cap.palette[(ci + j) % cap.palette.length] : cap.primary;
          if (isEmph(w.w, emph)) color = "#FFE400";
        } else if (active && (cap.mode === "active" || cap.mode === "typewriter")) {
          color = act;
          if (cap.hl_box) hl = cap.hl_box;
        } else if (active && cap.mode === "hollow") {
          color = cap.primary;
        } else if (cap.emph && isEmph(w.w, emph) && !hidden) {
          color = cap.emph;
          if (!cap.box && !cap.hl_box) scale = 1.12;
        }
        const fill = cap.mode === "hollow" ? (active ? cap.primary : "transparent") : color;
        const pop = active && (cap.pop || cap.bounce);
        return (
          <React.Fragment key={j + ":" + ci}>
            {j === split && <br />}
            <span className={`cw ${pop ? "cw-pop" : ""}`} style={{
              color: fill, visibility: hidden ? "hidden" : undefined, marginLeft: j && j !== split ? space : 0,
              background: hl, boxShadow: hl ? `0 0 0 ${16 * k}px ${hl}` : undefined,
              transform: scale !== 1 ? `scale(${scale})` : undefined,
              WebkitTextStroke: cap.mode === "hollow" ? `${stroke}px ${cap.outline}` : undefined,
            }}>{t}</span>
          </React.Fragment>
        );
      });
      const shown = cap.mode === "oneword" ? [words[Math.max(0, activeIdx)]] : words;
      const tilt = cap.tilt ? (plan?.tilts?.[ci] ?? (ci % 2 ? 2 : -2)) : 0;
      capEl = (
        <div className={`cap ${cap.box ? "cap-box" : ""} ${cap.slide ? "cap-slide" : ""} ${cap.fade ? "cap-fade" : ""} ${p.onPlace ? "draggable" : ""}`} key={"c" + ci} {...dragProps("cap")} style={{
          top: y, left: capLeft, fontFamily: `"${cap.font}", Impact, sans-serif`, fontSize: px * em, lineHeight: `${px}px`, whiteSpace: "nowrap", maxWidth: "none",
          WebkitTextStroke: cap.box || cap.mode === "hollow" ? undefined : `${stroke}px ${cap.outline}`,
          textShadow: cap.box ? "none" : shadow + glow,
          background: cap.box ? hexA(cap.outline, 1 - (cap.box_alpha ?? 0)) : undefined,
          padding: cap.box ? `${cap.bord * k}px` : undefined,
          borderRadius: cap.box ? 3 * k : undefined,
          transform: `translate(-50%, -50%) translateY(${dyOf(cap.font) * px}px) rotate(${-tilt}deg)`,
        }}>{shown}</div>
      );
    }
  }

  // ------------------------------------------------------------------ hook / CTA / watermark
  const hookKey = story?.title ? "" : style.hook_style;
  const hook: HookStyle | undefined = hookKey ? catalog?.hooks[hookKey] : undefined;
  const hookText = (p.hookText ?? edits.hook ?? clip.clip.title ?? "").trim();
  let hookEl: React.ReactNode = null;
  if (hook && hookText) {
    const dur = place.hook_dur ? Math.max(1, place.hook_dur) : hook.dur || D;
    if (T < Math.min(D, dur)) {
      // same wrapping + shrinking as captions.build_hook: up to 3 lines, 880 px wide
      const hs = Math.min(1.6, Math.max(0.5, place.hook_scale ?? 1));
      const txt = hook.upper ? hookText.toUpperCase() : hookText;
      let size = Math.floor(hook.size * hs);
      let lines = wrapMeasured(hook.font, txt, size, 3, 880);
      while (!lines && size > 40) { size = Math.floor(size * 0.9); lines = wrapMeasured(hook.font, txt, size, 3, 900); }
      if (!lines) lines = [txt];
      const yy0 = place.hook_y != null ? Math.min(1790, Math.max(140, place.hook_y * 1920)) : hook.y - 10 + ((lines.length - 1) * size) / 2;
      const hookXf = place.hook_x != null ? Math.min(1000, Math.max(80, place.hook_x * 1080)) / 1080 : 0.5;
      curPos.current.hook = [hookXf, yy0 / 1920];
      const dH = dragOff?.kind === "hook" ? dragOff : null;
      const yy = yy0 + (dH?.dy ?? 0) * 1920;
      const px = size * k;
      const em = emOf(hook.font);
      const outline = hook.outline ? `${(hook.bord || 6) * k * 2}px ${hook.outline}` : undefined;
      const boxBg = hook.box ? hexA(hook.box, 1 - (hook.box_alpha ?? 0)) : undefined;
      hookEl = (
        <div className={`hook anim-${hook.anim} ${p.onPlace ? "draggable" : ""}`} {...dragProps("hook")} style={{
          top: (yy / 1920) * H, left: (hookXf + (dH?.dx ?? 0)) * W, fontFamily: `"${hook.font}", Impact, sans-serif`, fontSize: px * em, lineHeight: `${px}px`, color: hook.color,
          WebkitTextStroke: hook.box ? undefined : outline, whiteSpace: "nowrap",
          textShadow: hook.glow ? `0 0 ${16 * k}px ${hook.glow}, 0 0 ${30 * k}px ${hook.glow}` : hook.box ? "none" : `${4 * k}px ${4 * k}px 0 rgba(0,0,0,.55)`,
          transform: `translate(-50%, -50%) translateY(${dyOf(hook.font) * px}px) rotate(${-(hook.tilt || 0)}deg)`,
        }}>{lines.map((l, i) => (
          <React.Fragment key={i}>{i > 0 && <br />}
            <span style={boxBg ? { background: boxBg, boxShadow: `0 0 0 ${20 * k}px ${boxBg}` } : undefined}>{l}</span>
          </React.Fragment>))}</div>
      );
    }
  }
  const cta: CtaStyle | undefined = style.cta_style ? catalog?.ctas[style.cta_style] : undefined;
  const ctaText = settings.cta_text || "";
  const ctaYf = place.cta_y != null ? Math.min(1790, Math.max(140, place.cta_y * 1920)) / 1920 : 0.42;
  const ctaXf = place.cta_x != null ? Math.min(1000, Math.max(80, place.cta_x * 1080)) / 1080 : 0.5;
  curPos.current.cta = [ctaXf, ctaYf];
  const dT = dragOff?.kind === "cta" ? dragOff : null;
  const ctaEl = cta && ctaText.trim() && D >= 8 && T >= D - 2.6 ? (
    <div className={`cta anim-${cta.anim} ${p.onPlace ? "draggable" : ""}`} {...dragProps("cta")} style={{
      top: (ctaYf + (dT?.dy ?? 0)) * H, left: (ctaXf + (dT?.dx ?? 0)) * W,
      fontFamily: `"${cta.font}", sans-serif`, fontSize: cta.size * k * emOf(cta.font), lineHeight: `${cta.size * k}px`, color: cta.color,
      background: cta.box, boxShadow: cta.box ? `0 0 0 ${18 * k}px ${cta.box}` : undefined,
      WebkitTextStroke: cta.outline ? `${(cta.bord || 5) * k * 2}px ${cta.outline}` : undefined,
      textShadow: cta.box ? "none" : `${3 * k}px ${3 * k}px 0 rgba(0,0,0,.55)`,
      textTransform: cta.font !== "Poppins" ? "uppercase" : "none",
      transform: `translate(-50%, -50%) translateY(${dyOf(cta.font) * cta.size * k}px)`,
    }}>{ctaText}</div>
  ) : null;
  const wm = settings.watermark?.trim();
  const wmPos = place.wm_pos || "top";

  // ------------------------------------------------------------------ Story FX titles + beat text
  const titleSet = new Set(story?.title?.els || []);
  const storyEl = storyEls.filter((e) => T >= e.t0 && T < e.t1).map((e, i) => {
    const grp: DragKind = titleSet.has(e) ? "title" : "beat";
    const dS = dragOff?.kind === grp ? dragOff : null;
    const fam = e.font === "Lato" && e.bold ? "Lato Bold" : e.font;
    const px = e.size * k;
    const [ax, ay] = AN[e.an] || AN[5];
    const anim = e.anim || [];
    const names: string[] = [];
    const durs: string[] = [];
    if (anim.includes("fade")) { names.push("sfxFade"); durs.push(anim.includes("scale") ? "140ms" : "220ms"); }
    if (anim.includes("scale")) { names.push("sfxScale"); durs.push("460ms"); }
    else if (anim.includes("rise")) { names.push("sfxRise"); durs.push("380ms"); }
    if (anim.includes("track")) { names.push("sfxTrack"); durs.push("800ms"); }
    if (anim.includes("wipe")) { names.push("sfxWipe"); durs.push("650ms"); }
    const out = T > e.t1 - 0.26;
    const shadow = e.shadow ? `${3 * k}px ${5 * k}px ${Math.max(3, e.size / 28) * k * 2}px rgba(0,0,0,.45)` : "";
    const glow = e.glow ? `0 0 ${(e.size / 9) * k}px rgba(255,255,255,.6), 0 0 ${(e.size / 4) * k}px rgba(255,255,255,.25)` : "";
    return (
      <div key={`${e.t0}:${e.text}:${i}`} className={`story-el ${p.onPlace ? "draggable" : ""}`} {...dragProps(grp)} style={{
        left: e.x * k + (dS?.dx ?? 0) * W, top: e.y * k + (dS?.dy ?? 0) * H, transform: `translate(${ax}, ${ay}) translateY(${dyOf(fam) * px}px)`,
        opacity: out ? 0 : 1, transition: out ? "opacity 260ms linear" : undefined,
      }}>
        <span style={{
          fontFamily: `"${fam}", Georgia, serif`, fontSize: px * emOf(fam), lineHeight: `${px}px`, color: e.color,
          textShadow: [shadow, glow].filter(Boolean).join(", ") || undefined,
          ["--sp" as string]: `${Math.max(2, Math.floor(e.size * 0.05)) * k}px`, ["--rise" as string]: `${34 * k}px`,
          // playing: run the entrance from where the clock is; paused / scrubbing: show the settled text
          animationName: playing ? names.join(",") : "none", animationDuration: durs.join(","),
          animationDelay: playing ? `${-Math.max(0, T - e.t0).toFixed(3)}s` : undefined,
          animationTimingFunction: "cubic-bezier(.2,.7,.3,1)", animationFillMode: "both",
        }}>{e.text}</span>
      </div>
    );
  });
  const tex = story?.texture;

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

  const isCur = (e: React.SyntheticEvent<HTMLVideoElement>) => e.currentTarget === cur();
  useEffect(() => { const v = cur(); if (v) v.muted = muted; }, [muted]);
  useEffect(() => { applyFrame(Tref.current); });

  return (
    <div className={`player ${p.fill ? "player-fill" : ""} ${p.className || ""}`} ref={box}
      onClick={() => p.controls && playPause()}>
      <div className="stage" ref={stageEl} data-grade={grade} style={{ height: H }}>
        <canvas ref={canvas} width={Math.round(W * dpr)} height={Math.round(H * dpr)} className="frame-canvas" />
        <video ref={vA} src={src} className="hidden-video" playsInline muted={muted} preload="auto" autoPlay={p.autoPlay}
          poster={clip.poster ? mediaUrl(clip.poster) : undefined}
          onPlay={(e) => isCur(e) && setPlaying(true)} onPause={(e) => isCur(e) && !frozen.current?.run && setPlaying(false)}
          onError={onVideoError}
          onLoadedMetadata={(e) => { if (isCur(e)) seekAbsFor(Tref.current); }} />
        <video ref={vB} src={src} className="hidden-video" playsInline muted preload="auto"
          onPlay={(e) => isCur(e) && setPlaying(true)} onPause={(e) => isCur(e) && !frozen.current?.run && setPlaying(false)}
          onLoadedMetadata={() => { prepped.current = -1; }} />
        {(VIGNETTE.has(gradeKey) || tex?.vignette) && <div className="vignette" />}
        {story && <div className="story-vig" />}
        {tex?.leak && <div className="story-leak" ref={leakEl} style={{ opacity: 0 }} />}
        {tex && tex.grain > 0 && <div className="story-grain" style={{ opacity: Math.min(0.5, tex.grain * 0.32) }} />}
        <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
          <filter id={filterId} x="-10%" y="0" width="120%" height="100%"><feGaussianBlur ref={streakBlur} stdDeviation="0 0" /></filter>
        </svg>
      </div>
      <div className="overlay">
        {storyEl}
        {hookEl}{capEl}{ctaEl}
        <div className="story-flash" ref={flashEl} style={{ opacity: 0 }} />
        {wm && <div className={`wm wm-${wmPos}`} style={{ fontSize: 40 * k * Math.min(1.6, Math.max(0.5, place.wm_scale ?? 1)) * emOf("Poppins"), lineHeight: `${40 * k * Math.min(1.6, Math.max(0.5, place.wm_scale ?? 1))}px` }}>{wm}</div>}
        {settings.progress_bar !== false && <div className="pbar" ref={pbarEl} style={{ background: accent, height: Math.max(2, 12 * k) }} />}
        <div className="intro" ref={introEl} style={{ opacity: 0 }} />
      </div>
      {p.withAudio && <audio ref={audio} src={audioSrc || undefined} muted={muted} preload="auto" />}
      {p.withAudio && audioBusy && p.controls && <div className="aud-busy" title="Updating sound effects and music"><span className="spin" /></div>}
      {err && <div className="player-err"><Icon name="alert" size={16} /> {err}</div>}
      {making && <div className="player-err"><span className="spin" /> Preparing a preview copy of this video…</div>}
      {p.controls && (
        <div className="pctl" onClick={(e) => e.stopPropagation()}>
          <button className="pbtn" onClick={playPause}>
            <Icon name={playing ? "pause" : "play"} size={16} />
          </button>
          <input ref={rangeEl} type="range" min={0} max={D} step={0.01} defaultValue={0}
            onInput={(e) => seekAbsFor(parseFloat((e.target as HTMLInputElement).value))} />
          <span className="ptime" ref={timeEl}>{fmt(T)} / {fmt(D)}</span>
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
