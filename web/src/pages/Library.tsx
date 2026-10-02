import { useEffect, useMemo, useRef, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { go, setState, toast, useStore } from "../lib/store";
import type { LibItem } from "../lib/types";
import { Icon } from "../components/Icon";
import { Btn, Empty, Field, IconBtn, Modal, Select, Tags, Text, Toggle, timeAgo } from "../components/ui";
import { fmt } from "../lib/timeline";
import { ThumbDesigner } from "../components/ThumbDesigner";

export const PLAT_ICON: Record<string, string> = { youtube: "youtube", tiktok: "tiktok", facebook: "facebook", instagram: "instagram" };

export function LibraryPage() {
  const tick = useStore((s) => s.libraryTick);
  const [items, setItems] = useState<LibItem[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<LibItem | null>(null);
  const [post, setPost] = useState<string[] | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [kind, setKind] = useState("all");
  const lastPick = useRef("");
  const load = () => get<LibItem[]>("/api/library").then(setItems).catch((e) => toast(e.message, "error"));
  const [delBusy, setDelBusy] = useState(false);
  const delMany = async (plans: string[]) => {
    if (!plans.length) return;
    if (!confirm(plans.length === 1 ? "Delete this Short from your computer? Its waiting posts are removed too." : `Delete ${plans.length} Shorts from your computer? Their waiting posts are removed too.`)) return;
    setDelBusy(true);
    try {
      const r = await api<{ deleted: number; posting: number; failed: number }>("/api/library/delete_many", { plan_files: plans });
      toast(`Deleted ${r.deleted} Short${r.deleted === 1 ? "" : "s"}` + (r.posting ? ` · ${r.posting} kept (posting right now)` : "") + (r.failed ? ` · ${r.failed} couldn't be deleted` : ""), r.posting || r.failed ? "info" : "ok");
      setSel(new Set());
      load();
    } catch (e: any) { toast(e.message, "error"); }
    setDelBusy(false);
  };
  useEffect(() => { load(); }, [tick]);
  const list = useMemo(() => (items || []).filter((i) => (kind === "all" || (i.kind || "short") === kind) && (!q || (i.meta.title || i.hook_text || "").toLowerCase().includes(q.toLowerCase()) || i.source_title.toLowerCase().includes(q.toLowerCase()))), [items, q, kind]);
  // tick one, Shift+click another: everything between is selected too
  const pick = (id: string, range: boolean) => {
    const n = new Set(sel), ids = list.map((i) => i.plan_file), a = ids.indexOf(lastPick.current), b = ids.indexOf(id);
    if (range && a >= 0 && b >= 0) for (let i = Math.min(a, b); i <= Math.max(a, b); i++) n.add(ids[i]);
    else if (n.has(id)) n.delete(id); else n.add(id);
    lastPick.current = id; setSel(n);
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const tg = e.target as HTMLElement;
      if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA") || document.querySelector(".modal-back")) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); setSel(new Set(list.map((i) => i.plan_file))); }
      else if (e.key === "Escape") setSel(new Set());
      else if (e.key === "Delete" && sel.size) delMany([...sel]);
    };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, [list, sel]);
  if (!items) return <div className="page center"><span className="spin big" /></div>;
  return (
    <div className="page library">
      <div className="page-head">
        <div><h1>Library</h1><span className="muted">{items.filter((i) => i.kind !== "video").length} finished Shorts{items.some((i) => i.kind === "video") ? ` · ${items.filter((i) => i.kind === "video").length} edited videos` : ""}{sel.size ? ` · ${sel.size} selected` : ""}</span></div>
        <div className="row gap">
          <div className="search"><Icon name="search" size={16} /><input value={q} placeholder="Search titles" onChange={(e) => setQ(e.target.value)} /></div>
          {items.some((i) => i.kind === "video") && <div className="seg">{([["all", "All"], ["short", "Shorts"], ["video", "Videos"]] as [string, string][]).map(([k, l]) => <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{l}</button>)}</div>}
          <Btn icon="folder" onClick={() => api("/api/open", { path: "output" })}>Open folder</Btn>
          {items.length > 0 && <div className="lib-sel-bar">
            {sel.size < list.length ? <Btn small kind="ghost" icon="check" onClick={() => setSel(new Set(list.map((i) => i.plan_file)))}>Select all{q ? " shown" : ""}</Btn>
              : <Btn small kind="ghost" icon="x" onClick={() => setSel(new Set())}>Clear selection</Btn>}
            {sel.size > 0 && <Btn small kind="danger" icon="trash" busy={delBusy} onClick={() => delMany([...sel])}>Delete {sel.size}</Btn>}
          </div>}
          {sel.size > 0 && <Btn kind="primary" icon="rocket" onClick={() => setPost([...sel])}>Post {sel.size}</Btn>}
        </div>
      </div>
      {!items.length ? (
        <Empty icon="film" title="Nothing exported yet" text="Exported Shorts and videos from the Editor land here, ready to post. Open a video's clips and press Export."><Btn kind="primary" icon="grid" onClick={() => go("projects")}>Go to clips</Btn></Empty>
      ) : (
        <div className="lib-grid scroll">
          {list.map((it) => (
            <div key={it.plan_file} className={`lib-card glass-2 depth-hover ${sel.has(it.plan_file) ? "sel" : ""}`}>
              <div className={`lib-thumb ${it.kind === "video" ? "wide" : ""}`} onClick={(e) => { if (e.ctrlKey || e.shiftKey || sel.size) pick(it.plan_file, e.shiftKey); else setOpen(it); }}>
                {it.thumb ? <img src={mediaUrl(it.thumb, it.created)} alt="" loading="lazy" /> : <Icon name="film" />}
                <span className="dur">{fmt(it.duration)}</span>
                {it.cover && <span className="cover-badge" title="Has a custom thumbnail"><Icon name="image" size={12} /></span>}
                <button className={`lib-check ${sel.has(it.plan_file) ? "on" : ""}`} title="Select (Shift+click selects everything in between)" onClick={(e) => { e.stopPropagation(); pick(it.plan_file, e.shiftKey); }}><Icon name="check" size={13} /></button>
                {it.kind === "video" && <span className="lib-kind">Video{it.height ? ` · ${it.height}p` : ""}</span>}
                <div className="hover-hint"><Icon name="play" size={22} /></div>
              </div>
              <div className="lib-body">
                <b title={it.meta.title}>{it.meta.title || it.hook_text}</b>
                <span className="muted small" title={it.source_title}>{it.source_title} · {timeAgo(it.created)}</span>
                <div className="row gap">
                  <Btn small kind="primary" icon="rocket" onClick={() => setPost([it.plan_file])}>Post</Btn>
                  {it.project_ref && <IconBtn icon="edit" title="Edit again" onClick={() => setState({ editing: { project: it.project_ref!.project, clip: it.project_ref!.clip } })} />}
                  {it.kind === "video" && it.edit_project && <IconBtn icon="edit" title="Edit again (opens its project)" onClick={() => setState({ page: "edit", editProject: it.edit_project! })} />}
                  <IconBtn icon="timeline" title="Open in the video editor as a new project" onClick={() => api<{ id: string }>("/api/edit/new", { paths: [it.output] }).then((q) => setState({ page: "edit", editProject: q.id })).catch((e) => toast(e.message, "error"))} />
                  <IconBtn icon="folder" title="Show file" onClick={() => api("/api/open", { path: it.output, select: true })} />
                  <IconBtn icon="trash" danger title="Delete this Short" onClick={() => delMany([it.plan_file])} />
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
    if (!confirm("Delete this Short from your computer? Its waiting posts are removed too.")) return;
    try { await api("/api/library/delete", { plan_file: it.plan_file }); onChanged(); onClose(); }
    catch (e: any) { toast(e.message, "error"); }
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
          <ThumbMaker it={it} title={title} onChanged={onChanged} />
        </div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ optional thumbnail: prompt + reference -> AI
const ASPECTS: [string, string][] = [["9:16", "9:16 · Shorts / TikTok / Reels cover"], ["16:9", "16:9 · YouTube thumbnail"], ["4:5", "4:5 · Instagram"], ["1:1", "1:1 · Square"]];

function ThumbMaker({ it, title, onChanged }: { it: LibItem; title: string; onChanged: () => void }) {
  const ci = it.cover_info || {};
  const [open, setOpen] = useState(!!it.cover);
  const [cover, setCover] = useState(it.cover || "");
  const [ver, setVer] = useState(ci.time || 0);
  const [prompt, setPrompt] = useState(ci.prompt || "");
  const [ref, setRef] = useState(ci.ref || "");
  const [head, setHead] = useState(ci.title ?? (it.hook_text || title || ""));
  const [aspect, setAspect] = useState(ci.aspect || "9:16");
  const [useFrame, setUseFrame] = useState(ci.use_frame !== false);
  const [busy, setBusy] = useState<"" | "ai" | "frame">("");
  const [provs, setProvs] = useState<string[] | null>(null);
  const [design, setDesign] = useState(false);
  useEffect(() => { if (open && provs === null) get<{ providers: string[] }>("/api/thumb/info").then((r) => setProvs(r.providers)).catch(() => setProvs([])); }, [open]);
  const make = async (mode: "ai" | "frame") => {
    setBusy(mode);
    try {
      const r = await api<{ cover: string; provider: string; cover_info: { time: number } }>("/api/thumb/make",
        { plan_file: it.plan_file, mode, prompt, ref, title: head, aspect, use_frame: useFrame });
      setCover(r.cover); setVer(r.cover_info.time);
      toast(mode === "ai" ? `Thumbnail made with ${r.provider === "gemini" ? "Gemini" : "OpenAI"}` : "Thumbnail made from the video frame", "ok");
      onChanged();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy("");
  };
  const pickRef = async () => {
    try { const r = await api<{ path: string }>("/api/thumb/ref", {}); if (r.path) setRef(r.path); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const remove = async () => {
    try { await api("/api/thumb/remove", { plan_file: it.plan_file }); setCover(""); onChanged(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const noKey = provs !== null && provs.length === 0;
  if (!open) return (
    <button className="thumb-open" onClick={() => setOpen(true)}><Icon name="image" size={15} /> Add a thumbnail <span className="muted">(optional)</span></button>
  );
  return (
    <div className="thumb-maker">
      <div className="sec-head"><h3><Icon name="image" size={15} /> Thumbnail <span className="muted small">optional</span></h3>
        {!cover && <button className="linkbtn small" onClick={() => setOpen(false)}>Hide</button>}</div>
      <div className="thumb-row">
        <div className={`thumb-prev a-${aspect.replace(":", "x")}`}>
          {busy ? <div className="thumb-busy"><span className="spin" /><small>{busy === "ai" ? "The AI is drawing… (10–60 s)" : "Making…"}</small></div>
            : cover ? <img src={mediaUrl(cover, ver)} alt="Thumbnail" /> : <div className="thumb-empty"><Icon name="image" size={22} /><small>No thumbnail yet</small></div>}
        </div>
        <div className="thumb-form">
          <Field label="Describe the thumbnail"><textarea className="input" rows={3} value={prompt} placeholder="e.g. dramatic close-up, shocked face, dark background, big yellow text, red arrow pointing at the phone" onChange={(e) => setPrompt(e.target.value)} /></Field>
          <Field label="Reference to copy the style from" hint="The AI replicates this image's layout, colours and text style, with your Short's person and your headline. Logos and people in the reference are not copied.">
            <div className="row gap">
              {ref ? <div className="ref-chip"><img src={mediaUrl(ref)} alt="" /><span>Reference added</span><button className="x" title="Remove reference" onClick={() => setRef("")}><Icon name="x" size={12} /></button></div>
                : <Btn small icon="plus" onClick={pickRef}>Add reference image</Btn>}
            </div></Field>
          <Field label="Headline on the thumbnail"><Text value={head} onChange={setHead} placeholder="Empty = no text" /></Field>
          <div className="two">
            <Field label="Size"><Select value={aspect} options={ASPECTS} onChange={(v) => setAspect(v)} /></Field>
            <Toggle on={useFrame} onChange={setUseFrame} label="Use the person from my Short" hint="Sends a clean frame of this Short so the thumbnail shows your real speaker" />
          </div>
        </div>
      </div>
      {noKey && <p className="muted small">The designer is free and needs no key. “Generate with AI” needs a Gemini or OpenAI key (Settings → AI).</p>}
      <div className="row gap wrap">
        <Btn kind="primary" icon="wand" disabled={!!busy} onClick={() => setDesign(true)}>{ci.mode === "design" && cover ? "Edit in designer" : "Open designer (free)"}</Btn>
        <Btn icon="spark" busy={busy === "ai"} disabled={!!busy || noKey || (!prompt.trim() && !ref)} onClick={() => make("ai")}>Generate with AI</Btn>
        <Btn icon="frame" busy={busy === "frame"} disabled={!!busy} onClick={() => make("frame")}>Quick: frame + title</Btn>
        {cover && <Btn kind="ghost" icon="folder" onClick={() => api("/api/open", { path: cover, select: true })}>Show file</Btn>}
        {cover && <Btn kind="ghost" icon="trash" disabled={!!busy} onClick={remove}>Remove</Btn>}
      </div>
      {design && <ThumbDesigner planFile={it.plan_file} initialAspect={aspect} initialRef={ref} onClose={() => setDesign(false)}
        onSaved={(c, t) => { setCover(c); setVer(t); onChanged(); }} />}
    </div>
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
  const [direct, setDirect] = useState<boolean>(!settings.auto_upload);
  const go_ = async () => {
    setBusy(true);
    let n = 0;
    try {
      for (const pf of plans) { const r = await api<any[]>("/api/library/upload", { plan_file: pf, platforms: plats, direct }); n += r.length; }
      toast(n ? (direct ? `Posting ${n} upload${n > 1 ? "s" : ""} now. Watch progress on the Publish page.` : `Scheduled ${n} upload${n > 1 ? "s" : ""}. Watch progress on the Publish page.`) : "Already queued or posted to those accounts.", n ? "ok" : "info",
        n ? { label: "Open Publish", run: () => go("publish", { editing: null }) } : undefined);
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal title={`Post ${plans.length > 1 ? plans.length + " Shorts" : "this Short"}`} onClose={onClose} width={520} footer={<>
      <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
      <Btn kind="primary" icon="rocket" busy={busy} disabled={!plats.length} onClick={go_}>{direct ? "Post now" : "Schedule"}</Btn></>}>
      {!avail.length ? (
        <div className="empty small"><p>No accounts connected yet.</p><Btn kind="primary" icon="user" onClick={() => { onClose(); go("publish", { editing: null }); }}>Connect accounts</Btn></div>
      ) : (
        <>
          <div className="post-mode">
            <button className={direct ? "on" : ""} onClick={() => setDirect(true)}><b>Post right away</b><small>Starts now. Each account posts one at a time.</small></button>
            <button className={!direct ? "on" : ""} onClick={() => setDirect(false)}><b>Spread out naturally</b><small>Random {settings.upload_gap_min}–{settings.upload_gap_max} min gaps, quiet hours and daily limits.</small></button>
          </div>
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
