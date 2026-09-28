"""Caption fonts: bundled + downloaded (all free/open licensed Google Fonts)."""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .config import BUNDLED_FONTS, FONTS_DIR

GF = "https://github.com/google/fonts/raw/main/"

# family name (as used in ASS) -> (file name, url)
FONT_SOURCES: dict[str, tuple[str, str]] = {
    # all of these ship inside assets/fonts; the URL is only a fallback if a file is missing
    "Anton": ("Anton-Regular.ttf", GF + "ofl/anton/Anton-Regular.ttf"),
    "Bebas Neue": ("BebasNeue-Regular.ttf", GF + "ofl/bebasneue/BebasNeue-Regular.ttf"),
    "Luckiest Guy": ("LuckiestGuy-Regular.ttf", GF + "apache/luckiestguy/LuckiestGuy-Regular.ttf"),
    "Bangers": ("Bangers-Regular.ttf", GF + "ofl/bangers/Bangers-Regular.ttf"),
    "Permanent Marker": ("PermanentMarker-Regular.ttf", GF + "apache/permanentmarker/PermanentMarker-Regular.ttf"),
    "Poppins": ("Poppins-Bold.ttf", GF + "ofl/poppins/Poppins-Bold.ttf"),
    "Poppins Black": ("Poppins-Black.ttf", GF + "ofl/poppins/Poppins-Black.ttf"),
    "Archivo Black": ("ArchivoBlack-Regular.ttf", GF + "ofl/archivoblack/ArchivoBlack-Regular.ttf"),
    "Rubik Mono One": ("RubikMonoOne-Regular.ttf", GF + "ofl/rubikmonoone/RubikMonoOne-Regular.ttf"),
    "Noto Nastaliq Urdu": ("NotoNastaliqUrdu-Bold.ttf", GF + "ofl/notonastaliqurdu/NotoNastaliqUrdu%5Bwght%5D.ttf"),
    "Noto Naskh Arabic": ("NotoNaskhArabic-Bold.ttf", GF + "ofl/notonaskharabic/NotoNaskhArabic%5Bwght%5D.ttf"),
    "Noto Sans Devanagari": ("NotoSansDevanagari-Bold.ttf",
                             GF + "ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth,wght%5D.ttf"),
}

GF2 = "https://raw.githubusercontent.com/google/fonts/main/"
FONT_SOURCES.update({
    "Bungee": ("Bungee-Regular.ttf", GF2 + "ofl/bungee/Bungee-Regular.ttf"),
    "Righteous": ("Righteous-Regular.ttf", GF2 + "ofl/righteous/Righteous-Regular.ttf"),
    "Pacifico": ("Pacifico-Regular.ttf", GF2 + "ofl/pacifico/Pacifico-Regular.ttf"),
    "Lobster": ("Lobster-Regular.ttf", GF2 + "ofl/lobster/Lobster-Regular.ttf"),
    "Russo One": ("RussoOne-Regular.ttf", GF2 + "ofl/russoone/RussoOne-Regular.ttf"),
    "Black Ops One": ("BlackOpsOne-Regular.ttf", GF2 + "ofl/blackopsone/BlackOpsOne-Regular.ttf"),
    "Staatliches": ("Staatliches-Regular.ttf", GF2 + "ofl/staatliches/Staatliches-Regular.ttf"),
    "Titan One": ("TitanOne-Regular.ttf", GF2 + "ofl/titanone/TitanOne-Regular.ttf"),
    "Chewy": ("Chewy-Regular.ttf", GF2 + "apache/chewy/Chewy-Regular.ttf"),
    "Bowlby One": ("BowlbyOne-Regular.ttf", GF2 + "ofl/bowlbyone/BowlbyOne-Regular.ttf"),
    "Passion One": ("PassionOne-Regular.ttf", GF2 + "ofl/passionone/PassionOne-Regular.ttf"),
    "Lilita One": ("LilitaOne-Regular.ttf", GF2 + "ofl/lilitaone/LilitaOne-Regular.ttf"),
    "Monoton": ("Monoton-Regular.ttf", GF2 + "ofl/monoton/Monoton-Regular.ttf"),
    "Kanit Black": ("Kanit-Black.ttf", GF2 + "ofl/kanit/Kanit-Black.ttf"),
    "Caveat Brush": ("CaveatBrush-Regular.ttf", GF2 + "ofl/caveatbrush/CaveatBrush-Regular.ttf"),
    "Shrikhand": ("Shrikhand-Regular.ttf", GF2 + "ofl/shrikhand/Shrikhand-Regular.ttf"),
    "Bungee Shade": ("BungeeShade-Regular.ttf", GF2 + "ofl/bungeeshade/BungeeShade-Regular.ttf"),
    "Press Start 2P": ("PressStart2P-Regular.ttf", GF2 + "ofl/pressstart2p/PressStart2P-Regular.ttf"),
    "Sigmar One": ("SigmarOne-Regular.ttf", GF2 + "ofl/sigmarone/SigmarOne-Regular.ttf"),
    "Alfa Slab One": ("AlfaSlabOne-Regular.ttf", GF2 + "ofl/alfaslabone/AlfaSlabOne-Regular.ttf"),
    "Special Elite": ("SpecialElite-Regular.ttf", GF2 + "apache/specialelite/SpecialElite-Regular.ttf"),
    "Satisfy": ("Satisfy-Regular.ttf", GF2 + "apache/satisfy/Satisfy-Regular.ttf"),
    "Boogaloo": ("Boogaloo-Regular.ttf", GF2 + "ofl/boogaloo/Boogaloo-Regular.ttf"),
})

