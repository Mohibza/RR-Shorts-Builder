// The editor's side panels: the tool tabs on the left (media, Auto Edit, text, shapes, clip looks and transitions, captions and
// cursor) and the properties of a selected zoom / text / shape / caption on the right.
import { useState } from "react";
import { mediaUrl } from "../../lib/api";
import { toast, useStore } from "../../lib/store";
import { ANIMS_IN, ANIMS_LOOP, ANIMS_OUT, MOTIONS, Assets, clamp, El, EProject, Fx, Item, itemEnd, LOOKS, Media, SHAPES, tc, TEXT_PRESETS, TRANSITIONS } from "../../lib/edit";
import { Icon } from "../Icon";
import { Btn, Field, Progress, Seg, Select, Toggle } from "../ui";

type Commit = (fn: (q: EProject) => EProject, tag?: string) => void;
export type AutoJob = { state: string; stage: string; frac: number; error?: string; summary?: Record<string, any> | null };

export function Num({ label, value, min, max, step = 1, unit = "", onChange }: { label: string; value: number; min: number; max: number; step?: number; unit?: string; onChange: (v: number) => void }) {
  return (
    <label className="ve-num"><span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={clamp(value, min, max)} style={{ ["--p" as any]: ((clamp(value, min, max) - min) / (max - min)) * 100 + "%" }} onChange={(e) => onChange(Number(e.target.value))} />
      <input type="number" min={min} max={max} step={step} value={Math.round(value * 100) / 100} onChange={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v)) onChange(clamp(v, min, max)); }} /><i>{unit}</i>
    </label>
  );
}
export function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return <label className="ve-num ve-colorrow"><span>{label}</span><div className="ve-color"><input type="color" value={value} onChange={(e) => onChange(e.target.value)} /><em>{value.toUpperCase()}</em></div></label>;
}

const TABS: [string, string, string][] = [["media", "Media", "film"], ["auto", "Auto Edit", "wand"], ["text", "Text", "type"], ["shapes", "Shapes", "layout"], ["clip", "Looks and transitions", "sliders"], ["rec", "Captions and cursor", "spark"]];

export function LeftPanel({ p, assets, items, commit, onImport, onAdd, addEl, auto, runAuto, cancelAuto }: {
  p: EProject; assets: Record<string, Assets>; items: Item[]; commit: Commit; onImport: () => void; onAdd: (m: Media) => void;
  addEl: (e: Partial<El>) => void; auto: AutoJob | null; runAuto: (opts: Record<string, any>) => void; cancelAuto: () => void }) {
  const [tab, setTab] = useState("media");
  return (
    <aside className="ve-panel ve-bin">
      <div className="ve-tabs">{TABS.map(([k, name, ic]) => <button key={k} className={tab === k ? "on" : ""} title={name} onClick={() => setTab(k)}><Icon name={ic} size={16} /></button>)}</div>
      <div className="ve-ptitle"><span>{TABS.find((t) => t[0] === tab)![1]}</span>{tab === "media" && <button title="Import video, audio or images (Ctrl+I)" onClick={onImport}><Icon name="plus" size={14} /></button>}</div>
      {tab === "media" && <MediaTab p={p} assets={assets} onImport={onImport} onAdd={onAdd} />}
      {tab === "auto" && <AutoTab p={p} auto={auto} run={runAuto} cancel={cancelAuto} />}
      {tab === "text" && (
        <div className="ve-bin-list">
          <p className="muted small ve-hint">Click a style to add it at the playhead. Then type your words on the right and drag it on the canvas.</p>
          <div className="ve-grid">{TEXT_PRESETS.map((t) => (
            <button key={t.key} className="ve-card" onClick={() => addEl({ kind: "text", x: 0.5, y: 0.5, ...t.el })}>
              <span style={{ fontFamily: `"${t.el.font}", sans-serif`, color: t.el.box ? t.el.color : t.el.color, background: t.el.box ? t.el.box_color : undefined, padding: t.el.box ? "2px 8px" : 0,
                fontWeight: t.el.bold ? 700 : 400, WebkitTextStroke: t.el.stroke ? `2px ${t.el.stroke_color}` : undefined, paintOrder: "stroke fill" }}>{t.key === "outline" ? "BIG" : t.key === "neon" ? "NEW" : "Aa"}</span>
              <small>{t.name}</small></button>))}</div>
          <button className="ve-import" onClick={() => addEl({ kind: "caption", text: "New caption" })}><Icon name="plus" size={15} /> Add a caption line</button>
        </div>
      )}
      {tab === "shapes" && (
        <div className="ve-bin-list">
          <p className="muted small ve-hint">Point at things, box them in, hide private details. Shapes follow the picture when it zooms.</p>
          <div className="ve-grid">{SHAPES.map((s) => (
            <button key={s.key} className="ve-card" onClick={() => addEl({ kind: "shape", x: 0.5, y: 0.5, ...s.el })}><Icon name={{ arrow: "send", rect: "frame", ellipse: "record", highlight: "edit", line: "minus", step: "info", blur: "eyeoff" }[s.key] || "layout"} size={24} /><small>{s.name}</small></button>))}</div>
          <button className="ve-import" onClick={() => addEl({ kind: "zoom", z: 1.7, cx: 0.5, cy: 0.5, ease: 0.5 })}><Icon name="search" size={15} /> Add a zoom at the playhead</button>
        </div>
      )}
      {tab === "clip" && <ClipTab p={p} items={items} commit={commit} />}
      {tab === "rec" && <RecTab p={p} commit={commit} />}
    </aside>
  );
}

