import { useEffect, useMemo, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { go, setState, toast, useStore } from "../lib/store";
import type { Clip, Project } from "../lib/types";
import { Icon } from "../components/Icon";
import { ClipPlayer } from "../components/ClipPlayer";
import { Btn, Chip, Empty, IconBtn, Progress, Score, timeAgo } from "../components/ui";
import { fmt, buildTimeline } from "../lib/timeline";
import { Steps } from "./Create";
import { PostDialog } from "./Library";

export function useProject(pid: string) {
  const tick = useStore((s) => s.projectTick[pid]);
  const cams = useStore((s) => s.cameras);
  const [p, setP] = useState<Project | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!pid) return;
    let live = true;
    get<Project>("/api/project", { id: pid }).then((x) => { if (live) { setP(x); setErr(""); } }).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [pid, tick]);
  const merged = useMemo(() => {
    if (!p) return p;
    return { ...p, clips: p.clips.map((c) => { const k = cams[p.id + "/" + c.id]; return k ? { ...c, camera: k.camera, framing: k.framing } : c; }) };
  }, [p, cams]);
  return { project: merged, setProject: setP, err };
}

export function ProjectsPage() {
  const projects = useStore((s) => s.projects);
  const open = useStore((s) => s.openProject);
  const pid = open && projects.some((p) => p.id === open) ? open : projects[0]?.id || "";
  if (!projects.length) {
    return <div className="page"><Empty icon="grid" title="No clips yet" text="Paste a link on the Create page. Every video you analyse shows up here with its best moments, scored and ready to preview.">
      <Btn kind="glow" icon="spark" onClick={() => go("create")}>Create Shorts</Btn></Empty></div>;
  }
  return (
    <div className="page projects">
      <aside className="proj-list glass depth">
        <div className="sec-head"><h2>Videos</h2><span className="muted">{projects.length}</span></div>
        <div className="scroll">
          {projects.map((p) => {
            const top = [...p.clips].sort((a, b) => b.score - a.score)[0];
            const exported = p.clips.filter((c) => c.status === "exported").length;
            return (
              <button key={p.id} className={`proj ${p.id === pid ? "on" : ""}`} onClick={() => setState({ openProject: p.id })}>
                <div className="proj-thumb">{top?.poster ? <img src={mediaUrl(top.poster)} alt="" /> : <Icon name="film" />}</div>
                <div className="proj-meta">
                  <b title={p.title}>{p.title}</b>
                  <span>{p.clips.length} clips · best {top?.score ?? "–"}{exported ? ` · ${exported} exported` : ""}</span>
                  <span className="muted">{timeAgo(p.created)}</span>
                </div>
              </button>
            );
          })}
        </div>
      </aside>
      {pid && <ProjectView key={pid} pid={pid} />}
    </div>
  );
}

function ProjectView({ pid }: { pid: string }) {
  const { project: p, err } = useProject(pid);
  const exports = useStore((s) => s.exports);
  const settings = useStore((s) => s.settings);
  const [post, setPost] = useState<string[] | null>(null);
  const [sort, setSort] = useState<"score" | "time">("score");
  if (err) return <div className="proj-main"><Empty icon="alert" title="Couldn't open this video" text={err} /></div>;
  if (!p) return <div className="proj-main center"><span className="spin big" /></div>;
  const clips = [...p.clips].sort((a, b) => sort === "score" ? b.score - a.score : a.clip.start - b.clip.start);
  const running = (c: Clip) => Object.values(exports).find((e) => e.project === p.id && e.clip === c.id && (e.state === "running" || e.state === "queued"));
  const exportedPlans = p.clips.flatMap((c) => c.exports.slice(-1).map((e) => e.plan_file));
  const del = async () => {
    if (!confirm("Remove this video and its clip previews? Exported Shorts in your Library stay.")) return;
    await api("/api/project/delete", { id: p.id });
    setState((s) => ({ projects: s.projects.filter((x) => x.id !== p.id), openProject: "" }));
  };
  return (
    <section className="proj-main">
      <div className="proj-head">
        <div>
          <Steps active={2} />
          <h1 className="proj-title" title={p.title}>{p.title}</h1>
          <div className="muted">{p.clips.length} clips · {fmt(p.duration)} video · {p.is_local ? "local file" : <a href={p.source} target="_blank" rel="noreferrer">source</a>}</div>
        </div>
        <div className="row gap">
          <div className="seg small"><button className={sort === "score" ? "on" : ""} onClick={() => setSort("score")}>Top score</button><button className={sort === "time" ? "on" : ""} onClick={() => setSort("time")}>Timeline</button></div>
          <IconBtn icon="trash" title="Remove video" onClick={del} />
          <Btn icon="download" onClick={() => api("/api/project/export", { project: p.id }).then(() => toast("Exporting all clips…", "ok")).catch((e) => toast(e.message, "error"))}>Export all</Btn>
          <Btn kind="primary" icon="rocket" disabled={!exportedPlans.length} onClick={() => setPost(exportedPlans)}>Post exported</Btn>
        </div>
      </div>
      <div className="clip-grid scroll">
        {clips.map((c) => <ClipCard key={c.id} p={p} c={c} running={running(c)} settings={settings} onPost={(pf) => setPost([pf])} />)}
      </div>
      {post && <PostDialog plans={post} onClose={() => setPost(null)} />}
    </section>
  );
}

