"""Viral director: decides which parts of a long video become Shorts.

Two brains:
* `local_director`: runs offline. Splits the talk into complete topic sections (question→answer,
  story→payoff), scores every sentence, then builds each Short from a whole section, trimming weak
  sentences out of long sections (cuts) and optionally opening on the strongest line (cold open).
* `claude_director`: optional. Sends the timestamped transcript to Claude (your API key) and lets
  it act as a human editor picking the most viral, self-contained moments. Falls back to local.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Optional

import numpy as np

from .highlights import (FILLER_PENALTY, HOOK_WORDS, INTENSE, STOP, Clip, _hook_score, _norm,
                         energy_highlights, flatten_words, make_title, sentences)

CONTEXT_OPENERS = {"and", "but", "so", "or", "then", "because", "which", "that", "this", "it", "he", "she",
                   "they", "those", "these", "also", "plus", "anyway", "yeah", "okay", "ok", "um", "uh", "right",
                   "aur", "lekin", "to", "phir", "ye", "wo", "یہ", "وہ", "اور", "لیکن", "तो", "और", "लेकिन"}
STORY_CUES = ("let me tell you", "one day", "when i was", "i remember", "the story", "imagine", "here's the thing",
              "the truth is", "the problem is", "the secret", "nobody talks about", "biggest mistake",
              "what happened", "you won't believe", "the reason", "here's why", "here is why")
OUTRO_CUES = ("welcome back", "welcome to", "like and subscribe", "hit the bell", "subscribe", "thanks for watching",
              "thank you for watching", "see you next time", "see you in the next", "that's all for today",
              "that's it for today", "link in the description", "links are in the description", "before we start",
              "sponsor", "sponsored by", "use code", "patreon")
SHIFT_CUES = ("moving on", "next question", "another thing", "second thing", "the next", "let's talk about",
              "now let's", "okay so", "alright so", "all right so", "anyway")
Log = Optional[Callable[[str], None]]


# ---------------------------------------------------------------------------------------------- features
def _sentence_table(words: list[dict], energy: np.ndarray, hop: float = 0.1) -> list[dict]:
    sents = sentences(words)
    med = float(np.median(energy)) if len(energy) > 5 else -30.0
    vocab = Counter(_norm(w["w"]) for w in words)
    total = sum(vocab.values()) or 1
    rows = []
    for i, s in enumerate(sents):
        ws = words[s["i0"]: s["i1"] + 1]
        toks = [t for t in (_norm(w["w"]) for w in ws) if t]
        dur = max(0.3, s["e"] - s["s"])
        a, b = int(s["s"] / hop), max(int(s["s"] / hop) + 1, int(s["e"] / hop))
        en = energy[a:b] if len(energy) > a else np.zeros(1)
        low = s["text"].lower()
        content = [t for t in toks if t not in STOP and len(t) > 2]
        rows.append({
            "i": i, "s": s["s"], "e": s["e"], "text": s["text"], "terminal": s["terminal"], "dur": dur,
            "toks": toks, "content": content,
            "hook": _hook_score(s["text"]) + (1.2 if any(c in low for c in STORY_CUES) else 0.0),
            "intense": sum(1 for t in toks if t in INTENSE or t in HOOK_WORDS) / max(1, len(toks)) * 10,
            "energy": float(np.mean(en) - med) if len(en) else 0.0,
            "pace": len(toks) / dur,
            "question": "?" in s["text"] or "؟" in s["text"],
            "exclaim": "!" in s["text"],
            "number": bool(re.search(r"\d", s["text"])),
            "filler": (sum(1 for t in toks if t in FILLER_PENALTY) + (1 if len(toks) <= 2 else 0)
                       + 3 * any(c in low for c in OUTRO_CUES)
                       + (1 if sum(t in {"um", "uh", "like", "yeah", "anyway", "know", "basically"} for t in toks)
                          >= max(2, len(toks) // 3) else 0)),
            "needs_context": bool(toks) and toks[0] in CONTEXT_OPENERS,
            "rare": sum(math.log(total / vocab[t]) for t in content) / max(1, len(content)),
            "conf": float(np.mean([w.get("p", 1.0) for w in ws])) if ws else 1.0,
        })
    if not rows:
        return rows
    # z-score blend -> one "sentence value"
    keys = {"hook": 1.4, "intense": 0.9, "energy": 0.8, "pace": 0.4, "rare": 0.4, "conf": 0.4}
    for k, wgt in keys.items():
        v = np.array([r[k] for r in rows], float)
        sd = v.std() or 1.0
        for r, z in zip(rows, (v - v.mean()) / sd):
            r.setdefault("z", 0.0)
            r["z"] += wgt * z
    for r in rows:
        r["z"] += 0.8 * r["question"] + 0.5 * r["exclaim"] + 0.4 * r["number"] - 1.2 * r["filler"]
        r["z"] += 0.3 if r["terminal"] else -0.3
    return rows


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    num = sum(a[k] * b[k] for k in a if k in b)
    return num / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


def _blocks(rows: list[dict]) -> list[list[dict]]:
    """Split the talk into complete sections (topic changes, long pauses, new questions)."""
    if not rows:
        return []
    cuts = [0]
    for i in range(1, len(rows)):
        gap = rows[i]["s"] - rows[i - 1]["e"]
        before = Counter(t for r in rows[max(0, i - 3):i] for t in r["content"])
        after = Counter(t for r in rows[i:i + 3] for t in r["content"])
        low = rows[i]["text"].lower()
        shift = (gap > 1.4 or (rows[i]["question"] and gap > 0.9) or any(low.startswith(c) for c in SHIFT_CUES)
                 or (rows[i]["filler"] >= 3) != (rows[i - 1]["filler"] >= 3)
                 or (_cos(before, after) < 0.05 and gap > 1.0 and not rows[i]["needs_context"]))
        if shift:
            strong = gap > 1.4 or rows[i]["question"]
            if rows[i]["s"] - rows[cuts[-1]]["s"] > 6:
                cuts.append(i)
            elif strong and cuts[-1] != 0:
                cuts[-1] = i  # a stronger break just after a weak one: the stub joins the previous section
    cuts.append(len(rows))
    return [rows[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]


# ---------------------------------------------------------------------------------------------- building
def _span(rs: list[dict]) -> float:
    return rs[-1]["e"] - rs[0]["s"]


def _parts_from(rows_sel: list[dict], all_rows: list[dict]) -> list[list[dict]]:
    """Group chosen sentences into contiguous runs (each run = one part of the Short)."""
    runs, cur = [], []
    for r in rows_sel:
        if cur and r["i"] != cur[-1]["i"] + 1:
            runs.append(cur)
            cur = []
        cur.append(r)
    if cur:
        runs.append(cur)
    return runs


def _compress(block: list[dict], max_len: float, min_len: float) -> Optional[list[dict]]:
    """Keep the block's best sentences (in order) within max_len. Setup sentence is kept for context."""
    must = [block[0]] if (block[0]["question"] or block[0]["hook"] > 0.5 or not block[0]["needs_context"]) else []
    # the payoff (answer / punchline / lesson) is almost always at the end of a section
    tail = [r for r in block if r["filler"] < 1]
    if tail and tail[-1] not in must:
        must.append(tail[-1])
        if len(tail) > 2 and tail[-2]["dur"] < 4 and tail[-2] not in must:
            must.append(tail[-2])
    budget = max_len - sum(r["dur"] + 0.3 for r in must)
    ranked = sorted((r for r in block if r not in must), key=lambda r: r["z"] / (r["dur"] ** 0.35), reverse=True)
    chosen = list(must)
    for r in ranked:
        if r["z"] < -0.8:
            break
        if r["dur"] + 0.3 <= budget:
            chosen.append(r)
            budget -= r["dur"] + 0.3
    chosen.sort(key=lambda r: r["i"])
    # fill 1-sentence holes so the story reads naturally
    ids = {r["i"] for r in chosen}
    for r in block:
        if r["i"] not in ids and (r["i"] - 1) in ids and (r["i"] + 1) in ids and r["dur"] + 0.3 <= budget:
            chosen.append(r)
            budget -= r["dur"] + 0.3
    chosen.sort(key=lambda r: r["i"])
    # a Short must end on a finished sentence
    while len(chosen) > 1 and not chosen[-1]["terminal"] and sum(r["dur"] for r in chosen[:-1]) >= min_len:
        chosen.pop()
    total = sum(r["dur"] + 0.3 for r in chosen)
    return chosen if total >= min_len * 0.9 else None


