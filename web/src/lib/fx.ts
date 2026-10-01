// Camera motion, focus zooms and opening effects for the live preview. Mirrors shortsforge/effects.py
// (motion_exprs / focus_windows / intro_overlay) number for number, so what you see is what exports.

export const FOCUS_RISE = 0.16, FOCUS_HOLD = 1.35, FOCUS_FALL = 0.35;
export type Punch = [number, number];            // [time in the Short, zoom amount]
export type FocusWin = [number, number, number];  // [start, end, amount]

const clip = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
const smooth = (x: number) => x * x * (3 - 2 * x);

export function focusWindows(punches: Punch[], D: number): FocusWin[] {
  const pts = punches.filter(([t]) => t >= 0.3 && t < D - 0.4).slice().sort((a, b) => a[0] - b[0]);
  const out: FocusWin[] = [];
  pts.forEach(([t, amt], i) => {
    const nxt = i + 1 < pts.length ? pts[i + 1][0] : D;
    const end = Math.min(t + FOCUS_RISE + FOCUS_HOLD, nxt - 0.05, D);
    if (end - t >= 0.3) out.push([t, end, amt]);
  });
  return out.slice(0, 40);
}

// ------------------------------------------------------------------ Story FX (mirrors shortsforge/story.py)
export type StoryEl = {
  text: string; font: string; size: number; x: number; y: number; an: number; t0: number; t1: number;
  anim: string[]; color: string; bold?: boolean; shadow?: boolean; glow?: string | null; wipe?: [number, number]; role?: string;
};
export type StoryPlan = {
  level: string; freezes: { t: number; d: number; kind: string }[]; spans: [number, number][];
  title: { t0: number; t1: number; look: string; y: number; els: StoryEl[] } | null;
  beats: { t0: number; t1: number; els: StoryEl[] }[]; streaks: number[]; leaks: number[];
  texture: { grain: number; bloom: number; leak: boolean; vignette: boolean } | null; behind?: boolean; D?: number;
};
export const FREEZE_PUSH = 0.075, FREEZE_BACK = 0.22, STREAK_Z = 0.07, STREAK_W = 0.2;

/** cut-timeline time -> finished-Short time (each freeze before it pushes it later) */
export function toFinal(t: number, fr: StoryPlan["freezes"]): number {
  let x = t;
  for (const f of fr) if (f.t < t - 1e-6) x += f.d;
  return x;
}

/** finished time -> [cut-timeline time, index of the freeze on screen or -1] */
export function toPre(T: number, fr: StoryPlan["freezes"]): [number, number] {
  let acc = 0;
  const s = [...fr].sort((a, b) => a.t - b.t);
  for (let i = 0; i < s.length; i++) {
    const a = s[i].t + acc;
    if (T < a) return [T - acc, -1];
    if (T < a + s[i].d) return [s[i].t, i];
    acc += s[i].d;
  }
  return [T - acc, -1];
}

/** per-frame Story FX looks: frozen?, flash, horizontal streak blur (px at 1080 wide), light-leak strength */
export function storyAt(st: StoryPlan | null | undefined, T: number): { frozen: boolean; flash: number; blur: number; leak: number } {
  if (!st) return { frozen: false, flash: 0, blur: 0, leak: 0 };
  let frozen = false, flash = 0, blur = 0, leak = 0;
  for (const [a, b] of st.spans || []) {
    if (T >= a && T <= b) frozen = true;
    if (T >= b && T <= b + 0.05) flash = 0.3;
  }
  for (const s of st.streaks || []) {
    if (T >= s - 0.05 && T <= s + 0.07) blur = Math.max(blur, 28);
    else if (T >= s - 0.13 && T <= s + 0.16) blur = Math.max(blur, 11);
  }
  if (st.texture?.leak) for (const L of (st.leaks || []).slice(0, 4)) if (T >= L && T < L + 2.4) leak += 0.5 * Math.exp(-Math.pow((T - L - 0.8) / 0.7, 2));
  return { frozen, flash, blur, leak: Math.min(1, leak) };
}

