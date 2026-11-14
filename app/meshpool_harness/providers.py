"""Inference providers: where a model runs. Picked per thread in the model picker, next to the harness (which agent
runs).

A model id carries its provider as a prefix; an id without one runs on the API key endpoint in settings (the
Meshpool endpoint, or another OpenAI-compatible endpoint the user set):

  deepseek-v4.1-flash          key        the Meshpool key (or the custom endpoint) in settings
  chatgpt:gpt-6.1-sol          chatgpt    the user's ChatGPT sign-in (core/chatgpt.py)
  anthropic:claude-...         anthropic  an Anthropic API key, through Anthropic's OpenAI-compatible endpoint
  gemini:gemini-...            gemini     a Gemini API key (Google AI Studio), OpenAI-compatible endpoint
  claude:default               claude     the user's Claude sign-in: runs only in Claude Code
  google:default               google     the user's Google sign-in: runs only in Gemini CLI

Claude and Google sign-ins run only in their makers' own agents: Anthropic's terms do not let other apps use a
claude.ai sign-in, and Google's terms forbid using a Gemini CLI or Antigravity sign-in from third-party tools.
Meshpool never reads or forwards those tokens.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from meshpool_harness import config as C

PREFIXED = ("chatgpt", "anthropic", "gemini", "claude", "google")
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
SIGNIN_ONLY = {"claude": "claude-code", "google": "gemini-cli"}   # a sign-in that only its maker's agent may use

# Which providers each built-in harness can run on, in order of preference.
SUPPORTS = {
    "meshpool-code": ["key", "chatgpt", "anthropic", "gemini"],
    "meshpool-work": ["key", "chatgpt", "anthropic", "gemini"],
    "meshpool-auto": ["key", "chatgpt", "anthropic", "gemini"],
    "pi": ["key", "anthropic", "gemini"],
    "hermes": ["key", "anthropic", "gemini"],
    "codex": ["key", "chatgpt"],
    "claude-code": ["claude", "anthropic"],
    "gemini-cli": ["google", "gemini"],
}
WHY_NOT = {
    ("pi", "chatgpt"): "Pi cannot use a ChatGPT sign-in yet (two programs refreshing one sign-in sign each other out).",
    ("hermes", "chatgpt"): "Hermes cannot use a ChatGPT sign-in in Meshpool yet.",
    ("codex", "anthropic"): "Codex runs on OpenAI-style endpoints only.",
    ("codex", "gemini"): "Codex runs on OpenAI-style endpoints only.",
}


def _cfg(k: str) -> str:
    return (C.read().get(k) or os.environ.get(k) or "").strip()


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def key_base() -> str:
    return (_cfg("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")


def key() -> str:
    return _cfg("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY", "").strip()


def anthropic_key() -> str:
    k = _cfg("ANTHROPIC_API_KEY")
    if not k and host(key_base()) == "api.anthropic.com":
        k = key()
    return k


def gemini_key() -> str:
    return _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY")


def key_name() -> str:
    h = host(key_base())
    return "Meshpool" if "meshpool" in h else (h or "Your endpoint")


def name(prov: str) -> str:
    return {"key": key_name(), "chatgpt": "ChatGPT subscription", "anthropic": "Claude (Anthropic API key)",
            "gemini": "Gemini (API key)", "claude": "Claude subscription", "google": "Gemini (Google sign-in)"}[prov]


def split(model: str | None) -> tuple[str, str]:
    """(provider, model id the provider knows) for a model id from the picker or settings."""
    m = (model or "").strip()
    head, sep, rest = m.partition(":")
    if sep and head in PREFIXED:
        return head, rest
    if (os.environ.get("MH_PROVIDER") or C.read().get("MH_PROVIDER")) == "chatgpt" and not key():
        return "chatgpt", m                      # set up with only a ChatGPT sign-in
    return "key", m


def join(prov: str, model: str) -> str:
    return model if prov == "key" else f"{prov}:{model}"


def ready(prov: str) -> bool:
    from meshpool_harness import harnesses as HR
    return {"key": lambda: bool(key()) and host(key_base()) != "api.anthropic.com",
            "chatgpt": lambda: HR.signed_in("codex"),
            "anthropic": lambda: bool(anthropic_key()),
            "gemini": lambda: bool(gemini_key()),
            "claude": lambda: HR.signed_in("claude"),
            "google": lambda: HR.signed_in("gemini")}[prov]()


def setup(prov: str) -> str:
    return {"key": "Add your Meshpool key: Settings, Account.",
            "chatgpt": "Sign in with ChatGPT: Settings, Account.",
            "anthropic": "Add an Anthropic API key: Settings, Account.",
            "gemini": "Add a Gemini API key (free at aistudio.google.com): Settings, Account.",
            "claude": "Sign in with your Claude account: Settings, Account.",
            "google": "Sign in with your Google account: Settings, Account."}[prov]


def endpoint(prov: str) -> tuple[str, str]:
    """(base URL, key) of an OpenAI-compatible provider."""
    if prov == "anthropic":
        return ANTHROPIC_BASE, anthropic_key()
    if prov == "gemini":
        return GEMINI_BASE, gemini_key()
    return key_base(), key()


def for_harness(hid: str, model: str | None = None) -> str | None:
    """The provider a harness runs on: the model's provider when the harness supports it and it is ready, else the
    first ready one it supports (the provider chosen at sign-up first)."""
    sup = SUPPORTS.get(hid)
    if not sup:
        return None
    if model:
        p, _ = split(model)
        if p in sup and ready(p):
            return p
    chosen = default_provider()
    order = ([chosen] if chosen in sup else []) + sup
    return next((p for p in order if ready(p)), None)


def default_provider() -> str:
    """The provider of the default model (the sign-up choice): key, chatgpt, claude ..."""
    p = _cfg("MH_PROVIDER") or "meshpool"
    return {"meshpool": "key", "custom": "key"}.get(p, p)


# ---------- model lists for the picker ----------------------------------------------------------------------------

_CACHE: dict = {}


def _get(url: str, headers: dict, ttl: float = 300) -> dict | None:
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Meshpool", **headers}),
                                    timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    _CACHE[url] = (time.time(), data)
    return data


def list_models(prov: str, refresh: bool = False) -> tuple[list[str], str]:
    """(model ids without prefix, error) for a provider other than the key endpoint (workbench lists that one)."""
    if refresh:
        _CACHE.clear()
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if refresh:
            CG._MODELS.update(t=0.0)
        ids = CG.models()
        d = CG.default_model()
        return list(dict.fromkeys([d] + ids)), "" if ids else "Showing the default model; the plan's list did not load."
    if prov == "anthropic":
        d = _get(ANTHROPIC_BASE + "/models?limit=100", {"x-api-key": anthropic_key(), "anthropic-version": "2023-06-01"})
        ids = [m["id"] for m in (d or {}).get("data") or [] if m.get("id")]
        return ids, "" if ids else "Could not list Anthropic models. Check the key."
    if prov == "gemini":
        d = _get(GEMINI_BASE + "/models", {"Authorization": f"Bearer {gemini_key()}"})
        ids = [str(m["id"]).removeprefix("models/") for m in (d or {}).get("data") or [] if m.get("id")]
        ids = [i for i in ids if i.startswith("gemini") and not any(x in i for x in ("embedding", "tts", "image", "aqa"))]
        return ids, "" if ids else "Could not list Gemini models. Check the key."
    if prov in SIGNIN_ONLY:
        return ["default"], ""
    return [], ""


def groups(key_rows: list[dict], key_error: str, refresh: bool = False) -> list[dict]:
    """The model picker's sections, one per provider, ready or not."""
    out = []
    for prov in ("key", "chatgpt", "anthropic", "claude", "gemini", "google"):
        ok = ready(prov)
        g = {"id": prov, "name": name(prov), "ready": ok, "setup": "" if ok else setup(prov),
             "only": SIGNIN_ONLY.get(prov), "models": [], "error": ""}
        if prov == "key":
            g["models"], g["error"] = (key_rows, key_error) if ok else ([], "")
        elif ok:
            ids, err = list_models(prov, refresh)
            vendor = {"chatgpt": "OpenAI", "anthropic": "Anthropic", "gemini": "Google", "claude": "Anthropic",
                      "google": "Google"}[prov]
            g["models"] = [{"id": join(prov, i), "name": i, "provider": vendor,
                            "caps": [] if prov in SIGNIN_ONLY else ["vision"]} for i in ids]
            g["error"] = err
        out.append(g)
    return out
