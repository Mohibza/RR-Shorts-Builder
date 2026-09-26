"""Headless mode:  python -m shortsforge <url|file|folder> [--count N] [--min 20] [--max 59] [--out DIR]"""
from __future__ import annotations

import argparse
import sys

from .config import Settings
from .downloader import expand
from .pipeline import Pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shortsforge", description="Turn long videos into styled Shorts.")
    ap.add_argument("source", help="YouTube video / playlist / channel URL, a video file, or a folder")
    ap.add_argument("--count", type=int, help="Shorts per video")
    ap.add_argument("--min", type=float, dest="min_d", help="min seconds")
    ap.add_argument("--max", type=float, dest="max_d", help="max seconds")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--limit", type=int, default=0, help="max videos from a playlist/channel")
    ap.add_argument("--style", help="caption style key or 'random'")
    a = ap.parse_args(argv)
    s = Settings.load()
    if a.count:
        s.shorts_per_video = a.count
    if a.min_d:
        s.min_duration = a.min_d
    if a.max_d:
        s.max_duration = a.max_d
    if a.out:
        s.output_dir = a.out
    if a.style:
        s.caption_style = a.style
    items = expand(a.source, s.cookies_browser)
    if a.limit:
        items = items[: a.limit]
    last = {"k": ""}

    def prog(stage, f, d):
        k = f"{stage} {int(f * 100)}%"
        if k != last["k"]:
            last["k"] = k
            print(f"\r{k:<40} {d[:40]:<40}", end="", flush=True)

    for it in items:
        Pipeline(s, log=lambda m: print("\n" + m), progress=prog).process(it)
    print()


if __name__ == "__main__":
    sys.exit(main())
