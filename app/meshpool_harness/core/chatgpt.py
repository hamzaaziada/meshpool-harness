"""ChatGPT subscription as a model provider for Meshpool's own agents (MH_PROVIDER=chatgpt).

The user signs in once with the official Codex sign-in (`codex login`, which writes ~/.codex/auth.json). Meshpool
then sends its model calls to the ChatGPT Codex endpoint with that sign-in, the way open-source harnesses such as
Codex CLI (Apache-2.0), OpenCode and Hermes Agent (MIT) do:

  POST https://chatgpt.com/backend-api/codex/responses     Responses API, stream: true, store: false
  Authorization: Bearer <access_token>                     from ~/.codex/auth.json
  ChatGPT-Account-ID: <chatgpt_account_id>                 from the token's "https://api.openai.com/auth" claim
  originator: meshpool, User-Agent: Meshpool/<version>     OpenAI asks third-party harnesses to name themselves

Token refresh follows Codex: refresh shortly before the access token expires, and once on a 401, at
https://auth.openai.com/oauth/token with the refresh token. Refresh tokens are single-use and shared with the Codex
CLI, so the refresh runs under a file lock, re-reads auth.json first, adopts a pair another program already rotated,
and writes the new pair back to auth.json so Codex keeps working too.

Scope: OpenAI allows this for local and open-source apps and asks them to move to "Sign in with ChatGPT" (SIWC);
it has never been allowed for hosted or commercial services. Meshpool runs it only on the user's own computer, with
the user's own sign-in. Claude subscriptions are never used here (see harnesses.credentials).
"""
from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE_URL = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"        # the public Codex CLI OAuth client its auth.json tokens belong to
REFRESH_SKEW_S = 120                               # refresh this long before the access token expires
REFRESH_EVERY_S = 8 * 24 * 3600                    # Codex also refreshes a token this old, whatever its expiry
DEFAULT_MODEL = "gpt-6.1-sol"
# Pi and Hermes reach the sign-in through the local relay (chatgpt_relay.py), so this process stays its only refresher.


class NotSignedIn(RuntimeError):
    pass


def base_url() -> str:
    return (os.environ.get("MH_CHATGPT_BASE_URL") or BASE_URL).rstrip("/")


def token_url() -> str:
    return os.environ.get("MH_CHATGPT_TOKEN_URL") or TOKEN_URL


def auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("meshpool-harness")
    except Exception:  # noqa: BLE001
        return "0"


def _claims(jwt: str) -> dict:
    try:
        part = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return {}


def account_headers(access_token: str, tokens: dict | None = None) -> dict:
    auth = _claims(access_token).get("https://api.openai.com/auth") or {}
    if not auth and tokens and tokens.get("id_token"):
        auth = _claims(tokens["id_token"]).get("https://api.openai.com/auth") or {}
    out = {}
    acct = auth.get("chatgpt_account_id") or (tokens or {}).get("account_id")
    if acct:
        out["ChatGPT-Account-ID"] = str(acct)
    res = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if res:
        out["x-openai-internal-codex-residency"] = str(res).strip()   # residency-bound workspaces 401 without it
    return out


# ---------- the sign-in in ~/.codex/auth.json ------------------------------------------------------------------

def _read() -> dict:
    p = auth_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotSignedIn("Not signed in to ChatGPT on this computer. Sign in: Settings, Account, Runs on.")
    except (OSError, ValueError) as e:
        raise NotSignedIn(f"Could not read the ChatGPT sign-in ({p}): {e}")
    tokens = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise NotSignedIn("Codex on this computer is signed in with an API key, not a ChatGPT account. Sign in "
                          "with ChatGPT: Settings, Account, Runs on.")
    return d


