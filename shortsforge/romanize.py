"""Roman Urdu / Roman Hindi captions: "کیا کر رہے ہو" -> "kya kar rahe ho".

Speech is transcribed in its own script (best accuracy), then every word inside the chosen Shorts is
transliterated one-to-one so caption timing stays exact. The AI editor (Gemini/Claude) does it best;
a built-in dictionary + letter rules is the offline fallback.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .config import CACHE_DIR

ROMAN_DIR = CACHE_DIR / "roman"
ROMAN_DIR.mkdir(parents=True, exist_ok=True)
NON_LATIN = re.compile(r"[֐-ࣿऀ-෿Ѐ-ӿ฀-࿿぀-ヿ一-鿿가-힯]")


def needs_roman(text: str) -> bool:
    return bool(NON_LATIN.search(text or ""))


# ---------------------------------------------------------------------------------------------- offline
COMMON = {
    # pronouns / basics
    "میں": "main", "مَیں": "main", "ہم": "hum", "تم": "tum", "آپ": "aap", "وہ": "woh", "یہ": "yeh", "اس": "is",
    "ان": "in", "اسے": "isay", "انہیں": "unhein", "انہوں": "unhon", "ہمیں": "humein", "تمہیں": "tumhein",
    "مجھے": "mujhe", "مجھ": "mujh", "میرا": "mera", "میری": "meri", "میرے": "mere", "تمہارا": "tumhara",
    "آپکا": "aapka", "اپنا": "apna", "اپنی": "apni", "اپنے": "apne", "ہمارا": "hamara", "ہماری": "hamari",
    "ہمارے": "hamare", "کوئی": "koi", "کچھ": "kuch", "سب": "sab", "سارے": "saare", "کون": "kaun",
    # verbs / auxiliaries
    "ہے": "hai", "ہیں": "hain", "ہوں": "hoon", "ہو": "ho", "تھا": "tha", "تھی": "thi", "تھے": "thay",
    "گا": "ga", "گی": "gi", "گے": "ge", "کر": "kar", "کرنا": "karna", "کرتے": "karte", "کرتا": "karta",
    "کرتی": "karti", "کیا": "kya", "کیے": "kiye", "کی": "ki", "کے": "ke", "کا": "ka", "کو": "ko",
    "رہا": "raha", "رہی": "rahi", "رہے": "rahe", "رہ": "reh", "گیا": "gaya", "گئی": "gayi", "گئے": "gaye",
    "جا": "ja", "جائے": "jaye", "جاتا": "jata", "جاتی": "jati", "جاتے": "jatay", "دیا": "diya", "دی": "di",
    "دے": "de", "دو": "do", "لیا": "liya", "لے": "le", "لو": "lo", "ہوا": "hua", "ہوئی": "hui", "ہوئے": "huay",
    "ہوتا": "hota", "ہوتی": "hoti", "ہوتے": "hote", "سکتا": "sakta", "سکتی": "sakti", "سکتے": "saktay",
    "چاہیے": "chahiye", "چاہتا": "chahta", "چاہتے": "chahte", "بولا": "bola", "کہا": "kaha", "کہتے": "kehte",
    "دیکھو": "dekho", "دیکھیں": "dekhein", "سنو": "suno", "آیا": "aaya", "آئی": "aayi", "آئے": "aaye",
    "بتاؤ": "batao", "بتایا": "bataya", "سمجھ": "samajh", "لگتا": "lagta", "لگا": "laga", "ملا": "mila",
    # particles / connectors
    "نہیں": "nahi", "نہ": "na", "نا": "na", "بھی": "bhi", "تو": "to", "اور": "aur", "لیکن": "lekin",
    "مگر": "magar", "کیونکہ": "kyunke", "کیوں": "kyun", "کیسے": "kaise", "کب": "kab", "کہاں": "kahan",
    "جب": "jab", "تب": "tab", "اگر": "agar", "پھر": "phir", "ابھی": "abhi", "بس": "bas", "یا": "ya",
    "سے": "se", "میں۔": "mein", "پر": "par", "تک": "tak", "والا": "wala", "والی": "wali", "والے": "walay",
    "جو": "jo", "جس": "jis", "بہت": "bohat", "زیادہ": "zyada", "کم": "kam", "صرف": "sirf", "بالکل": "bilkul",
    "ہاں": "haan", "جی": "ji", "اچھا": "acha", "اچھی": "achi", "برا": "bura", "ٹھیک": "theek", "سچ": "sach",
    # nouns frequent in talk
    "بات": "baat", "لوگ": "log", "لوگوں": "logon", "کام": "kaam", "پیسہ": "paisa", "پیسے": "paisay",
    "زندگی": "zindagi", "وقت": "waqt", "دن": "din", "سال": "saal", "دنیا": "duniya", "ملک": "mulk",
    "پاکستان": "Pakistan", "اللہ": "Allah", "دل": "dil", "گھر": "ghar", "بچے": "bachay", "آدمی": "aadmi",
    "طرح": "tarah", "چیز": "cheez", "چیزیں": "cheezein", "مسئلہ": "masla", "سوال": "sawal", "جواب": "jawab",
    "کہانی": "kahani", "راز": "raaz", "غلطی": "ghalti", "کامیابی": "kamyabi", "محنت": "mehnat",
    "ایک": "aik", "دو۔": "do", "تین": "teen", "چار": "chaar", "پانچ": "paanch", "دس": "das", "سو": "sau",
    "پتا": "pata", "پتہ": "pata", "بزنس": "business", "چلے": "chalay", "چلا": "chala", "چلو": "chalo",
    "بڑی": "bari", "بڑا": "bara", "بڑے": "baray", "چھوٹا": "chota", "کہ": "ke", "دیے": "diye", "نے": "ne",
    "یار": "yaar", "بھائی": "bhai", "پیارے": "pyare", "دوست": "dost", "کمپنی": "company", "مارکیٹ": "market",
    "یوٹیوب": "YouTube", "ویڈیو": "video", "چینل": "channel", "فون": "phone", "آئیڈیا": "idea",
    "پہلا": "pehla", "پہلی": "pehli", "کھویا": "khoya", "کھو": "kho", "تر": "tar", "کامیاب": "kamyab",
    "بتاؤں": "bataun", "بھروسہ": "bharosa", "مہینے": "mahine", "مہینہ": "mahina", "ختم": "khatam",
    "سیکھی": "seekhi", "سیکھا": "seekha", "جلدی": "jaldi", "ضروری": "zaroori", "ہار": "haar", "مان": "maan",
    "روز": "roz", "تھوڑا": "thora", "تھوڑی": "thori", "چھوڑ": "chor", "اصل": "asal", "ڈیل": "deal",
    "سائن": "sign", "آفس": "office", "خیر": "khair", "بدل": "badal", "دیکھا": "dekha", "لیتے": "lete",
    "دیتے": "dete", "کرو": "karo", "رکو": "ruko", "ہوں۔": "hoon", "بار": "baar",
    "ہزار": "hazaar", "لاکھ": "lakh", "کروڑ": "crore", "پہلے": "pehle", "بعد": "baad", "آج": "aaj", "کل": "kal",
}
# letter-by-letter fallback (reasonable, not perfect: Urdu omits most short vowels)
CHARS = {
    "ا": "a", "آ": "aa", "ب": "b", "پ": "p", "ت": "t", "ٹ": "t", "ث": "s", "ج": "j", "چ": "ch", "ح": "h",
    "خ": "kh", "د": "d", "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r", "ز": "z", "ژ": "zh", "س": "s", "ش": "sh",
    "ص": "s", "ض": "z", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "ك": "k",
    "گ": "g", "ل": "l", "م": "m", "ن": "n", "ں": "n", "و": "o", "ہ": "h", "ھ": "h", "ۂ": "h", "ة": "h", "ء": "",
    "ی": "i", "ي": "i", "ے": "e", "ئ": "i", "ۓ": "e", "ؤ": "o", "أ": "a", "إ": "i", "ٰ": "a", "َ": "a", "ِ": "i",
    "ُ": "u", "ّ": "", "ْ": "", "۔": ".", "،": ",", "؟": "?",
}
DEVANAGARI = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ट": "t", "ठ": "th",
    "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph",
    "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s",
    "ह": "h", "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ं": "n", "ँ": "n", "़": "", "्": "", "ः": "h", "।": ".", "ज़": "z", "फ़": "f", "क़": "q", "ख़": "kh", "ग़": "gh",
}


def offline_word(w: str) -> str:
    core = re.sub(r"[۔،؟!?.,]+$", "", w)
    tail = w[len(core):].replace("۔", ".").replace("،", ",").replace("؟", "?")
    if core in COMMON:
        return COMMON[core] + tail
    if re.search(r"[ऀ-ॿ]", core):
        out = "".join(DEVANAGARI.get(c, c) for c in core)
    else:
        out = ""
        for i, c in enumerate(core):
            r = CHARS.get(c, c)
            # و / ی between consonants usually read as long vowels; at word start as consonants
            if c == "و" and i == 0:
                r = "w"
            if c in "یي" and i == 0:
                r = "y"
            out += r
        out = re.sub(r"(.)\1{2,}", r"\1\1", out)
    return (out or w) + tail


# ---------------------------------------------------------------------------------------------- AI
PROMPT = """Transliterate every word below into {target}, the way Pakistanis/Indians type it on phones \
(e.g. "کیا کر رہے ہو" -> "kya kar rahe ho", "مجھے نہیں پتا" -> "mujhe nahi pata"). English words written in \
Urdu/Hindi script must come back in normal English spelling (e.g. "بزنس" -> "business"). Keep numbers as digits.
Return EXACTLY one output word per input word, same order, same count. Keep trailing punctuation (?, !, .).
Reply with JSON only: {{"items":[{{"id":1,"roman":["...","..."]}}]}}

