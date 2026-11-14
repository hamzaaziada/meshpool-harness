"""Inference providers: where a model runs. Picked per thread in the model picker, next to the harness (which agent
runs).

A model id carries its provider as a prefix; an id without one runs on the API key endpoint in settings (the
Meshpool endpoint, or another OpenAI-compatible endpoint the user set):

  deepseek-v4.1-flash          key        the Meshpool key (or the custom endpoint) in settings
  chatgpt:gpt-6.1-sol          chatgpt    the user's ChatGPT sign-in (core/chatgpt.py)
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from meshpool_harness import config as C

PREFIXED = ("chatgpt",)
PROVIDERS = ("key", "chatgpt")

# Which providers each built-in harness can run on, in order of preference.
SUPPORTS = {
    "meshpool-code": ["key", "chatgpt"],
    "meshpool-work": ["key", "chatgpt"],
    "meshpool-auto": ["key", "chatgpt"],
    "pi": ["key", "chatgpt"],         # ChatGPT through the local relay (chatgpt_relay.py)
    "hermes": ["key", "chatgpt"],
    "codex": ["key", "chatgpt"],
}


def _cfg(k: str) -> str:
    return (C.read().get(k) or os.environ.get(k) or "").strip()


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _openai_pair() -> tuple[str, str]:
    """(key, base URL) from OPENAI_API_KEY and OPENAI_BASE_URL, only when there is no Meshpool key and both are set:
    an OpenAI key alone must never be sent to the Meshpool endpoint."""
    if _cfg("MESHPOOL_API_KEY"):
        return "", ""
    k, b = os.environ.get("OPENAI_API_KEY", "").strip(), os.environ.get("OPENAI_BASE_URL", "").strip()
    return (k, b) if k and b else ("", "")


def key_base() -> str:
    return (_openai_pair()[1] or _cfg("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")


def key() -> str:
    return _cfg("MESHPOOL_API_KEY") or _openai_pair()[0]


def key_name() -> str:
    h = host(key_base())
    return "Meshpool" if "meshpool" in h else (h or "Your endpoint")


def name(prov: str) -> str:
    return {"key": key_name(), "chatgpt": "ChatGPT subscription"}[prov]


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
    return {"key": lambda: bool(key()),
            "chatgpt": lambda: HR.signed_in("codex")}[prov]()


def setup(prov: str) -> str:
    return {"key": "Add your Meshpool key: Settings, Account.",
            "chatgpt": "Sign in with ChatGPT: Settings, Account."}[prov]


def endpoint(prov: str) -> tuple[str, str]:
    """(base URL, key) of an OpenAI-compatible provider. ChatGPT: this process's local relay, for agents that speak
    chat completions; the sign-in itself never leaves Meshpool."""
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt_relay
        return chatgpt_relay.ensure()
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
    """The provider of the default model (the sign-up choice): key or chatgpt."""
    p = _cfg("MH_PROVIDER") or "meshpool"
    return "chatgpt" if p == "chatgpt" else "key"


# ---------- model lists for the picker ----------------------------------------------------------------------------

def list_models(prov: str, refresh: bool = False) -> tuple[list[str], str]:
    """(model ids without prefix, error) for a provider other than the key endpoint (workbench lists that one)."""
    if prov == "chatgpt":
        from meshpool_harness.core import chatgpt as CG
        if refresh:
            CG._MODELS.update(t=0.0)
        ids = CG.models()
        d = CG.default_model()
        note = CG.limit_note() or ("" if ids else "Showing the default model; the plan's list did not load.")
        return list(dict.fromkeys([d] + ids)), note
    return [], ""


def groups(key_rows: list[dict], key_error: str, refresh: bool = False) -> list[dict]:
    """The model picker's sections, one per provider, ready or not."""
    out = []
    for prov in PROVIDERS:
        ok = ready(prov)
        g = {"id": prov, "name": name(prov), "ready": ok, "setup": "" if ok else setup(prov),
             "models": [], "error": ""}
        if prov == "key":
            g["models"], g["error"] = (key_rows, key_error) if ok else ([], "")
        elif ok:
            ids, err = list_models(prov, refresh)
            g["models"] = [{"id": join(prov, i), "name": i, "provider": "OpenAI", "caps": ["vision"]} for i in ids]
            g["error"] = err
        out.append(g)
    return out
