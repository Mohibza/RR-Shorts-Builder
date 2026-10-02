import { useEffect, useMemo } from "react";
import { TOKEN } from "./lib/api";
import { go, refreshAll, setState, startEvents, useStore } from "./lib/store";
import { Icon } from "./components/Icon";
import { setFontMetrics } from "./components/ClipPlayer";
import { Btn } from "./components/ui";
import { CreatePage } from "./pages/Create";
import { ProjectsPage } from "./pages/Projects";
import { Editor } from "./pages/Editor";
import { LibraryPage } from "./pages/Library";
import { PublishPage } from "./pages/Publish";
import { MusicPage } from "./pages/Music";
import { SettingsPage } from "./pages/Settings";
import { StudioPage } from "./pages/Studio";
import { VideoEditorPage } from "./pages/VideoEditor";
import { MenuBar } from "./components/MenuBar";
import { get } from "./lib/api";

const NAV: [string, string, string][] = [
  ["create", "Create", "spark"],
  ["studio", "Studio", "record"],
  ["edit", "Editor", "timeline"],
  ["projects", "Clips", "grid"],
  ["library", "Library", "film"],
  ["publish", "Publish", "rocket"],
  ["music", "Music", "music"],
  ["settings", "Settings", "settings"],
];

function FontFaces() {
  const fonts = useStore((s) => s.catalog?.fonts);
  const metrics = useStore((s) => (s.catalog as any)?.metrics);
  setFontMetrics(metrics);
  const css = useMemo(() => Object.entries(fonts || {}).map(([fam, file]) =>
    `@font-face{font-family:"${fam}";src:url("/fonts/${encodeURIComponent(file)}") format("truetype");font-display:swap}`).join("\n"), [fonts]);
  return <style>{css}</style>;
}

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind} glass`}>
          <Icon name={t.kind === "ok" ? "check" : t.kind === "error" ? "alert" : "info"} size={16} />
          <span>{t.text}</span>
          {t.action && <button className="linkbtn" onClick={t.action.run}>{t.action.label}</button>}
          <button className="toast-x" onClick={() => setState((s) => ({ toasts: s.toasts.filter((x) => x.id !== t.id) }))}>×</button>
        </div>
      ))}
    </div>
  );
}

function StatusPill() {
  const st = useStore((s) => s.status);
  const online = useStore((s) => s.online);
  if (!st) return null;
  const busy = st.running + st.exporting;
  return (
    <div className="side-status glass-2">
      <div className="ss-row"><span className={`dot ${online ? "ok" : "bad"}`} />{online ? (busy ? `${busy} task${busy > 1 ? "s" : ""} running` : "Ready") : "Reconnecting…"}</div>
      <div className="ss-row sub">
        <span className={st.ffmpeg ? "ok" : "bad"}>FFmpeg</span>·
        <span className={st.yt_login ? "ok" : "warn"}>YouTube</span>·
        <span className={st.ai !== "offline" ? "ok" : "muted"}>{st.ai === "offline" ? "Offline AI" : st.ai === "gemini" ? "Gemini" : st.ai === "openai" ? "ChatGPT" : "Claude"}</span>
      </div>
      {st.trial && (
        <button className={`ss-row sub trial ${st.trial.status === "active" ? "ok" : (st.trial.left ?? 0) > 0 ? "" : "bad"}`} onClick={() => go("settings", { settingsSec: "license" })}>
          <Icon name="key" size={12} /> {st.trial.status === "active" ? `${st.trial.plan} plan${st.trial.expires_at ? ` · until ${new Date(st.trial.expires_at * 1000).toLocaleDateString()}` : ""}`
            : (st.trial.left ?? 0) > 0 ? `Free trial: ${st.trial.left} Short${st.trial.left === 1 ? "" : "s"} left` : "Trial over · enter your key"}
        </button>
      )}
      {(st.watching > 0 || st.uploads_waiting > 0) && (
        <div className="ss-row sub auto"><Icon name="zap" size={12} /> Autopilot: {[st.watching ? `${st.watching} channel${st.watching > 1 ? "s" : ""}` : "", st.uploads_waiting ? `${st.uploads_waiting} upload${st.uploads_waiting > 1 ? "s" : ""} queued` : ""].filter(Boolean).join(", ")}</div>
      )}
    </div>
  );
}

export default function App() {
  const page = useStore((s) => s.page);
  const ready = useStore((s) => s.ready);
  const error = useStore((s) => s.error);
  const editing = useStore((s) => s.editing);
  const version = useStore((s) => s.status?.version);
  const compact = useStore((s) => s.page === "edit" && !!s.editProject && !s.editing);      // editor workspace: the sidebar folds to icons
  const running = useStore((s) => Object.values(s.jobs).filter((j) => j.state === "running" || j.state === "queued").length
    + Object.values(s.exports).filter((e) => e.state === "running" || e.state === "queued").length);

  useEffect(() => {
    if (!TOKEN) { setState({ error: "Open the app from its desktop shortcut (this page needs the app's launch key)." }); return; }
    refreshAll().catch((e) => setState({ error: String(e.message || e) }));
    // back from a screen recording: open Studio on the new recording
    get<{ sessions: { seen: boolean; state: string }[]; active: unknown }>("/api/record/sessions")
      .then((r) => { if (r.active || r.sessions.some((x) => !x.seen)) go("studio", { editing: null }); }).catch(() => {});
    return startEvents();
  }, []);

  if (error && !ready) {
    return <div className="boot"><div className="boot-card glass"><Icon name="alert" size={28} /><h2>Can't reach the engine</h2><p>{error}</p>
      <Btn kind="primary" icon="refresh" onClick={() => location.reload()}>Try again</Btn></div></div>;
  }
  if (!ready) return <div className="boot"><div className="boot-logo"><Logo big /></div><span className="spin big" /></div>;

  return (
    <div className="shell">
      <MenuBar />
      <div className={`app ${compact ? "compact" : ""}`}>
      <FontFaces />
      <aside className="side">
        <Logo version={version} />
        <nav>
          {NAV.map(([k, label, ic]) => (
            <button key={k} title={label} className={`nav ${page === k && !editing ? "on" : ""}`} onClick={() => go(k, { editing: null })}>
              <Icon name={ic} size={19} /><span>{label}</span>
              {k === "create" && running > 0 && <em className="badge">{running}</em>}
            </button>
          ))}
        </nav>
        <div className="grow" />
        <StatusPill />
      </aside>
      <main className="main">
        {editing ? <Editor key={editing.project + editing.clip} pid={editing.project} cid={editing.clip} /> :
          page === "create" ? <CreatePage /> :
          page === "studio" ? <StudioPage /> :
          page === "edit" ? <VideoEditorPage /> :
          page === "projects" ? <ProjectsPage /> :
          page === "library" ? <LibraryPage /> :
          page === "publish" ? <PublishPage /> :
          page === "music" ? <MusicPage /> : <SettingsPage />}
      </main>
      <Toasts />
      </div>
    </div>
  );
}

export function Logo({ big, version }: { big?: boolean; version?: string }) {
  return (
    <div className={`logo ${big ? "big" : ""}`}>
      <div className="logo-mark"><Icon name="play" size={big ? 30 : 18} /></div>
      <div className="logo-txt"><b>Rebels Revolt</b><span>SHORTS{version ? ` · v${version}` : ""}</span></div>
    </div>
  );
}