function ClipCard({ p, c, running, settings, onPost }: { p: Project; c: Clip; running: any; settings: any; onPost: (pf: string) => void }) {
  const cat = useStore((s) => s.catalog);
  const [hover, setHover] = useState(false);
  const title = c.edits.hook || c.clip.title;
  const last = c.exports[c.exports.length - 1];
  const style = { ...c.style, ...(c.edits.style || {}), place: c.edits.place ?? c.style.place };
  const D = useMemo(() => buildTimeline(c, c.edits, settings.remove_pauses !== false).D, [c]);
  const edit = () => setState({ editing: { project: p.id, clip: c.id } });
  return (
    <div className="clip-card glass-2 depth-hover" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <div className="clip-media" onClick={edit}>
        {hover ? (
          <ClipPlayer clip={c} pid={p.id} edits={c.edits} style={style} catalog={cat} settings={settings} autoPlay muted
            camera={c.camera} framing={c.framing} srcWH={[p.info.width, p.info.height]} />
        ) : (
          c.poster ? <img className="poster" src={mediaUrl(c.poster)} alt="" /> : <div className="poster ph"><Icon name="film" /></div>
        )}
        <div className="clip-badges">
          <Score value={c.score} size={44} />
          <span className="dur">{fmt(D)}</span>
        </div>
        {!hover && <div className="hover-hint"><Icon name="play" size={22} /></div>}
        {running && <div className="clip-render"><Progress frac={running.state === "running" ? running.frac : 0.02} label={running.state === "running" ? "Exporting…" : "Queued"} tone="green" /></div>}
      </div>
      <div className="clip-body">
        <div className="clip-title" title={title}>{title}</div>
        <ul className="reasons">{c.reasons.slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}</ul>
        <div className="clip-bars">
          {(["hook", "emotion", "value", "flow"] as const).map((k) => (
            <div key={k} className="bar" title={`${k[0].toUpperCase() + k.slice(1)}: ${c.breakdown[k]}/99`}><span style={{ width: `${c.breakdown[k]}%` }} /><em>{({ hook: "Hook", emotion: "Feel", value: "Value", flow: "Flow" } as any)[k]}</em></div>
          ))}
        </div>
        <div className="clip-actions">
          <Btn small kind="primary" icon="edit" onClick={edit}>Edit</Btn>
          {last ? (
            <>
              <IconBtn icon="folder" title="Show file" onClick={() => api("/api/open", { path: last.path, select: true }).catch((e) => toast(e.message, "error"))} />
              <Btn small icon="rocket" onClick={() => onPost(last.plan_file)}>Post</Btn>
            </>
          ) : (
            <Btn small icon="download" busy={!!running} onClick={() => api("/api/clip/export", { project: p.id, clip: c.id }).catch((e) => toast(e.message, "error"))}>Export</Btn>
          )}
          {last && !running && <Chip tone="green" icon="check">Ready</Chip>}
          {c.status === "failed" && !running && <Chip tone="red" icon="alert">Failed</Chip>}
        </div>
      </div>
    </div>
  );
}