# If a font can't be downloaded, Windows always has these.
WINDOWS_FALLBACK = {
    "Anton": "Impact", "Bebas Neue": "Impact", "Luckiest Guy": "Arial Black",
    "Bangers": "Impact", "Permanent Marker": "Comic Sans MS", "Poppins": "Segoe UI",
    "Poppins Black": "Arial Black", "Archivo Black": "Arial Black", "Rubik Mono One": "Arial Black",
    "Noto Nastaliq Urdu": "Arial", "Noto Naskh Arabic": "Arial", "Noto Sans Devanagari": "Nirmala UI",
}
WINDOWS_FALLBACK.update({ "Bungee": "Impact", "Righteous": "Segoe UI", "Pacifico": "Segoe Script", "Lobster": "Segoe Script", "Russo One": "Arial Black", "Black Ops One": "Impact", "Staatliches": "Impact", "Titan One": "Arial Black", "Chewy": "Comic Sans MS", "Bowlby One": "Arial Black", "Passion One": "Impact", "Lilita One": "Arial Black", "Monoton": "Impact", "Kanit Black": "Arial Black", "Caveat Brush": "Comic Sans MS", "Shrikhand": "Arial Black", "Bungee Shade": "Impact", "Press Start 2P": "Consolas", "Sigmar One": "Arial Black", "Alfa Slab One": "Rockwell", "Special Elite": "Courier New", "Satisfy": "Segoe Script", "Boogaloo": "Comic Sans MS",})


def fonts_dir() -> Path:
    """Directory handed to libass (fontsdir=). Bundled fonts are copied in."""
    if BUNDLED_FONTS.exists():
        for f in BUNDLED_FONTS.iterdir():
            if f.suffix.lower() in (".ttf", ".otf"):
                dst = FONTS_DIR / f.name
                if not dst.exists():
                    try:
                        shutil.copy2(f, dst)
                    except OSError:
                        pass
    return FONTS_DIR


def available(family: str) -> bool:
    src = FONT_SOURCES.get(family)
    return bool(src) and (FONTS_DIR / src[0]).exists()


def resolve(family: str) -> str:
    """Return the family if installed, else a system fallback name."""
    if family not in FONT_SOURCES or available(family):
        return family
    return WINDOWS_FALLBACK.get(family, "Arial")


def missing_fonts() -> list[str]:
    fonts_dir()
    return [fam for fam, (fn, _) in FONT_SOURCES.items() if not (FONTS_DIR / fn).exists()]


def ensure_fonts(log: Optional[Callable[[str], None]] = None) -> list[str]:
    """Download any missing fonts. Returns list of families that failed."""
    failed = []
    for fam in missing_fonts():
        fn, url = FONT_SOURCES[fam]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RRShortsBuilder/1.5"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if len(data) < 1000:
                raise IOError("empty download")
            (FONTS_DIR / fn).write_bytes(data)
            if log:
                log(f"Font installed: {fam}")
        except Exception as e:  # network optional
            failed.append(fam)
            if log:
                log(f"Could not download font {fam} ({e}); using {WINDOWS_FALLBACK.get(fam, 'Arial')}")
    return failed


# ---------------------------------------------------------------------------
# Text measuring (exact per-glyph advance widths, bundled in assets/fonts/metrics.json)
_METRICS: dict = {}


def _metrics() -> dict:
    global _METRICS
    if not _METRICS:
        import json
        try:
            _METRICS = json.loads((BUNDLED_FONTS / "metrics.json").read_text(encoding="utf-8"))
        except Exception:
            _METRICS = {"_": {}}
    return _METRICS


def text_width(family: str, text: str, size: float) -> float:
    """Rendered width in pixels (ASS/libass sizing) of `text` in `family` at `size`."""
    fn = FONT_SOURCES.get(family, ("", ""))[0]
    m = _metrics().get(fn)
    if not m:
        return len(text) * size * 0.5
    w, other = m["w"], m["other"]
    return size * sum(w.get(c, other) for c in text)
