// Talks to the app's local engine (127.0.0.1). The per-launch token arrives in the URL hash (#t=...).

function readToken(): string {
  const m = /[#&]t=([^&]+)/.exec(location.hash);
  if (m) {
    const t = decodeURIComponent(m[1]);
    try { sessionStorage.setItem("rr_t", t); } catch { /* storage may be off */ }
    history.replaceState(null, "", location.pathname + location.search);
    return t;
  }
  try { return sessionStorage.getItem("rr_t") || ""; } catch { return ""; }
}

export const TOKEN = readToken();

export class ApiError extends Error {}

export async function api<T = any>(path: string, body?: any): Promise<T> {
  const init: RequestInit = { headers: { "X-RR-Token": TOKEN } };
  if (body !== undefined) {
    init.method = "POST";
    (init.headers as any)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let r: Response;
  try {
    r = await fetch(path, init);
  } catch {
    throw new ApiError("The app engine isn't responding. Is Rebels Revolt Shorts still running?");
  }
  let j: any = null;
  try { j = await r.json(); } catch { /* not json */ }
  if (!r.ok || !j || j.ok === false) throw new ApiError((j && j.error) || `Request failed (${r.status})`);
  return j.data as T;
}

export const get = <T = any>(path: string, q?: Record<string, any>) =>
  api<T>(q ? path + "?" + new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)])).toString() : path);

export function mediaUrl(path: string | undefined | null, bust?: string | number): string {
  if (!path) return "";
  return `/media?path=${encodeURIComponent(path)}&t=${encodeURIComponent(TOKEN)}${bust !== undefined ? `&v=${bust}` : ""}`;
}

export function stylePreviewUrl(kind: string, key: string): string {
  return `/api/style-preview?kind=${kind}&key=${encodeURIComponent(key)}&t=${encodeURIComponent(TOKEN)}`;
}

type Handler = (kind: string, data: any) => void;

export function connectEvents(onEvent: Handler, onState: (up: boolean) => void): () => void {
  let es: EventSource | null = null;
  let closed = false;
  const kinds = ["log", "job", "export", "project", "clip_camera", "library", "queue", "upload", "status",
    "settings", "accounts", "signin", "connect", "watch", "toast", "license", "expanding", "expanded",
    "job_removed", "jobs_cleared", "edit"];
  const open = () => {
    if (closed) return;
    es = new EventSource(`/api/events?t=${encodeURIComponent(TOKEN)}`);
    es.onopen = () => onState(true);
    es.onerror = () => { onState(false); };
    for (const k of kinds) es.addEventListener(k, (e: MessageEvent) => {
      try { onEvent(k, JSON.parse(e.data)); } catch { /* ignore */ }
    });
  };
  open();
  return () => { closed = true; es?.close(); };
}
