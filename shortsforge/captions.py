"""Animated caption styles + text templates, rendered as ASS subtitles (libass)."""
from __future__ import annotations

import random
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from . import fonts

W, H = 1080, 1920

# ---------------------------------------------------------------------------
# Caption styles
# mode: active (word highlight), karaoke (fill sweep), typewriter, oneword, static, hollow
# ---------------------------------------------------------------------------
CAPTION_STYLES: dict[str, dict] = {
    "hormozi": dict(name="Bold Highlight", font="Anton", size=200, upper=True, chunk=3, maxchars=18,
                    primary="#FFFFFF", outline="#000000", bord=9, shadow=4, active="#FFE400", emph="#3DFF6B",
                    mode="active", pop=True, bounce=True),
    "beast": dict(name="Beast Mode", font="Luckiest Guy", size=190, upper=True, chunk=2, maxchars=16,
                  primary="#FFFFFF", outline="#000000", bord=11, shadow=6, active="#FF2E4D", emph="#FFD400",
                  mode="active", pop=True, bounce=True, tilt=True),
    "karaoke": dict(name="Karaoke Sweep", font="Poppins Black", size=130, upper=False, chunk=4, maxchars=26,
                    primary="#00E5FF", secondary="#FFFFFF", outline="#000000", bord=6, shadow=3,
                    mode="karaoke", bounce=False),
    "boxed": dict(name="Boxed Pop", font="Poppins Black", size=160, upper=True, chunk=3, maxchars=20,
                  primary="#FFFFFF", outline="#FF2E63", bord=18, shadow=0, active="#FFE400", emph="#FFFFFF",
                  mode="active", box=True, bounce=True),
    "minimal": dict(name="Clean Minimal", font="Poppins", size=120, upper=False, chunk=4, maxchars=30,
                    primary="#FFFFFF", outline="#000000", bord=2, shadow=5, mode="static", fade=True),
    "neon": dict(name="Neon Glow", font="Bebas Neue", size=220, upper=True, chunk=3, maxchars=18,
                 primary="#FFFFFF", outline="#FF2BD6", bord=4, shadow=0, active="#8CFFFB", emph="#FFFFFF",
                 mode="active", glow="#FF2BD6", bounce=True),
    "typewriter": dict(name="Typewriter", font="Poppins Black", size=130, upper=False, chunk=4, maxchars=26,
                       primary="#FFFFFF", outline="#000000", bord=6, shadow=3, active="#FFB800",
                       emph="#FFFFFF", mode="typewriter"),
    "oneword": dict(name="One Word Punch", font="Anton", size=240, upper=True, chunk=1, maxchars=12,
                    primary="#FFFFFF", outline="#000000", bord=10, shadow=6, mode="oneword",
                    palette=["#FFFFFF", "#FFE400", "#3DFF6B", "#FF4D6D", "#4DD2FF"]),
    "comic": dict(name="Comic Burst", font="Bangers", size=200, upper=True, chunk=2, maxchars=16,
                  primary="#FFE600", outline="#000000", bord=10, shadow=7, active="#FFFFFF", emph="#FF4D4D",
                  mode="active", pop=True, bounce=True, tilt=True),
    "marker": dict(name="Marker Hand", font="Permanent Marker", size=170, upper=False, chunk=3, maxchars=20,
                   primary="#FFFFFF", outline="#101010", bord=7, shadow=4, active="#FF5FA2", emph="#FFE66D",
                   mode="active", bounce=True, tilt=True),
    "slide": dict(name="Slide Up", font="Archivo Black", size=170, upper=True, chunk=3, maxchars=18,
                  primary="#FFFFFF", outline="#000000", bord=7, shadow=5, active="#7CFF4F", emph="#FFD23F",
                  mode="active", slide=True),
    "pill": dict(name="Highlight Box", font="Poppins Black", size=170, upper=True, chunk=3, maxchars=20,
                 primary="#FFFFFF", outline="#000000", bord=6, shadow=3, active="#FFFFFF", emph="#FFE66D",
                 mode="active", hl_box="#7C3BFF", bounce=True),
    "card": dict(name="Caption Card", font="Poppins", size=115, upper=False, chunk=4, maxchars=28,
                 primary="#FFFFFF", outline="#0B0B10", bord=20, shadow=0, active="#FFD84D", emph="#FFFFFF",
                 mode="active", box=True, box_alpha=0.35, fade=True),
    "hollow": dict(name="Hollow Fill", font="Anton", size=200, upper=True, chunk=3, maxchars=18,
                   primary="#FFE400", outline="#FFFFFF", bord=5, shadow=0, mode="hollow", bounce=True),
}

# Hook / title templates shown at the start (or the whole clip)
HOOK_STYLES: dict[str, dict] = {
    "banner_white": dict(name="White Banner", font="Poppins Black", size=124, color="#111111", box="#FFFFFF",
                         upper=True, dur=4.0, y=300, anim="drop"),
    "yellow_impact": dict(name="Yellow Impact", font="Anton", size=170, color="#FFE400", outline="#000000",
                          bord=8, upper=True, dur=3.5, y=330, anim="pop"),
    "red_tag": dict(name="Red Tag", font="Archivo Black", size=118, color="#FFFFFF", box="#E3122C",
                    upper=True, dur=4.0, y=300, anim="slide"),
    "headline": dict(name="Dark Headline", font="Poppins Black", size=124, color="#FFFFFF",
                     box="#000000", box_alpha=0.25, upper=True, dur=4.0, y=300, anim="drop"),
    "glitch": dict(name="Glitch RGB", font="Bebas Neue", size=180, color="#FFFFFF", outline="#000000", bord=3,
                   upper=True, dur=3.5, y=330, anim="glitch"),
    "comic_burst": dict(name="Comic Burst", font="Bangers", size=175, color="#FFE600", outline="#000000",
                        bord=9, upper=True, dur=3.5, y=330, anim="pop", tilt=-5),
    "marker_note": dict(name="Marker Note", font="Permanent Marker", size=124, color="#FFFFFF", box="#111111",
                        upper=False, dur=4.0, y=310, anim="drop", tilt=2),
    "neon_sign": dict(name="Neon Sign", font="Bebas Neue", size=170, color="#FFFFFF", outline="#00F0FF",
                      bord=3, glow="#00F0FF", upper=True, dur=3.5, y=330, anim="flicker"),
}

