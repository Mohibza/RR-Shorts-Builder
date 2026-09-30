import { useState } from "react";
import { api } from "../lib/api";
import { Icon } from "./Icon";
import { Btn, Modal, Text } from "./ui";
import { toast } from "../lib/store";

type Check = { ok: boolean; ip?: string; country?: string; city?: string; ms?: number; error?: string };

export const PROXY_HELP = "host:port · host:port:user:pass · user:pass@host:port · socks5://user:pass@host:port";

/** Proxy text box with a Test button. `saved` = masked proxy already saved (shown as placeholder). */
export function ProxyField({ value, onChange, saved, testAccount }: {
  value: string; onChange: (v: string) => void; saved?: string; testAccount?: { platform: string; id: string };
}) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Check | null>(null);
  const test = async () => {
    setBusy(true); setRes(null);
    try {
      const body: Record<string, any> = value.trim() ? { proxy: value.trim() } : { ...testAccount };
      setRes(await api<Check>("/api/accounts/proxy/test", body));
    } catch (e: any) { setRes({ ok: false, error: e.message }); }
    setBusy(false);
  };
  const canTest = !!value.trim() || (!!saved && !!testAccount);
  return (
    <div className="proxy-field">
      <div className="row gap">
        <Text value={value} onChange={(v) => { onChange(v); setRes(null); }} placeholder={saved ? `Saved: ${saved} (type to replace)` : "No proxy (your own connection)"} className="grow" />
        <Btn small kind="soft" icon="zap" busy={busy} disabled={!canTest} onClick={test}>Test</Btn>
      </div>
      <small className="muted">{PROXY_HELP}</small>
      {res && <div className={`proxy-res ${res.ok ? "ok" : "bad"}`}>
        <Icon name={res.ok ? "check" : "alert"} size={14} />
        {res.ok ? <span>Works · IP <b>{res.ip}</b>{res.country ? ` · ${[res.city, res.country].filter(Boolean).join(", ")}` : ""} · {res.ms} ms</span> : <span>{res.error}</span>}
      </div>}
    </div>
  );
}

/** Edit the proxy of an existing direct sign-in account. */
export function ProxyModal({ platform, acc, onClose }: { platform: string; acc: { id: string; name: string; proxy?: string }; onClose: () => void }) {
  const [v, setV] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (text: string) => {
    setBusy(true);
    try {
      const r = await api<{ proxy: string }>("/api/accounts/proxy", { platform, id: acc.id, proxy: text });
      toast(r.proxy ? `${acc.name} now uses ${r.proxy}` : `${acc.name} now uses your own connection`, "ok");
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <Modal title={`Proxy · ${acc.name}`} onClose={onClose} width={560} footer={<>
      {acc.proxy && <Btn kind="ghost" icon="trash" busy={busy} onClick={() => save("")}>Remove proxy</Btn>}
      <div className="grow" />
      <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
      <Btn kind="primary" icon="check" busy={busy} disabled={!v.trim()} onClick={() => save(v.trim())}>Save</Btn></>}>
      <p className="muted">Only this account's browser uses this proxy (sign-in, posting and “Open in its browser”). If the proxy stops working, posts wait and retry. They never switch to your own connection.</p>
      <ProxyField value={v} onChange={setV} saved={acc.proxy} testAccount={{ platform, id: acc.id }} />
      <p className="muted small">Tip: sign in to the account through its proxy (<b>Sign in again</b>) after adding one, so the platform sees the same address every time. Use one proxy per account.</p>
    </Modal>
  );
}