/** [zoom, dx, dy]: dx/dy are fractions of the frame (already clamped so no edges show). */
export function motionAt(motion: string, intro: string, wins: FocusWin[], t: number, D: number, story?: StoryPlan | null): [number, number, number] {
  const d = Math.max(D, 0.1);
  let z = 1, dx = 0, dy = 0;
  switch (motion) {
    case "slow_zoom": z = 1 + 0.1 * t / d; break;
    case "zoom_out": z = 1.12 - 0.1 * t / d; break;
    case "ken_burns": z = 1.04 + 0.08 * t / d; dx = (1 - 1 / z) / 2 * 0.6 * (t / d - 0.5) * 2; break;
    case "breathe": z = 1.035 + 0.025 * Math.sin(t * 2.2); break;
    case "pan_left": case "pan_right": z = 1.1; dx = (motion === "pan_left" ? -1 : 1) * (1 - 1 / 1.1) / 2 * 0.9 * (2 * t / d - 1); break;
    case "drift_up": z = 1.08; dy = -(1 - 1 / 1.08) / 2 * 0.9 * (2 * t / d - 1); break;
    case "zoom_pulse": z = 1.04 + 0.03 * Math.pow(Math.abs(Math.sin(t * 3.14159)), 6); break;
    case "sway": z = 1.08; dx = (1 - 1 / 1.08) / 2 * 0.8 * Math.sin(t * 0.55); dy = (1 - 1 / 1.08) / 2 * 0.6 * Math.sin(t * 0.37 + 1); break;
  }
  let extra = 0;
  for (const [a, e, amt] of wins) {
    if (t < a || t > e + 0.01) continue;
    extra += amt * Math.min(smooth(clip((t - a) / FOCUS_RISE, 0, 1)), smooth(clip((e - t) / FOCUS_FALL, 0, 1)));
  }
  for (const [a, b] of story?.spans || []) {
    const d = Math.max(0.05, b - a);
    extra += FREEZE_PUSH * smooth(clip((t - a) / d, 0, 1)) * smooth(clip((b + FREEZE_BACK - t) / FREEZE_BACK, 0, 1));
  }
  for (const T of story?.streaks || []) {
    const k = Math.pow(Math.max(0, 1 - Math.abs(t - T) / STREAK_W), 2);
    extra += STREAK_Z * k;
    dx += 0.018 * k * (t - T) / STREAK_W;
  }
  if (intro === "zoom_slam") extra += 0.38 * (1 - smooth(clip(t / 0.42, 0, 1)));
  else if (intro === "punch_in") extra += 0.13 * Math.min(clip(t / 0.1, 0, 1), smooth(clip((0.62 - t) / 0.5, 0, 1)));
  else if (intro === "whip") { const k = 1 - smooth(clip(t / 0.32, 0, 1)); extra += 0.22 * k; dx += 0.085 * k; }
  else if (intro === "rgb_glitch") { if (t < 0.5) { extra += 0.06; dx += 0.009 * Math.sin(t * 170); } }
  else if (intro === "shake") { if (t < 0.45) { extra += 0.05; dx += 0.0148 * Math.sin(t * 95); dy += 0.00625 * Math.cos(t * 83); } }
  z = z * (1 + extra);
  const h = (1 - 1 / z) / 2;
  return [z, clip(dx, -h, h), clip(dy, -h, h)];
}

/** Overlay for the opening: white/black flash opacity, and whether the RGB split is on. */
export function introAt(intro: string, t: number): { color?: string; alpha: number; rgb: boolean } {
  switch (intro) {
    case "flash": return { color: "#fff", alpha: t < 0.35 ? 1 - t / 0.35 : 0, rgb: false };
    case "fade_white": return { color: "#fff", alpha: t < 0.7 ? 1 - t / 0.7 : 0, rgb: false };
    case "fade_black": return { color: "#000", alpha: t < 0.4 ? 1 - t / 0.4 : 0, rgb: false };
    case "zoom_slam": return { color: "#fff", alpha: t < 0.12 ? 1 - t / 0.12 : 0, rgb: false };
    case "rgb_glitch": return { alpha: 0, rgb: t < 0.5 && (t % 0.14) < 0.08 };
    default: return { alpha: 0, rgb: false };
  }
}