CTA_STYLES: dict[str, dict] = {
    "pill": dict(name="Accent Pill", font="Poppins Black", size=80, color="#FFFFFF", box="#FF2E63", anim="slide"),
    "pulse": dict(name="Pulse Text", font="Anton", size=110, color="#FFE400", outline="#000000", bord=7,
                  anim="pulse"),
    "subscribe": dict(name="Red Subscribe", font="Archivo Black", size=74, color="#FFFFFF", box="#E3122C",
                      anim="pulse"),
    "clean": dict(name="Clean", font="Poppins", size=84, color="#FFFFFF", outline="#000000", bord=3,
                  anim="fade"),
}

# ---------------------------------------------------------------------------
# Template library (CapCut-style): more caption looks, hook headings and end cards.
# `cat` groups them in the editor. Every key below is understood by the ASS builder and the live preview.
_C = dict
CAPTION_STYLES.update({
    # bold / viral
    "bungee_pop": _C(name="Bungee Pop", font="Bungee", size=150, upper=True, chunk=2, maxchars=16, primary="#FFFFFF",
                     outline="#000000", bord=8, shadow=4, active="#00F0FF", emph="#FFE400", mode="active", pop=True, bounce=True),
    "lilita": _C(name="Lilita Bold", font="Lilita One", size=180, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                 outline="#000000", bord=8, shadow=4, active="#7CFF4F", emph="#FFE400", mode="active", pop=True, bounce=True),
    "russo": _C(name="Russo Slide", font="Russo One", size=150, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                outline="#000000", bord=7, shadow=4, active="#FF3D00", emph="#FFD23F", mode="active", slide=True),
    "slab_bold": _C(name="Slab Punch", font="Alfa Slab One", size=150, upper=False, chunk=3, maxchars=18,
                    primary="#FFFFFF", outline="#000000", bord=8, shadow=5, active="#FFB703", emph="#FFFFFF",
                    mode="active", pop=True),
    "staat": _C(name="Tall Headline", font="Staatliches", size=210, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                outline="#000000", bord=6, shadow=4, active="#FFD60A", emph="#FFFFFF", mode="active", slide=True),
    "righteous": _C(name="Righteous", font="Righteous", size=160, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                    outline="#0A0A0A", bord=7, shadow=4, active="#00E0FF", emph="#FFE400", mode="active", slide=True),
    "black_ops": _C(name="Gamer", font="Black Ops One", size=160, upper=True, chunk=2, maxchars=16, primary="#FFFFFF",
                    outline="#000000", bord=7, shadow=5, active="#FF2E2E", emph="#FFD400", mode="active", pop=True, tilt=True),
    "kanit_word": _C(name="Kanit Heavy", font="Kanit Black", size=170, upper=True, chunk=2, maxchars=16,
                     primary="#FFFFFF", outline="#000000", bord=9, shadow=5, active="#FFE400", emph="#3DFF6B",
                     mode="active", pop=True, bounce=True),
    # fun
    "titan": _C(name="Titan Bubble", font="Titan One", size=170, upper=False, chunk=3, maxchars=18, primary="#FFFFFF",
                outline="#1A1A1A", bord=9, shadow=5, active="#FF9F1C", emph="#FFE400", mode="active", pop=True, bounce=True),
    "sigmar": _C(name="Sigmar Fun", font="Sigmar One", size=150, upper=True, chunk=2, maxchars=16, primary="#FFE400",
                 outline="#000000", bord=9, shadow=5, active="#FFFFFF", emph="#FF4D6D", mode="active", pop=True, tilt=True),
    "chewy_fun": _C(name="Chewy Candy", font="Chewy", size=180, upper=False, chunk=3, maxchars=18, primary="#FFFFFF",
                    outline="#2B0A3D", bord=9, shadow=4, active="#FF6FD8", emph="#FFE400", mode="active", pop=True,
                    bounce=True, tilt=True),
    "boogaloo": _C(name="Boogaloo", font="Boogaloo", size=190, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                   outline="#000000", bord=8, shadow=4, active="#4DFFB8", emph="#FFE400", mode="active", bounce=True),
    "shrikhand": _C(name="Retro Sweet", font="Shrikhand", size=150, upper=False, chunk=3, maxchars=18, primary="#FFE8D6",
                    outline="#3D0C02", bord=8, shadow=5, active="#FF7B00", emph="#FFFFFF", mode="active", pop=True),
    "brush": _C(name="Brush Pen", font="Caveat Brush", size=170, upper=False, chunk=3, maxchars=20, primary="#FFFFFF",
                outline="#111111", bord=7, shadow=4, active="#FF5FA2", emph="#FFE66D", mode="active", bounce=True, tilt=True),
    # boxed
    "bowlby_box": _C(name="Bold Box", font="Bowlby One", size=140, upper=True, chunk=3, maxchars=18, primary="#FFFFFF",
                     outline="#000000", bord=18, shadow=0, active="#FFE400", emph="#FFFFFF", mode="active", box=True,
                     bounce=True),
    "passion_box": _C(name="Pink Box", font="Passion One", size=180, upper=True, chunk=3, maxchars=18,
                      primary="#FFFFFF", outline="#FF2E63", bord=18, shadow=0, active="#FFE400", emph="#FFFFFF",
                      mode="active", box=True, bounce=True),
    "pill_green": _C(name="Green Highlight", font="Poppins Black", size=160, upper=True, chunk=3, maxchars=20,
                     primary="#FFFFFF", outline="#000000", bord=6, shadow=3, active="#FFFFFF", emph="#FFE66D",
                     mode="active", hl_box="#16C172", bounce=True),
    "pill_red": _C(name="Red Highlight", font="Poppins Black", size=160, upper=True, chunk=3, maxchars=20,
                   primary="#FFFFFF", outline="#000000", bord=6, shadow=3, active="#FFFFFF", emph="#FFE66D",
                   mode="active", hl_box="#E3122C", bounce=True),
    "pill_yellow": _C(name="Yellow Marker", font="Poppins Black", size=160, upper=True, chunk=3, maxchars=20,
                      primary="#FFFFFF", outline="#000000", bord=6, shadow=3, active="#111111", emph="#FFFFFF",
                      mode="active", hl_box="#FFD400", bounce=True),
    "tiktok_box": _C(name="Classic Box", font="Poppins Black", size=120, upper=False, chunk=4, maxchars=26,
                     primary="#FFFFFF", outline="#000000", bord=16, shadow=0, active="#FFE400", emph="#FFFFFF",
                     mode="active", box=True),
    "card_light": _C(name="Light Card", font="Poppins", size=115, upper=False, chunk=4, maxchars=28, primary="#111111",
                     outline="#FFFFFF", bord=20, shadow=0, active="#E3122C", emph="#111111", mode="active", box=True,
                     box_alpha=0.05, fade=True),
    "subtitle": _C(name="Subtitle", font="Poppins", size=90, upper=False, chunk=6, maxchars=36, primary="#FFFFFF",
                   outline="#000000", bord=14, shadow=0, mode="static", box=True, box_alpha=0.45, fade=True),
    # karaoke
    "karaoke_gold": _C(name="Gold Karaoke", font="Poppins Black", size=130, upper=False, chunk=4, maxchars=26,
                       primary="#FFD60A", secondary="#FFFFFF", outline="#000000", bord=6, shadow=3, mode="karaoke"),
    "karaoke_pink": _C(name="Pink Karaoke", font="Kanit Black", size=140, upper=True, chunk=3, maxchars=22,
                       primary="#FF4FD8", secondary="#FFFFFF", outline="#000000", bord=7, shadow=3, mode="karaoke"),
    "karaoke_green": _C(name="Green Karaoke", font="Lilita One", size=150, upper=False, chunk=3, maxchars=22,
                        primary="#3DFF6B", secondary="#FFFFFF", outline="#000000", bord=7, shadow=3, mode="karaoke"),
    # neon
    "monoton_neon": _C(name="Retro Neon", font="Monoton", size=140, upper=True, chunk=2, maxchars=14,
                       primary="#FFFFFF", outline="#FF2BD6", bord=3, shadow=0, active="#00F5FF", emph="#FFFFFF",
                       mode="active", glow="#FF2BD6"),
    "cyber": _C(name="Cyber Glow", font="Russo One", size=150, upper=True, chunk=3, maxchars=18, primary="#00F5FF",
                outline="#001018", bord=6, shadow=0, active="#FF00E5", emph="#FFFFFF", mode="active", glow="#00F5FF",
                bounce=True),
    "hollow_pink": _C(name="Hollow Neon", font="Bungee", size=160, upper=True, chunk=2, maxchars=16, primary="#FF4FD8",
                      outline="#FFFFFF", bord=5, shadow=0, mode="hollow", bounce=True),
    # retro
    "retro_arcade": _C(name="Arcade", font="Press Start 2P", size=90, upper=True, chunk=2, maxchars=14,
                       primary="#FFFFFF", outline="#000000", bord=6, shadow=4, active="#39FF14", emph="#FFE400",
                       mode="active", pop=True),
    "retro_shade": _C(name="3D Shade", font="Bungee Shade", size=130, upper=True, chunk=2, maxchars=14,
                      primary="#FFD23F", outline="#000000", bord=4, shadow=5, active="#FF5E5B", emph="#FFFFFF",
                      mode="active", pop=True),
    "type_mono": _C(name="Old Typewriter", font="Special Elite", size=120, upper=False, chunk=4, maxchars=26,
                    primary="#FFFFFF", outline="#000000", bord=5, shadow=3, active="#FFB800", emph="#FFFFFF",
                    mode="typewriter"),
    # script
    "script_pacifico": _C(name="Pacifico", font="Pacifico", size=120, upper=False, chunk=3, maxchars=24,
                          primary="#FFFFFF", outline="#000000", bord=6, shadow=4, active="#FFD1DC", emph="#FFE66D",
                          mode="active", fade=True),
    "script_lobster": _C(name="Lobster", font="Lobster", size=140, upper=False, chunk=3, maxchars=22, primary="#FFFFFF",
                         outline="#1A1A1A", bord=6, shadow=4, active="#FFE400", emph="#FFFFFF", mode="active", bounce=True),
    "script_satisfy": _C(name="Elegant Script", font="Satisfy", size=130, upper=False, chunk=4, maxchars=26,
                         primary="#FFFFFF", outline="#000000", bord=5, shadow=4, mode="static", fade=True),
    # word by word
    "oneword_neon": _C(name="One Word Neon", font="Bebas Neue", size=250, upper=True, chunk=1, maxchars=12,
                       primary="#FFFFFF", outline="#000000", bord=6, shadow=0, mode="oneword", glow="#FF2BD6",
                       palette=["#FFFFFF", "#00F5FF", "#FF4FD8", "#FFE400"]),
    "oneword_kanit": _C(name="One Word Heavy", font="Kanit Black", size=220, upper=True, chunk=1, maxchars=12,
                        primary="#FFFFFF", outline="#000000", bord=10, shadow=6, mode="oneword",
                        palette=["#FFFFFF", "#FFE400", "#FF4D4D", "#4DD2FF"]),
    "oneword_titan": _C(name="One Word Bubble", font="Titan One", size=200, upper=False, chunk=1, maxchars=12,
                        primary="#FFFFFF", outline="#2B0A3D", bord=10, shadow=6, mode="oneword",
                        palette=["#FFFFFF", "#FF9F1C", "#7CFF4F", "#FF6FD8"]),
    # clean
    "minimal_upper": _C(name="Minimal Caps", font="Kanit Black", size=110, upper=True, chunk=3, maxchars=22,
                        primary="#FFFFFF", outline="#000000", bord=3, shadow=5, mode="static", fade=True),
    "clean_yellow": _C(name="Clean Yellow", font="Poppins", size=120, upper=False, chunk=4, maxchars=28,
                       primary="#FFFFFF", outline="#000000", bord=3, shadow=5, active="#FFE400", emph="#FFFFFF",
                       mode="active", fade=True),
})
_CAP_CATS = {
    "bold": ["hormozi", "beast", "slide", "bungee_pop", "lilita", "russo", "slab_bold", "staat", "righteous", "black_ops",
             "kanit_word"],
    "fun": ["comic", "marker", "titan", "sigmar", "chewy_fun", "boogaloo", "shrikhand", "brush"],
    "boxed": ["boxed", "pill", "card", "bowlby_box", "passion_box", "pill_green", "pill_red", "pill_yellow", "tiktok_box",
              "card_light", "subtitle"],
    "karaoke": ["karaoke", "karaoke_gold", "karaoke_pink", "karaoke_green"],
    "neon": ["neon", "monoton_neon", "cyber", "hollow", "hollow_pink"],
    "retro": ["typewriter", "retro_arcade", "retro_shade", "type_mono"],
    "script": ["script_pacifico", "script_lobster", "script_satisfy"],
    "word": ["oneword", "oneword_neon", "oneword_kanit", "oneword_titan"],
    "clean": ["minimal", "minimal_upper", "clean_yellow"],
}
for _cat, _keys in _CAP_CATS.items():
    for _k in _keys:
        CAPTION_STYLES[_k]["cat"] = _cat