def _clean(sel: list[dict], min_len: float) -> list[dict]:
    """Cut filler, rambles and channel-outro lines out of a selection (creates jump cuts)."""
    out = [r for r in sel if not (r["filler"] >= 1 and r["z"] < 0.3) and r["filler"] < 3]
    while out and out[0]["needs_context"] and len(out) > 2 and out[0]["hook"] < 0.5:
        out = out[1:]
    return out if sum(r["dur"] for r in out) >= min_len * 0.85 else sel


def _score(sel: list[dict], min_len: float, max_len: float, block_end: Optional[int] = None) -> float:
    if not sel:
        return -1e9
    zs = sorted((r["z"] for r in sel), reverse=True)
    top = np.mean(zs[: max(2, len(zs) // 2)])
    dur = sum(r["dur"] for r in sel)
    target = min(max_len, max(min_len, 38.0))
    runs = len(_parts_from(sel, []))
    score = 1.2 * top + 0.4 * np.mean(zs)
    score += 1.1 * max(0.0, sel[0]["hook"]) - (1.3 if sel[0]["needs_context"] else 0.0)
    score += 0.6 if sel[-1]["terminal"] else -0.8
    score -= 0.35 * max(0, runs - 1) + (0.8 if runs > 4 else 0)
    score -= 0.6 * abs(dur - target) / target
    if block_end is not None:  # reward Shorts that reach the section's payoff
        score += 0.9 if sel[-1]["i"] >= block_end else -0.5
    return float(score)


def _as_clip(sel: list[dict], cold: Optional[dict], words: list[dict], score: float, why: dict) -> Clip:
    runs = _parts_from(sel, [])
    parts = []
    if cold:
        parts.append([max(0.0, cold["s"] - 0.1), cold["e"] + 0.25])
    for run in runs:
        parts.append([max(0.0, run[0]["s"] - 0.12), run[-1]["e"] + 0.3])
    text = " ".join(r["text"] for r in ([cold] if cold else []) + sel)
    best = max(([cold] if cold else []) + sel[:3], key=lambda r: r["hook"])
    content = Counter(t for r in sel for t in r["content"] if len(t) > 3)
    return Clip(start=min(p[0] for p in parts), end=max(p[1] for p in parts), score=round(score, 3), text=text,
                hook=best["text"], title=make_title(best["text"]), keywords=[k for k, _ in content.most_common(6)],
                reasons=why, segments=parts if len(parts) > 1 else [])


def local_director(transcript: dict, energy: np.ndarray, duration: float, n: int, min_len: float,
                   max_len: float, min_gap: float = 5.0, cold_open: bool = True, log: Log = None) -> list[Clip]:
    words = flatten_words(transcript)
    if len(words) < 20:
        return energy_highlights(energy, duration, n, min_len, max_len, min_gap)
    rows = _sentence_table(words, energy)
    blocks = _blocks(rows)
    cands = []
    for bi, block in enumerate(blocks):
        # grow short sections with their neighbours until they're long enough to stand alone
        if np.mean([r["filler"] >= 3 for r in block]) > 0.5:
            continue  # intro / outro / sponsor section
        grown = list(block)
        j = bi + 1
        while _span(grown) < min_len and j < len(blocks) and np.mean([r["filler"] >= 3 for r in blocks[j]]) <= 0.5:
            grown += blocks[j]
            j += 1
        if _span(grown) < min_len * 0.85:
            continue
        options = []
        if _span(grown) <= max_len:
            options.append(("complete section", grown))
        else:
            comp = _compress(grown, max_len, min_len)
            if comp:
                options.append(("best of section (cuts)", comp))
            # best continuous window that starts cleanly inside the section
            for k in range(len(grown)):
                if grown[k]["needs_context"] and k:
                    continue
                win = []
                for r in grown[k:]:
                    if _span(win + [r]) > max_len:
                        break
                    win.append(r)
                while len(win) > 1 and not win[-1]["terminal"]:
                    win.pop()
                if win and _span(win) >= min_len:
                    options.append(("continuous window", win))
        for kind, sel in options:
            cleaned = _clean(sel, min_len)
            if len(cleaned) < len(sel):
                kind += " (filler cut)" if "cuts" not in kind else ""
                sel = cleaned
            payoff = max((r["i"] for r in grown if r["filler"] < 1), default=None)
            sc = _score(sel, min_len, max_len, payoff)
            if any(r["i"] < 2 and r["s"] < 20 for r in sel[:1]):  # channel intros rarely go viral
                sc -= 0.8
            cands.append((sc, kind, sel))
    if not cands:
        return energy_highlights(energy, duration, n, min_len, max_len, min_gap)

    cands.sort(key=lambda c: c[0], reverse=True)
    chosen, used = [], []
    sep = min(min_gap, 1.0)  # neighbouring sections may both become Shorts, they just can't overlap
    for sc, kind, sel in cands:
        a, b = sel[0]["s"], sel[-1]["e"]
        if any(not (b + sep <= u0 or a >= u1 + sep) for u0, u1 in used):
            continue
        cold = None
        if cold_open and len(sel) >= 4:
            best = max(sel[2:], key=lambda r: r["hook"] + 0.5 * r["z"])
            dur = sum(r["dur"] for r in sel)
            if (best["hook"] > max(1.2, sel[0]["hook"] + 0.6) and best["dur"] <= 7.5
                    and dur + best["dur"] <= max_len and not best["needs_context"]):
                cold = best
        why = {"type": kind, "cold_open": bool(cold), "parts": len(_parts_from(sel, [])) + (1 if cold else 0)}
        clip = _as_clip(sel, cold, words, sc, why)
        if cold and clip.duration > max_len * 1.05:
            why["cold_open"] = False
            clip = _as_clip(sel, None, words, sc, why)
        chosen.append(clip)
        used.append((a, b))
        if len(chosen) >= n:
            break
    if log:
        for c in chosen:
            log(f"  • {int(c.start // 60)}:{int(c.start % 60):02d} {c.reasons.get('type')}"
                f"{' + cold open' if c.reasons.get('cold_open') else ''} ({c.duration:.0f}s): {c.title}")
    chosen.sort(key=lambda c: c.start)
    return chosen


# ---------------------------------------------------------------------------------------------- AI editor
PROMPT = """You are a world-class short-form video editor (YouTube Shorts / TikTok / Reels) who has grown \
channels to millions of views. Below is the timestamped transcript of a long video, one numbered sentence per \
line: [id] start-end | text

Pick the {n} moments with the HIGHEST viral potential and turn each into one Short of {min_len:.0f}-{max_len:.0f} \
seconds (sweet spot 25-50 s).

What goes viral: a surprising or controversial claim, a strong opinion, a painful mistake or confession, a \
story with a twist/payoff, a secret/insider fact, a clear "how to" with a result, big numbers/money, emotion, \
humour, a heated exchange. Skip generic talk, greetings, housekeeping, sponsor reads, "like and subscribe".

Rules:
- Each Short must be a COMPLETE, self-contained idea that makes sense with zero prior context (question → answer, \
story → payoff, claim → proof). Never end mid-thought: end on the payoff / punchline / conclusion.
- The first sentence must hook instantly. Never start on a sentence that depends on earlier context \
("and", "so", "that's why", "he", "یہ", "تو"...).
- You MAY cut: use several parts (ranges of sentence ids, chronological order) to skip weak, repetitive or \
rambling sentences inside a moment. Keep it natural, max 4 parts.
- Optional "cold_open": the id of ONE short, gripping sentence from inside the Short to play first as a teaser.
- Shorts must not overlap each other. Rank best first. Give each a "score" 1-10 for viral potential.
- "title": a scroll-stopping hook for the on-screen heading, max 7 words, written in {title_lang}.

Reply with JSON only:
{{"shorts":[{{"title":"...","parts":[[first_id,last_id],...],"cold_open":null,"score":8,"why":"one line"}}]}}

Transcript:
{lines}"""


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def call_claude(prompt: str, api_key: str, model: str, max_tokens: int = 4000, timeout: int = 240) -> str:
    """Kept for the Settings "Test key" button and older callers."""
    from .llm import claude_generate
    return claude_generate(prompt, api_key, model, max_tokens=max_tokens, timeout=timeout)


def ai_director(transcript: dict, energy: np.ndarray, duration: float, n: int, min_len: float, max_len: float,
                ai, title_lang: str = "the same language and script as the transcript", min_gap: float = 5.0,
                log: Log = None) -> list[Clip]:
    words = flatten_words(transcript)
    rows = _sentence_table(words, energy)
    if len(rows) < 8:
        return local_director(transcript, energy, duration, n, min_len, max_len, min_gap, log=log)
    lines = "\n".join(f"[{r['i']}] {_fmt(r['s'])}-{_fmt(r['e'])} | {r['text'][:400]}" for r in rows)
    if log:
        log(f"Asking {ai.label} to find the most viral moments…")
    data = ai.ask_json(PROMPT.format(n=n, min_len=min_len, max_len=max_len, lines=lines, title_lang=title_lang))
    shorts = data.get("shorts", []) if isinstance(data, dict) else data
    by_id = {r["i"]: r for r in rows}
    clips, used = [], []
    sep = min(min_gap, 1.0)
    for sh in sorted(shorts, key=lambda x: -float(x.get("score", 5) or 5)):
        sel = []
        for part in (sh.get("parts") or [])[:5]:
            try:
                a, b = int(part[0]), int(part[1])
            except (TypeError, ValueError, IndexError):
                continue
            sel += [by_id[i] for i in range(min(a, b), max(a, b) + 1) if i in by_id]
        seen, uniq = set(), []
        for r in sorted(sel, key=lambda r: r["i"]):
            if r["i"] not in seen:
                seen.add(r["i"])
                uniq.append(r)
        sel = uniq
        if not sel:
            continue
        while sum(r["dur"] for r in sel) > max_len * 1.1 and len(sel) > 1:  # respect the length limit
            sel.pop()
        dur = sum(r["dur"] for r in sel)
        if dur < min_len * 0.6:
            continue
        a, b = sel[0]["s"], sel[-1]["e"]
        if any(not (b + sep <= u0 or a >= u1 + sep) for u0, u1 in used):
            continue
        co = sh.get("cold_open")
        cold = by_id.get(int(co)) if isinstance(co, (int, float)) or (isinstance(co, str) and co.isdigit()) else None
        if cold and (cold not in sel or cold["dur"] > 9 or dur + cold["dur"] > max_len * 1.1):
            cold = None
        clip = _as_clip(sel, cold, words, float(sh.get("score", 5) or 5),
                        {"type": f"{ai.label} pick", "why": str(sh.get("why", ""))[:200], "cold_open": bool(cold),
                         "ai_title": bool(sh.get("title"))})
        if sh.get("title"):
            clip.title = str(sh["title"]).strip().strip('"')[:80]
        clips.append(clip)
        used.append((a, b))
        if len(clips) >= n:
            break
    if log:
        for c in clips:
            log(f"  • {int(c.start // 60)}:{int(c.start % 60):02d} ({c.duration:.0f}s, score {c.score:.0f}) "
                f"{c.title} — {c.reasons.get('why', '')}")
    if len(clips) < n:  # top up with the local picks that don't overlap
        for c in local_director(transcript, energy, duration, n * 2, min_len, max_len, min_gap):
            if all(c.end + sep <= u0 or c.start >= u1 + sep for u0, u1 in used):
                clips.append(c)
                used.append((c.start, c.end))
            if len(clips) >= n:
                break
    clips.sort(key=lambda c: c.start)
    return clips


def claude_director(transcript, energy, duration, n, min_len, max_len, api_key, model="claude-sonnet-5",
                    min_gap=5.0, log: Log = None, _call=None):
    """Backwards-compatible wrapper (tests pass a fake `_call`)."""
    from .llm import AI

    class _Fake(AI):
        def ask(self, prompt, json_mode=True, timeout=200):
            return _call(prompt, api_key, model) if _call else super().ask(prompt, json_mode, timeout)
    return ai_director(transcript, energy, duration, n, min_len, max_len, _Fake("claude", api_key, model, log),
                       min_gap=min_gap, log=log)


def title_language(s, transcript: dict) -> str:
    cap = getattr(s, "caption_lang", "roman")
    spoken = transcript.get("spoken") or transcript.get("language", "en")
    if cap == "en" or spoken == "en":
        return "English"
    if cap == "roman":
        return ("Roman Urdu using Latin letters like Pakistanis type on phones (e.g. \"Yeh ghalti kabhi mat karna\")"
                if spoken != "hi" else "Roman Hindi using Latin letters (e.g. \"Yeh galti kabhi mat karna\")")
    return "the same language and script as the transcript"


def pick(transcript: dict, energy: np.ndarray, duration: float, s, log: Log = None, ai=None) -> list[Clip]:
    """Entry point used by the pipeline (s = Settings)."""
    from .llm import AI
    ai = ai or AI.from_settings(s, log)
    if ai is not None:
        try:
            return ai_director(transcript, energy, duration, s.shorts_per_video, s.min_duration, s.max_duration,
                               ai, title_language(s, transcript), s.min_gap, log)
        except Exception as e:
            if log:
                log(f"{ai.label} unavailable ({str(e)[:160]}); using the built-in director.")
    return local_director(transcript, energy, duration, s.shorts_per_video, s.min_duration, s.max_duration,
                          s.min_gap, getattr(s, "cold_open", True), log)
