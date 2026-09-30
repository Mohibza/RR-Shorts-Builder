import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { saveSettings, toast, useStore } from "../lib/store";
import type { QueueJob } from "../lib/types";
import { Icon } from "../components/Icon";
import { Btn, Chip, Field, IconBtn, Modal, Progress, Select, Slider, Text, Toggle, when } from "../components/ui";
import { SignInModal } from "./SignIn";
import { ProxyModal } from "../components/ProxyField";
import { PLAT_ICON } from "./Library";

const KEYS: Record<string, [string, string, string, string]> = {
  youtube: ["yt_client_id", "yt_client_secret", "OAuth client ID", "Client secret"],
  facebook: ["fb_app_id", "fb_app_secret", "Meta app ID", "App secret"],
  instagram: ["fb_app_id", "fb_app_secret", "Meta app ID", "App secret"],
  tiktok: ["tt_client_key", "tt_client_secret", "Client key", "Client secret"],
};
const GUIDE: Record<string, string> = {
  youtube: "Google Cloud Console → new project → enable YouTube Data API v3 → OAuth consent screen (External, add yourself as test user) → Credentials → OAuth client ID → Desktop app.",
  facebook: "developers.facebook.com → Create app (Business) → add Facebook Login → Settings: add http://localhost:53682/ as a redirect URI. Posts Reels to Pages you manage.",
  instagram: "Same Meta app as Facebook, with Instagram Graph API added. Your Instagram must be a Business/Creator account linked to a Facebook Page.",
  tiktok: "developers.tiktok.com → Manage apps → Content Posting API + Login Kit → redirect http://localhost:53683/callback/. Unaudited apps post as “Only me”.",
};
const HOURS: [number, string][] = Array.from({ length: 24 }, (_, h) => [h, `${String(h).padStart(2, "0")}:00`]);