Input:
{items}"""


def _cache_path(vid: str):
    return ROMAN_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', vid)}.json"


def romanize_transcript(transcript: dict, ranges: list[tuple[float, float]], vid: str, ai=None,
                        log: Optional[Callable[[str], None]] = None) -> int:
    """Rewrite non-Latin words inside `ranges` (absolute seconds) in place. Returns words converted."""
    cache_file = _cache_path(vid)
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    except Exception:
        cache = {}
    in_range = lambda w: any(a - 0.05 <= w["s"] <= b + 0.3 for a, b in ranges)
    groups = []  # per transcript segment: list of word dicts to convert
    for seg in transcript.get("segments", []):
        ws = [w for w in seg.get("words", []) if in_range(w) and needs_roman(w["w"])]
        if ws:
            groups.append(ws)
    if not groups:
        return 0
    key = lambda w: f"{w['s']:.2f}|{w['w']}"
    todo = [g for g in groups if any(key(w) not in cache for w in g)]
    if todo and ai is not None:
        spoken = transcript.get("spoken") or transcript.get("language") or "ur"
        target = "Roman Hindi (Latin letters)" if spoken == "hi" else "Roman Urdu (Latin letters)"
        for start in range(0, len(todo), 120):  # keep each request comfortably small
            batch = todo[start:start + 120]
            items = "\n".join(json.dumps({"id": i + 1, "words": [w["w"] for w in g]}, ensure_ascii=False)
                              for i, g in enumerate(batch))
            try:
                data = ai.ask_json(PROMPT.format(target=target, items=items))
                for it in data.get("items", []):
                    i = int(it.get("id", 0)) - 1
                    if 0 <= i < len(batch) and len(it.get("roman", [])) == len(batch[i]):
                        for w, r in zip(batch[i], it["roman"]):
                            r = str(r).strip()
                            if r and not needs_roman(r):
                                cache[key(w)] = r
            except Exception as e:
                if log:
                    log(f"AI transliteration unavailable ({e}); using the built-in Roman Urdu converter.")
                break
    n = 0
    rmap = transcript.setdefault("_roman_map", {})
    for g in groups:
        for w in g:
            orig = w["w"]
            w["w"] = cache.get(key(w)) or offline_word(orig)
            rmap.setdefault(re.sub(r"[۔،؟!?.,]+$", "", orig), re.sub(r"[.,?!]+$", "", w["w"]))
            n += 1
    try:
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return n


def romanize_text(text: str, transcript: dict) -> str:
    """Convert a title/hook that still contains Urdu script, reusing the caption spellings."""
    if not needs_roman(text):
        return text
    rmap = transcript.get("_roman_map", {})
    out = []
    for t in text.split():
        if not needs_roman(t):
            out.append(t)
            continue
        core = re.sub(r"[۔،؟!?.,]+$", "", t)
        tail = t[len(core):].replace("۔", ".").replace("،", ",").replace("؟", "?")
        out.append((rmap.get(core) or offline_word(core)) + tail)
    res = " ".join(out)
    return res[:1].upper() + res[1:]
