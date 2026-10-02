// Video editor: project model, timeline maths, the play clock and the command bus the menu bar uses.
import { useSyncExternalStore } from "react";

export type Media = { id: string; path: string; name: string; kind: "video" | "audio" | "image"; duration: number;
  width: number; height: number; fps: number; has_audio: boolean; key: string };
export type Track = { id: string; kind: "video" | "audio"; name: string; muted: boolean; hidden: boolean };
export type Item = { id: string; track: string; media: string; start: number; in: number; out: number; speed: number;
  volume: number; muted: boolean; fade_in: number; fade_out: number; x: number; y: number; scale: number; rot: number;
  opacity: number; crop: [number, number, number, number] };
export type EProject = { id: string; name: string; created: number; updated: number; width: number; height: number;
  fps: number; bg: string; media: Media[]; tracks: Track[]; items: Item[]; markers?: number[]; recording?: string };
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