export function PublishPage() {
  const accounts = useStore((s) => s.accounts);
  const cat = useStore((s) => s.catalog);
  const s = useStore((x) => x.settings);
  const queue = useStore((x) => x.queue);
  const uploads = useStore((x) => x.uploads);
  const connect = useStore((x) => x.connect);
  const [signin, setSignin] = useState<{ platform: string; account?: string } | null>(null);
  const [keys, setKeys] = useState<string | null>(null);
  const [pxEdit, setPxEdit] = useState<{ platform: string; acc: { id: string; name: string; proxy?: string } } | null>(null);
  const [filter, setFilter] = useState<"upcoming" | "done" | "failed">("upcoming");
  const set = (patch: Record<string, any>) => saveSettings(patch).catch((e) => toast(e.message, "error"));
  const plats = Object.keys(cat?.platforms || {});

  const q = useMemo(() => {
    const up = queue.filter((j) => j.status === "waiting" || j.status === "uploading").sort((a, b) => a.due - b.due);
    const done = queue.filter((j) => j.status === "done").sort((a, b) => (b.done_at || 0) - (a.done_at || 0));
    const failed = queue.filter((j) => j.status === "failed");
    return { upcoming: up, done, failed };
  }, [queue]);
  const act = (id: string, action: string, extra: Record<string, any> = {}) => api("/api/queue/action", { id, action, ...extra }).catch((e) => toast(e.message, "error"));
  const bulk = (action: string, msg?: string) => api("/api/queue/action", { action }).then(() => msg && toast(msg, "info")).catch((e) => toast(e.message, "error"));
  const off = useMemo(() => {
    const o = new Set<string>();
    Object.entries(accounts).forEach(([p, l]) => l.forEach((a) => { if (!a.enabled) o.add(`${p}|${a.id}`); }));
    return o;
  }, [accounts]);
  const paused = !!s.upload_paused;
  const posting = q.upcoming.filter((j) => j.status === "uploading").length;

  return (
    <div className="page publish">
      <div className="pub-left">
        <div className="page-head"><div><h1>Publish</h1><span className="muted">Connect accounts once. Shorts post themselves on a natural schedule.</span></div></div>
        <div className="acc-grid">
          {plats.map((p) => {
            const list = accounts[p] || [];
            const cn = connect[p];
            const hosts = list.map((a) => a.proxy_host || "").filter(Boolean);
            const dup = (h?: string) => !!h && hosts.filter((x) => x === h).length > 1;
            return (
              <div key={p} className={`acc-card glass depth pl-${p}`}>
                <div className="acc-head"><span className="plat-ic"><Icon name={PLAT_ICON[p]} size={20} /></span><b>{cat?.platforms[p]}</b>{list.length > 1 && <span className="acc-count">{list.length} accounts</span>}
                  <Toggle on={(s.upload_platforms || []).includes(p)} onChange={(on) => set({ upload_platforms: on ? [...new Set([...(s.upload_platforms || []), p])] : (s.upload_platforms || []).filter((x: string) => x !== p) })} label="Auto" hint="Include this platform when Shorts are posted automatically" />
                </div>
                <div className="acc-list">
                  {!list.length && <p className="muted small">No account yet.</p>}
                  {list.map((a) => (
                    <div key={a.id} className={`acc ${a.enabled ? "" : "off"}`}>
                      <Icon name="user" size={15} />
                      <div className="acc-name"><b>{a.name}</b>
                        {a.proxy ? <small className={`px ${dup(a.proxy_host) ? "dup" : ""}`} title={dup(a.proxy_host) ? "Another account here uses the same proxy. Use one proxy per account." : "This account's browser goes through this proxy"}>via {a.proxy}{dup(a.proxy_host) ? " · shared!" : ""}</small>
                          : <small>{a.mode === "browser" ? "Direct sign-in · own connection" : "Official API"}</small>}</div>
                      <Toggle on={a.enabled} onChange={(on) => api("/api/accounts/enable", { platform: p, id: a.id, on })} />
                      {a.mode === "browser" && <IconBtn icon="globe" active={!!a.proxy} title={a.proxy ? `Proxy: ${a.proxy}` : "Add a proxy for this account"} onClick={() => setPxEdit({ platform: p, acc: a })} />}
                      {a.mode === "browser" && <IconBtn icon="refresh" title="Sign in again" onClick={() => setSignin({ platform: p, account: a.id })} />}
                      {a.mode === "browser" && <IconBtn icon="external" title="Open in its browser (e.g. to finish a security check)" onClick={() => api("/api/accounts/open", { platform: p, id: a.id }).catch((e) => toast(e.message, "error"))} />}
                      <IconBtn icon="trash" danger title="Remove" onClick={() => { if (confirm(`Remove ${a.name}?`)) api("/api/accounts/remove", { platform: p, id: a.id }); }} />
                    </div>
                  ))}
                </div>
                <div className="acc-foot">
                  <Btn small kind="primary" icon="plus" onClick={() => setSignin({ platform: p })}>{list.length ? "Add another account" : "Add account"}</Btn>
                  <button className="linkbtn small" onClick={() => setKeys(p)}>Use developer keys</button>
                </div>
                {cn && cn.state !== "ok" && <div className={`acc-msg st-${cn.state}`}>{cn.msg}</div>}
              </div>
            );
          })}
        </div>
        <div className="auto glass depth">
          <div className="sec-head"><h2><Icon name="zap" size={18} /> Auto-post</h2><span className="muted small grow">{s.auto_upload ? "On: every new Short is posted on a natural schedule." : "Off: Shorts post only when you click Post, and they go out right away."}</span><Toggle on={!!s.auto_upload} onChange={(v) => set({ auto_upload: v })} label={s.auto_upload ? "On" : "Off"} /></div>
          <div className="auto-grid">
            <Field label="Random gap between posts (minutes)"><div className="row gap">
              <Text value={String(s.upload_gap_min)} onChange={(v) => set({ upload_gap_min: Math.max(1, Number(v) || 1) })} /><span className="muted">to</span>
              <Text value={String(s.upload_gap_max)} onChange={(v) => set({ upload_gap_max: Math.max(Number(s.upload_gap_min) || 1, Number(v) || 1) })} /></div></Field>
            <Field label="Max posts per account per day"><Slider value={s.upload_daily_cap ?? 6} min={1} max={20} onChange={(v) => set({ upload_daily_cap: v })} /></Field>
            <Field label="Quiet hours (no posting)"><div className="row gap">
              <Select value={s.upload_quiet_start ?? 1} options={HOURS} onChange={(v) => set({ upload_quiet_start: v })} /><span className="muted">to</span>
              <Select value={s.upload_quiet_end ?? 8} options={HOURS} onChange={(v) => set({ upload_quiet_end: v })} /></div></Field>
            <Field label="YouTube visibility"><Select value={s.yt_privacy || "public"} options={[["public", "Public"], ["unlisted", "Unlisted"], ["private", "Private"]]} onChange={(v) => set({ yt_privacy: v })} /></Field>
            <Field label="Uploads at the same time"><Slider value={s.upload_parallel ?? 2} min={1} max={4} onChange={(v) => set({ upload_parallel: v })} fmt={(v) => v === 1 ? "1 (one by one)" : `${v} accounts`} /></Field>
            <Field label="Close a stuck upload after"><Select value={s.upload_timeout_min ?? 0} options={[[0, "Automatic"], [20, "20 min"], [30, "30 min"], [45, "45 min"], [60, "60 min"], [90, "90 min"]]} onChange={(v) => set({ upload_timeout_min: v })} /></Field>
            <Toggle on={!!s.web_upload_visible} onChange={(v) => set({ web_upload_visible: v })} label="Show the browser while posting" hint="Direct sign-in uploads normally run in a minimized window." />
          </div>
        </div>
      </div>
      <div className="pub-right glass depth">
        <div className="sec-head"><h2>Queue</h2>
          <Btn small kind={paused ? "primary" : "ghost"} icon={paused ? "play" : "pause"} onClick={() => set({ upload_paused: !paused })}>{paused ? "Resume" : "Pause"}</Btn>
          <div className="seg small">
            {(["upcoming", "done", "failed"] as const).map((k) => <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{k === "upcoming" ? "Upcoming" : k === "done" ? "Posted" : "Failed"} {q[k].length ? <em>{q[k].length}</em> : null}</button>)}
          </div>
        </div>
        {(paused || posting > 0) && <div className={`q-banner ${paused ? "warn" : ""}`}>
          <Icon name={paused ? "pause" : "upload"} size={15} />
          {paused ? <span>Queue paused. Nothing new is posted until you click Resume{posting ? ` (${posting} finishing now)` : ""}.</span>
            : <span>Posting {posting} now · up to {s.upload_parallel ?? 2} at once, one per account.</span>}
        </div>}
        <div className="queue scroll">
          {!q[filter].length && <div className="act-empty"><Icon name="calendar" size={26} /><p>{filter === "upcoming" ? "Nothing scheduled. Post a Short from Clips or Library, or turn on Auto-post." : "Nothing here yet."}</p></div>}
          {q[filter].map((j) => <QueueRow key={j.id} j={j} frac={uploads[j.id]} act={act} off={off.has(`${j.platform}|${j.account_id}`)} />)}
        </div>
        {filter === "upcoming" && q.upcoming.some((j) => j.status === "waiting") && <div className="row end"><Btn small kind="ghost" icon="send" onClick={() => { if (confirm("Post everything waiting now? Each account still posts one Short at a time.")) bulk("post_all", "Posting now, one per account at a time."); }}>Post all now</Btn></div>}
        {filter === "done" && q.done.length > 0 && <div className="row end"><Btn small kind="ghost" icon="trash" onClick={() => api("/api/queue/clear", {})}>Clear posted</Btn></div>}
        {filter === "failed" && q.failed.length > 0 && <div className="row end gap">
          <button className="linkbtn small" onClick={() => api("/api/open", { path: "errors" })}>Open error screenshots</button>
          <Btn small kind="ghost" icon="trash" onClick={() => bulk("clear_failed")}>Clear</Btn>
          <Btn small kind="primary" icon="refresh" onClick={() => bulk("retry_failed", "Failed posts are back in line, a few minutes apart.")}>Retry all</Btn>
        </div>}
      </div>
      {signin && <SignInModal platform={signin.platform} account={signin.account} onClose={() => setSignin(null)} />}
      {keys && <KeysModal platform={keys} onClose={() => setKeys(null)} />}
      {pxEdit && <ProxyModal platform={pxEdit.platform} acc={pxEdit.acc} onClose={() => setPxEdit(null)} />}
    </div>
  );
}