function MediaTab({ p, assets, onImport, onAdd }: { p: EProject; assets: Record<string, Assets>; onImport: () => void; onAdd: (m: Media) => void }) {
  return (
    <>
      <div className="ve-bin-list">
        {p.media.map((m) => {
          const a = assets[m.id];
          return (
            <div key={m.id} className="ve-media" draggable onDragStart={(e) => { e.dataTransfer.setData("text/rr-media", m.id); e.dataTransfer.effectAllowed = "copy"; }}
              onDoubleClick={() => onAdd(m)} title={`${m.name}\nDrag onto the timeline, or double-click to add at the end`}>
              <div className="ve-mthumb">
                {a?.poster ? <img src={mediaUrl(a.poster)} alt="" draggable={false} /> : <Icon name={m.kind === "audio" ? "music" : m.kind === "image" ? "image" : "film"} size={20} />}
                {m.kind !== "image" && <span className="dur">{tc(m.duration, 30, false)}</span>}
                {a && !a.ready && <span className="ve-mbusy"><span className="spin" /></span>}
              </div>
              <div className="ve-mname"><b>{m.name}</b><span>{m.kind === "audio" ? "Audio" : `${m.width}×${m.height}`}{a?.need_proxy && !a.proxy && !a.ready ? " · preparing preview" : ""}</span></div>
              <button className="ve-madd" title="Add to the timeline" onClick={() => onAdd(m)}><Icon name="plus" size={14} /></button>
            </div>
          );
        })}
        {!p.media.length && <p className="muted small ve-bin-empty">No media yet. Import a video to begin.</p>}
      </div>
      <button className="ve-import" onClick={onImport}><Icon name="upload" size={16} /> Import media</button>
    </>
  );
}

