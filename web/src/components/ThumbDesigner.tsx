// Free thumbnail designer: everything is drawn on a canvas in the app (no AI service, no quota, no watermark).
// Layers: background (video frame / gradient / solid) -> speaker cut-out (outline, glow) -> text and stickers.
// Every layer can be dragged on the canvas and resized with the mouse wheel. "Copy reference" reads a reference
// image's colours and layout (server side) and rebuilds it with the user's own speaker and words.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl } from "../lib/api";
import { toast, useStore } from "../lib/store";
import { Icon } from "./Icon";
import { Btn, Field, Modal, Seg, Select, Slider, Text, Toggle } from "./ui";

type TextLayer = {
  id: string; kind: "text"; text: string; font: string; size: number; x: number; y: number; color: string;
  stroke: string; strokeW: number; shadow: number; glow: string; upper: boolean; rot: number; box: string; align: "left" | "center" | "right";
};
type Sticker = { id: string; kind: "arrow" | "circle" | "emoji" | "bar"; x: number; y: number; size: number; rot: number; color: string; text?: string };
type Layer = TextLayer | Sticker;
export type Design = {
  bg: { mode: "frame" | "gradient" | "solid"; blur: number; dim: number; c1: string; c2: string; tint: string; tintA: number; vignette: number; sat: number };
  person: { show: boolean; x: number; y: number; scale: number; outline: string; outlineW: number; glow: string; flip: boolean; shadow: number };
  layers: Layer[];
};
type Assets = { frame: string; cutout: string; box: number[] | null; w: number; h: number; design?: Design | null; title: string };
type Ref = { palette: string[]; accent: string; dark: boolean; subject: number[] | null; text: { x: number; y: number; w: number; h: number; align: string; color: string; stroke: string } | null };

const uid = () => Math.random().toString(36).slice(2, 9);
const ASPECTS: [string, string][] = [["9:16", "9:16 Shorts"], ["16:9", "16:9 YouTube"], ["4:5", "4:5"], ["1:1", "1:1"]];
const EMOJI = ["🔥", "😱", "💰", "⚠️", "✅", "❌", "👀", "🚀", "💡", "❓", "⭐", "👉"];
const SWATCH = ["#FFFFFF", "#000000", "#FFE400", "#FF2D2D", "#00E5FF", "#39FF14", "#FF4FD8", "#FF8A00", "#7C4DFF", "#1E90FF"];

function splitHeadline(title: string): [string, string] {
  const w = title.trim().split(/\s+/).filter(Boolean);
  if (w.length <= 2) return ["", w.join(" ")];
  const tail = w.slice(-3);
  let bi = 0;
  tail.forEach((x, i) => { if (x.replace(/\W/g, "").length >= tail[bi].replace(/\W/g, "").length) bi = i; });
  const big = tail[bi];
  const rest = w.filter((x, i) => i !== w.length - tail.length + bi).join(" ");
  return [rest, big];
}

const text = (p: Partial<TextLayer>): TextLayer => ({
  id: uid(), kind: "text", text: "TEXT", font: "Anton", size: 0.2, x: 0.5, y: 0.2, color: "#FFFFFF", stroke: "#000000",
  strokeW: 0.012, shadow: 0.5, glow: "", upper: true, rot: 0, box: "", align: "center", ...p,
});

