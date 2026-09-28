// Draws one video frame into the 9:16 preview canvas exactly like the export's layout (renderer.layout_graph).
export type FrameParams = { layout: string; cam: number | null; z: number; fx: number; fy: number };

let small: HTMLCanvasElement | null = null;

function blurBg(ctx: CanvasRenderingContext2D, v: HTMLVideoElement, x: number, y: number, w: number, h: number) {
  if (!small) { small = document.createElement("canvas"); small.width = 48; small.height = 86; }
  const sc = small.getContext("2d")!;
  const sw = v.videoWidth, sh = v.videoHeight;
  // cover-crop the frame into the small canvas, then enlarge it blurred and darkened
  const r = Math.max(small.width / sw, small.height / sh);
  const cw = small.width / r, ch = small.height / r;
  sc.drawImage(v, (sw - cw) / 2, (sh - ch) / 2, cw, ch, 0, 0, small.width, small.height);
  ctx.save();
  ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
  ctx.filter = `blur(${Math.round(w / 45)}px) brightness(0.72) saturate(1.3)`;
  const pad = w * 0.08;
  // keep the enlarged background at the region's own aspect
  const rr = Math.max((w + 2 * pad) / small.width, (h + 2 * pad) / small.height);
  const dw = small.width * rr, dh = small.height * rr;
  ctx.drawImage(small, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
  ctx.restore();
  ctx.filter = "none";
}

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

export function drawLayout(ctx: CanvasRenderingContext2D, v: HTMLVideoElement, W: number, H: number, p: FrameParams) {
  const sw = v.videoWidth, sh = v.videoHeight;
  if (!sw || !sh) return;
  const { z, fx, fy } = p;
  const cw0 = (sh * 9) / 16;
  const cropX = (cw: number, center = false) => {
    const camLeft = (center || p.cam == null ? 0.5 : p.cam) * Math.max(0, sw - cw0);
    return clamp(camLeft + (cw0 - cw) / 2 + (fx * (sw - cw)) / 2, 0, Math.max(0, sw - cw));
  };
  let layout = p.layout;
  const crops = ["smart_crop", "center_crop", "split", "split_reverse", "two_speakers", "zoom45", "square"];
  if (sw / sh <= 0.62 && crops.includes(layout)) layout = "blur_fit";
  if (layout === "fit") {                                 // vertical source: fill
    const r = Math.max(W / sw, H / sh) * z;
    ctx.drawImage(v, (W - sw * r) / 2 * (1 + fx), (H - sh * r) / 2 * (1 + fy), sw * r, sh * r);
    return;
  }
  if (layout === "smart_crop" || layout === "center_crop") {
    const chz = sh / z, cwz = Math.min(sw, cw0 / z);
    const x = cropX(cwz, layout === "center_crop");
    const y = ((sh - chz) / 2) * (1 + fy);
    ctx.drawImage(v, x, y, cwz, chz, 0, 0, W, H);
    return;
  }
  const full = (y0: number, h: number) => {     // whole frame, full width, centred in a band
    const fh = (W * sh) / sw;
    ctx.drawImage(v, 0, y0 + (h - fh) / 2, W, fh);
  };
  if (layout === "split" || layout === "split_reverse") {
    const hh = H / 2;
    let cw = (sh * W) / hh;
    if (cw > sw) cw = sw;
    const x = cropX(cw);
    const faceY = layout === "split" ? 0 : hh, frameY = layout === "split" ? hh : 0;
    ctx.drawImage(v, x, 0, cw, sh, 0, faceY, W, hh);
    blurBg(ctx, v, 0, frameY, W, hh);
    ctx.save(); ctx.beginPath(); ctx.rect(0, frameY, W, hh); ctx.clip(); full(frameY, hh); ctx.restore();
    return;
  }
  if (layout === "two_speakers") {
    const hh = H / 2;
    let cw = sw / 2;
    let ch = Math.min(sh, (cw * hh) / W);
    cw = (ch * W) / hh;
    const y = Math.max(0, (sh - ch) * 0.35 * (1 + fy));
    const lx = clamp(sw / 4 - cw / 2 + (fx * sw) / 8, 0, sw - cw);
    const rx = clamp((3 * sw) / 4 - cw / 2 + (fx * sw) / 8, 0, sw - cw);
    ctx.drawImage(v, lx, y, cw, ch, 0, 0, W, hh);
    ctx.drawImage(v, rx, y, cw, ch, 0, hh, W, hh);
    ctx.fillStyle = "rgba(0,0,0,.9)";
    ctx.fillRect(0, hh - (3 * W) / 1080, W, (6 * W) / 1080);
    return;
  }
  if (layout === "zoom45" || layout === "square") {
    blurBg(ctx, v, 0, 0, W, H);
    const ow = W, oh = layout === "zoom45" ? (W * 5) / 4 : W;
    let ch = sh / z;
    const cw = Math.min(sw, (ch * ow) / oh);
    ch = (cw * oh) / ow;
    const y = ((sh - ch) / 2) * (1 + fy);
    const oy = (H - oh) / 2 - H * 0.03;
    ctx.drawImage(v, cropX(cw), y, cw, ch, 0, oy, ow, oh);
    return;
  }
  if (layout === "framed") {
    blurBg(ctx, v, 0, 0, W, H);
    let fw = W * 0.9 * Math.min(z, 1.1), fh = (fw * sh) / sw;
    if (fh > H * 0.7) { fh = H * 0.7; fw = (fh * sw) / sh; }
    const bd = (10 * W) / 1080;
    const ox = (W - fw - 2 * bd) / 2, oy = ((H - fh - 2 * bd) / 2) * (1 + fy * 0.8);
    ctx.fillStyle = "rgba(0,0,0,.45)";
    ctx.fillRect(ox + (14 * W) / 1080, oy + (22 * W) / 1080, fw + 2 * bd, fh + 2 * bd);
    ctx.fillStyle = "#fff";
    ctx.fillRect(ox, oy, fw + 2 * bd, fh + 2 * bd);
    ctx.drawImage(v, ox + bd, oy + bd, fw, fh);
    return;
  }
  // blur_fit / black_fit
  const fit = Math.min(W / sw, H / sh) * z;
  const fw = sw * fit, fh = sh * fit;
  if (layout === "black_fit") { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H); }
  else blurBg(ctx, v, 0, 0, W, H);
  const left = fw < W ? ((W - fw) / 2) * (1 + fx) : -((fw - W) / 2) * (1 + fx);
  const top = fh < H ? ((H - fh) / 2) * (1 + fy) : -(fh - H) / 2;
  ctx.drawImage(v, left, top, fw, fh);
}