function AutoTab({ p, auto, run, cancel }: { p: EProject; auto: AutoJob | null; run: (o: Record<string, any>) => void; cancel: () => void }) {
  const [o, setO] = useState<Record<string, any>>({ cuts: true, fillers: true, retakes: true, zoom: true, zoom_level: 1.7, zoom_hold: 3, zoom_click: 0.5, captions: true, chapters: true, audio: true, cursor: true });
  const set = (k: string, v: any) => setO((x) => ({ ...x, [k]: v }));
  const rec = !!p.cursor?.events, running = auto?.state === "running", s = p.auto;
  const T = (k: string, label: string, hint: string, off?: boolean) => <Toggle on={!!o[k] && !off} onChange={(v) => set(k, v)} label={label} hint={off ? "Needs a recording made in Studio (it logs where you click)" : hint} />;
  return (
    <div className="ve-bin-list ve-auto">
      <p className="muted small ve-hint">One click turns the raw video into a finished edit. Every cut, zoom and caption lands on the timeline, so you can change or remove any of them. Ctrl+Z undoes all of it.</p>
      {T("cuts", "Cut dead air", "Removes pauses where nobody talks and nothing happens")}
      {T("fillers", "Cut “um” and “uh”", "Needs speech to be recognised")}
      {T("retakes", "Cut repeated takes", "When you restart the same sentence, the first try goes")}
      {T("zoom", "Zoom to clicks and typing", "Zooms in where you click, glides to where you type, and stays until the typing is over", !rec)}
      {o.zoom && rec && <Num label="Zoom" unit="×" min={1.3} max={2.5} step={0.1} value={o.zoom_level} onChange={(v) => set("zoom_level", v)} />}
      {o.zoom && rec && <Num label="Stay in" unit="s" min={0} max={10} step={0.5} value={o.zoom_hold} onChange={(v) => set("zoom_hold", v)} />}
      {o.zoom && rec && <Num label="Clicks" unit="%" min={0} max={100} step={5} value={Math.round((o.zoom_click ?? 0.5) * 100)} onChange={(v) => set("zoom_click", v / 100)} />}
      {o.zoom && rec && <p className="muted small">Typing in a text field gets the full zoom and keeps it until the typing is over. A click that does something (button, menu, link) gets a lighter zoom, set by “Clicks”. A click on nothing only shows the ripple. When the next action comes within “Stay in” seconds the view glides to it; later than that, it has zoomed back out and zooms in again.</p>}
      {T("cursor", "Click ripples", "A ring spreads from every click", !rec)}
      {T("captions", "Captions", "Writes what you say under the video")}
      {T("chapters", "Chapters from markers", "Each marker you dropped while recording starts a chapter")}
      {T("audio", "Clean up the voice", "Removes background hiss and evens out the volume (heard in the export)")}
      {running ? (
        <div className="ve-autobusy"><Progress frac={auto!.frac} label={auto!.stage} /><Btn small kind="ghost" onClick={cancel}>Stop</Btn></div>
      ) : <Btn kind="primary" icon="wand" onClick={() => run({ ...o, zoom: o.zoom && rec, speedup: false, cursor: o.cursor && rec })}>{p.auto ? "Run Auto Edit again" : "Run Auto Edit"}</Btn>}
      {p.auto && !running && <p className="muted small">Running again starts from the video as it was before the last Auto Edit, so changes made since then are replaced.</p>}
      {auto?.state === "failed" && <p className="bad-line"><Icon name="alert" size={15} /> {auto.error}</p>}
      {!running && s && (
        <div className="ve-autosum">
          <b>Last Auto Edit</b>
          <span>{tc(s.before || 0, 30, false)} → {tc(s.after || 0, 30, false)} ({Math.round(s.saved || 0)} s shorter)</span>
          <span>{s.cuts || 0} cuts · {s.zooms || 0} zooms{s.plain_clicks ? ` · ${s.plain_clicks} clicks with ripple only` : ""} · {s.captions || 0} captions{s.chapters ? ` · ${s.chapters} chapters` : ""}</span>
          {!s.voice && <span className="muted">No speech was found, so cuts follow your mouse and keyboard activity only.</span>}
        </div>
      )}
      {(p.chapters?.length || 0) > 1 && <Btn small icon="copy" onClick={() => { navigator.clipboard?.writeText(p.chapters!.map((c) => `${tc(c.t, 30, false)} ${c.title}`).join("\n")); toast("Chapters copied. Paste them into the YouTube description.", "ok"); }}>Copy chapters for YouTube</Btn>}
    </div>
  );
}