def _write(d: dict) -> None:
    """Atomic write back to auth.json, owner-only, so the Codex CLI sees the new pair."""
    p = auth_path()
    tmp = p.with_name(p.name + f".meshpool-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, p)


@contextlib.contextmanager
def _locked():
    """One refresher at a time across Meshpool processes (a sibling lock file; auth.json itself is replaced)."""
    p = auth_path().with_name("auth.json.meshpool.lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            for _ in range(300):
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


def _stale(d: dict) -> bool:
    exp = _claims(d["tokens"]["access_token"]).get("exp")
    if isinstance(exp, (int, float)):
        return exp - time.time() < REFRESH_SKEW_S
    last = str(d.get("last_refresh") or "")
    try:
        from datetime import datetime
        t = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        return time.time() - t > REFRESH_EVERY_S
    except ValueError:
        return False


def _refresh(d: dict) -> dict:
    tok = d["tokens"]
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                   "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(token_url(), data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "User-Agent": f"Meshpool/{_version()}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            new = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (400, 401, 403):
            raise NotSignedIn("Your ChatGPT sign-in expired or was used up by another program. Sign in again: "
                              f"Settings, Account, Runs on. ({e.code}: {detail})")
        raise RuntimeError(f"chatgpt token refresh HTTP {e.code}: {detail}")
    if not new.get("access_token"):
        raise RuntimeError("chatgpt token refresh returned no access token")
    tok = dict(tok, access_token=new["access_token"])
    for k in ("refresh_token", "id_token"):
        if new.get(k):
            tok[k] = new[k]
    d = dict(d, tokens=tok, last_refresh=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _write(d)
    return d


def credentials(force: bool = False, failed: str = "") -> tuple[str, dict]:
    """(access_token, tokens) ready to use. force: the endpoint said 401 for `failed`, refresh unless another
    program already did."""
    d = _read()
    if not force and not _stale(d):
        return d["tokens"]["access_token"], d["tokens"]
    with _locked():
        d = _read()                                   # someone may have refreshed while we waited
        cur = d["tokens"]["access_token"]
        if (force and failed and cur != failed) or (not force and not _stale(d)):
            return cur, d["tokens"]
        d = _refresh(d)
    return d["tokens"]["access_token"], d["tokens"]


# ---------- the wire --------------------------------------------------------------------------------------------

def _parts(content) -> list | str:
    """Chat-completions content -> Responses input content (text and images)."""
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if c.get("type") == "text":
            out.append({"type": "input_text", "text": c.get("text", "")})
        elif c.get("type") == "image_url":
            url = c.get("image_url")
            out.append({"type": "input_image", "image_url": url.get("url") if isinstance(url, dict) else url})
    return out


def to_request(body: dict, model: str) -> dict:
    """A chat-completions request -> the ChatGPT Codex Responses request. System messages become `instructions`
    (the endpoint rejects system items); output limits and temperature are not accepted there."""
    instructions, items = [], []
    for m in body.get("messages", []):
        role = m.get("role")
        if role == "system":
            instructions.append(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))})
            continue
        if m.get("content"):
            if role == "assistant":
                text = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
                items.append({"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": text}]})
            else:
                items.append({"type": "message", "role": "user" if role != "developer" else "developer",
                              "content": _parts(m["content"])})
        for c in m.get("tool_calls") or []:
            f = c.get("function", {})
            items.append({"type": "function_call", "call_id": c.get("id", ""), "name": f.get("name", ""),
                          "arguments": f.get("arguments") or "{}"})
    out = {"model": model, "instructions": "\n\n".join(instructions) or "You are a helpful assistant.",
           "input": items, "store": False, "stream": True}
    if body.get("tools"):
        out["tools"] = [{"type": "function", "name": t["function"]["name"],
                         "description": t["function"].get("description", ""),
                         "parameters": t["function"].get("parameters", {})} for t in body["tools"]]
        out["tool_choice"] = body.get("tool_choice", "auto")
    if body.get("prompt_cache_key"):
        out["prompt_cache_key"] = body["prompt_cache_key"]
    if (body.get("response_format") or {}).get("type") == "json_object":
        out["text"] = {"format": {"type": "json_object"}}
    return out


