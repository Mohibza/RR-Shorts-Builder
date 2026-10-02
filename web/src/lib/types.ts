export type Word = { w: string; s: number; e: number };

export type Place = {
  cap_y?: number; cap_scale?: number; hook_y?: number; hook_scale?: number; hook_dur?: number; cta_y?: number;
  wm_pos?: string; wm_scale?: number; frame_x?: number; frame_y?: number; frame_zoom?: number;
  // text dragged in the editor: centre x as a fraction of the width (captions, hook, end card) and the
  // story title / beat text group offset (fractions of the frame) + size
  cap_x?: number; hook_x?: number; cta_x?: number;
  title_dx?: number; title_dy?: number; title_scale?: number; beat_dx?: number; beat_dy?: number; beat_scale?: number;
};

export type Style = {
  caption_style: string; hook_style: string | null; cta_style: string | null; color_grade: string;
  motion: string; intro: string; layout: string; position: string; place: Place;
};

export type Edits = {
  trim?: [number, number];
  cut?: number[];
  fix?: Record<string, string>;
  hook?: string;
  style?: Partial<Style>;
  place?: Place;
  audio?: { music?: string; music_volume?: number | null; sfx_level?: string; sfx_pack?: string; music_mood?: string;
    seed?: number; music_offset?: number | null };
  story?: StoryEdits;
  zooms?: number[] | null;       // focus zoom moments (absolute source seconds); missing = automatic
  zoom_mult?: number;            // focus zoom strength (1 = the vibe's default)
  vibe?: string;                 // override the detected vibe
  meta?: { title?: string; description?: string; tags?: string[]; hashtags?: string[] };
};

export type ClipInfo = {
  start: number; end: number; score: number; text: string; hook: string; title: string;
  keywords: string[]; reasons: Record<string, any>; segments: [number, number][];
};

export type ExportRec = { path: string; thumb: string; plan_file: string; time: number; title: string };

export type Clip = {
  id: string; index: number; clip: ClipInfo; score: number;
  breakdown: { hook: number; emotion: number; value: number; flow: number };
  reasons: string[]; hooks: string[];
  seo: { title?: string; description?: string; tags?: string[]; hashtags?: string[] };
  media: { file: string; offset: number }; proxy?: { file: string; offset: number }; words?: Word[]; style: Style; poster: string;
  edits: Edits; exports: ExportRec[]; status: string; duration: number;
  camera?: [number, number][]; framing?: string; coverage?: number; vibe?: string; seed?: number;
};

export type Project = {
  id: string; source: string; title: string; is_local: boolean; created: number; updated?: number;
  duration: number; language: string; info: { width: number; height: number; fps: number; duration: number };
  out_dir: string; clips: Clip[]; status: string;
};

export type Job = {
  id: string; title: string; source: string; state: string; stage: string; frac: number; detail: string;
  error: string; login: boolean; project: string; created: number;
};

export type ExportTask = {
  id: string; project: string; clip: string; title: string; state: string; stage: string; frac: number;
  error: string; path: string; thumb: string; plan_file: string; created: number;
};

export type Account = { id: string; name: string; mode: string; enabled: boolean; profile: boolean; proxy?: string; proxy_host?: string };

export type QueueJob = {
  id: string; plan_file: string; video: string; title: string; platform: string; account_id: string;
  account: string; status: string; due: number; attempts: number; error: string; url: string;
  note?: string; done_at?: number; frac?: number; waits?: number; lane?: string; started?: number;
};

export type Status = {
  version: string; ffmpeg: boolean; fonts_missing: number; yt_login: boolean; running: number; exporting: number;
  uploads_waiting: number; uploads_active?: number; uploads_paused?: boolean; watching: number; auto_upload: boolean; ai: string; license: boolean;
  trial?: { status: string; left: number | null; plan: string | null; expires_at: number | null } | null;
};

export type CapStyle = {
  name: string; font: string; size: number; upper: boolean; chunk: number; maxchars: number; primary: string;
  outline: string; bord: number; shadow: number; active?: string; emph?: string; secondary?: string; mode: string;
  pop?: boolean; bounce?: boolean; box?: boolean; box_alpha?: number; glow?: string; hl_box?: string; tilt?: boolean;
  palette?: string[]; fade?: boolean; slide?: boolean;
};
export type HookStyle = {
  name: string; font: string; size: number; color: string; box?: string; box_alpha?: number; outline?: string;
  bord?: number; upper: boolean; dur: number; y: number; anim: string; glow?: string; tilt?: number;
};
export type CtaStyle = { name: string; font: string; size: number; color: string; box?: string; outline?: string; bord?: number; anim: string };

export type Catalog = {
  captions: Record<string, CapStyle>; hooks: Record<string, HookStyle>; ctas: Record<string, CtaStyle>;
  grades: Record<string, { name: string }>; motions: Record<string, string>; intros: Record<string, string>;
  layouts: Record<string, string>; wm_positions: Record<string, string>; fonts: Record<string, string>;
  music_sources: Record<string, { name: string; note: string }>; platforms: Record<string, string>;
  packs: Record<string, { name: string; desc: string; set: Record<string, string> }>;
  vibes?: Record<string, { name: string; music: string[]; pack: string }>;
  sfx_packs?: Record<string, string>;
};

export type LibItem = {
  plan_file: string; output: string; thumb: string; source: string; source_title: string; created: number;
  duration: number; project_ref?: { project: string; clip: string } | null; hook_text: string;
  meta: { title?: string; body?: string; description?: string; tags?: string[]; hashtags?: string[]; credit?: string };
  style: Partial<Style>;
  cover?: string;
  cover_info?: { mode?: string; prompt?: string; ref?: string; aspect?: string; title?: string; provider?: string; use_frame?: boolean; time?: number };
};

export type Settings = Record<string, any>;

export type StoryEdits = { level?: string; pauses?: boolean; titles?: boolean; transitions?: boolean; textures?: boolean; behind?: boolean };
