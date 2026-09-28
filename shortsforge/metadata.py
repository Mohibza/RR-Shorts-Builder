"""Viral upload metadata for every Short: title, description, tags and hashtags.

One AI request covers all Shorts of a video (keeps well inside Gemini's free limit). Without an AI key, or if
the request fails, an offline writer builds solid metadata from the clip's hook, keywords and text.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from .highlights import STOP as _HL_STOP

Log =Optional[Callable[[str], None]]

STOP = set("""a an the and or but if so to of in on at for with from by is are was were be been being it this that
these those i you he she we they me him her us them my your our their not no yes do does did have has had just very
really can will would should could about into over than then there here what when where why how who which also
aur ka ki ke ko se mein main hai hain tha thi the ho hota hoti hotay kya ye yeh woh wo bhi to na nahi ek aik
maine mene humne hum tum aap unhon usne gaya gaye gayi hua hui huay raha rahe rahi sab phir lekin magar jab tab
kyun kyunke apna apni apne unka uski iska kuch bohat bahut liye wala wali wale karna karte kar kiya diya liya
lete leta kaha keh bola dekho yaar bas abhi""".split())


def _clean_tag(t: str) -> str:
    t = re.sub(r"[^\w\s]", "", str(t)).strip()
    return re.sub(r"\s+", " ", t)[:30]


def _hashtag(t: str) -> str:
    return "#" + re.sub(r"[^\w]", "", str(t)).lower()


# spoken filler that says nothing about the topic (apostrophes are stripped before the check: "that's" -> thats)
FILLER = set("""thats dont doesnt didnt isnt wasnt arent werent cant couldnt wouldnt shouldnt wont im ive ill id youre
youve youll youd hes shes theyre theyve weve were lets whats heres theres its think thought like well even still
much many something anything everything nothing someone anyone everyone somebody said says say saying tell tells
told went come came comes make made makes want wanted see saw seen look looked back now first every being guess
maybe probably sure okay yeah yes gonna wanna gotta kinda sorta stuff whatever pretty little bit way ever never
always whole another around through after before because while since though although where when then than just
happened happens eventually albeit completely basically needs point
kyunki matlab acha accha theek thik haan""".split())


def _is_topic(w: str) -> bool:
    """A tag candidate that tells a viewer (or search) what the Short is about."""
    k = re.sub(r"[^\w]", "", w.lower())
    return (len(k) > 3 and k not in STOP and k not in FILLER and k not in _HL_STOP
            and not re.fullmatch(r"\d+(st|nd|rd|th|s)?", k))


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", text.lower()) if _is_topic(w)]


def offline(clip, video_title: str, source: str) -> dict:
    """Good metadata without AI: hook as title, first lines as description, keyword tags."""
    title = (clip.title or clip.hook or clip.text[:80]).strip().rstrip(".")
    kw = [k for k in (clip.keywords or []) if k and all(_is_topic(p) for p in str(k).split())]
    freq: dict[str, int] = {}
    for w in _words(clip.text):
        freq[w] = freq.get(w, 0) + 1
    kw += [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])][:8]
    seen, tags = set(), []
    for k in kw + _words(video_title)[:4]:
        c = _clean_tag(k)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            tags.append(c)
    tags = tags[:12] + ["shorts", "viral", "trending"]
    hashtags = ["#shorts"] + [_hashtag(t) for t in tags[:4] if len(t) > 2]
    lines = re.split(r"(?<=[.!?])\s+", clip.text.strip())
    desc = " ".join(lines[:2])[:240]
    return {"title": title[:95], "description": desc, "tags": tags, "hashtags": list(dict.fromkeys(hashtags))[:6]}


PROMPT = """You are a top YouTube Shorts / TikTok / Reels growth editor. For each Short below write upload metadata
that maximises clicks, watch time and search reach, while staying truthful to what is said (no fake claims).

Language: {lang}.
Original long video: "{video_title}"

Rules per Short:
- "title": max 70 characters, curiosity + benefit, a strong hook in the viewer's language. 0-1 emoji. No hashtags.
- "description": 2-3 short lines: a hook line, what they learn, a call to action (follow / watch the full video).
  Max 300 characters. No hashtags here.
- "tags": 12-15 search keywords/phrases (mix English + the viewer's language), most relevant first.
- "hashtags": 4-6 hashtags, first one "#shorts".

Return ONLY JSON: {{"shorts": [{{"id": 0, "title": "...", "description": "...", "tags": ["..."], "hashtags": ["#shorts", "..."]}}]}}

Shorts:
{items}
"""


def generate(ai, clips: list, video_title: str, lang: str, source: str = "", log: Log = None) -> list[dict]:
    """Return one metadata dict per clip (same order)."""
    out = [offline(c, video_title, source) for c in clips]
    if ai is None or not clips:
        return out
    items = "\n".join(f'[{i}] hook: "{c.title}" | says: "{c.text[:500]}"' for i, c in enumerate(clips))
    try:
        data = ai.ask_json(PROMPT.format(lang=lang, video_title=video_title[:120], items=items), timeout=120)
        rows = data.get("shorts", data) if isinstance(data, dict) else data
        for r in rows or []:
            try:
                i = int(r.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not 0 <= i < len(out):
                continue
            m = out[i]
            if str(r.get("title", "")).strip():
                m["title"] = str(r["title"]).strip().replace("#", "")[:95]
            if str(r.get("description", "")).strip():
                m["description"] = str(r["description"]).strip()[:400]
            tags = [_clean_tag(t) for t in (r.get("tags") or []) if _clean_tag(t)]
            if tags:
                m["tags"] = list(dict.fromkeys(tags))[:15]
            hs = [_hashtag(h) for h in (r.get("hashtags") or []) if re.sub(r"[^\w]", "", str(h))]
            if hs:
                if "#shorts" not in hs:
                    hs.insert(0, "#shorts")
                m["hashtags"] = list(dict.fromkeys(hs))[:6]
        if log:
            log(f"{ai.label} wrote titles, descriptions and tags for {len(clips)} Shorts")
    except Exception as e:  # metadata is a bonus: never fail the build
        if log:
            log(f"(AI metadata skipped: {str(e)[:120]}; using offline titles)")
    return out


def compose(meta: dict, source: str = "", credit: str = "") -> str:
    """Full description text as uploaded: description, full-video link, music credit, hashtags."""
    parts = [meta.get("description", "").strip()]
    if source.startswith("http"):
        parts.append(f"▶ Full video: {source}")
    if credit:
        parts.append(f"🎵 Music: {credit}")
    parts.append(" ".join(meta.get("hashtags") or ["#shorts"]))
    return "\n\n".join(p for p in parts if p)