type Preset = { name: string; make: (title: string, wide: boolean, hasPerson: boolean) => Design };
// over a video-frame background the cut-out sits on top of its own blurred image; on a flat background it drops lower
const base = (wide: boolean, flat = false): Design["person"] => ({ show: true, x: wide && flat ? 0.72 : 0.5, y: flat ? (wide ? 0.56 : 0.62) : 0.51, scale: flat ? 1.14 : 1.06, outline: "#FFFFFF", outlineW: 0.008, glow: "", flip: false, shadow: 0.5 });
const PRESETS: Preset[] = [
  { name: "Bold Yellow", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "frame", blur: 7, dim: 0.38, c1: "#101018", c2: "#2a1a55", tint: "#000000", tintA: 0, vignette: 0.5, sat: 1.15 }, person: base(w),
    layers: [text({ text: b, font: "Anton", size: w ? 0.14 : 0.27, x: w ? 0.3 : 0.5, y: w ? 0.48 : 0.17, color: "#FFE400", strokeW: 0.014 }),
      ...(k ? [text({ text: k, font: "Anton", size: w ? 0.062 : 0.1, x: w ? 0.3 : 0.5, y: w ? 0.3 : 0.065, color: "#FFFFFF", strokeW: 0.008 })] : [])] }; } },
  { name: "Red Alert", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "frame", blur: 4, dim: 0.45, c1: "#1a0000", c2: "#5a0000", tint: "#FF0000", tintA: 0.12, vignette: 0.7, sat: 1.1 }, person: { ...base(w), outline: "#FF2D2D" },
    layers: [text({ text: b, font: "Archivo Black", size: w ? 0.1 : 0.19, x: w ? 0.3 : 0.5, y: w ? 0.5 : 0.18, color: "#FFFFFF", stroke: "#000000", strokeW: 0, box: "#FF2D2D", shadow: 0.3 }),
      ...(k ? [text({ text: k, font: "Archivo Black", size: w ? 0.05 : 0.085, x: w ? 0.3 : 0.5, y: w ? 0.32 : 0.075, color: "#FFFFFF", strokeW: 0.008 })] : []),
      { id: uid(), kind: "arrow", x: w ? 0.5 : 0.24, y: w ? 0.68 : 0.36, size: w ? 0.16 : 0.26, rot: w ? -10 : 35, color: "#FF2D2D" }] }; } },
  { name: "Neon", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "gradient", blur: 0, dim: 0, c1: "#12002e", c2: "#00306e", tint: "#000000", tintA: 0, vignette: 0.6, sat: 1 }, person: { ...base(w, true), outline: "", outlineW: 0, glow: "#FF4FD8" },
    layers: [text({ text: b, font: "Bebas Neue", size: w ? 0.16 : 0.3, x: w ? 0.3 : 0.5, y: w ? 0.48 : 0.17, color: "#00E5FF", stroke: "#001a33", strokeW: 0.006, glow: "#00E5FF" }),
      ...(k ? [text({ text: k, font: "Bebas Neue", size: w ? 0.07 : 0.11, x: w ? 0.3 : 0.5, y: w ? 0.3 : 0.06, color: "#FF4FD8", strokeW: 0, glow: "#FF4FD8" })] : [])] }; } },
  { name: "Editorial", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "frame", blur: 10, dim: 0.22, c1: "#101010", c2: "#303030", tint: "#000000", tintA: 0, vignette: 0.45, sat: 1.1 }, person: { ...base(w), outline: "", outlineW: 0, shadow: 0.7 },
    layers: [text({ text: b, font: "Abril Fatface", size: w ? 0.13 : 0.25, x: w ? 0.3 : 0.5, y: w ? 0.5 : 0.19, color: "#FFFFFF", strokeW: 0, shadow: 0.7 }),
      ...(k ? [text({ text: k, font: "Lato", size: w ? 0.034 : 0.05, x: w ? 0.3 : 0.5, y: w ? 0.36 : 0.095, color: "#FFFFFF", strokeW: 0, upper: false, shadow: 0.6 })] : [])] }; } },
  { name: "Clean White", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "frame", blur: 0, dim: 0.3, c1: "#101018", c2: "#222", tint: "#000000", tintA: 0, vignette: 0.55, sat: 1.2 }, person: { ...base(w), show: false },
    layers: [text({ text: (k ? k + "\n" : "") + b, font: "Poppins Black", size: w ? 0.085 : 0.14, x: 0.5, y: w ? 0.78 : 0.2, color: "#FFFFFF", strokeW: 0.006, shadow: 0.8 })] }; } },
  { name: "Pop Split", make: (t, w) => { const [k, b] = splitHeadline(t); return {
    bg: { mode: "gradient", blur: 0, dim: 0, c1: "#FFE400", c2: "#FF8A00", tint: "#000000", tintA: 0, vignette: 0.15, sat: 1 }, person: { ...base(w, true), outline: "#FFFFFF", outlineW: 0.012, shadow: 0.6 },
    layers: [text({ text: b, font: "Luckiest Guy", size: w ? 0.13 : 0.24, x: w ? 0.3 : 0.5, y: w ? 0.5 : 0.17, color: "#111111", stroke: "#FFFFFF", strokeW: 0.012, shadow: 0.2, rot: -4 }),
      ...(k ? [text({ text: k, font: "Luckiest Guy", size: w ? 0.055 : 0.09, x: w ? 0.3 : 0.5, y: w ? 0.3 : 0.065, color: "#FFFFFF", stroke: "#111111", strokeW: 0.008, rot: -4 })] : [])] }; } },
];

const imgCache = new Map<string, HTMLImageElement>();
function loadImg(src: string): Promise<HTMLImageElement> {
  if (imgCache.has(src)) return Promise.resolve(imgCache.get(src)!);
  return new Promise((res, rej) => { const i = new Image(); i.onload = () => { imgCache.set(src, i); res(i); }; i.onerror = rej; i.src = src; });
}
let tintC: HTMLCanvasElement | null = null;
function tinted(img: HTMLImageElement, color: string, w: number, h: number): HTMLCanvasElement {
  tintC = tintC || document.createElement("canvas");
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h));
  const x = c.getContext("2d")!;
  x.drawImage(img, 0, 0, c.width, c.height);
  x.globalCompositeOperation = "source-in";
  x.fillStyle = color; x.fillRect(0, 0, c.width, c.height);
  return c;
}
type Box = { id: string; x: number; y: number; w: number; h: number };