_H = dict
HOOK_STYLES.update({
    "bungee_yellow": _H(name="Yellow Sign", font="Bungee", size=110, color="#111111", box="#FFE400", upper=True, dur=4.0,
                        y=300, anim="drop"),
    "titan_bubble": _H(name="Bubble Pop", font="Titan One", size=150, color="#FFFFFF", outline="#FF2E63", bord=10,
                       upper=False, dur=3.5, y=330, anim="pop"),
    "lilita_green": _H(name="Green Punch", font="Lilita One", size=160, color="#3DFF6B", outline="#000000", bord=9,
                       upper=True, dur=3.5, y=330, anim="pop"),
    "russo_red": _H(name="Red Strip", font="Russo One", size=110, color="#FFFFFF", box="#E3122C", upper=True, dur=4.0,
                    y=300, anim="slide"),
    "staat_white": _H(name="Tall White", font="Staatliches", size=180, color="#FFFFFF", outline="#000000", bord=6,
                      upper=True, dur=3.5, y=330, anim="drop"),
    "monoton_neon": _H(name="Neon Tube", font="Monoton", size=120, color="#FFFFFF", outline="#FF2BD6", bord=3,
                       glow="#FF2BD6", upper=True, dur=3.5, y=330, anim="flicker"),
    "arcade": _H(name="Arcade Title", font="Press Start 2P", size=70, color="#39FF14", outline="#000000", bord=6,
                 upper=True, dur=3.5, y=330, anim="glitch"),
    "slab_news": _H(name="News Flash", font="Alfa Slab One", size=105, color="#FFFFFF", box="#0A2463", upper=True,
                    dur=4.0, y=300, anim="slide"),
    "breaking": _H(name="Breaking", font="Archivo Black", size=112, color="#FFFFFF", box="#E3122C", upper=True, dur=4.0,
                   y=300, anim="glitch"),
    "black_ops": _H(name="Gamer Title", font="Black Ops One", size=140, color="#FFD400", outline="#000000", bord=7,
                    upper=True, dur=3.5, y=330, anim="glitch"),
    "chewy_pink": _H(name="Candy Title", font="Chewy", size=150, color="#FFFFFF", outline="#9D0191", bord=9, upper=False,
                     dur=3.5, y=330, anim="pop", tilt=-4),
    "script_pink": _H(name="Pink Script", font="Pacifico", size=115, color="#FFFFFF", outline="#FF4FD8", bord=5,
                      glow="#FF4FD8", upper=False, dur=4.0, y=320, anim="fade"),
    "lobster_gold": _H(name="Gold Script", font="Lobster", size=140, color="#FFD60A", outline="#3D2B00", bord=8,
                       upper=False, dur=4.0, y=320, anim="drop"),
    "kanit_black": _H(name="Heavy Black", font="Kanit Black", size=125, color="#FFFFFF", box="#000000", box_alpha=0.2,
                      upper=True, dur=4.0, y=300, anim="drop"),
    "sigmar_orange": _H(name="Orange Burst", font="Sigmar One", size=120, color="#FF7B00", outline="#FFFFFF", bord=8,
                        upper=True, dur=3.5, y=330, anim="pop"),
    "bowlby_card": _H(name="White Card", font="Bowlby One", size=105, color="#111111", box="#FFFFFF", upper=True,
                      dur=4.0, y=300, anim="drop", tilt=2),
    "shade_retro": _H(name="Retro 3D", font="Bungee Shade", size=115, color="#FFD23F", outline="#000000", bord=3,
                      upper=True, dur=3.5, y=330, anim="flicker"),
    "question": _H(name="Purple Question", font="Poppins Black", size=120, color="#FFFFFF", box="#7C3BFF", upper=True,
                   dur=4.0, y=300, anim="slide"),
    "brush_note": _H(name="Brush Note", font="Caveat Brush", size=140, color="#FFFFFF", box="#FF2E63", upper=False,
                     dur=4.0, y=310, anim="drop", tilt=-2),
    "typewriter_hook": _H(name="Typed Title", font="Special Elite", size=110, color="#FFFFFF", box="#111111",
                          upper=False, dur=4.0, y=310, anim="fade"),
})
_HOOK_CATS = {
    "bold": ["yellow_impact", "headline", "lilita_green", "staat_white", "kanit_black", "sigmar_orange", "black_ops"],
    "banner": ["banner_white", "red_tag", "bungee_yellow", "russo_red", "slab_news", "breaking", "bowlby_card", "question"],
    "fun": ["comic_burst", "titan_bubble", "chewy_pink", "brush_note"],
    "neon": ["neon_sign", "glitch", "monoton_neon", "arcade", "shade_retro"],
    "script": ["marker_note", "script_pink", "lobster_gold", "typewriter_hook"],
}
for _cat, _keys in _HOOK_CATS.items():
    for _k in _keys:
        HOOK_STYLES[_k]["cat"] = _cat