function ClipTab({ p, items, commit }: { p: EProject; items: Item[]; commit: Commit }) {
  if (!items.length) return <div className="ve-bin-list"><p className="muted small ve-hint">Select one or more clips on the timeline, then pick a look or a transition here.</p></div>;
  const it = items[0], ids = new Set(items.map((x) => x.id));
  const set = (patch: Partial<Item>, tag: string) => commit((q) => ({ ...q, items: q.items.map((o) => (ids.has(o.id) ? { ...o, ...patch } : o)) }), tag + it.id);
  const fx = it.fx || {};
  const setFx = (patch: Fx, tag: string) => set({ fx: { ...fx, ...patch } }, "fx" + tag);
  const prev = p.items.find((o) => o.track === it.track && Math.abs(itemEnd(o) - it.start) < 0.05);
  const cross = () => commit((q) => ({ ...q, items: q.items.map((o) => (o.id === it.id ? { ...o, enter: { type: "fade", dur: 0.5 } } : prev && o.id === prev.id ? { ...o, tail: 0.5 } : o)) }));
  return (
    <div className="ve-bin-list">
      <h4>Look</h4>
      <div className="ve-looks">{Object.entries(LOOKS).map(([k, [name]]) => <button key={k} className={(fx.look || "none") === k ? "on" : ""} onClick={() => setFx({ look: k }, "look")}>{name}</button>)}</div>
      <Num label="Bright" min={-30} max={30} value={(fx.bright || 0) * 100} onChange={(v) => setFx({ bright: v / 100 }, "b")} />
      <Num label="Contrast" unit="%" min={60} max={160} value={(fx.contrast ?? 1) * 100} onChange={(v) => setFx({ contrast: v / 100 }, "c")} />
      <Num label="Colour" unit="%" min={0} max={200} value={(fx.sat ?? 1) * 100} onChange={(v) => setFx({ sat: v / 100 }, "s")} />
      <Num label="Warmth" min={-40} max={40} value={(fx.temp || 0) * 100} onChange={(v) => setFx({ temp: v / 100 }, "t")} />
      <Num label="Blur" min={0} max={100} value={(fx.blur || 0) * 100} onChange={(v) => setFx({ blur: v / 100 }, "bl")} />
      <Num label="Vignette" min={0} max={100} value={(fx.vignette || 0) * 100} onChange={(v) => setFx({ vignette: v / 100 }, "v")} />
      <Num label="Sharpen" min={0} max={100} value={(fx.sharpen || 0) * 100} onChange={(v) => setFx({ sharpen: v / 100 }, "sh")} />
      <Num label="Grain" min={0} max={100} value={(fx.grain || 0) * 100} onChange={(v) => setFx({ grain: v / 100 }, "g")} />
      <p className="muted small">Sharpen and grain show in the export.</p>
      <h4>Transition in</h4>
      <Select value={it.enter?.type || "none"} options={TRANSITIONS} onChange={(v) => set({ enter: { type: v, dur: it.enter?.dur || 0.5 } }, "en")} />
      {it.enter?.type && it.enter.type !== "none" && <Num label="Length" unit="s" min={0.1} max={2} step={0.05} value={it.enter.dur} onChange={(v) => set({ enter: { ...it.enter!, dur: v } }, "end")} />}
      {prev && <Btn small icon="layout" onClick={cross}>Cross-dissolve from the previous clip</Btn>}
      <h4>Movement</h4>
      <Seg value={it.motion || "none"} options={MOTIONS} onChange={(v) => set({ motion: v }, "mo")} />
      <h4>Transition out</h4>
      <Select value={it.exit?.type || "none"} options={TRANSITIONS.map(([k, n]) => [k, n.replace("from", "to")] as [string, string])} onChange={(v) => set({ exit: { type: v, dur: it.exit?.dur || 0.5 } }, "ex")} />
      {it.exit?.type && it.exit.type !== "none" && <Num label="Length" unit="s" min={0.1} max={2} step={0.05} value={it.exit.dur} onChange={(v) => set({ exit: { ...it.exit!, dur: v } }, "exd")} />}
    </div>
  );
}