/** Draw the whole design at W x H. Returns the layers' boxes (for clicking and dragging). */
function draw(ctx: CanvasRenderingContext2D, W: number, H: number, d: Design, frame: HTMLImageElement | null, cut: HTMLImageElement | null, sel: string | null, em: (f: string) => number): Box[] {
  const boxes: Box[] = [];
  ctx.save();
  ctx.clearRect(0, 0, W, H);
  // background
  if (d.bg.mode === "frame" && frame) {
    const k = Math.max(W / frame.width, H / frame.height) * (d.bg.blur ? 1.08 : 1);
    const fw = frame.width * k, fh = frame.height * k;
    ctx.filter = `blur(${(d.bg.blur * W) / 1080}px) saturate(${d.bg.sat})`;
    ctx.drawImage(frame, (W - fw) / 2, (H - fh) / 2, fw, fh);
    ctx.filter = "none";
  } else if (d.bg.mode === "gradient") {
    const g = ctx.createLinearGradient(0, 0, W * 0.6, H);
    g.addColorStop(0, d.bg.c1); g.addColorStop(1, d.bg.c2);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  } else { ctx.fillStyle = d.bg.c1; ctx.fillRect(0, 0, W, H); }
  if (d.bg.dim > 0) { ctx.fillStyle = `rgba(0,0,0,${d.bg.dim})`; ctx.fillRect(0, 0, W, H); }
  if (d.bg.tintA > 0) { ctx.globalAlpha = d.bg.tintA; ctx.fillStyle = d.bg.tint; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1; }
  if (d.bg.vignette > 0) {
    const g = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.hypot(W, H) * 0.62);
    g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${d.bg.vignette})`);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
  // speaker
  if (d.person.show && cut) {
    const k = Math.max(W / cut.width, H / cut.height) * d.person.scale;
    const pw = cut.width * k, ph = cut.height * k;
    const px = d.person.x * W - pw / 2, py = d.person.y * H - ph / 2 + (1 - 1 / d.person.scale) * 0;
    ctx.save();
    if (d.person.flip) { ctx.translate(W, 0); ctx.scale(-1, 1); }
    const X = d.person.flip ? W - px - pw : px;
    if (d.person.glow) { ctx.shadowColor = d.person.glow; ctx.shadowBlur = W * 0.05; ctx.drawImage(cut, X, py, pw, ph); ctx.drawImage(cut, X, py, pw, ph); ctx.shadowBlur = 0; }
    if (d.person.outline && d.person.outlineW > 0) {
      const t = tinted(cut, d.person.outline, pw / 2, ph / 2);
      const r = d.person.outlineW * W;
      for (let a = 0; a < 20; a++) ctx.drawImage(t, X + Math.cos((a / 20) * 6.283) * r, py + Math.sin((a / 20) * 6.283) * r, pw, ph);
    }
    if (d.person.shadow > 0) { ctx.shadowColor = `rgba(0,0,0,${d.person.shadow})`; ctx.shadowBlur = W * 0.03; ctx.shadowOffsetY = W * 0.012; }
    ctx.drawImage(cut, X, py, pw, ph);
    ctx.restore();
    boxes.push({ id: "person", x: px + pw * 0.2, y: py + ph * 0.2, w: pw * 0.6, h: ph * 0.8 });
  }
  // text + stickers
  for (const L of d.layers) {
    ctx.save();
    const cx = L.x * W, cy = L.y * H;
    ctx.translate(cx, cy); ctx.rotate(((L.rot || 0) * Math.PI) / 180);
    let bw = 0, bh = 0;
    if (L.kind === "text") {
      const px = L.size * W;
      ctx.font = `${px * em(L.font)}px "${L.font}", Impact, sans-serif`;
      ctx.textBaseline = "middle"; ctx.textAlign = L.align; ctx.lineJoin = "round";
      const lines = (L.upper ? L.text.toUpperCase() : L.text).split("\n");
      const lh = px * 0.98;
      const ws = lines.map((s) => ctx.measureText(s).width);
      bw = Math.max(...ws, 10); bh = lh * lines.length;
      const ox = L.align === "left" ? -bw / 2 : L.align === "right" ? bw / 2 : 0;
      lines.forEach((s, i) => {
        const y = (i - (lines.length - 1) / 2) * lh;
        if (L.box) {
          const pad = px * 0.14, w = ws[i] + pad * 2, x0 = (L.align === "left" ? ox : L.align === "right" ? ox - ws[i] : -ws[i] / 2) - pad;
          ctx.fillStyle = L.box; ctx.beginPath(); (ctx as any).roundRect?.(x0, y - lh / 2, w, lh, px * 0.08); ctx.fill();
        }
        if (L.glow) { ctx.shadowColor = L.glow; ctx.shadowBlur = px * 0.35; ctx.fillStyle = L.glow; ctx.fillText(s, ox, y); }
        ctx.shadowColor = `rgba(0,0,0,${L.shadow})`; ctx.shadowBlur = L.shadow > 0 ? px * 0.12 : 0; ctx.shadowOffsetY = L.shadow > 0 ? px * 0.05 : 0;
        if (L.strokeW > 0) { ctx.strokeStyle = L.stroke; ctx.lineWidth = L.strokeW * W * 2; ctx.strokeText(s, ox, y); }
        ctx.shadowColor = L.strokeW > 0 ? "transparent" : ctx.shadowColor;
        ctx.fillStyle = L.color; ctx.fillText(s, ox, y);
        ctx.shadowColor = "transparent"; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
      });
    } else {
      const s = L.size * W;
      bw = s; bh = s;
      ctx.strokeStyle = L.color; ctx.fillStyle = L.color; ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = s * 0.08; ctx.shadowOffsetY = s * 0.03;
      if (L.kind === "circle") { ctx.lineWidth = s * 0.07; ctx.beginPath(); ctx.ellipse(0, 0, s / 2, s / 2.4, 0, 0, 6.283); ctx.stroke(); }
      else if (L.kind === "bar") { ctx.fillRect(-s / 2, -s * 0.06, s, s * 0.12); bh = s * 0.3; }
      else if (L.kind === "emoji") { ctx.font = `${s * 0.9}px "Segoe UI Emoji", "Apple Color Emoji", sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(L.text || "🔥", 0, 0); }
      else {   // curved arrow
        ctx.lineWidth = s * 0.11; ctx.beginPath(); ctx.moveTo(-s / 2, -s * 0.18); ctx.quadraticCurveTo(-s * 0.05, -s * 0.5, s * 0.3, s * 0.12); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(s * 0.5, s * 0.32); ctx.lineTo(s * 0.06, s * 0.2); ctx.lineTo(s * 0.42, -s * 0.1); ctx.closePath(); ctx.fill();
      }
    }
    if (sel === L.id) { ctx.shadowColor = "transparent"; ctx.setLineDash([W * 0.012, W * 0.01]); ctx.strokeStyle = "#8b7bff"; ctx.lineWidth = Math.max(2, W * 0.004); ctx.strokeRect(-bw / 2 - W * 0.01, -bh / 2 - W * 0.01, bw + W * 0.02, bh + W * 0.02); }
    ctx.restore();
    boxes.push({ id: L.id, x: cx - bw / 2, y: cy - bh / 2, w: bw, h: bh });
  }
  if (sel === "person") { const b = boxes.find((x) => x.id === "person"); if (b) { ctx.setLineDash([W * 0.012, W * 0.01]); ctx.strokeStyle = "#8b7bff"; ctx.lineWidth = Math.max(2, W * 0.004); ctx.strokeRect(b.x, b.y, b.w, b.h); } }
  ctx.restore();
  return boxes;
}