CTA_STYLES.update({
    "bungee_bar": dict(name="Yellow Bar", font="Bungee", size=70, color="#111111", box="#FFE400", anim="slide"),
    "neon_follow": dict(name="Neon Tube", font="Monoton", size=80, color="#FFFFFF", outline="#00F0FF", bord=3, anim="pulse"),
    "lilita_pill": dict(name="Purple Pill", font="Lilita One", size=90, color="#FFFFFF", box="#7C3BFF", anim="slide"),
    "script_follow": dict(name="Script", font="Pacifico", size=80, color="#FFFFFF", outline="#000000", bord=4, anim="fade"),
    "arcade_cta": dict(name="Arcade", font="Press Start 2P", size=48, color="#39FF14", outline="#000000", bord=5,
                       anim="pulse"),
    "black_box": dict(name="Black Box", font="Kanit Black", size=76, color="#FFFFFF", box="#000000", anim="slide"),
    "green_sub": dict(name="Green Subscribe", font="Russo One", size=74, color="#FFFFFF", box="#16C172", anim="pulse"),
    "comic_cta": dict(name="Comic", font="Bangers", size=110, color="#FFE600", outline="#000000", bord=7, anim="pulse"),
})

POSITIONS = {"upper": 0.33, "middle": 0.52, "lower": 0.67}
RTL_FONT = {"ur": "Noto Nastaliq Urdu", "ar": "Noto Naskh Arabic", "fa": "Noto Naskh Arabic",
            "ps": "Noto Naskh Arabic", "hi": "Noto Sans Devanagari", "mr": "Noto Sans Devanagari",
            "ne": "Noto Sans Devanagari"}


