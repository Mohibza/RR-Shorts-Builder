// Mirrors the engine's edit logic (projects.playback_parts / edited_words / pacing.keep_ranges) so the live
// preview plays exactly the parts the export will contain, without rendering anything.
import type { Clip, Edits, Word } from "./types";

export type Seg = { a: number; b: number; t0: number }; // absolute source range + where it starts in the Short

export function baseParts(c: Clip): [number, number][] {
  const segs = c.clip.segments || [];
  return segs.length ? segs.map((s) => [s[0], s[1]] as [number, number]) : [[c.clip.start, c.clip.end]];
}

export function playbackParts(c: Clip, edits: Edits): [number, number][] {
  let parts = baseParts(c);
  if (edits.trim) {
    const [t0, t1] = edits.trim;
    const out: [number, number][] = [];
    let acc = 0;
    for (const [a, b] of parts) {
      const L = b - a;
      const s = Math.max(t0, acc), e = Math.min(t1, acc + L);
      if (e - s > 0.05) out.push([a + (s - acc), a + (e - acc)]);
      acc += L;
    }
    if (out.length) parts = out;
  }
  const words = c.words || [];
  const cuts: [number, number][] = [];
  for (const i of [...new Set(edits.cut || [])].sort((x, y) => x - y)) {
    const w = words[i];
    if (!w) continue;
    const a = w.s - 0.02, b = w.e + 0.02;
    if (cuts.length && a <= cuts[cuts.length - 1][1] + 0.12) cuts[cuts.length - 1][1] = Math.max(cuts[cuts.length - 1][1], b);
    else cuts.push([a, b]);
  }
  for (const [ca, cb] of cuts) {
    const nxt: [number, number][] = [];
    for (const [a, b] of parts) {
      if (cb <= a || ca >= b) { nxt.push([a, b]); continue; }
      if (ca - a > 0.08) nxt.push([a, ca]);
      if (b - cb > 0.08) nxt.push([cb, b]);
    }
    parts = nxt;
  }
  const res = parts.filter((p) => p[1] - p[0] > 0.05);
  return res.length ? res : baseParts(c);
}

export function editedWords(c: Clip, edits: Edits): (Word & { i: number })[] {
  const cut = new Set(edits.cut || []);
  const fix = edits.fix || {};
  const out: (Word & { i: number })[] = [];
  (c.words || []).forEach((w, i) => {
    if (cut.has(i)) return;
    const t = String(fix[i] ?? w.w).trim();
    if (t) out.push({ w: t, s: w.s, e: w.e, i });
  });
  return out;
}