function RecTab({ p, commit }: { p: EProject; commit: Commit }) {
  const fontMap = useStore((s) => s.catalog?.fonts);
  const fonts = Object.keys(fontMap || {});
  const c = p.captions || ({} as El), cur = p.cursor;
  const setC = (patch: Partial<El>, tag: string) => commit((q) => ({ ...q, captions: { ...(q.captions || ({} as El)), ...patch } }), "cap" + tag);
  const setCur = (patch: Record<string, any>, tag: string) => commit((q) => ({ ...q, cursor: { ...q.cursor!, ...patch } }), "cur" + tag);
  const n = (p.els || []).filter((e) => e.kind === "caption").length;
  return (
    <div className="ve-bin-list">
      <h4>Caption style</h4>
      <p className="muted small">{n ? `${n} caption line${n > 1 ? "s" : ""} on the timeline. Select one to change its words.` : "No captions yet. Run Auto Edit, or add lines from the Text tab."}</p>
      <Field label="Font"><Select value={c.font || "Poppins"} options={(fonts.length ? fonts : ["Poppins"]).map((f) => [f, f] as [string, string])} onChange={(v) => setC({ font: v }, "f")} /></Field>
      <Num label="Size" min={2} max={12} step={0.1} value={(c.size || 0.05) * 100} onChange={(v) => setC({ size: v / 100 }, "s")} />
      <Num label="Height" unit="%" min={5} max={95} value={(c.y ?? 0.88) * 100} onChange={(v) => setC({ y: v / 100 }, "y")} />
      <Color label="Text" value={c.color || "#FFFFFF"} onChange={(v) => setC({ color: v }, "c")} />
      <Toggle on={!!c.box} onChange={(v) => setC({ box: v, stroke: v ? 0 : 5, stroke_color: "#000000" }, "b")} label="Box behind the text" />
      {c.box ? <><Color label="Box" value={c.box_color || "#000000"} onChange={(v) => setC({ box_color: v }, "bc")} /><Num label="Box" unit="%" min={10} max={100} value={(c.box_alpha ?? 0.62) * 100} onChange={(v) => setC({ box_alpha: v / 100 }, "ba")} /></>
        : <Num label="Outline" min={0} max={12} value={c.stroke || 0} onChange={(v) => setC({ stroke: v }, "st")} />}
      <Toggle on={!!c.bold} onChange={(v) => setC({ bold: v }, "bo")} label="Bold" />
      <Toggle on={!!c.upper} onChange={(v) => setC({ upper: v }, "u")} label="ALL CAPS" />
      {n > 0 && <Btn small kind="ghost" icon="trash" onClick={() => commit((q) => ({ ...q, els: (q.els || []).filter((e) => e.kind !== "caption") }))}>Remove all captions</Btn>}
      <h4>Cursor</h4>
      {!cur?.events ? <p className="muted small">Cursor effects work on recordings made in Studio, because the recorder logs where the mouse was.</p> : (
        <>
          <Toggle on={cur.ripple !== false} onChange={(v) => setCur({ ripple: v }, "r")} label="Ripple on every click" />
          {cur.ripple !== false && <Color label="Ripple" value={cur.ripple_color || "#FFD400"} onChange={(v) => setCur({ ripple_color: v }, "rc")} />}
          <Toggle on={!!cur.highlight} onChange={(v) => setCur({ highlight: v }, "h")} label="Highlight circle around the cursor" />
          {cur.highlight && <Color label="Circle" value={cur.highlight_color || "#FFD400"} onChange={(v) => setCur({ highlight_color: v }, "hc")} />}
          <Toggle on={!!cur.spotlight} onChange={(v) => setCur({ spotlight: v }, "s")} label="Spotlight (dim everything else)" />
          {(cur.highlight || cur.spotlight) && <Num label="Size" unit="×" min={0.5} max={2.5} step={0.1} value={cur.size || 1} onChange={(v) => setCur({ size: v }, "sz")} />}
          <Num label="Timing" unit="s" min={-3} max={3} step={0.05} value={cur.offset || 0} onChange={(v) => setCur({ offset: v }, "o")} />
          <p className="muted small">If ripples appear before or after the real click, nudge Timing until they match.</p>
        </>
      )}
    </div>
  );
}

