"""Tiny clients for the AI editors (Gemini free tier / Claude), no extra packages needed.

Gemini: the best available *Flash* model is discovered from your key (Google renames models often),
cached for a day, and we fall back to Flash-Lite when a daily quota is hit.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from .config import data_dir

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_MODEL_CACHE = data_dir() / "gemini_models.json"


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """Free-tier limit reached (HTTP 429)."""


def _http(url: str, body: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 180) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(detail).get("error", {}).get("message", detail)
        except Exception:
            msg = detail
        msg = str(msg)[:300]
        if e.code == 429:
            raise QuotaError(f"quota reached: {msg}") from None
        raise LLMError(f"HTTP {e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"network error: {e.reason}") from None


# ------------------------------------------------------------------------------------------------ Gemini
def _version_key(name: str) -> tuple:
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", name.split("gemini-")[-1])[:1]] or [0.0]
    stable = 0 if re.search(r"preview|exp|\d{2}-\d{2}", name) else 1
    return (stable, nums[0])


def gemini_models(key: str, refresh: bool = False) -> dict:
    """{'flash': [...best first], 'lite': [...best first]} usable text models for this key."""
    if not refresh and _MODEL_CACHE.exists():
        try:
            c = json.loads(_MODEL_CACHE.read_text(encoding="utf-8"))
            if time.time() - c.get("t", 0) < 86400 and c.get("k") == key[-6:]:
                return c["m"]
        except Exception:
            pass
    names, token = [], ""
    for _ in range(5):
        url = f"{GEMINI_API}/models?pageSize=200&key={key}" + (f"&pageToken={token}" if token else "")
        d = _http(url, timeout=30)
        for m in d.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(m["name"].split("/")[-1])
        token = d.get("nextPageToken", "")
        if not token:
            break
    bad = re.compile(r"tts|live|image|audio|vision|embed|transcribe|omni|robotics|computer|thinking|learnlm|gemma|aqa")
    flash = [n for n in names if "flash" in n and "lite" not in n and not bad.search(n)]
    lite = [n for n in names if "flash-lite" in n and not bad.search(n)]
    # "-latest" aliases always point at the current model: try them first
    order = lambda xs: sorted(xs, key=lambda n: ("latest" in n, _version_key(n)), reverse=True)
    res = {"flash": order(flash), "lite": order(lite)}
    try:
        _MODEL_CACHE.write_text(json.dumps({"t": time.time(), "k": key[-6:], "m": res}), encoding="utf-8")
    except OSError:
        pass
    return res


def gemini_generate(prompt: str, key: str, model: str = "auto", json_mode: bool = True, timeout: int = 180,
                    log=None) -> str:
    key = key.strip()
    if model and model != "auto":
        chain = [model]
    else:
        ms = gemini_models(key)
        chain = ms["flash"][:2] + ms["lite"][:2] or ["gemini-flash-latest", "gemini-flash-lite-latest"]
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, **({"responseMimeType": "application/json"} if json_mode else {})}}
    last: Exception = LLMError("no Gemini model available for this key")
    for m in chain:
        try:
            d = _http(f"{GEMINI_API}/models/{m}:generateContent?key={key}", body, timeout=timeout)
        except QuotaError as e:
            last = e
            if log:
                log(f"Gemini {m}: daily free limit reached, trying a lighter model…")
            continue
        except LLMError as e:
            last = e
            if "HTTP 404" in str(e) or "HTTP 400" in str(e) and "model" in str(e).lower():
                continue
            if "HTTP 5" in str(e):
                continue
            raise
        parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if text.strip():
            if log:
                log(f"Gemini model used: {m}")
            return text
        last = LLMError(f"empty reply from {m} ({(d.get('candidates') or [{}])[0].get('finishReason', '?')})")
    raise last


# ------------------------------------------------------------------------------------------------ Claude
def claude_generate(prompt: str, key: str, model: str = "claude-sonnet-5", max_tokens: int = 8000,
                    timeout: int = 240, log=None) -> str:
    d = _http("https://api.anthropic.com/v1/messages",
              {"model": model or "claude-sonnet-5", "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]},
              {"x-api-key": key.strip(), "anthropic-version": "2023-06-01"}, timeout=timeout)
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


# ------------------------------------------------------------------------------------------------ facade
class AI:
    """Whichever AI editor the user configured; `ask()` returns text (JSON when asked)."""

    def __init__(self, provider: str, key: str, model: str = "auto", log=None):
        self.provider, self.key, self.model, self.log = provider, (key or "").strip(), model, log

    @classmethod
    def from_settings(cls, s, log=None) -> Optional["AI"]:
        p = getattr(s, "clip_picker", "local")
        if p == "gemini" and getattr(s, "gemini_api_key", "").strip():
            return cls("gemini", s.gemini_api_key, getattr(s, "gemini_model", "auto") or "auto", log)
        if p == "claude" and getattr(s, "anthropic_api_key", "").strip():
            return cls("claude", s.anthropic_api_key, getattr(s, "claude_model", "claude-sonnet-5"), log)
        return None

    @property
    def label(self) -> str:
        return "Gemini" if self.provider == "gemini" else "Claude"

    def ask(self, prompt: str, json_mode: bool = True, timeout: int = 200) -> str:
        if self.provider == "gemini":
            return gemini_generate(prompt, self.key, self.model, json_mode, timeout, self.log)
        return claude_generate(prompt, self.key, self.model, timeout=timeout, log=self.log)

    def ask_json(self, prompt: str, timeout: int = 200) -> dict:
        text = self.ask(prompt, True, timeout)
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if not m:
            raise LLMError("AI reply had no JSON")
        return json.loads(m.group(0))


def test_key(provider: str, key: str, model: str = "auto") -> str:
    """Returns a short human message; raises on failure."""
    if provider == "gemini":
        ms = gemini_models(key.strip(), refresh=True)
        if not (ms["flash"] or ms["lite"]):
            raise LLMError("key works but no Gemini Flash model is available to it")
        reply = gemini_generate('Reply with JSON {"ok": true}', key, model, True, 30)
        return f"Key works · model {ms['flash'][0] if ms['flash'] else ms['lite'][0]} · reply {reply.strip()[:30]}"
    reply = claude_generate("Reply with the single word: ready", key, model, max_tokens=10, timeout=30)
    return f"Key works · reply {reply.strip()[:20]}"