function QueueRow({ j, frac, act, off }: { j: QueueJob; frac?: number; act: (id: string, a: string, extra?: Record<string, any>) => void; off?: boolean }) {
  const soft = j.status === "waiting" && j.error && (j.waits || 0) > 0 && !j.attempts;
  const info = j.status === "done" ? `posted ${when(j.done_at || j.due)}`
    : j.status === "uploading" ? `posting now…${frac && frac > 0.05 ? ` ${Math.round(frac * 100)}%` : ""}`
    : j.status === "failed" ? j.error
    : off ? "account switched off, waiting" : when(j.due);
  return (
    <div className={`qrow st-${j.status} ${off && j.status === "waiting" ? "off" : ""}`}>
      <span className="plat-ic sm"><Icon name={PLAT_ICON[j.platform]} size={16} /></span>
      <div className="qmain">
        <b title={j.title}>{j.title || "Short"}</b>
        <small title={j.error || undefined}>{j.account} · {info}
          {j.status === "waiting" && j.error ? (soft ? ` · ${j.error}` : ` · retry ${j.attempts + 1}: ${j.error}`) : ""}</small>
        {j.status === "uploading" && <Progress frac={frac ?? 0.02} tone="green" />}
      </div>
      {j.status === "done" && j.url && <a className="linkbtn small" href={j.url} target="_blank" rel="noreferrer">View</a>}
      {j.status === "done" && j.note && <Chip title={j.note}>note</Chip>}
      {j.status === "waiting" && <IconBtn icon="send" title="Post now" onClick={() => act(j.id, "now")} />}
      {j.status === "waiting" && <IconBtn icon="clock" title="Post 1 hour later" onClick={() => act(j.id, "later", { minutes: 60 })} />}
      {j.status === "uploading" && <IconBtn icon="pause" title="Stop this upload (closes its browser; you can retry it)" onClick={() => act(j.id, "stop")} />}
      {j.status === "failed" && <IconBtn icon="refresh" title="Retry" onClick={() => act(j.id, "retry")} />}
      {j.status !== "uploading" && <IconBtn icon="x" title="Remove" onClick={() => act(j.id, "remove")} />}
    </div>
  );
}