// ================================================================ properties of a zoom / text / shape / caption
export function ElProps({ p, e, commit }: { p: EProject; e: El; commit: Commit }) {
  const fontMap = useStore((s) => s.catalog?.fonts);
  const fonts = Object.keys(fontMap || {});
  const set = (patch: Partial<El>, tag: string) => commit((q) => ({ ...q, els: (q.els || []).map((o) => (o.id === e.id ? { ...o, ...patch } : o)) }), tag + e.id);
  const timing = <><h4>Timing</h4><div className="ve-facts"><span>Starts</span><b>{tc(e.start, p.fps)}</b><span>Length</span><b>{tc(e.dur, p.fps)}</b></div>
    <Num label="Length" unit="s" min={0.2} max={60} step={0.1} value={e.dur} onChange={(v) => set({ dur: v }, "dur")} /></>;
  const anim = (
    <>
      <h4>Animation</h4>
      <Field label="In"><Select value={e.anim_in?.type || "none"} options={ANIMS_IN} onChange={(v) => set({ anim_in: { type: v, dur: e.anim_in?.dur || 0.4 } }, "ai")} /></Field>
      {e.anim_in?.type && e.anim_in.type !== "none" && <Num label="In" unit="s" min={0.1} max={2} step={0.05} value={e.anim_in.dur} onChange={(v) => set({ anim_in: { ...e.anim_in!, dur: v } }, "aid")} />}
      <Field label="While showing"><Select value={e.anim_loop?.type || "none"} options={ANIMS_LOOP} onChange={(v) => set({ anim_loop: { type: v, dur: 0 } }, "al")} /></Field>
      <Field label="Out"><Select value={e.anim_out?.type || "none"} options={ANIMS_OUT} onChange={(v) => set({ anim_out: { type: v, dur: e.anim_out?.dur || 0.4 } }, "ao")} /></Field>
      {e.anim_out?.type && e.anim_out.type !== "none" && <Num label="Out" unit="s" min={0.1} max={2} step={0.05} value={e.anim_out.dur} onChange={(v) => set({ anim_out: { ...e.anim_out!, dur: v } }, "aod")} />}
    </>
  );
  const pin = <Toggle on={!!e.pin} onChange={(v) => set({ pin: v }, "pin")} label="Stay fixed when the picture zooms" hint="On: a title that never moves. Off: sticks to the spot it points at." />;
  if (e.kind === "zoom") return (
    <div className="ve-pbodyscroll">
      <p className="muted small ve-tip">Drag the frame on the canvas to aim the zoom. Drag the block on the timeline to change when it happens.</p>
      <Num label="Zoom" unit="×" min={1.1} max={4} step={0.05} value={e.z || 1.6} onChange={(v) => set({ z: v, cx: clamp(e.cx ?? 0.5, 0.5 / v, 1 - 0.5 / v), cy: clamp(e.cy ?? 0.5, 0.5 / v, 1 - 0.5 / v) }, "z")} />
      <Num label="Across" unit="%" min={0} max={100} value={(e.cx ?? 0.5) * 100} onChange={(v) => set({ cx: v / 100 }, "cx")} />
      <Num label="Down" unit="%" min={0} max={100} value={(e.cy ?? 0.5) * 100} onChange={(v) => set({ cy: v / 100 }, "cy")} />
      <Num label="Glide" unit="s" min={0.15} max={1.5} step={0.05} value={e.ease || 0.5} onChange={(v) => set({ ease: v }, "e")} />
      {timing}
    </div>
  );
  if (e.kind === "caption") return (
    <div className="ve-pbodyscroll">
      <Field label="Words"><textarea className="input ve-textarea" value={e.text || ""} onChange={(ev) => set({ text: ev.target.value }, "txt")} rows={3} /></Field>
      <p className="muted small">The look of all captions is set under the last tab on the left (Captions and cursor).</p>
      {timing}
    </div>
  );
  if (e.kind === "text") return (
    <div className="ve-pbodyscroll">
      <Field label="Words"><textarea className="input ve-textarea" value={e.text || ""} onChange={(ev) => set({ text: ev.target.value }, "txt")} rows={3} autoFocus /></Field>
      <Field label="Font"><Select value={e.font || "Poppins"} options={(fonts.length ? fonts : ["Poppins"]).map((f) => [f, f] as [string, string])} onChange={(v) => set({ font: v }, "f")} /></Field>
      <Num label="Size" min={1.5} max={40} step={0.1} value={(e.size || 0.06) * 100} onChange={(v) => set({ size: v / 100 }, "s")} />
      <Color label="Colour" value={e.color || "#FFFFFF"} onChange={(v) => set({ color: v }, "c")} />
      <div className="ve-quick"><button className={e.bold ? "on" : ""} onClick={() => set({ bold: !e.bold }, "b")}>Bold</button><button className={e.italic ? "on" : ""} onClick={() => set({ italic: !e.italic }, "i")}>Italic</button>
        <button className={e.upper ? "on" : ""} onClick={() => set({ upper: !e.upper }, "u")}>CAPS</button><button onClick={() => set({ x: 0.5, y: 0.5, rot: 0 }, "ctr")}>Centre</button></div>
      <Num label="Rotate" unit="°" min={-180} max={180} value={e.rot || 0} onChange={(v) => set({ rot: v }, "r")} />
      <h4>Style</h4>
      <Toggle on={!!e.box} onChange={(v) => set({ box: v }, "bx")} label="Box behind the text" />
      {e.box ? <><Color label="Box" value={e.box_color || "#000000"} onChange={(v) => set({ box_color: v }, "bc")} />
        <Num label="Box" unit="%" min={10} max={100} value={(e.box_alpha ?? 0.6) * 100} onChange={(v) => set({ box_alpha: v / 100 }, "ba")} />
        <Num label="Padding" min={0} max={60} value={e.box_pad ?? 14} onChange={(v) => set({ box_pad: v }, "bp")} /></>
        : <><Num label="Outline" min={0} max={16} value={e.stroke || 0} onChange={(v) => set({ stroke: v }, "st")} />
          {(e.stroke || 0) > 0 && <Color label="Outline" value={e.stroke_color || "#000000"} onChange={(v) => set({ stroke_color: v }, "sc")} />}
          <Num label="Shadow" min={0} max={14} value={e.shadow || 0} onChange={(v) => set({ shadow: v }, "sh")} /></>}
      {anim}{pin}{timing}
    </div>
  );
  return (
    <div className="ve-pbodyscroll">
      {e.shape !== "blur" && <Color label="Colour" value={e.color || "#FF3D6E"} onChange={(v) => set({ color: v }, "c")} />}
      {["rect", "ellipse", "arrow", "line"].includes(e.shape || "") && <Num label="Thickness" min={1} max={40} value={e.width || 6} onChange={(v) => set({ width: v }, "w")} />}
      {e.shape === "highlight" && <Num label="Strength" unit="%" min={10} max={100} value={(e.alpha ?? 0.45) * 100} onChange={(v) => set({ alpha: v / 100 }, "a")} />}
      {e.shape === "blur" && <Num label="Blur" unit="%" min={10} max={100} value={(e.strength ?? 0.6) * 100} onChange={(v) => set({ strength: v / 100 }, "bs")} />}
      {e.shape === "step" && <Num label="Number" min={1} max={99} value={e.n || 1} onChange={(v) => set({ n: Math.round(v) }, "n")} />}
      <Num label="Width" unit="%" min={1} max={100} value={(e.w ?? 0.2) * 100} onChange={(v) => set({ w: v / 100 }, "ww")} />
      <Num label="Height" unit="%" min={1} max={100} value={(e.h ?? 0.2) * 100} onChange={(v) => set({ h: v / 100 }, "hh")} />
      {e.shape !== "blur" && <Num label="Rotate" unit="°" min={-180} max={180} value={e.rot || 0} onChange={(v) => set({ rot: v }, "r")} />}
      {e.shape !== "blur" && anim}
      {e.shape !== "blur" && pin}
      {timing}
    </div>
  );
}