def read_stream(lines, on_text=None) -> dict:
    """SSE events -> the final Responses object (from response.completed, or rebuilt from the output items).
    on_text gets each piece of reply text as it arrives."""
    done, items, text, err = None, [], [], None
    for raw in lines:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        t = ev.get("type", "")
        if t == "response.output_text.delta":
            text.append(ev.get("delta", ""))
            if on_text and ev.get("delta"):
                on_text(ev["delta"])
        elif t == "response.output_item.done" and ev.get("item"):
            items.append(ev["item"])
        elif t in ("response.completed", "response.done", "response.incomplete"):
            done = ev.get("response") or {}
        elif t in ("response.failed", "error"):
            err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
    if err and not done:
        raise RuntimeError(f"chatgpt HTTP 500: {json.dumps(err)[:400]}")
    done = done or {}
    if not done.get("output"):
        done["output"] = items or ([{"type": "message", "content": [{"type": "output_text", "text": "".join(text)}]}]
                                   if text else [])
    return done


_MODELS: dict = {"t": 0.0, "rows": []}


def models() -> list[str]:
    """Model ids the user's ChatGPT plan offers (the Codex model list), cached for five minutes."""
    if _MODELS["rows"] and time.time() - _MODELS["t"] < 300:
        return _MODELS["rows"]
    try:
        tok, tokens = credentials()
        req = urllib.request.Request(f"{base_url()}/models?client_version={_version()}", headers={
            "Authorization": f"Bearer {tok}", "originator": "meshpool", "User-Agent": f"Meshpool/{_version()}",
            **account_headers(tok, tokens)})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("models") or data.get("data") or []
        ids = [m.get("slug") or m.get("id") for m in rows if isinstance(m, dict)
               and m.get("visibility", "list") != "hide" and (m.get("slug") or m.get("id"))]
    except Exception:  # noqa: BLE001
        ids = []
    if ids:
        _MODELS.update(t=time.time(), rows=ids)
    return ids


def default_model() -> str:
    from meshpool_harness import config as C
    m = (C.read().get("MH_MODEL") or os.environ.get("MH_MODEL") or "").strip()
    if m.startswith("chatgpt:") and m[8:]:
        return m[8:]                                  # the default model chosen in the picker
    return (C.read().get("MH_CHATGPT_MODEL") or os.environ.get("MH_CHATGPT_MODEL") or DEFAULT_MODEL).strip()


def resolve_model(model: str | None) -> str:
    """A Meshpool or other provider's model id means nothing to ChatGPT: use the ChatGPT default instead."""
    m = (model or "").strip()
    known = _MODELS["rows"]
    if m and (m in known or (not known and m.lower().startswith(("gpt-", "o1", "o3", "o4", "codex")))):
        return m
    return default_model()


def chat(body: dict, task_id: str = "", timeout: int = 600, idle: int = 120, on_text=None) -> dict:
    """One model call on the ChatGPT sign-in. Returns the Responses object; core.wire turns it into chat shape."""
    req_body = to_request(body, resolve_model(body.get("model")))
    data = json.dumps(req_body).encode()
    session = (task_id.split("-esc")[0] if task_id else "") or str(uuid.uuid4())
    failed = ""
    for attempt in range(2):
        tok, tokens = credentials(force=bool(failed), failed=failed)
        req = urllib.request.Request(f"{base_url()}/responses", data=data, method="POST", headers={
            "Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "text/event-stream",
            "originator": "meshpool", "User-Agent": f"Meshpool/{_version()}", "session_id": session,
            **account_headers(tok, tokens)})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                from meshpool_harness.core.wire import _first_then_idle
                return read_stream(_first_then_idle(r, idle), on_text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code == 401 and attempt == 0:
                failed = tok                          # refresh once, unless another program already did
                continue
            if e.code == 401:
                raise NotSignedIn(f"ChatGPT did not accept the sign-in. Sign in again: Settings, Account, Runs on. "
                                  f"({detail})")
            raise RuntimeError(f"chatgpt HTTP {e.code}: {detail}")
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            raise RuntimeError(f"chatgpt HTTP 0: timed out or connection lost: {e}")
    raise RuntimeError("chatgpt HTTP 401")
