// Video editor: project model, timeline maths, the play clock and the command bus the menu bar uses.
import { useSyncExternalStore } from "react";

export type Media = { id: string; path: string; name: string; kind: "video" | "audio" | "image"; duration: number;
  width: number; height: number; fps: number; has_audio: boolean; key: string };
export type Track = { id: string; kind: "video" | "audio"; name: string; muted: boolean; hidden: boolean; pin?: boolean; duck?: boolean };
export type Fx = { look?: string; bright?: number; contrast?: number; sat?: number; temp?: number; tint?: number; lift?: number; blur?: number; vignette?: number; grain?: number; sharpen?: number };
export type Trans = { type: string; dur: number };
export type Anim = { type: string; dur: number };
/** Things drawn on top of (or done to) the picture: zoom blocks, text, shapes, captions. */
export type El = { id: string; kind: "zoom" | "text" | "shape" | "caption"; start: number; dur: number; auto?: boolean; pin?: boolean;
  cx?: number; cy?: number; z?: number; ease?: number;
  text?: string; font?: string; size?: number; color?: string; bold?: boolean; italic?: boolean; upper?: boolean; stroke?: number; stroke_color?: string; shadow?: number;
  box?: boolean; box_color?: string; box_alpha?: number; box_pad?: number; spacing?: number;
  shape?: string; x?: number; y?: number; w?: number; h?: number; rot?: number; width?: number; alpha?: number; n?: number; strength?: number; text_color?: string;
  anim_in?: Anim; anim_out?: Anim; anim_loop?: Anim };
export type CursorFx = { media: string; events: string; offset?: number; ripple?: boolean; ripple_color?: string; highlight?: boolean; highlight_color?: string; spotlight?: boolean; size?: number };
export type Events = { clicks: [number, number, number, string][]; moves: [number, number, number][]; keys: number[] };
export type Item = { id: string; track: string; media: string; start: number; in: number; out: number; speed: number;
  volume: number; muted: boolean; fade_in: number; fade_out: number; x: number; y: number; scale: number; rot: number;
  opacity: number; crop: [number, number, number, number]; fx?: Fx; enter?: Trans; exit?: Trans; tail?: number; motion?: string; afx?: { denoise?: boolean; level?: boolean }; auto?: string };
export type EProject = { id: string; name: string; created: number; updated: number; width: number; height: number;
  fps: number; bg: string; media: Media[]; tracks: Track[]; items: Item[]; markers?: number[]; recording?: string;
  els?: El[]; cursor?: CursorFx; captions?: El; chapters?: { t: number; title: string }[]; auto?: Record<string, any> };
export type Assets = { key: string; poster: string; strip: string; strip_n: number; wave: string; proxy: string;
  need_proxy: boolean; ready: boolean; error: string };
export type EExport = { id: string; project: string; state: string; frac: number; file: string; error: string; name: string;
  width: number; height: number };

export const uid = () => Math.random().toString(36).slice(2, 10);
export const r3 = (v: number) => Math.round(v * 1000) / 1000;
export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
export const itemDur = (it: Item) => (it.out - it.in) / Math.max(0.05, it.speed || 1);
export const itemEnd = (it: Item) => it.start + itemDur(it);
export const projDur = (p: EProject) => p.items.reduce((m, it) => Math.max(m, itemEnd(it)), 0);
export const elEnd = (e: El) => e.start + e.dur;