def col(hex_rgb: str, alpha: float = 0.0, style: bool = False) -> str:
    """#RRGGBB -> ASS colour. alpha 0 = opaque, 1 = invisible."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = f"{int(round(alpha * 255)):02X}"
    return f"&H{a}{b}{g}{r}".upper() if style else f"&H{b}{g}{r}&".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _has_script(text: str) -> Optional[str]:
    if re.search(r"[؀-ۿ]", text):
        return "ur"
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return None


@dataclass
class TextPlan:
    caption_style: str = "hormozi"
    hook_style: Optional[str] = "yellow_impact"
    cta_style: Optional[str] = "pill"
    hook_text: str = ""
    cta_text: str = "Follow for more"
    watermark: str = ""
    part_label: str = ""
    position: str = "lower"
    language: str = "en"
    emphasis: set = field(default_factory=set)
    seed: int = 0
    # manual placement from the editor (all optional):
    # cap_y/hook_y/cta_y = centre as a fraction of the height, cap_scale/hook_scale = size multiplier,
    # hook_dur = seconds on screen, wm_pos = top | top_left | top_right | bottom | bottom_left | bottom_right
    place: dict = field(default_factory=dict)


WM_POSITIONS = {"top": "Top centre", "top_left": "Top left", "top_right": "Top right",
                "bottom": "Bottom centre", "bottom_left": "Bottom left", "bottom_right": "Bottom right"}


def _py(place: dict, key: str, default: int, lo: int = 140, hi: int = 1790) -> int:
    v = (place or {}).get(key)
    if v is None:
        return default
    try:
        return int(min(hi, max(lo, float(v) * H)))
    except (TypeError, ValueError):
        return default


def _pscale(place: dict, key: str) -> float:
    try:
        return min(1.6, max(0.5, float((place or {}).get(key, 1.0))))
    except (TypeError, ValueError):
        return 1.0


class AssBuilder:
    def __init__(self, language: str = "en"):
        self.styles: list[str] = []
        self.events: list[str] = []
        self.lang = language

    def font(self, fam: str, sample: str = "") -> str:
        script = _has_script(sample)  # decide by the letters actually shown (Roman Urdu uses normal fonts)
        if script and script in RTL_FONT:
            fam = RTL_FONT[script]
        return fonts.resolve(fam)

    def add_style(self, name: str, font: str, size: int, primary: str, secondary: str = "#FFFFFF",
                  outline: str = "#000000", back: str = "#000000", bord: float = 4, shadow: float = 2,
                  border_style: int = 1, back_alpha: float = 0.35, primary_alpha: float = 0.0,
                  bold: int = 0, align: int = 5, spacing: float = 0, outline_alpha: float = 0.0) -> None:
        self.styles.append(
            f"Style: {name},{font},{size},{col(primary, primary_alpha, True)},{col(secondary, 0, True)},"
            f"{col(outline, outline_alpha, True)},{col(back, back_alpha, True)},{bold},0,0,0,100,100,{spacing},0,"
            f"{border_style},{bord},{shadow},{align},80,80,60,1"
        )

    def ev(self, start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        if end - start < 0.02:
            return
        self.events.append(f"Dialogue: {layer},{ts(start)},{ts(end)},{style},,0,0,0,,{text}")

    def render(self) -> str:
        head = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        body = "\n".join(self.styles)
        evs = ("\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
               + "\n".join(self.events) + "\n")
        return head + body + evs


# ---------------------------------------------------------------------------
def chunk_words(words: list[dict], n: int, maxchars: int) -> list[list[dict]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if len(cur) >= n or chars > maxchars or gap > 0.55 or re.search(r"[.,?!;:۔؟،।]$", cur[-1]["w"]):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _clean_word(w: str, upper: bool) -> str:
    w = w.strip()
    w = re.sub(r"^[\"'“(]+|[\"'”)]+$", "", w)
    w = re.sub(r"[,;:]+$", "", w)
    w = re.sub(r"(?<!\.)\.$", "", w)  # drop a single trailing period, keep ... ? !
    if upper and not _has_script(w):
        w = w.upper()
    return esc(w)


def is_emphasis(word: str, emphasis: set) -> bool:
    k = re.sub(r"[^\w']", "", word.lower())
    return bool(k) and (k in emphasis or bool(re.fullmatch(r"\$?\d[\d,.%]*[kmb%]?", k)))


SAFE_W = 1080 - 2 * 64  # keep clear of the screen edges


def fit_size(fam: str, text: str, max_size: int, st: dict, rtl: bool = False) -> int:
    """Largest font size (up to the style's max) at which the line fits the safe width, viral style."""
    grow = 1.12 if (st.get("pop") or st.get("emph")) else 1.0   # room for the word pop / emphasis zoom
    pad = 2 * (st.get("bord", 6) + (16 if st.get("hl_box") else 0) + (st.get("bord", 0) if st.get("box") else 0))
    unit = fonts.text_width(fam, text, 1.0) * grow
    if unit <= 0:
        return max_size
    fs = int((SAFE_W - pad) / unit)
    lo = int(max_size * (0.62 if not rtl else 0.55))
    return max(lo, min(max_size, fs))


def build_captions(ab: AssBuilder, words: list[dict], clip_dur: float, plan: TextPlan) -> None:
    st = CAPTION_STYLES.get(plan.caption_style, CAPTION_STYLES["hormozi"])
    rnd = random.Random(plan.seed)
    sample = " ".join(w["w"] for w in words[:30])
    fam = ab.font(st["font"], sample)
    rtl = _has_script(sample) is not None
    upper = st.get("upper", False) and not rtl
    size = st["size"] if not rtl else int(st["size"] * 0.85)
    size = int(size * _pscale(plan.place, "cap_scale"))
    y = _py(plan.place, "cap_y", int(H * POSITIONS.get(plan.position, 0.70)))
    mode = st["mode"]

    if st.get("box"):
        ba = st.get("box_alpha", 0.0)
        ab.add_style("Cap", fam, size, st["primary"], outline=st["outline"], back=st["outline"],
                     bord=st["bord"], shadow=0, border_style=3, back_alpha=ba, outline_alpha=ba)
    elif mode == "hollow":
        ab.add_style("Cap", fam, size, st["primary"], outline=st["outline"], bord=st["bord"], shadow=0,
                     primary_alpha=1.0)
    elif mode == "karaoke":
        ab.add_style("Cap", fam, size, st["primary"], secondary=st["secondary"], outline=st["outline"],
                     bord=st["bord"], shadow=st["shadow"])
    else:
        ab.add_style("Cap", fam, size, st["primary"], outline=st["outline"], bord=st["bord"], shadow=st["shadow"])
    if st.get("glow"):
        ab.add_style("Glow", fam, size, st["glow"], outline=st["glow"], bord=st["bord"] + 10, shadow=0,
                     primary_alpha=1.0)
    if st.get("hl_box"):  # coloured box behind the active word only
        ab.add_style("HL", fam, size, st["primary"], outline=st["hl_box"], back=st["hl_box"], bord=16, shadow=0,
                     border_style=3, back_alpha=1.0, primary_alpha=1.0)

    chunks = chunk_words(words, st["chunk"], st["maxchars"])
    palette = st.get("palette", [])
    for ci, ch in enumerate(chunks):
        cs = ch[0]["s"]
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else clip_dur
        ce = min(nxt, ch[-1]["e"] + 0.6)
        if nxt - ch[-1]["e"] < 0.3:
            ce = nxt
        ce = min(ce, clip_dur)
        toks = [_clean_word(w["w"], upper) for w in ch]
        fs = fit_size(fam, " ".join(toks), size, st, rtl)
        split = None  # word index where a 2nd line starts (big text stacks instead of shrinking)
        if len(toks) >= 3 and fs < size * 0.85:
            best = None
            for k in range(1, len(toks)):
                a, b = " ".join(toks[:k]), " ".join(toks[k:])
                f2 = min(fit_size(fam, a, size, st, rtl), fit_size(fam, b, size, st, rtl))
                if best is None or f2 > best[0]:
                    best = (f2, k)
            if best and best[0] > fs * 1.15:
                fs, split = best[0], best[1]

        def J(parts: list) -> str:
            return "".join(("\\N" if i == split else " ") + p if i else p for i, p in enumerate(parts))

        fs_tag = f"\\fs{fs}" if fs != size else ""
        tilt = f"\\frz{rnd.uniform(-4, 4):.1f}" if st.get("tilt") else ""
        base_pos = f"\\an5\\pos(540,{y})\\blur0.6"  # soft anti-aliased edges look far more premium

        def entrance() -> str:
            if st.get("slide"):
                return f"\\an5\\move(540,{y + 70},540,{y},0,140)\\blur0.6\\alpha&HFF&\\t(0,120,\\alpha&H00&)"
            if st.get("bounce"):
                return f"{base_pos}\\fscx72\\fscy72\\t(0,110,\\fscx106\\fscy106)\\t(110,190,\\fscx100\\fscy100)"
            if st.get("fade"):
                return f"{base_pos}\\fad(90,70)"
            return base_pos

        prim = col(st["primary"])

        def word_txt(i: int, active: Optional[int], shown: Optional[int] = None) -> str:
            parts = []
            for m, t in enumerate(toks):
                tag, undo = "", ""
                if shown is not None and m > shown:
                    tag, undo = "\\alpha&HFF&", "\\alpha&H00&"
                elif m == active and mode in ("active", "typewriter"):
                    tag, undo = f"\\c{col(st.get('active', '#FFE400'))}", f"\\c{prim}"
                    if st.get("pop"):
                        tag += "\\fscx88\\fscy88\\t(0,80,\\fscx112\\fscy112)\\t(80,160,\\fscx106\\fscy106)"
                        undo += "\\fscx100\\fscy100"
                elif m == active and mode == "hollow":
                    tag, undo = "\\1a&H00&", "\\1a&HFF&"
                elif st.get("emph") and is_emphasis(ch[m]["w"], plan.emphasis):
                    if st.get("box") or st.get("hl_box"):
                        tag, undo = f"\\c{col(st['emph'])}", f"\\c{prim}"
                    else:
                        tag, undo = f"\\c{col(st['emph'])}\\fscx112\\fscy112", f"\\c{prim}\\fscx100\\fscy100"
                parts.append(("{" + tag + "}" if tag else "") + t + ("{" + undo + "}" if undo else ""))
            return J(parts)

        if mode in ("static",):
            ab.ev(cs, ce, "Cap", "{" + entrance() + fs_tag + tilt + "}" + J(toks))
            continue
        if mode == "karaoke":
            kparts = []
            for k, w in enumerate(ch):
                end_k = ch[k + 1]["s"] if k + 1 < len(ch) else w["e"]
                kparts.append(f"{{\\kf{max(1, int(round((end_k - w['s']) * 100)))}}}{toks[k]}")
            lead = max(0, int(round((ch[0]["s"] - cs) * 100)))
            ab.ev(cs, ce, "Cap", "{" + base_pos + fs_tag + "\\fad(60,60)" + (f"\\k{lead}" if lead else "") + "}"
                  + J(kparts))
            continue
        if mode == "oneword":
            for k, w in enumerate(ch):
                s = w["s"] if k else cs
                e = ch[k + 1]["s"] if k + 1 < len(ch) else ce
                c = palette[(ci + k) % len(palette)] if palette else st["primary"]
                if is_emphasis(w["w"], plan.emphasis):
                    c = "#FFE400"
                ab.ev(s, e, "Cap", "{" + base_pos + fs_tag + f"\\c{col(c)}"
                      + "\\fscx130\\fscy130\\t(0,90,\\fscx96\\fscy96)\\t(90,150,\\fscx100\\fscy100)}" + toks[k])
            continue
        # active / typewriter / hollow: one event per word
        for k, w in enumerate(ch):
            s = cs if k == 0 else w["s"]
            e = ch[k + 1]["s"] if k + 1 < len(ch) else ce
            lead_tags = (entrance() if k == 0 else base_pos) + fs_tag + tilt
            txt = word_txt(k, k, shown=k if mode == "typewriter" else None)
            if st.get("glow"):
                ab.ev(s, e, "Glow", "{" + lead_tags + "\\blur12}" + J(toks), layer=0)
            if st.get("hl_box"):
                boxed = J([("{\\3a&H00&}" if m == k else "{\\3a&HFF&}") + t for m, t in enumerate(toks)])
                ab.ev(s, e, "HL", "{" + lead_tags + "}" + boxed, layer=0)
            ab.ev(s, e, "Cap", "{" + lead_tags + "}" + txt, layer=1)


def wrap_measured(fam: str, text: str, size: int, max_lines: int, width: int = 920) -> Optional[list]:
    """Greedy word wrap using real glyph widths; None if it needs more than max_lines."""
    lines, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if cur and fonts.text_width(fam, cand, size) > width:
            lines.append(cur)
            cur = w
        else:
            cur = cand
        if fonts.text_width(fam, cur, size) > width:
            return None
    if cur:
        lines.append(cur)
    return lines if len(lines) <= max_lines else None


def _wrap(text: str, width: int) -> str:
    lines = textwrap.wrap(text, width=width) or [text]
    return "\\N".join(lines[:3])


def build_hook(ab: AssBuilder, text: str, clip_dur: float, style_key: str, seed: int = 0,
               place: Optional[dict] = None) -> None:
    if not text.strip():
        return
    st = HOOK_STYLES.get(style_key, HOOK_STYLES["yellow_impact"])
    rtl = _has_script(text) is not None
    fam = ab.font(st["font"], text)
    txt = text.upper() if st.get("upper") and not rtl else text
    size = int(st["size"] * _pscale(place, "hook_scale"))
    # viral hooks read as 2 short, big lines: wrap narrower than the screen so longer titles stack
    lines = wrap_measured(fam, esc(txt), size, 3, width=880)
    while lines is None and size > 40:   # too long for 3 lines -> shrink until it fits
        size = int(size * 0.9)
        lines = wrap_measured(fam, esc(txt), size, 3, width=900)
    if lines is None:
        lines = textwrap.wrap(esc(txt), 24)[:3]
    body = "{\\q2}" + "\\N".join(lines)
    dur = min(clip_dur, st["dur"]) if st.get("dur") else clip_dur
    if (place or {}).get("hook_dur"):
        dur = min(clip_dur, max(1.0, float(place["hook_dur"])))
    y = st["y"] - 10 + (len(lines) - 1) * size // 2  # grow downward, clear of the part label
    y = _py(place, "hook_y", y)
    tilt = f"\\frz{st['tilt']}" if st.get("tilt") else ""
    if st.get("box"):
        ab.add_style("Hook", fam, size, st["color"], outline=st["box"], back=st["box"], bord=20, shadow=0,
                     border_style=3, back_alpha=st.get("box_alpha", 0), outline_alpha=st.get("box_alpha", 0))
    else:
        ab.add_style("Hook", fam, size, st["color"], outline=st.get("outline", "#000000"),
                     bord=st.get("bord", 6), shadow=4)
    anim = st.get("anim")
    pos = f"\\an5\\pos(540,{y})"
    out_fade = "\\fad(0,200)" if st.get("dur") else ""
    if anim == "pop":
        a = f"{pos}\\fscx40\\fscy40\\t(0,140,\\fscx112\\fscy112)\\t(140,240,\\fscx100\\fscy100){out_fade}"
    elif anim == "drop":
        a = f"\\an5\\move(540,{y - 140},540,{y},0,220)\\fad(120,200)"
    elif anim == "slide":
        a = f"\\an5\\move(-400,{y},540,{y},0,260){out_fade}"
    elif anim == "flicker":
        a = (f"{pos}\\alpha&HFF&\\t(0,60,\\alpha&H00&)\\t(60,120,\\alpha&HC0&)\\t(120,180,\\alpha&H00&)"
             f"\\t(260,300,\\alpha&H90&)\\t(300,340,\\alpha&H00&){out_fade}")
    elif anim == "glitch":
        a = f"{pos}\\fad(80,200)"
    else:
        a = f"{pos}\\fad(250,250)"
    if st.get("glow"):
        ab.add_style("HookGlow", fam, size, st["glow"], outline=st["glow"], bord=14, shadow=0,
                     primary_alpha=1.0)
        ab.ev(0, dur, "HookGlow", "{" + a + tilt + "\\blur14}" + body, layer=5)
    if anim == "glitch":
        # chromatic split that settles, plus a few shake frames
        for dx, c in ((-10, "#00FFFF"), (10, "#FF00FF")):
            ab.ev(0, dur, "Hook", "{" + f"\\an5\\move({540 + dx * 2},{y},{540 + dx // 3},{y},0,400)"
                  + f"\\c{col(c)}\\3a&HFF&\\4a&HFF&\\alpha&H40&\\fad(80,200)" + "}" + body, layer=5)
        for i, (dx, dy) in enumerate(((14, -6), (-12, 5), (8, 7), (-6, -4))):
            t0 = 0.05 + i * 0.07
            ab.ev(t0, t0 + 0.06, "Hook", "{" + f"\\an5\\pos({540 + dx},{y + dy})" + "}" + body, layer=7)
        ab.ev(0.33, dur, "Hook", "{" + a + tilt + "}" + body, layer=6)
        return
    ab.ev(0, dur, "Hook", "{" + a + tilt + "}" + body, layer=6)


def build_cta(ab: AssBuilder, text: str, clip_dur: float, style_key: str, place: Optional[dict] = None) -> None:
    if not text.strip() or clip_dur < 8:
        return
    st = CTA_STYLES.get(style_key, CTA_STYLES["pill"])
    fam = ab.font(st["font"], text)
    txt = esc(text.upper() if st["font"] != "Poppins" else text)
    if st.get("box"):
        ab.add_style("Cta", fam, st["size"], st["color"], outline=st["box"], back=st["box"], bord=18, shadow=0,
                     border_style=3, back_alpha=0)
    else:
        ab.add_style("Cta", fam, st["size"], st["color"], outline=st.get("outline", "#000000"),
                     bord=st.get("bord", 5), shadow=3)
    y = _py(place, "cta_y", int(H * 0.42))
    s = max(0.0, clip_dur - 2.6)
    anim = st["anim"]
    if anim == "slide":
        a = f"\\an5\\move(540,{y + 200},540,{y},0,260)\\fad(150,0)"
    elif anim == "pulse":
        a = (f"\\an5\\pos(540,{y})\\fad(150,0)\\t(0,300,\\fscx112\\fscy112)\\t(300,600,\\fscx100\\fscy100)"
             f"\\t(600,900,\\fscx112\\fscy112)\\t(900,1200,\\fscx100\\fscy100)\\t(1200,1500,\\fscx112\\fscy112)"
             f"\\t(1500,1800,\\fscx100\\fscy100)")
    else:
        a = f"\\an5\\pos(540,{y})\\fad(300,0)"
    ab.ev(s, clip_dur, "Cta", "{" + a + "}" + txt, layer=8)


def build_watermark(ab: AssBuilder, text: str, clip_dur: float, place: Optional[dict] = None) -> None:
    if not text.strip():
        return
    ab.add_style("Wm", ab.font("Poppins", text), int(40 * _pscale(place, "wm_scale")), "#FFFFFF",
                 outline="#000000", bord=2, shadow=1, primary_alpha=0.35, outline_alpha=0.6)
    pos = {"top": "\\an8\\pos(540,70)", "top_left": "\\an7\\pos(50,70)", "top_right": "\\an9\\pos(1030,70)",
           "bottom": "\\an2\\pos(540,1840)", "bottom_left": "\\an1\\pos(50,1840)",
           "bottom_right": "\\an3\\pos(1030,1840)"}.get((place or {}).get("wm_pos") or "top", "\\an8\\pos(540,70)")
    ab.ev(0, clip_dur, "Wm", "{" + pos + "}" + esc(text), layer=3)


def build_part_label(ab: AssBuilder, text: str, clip_dur: float) -> None:
    if not text.strip():
        return
    ab.add_style("Part", ab.font("Archivo Black", text), 44, "#111111", outline="#FFE400", back="#FFE400",
                 bord=12, shadow=0, border_style=3, back_alpha=0)
    ab.ev(0, clip_dur, "Part", "{\\an7\\pos(60,105)\\fad(200,0)}" + esc(text.upper()), layer=4)


def build_ass(words: list[dict], clip_dur: float, plan: TextPlan) -> str:
    ab = AssBuilder(plan.language)
    if words and plan.caption_style != "none":
        build_captions(ab, words, clip_dur, plan)
    if plan.hook_style and plan.hook_text:
        build_hook(ab, plan.hook_text, clip_dur, plan.hook_style, plan.seed, plan.place)
    if plan.cta_style and plan.cta_text:
        build_cta(ab, plan.cta_text, clip_dur, plan.cta_style, plan.place)
    build_watermark(ab, plan.watermark, clip_dur, plan.place)
    build_part_label(ab, plan.part_label, clip_dur)
    if not ab.styles:
        ab.add_style("Default", "Arial", 40, "#FFFFFF")
    return ab.render()
