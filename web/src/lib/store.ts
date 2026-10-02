import { useSyncExternalStore } from "react";
import { api, connectEvents } from "./api";
import type { Account, Catalog, ExportTask, Job, Project, QueueJob, Settings, Status } from "./types";

export type Toast = { id: number; kind: "ok" | "error" | "info"; text: string; action?: { label: string; run: () => void } };

export type State = {
  ready: boolean; online: boolean; error: string;
  status: Status | null; settings: Settings; catalog: Catalog | null;
  jobs: Record<string, Job>; exports: Record<string, ExportTask>;
  projects: Project[]; accounts: Record<string, Account[]>; queue: QueueJob[];
  logs: { t: number; msg: string }[]; toasts: Toast[];
  signins: Record<string, any>; connect: Record<string, any>;
  uploads: Record<string, number>; libraryTick: number; projectTick: Record<string, number>;
  cameras: Record<string, { camera: [number, number][]; framing?: string }>;
  page: string; settingsSec?: string; openProject: string; editing: { project: string; clip: string } | null;
  editProject: string; editName: string; editDirty: boolean;
};

let state: State = {
  ready: false, online: false, error: "", status: null, settings: {}, catalog: null, jobs: {}, exports: {},
  projects: [], accounts: {}, queue: [], logs: [], toasts: [], signins: {}, connect: {}, uploads: {},
  libraryTick: 0, projectTick: {}, cameras: {}, page: "create", openProject: "", editing: null, editProject: "", editName: "", editDirty: false,
};
const subs = new Set<() => void>();

export function getState() { return state; }
export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...p };
  subs.forEach((f) => f());
}
export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => sel(state));
}

let toastId = 1;
export function toast(text: string, kind: Toast["kind"] = "info", action?: Toast["action"]) {
  const id = toastId++;
  setState((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text, action }] }));
  setTimeout(() => setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), kind === "error" ? 9000 : 4500);
}

export function go(page: string, extra: Partial<State> = {}) { setState({ page, ...extra }); }

export async function refreshAll() {
  const d = await api<any>("/api/state");
  const jobs: Record<string, Job> = {};
  d.jobs.forEach((j: Job) => (jobs[j.id] = j));
  const exps: Record<string, ExportTask> = {};
  d.exports.forEach((e: ExportTask) => (exps[e.id] = e));
  setState({ ready: true, status: d.status, settings: d.settings, catalog: d.catalog, jobs, exports: exps,
    projects: d.projects, accounts: d.accounts, queue: d.queue, logs: d.logs });
}

export async function refreshQueue() {
  const q = await api<QueueJob[]>("/api/queue");
  setState({ queue: q });
}
export async function refreshAccounts() {
  setState({ accounts: await api("/api/accounts") });
}

export async function saveSettings(patch: Settings) {
  const s = await api<Settings>("/api/settings", patch);
  setState({ settings: s });
  return s;
}

let queueTimer: any = null;
function upsertProject(p: Project) {
  setState((s) => {
    const list = s.projects.filter((x) => x.id !== p.id);
    list.unshift(p);
    list.sort((a, b) => b.created - a.created);
    return { projects: list, projectTick: { ...s.projectTick, [p.id]: Date.now() } };
  });
}

export function startEvents() {
  return connectEvents((kind, data) => {
    switch (kind) {
      case "log":
        setState((s) => ({ logs: [...s.logs.slice(-399), data] }));
        break;
      case "job":
        setState((s) => ({ jobs: { ...s.jobs, [data.id]: data } }));
        if (data.state === "done" && data.project) {
          const s = getState();
          if (s.page === "create" && !s.editing) go("projects", { openProject: data.project });
          toast(`Clips ready: ${data.title}`, "ok");
        } else if (data.state === "failed") {
          toast(`${data.title}: ${data.error}`, "error");
        }
        break;
      case "job_removed":
        setState((s) => { const j = { ...s.jobs }; delete j[data.id]; return { jobs: j }; });
        break;
      case "jobs_cleared":
        refreshAll();
        break;
      case "export":
        setState((s) => ({ exports: { ...s.exports, [data.id]: data } }));
        if (data.state === "failed") toast(`Export failed: ${data.error}`, "error");
        break;
      case "project":
        upsertProject(data);
        break;
      case "clip_camera":
        setState((s) => ({ cameras: { ...s.cameras, [data.project + "/" + data.clip]: data } }));
        break;
      case "library":
        setState((s) => ({ libraryTick: s.libraryTick + 1 }));
        break;
      case "queue":
        clearTimeout(queueTimer);
        queueTimer = setTimeout(() => refreshQueue().catch(() => {}), 150);
        break;
      case "upload":
        setState((s) => ({ uploads: { ...s.uploads, [data.id]: data.frac ?? 0 } }));
        break;
      case "status":
        setState({ status: data });
        break;
      case "settings":
        setState({ settings: data });
        break;
      case "accounts":
        refreshAccounts().catch(() => {});
        break;
      case "signin":
        setState((s) => ({ signins: { ...s.signins, [data.id]: data } }));
        break;
      case "connect":
        setState((s) => ({ connect: { ...s.connect, [data.platform]: data } }));
        break;
      case "toast":
        toast(data.text, data.kind || "info", data.login ? { label: "Sign in to YouTube", run: () => go("settings", {}) } : undefined);
        break;
      case "license":
        toast(data.text, "error", { label: "Enter key", run: () => go("settings", { settingsSec: "license" }) });
        break;
      case "edit":            // video editor: previews ready, export progress
        window.dispatchEvent(new CustomEvent("rr-edit", { detail: data }));
        break;
      case "watch":
        if (data.found) toast(`Auto-watch found ${data.found} new video(s)`, "ok");
        break;
    }
  }, (up) => {
    const was = state.online;
    setState({ online: up });
    if (up && !was && state.ready) refreshAll().catch(() => {});
  });
}
