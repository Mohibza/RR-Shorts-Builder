import { useEffect, useRef, useState } from "react";
import { api, get, mediaUrl } from "../lib/api";
import { saveSettings, toast, useStore } from "../lib/store";
import { Icon } from "../components/Icon";
import { Btn, Chip, Field, IconBtn, Select, Seg, Slider, Text, Toggle } from "../components/ui";

type Local = { name: string; path: string; title: string; artist: string; license: string; source: string; credit: string; starred: boolean;
  trending?: boolean; vibes?: string[]; bpm?: number };
const TREND_QUERIES = ["phonk", "trap beat", "epic cinematic", "sad piano", "funny quirky", "lofi chill", "motivational", "dark suspense"];

export function MusicPage() {
  const s = useStore((x) => x.settings);
  const cat = useStore((x) => x.catalog);
  const [lib, setLib] = useState<{ folder: string; trending_folder?: string; tracks: Local[] } | null>(null);
  const [q, setQ] = useState("");
  const [src, setSrc] = useState("all");
  const [failed, setFailed] = useState<string[]>([]);
  const [moods, setMoods] = useState<Record<string, string>>({});
  const [mood, setMood] = useState("lofi");
  const [secs, setSecs] = useState(90);
  const [genBusy, setGenBusy] = useState(false);
  const [res, setRes] = useState<any[] | null>(null);
  const [more, setMore] = useState(false);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [dl, setDl] = useState<Record<string, string>>({});
  const [play, setPlay] = useState("");
  const audio = useRef<HTMLAudioElement>(null);
  const load = () => get("/api/music").then(setLib).catch((e) => toast(e.message, "error"));
  useEffect(() => { load(); get<Record<string, string>>("/api/music/moods").then(setMoods).catch(() => {}); const id = setInterval(load, 8000); return () => clearInterval(id); }, []);
  const generate = async (star: boolean) => {
    setGenBusy(true);
    try { const r = await api<any>("/api/music/generate", { mood, seconds: secs, star }); load(); toast(`Made “${r.name.replace(/\.m4a$/, "")}”. It's yours, no copyright.`, "ok"); }
    catch (e: any) { toast(e.message, "error"); }
    setGenBusy(false);
  };
  useEffect(() => { const a = audio.current; if (!a) return; if (play) { a.src = play; a.play().catch(() => {}); } else a.pause(); }, [play]);
  const set = (patch: Record<string, any>) => saveSettings(patch).catch((e) => toast(e.message, "error"));

  const search = async (pg = 1, qq = q) => {
    setBusy(true);
    try {
      const r = await api<any>("/api/music/search", { source: src, query: qq, page: pg });
      setRes(pg === 1 ? r.tracks : [...(res || []), ...r.tracks]); setMore(r.more); setPage(pg); setFailed(r.failed || []);
    } catch (e: any) { toast(e.message, "error"); setRes([]); }
    setBusy(false);
  };
  const download = async (t: any, star = false) => {
    setDl((d) => ({ ...d, [t.id]: "busy" }));
    try { await api("/api/music/download", { track: t, star }); setDl((d) => ({ ...d, [t.id]: "done" })); load(); toast(`Added “${t.title}”`, "ok"); }
    catch (e: any) { setDl((d) => ({ ...d, [t.id]: "" })); toast(e.message, "error"); }
  };
  const starred = lib?.tracks.filter((t) => t.starred).length || 0;

  return (
    <div className="page music">
      <div className="mus-left glass depth">
        <div className="sec-head"><h2>Your music</h2>
          <div className="row gap">
            <Btn small icon="plus" onClick={() => api("/api/music/import", {}).then(load).catch((e) => toast(e.message, "error"))}>Add files</Btn>
            <IconBtn icon="folder" title="Open music folder" onClick={() => api("/api/open", { path: "music" })} />
          </div>
        </div>
        <div className="mus-settings">
          <Toggle on={!!s.add_music} onChange={(v) => set({ add_music: v })} label="Add music to new Shorts" />
          <Toggle on={s.music_match !== false} onChange={(v) => set({ music_match: v })} label="Match music to each clip's vibe" />
          <Field label="Which tracks"><Seg value={s.music_mode || "random"} options={[["random", "Any track"], ["starred", `Starred only (${starred})`]]} onChange={(v) => set({ music_mode: v })} /></Field>
          <Field label="Level"><div className="row gap"><Toggle on={s.music_auto !== false} onChange={(v) => set({ music_auto: v })} label="Auto" />
            {s.music_auto === false && <Slider value={s.music_volume ?? 0.12} min={0} max={0.5} step={0.01} fmt={(v) => `${Math.round(v * 200)}%`} onChange={(v) => set({ music_volume: v })} />}</div></Field>
        </div>
        {lib?.folder && <p className="muted small folder-note" title={lib.folder}>Saved in {lib.folder}</p>}
        {lib?.trending_folder && <p className="muted small trend-note">
          <Icon name="flame" size={12} /> Tracks you own the rights to that are trending right now: put them in the <button className="linkbtn" onClick={() => api("/api/open", { path: lib.trending_folder })}>Trending folder</button>. Auto music prefers them when they fit the clip.</p>}
        <div className="track-list scroll">
          {lib && !lib.tracks.length && <div className="act-empty"><Icon name="music" size={26} /><p>No tracks yet. Search free music on the right, or add your own files.</p></div>}
          {lib?.tracks.map((t) => (
            <div key={t.name} className="track">
              <button className="pbtn" onClick={() => setPlay(play === mediaUrl(t.path) ? "" : mediaUrl(t.path))}><Icon name={play === mediaUrl(t.path) ? "pause" : "play"} size={13} /></button>
              <div><b>{t.title}</b><small>{[(t.vibes || []).map((v) => cat?.vibes?.[v]?.name || v).join(" / "), t.bpm ? `${Math.round(t.bpm)} BPM` : "", t.artist, t.license].filter(Boolean).join(" · ")}</small></div>
              {t.trending && <Chip tone="red" icon="flame">Trending</Chip>}
              {t.credit && <Chip title={t.credit}>credit</Chip>}
              <IconBtn icon="star" title={t.starred ? "Unstar" : "Star"} active={t.starred} onClick={() => api("/api/music/star", { name: t.name, on: !t.starred }).then(load)} />
              <IconBtn icon="trash" danger title="Remove" onClick={() => { if (confirm(`Delete ${t.title}?`)) api("/api/music/remove", { name: t.name, path: t.path }).then(load); }} />
            </div>
          ))}
        </div>
      </div>
      <div className="mus-right">
        <div className="glass depth mus-find">
          <div className="sec-head"><h2>Find free music</h2><span className="muted small">safe for monetized videos{s.music_safe_only ? "" : " (filter off)"}</span></div>
          <div className="row gap">
            <Select value={src} options={[["all", "All free sources"], ["ccmixter", "ccMixter"], ["archive", "Internet Archive"], ["commons", "Wikimedia Commons"], ["openverse", "Openverse"], ["jamendo", "Jamendo"]]} onChange={setSrc} />
            <div className="search grow"><Icon name="search" size={16} /><input value={q} placeholder="mood or genre: upbeat, lofi, epic, motivational…" onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search(1)} /></div>
            <Btn kind="primary" icon="search" busy={busy} onClick={() => search(1)}>Search</Btn>
          </div>
          {src === "jamendo" && !s.jamendo_client_id && (
            <div className="row gap note"><span className="muted small">Jamendo needs a free client ID (devportal.jamendo.com):</span><Text value="" placeholder="Paste client ID" onChange={(v) => v.length > 6 && set({ jamendo_client_id: v.trim() })} /></div>
          )}
          {failed.length > 0 && <p className="muted small">Not answering right now: {failed.join(", ")}. Results from the other sources are below.</p>}
          <div className="results scroll">
            <div className="chips-row trend-q"><span className="muted small"><Icon name="flame" size={12} /> Trending styles:</span>
              {TREND_QUERIES.map((t) => <button key={t} className="chipbtn sm" onClick={() => { setQ(t); search(1, t); }}>{t}</button>)}</div>
            {res && !res.length && <p className="muted">No results. Try another word.</p>}
            {res?.map((t) => (
              <div key={t.id} className="track">
                <button className="pbtn" onClick={() => setPlay(play === t.preview_url ? "" : t.preview_url)}><Icon name={play === t.preview_url ? "pause" : "play"} size={13} /></button>
                <div><b>{t.title}</b><small>{t.artist} · {t.license} · {t.source}{t.duration ? ` · ${Math.round(t.duration)}s` : ""}</small></div>
                {dl[t.id] === "done" ? <Chip tone="green" icon="check">Added</Chip> : (
                  <><Btn small icon="download" busy={dl[t.id] === "busy"} onClick={() => download(t)}>Add</Btn>
                    <IconBtn icon="star" title="Add and star" onClick={() => download(t, true)} /></>
                )}
              </div>
            ))}
            {more && <div className="row center"><Btn small kind="ghost" busy={busy} onClick={() => search(page + 1)}>More results</Btn></div>}
          </div>
        </div>
        <div className="glass depth mus-make">
          <div className="sec-head"><h2><Icon name="spark" size={17} /> Make music</h2><span className="muted small">original tracks made on your PC · zero copyright claims · works offline</span></div>
          <div className="row gap wrap">
            <Select value={mood} options={Object.entries(moods) as [string, string][]} onChange={setMood} />
            <Seg value={String(secs)} options={[["60", "1 min"], ["90", "1.5 min"], ["150", "2.5 min"]]} onChange={(v) => setSecs(Number(v))} />
            <div className="grow" />
            <Btn icon="star" busy={genBusy} onClick={() => generate(true)}>Make & star</Btn>
            <Btn kind="primary" icon="music" busy={genBusy} onClick={() => generate(false)}>Make a track</Btn>
          </div>
        </div>
        <div className="glass depth mus-sources">
          <div className="sec-head"><h2>More free libraries</h2><span className="muted small">open in the app's browser · downloads go straight to your music folder</span></div>
          <div className="src-grid">
            {Object.entries(cat?.music_sources || {}).map(([k, v]) => (
              <button key={k} className="src glass-2" onClick={() => api("/api/music/source", { key: k }).then(() => toast(`Opening ${v.name}… downloads are saved to your music folder.`, "info")).catch((e) => toast(e.message, "error"))}>
                <b>{v.name} <Icon name="external" size={13} /></b><small>{v.note}</small>
              </button>
            ))}
          </div>
        </div>
      </div>
      <audio ref={audio} onEnded={() => setPlay("")} />
    </div>
  );
}