// filler sounds cut out with the pauses (pacing.FILLERS)
const FILLERS = new Set(["um", "umm", "ummm", "uh", "uhh", "uhhh", "uhm", "erm", "hmm", "hmmm", "mmm"]);
export const isFiller = (w: string) => FILLERS.has(w.toLowerCase().replace(/[^\p{L}\p{N}_']/gu, ""));

function keepRanges(words: Word[], dur: number): [number, number][] {
  const maxPause = 0.45, lead = 0.12, tail = 0.15, startPad = 0.08, endPad = 0.35;
  const idx: number[] = [];
  words.forEach((w, i) => { if (!isFiller(w.w)) idx.push(i); });
  if (!idx.length) return [[0, dur]];
  const real = idx.map((i) => words[i]);
  const ranges: [number, number][] = [];
  let a = real[0].s > 0.4 ? Math.max(0, real[0].s - startPad) : 0;
  let prevEnd = real[0].e;
  let prevI = idx[0];
  for (let k = 1; k < real.length; k++) {
    const w = real[k];
    const fill = idx[k] - prevI > 1;
    const gap = w.s - prevEnd;
    if (gap > maxPause || (fill && gap > 0.25)) {
      ranges.push([a, prevEnd + Math.min(tail, fill ? gap / 3 : tail)]);
      a = w.s - Math.min(lead, fill ? gap / 3 : lead);
    }
    prevEnd = Math.max(prevEnd, w.e);
    prevI = idx[k];
  }
  ranges.push([a, Math.min(dur, prevEnd + endPad)]);
  const out: [number, number][] = [];
  for (const [s, e] of ranges) {
    if (e - s < 0.05) continue;
    if (out.length && s <= out[out.length - 1][1] + 0.02) out[out.length - 1][1] = Math.max(out[out.length - 1][1], e);
    else out.push([s, e]);
  }
  return out.length ? out : [[0, dur]];
}

export type Timeline = { segs: Seg[]; D: number; words: (Word & { i: number; T: number; TE: number })[] };

/** Segments the Short plays (after trim, removed words and pause removal) and the words re-timed to them. */
export function buildTimeline(c: Clip, edits: Edits, removePauses = true): Timeline {
  const parts = playbackParts(c, edits);
  const ew = editedWords(c, edits);
  const segs: Seg[] = [];
  let T = 0;
  for (const [a, b] of parts) {
    const pw = ew.filter((w) => w.s >= a - 0.05 && w.e <= b + 0.3).map((w) => ({ w: w.w, s: Math.max(0, w.s - a), e: Math.min(b, w.e) - a }));
    let keep: [number, number][] = [[0, b - a]];
    if (removePauses && pw.length >= 3) {
      const r = keepRanges(pw, b - a);
      const kept = r.reduce((x, [s, e]) => x + e - s, 0);
      if (b - a - kept >= 0.4) keep = r;
    }
    for (const [s, e] of keep) { segs.push({ a: a + s, b: a + e, t0: T }); T += e - s; }
  }
  const words: Timeline["words"] = [];
  const seen = new Set<string>();
  for (const w of ew) {
    if (removePauses && isFiller(w.w)) {            // a cut-out "um" leaves the captions too
      const mid = (w.s + w.e) / 2;
      if (!segs.some((g) => mid >= g.a && mid <= g.b)) continue;
    }
    for (const g of segs) {
      if (w.s >= g.a - 0.15 && w.s < g.b) {
        const key = `${w.i}@${g.t0}`;
        if (seen.has(key)) break;
        seen.add(key);
        const s = g.t0 + Math.max(0, w.s - g.a);
        words.push({ ...w, T: s, TE: Math.max(s + 0.05, g.t0 + Math.min(g.b, w.e) - g.a) });
        break;
      }
    }
  }
  words.sort((x, y) => x.T - y.T);
  return { segs, D: T, words };
}

export function segAt(tl: Timeline, T: number): number {
  for (let i = 0; i < tl.segs.length; i++) {
    const g = tl.segs[i];
    if (T < g.t0 + (g.b - g.a)) return i;
  }
  return tl.segs.length - 1;
}

export function absAt(tl: Timeline, T: number): number {
  const i = segAt(tl, T);
  const g = tl.segs[i];
  return g ? g.a + Math.max(0, T - g.t0) : 0;
}

export function chunkWords<W extends { w: string; T: number; TE: number }>(words: W[], n: number, maxchars: number): W[][] {
  const chunks: W[][] = [];
  let cur: W[] = [];
  for (const w of words) {
    if (cur.length) {
      const gap = w.T - cur[cur.length - 1].TE;
      const chars = cur.reduce((x, y) => x + y.w.length + 1, 0) + w.w.length;
      if (cur.length >= n || chars > maxchars || gap > 0.55 || /[.,?!;:۔؟،।]$/.test(cur[cur.length - 1].w)) {
        chunks.push(cur); cur = [];
      }
    }
    cur.push(w);
  }
  if (cur.length) chunks.push(cur);
  return chunks;
}

export function cameraAt(cam: [number, number][] | undefined, abs: number): number | null {
  if (!cam || !cam.length) return null;
  if (abs <= cam[0][0]) return cam[0][1];
  for (let i = 1; i < cam.length; i++) {
    const [t1, x1] = cam[i];
    const [t0, x0] = cam[i - 1];
    if (abs <= t1) {
      if (t1 - t0 > 3) return x1;       // different part: jump, don't glide across the cut
      return x0 + ((x1 - x0) * (abs - t0)) / Math.max(1e-3, t1 - t0);
    }
  }
  return cam[cam.length - 1][1];
}

export function cleanWord(w: string, upper: boolean): string {
  let t = w.trim().replace(/^["'“(]+|["'”)]+$/g, "").replace(/[,;:]+$/, "").replace(/(?<!\.)\.$/, "");
  if (upper && !/[؀-ۿऀ-ॿ]/.test(t)) t = t.toUpperCase();
  return t;
}

export const fmt = (s: number) => {
  s = Math.max(0, s);
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};
