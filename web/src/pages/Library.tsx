import { useEffect, useMemo, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { go, setState, toast, useStore } from "../lib/store";
import type { LibItem } from "../lib/types";
import { Icon } from "../components/Icon";
import { Btn, Empty, Field, IconBtn, Modal, Tags, Text, timeAgo } from "../components/ui";
import { fmt } from "../lib/timeline";

export const PLAT_ICON: Record<string, string> = { youtube: "youtube", tiktok: "tiktok", facebook: "facebook", instagram: "instagram" };

export function LibraryPage() {
  const tick = useStore((s) => s.libraryTick);
  const [items, setItems] = useState<LibItem[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<LibItem | null>(null);
  const [post, setPost] = useState<string[] | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const load = () => get<LibItem[]>("/api/library").then(setItems).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); }, [tick]);
  const list = useMemo(() => (items || []).filter((i) => !q || (i.meta.title || i.hook_text || "").toLowerCase().includes(q.toLowerCase()) || i.source_title.toLowerCase().includes(q.toLowerCase())), [items, q]);
  if (!items) return <div className="page center"><span className="spin big" /></div>;
  return (
    <div className="page library">
      <div className="page-head">
        <div><h1>Library</h1><span className="muted">{items.length} finished Shorts</span></div>
        <div className="row gap">
          <div className="search"><Icon name="search" size={16} /><input value={q} placeholder="Search titles" onChange={(e) => setQ(e.target.value)} /></div>
          <Btn icon="folder" onClick={() => api("/api/open", { path: "output" })}>Open folder</Btn>
          {sel.size > 0 && <Btn kind="primary" icon="rocket" onClick={() => setPost([...sel])}>Post {sel.size}</Btn>}
        </div>
      </div>
      {!items.length ? (
        <Empty icon="film" title="Nothing exported yet" text="Exported Shorts land here, ready to post. Open a video's clips and press Export."><Btn kind="primary" icon="grid" onClick={() => go("projects")}>Go to clips</Btn></Empty>
      ) : (
        <div className="lib-grid scroll">
          {list.map((it) => (
            <div key={it.plan_file} className={`lib-card glass-2 depth-hover ${sel.has(it.plan_file) ? "sel" : ""}`}>
              <div className="lib-thumb" onClick={() => setOpen(it)}>
                {it.thumb ? <img src={mediaUrl(it.thumb, it.created)} alt="" loading="lazy" /> : <Icon name="film" />}
                <span className="dur">{fmt(it.duration)}</span>
                <button className={`lib-check ${sel.has(it.plan_file) ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); const n = new Set(sel); n.has(it.plan_file) ? n.delete(it.plan_file) : n.add(it.plan_file); setSel(n); }}><Icon name="check" size={13} /></button>
                <div className="hover-hint"><Icon name="play" size={22} /></div>
              </div>
              <div className="lib-body">
                <b title={it.meta.title}>{it.meta.title || it.hook_text}</b>
                <span className="muted small" title={it.source_title}>{it.source_title} · {timeAgo(it.created)}</span>
                <div className="row gap">
                  <Btn small kind="primary" icon="rocket" onClick={() => setPost([it.plan_file])}>Post</Btn>
                  {it.project_ref && <IconBtn icon="edit" title="Edit again" onClick={() => setState({ editing: { project: it.project_ref!.project, clip: it.project_ref!.clip } })} />}
                  <IconBtn icon="folder" title="Show file" onClick={() => api("/api/open", { path: it.output, select: true })} />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {open && <ShortModal it={open} onClose={() => setOpen(null)} onChanged={load} onPost={() => { setPost([open.plan_file]); setOpen(null); }} />}
      {post && <PostDialog plans={post} onClose={() => { setPost(null); setSel(new Set()); }} />}
    </div>
  );
}

function ShortModal({ it, onClose, onChanged, onPost }: { it: LibItem; onClose: () => void; onChanged: () => void; onPost: () => void }) {
  const [title, setTitle] = useState(it.meta.title || "");
  const [body, setBody] = useState(it.meta.body ?? it.meta.description ?? "");
  const [tags, setTags] = useState<string[]>(it.meta.tags || []);
  const [hash, setHash] = useState<string[]>(it.meta.hashtags || ["#shorts"]);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try { await api("/api/library/meta", { plan_file: it.plan_file, title, body, tags, hashtags: hash }); toast("Saved", "ok"); onChanged(); }
    catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  const del = async () => {
    if (!confirm("Delete this Short from your computer?")) return;
    await api("/api/library/delete", { plan_file: it.plan_file });
    onChanged(); onClose();
  };
  return (
    <Modal title={it.meta.title || "Short"} onClose={onClose} width={900} footer={<>
      <Btn kind="danger" icon="trash" onClick={del}>Delete</Btn><div className="grow" />
      <Btn icon="check" busy={busy} onClick={save}>Save details</Btn>
      <Btn kind="primary" icon="rocket" onClick={async () => { await save(); onPost(); }}>Post</Btn></>}>
      <div className="short-modal">
        <video src={mediaUrl(it.output, it.created)} controls autoPlay playsInline className="short-video" />
        <div className="short-meta">
          <Field label="Title"><Text value={title} onChange={setTitle} /><div className="count">{title.length}/100</div></Field>
          <Field label="Description"><textarea className="input" rows={6} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
          <Field label="Hashtags"><Tags value={hash} onChange={(v) => setHash(v.map((t) => (t.startsWith("#") ? t : "#" + t)))} /></Field>
          <Field label="Tags"><Tags value={tags} onChange={setTags} /></Field>
          {it.meta.credit && <p className="muted small">Music credit added automatically: {it.meta.credit}</p>}
        </div>
      </div>
    </Modal>
  );
}

export function PostDialog({ plans, onClose }: { plans: string[]; onClose: () => void }) {
  const accounts = useStore((s) => s.accounts);
  const cat = useStore((s) => s.catalog);
  const settings = useStore((s) => s.settings);
  const avail = Object.entries(accounts).filter(([, l]) => l.some((a) => a.enabled)).map(([p]) => p);
  const [plats, setPlats] = useState<string[]>(() => (settings.upload_platforms || []).filter((p: string) => avail.includes(p)).length
    ? (settings.upload_platforms || []).filter((p: string) => avail.includes(p)) : avail);
  const [busy, setBusy] = useState(false);
  const go_ = async () => {
    setBusy(true);
    let n = 0;
    try {
      for (const pf of plans) { const r = await api<any[]>("/api/library/upload", { plan_file: pf, platforms: plats }); n += r.length; }
      toast(n ? `Scheduled ${n} upload${n > 1 ? "s" : ""}. Watch progress on the Publish page.` : "Already queued or posted to those accounts.", n ? "ok" : "info",
        n ? { label: "Open Publish", run: () => go("publish", { editing: null }) } : undefined);
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal title={`Post ${plans.length > 1 ? plans.length + " Shorts" : "this Short"}`} onClose={onClose} width={520} footer={<>
      <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
      <Btn kind="primary" icon="rocket" busy={busy} disabled={!plats.length} onClick={go_}>Schedule</Btn></>}>
      {!avail.length ? (
        <div className="empty small"><p>No accounts connected yet.</p><Btn kind="primary" icon="user" onClick={() => { onClose(); go("publish", { editing: null }); }}>Connect accounts</Btn></div>
      ) : (
        <>
          <p className="muted">Uploads go out one by one with a random gap ({settings.upload_gap_min}–{settings.upload_gap_max} min) so they look natural. Quiet hours and daily limits apply (Publish page).</p>
          <div className="plat-pick">
            {avail.map((p) => (
              <button key={p} className={`plat ${plats.includes(p) ? "on" : ""}`} onClick={() => setPlats(plats.includes(p) ? plats.filter((x) => x !== p) : [...plats, p])}>
                <Icon name={PLAT_ICON[p]} size={20} /><b>{cat?.platforms[p]}</b>
                <small>{accounts[p].filter((a) => a.enabled).map((a) => a.name).join(", ")}</small>
                <span className="tick"><Icon name="check" size={13} /></span>
              </button>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}
