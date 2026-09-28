import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { toast, useStore } from "../lib/store";
import { Icon } from "../components/Icon";
import { Btn, Modal, Text } from "../components/ui";

const HINT: Record<string, string> = {
  ytdl: "YouTube sometimes asks downloaders to prove they're not a bot. Sign in once in the app's own browser window and the app keeps the login fresh.",
  youtube: "Sign in with the Google account that owns your channel. If YouTube asks which channel, pick it.",
  tiktok: "Log in to TikTok the way you normally do (phone, email, Google…).",
  facebook: "Log in to Facebook. To post Reels as your Page, switch to the Page's profile in that window before clicking Finish.",
  instagram: "Log in to Instagram (any account type). If it asks to save login info, click Save.",
};
const NAME: Record<string, string> = { ytdl: "YouTube", youtube: "YouTube", tiktok: "TikTok", facebook: "Facebook", instagram: "Instagram" };

function useSession(platform: string, account?: string) {
  const [sid, setSid] = useState("");
  const ses = useStore((s) => (sid ? s.signins[sid] : null));
  const [label, setLabel] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    let id = "";
    api("/api/signin/start", { platform, account: account || "" }).then((r: any) => { id = r.id; setSid(r.id); setLabel(r.label || ""); })
      .catch((e) => setErr(e.message));
    return () => { if (id) api("/api/signin/cancel", { id }).catch(() => {}); };
  }, [platform, account]);
  return { sid, ses, label, setLabel, err, setErr };
}

export function SignInModal({ platform, account, onClose }: { platform: string; account?: string; onClose: () => void }) {
  const { sid, ses, label, setLabel, err, setErr } = useSession(platform, account);
  const state = ses?.state || "checking";
  const ok = state === "ok";
  useEffect(() => { if (ok) toast(`Signed in to ${NAME[platform]}`, "ok"); }, [ok]);
  const call = (path: string) => api(path, { id: sid, label: platform === "ytdl" ? undefined : label }).catch((e) => setErr(e.message));
  const done = async () => {
    if (platform !== "ytdl" && sid) await api("/api/signin/label", { id: sid, label }).catch(() => {});
    onClose();
  };
  const steps = [
    `Click “Open sign-in window”. A Chrome window opens with the app's private profile (your normal Chrome isn't touched).`,
    `Sign in to ${NAME[platform]}.`,
    `Come back and click Finish (or just close that window).`,
  ];
  return (
    <Modal title={`Sign in to ${NAME[platform]}`} onClose={onClose} width={560} footer={
      <>
        <Btn kind="ghost" onClick={onClose}>{ok ? "Close" : "Cancel"}</Btn>
        {!ok && <Btn icon="globe" disabled={!sid || state === "checking" || state === "closing"} onClick={() => call("/api/signin/open")}>Open sign-in window</Btn>}
        {ok ? <Btn kind="primary" icon="check" onClick={done}>Done</Btn>
          : <Btn kind="primary" icon="check" busy={state === "checking" || state === "closing"} disabled={!sid} onClick={() => call("/api/signin/finish")}>Finish</Btn>}
      </>}>
      <p className="muted">{HINT[platform]}</p>
      <ol className="steps-list">{steps.map((t, i) => <li key={i}><span>{i + 1}</span>{t}</li>)}</ol>
      {platform !== "ytdl" && (
        <div className="field"><div className="field-label">Account name (shown in the app)</div>
          <Text value={label} onChange={setLabel} placeholder="e.g. Main channel, Urdu page, @myhandle" /></div>
      )}
      <div className={`signin-status st-${state}`}>
        {state === "checking" || state === "closing" ? <span className="spin" /> : <Icon name={ok ? "check" : state === "error" ? "alert" : "info"} size={16} />}
        <span>{err || ses?.msg || "Checking…"}</span>
      </div>
    </Modal>
  );
}

export function YouTubeSignIn({ onClose }: { onClose: () => void }) {
  return <SignInModal platform="ytdl" onClose={onClose} />;
}