function KeysModal({ platform, onClose }: { platform: string; onClose: () => void }) {
  const s = useStore((x) => x.settings);
  const cat = useStore((x) => x.catalog);
  const [a, b, la, lb] = KEYS[platform];
  const [v1, setV1] = useState(s[a] || "");
  const [v2, setV2] = useState(s[b] || "");
  const [busy, setBusy] = useState(false);
  const connect = async () => {
    setBusy(true);
    try {
      await saveSettings({ [a]: v1.trim(), [b]: v2.trim() });
      await api("/api/accounts/connect", { platform });
      toast("Approve access in the browser tab that opened.", "info");
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal title={`${cat?.platforms[platform]} · official API`} onClose={onClose} footer={<>
      <Btn kind="ghost" onClick={onClose}>Cancel</Btn><Btn kind="primary" icon="link" busy={busy} disabled={!v1.trim() || !v2.trim()} onClick={connect}>Save & connect</Btn></>}>
      <p className="muted">Optional. Most people use <b>Add account</b> (direct sign-in, no keys). Developer keys use the platform's official upload API instead.</p>
      <p className="guide">{GUIDE[platform]}</p>
      <Field label={la}><Text value={v1} onChange={setV1} /></Field>
      <Field label={lb}><Text value={v2} onChange={setV2} type="password" /></Field>
    </Modal>
  );
}
