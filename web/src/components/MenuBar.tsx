// The application menu bar (File, Edit, View, Record, Help). It sits right under the window's title bar, in the same
// colour, so the two read as one frame. Every entry sends a command the open page listens to.
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { cmd, Cmd, SHORTCUTS } from "../lib/edit";
import { go, setState, toast, useStore } from "../lib/store";
import { Icon } from "./Icon";
import { Modal } from "./ui";

type Entry = { label: string; key?: string; run?: () => void; off?: boolean; sep?: boolean };

export function MenuBar() {
  const page = useStore((s) => s.page);
  const editing = useStore((s) => s.editing);
  const pid = useStore((s) => s.editProject);
  const name = useStore((s) => s.editName);
  const dirty = useStore((s) => s.editDirty);
  const version = useStore((s) => s.status?.version);
  const online = useStore((s) => s.online);
  const [open, setOpen] = useState("");
  const [dialog, setDialog] = useState<"" | "keys" | "about">("");
  const bar = useRef<HTMLDivElement>(null);
  const inEditor = page === "edit" && !editing && !!pid;
  const c = (x: Cmd) => () => cmd(x);
  const toEditor = (x: Cmd) => () => { setState({ editProject: "" }); go("edit", { editing: null }); setTimeout(() => cmd(x), 80); };
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "o" && !document.querySelector(".modal-back")) { e.preventDefault(); toEditor("open")(); } };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, []);
  const nav = (p: string) => () => go(p, { editing: null });

  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { if (!bar.current?.contains(e.target as Node)) setOpen(""); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(""); };
    window.addEventListener("pointerdown", down); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("pointerdown", down); window.removeEventListener("keydown", key); };
  }, [open]);

  const menus: [string, Entry[]][] = [
    ["File", [
      { label: "Open video…", key: "Ctrl+O", run: toEditor("open") },
      { label: "All projects", run: () => { cmd("home"); go("edit", { editing: null }); } },
      { label: "", sep: true },
      { label: "Import media…", key: "Ctrl+I", run: c("import"), off: !inEditor },
      { label: "Save", key: "Ctrl+S", run: c("save"), off: !inEditor },
      { label: "Export video…", key: "Ctrl+E", run: c("export"), off: !inEditor },
      { label: "", sep: true },
      { label: "Open output folder", run: () => api("/api/open", { path: "output" }).catch((e) => toast(e.message, "error")) },
    ]],
    ["Edit", [
      { label: "Undo", key: "Ctrl+Z", run: c("undo"), off: !inEditor },
      { label: "Redo", key: "Ctrl+Y", run: c("redo"), off: !inEditor },
      { label: "", sep: true },
      { label: "Split at playhead", key: "S", run: c("split"), off: !inEditor },
      { label: "Delete", key: "Del", run: c("delete"), off: !inEditor },
      { label: "Ripple delete", key: "Shift+Del", run: c("ripple"), off: !inEditor },
      { label: "Duplicate", key: "Ctrl+D", run: c("duplicate"), off: !inEditor },
      { label: "", sep: true },
      { label: "Copy", key: "Ctrl+C", run: c("copy"), off: !inEditor },
      { label: "Paste", key: "Ctrl+V", run: c("paste"), off: !inEditor },
      { label: "Select all", key: "Ctrl+A", run: c("selectAll"), off: !inEditor },
    ]],
    ["View", [
      { label: "Create Shorts", run: nav("create") }, { label: "Studio (recorder)", run: nav("studio") }, { label: "Editor", run: nav("edit") },
      { label: "Clips", run: nav("projects") }, { label: "Library", run: nav("library") }, { label: "Publish", run: nav("publish") },
      { label: "Music", run: nav("music") }, { label: "Settings", run: nav("settings") },
      { label: "", sep: true },
      { label: "Zoom timeline in", key: "+", run: c("zoomIn"), off: !inEditor },
      { label: "Zoom timeline out", key: "−", run: c("zoomOut"), off: !inEditor },
      { label: "Fit timeline", key: "Shift+Z", run: c("zoomFit"), off: !inEditor },
    ]],
    ["Record", [
      { label: "New screen recording…", run: nav("studio") },
      { label: "Recordings", run: nav("studio") },
    ]],
    ["Help", [
      { label: "Keyboard shortcuts", run: () => setDialog("keys") },
      { label: "Licence and plan", run: () => go("settings", { settingsSec: "license", editing: null }) },
      { label: "About", run: () => setDialog("about") },
    ]],
  ];

  return (
    <header className="menubar" ref={bar}>
      <div className="mb-mark"><Icon name="play" size={11} /></div>
      <nav className="mb-menus">
        {menus.map(([title, items]) => (
          <div key={title} className={`mb-menu ${open === title ? "open" : ""}`}>
            <button onClick={() => setOpen(open === title ? "" : title)} onPointerEnter={() => { if (open) setOpen(title); }}>{title}</button>
            {open === title && (
              <div className="mb-drop">
                {items.map((it, i) => it.sep ? <hr key={i} /> : (
                  <button key={i} disabled={it.off} onClick={() => { setOpen(""); it.run?.(); }}><span>{it.label}</span>{it.key && <kbd>{it.key}</kbd>}</button>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>
      <div className="mb-doc">{inEditor && name ? <><b>{name}</b><i>{dirty ? "Saving…" : "Saved"}</i></> : null}</div>
      <div className="mb-right"><span className={`dot ${online ? "ok" : "bad"}`} />{online ? "Engine ready" : "Reconnecting…"}</div>
      {dialog === "keys" && (
        <Modal title="Keyboard shortcuts" width={560} onClose={() => setDialog("")}>
          <div className="keys">{SHORTCUTS.map(([k, what]) => <div key={k}><kbd>{k}</kbd><span>{what}</span></div>)}</div>
        </Modal>
      )}
      {dialog === "about" && (
        <Modal title="About" width={420} onClose={() => setDialog("")}>
          <div className="about"><div className="logo-mark"><Icon name="play" size={22} /></div><h2>Rebels Revolt Shorts</h2>
            <p className="muted">Version {version || ""}</p><p className="muted small">Shorts maker, screen recorder and video editor. Everything runs on this PC.</p></div>
        </Modal>
      )}
    </header>
  );
}
