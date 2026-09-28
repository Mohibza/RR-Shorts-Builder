import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: "primary" | "ghost" | "soft" | "danger" | "glow"; icon?: string; busy?: boolean; small?: boolean;
};
export function Btn({ kind = "soft", icon, busy, small, children, className = "", ...rest }: BtnProps) {
  return (
    <button className={`btn btn-${kind} ${small ? "btn-sm" : ""} ${className}`} disabled={busy || rest.disabled} {...rest}>
      {busy ? <span className="spin" /> : icon ? <Icon name={icon} size={small ? 15 : 17} /> : null}
      {children && <span>{children}</span>}
    </button>
  );
}

export function IconBtn({ icon, title, onClick, active, danger, size = 17 }: { icon: string; title: string; onClick?: (e: React.MouseEvent) => void; active?: boolean; danger?: boolean; size?: number }) {
  return (
    <button className={`iconbtn ${active ? "on" : ""} ${danger ? "danger" : ""}`} title={title} aria-label={title} onClick={onClick}>
      <Icon name={icon} size={size} />
    </button>
  );
}

export function Toggle({ on, onChange, label, hint }: { on: boolean; onChange: (v: boolean) => void; label?: string; hint?: string }) {
  return (
    <label className="toggle-row" title={hint}>
      <button type="button" className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} role="switch" aria-checked={on}>
        <span />
      </button>
      {label && <span className="toggle-label">{label}</span>}
    </label>
  );
}

export function Select<T extends string | number>({ value, options, onChange, className = "" }: { value: T; options: [T, string][]; onChange: (v: T) => void; className?: string }) {
  return (
    <div className={`select ${className}`}>
      <select value={String(value)} onChange={(e) => {
        const o = options.find((x) => String(x[0]) === e.target.value);
        if (o) onChange(o[0]);
      }}>
        {options.map(([v, l]) => <option key={String(v)} value={String(v)}>{l}</option>)}
      </select>
      <Icon name="down" size={14} />
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map(([v, l]) => <button key={v} className={v === value ? "on" : ""} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  );
}

export function Slider({ value, min, max, step = 1, onChange, fmt }: { value: number; min: number; max: number; step?: number; onChange: (v: number) => void; fmt?: (v: number) => string }) {
  const pct = ((value - min) / (max - min || 1)) * 100;
  return (
    <div className="slider">
      <input type="range" min={min} max={max} step={step} value={value} style={{ ["--p" as any]: pct + "%" }}
        onChange={(e) => onChange(parseFloat(e.target.value))} />
      <span className="slider-val">{fmt ? fmt(value) : value}</span>
    </div>
  );
}

export function Field({ label, children, hint, className = "" }: { label: string; children: React.ReactNode; hint?: string; className?: string }) {
  return (
    <div className={`field ${className}`}>
      <div className="field-label">{label}{hint && <span className="hint" title={hint}><Icon name="info" size={13} /></span>}</div>
      {children}
    </div>
  );
}

export function Text({ value, onChange, placeholder, type = "text", onEnter, className = "", autoFocus }: { value: string; onChange: (v: string) => void; placeholder?: string; type?: string; onEnter?: () => void; className?: string; autoFocus?: boolean }) {
  return <input className={`input ${className}`} type={type} value={value} placeholder={placeholder} autoFocus={autoFocus}
    onChange={(e) => onChange(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && onEnter) onEnter(); }} />;
}

export function Progress({ frac, label, tone = "accent" }: { frac: number; label?: string; tone?: string }) {
  return (
    <div className={`progress tone-${tone}`}>
      <div className="progress-fill" style={{ width: `${Math.round(Math.max(0.02, Math.min(1, frac)) * 100)}%` }} />
      {label && <span>{label}</span>}
    </div>
  );
}

export function scoreTone(s: number) { return s >= 85 ? "hot" : s >= 70 ? "good" : s >= 55 ? "ok" : "low"; }

export function Score({ value, size = 46 }: { value: number; size?: number }) {
  const r = size / 2 - 4, c = 2 * Math.PI * r;
  return (
    <div className={`score score-${scoreTone(value)}`} style={{ width: size, height: size }} title={`Virality score ${value}/99`}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} className="score-bg" />
        <circle cx={size / 2} cy={size / 2} r={r} className="score-fg" strokeDasharray={c} strokeDashoffset={c * (1 - value / 99)} />
      </svg>
      <b>{value}</b>
    </div>
  );
}

export function Modal({ title, onClose, children, width = 560, footer }: { title: string; onClose: () => void; children: React.ReactNode; width?: number; footer?: React.ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal glass" style={{ width }}>
        <div className="modal-head"><h3>{title}</h3><IconBtn icon="x" title="Close" onClick={onClose} /></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Empty({ icon, title, text, children }: { icon: string; title: string; text?: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-ic"><Icon name={icon} size={30} /></div>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {children}
    </div>
  );
}

export function Chip({ children, tone = "", icon, title }: { children: React.ReactNode; tone?: string; icon?: string; title?: string }) {
  return <span className={`chip ${tone}`} title={title}>{icon && <Icon name={icon} size={13} />}{children}</span>;
}

export function Tags({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [t, setT] = useState("");
  const add = () => {
    const parts = t.split(",").map((x) => x.trim()).filter(Boolean);
    if (parts.length) onChange([...value, ...parts.filter((p) => !value.includes(p))]);
    setT("");
  };
  return (
    <div className="tags">
      {value.map((v) => <span key={v} className="tag">{v}<button onClick={() => onChange(value.filter((x) => x !== v))}>×</button></span>)}
      <input value={t} placeholder={value.length ? "" : placeholder} onChange={(e) => setT(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } else if (e.key === "Backspace" && !t && value.length) onChange(value.slice(0, -1)); }}
        onBlur={add} />
    </div>
  );
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

export function timeAgo(t: number) {
  const d = Date.now() / 1000 - t;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}

export function when(t: number) {
  const d = new Date(t * 1000), now = new Date();
  const same = d.toDateString() === now.toDateString();
  const tm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (same) return `Today ${tm}`;
  const tmr = new Date(now.getTime() + 86400000);
  if (d.toDateString() === tmr.toDateString()) return `Tomorrow ${tm}`;
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + " " + tm;
}
