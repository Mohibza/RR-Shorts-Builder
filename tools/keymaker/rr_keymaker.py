"""RR Key Maker: makes Monthly / Annual / Lifetime keys for Rebels Revolt Shorts.

KEEP THIS FOR YOURSELF. Never put it in the installer or send it to anyone.
Your private signing key lives in  %USERPROFILE%\\RR-License-Keys\\signing_key.txt  (outside the project and
outside Git). Back that file up somewhere safe: if you lose it you can't make keys for existing installs,
and anyone who gets it can make unlimited keys.

    python rr_keymaker.py                     (window)
    python rr_keymaker.py --device RRD-ABCD-EFGH-IJKL-MNOP --plan monthly [--name "Ali"] [--days 31]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from shortsforge import ed25519, licensing  # noqa: E402

KEYS_DIR = Path(os.environ.get("RR_KEYS_DIR") or (Path.home() / "RR-License-Keys"))
PRIV = KEYS_DIR / "signing_key.txt"
LOG = KEYS_DIR / "issued_keys.csv"
PUB_FILE = ROOT / "shortsforge" / "license_pub.py"


def load_private() -> str:
    if not PRIV.exists():
        return ""
    m = re.search(r"[0-9a-f]{64}", PRIV.read_text(encoding="utf-8"))
    return m.group(0) if m else ""


def setup() -> str:
    """Create the signing key pair once and put the public half into the app."""
    if load_private():
        priv = load_private()
    else:
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        priv = secrets.token_hex(32)
        PRIV.write_text("RR Shorts license SIGNING KEY - keep secret, back it up, never share or commit it.\n"
                        + priv + "\n", encoding="utf-8")
    pub = ed25519.public_key(bytes.fromhex(priv)).hex()
    txt = PUB_FILE.read_text(encoding="utf-8")
    txt = re.sub(r'PUBLIC_KEY_HEX = "[0-9a-f]*"', f'PUBLIC_KEY_HEX = "{pub}"', txt)
    PUB_FILE.write_text(txt, encoding="utf-8")
    return pub


def make(device: str, plan: str, name: str = "", days: int | None = None) -> tuple[str, str]:
    priv = load_private()
    if not priv:
        raise SystemExit("No signing key yet: run Set up first.")
    kid = secrets.randbits(32)
    key = licensing.make_key(priv, plan, device, days, kid)
    k = licensing.unpack(licensing.decode_key(key)[0])
    ends = "never" if k.expires_at is None else time.strftime("%Y-%m-%d", time.localtime(k.expires_at))
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["issued", "customer", "device_id", "plan", "ends", "key_id", "key"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M"), name, device.strip().upper(), plan, ends, f"{kid:08x}", key])
    return key, ends


def gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = tk.Tk()
    root.title("RR Key Maker")
    root.geometry("760x420")
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    status = tk.StringVar()

    def refresh():
        if load_private():
            status.set(f"Signing key: {PRIV}   (back this file up!)")
        else:
            status.set("No signing key yet. Click 'Set up' once, then rebuild the installer.")
    ttk.Label(frm, textvariable=status, wraplength=720).grid(row=0, column=0, columnspan=3, sticky="w")

    def do_setup():
        pub = setup()
        refresh()
        messagebox.showinfo("Set up", "Done. The app now accepts keys from this Key Maker.\n\n"
                            "Rebuild the installer (build_installer.bat) so customers get it.\n"
                            f"Public key: {pub[:16]}…\n\nBack up {PRIV} somewhere safe (USB / password manager).")
    ttk.Button(frm, text="Set up (first time only)", command=do_setup).grid(row=0, column=3, sticky="e")
    ttk.Label(frm, text="Customer name / email").grid(row=1, column=0, sticky="w", pady=(18, 4))
    name = ttk.Entry(frm, width=40)
    name.grid(row=1, column=1, columnspan=2, sticky="we", pady=(18, 4))
    ttk.Label(frm, text="Device ID (from their Settings → License)").grid(row=2, column=0, sticky="w", pady=4)
    dev = ttk.Entry(frm, width=40)
    dev.grid(row=2, column=1, columnspan=2, sticky="we", pady=4)
    ttk.Label(frm, text="Plan").grid(row=3, column=0, sticky="w", pady=4)
    plan = ttk.Combobox(frm, values=["monthly", "annual", "lifetime"], state="readonly", width=12)
    plan.set("monthly")
    plan.grid(row=3, column=1, sticky="w", pady=4)
    ttk.Label(frm, text="Days (empty = 31 / 366)").grid(row=4, column=0, sticky="w", pady=4)
    days = ttk.Entry(frm, width=8)
    days.grid(row=4, column=1, sticky="w", pady=4)
    out = tk.Text(frm, height=5, width=90, wrap="char")
    out.grid(row=6, column=0, columnspan=4, pady=10, sticky="we")

    def do_make():
        try:
            key, ends = make(dev.get(), plan.get(), name.get(), int(days.get()) if days.get().strip() else None)
        except SystemExit as e:
            messagebox.showerror("RR Key Maker", str(e))
            return
        except Exception as e:
            messagebox.showerror("RR Key Maker", str(e))
            return
        out.delete("1.0", "end")
        out.insert("1.0", key)
        root.clipboard_clear()
        root.clipboard_append(key)
        messagebox.showinfo("Key ready", f"{plan.get().title()} key (ends: {ends}) copied to the clipboard.\n"
                            f"Logged in {LOG}.")
    ttk.Button(frm, text="Make key + copy", command=do_make).grid(row=5, column=0, sticky="w", pady=8)
    ttk.Label(frm, text="Monthly = 31 days, Annual = 366 days, each + 3 days grace. Keys only work on the PC "
                        "with that Device ID.", wraplength=720, foreground="#555").grid(row=7, column=0, columnspan=4, sticky="w")
    refresh()
    root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--device", default="")
    ap.add_argument("--plan", default="monthly", choices=["monthly", "annual", "lifetime"])
    ap.add_argument("--name", default="")
    ap.add_argument("--days", type=int)
    a = ap.parse_args()
    if a.setup:
        print("Public key installed:", setup())
    elif a.device:
        key, ends = make(a.device, a.plan, a.name, a.days)
        print(key)
        print("ends:", ends)
    else:
        gui()