export function tc(t: number, fps = 30, frames = true): string {
  t = Math.max(0, t);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  const f = Math.floor((t - Math.floor(t)) * fps + 1e-6);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${h ? p(h) + ":" : ""}${p(m)}:${p(s)}${frames ? ":" + p(f) : ""}`;
}

/** Snap a time to the nearest interesting point (clip edges, playhead, markers) within `tol` seconds. */
export function snapTime(t: number, points: number[], tol: number): number {
  let best = t, bd = tol;
  for (const p of points) { const d = Math.abs(p - t); if (d < bd) { bd = d; best = p; } }
  return best;
}
export function snapPoints(p: EProject, skip: Set<string>, playhead: number): number[] {
  const out = [0, playhead, ...(p.markers || [])];
  for (const it of p.items) if (!skip.has(it.id)) out.push(it.start, itemEnd(it));
  for (const e of p.els || []) if (!skip.has(e.id)) out.push(e.start, elEnd(e));
  return out;
}

/** Keep clips on one track from overlapping: push `ids` right until they sit in free space. */
export function settle(items: Item[], ids: Set<string>): Item[] {
  const out = items.map((x) => ({ ...x }));
  const moved = out.filter((x) => ids.has(x.id)).sort((a, b) => a.start - b.start);
  for (const it of moved) {
    it.start = Math.max(0, it.start);
    for (let guard = 0; guard < 200; guard++) {
      const hit = out.find((o) => o.id !== it.id && o.track === it.track && o.start < itemEnd(it) - 0.001 && itemEnd(o) > it.start + 0.001);
      if (!hit) break;
      it.start = r3(itemEnd(hit));
    }
  }
  return out;
}

export function splitAt(p: EProject, t: number, sel: string[]): { items: Item[]; made: string[] } {
  const under = p.items.filter((it) => t > it.start + 0.04 && t < itemEnd(it) - 0.04);
  const pick = sel.length ? under.filter((it) => sel.includes(it.id)) : under;
  const targets = pick.length ? pick : under;
  const made: string[] = [];
  const items = p.items.flatMap((it) => {
    if (!targets.includes(it)) return [it];
    const cut = r3(it.in + (t - it.start) * it.speed);
    const b: Item = { ...it, id: uid(), start: r3(t), in: cut, fade_in: 0 };
    made.push(b.id);
    return [{ ...it, out: cut, fade_out: 0 }, b];
  });
  return { items, made };
}

export function removeItems(p: EProject, ids: string[], ripple: boolean): Item[] {
  const gone = p.items.filter((it) => ids.includes(it.id));
  let items = p.items.filter((it) => !ids.includes(it.id));
  if (ripple) {
    for (const g of gone.sort((a, b) => b.start - a.start)) {
      const d = itemDur(g);
      items = items.map((it) => (it.track === g.track && it.start >= g.start - 0.001 ? { ...it, start: r3(Math.max(0, it.start - d)) } : it));
    }
  }
  return items;
}

/** Closing a gap in the picture also pulls the captions, zooms and titles after it along. */
export function rippleEls(p: EProject, ids: string[]): El[] {
  let els = p.els || [];
  const gone = p.items.filter((it) => ids.includes(it.id) && p.tracks.find((t) => t.id === it.track)?.kind === "video" && !p.tracks.find((t) => t.id === it.track)?.pin);
  for (const g of gone.sort((a, b) => b.start - a.start)) {
    const d = itemDur(g), a = g.start, b = a + d;
    els = els.flatMap((e) => {
      if (elEnd(e) <= a + 0.001) return [e];
      if (e.start >= b - 0.001) return [{ ...e, start: r3(e.start - d) }];
      const s = Math.min(e.start, a), end = Math.max(a, elEnd(e) - d);
      return end - s > 0.15 ? [{ ...e, start: r3(s), dur: r3(end - s) }] : [];
    });
  }
  return els;
}

// ================================================================ effects (mirrors shortsforge/vfx.py: keep the numbers identical)
export type ZKey = [number, number, number, number];
export function zoomKeys(els: El[]): ZKey[] {
  const zs = els.filter((e) => e.kind === "zoom" && e.dur > 0.15).sort((a, b) => a.start - b.start);
  const keys: ZKey[] = [];
  let prevEnd = -1e9;
  zs.forEach((e, i) => {
    const a = Math.max(e.start, prevEnd), b = e.start + e.dur;
    if (b - a < 0.15) return;
    const z = Math.max(1, e.z || 1.6), cx = e.cx ?? 0.5, cy = e.cy ?? 0.5, ease = Math.min(e.ease || 0.5, (b - a) / 2);
    const joined = keys.length > 0 && a - prevEnd < 0.3 && keys[keys.length - 1][1] > 1.001;
    if (!joined) {
      if (keys.length && keys[keys.length - 1][1] > 1.001) { const k = keys[keys.length - 1]; keys.push([k[0] + Math.min(0.5, Math.max(0.1, a - k[0])), 1, k[2], k[3]]); }
      keys.push([a, 1, cx, cy]);
    }
    keys.push([a + ease, z, cx, cy]);
    const nxt = zs[i + 1], chain = !!nxt && nxt.start - b < 0.3;
    keys.push([Math.max(a + ease, chain ? b : b - ease), z, cx, cy]);
    if (!chain) keys.push([b, 1, cx, cy]);
    prevEnd = b;
  });
  const out: ZKey[] = [];
  for (let k of keys) { if (out.length && k[0] <= out[out.length - 1][0]) k = [out[out.length - 1][0] + 0.001, k[1], k[2], k[3]]; out.push(k); }
  return out;
}
export function zoomAt(keys: ZKey[], t: number): [number, number, number] {
  if (!keys.length || t <= keys[0][0] || t >= keys[keys.length - 1][0]) return [1, 0.5, 0.5];
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (a[0] <= t && t < b[0]) { const u = (t - a[0]) / (b[0] - a[0]), s = u * u * (3 - 2 * u); return [a[1] + (b[1] - a[1]) * s, a[2] + (b[2] - a[2]) * s, a[3] + (b[3] - a[3]) * s]; }
  }
  return [1, 0.5, 0.5];
}

export const LOOKS: Record<string, [string, Fx]> = {
  none: ["Original", {}], vivid: ["Vivid", { contrast: 1.08, sat: 1.35 }], cinematic: ["Cinematic", { contrast: 1.12, sat: 1.06, temp: 0.07, bright: -0.01, vignette: 0.4 }],
  teal_orange: ["Teal & orange", { contrast: 1.1, sat: 1.15, temp: 0.12, tint: -0.05, vignette: 0.25 }], warm: ["Warm", { temp: 0.14, sat: 1.1 }],
  cool: ["Cool", { temp: -0.14, sat: 1.05, contrast: 1.04 }], golden: ["Golden hour", { temp: 0.2, sat: 1.18, bright: 0.02, lift: 0.03 }], bw: ["Black & white", { sat: 0, contrast: 1.2 }],
  noir: ["Noir", { sat: 0, contrast: 1.42, bright: -0.03, vignette: 0.55 }], vintage: ["Vintage", { sat: 0.78, temp: 0.13, lift: 0.07, contrast: 0.95, vignette: 0.4, grain: 0.35 }],
  matte: ["Matte", { contrast: 0.9, lift: 0.09, sat: 0.92 }], faded: ["Faded", { contrast: 0.84, lift: 0.12, sat: 0.8 }], punchy: ["Punchy", { contrast: 1.18, sat: 1.45 }],
  dream: ["Dream", { contrast: 0.95, bright: 0.04, sat: 1.1, blur: 0.6 }], night: ["Night", { temp: -0.2, bright: -0.07, sat: 0.85, contrast: 1.1, vignette: 0.3 }],
  pastel: ["Pastel", { sat: 0.74, bright: 0.05, contrast: 0.92, lift: 0.04 }], crisp: ["Crisp", { contrast: 1.06, sat: 1.08, sharpen: 0.6 }],
  sepia: ["Sepia", { sat: 0.25, temp: 0.3, contrast: 1.05, lift: 0.03 }], sunset: ["Sunset", { temp: 0.26, tint: 0.08, sat: 1.25, contrast: 1.06, vignette: 0.2 }],
  emerald: ["Emerald", { tint: -0.14, temp: -0.04, sat: 1.12, contrast: 1.05 }], cyber: ["Cyber", { temp: -0.16, tint: 0.16, sat: 1.4, contrast: 1.14 }],
  bleach: ["Bleach", { sat: 0.55, contrast: 1.3, bright: 0.02 }], moody: ["Moody", { bright: -0.06, contrast: 1.16, sat: 0.85, vignette: 0.45 }],
  glow: ["Soft glow", { bright: 0.05, contrast: 0.96, sat: 1.15, blur: 0.25, lift: 0.03 }],
};
const FX0 = { bright: 0, contrast: 1, sat: 1, temp: 0, tint: 0, lift: 0, blur: 0, vignette: 0, grain: 0, sharpen: 0 };
export function fxValues(fx?: Fx) {
  const v: Record<string, number> = { ...FX0 };
  if (!fx) return v;
  Object.assign(v, (LOOKS[fx.look || "none"] || LOOKS.none)[1]);
  for (const k of Object.keys(FX0)) { const d = (fx as any)[k]; if (typeof d === "number") v[k] = k === "contrast" || k === "sat" ? v[k] * d : v[k] + d; }
  return v;
}
/** feColorMatrix values that do exactly what the export's colour filters do. null = no colour change. */
export function fxMatrix(fx?: Fx): string | null {
  if (!fx) return null;
  const v = fxValues(fx);
  const gains = [1 + 0.5 * v.temp, 1 - 0.5 * v.tint, 1 - 0.5 * v.temp];
  const S = gains.map((g) => v.contrast * g * (1 - v.lift)), O = gains.map((g) => ((0.5 - 0.5 * v.contrast) + v.bright) * g * (1 - v.lift) + v.lift);
  if (S.every((x) => Math.abs(x - 1) < 1e-4) && O.every((x) => Math.abs(x) < 1e-4) && Math.abs(v.sat - 1) < 1e-4) return null;
  const lw = [0.2126, 0.7152, 0.0722], rows: string[] = [];
  for (let i = 0; i < 3; i++) {
    const m = [0, 1, 2].map((j) => lw[j] * (1 - v.sat) + (i === j ? v.sat : 0));
    rows.push([m[0] * S[0], m[1] * S[1], m[2] * S[2], 0, m[0] * O[0] + m[1] * O[1] + m[2] * O[2]].map((x) => x.toFixed(5)).join(" "));
  }
  rows.push("0 0 0 1 0");
  return rows.join(" ");
}

/** Where an animated element is at time t: opacity, offset (canvas px), scale, extra rotation, blur (canvas px). */
export function animAt(e: El, t: number, H: number) {
  const d = e.dur, u0 = t - e.start;
  const ain = e.anim_in?.type && e.anim_in.type !== "none" ? e.anim_in : null, aout = e.anim_out?.type && e.anim_out.type !== "none" ? e.anim_out : null;
  const di = ain ? Math.min(ain.dur || 0.4, d / 2) : 0, dout = aout ? Math.min(aout.dur || 0.4, d / 2) : 0;
  const s = { op: 1, dx: 0, dy: 0, sc: 1, sx: 1, sy: 1, rot: 0, blur: 0, chars: -1 };
  const off = 0.06 * H;
  if (ain && u0 < di) {
    const u = clamp(u0 / di, 0, 1), fade = Math.min(1, u / 0.6);
    switch (ain.type) {
      case "fade": s.op = u; break;
      case "up": s.dy = off * (1 - u); s.op = fade; break;
      case "down": s.dy = -off * (1 - u); s.op = fade; break;
      case "left": s.dx = off * (1 - u); s.op = fade; break;
      case "right": s.dx = -off * (1 - u); s.op = fade; break;
      case "pop": s.sc = u < 0.65 ? 0.55 + 0.53 * (u / 0.65) : 1.08 - 0.08 * ((u - 0.65) / 0.35); s.op = fade; break;
      case "zoom": s.sc = 1.65 - 0.65 * Math.sqrt(u); s.op = fade; break;
      case "blur": s.blur = 14 * (1 - u); s.op = u; break;
      case "spin": s.rot = -14 * (1 - Math.sqrt(u)); s.sc = 0.8 + 0.2 * Math.sqrt(u); s.op = fade; break;
      case "drop": s.dy = -2.2 * off * (1 - u); s.op = fade; break;
      case "stretch": s.sx = Math.sqrt(u); s.op = fade; break;
      case "flip": s.sy = Math.sqrt(u); s.op = fade; break;
      case "bounce": s.sc = u < 0.45 ? 0.3 + 0.88 * (u / 0.45) : u < 0.7 ? 1.18 - 0.26 * ((u - 0.45) / 0.25) : u < 0.85 ? 0.92 + 0.12 * ((u - 0.7) / 0.15) : 1.04 - 0.04 * ((u - 0.85) / 0.15); s.op = Math.min(1, u / 0.3); break;
      case "type": s.chars = u; break;
      default: s.op = u;
    }
  } else if (aout && u0 > d - dout) {
    const u = clamp((u0 - (d - dout)) / dout, 0, 1), fade = 1 - clamp((u - 0.4) / 0.6, 0, 1);
    switch (aout.type) {
      case "fade": s.op = 1 - u; break;
      case "up": s.dy = -off * u; s.op = fade; break;
      case "down": s.dy = off * u; s.op = fade; break;
      case "left": s.dx = -off * u; s.op = fade; break;
      case "right": s.dx = off * u; s.op = fade; break;
      case "pop": s.sc = 1 - 0.45 * u; s.op = fade; break;
      case "zoom": s.sc = 1 + 0.65 * u * u; s.op = fade; break;
      case "blur": s.blur = 14 * u; s.op = 1 - u; break;
      case "spin": s.rot = 14 * u * u; s.sc = 1 - 0.2 * u * u; s.op = fade; break;
      case "drop": s.dy = 2.2 * off * u; s.op = fade; break;
      case "stretch": s.sx = 1 - u * u; s.op = fade; break;
      case "flip": s.sy = 1 - u * u; s.op = fade; break;
      default: s.op = 1 - u;
    }
  } else if (e.anim_loop?.type && LOOP_PERIOD[e.anim_loop.type]) {      // keeps moving while it is on screen
    const P = LOOP_PERIOD[e.anim_loop.type], hold = d - di - dout, th = u0 - di;
    if (hold >= P && th < Math.min(150, Math.floor(hold / P)) * P) {
      const f = (th % P) / P, tri = (pts: [number, number][]) => { let pf = 0, pv = 0; for (const [x, v] of pts) { if (f <= x) return pv + (v - pv) * ((f - pf) / (x - pf)); pf = x; pv = v; } return pv; };
      switch (e.anim_loop.type) {
        case "pulse": s.sc = 1 + tri([[0.5, 0.06], [1, 0]]); break;
        case "heartbeat": s.sc = 1 + tri([[0.1, 0.12], [0.25, 0], [0.35, 0.08], [0.5, 0], [1, 0]]); break;
        case "wiggle": s.rot = -tri([[0.25, 3], [0.75, -3], [1, 0]]); break;
        case "swing": s.rot = -tri([[0.25, 7], [0.75, -7], [1, 0]]); break;
        case "blink": s.op = 1 - tri([[0.5, 0.7], [1, 0]]); break;
      }
    }
  }
  return s;
}

/** Clip entrance / exit at time t: offset as a share of the canvas, size multiplier, opacity multiplier. */
export function transAt(it: Item, t: number) {
  const d = itemDur(it), u0 = t - it.start, s = { dx: 0, dy: 0, sc: 1, op: 1, tint: "", ta: 0 };
  if (it.motion === "push") s.sc += 0.12 * clamp(u0 / d, 0, 1); else if (it.motion === "pull") s.sc += 0.12 * (1 - clamp(u0 / d, 0, 1));
  for (const [tr, leaving] of [[it.enter, false], [it.exit, true]] as [Trans | undefined, boolean][]) {
    if (!tr?.type || tr.type === "none") continue;
    const dd = Math.min(tr.dur || 0.5, d / 2);
    const lin = leaving ? clamp((u0 - (d - dd)) / dd, 0, 1) : clamp(1 - u0 / dd, 0, 1), p = lin * lin;
    if (tr.type === "fade" || tr.type === "zoom" || tr.type === "grow") s.op *= leaving ? clamp((d - u0) / dd, 0, 1) : clamp(u0 / dd, 0, 1);
    if (tr.type === "zoom") s.sc += 0.3 * p; else if (tr.type === "grow") s.sc -= 0.45 * p;
    if ((tr.type === "flash" || tr.type === "dip") && lin > 0) { s.tint = tr.type === "flash" ? "#fff" : "#000"; s.ta = Math.max(s.ta, lin); }
    const dir = tr.type.replace("slide-", ""), sg = leaving ? -1 : 1;
    if (dir === "left") s.dx += sg * p; else if (dir === "right") s.dx -= sg * p; else if (dir === "up") s.dy += sg * p; else if (dir === "down") s.dy -= sg * p;
  }
  return s;
}

const P = (x: number, y: number) => `${x.toFixed(1)} ${y.toFixed(1)}`;
function ellipseD(rx: number, ry: number, rev = false) {
  const k = 0.5523;
  let p: [number, number][] = [[-rx, 0], [-rx, -ry * k], [-rx * k, -ry], [0, -ry], [rx * k, -ry], [rx, -ry * k], [rx, 0], [rx, ry * k], [rx * k, ry], [0, ry], [-rx * k, ry], [-rx, ry * k], [-rx, 0]];
  if (rev) p = p.reverse();
  let s = "M " + P(...p[0]);
  for (let i = 1; i < 13; i += 3) s += " C " + p.slice(i, i + 3).map((q) => P(...q)).join(" ");
  return s + " Z";
}
function rectD(w: number, h: number, rev = false) {
  let p: [number, number][] = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
  if (rev) p = p.reverse();
  return "M " + p.map((q) => P(...q)).join(" L ") + " Z";
}
/** SVG path of a shape around (0,0); w, h, sw in the same pixels. Same geometry as vfx.shape_path. */
export function shapePath(kind: string, w: number, h: number, sw: number): string {
  if (kind === "rect") return rectD(w, h) + " " + rectD(Math.max(1, w - 2 * sw), Math.max(1, h - 2 * sw), true);
  if (kind === "ellipse") return ellipseD(w / 2, h / 2) + " " + ellipseD(Math.max(1, w / 2 - sw), Math.max(1, h / 2 - sw), true);
  if (kind === "line") return rectD(w, sw);
  if (kind === "arrow") {
    const hl = Math.min(w * 0.6, sw * 3.6), hw = sw * 1.9;
    const p: [number, number][] = [[-w / 2, -sw / 2], [w / 2 - hl, -sw / 2], [w / 2 - hl, -hw], [w / 2, 0], [w / 2 - hl, hw], [w / 2 - hl, sw / 2], [-w / 2, sw / 2]];
    return "M " + p.map((q) => P(...q)).join(" L ") + " Z";
  }
  if (kind === "step") return ellipseD(h / 2, h / 2);
  return rectD(w, h);
}

export const LOOP_PERIOD: Record<string, number> = { pulse: 0.9, heartbeat: 1.2, wiggle: 0.5, swing: 1.6, blink: 0.8 };
export const ANIMS_IN: [string, string][] = [["none", "None"], ["fade", "Fade"], ["pop", "Pop"], ["bounce", "Bounce"], ["type", "Typewriter"], ["up", "Slide up"], ["down", "Slide down"], ["left", "Slide from right"], ["right", "Slide from left"],
  ["drop", "Drop in"], ["zoom", "Zoom in"], ["blur", "Blur in"], ["spin", "Spin in"], ["stretch", "Stretch open"], ["flip", "Flip open"]];
export const ANIMS_OUT: [string, string][] = [["none", "None"], ["fade", "Fade"], ["pop", "Shrink"], ["up", "Slide up"], ["down", "Slide down"], ["left", "Slide left"], ["right", "Slide right"], ["drop", "Drop away"], ["zoom", "Zoom out"],
  ["blur", "Blur out"], ["spin", "Spin out"], ["stretch", "Squeeze shut"], ["flip", "Flip shut"]];
export const ANIMS_LOOP: [string, string][] = [["none", "None"], ["pulse", "Pulse"], ["heartbeat", "Heartbeat"], ["wiggle", "Wiggle"], ["swing", "Swing"], ["blink", "Blink"]];
export const TRANSITIONS: [string, string][] = [["none", "None"], ["fade", "Fade"], ["slide-left", "Slide from right"], ["slide-right", "Slide from left"], ["slide-up", "Slide from bottom"], ["slide-down", "Slide from top"], ["zoom", "Zoom"],
  ["grow", "Grow"], ["flash", "Flash white"], ["dip", "Dip to black"]];
export const MOTIONS: [string, string][] = [["none", "Still"], ["push", "Slow push in"], ["pull", "Slow pull out"]];
export const TEXT_PRESETS: { key: string; name: string; el: Partial<El> }[] = [
  { key: "title", name: "Title", el: { text: "Your title", font: "Poppins Black", size: 0.1, color: "#FFFFFF", stroke: 0, shadow: 5, y: 0.45, pin: true, anim_in: { type: "pop", dur: 0.5 }, anim_out: { type: "fade", dur: 0.3 } } },
  { key: "subtitle", name: "Subtitle", el: { text: "A line that explains it", font: "Poppins", size: 0.045, color: "#E9E7FA", shadow: 3, y: 0.58, pin: true, anim_in: { type: "up", dur: 0.4 }, anim_out: { type: "fade", dur: 0.3 } } },
  { key: "lower", name: "Lower third", el: { text: "Name · what they do", font: "Poppins", bold: true, size: 0.04, color: "#FFFFFF", box: true, box_color: "#8A3BFF", box_alpha: 0.95, box_pad: 16, x: 0.2, y: 0.88, pin: true, anim_in: { type: "right", dur: 0.4 }, anim_out: { type: "left", dur: 0.35 } } },
  { key: "callout", name: "Callout", el: { text: "Click here", font: "Poppins", bold: true, size: 0.036, color: "#101018", box: true, box_color: "#FFD400", box_alpha: 1, box_pad: 12, pin: false, anim_in: { type: "pop", dur: 0.35 }, anim_out: { type: "fade", dur: 0.2 } } },
  { key: "outline", name: "Bold outline", el: { text: "BIG POINT", font: "Anton", size: 0.13, color: "#FFD400", stroke: 9, stroke_color: "#000000", shadow: 0, upper: true, pin: true, anim_in: { type: "zoom", dur: 0.35 }, anim_out: { type: "pop", dur: 0.25 } } },
  { key: "chapter", name: "Chapter card", el: { text: "Part 1\nGetting started", font: "DM Serif Display", size: 0.09, color: "#FFFFFF", box: true, box_color: "#000000", box_alpha: 0.72, box_pad: 40, pin: true, anim_in: { type: "blur", dur: 0.5 }, anim_out: { type: "blur", dur: 0.4 } } },
  { key: "note", name: "Side note", el: { text: "Tip: you can undo this", font: "Lato", size: 0.034, color: "#FFFFFF", box: true, box_color: "#15141F", box_alpha: 0.9, box_pad: 14, x: 0.8, y: 0.14, pin: true, anim_in: { type: "left", dur: 0.4 }, anim_out: { type: "right", dur: 0.3 } } },
  { key: "quote", name: "Quote", el: { text: "“Something worth\nremembering.”", font: "Abril Fatface", size: 0.075, color: "#FFFFFF", shadow: 4, pin: true, anim_in: { type: "fade", dur: 0.6 }, anim_out: { type: "fade", dur: 0.5 } } },
  { key: "marker", name: "Marker pen", el: { text: "Don't skip this", font: "Permanent Marker", size: 0.07, color: "#FF3D6E", rot: -4, stroke: 5, stroke_color: "#FFFFFF", pin: false, anim_in: { type: "spin", dur: 0.4 }, anim_out: { type: "fade", dur: 0.25 } } },
  { key: "typer", name: "Typewriter", el: { text: "Typing it out, letter by letter", font: "Special Elite", size: 0.05, color: "#FFFFFF", box: true, box_color: "#101018", box_alpha: 0.85, box_pad: 16, pin: true, anim_in: { type: "type", dur: 1.6 }, anim_out: { type: "fade", dur: 0.3 } } },
  { key: "cta", name: "Pulsing button", el: { text: "SUBSCRIBE", font: "Poppins Black", size: 0.05, color: "#FFFFFF", box: true, box_color: "#FF3D6E", box_alpha: 1, box_pad: 18, x: 0.84, y: 0.86, pin: true, anim_in: { type: "bounce", dur: 0.6 }, anim_loop: { type: "pulse", dur: 0 }, anim_out: { type: "pop", dur: 0.3 } } },
  { key: "stat", name: "Big number", el: { text: "10×", font: "Bebas Neue", size: 0.24, color: "#FFD400", shadow: 6, pin: true, anim_in: { type: "bounce", dur: 0.6 }, anim_loop: { type: "heartbeat", dur: 0 }, anim_out: { type: "zoom", dur: 0.3 } } },
  { key: "sticker", name: "Sticker", el: { text: "NEW!", font: "Luckiest Guy", size: 0.08, color: "#101018", box: true, box_color: "#FFD400", box_alpha: 1, box_pad: 14, rot: -8, x: 0.82, y: 0.16, pin: true, anim_in: { type: "spin", dur: 0.45 }, anim_loop: { type: "wiggle", dur: 0 }, anim_out: { type: "spin", dur: 0.3 } } },
  { key: "neon", name: "Neon", el: { text: "NEW", font: "Bungee", size: 0.11, color: "#2FD3FF", stroke: 3, stroke_color: "#8A3BFF", shadow: 6, pin: true, anim_in: { type: "blur", dur: 0.45 }, anim_out: { type: "zoom", dur: 0.3 } } },
];
export const SHAPES: { key: string; name: string; el: Partial<El> }[] = [
  { key: "arrow", name: "Arrow", el: { shape: "arrow", w: 0.16, h: 0.06, color: "#FF3D6E", width: 12, rot: 0, anim_in: { type: "pop", dur: 0.3 } } },
  { key: "rect", name: "Box", el: { shape: "rect", w: 0.22, h: 0.16, color: "#FF3D6E", width: 7, anim_in: { type: "pop", dur: 0.3 } } },
  { key: "ellipse", name: "Circle", el: { shape: "ellipse", w: 0.14, h: 0.2, color: "#FF3D6E", width: 7, anim_in: { type: "pop", dur: 0.3 } } },
  { key: "highlight", name: "Highlight", el: { shape: "highlight", w: 0.24, h: 0.06, color: "#FFD400", alpha: 0.45, anim_in: { type: "fade", dur: 0.25 } } },
  { key: "line", name: "Line", el: { shape: "line", w: 0.22, h: 0.03, color: "#FFFFFF", width: 6, anim_in: { type: "fade", dur: 0.2 } } },
  { key: "step", name: "Step number", el: { shape: "step", w: 0.07, h: 0.07, color: "#8A3BFF", n: 1, anim_in: { type: "pop", dur: 0.3 } } },
  { key: "blur", name: "Blur (hide)", el: { shape: "blur", w: 0.24, h: 0.1, strength: 0.6 } },
];
export const EL_COLORS: Record<string, string> = { zoom: "#2fd3ff", text: "#ff7ab8", shape: "#ffb020", caption: "#7fd9b0" };
export const elLabel = (e: El) => e.kind === "zoom" ? `Zoom ${(e.z || 1.6).toFixed(1)}×` : e.kind === "shape" ? (SHAPES.find((s) => s.key === e.shape)?.name || "Shape") : (e.text || "Text").replace(/\n/g, " ");

/** Where an item is drawn on the canvas: size in canvas pixels (before rotation) and its centre. */
export function itemBox(p: EProject, it: Item, m: Media) {
  const [l, t, r, b] = it.crop || [0, 0, 0, 0];
  const cw = Math.max(2, m.width * (1 - l - r)), ch = Math.max(2, m.height * (1 - t - b));
  const f = Math.min(p.width / cw, p.height / ch) * (it.scale || 1);
  return { w: cw * f, h: ch * f, cx: it.x * p.width, cy: it.y * p.height, fullW: m.width * f, fullH: m.height * f, l, t };
}

export function fadeAt(it: Item, t: number): number {
  const d = itemDur(it), u = t - it.start;
  let a = 1;
  const fi = Math.min(it.fade_in || 0, d / 2), fo = Math.min(it.fade_out || 0, d / 2);
  if (fi > 0 && u < fi) a = Math.max(0, u / fi);
  if (fo > 0 && u > d - fo) a = Math.min(a, Math.max(0, (d - u) / fo));
  return a;
}

// ---------------------------------------------------------------- play clock (kept out of React state: 60 updates a second)
type Clock = { t: number; playing: boolean };
let clock: Clock = { t: 0, playing: false };
const subs = new Set<() => void>();
export const getClock = () => clock;
export function setClock(patch: Partial<Clock>) { clock = { ...clock, ...patch }; subs.forEach((f) => f()); }
export function useClock<T>(sel: (c: Clock) => T): T {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => sel(clock));
}

// ---------------------------------------------------------------- commands (menu bar, shortcuts, toolbar all speak this)
export type Cmd = "new" | "open" | "home" | "save" | "export" | "import" | "undo" | "redo" | "split" | "delete" | "ripple" | "duplicate"
  | "selectAll" | "copy" | "paste" | "zoomIn" | "zoomOut" | "zoomFit" | "play" | "shortcuts" | "about";
const handlers = new Set<(c: Cmd) => void>();
export function onCmd(f: (c: Cmd) => void) { handlers.add(f); return () => { handlers.delete(f); }; }
export function cmd(c: Cmd) { handlers.forEach((f) => f(c)); }

export const SHORTCUTS: [string, string][] = [
  ["Space", "Play / pause"], ["S", "Split at the playhead"], ["Delete", "Delete selected"], ["Shift + Delete", "Delete and close the gap"],
  ["Ctrl + Z", "Undo"], ["Ctrl + Y", "Redo"], ["Ctrl + D", "Duplicate"], ["Ctrl + C / Ctrl + V", "Copy / paste"], ["Ctrl + A", "Select everything"],
  ["← / →", "One frame back / forward"], ["Shift + ← / →", "One second back / forward"], ["Home / End", "Start / end of the video"],
  ["J / K / L", "Back 5 s / pause / play"], ["+ / −", "Zoom the timeline"], ["Shift + Z", "Fit the timeline"], ["Ctrl + mouse wheel", "Zoom the timeline at the cursor"],
  ["Ctrl + E", "Export"], ["Ctrl + I", "Import media"], ["Ctrl + click", "Add to the selection"],
];