export function ThumbDesigner({ planFile, initialAspect, initialRef, onClose, onSaved }: { planFile: string; initialAspect?: string; initialRef?: string; onClose: () => void; onSaved: (cover: string, t: number) => void }) {
  const cat = useStore((s) => s.catalog);
  const metrics: Record<string, { em?: number }> = (cat as any)?.metrics || {};
  const em = (f: string) => metrics[f]?.em ?? 0.8;
  const fonts = useMemo(() => Object.keys(cat?.fonts || {}).filter((f) => !/Noto|Press Start/.test(f)).sort(), [cat]);
  const [aspect, setAspect] = useState(initialAspect || "9:16");
  const [at, setAt] = useState(0.3);
  const [assets, setAssets] = useState<Assets | null>(null);
  const [imgs, setImgs] = useState<{ frame: HTMLImageElement | null; cut: HTMLImageElement | null }>({ frame: null, cut: null });
  const [d, setD] = useState<Design | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState<"style" | "bg" | "speaker" | "text" | "stickers">("style");
  const [ref, setRef] = useState(initialRef || "");
  const [busy, setBusy] = useState("");
  const [headline, setHeadline] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  const boxes = useRef<Box[]>([]);
  const hist = useRef<Design[]>([]);
  const wide = aspect === "16:9";

  // frame + cut-out for this size / moment
  useEffect(() => {
    let live = true;
    setBusy("load");
    api<Assets>("/api/thumb/assets", { plan_file: planFile, aspect, at }).then(async (a) => {
      if (!live) return;
      const frame = await loadImg(mediaUrl(a.frame, at)).catch(() => null);
      const cut = a.cutout ? await loadImg(mediaUrl(a.cutout, at)).catch(() => null) : null;
      if (!live) return;
      setAssets(a); setImgs({ frame, cut });
      setHeadline((h) => h || a.title || "Your headline");
      setD((cur) => cur || (a.design as Design) || PRESETS[0].make(a.title || "Your headline", aspect === "16:9", !!cut));
    }).catch((e) => toast(e.message, "error")).finally(() => live && setBusy(""));
    return () => { live = false; };
  }, [planFile, aspect, at]);

  // make sure the fonts in use are loaded before drawing
  const fontKey = d ? d.layers.map((l) => (l.kind === "text" ? l.font : "")).join("|") : "";
  const [fontTick, setFontTick] = useState(0);
  useEffect(() => {
    if (!d) return;
    Promise.all(d.layers.filter((l) => l.kind === "text").map((l) => (document as any).fonts?.load(`40px "${(l as TextLayer).font}"`))).then(() => setFontTick((x) => x + 1)).catch(() => {});
  }, [fontKey]);

  const PW = wide ? 620 : aspect === "1:1" ? 460 : aspect === "4:5" ? 420 : 330;
  const PH = assets ? Math.round((PW * assets.h) / assets.w) : Math.round((PW * 16) / 9);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !d) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = PW * dpr; c.height = PH * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    boxes.current = draw(ctx, PW, PH, d, imgs.frame, imgs.cut, sel, em);
  }, [d, imgs, sel, PW, PH, fontTick]);

  const change = (f: (x: Design) => Design, keep = true) => setD((cur) => { if (!cur) return cur; if (keep) { hist.current.push(cur); if (hist.current.length > 60) hist.current.shift(); } return f(cur); });
  const undo = () => { const p = hist.current.pop(); if (p) setD(p); };
  const setLayer = (id: string, patch: Record<string, any>, keep = true) => change((x) => ({ ...x, layers: x.layers.map((l) => (l.id === id ? ({ ...l, ...patch } as Layer) : l)) }), keep);
  const cur = d?.layers.find((l) => l.id === sel) || null;

  // drag + wheel on the canvas
  const hit = (e: { clientX: number; clientY: number }) => {
    const r = canvas.current!.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const b = [...boxes.current].reverse().find((q) => x >= q.x - 6 && x <= q.x + q.w + 6 && y >= q.y - 6 && y <= q.y + q.h + 6);
    return { id: b?.id || null, x, y };
  };
  const down = (e: React.PointerEvent) => {
    const h = hit(e);
    setSel(h.id);
    if (!h.id || !d) return;
    if (h.id !== "person") setTab(d.layers.find((l) => l.id === h.id)?.kind === "text" ? "text" : "stickers"); else setTab("speaker");
    const start = h.id === "person" ? { x: d.person.x, y: d.person.y } : (() => { const l = d.layers.find((q) => q.id === h.id)!; return { x: l.x, y: l.y }; })();
    hist.current.push(d);
    const x0 = e.clientX, y0 = e.clientY;
    const mv = (ev: PointerEvent) => {
      const nx = start.x + (ev.clientX - x0) / PW, ny = start.y + (ev.clientY - y0) / PH;
      if (h.id === "person") change((q) => ({ ...q, person: { ...q.person, x: nx, y: ny } }), false);
      else setLayer(h.id!, { x: Math.min(1.1, Math.max(-0.1, nx)), y: Math.min(1.1, Math.max(-0.1, ny)) }, false);
    };
    const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  };
  const wheel = (e: React.WheelEvent) => {
    const h = hit(e);
    if (!h.id || !d) return;
    const k = e.deltaY < 0 ? 1.06 : 1 / 1.06;
    if (h.id === "person") change((q) => ({ ...q, person: { ...q.person, scale: Math.min(3, Math.max(0.3, q.person.scale * k)) } }));
    else { const l = d.layers.find((q) => q.id === h.id)!; setLayer(h.id, { size: Math.min(0.9, Math.max(0.02, l.size * k)) }); }
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.key === "Delete" || e.key === "Backspace") && sel && sel !== "person") { change((x) => ({ ...x, layers: x.layers.filter((l) => l.id !== sel) })); setSel(null); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.stopPropagation(); undo(); }
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  });

  const applyPreset = (p: Preset) => { if (!assets || !d) return; change(() => p.make(headline.trim() || "Your headline", wide, !!imgs.cut)); setSel(null); };
  const pickRef = async () => { try { const r = await api<{ path: string }>("/api/thumb/ref", {}); if (r.path) setRef(r.path); } catch (e: any) { toast(e.message, "error"); } };
  const copyRef = async () => {
    if (!ref || !d) return;
    setBusy("ref");
    try {
      const r = await api<Ref>("/api/thumb/analyze", { ref });
      change((x) => {
        const n: Design = JSON.parse(JSON.stringify(x));
        // colours: keep the video frame behind, graded toward the reference; or its two main colours as a gradient
        n.bg.c1 = r.palette[0]; n.bg.c2 = r.palette[1] || r.palette[0];
        n.bg.tint = r.palette[0]; n.bg.tintA = n.bg.mode === "frame" ? 0.28 : 0;
        n.bg.dim = r.dark ? Math.max(n.bg.dim, 0.3) : Math.min(n.bg.dim, 0.15);
        if (r.subject) { n.person.x = r.subject[0] + r.subject[2] / 2; n.person.y = Math.min(0.75, r.subject[1] + r.subject[3] / 2 + 0.04); }
        const ts = n.layers.filter((l) => l.kind === "text") as TextLayer[];
        if (r.text && ts.length) {
          const main = ts.reduce((a, b) => (b.size > a.size ? b : a));
          const dy = r.text.y - main.y, dx = r.text.x - main.x;
          const len = Math.max(...main.text.split("\n").map((s) => s.length), 3);
          main.size = Math.min(0.4, Math.max(0.06, (r.text.w / len) * 1.9));
          ts.forEach((t) => { t.x = Math.min(0.9, Math.max(0.1, t.x + dx)); t.y = Math.min(0.94, Math.max(0.05, t.y + dy)); t.align = r.text!.align as any; t.color = t === main ? r.text!.color : t.color; t.stroke = r.text!.stroke; });
        } else ts.forEach((t, i) => { if (i === 0) t.color = r.accent; });
        return n;
      });
      toast(r.text ? "Copied the reference's colours and layout. Drag anything to fine-tune." : "Copied the reference's colours. Its headline position wasn't clear, so place the text yourself.", "ok");
    } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const save = async () => {
    if (!assets || !d) return;
    setBusy("save");
    try {
      await Promise.all(d.layers.filter((l) => l.kind === "text").map((l) => (document as any).fonts?.load(`40px "${(l as TextLayer).font}"`)));
      const c = document.createElement("canvas");
      c.width = assets.w; c.height = assets.h;
      draw(c.getContext("2d")!, assets.w, assets.h, d, imgs.frame, imgs.cut, null, em);
      const r = await api<{ cover: string; cover_info: { time: number } }>("/api/thumb/save", { plan_file: planFile, aspect, image: c.toDataURL("image/jpeg", 0.93), design: d, ref });
      toast("Thumbnail saved", "ok");
      onSaved(r.cover, r.cover_info.time);
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };

  const Color = ({ value, onChange, none }: { value: string; onChange: (v: string) => void; none?: boolean }) => (
    <div className="td-colors">
      {none && <button className={`td-sw none ${!value ? "on" : ""}`} title="None" onClick={() => onChange("")}>∅</button>}
      {SWATCH.map((c) => <button key={c} className={`td-sw ${value?.toUpperCase() === c ? "on" : ""}`} style={{ background: c }} onClick={() => onChange(c)} />)}
      <input type="color" value={value || "#ffffff"} onChange={(e) => onChange(e.target.value.toUpperCase())} title="Any colour" />
    </div>
  );
  const bg = d?.bg, ps = d?.person;
  return (
    <Modal title="Thumbnail designer · free, no watermark" onClose={onClose} width={1080} footer={<>
      <Btn kind="ghost" icon="undo" onClick={undo}>Undo</Btn><div className="grow" />
      <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
      <Btn kind="primary" icon="check" busy={busy === "save"} disabled={!d || !!busy} onClick={save}>Save thumbnail</Btn></>}>
      <div className="td">
        <div className="td-stage">
          <div className="td-canvas" style={{ width: PW, height: PH }}>
            <canvas ref={canvas} style={{ width: PW, height: PH }} onPointerDown={down} onWheel={wheel} />
            {busy === "load" && <div className="td-load"><span className="spin" /></div>}
          </div>
          <small className="muted">Click to select · drag to move · mouse wheel to resize · Delete removes</small>
          <div className="row gap wrap td-under">
            <Select value={aspect} options={ASPECTS} onChange={(v) => { setAspect(v); }} />
            <div className="td-at"><span className="muted small">Frame</span><Slider value={at} min={0.02} max={0.96} step={0.02} onChange={setAt} fmt={(v) => `${Math.round(v * 100)}%`} /></div>
          </div>
        </div>
        <div className="td-panel">
          <div className="seg small td-tabs">
            {([["style", "Styles"], ["bg", "Background"], ["speaker", "Speaker"], ["text", "Text"], ["stickers", "Stickers"]] as const).map(([k, n]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{n}</button>)}
          </div>
          {d && bg && ps && <div className="td-body scroll">
            {tab === "style" && <>
              <Field label="Headline (used by the styles below)"><Text value={headline} onChange={setHeadline} placeholder="Your headline" /></Field>
              <div className="sec-head"><h3>One-click styles</h3></div>
              <div className="td-presets">{PRESETS.map((p) => <button key={p.name} className="chipbtn" onClick={() => applyPreset(p)}>{p.name}</button>)}</div>
              <div className="sec-head"><h3>Copy a reference</h3></div>
              <p className="muted small">Pick a thumbnail you like. The designer copies its colours, where the headline sits and where the person sits, using your speaker and your words. Logos and people in the reference are not copied.</p>
              <div className="row gap wrap">
                {ref ? <div className="ref-chip"><img src={mediaUrl(ref)} alt="" /><span>Reference</span><button className="x" onClick={() => setRef("")}><Icon name="x" size={12} /></button></div>
                  : <Btn small icon="plus" onClick={pickRef}>Add reference image</Btn>}
                {ref && <Btn small kind="primary" icon="wand" busy={busy === "ref"} onClick={copyRef}>Copy its style</Btn>}
              </div>
              {!imgs.cut && busy !== "load" && <p className="muted small">No clear speaker in this frame, so there is no cut-out. Move the Frame slider to a moment where the person is centred.</p>}
            </>}
            {tab === "bg" && <>
              <Seg value={bg.mode} options={[["frame", "Video frame"], ["gradient", "Gradient"], ["solid", "Solid"]]} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, mode: v } }))} />
              {bg.mode === "frame" && <>
                <Field label="Blur"><Slider value={bg.blur} min={0} max={30} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, blur: v } }), false)} /></Field>
                <Field label="Colour boost"><Slider value={bg.sat} min={0} max={2} step={0.05} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, sat: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
              </>}
              {bg.mode !== "frame" && <Field label={bg.mode === "gradient" ? "From" : "Colour"}><Color value={bg.c1} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, c1: v } }))} /></Field>}
              {bg.mode === "gradient" && <Field label="To"><Color value={bg.c2} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, c2: v } }))} /></Field>}
              <Field label="Darken"><Slider value={bg.dim} min={0} max={0.8} step={0.02} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, dim: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
              <Field label="Colour wash"><Color value={bg.tintA > 0 ? bg.tint : ""} none onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, tint: v || x.bg.tint, tintA: v ? Math.max(0.2, x.bg.tintA) : 0 } }))} /></Field>
              {bg.tintA > 0 && <Field label="Wash strength"><Slider value={bg.tintA} min={0.05} max={0.7} step={0.01} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, tintA: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>}
              <Field label="Dark edges"><Slider value={bg.vignette} min={0} max={0.9} step={0.05} onChange={(v) => change((x) => ({ ...x, bg: { ...x.bg, vignette: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
            </>}
            {tab === "speaker" && (imgs.cut ? <>
              <Toggle on={ps.show} onChange={(v) => change((x) => ({ ...x, person: { ...x.person, show: v } }))} label="Show the speaker cut-out" />
              <Field label="Size"><Slider value={ps.scale} min={0.4} max={2.5} step={0.02} onChange={(v) => change((x) => ({ ...x, person: { ...x.person, scale: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
              <Field label="Outline"><Color value={ps.outlineW > 0 ? ps.outline : ""} none onChange={(v) => change((x) => ({ ...x, person: { ...x.person, outline: v, outlineW: v ? Math.max(0.006, x.person.outlineW) : 0 } }))} /></Field>
              {ps.outlineW > 0 && <Field label="Outline width"><Slider value={ps.outlineW} min={0.003} max={0.03} step={0.001} onChange={(v) => change((x) => ({ ...x, person: { ...x.person, outlineW: v } }), false)} fmt={(v) => `${Math.round(v * 1000)}`} /></Field>}
              <Field label="Glow"><Color value={ps.glow} none onChange={(v) => change((x) => ({ ...x, person: { ...x.person, glow: v } }))} /></Field>
              <Field label="Shadow"><Slider value={ps.shadow} min={0} max={1} step={0.05} onChange={(v) => change((x) => ({ ...x, person: { ...x.person, shadow: v } }), false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
              <Toggle on={ps.flip} onChange={(v) => change((x) => ({ ...x, person: { ...x.person, flip: v } }))} label="Flip left / right" />
            </> : <p className="muted small">No speaker cut-out for this frame. Move the Frame slider under the preview to a moment where the person is clearly visible.</p>)}
            {tab === "text" && <>
              <div className="row gap wrap">
                <Btn small icon="plus" onClick={() => { const t = text({ text: "New text", y: 0.5, size: wide ? 0.07 : 0.12 }); change((x) => ({ ...x, layers: [...x.layers, t] })); setSel(t.id); }}>Add text</Btn>
                {d.layers.filter((l) => l.kind === "text").map((l) => <button key={l.id} className={`chipbtn ${sel === l.id ? "on" : ""}`} onClick={() => setSel(l.id)}>{(l as TextLayer).text.split("\n")[0].slice(0, 14) || "text"}</button>)}
              </div>
              {cur && cur.kind === "text" ? <>
                <Field label="Words (Enter = new line)"><textarea className="input" rows={2} value={cur.text} onChange={(e) => setLayer(cur.id, { text: e.target.value }, false)} /></Field>
                <div className="two">
                  <Field label="Font"><Select value={cur.font} options={fonts.map((f) => [f, f] as [string, string])} onChange={(v) => setLayer(cur.id, { font: v })} /></Field>
                  <Field label="Size"><Slider value={cur.size} min={0.02} max={0.6} step={0.005} onChange={(v) => setLayer(cur.id, { size: v }, false)} fmt={(v) => `${Math.round(v * 100)}`} /></Field>
                </div>
                <Field label="Colour"><Color value={cur.color} onChange={(v) => setLayer(cur.id, { color: v })} /></Field>
                <Field label="Outline"><Color value={cur.strokeW > 0 ? cur.stroke : ""} none onChange={(v) => setLayer(cur.id, { stroke: v || cur.stroke, strokeW: v ? Math.max(0.006, cur.strokeW) : 0 })} /></Field>
                {cur.strokeW > 0 && <Field label="Outline width"><Slider value={cur.strokeW} min={0.002} max={0.03} step={0.001} onChange={(v) => setLayer(cur.id, { strokeW: v }, false)} fmt={(v) => `${Math.round(v * 1000)}`} /></Field>}
                <Field label="Box behind"><Color value={cur.box} none onChange={(v) => setLayer(cur.id, { box: v })} /></Field>
                <Field label="Glow"><Color value={cur.glow} none onChange={(v) => setLayer(cur.id, { glow: v })} /></Field>
                <div className="two">
                  <Field label="Shadow"><Slider value={cur.shadow} min={0} max={1} step={0.05} onChange={(v) => setLayer(cur.id, { shadow: v }, false)} fmt={(v) => `${Math.round(v * 100)}%`} /></Field>
                  <Field label="Tilt"><Slider value={cur.rot} min={-25} max={25} onChange={(v) => setLayer(cur.id, { rot: v }, false)} fmt={(v) => `${v}°`} /></Field>
                </div>
                <div className="row gap wrap">
                  <Seg value={cur.align} options={[["left", "Left"], ["center", "Centre"], ["right", "Right"]]} onChange={(v) => setLayer(cur.id, { align: v })} />
                  <Toggle on={cur.upper} onChange={(v) => setLayer(cur.id, { upper: v })} label="CAPITALS" />
                  <Btn small kind="ghost" icon="trash" onClick={() => { change((x) => ({ ...x, layers: x.layers.filter((l) => l.id !== cur.id) })); setSel(null); }}>Delete</Btn>
                </div>
              </> : <p className="muted small">Click a text on the thumbnail (or one of the names above) to edit it.</p>}
            </>}
            {tab === "stickers" && <>
              <div className="row gap wrap">
                {([["arrow", "Arrow"], ["circle", "Circle"], ["bar", "Underline"]] as const).map(([k, n]) => <Btn key={k} small icon="plus" onClick={() => { const s: Sticker = { id: uid(), kind: k, x: 0.5, y: 0.45, size: 0.25, rot: 0, color: "#FF2D2D" }; change((x) => ({ ...x, layers: [...x.layers, s] })); setSel(s.id); }}>{n}</Btn>)}
              </div>
              <div className="td-emoji">{EMOJI.map((e) => <button key={e} onClick={() => { const s: Sticker = { id: uid(), kind: "emoji", x: 0.5, y: 0.4, size: 0.2, rot: 0, color: "#fff", text: e }; change((x) => ({ ...x, layers: [...x.layers, s] })); setSel(s.id); }}>{e}</button>)}</div>
              {cur && cur.kind !== "text" ? <>
                {cur.kind !== "emoji" && <Field label="Colour"><Color value={cur.color} onChange={(v) => setLayer(cur.id, { color: v })} /></Field>}
                <div className="two">
                  <Field label="Size"><Slider value={cur.size} min={0.04} max={0.9} step={0.01} onChange={(v) => setLayer(cur.id, { size: v }, false)} fmt={(v) => `${Math.round(v * 100)}`} /></Field>
                  <Field label="Rotate"><Slider value={cur.rot} min={-180} max={180} step={5} onChange={(v) => setLayer(cur.id, { rot: v }, false)} fmt={(v) => `${v}°`} /></Field>
                </div>
                <Btn small kind="ghost" icon="trash" onClick={() => { change((x) => ({ ...x, layers: x.layers.filter((l) => l.id !== cur.id) })); setSel(null); }}>Delete</Btn>
              </> : <p className="muted small">Add a sticker, then drag it on the thumbnail.</p>}
            </>}
          </div>}
        </div>
      </div>
    </Modal>
  );
}
