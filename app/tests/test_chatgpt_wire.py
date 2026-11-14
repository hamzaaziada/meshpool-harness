"""The ChatGPT sign-in as a model provider (core/chatgpt.py), against a local mock of the ChatGPT Codex endpoint and
the OpenAI token endpoint. No network, no real sign-in."""
import base64
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.core import chatgpt as CG  # noqa: E402



def jwt(exp: float, acct: str = "acct-42") -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp), 'https://api.openai.com/auth': {'chatgpt_account_id': acct}})}.sig"


class Mock:
    def __init__(self):
        self.seen, self.token_calls = [], []
        self.valid = {jwt(time.time() + 3600)}            # access tokens the endpoint accepts
        self.reject_next = False
        self.limit = False                                   # answer like a plan whose usage limit is used up
        self.refresh_status = 200
        self.issued = 0

    def new_token(self):
        self.issued += 1
        t = jwt(time.time() + 3600 + self.issued)
        self.valid.add(t)
        return t


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.fixture
def mock(tmp_path, monkeypatch):
    m = Mock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            m.seen.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}, b""))
            if self.path.startswith("/codex/models"):
                return self._reply(200, json.dumps({"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-6-luna"},
                                                               {"slug": "hidden", "visibility": "hide"}]}).encode())
            self._reply(404, b"{}")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            m.seen.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, raw))
            if self.path == "/oauth/token":
                form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                m.token_calls.append(form)
                if m.refresh_status != 200:
                    return self._reply(m.refresh_status, b'{"error": "refresh_token_reused"}')
                assert form["grant_type"] == "refresh_token" and form["client_id"] == CG.CLIENT_ID
                n = len(m.token_calls)
                return self._reply(200, json.dumps({"access_token": m.new_token(), "refresh_token": f"rt-{n + 1}",
                                                    "id_token": jwt(time.time() + 3600)}).encode())
            if self.path == "/codex/responses":
                tok = self.headers.get("Authorization", "")[7:]
                if m.limit:                                      # the body ChatGPT sends (seen live, Go plan)
                    return self._reply(429, json.dumps({"error": {
                        "type": "usage_limit_reached", "message": "The usage limit has been reached", "plan_type": "go",
                        "resets_at": int(time.time()) + 973722, "limit_window_minutes": 43200,
                        "resets_in_seconds": 973722}}).encode())
                if m.reject_next or tok not in m.valid:
                    m.reject_next = False
                    return self._reply(401, b'{"detail": "token expired"}')
                text = "" if "USE A TOOL" in raw.decode() else "hel"
                evs = [{"type": "response.created", "response": {"id": "r1"}}]
                out = []
                if text:
                    evs += [{"type": "response.output_text.delta", "delta": "hel"},
                            {"type": "response.output_text.delta", "delta": "lo"}]
                    out = [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]
                else:
                    out = [{"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command": "ls"}'}]
                    evs += [{"type": "response.output_item.done", "item": out[0]}]
                evs.append({"type": "response.completed", "response": {
                    "id": "r1", "output": out,
                    "usage": {"input_tokens": 50, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 40}}}})
                return self._reply(200, sse(*evs), "text/event-stream")
            self._reply(404, b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("MH_CHATGPT_BASE_URL", url + "/codex")
    monkeypatch.setenv("MH_CHATGPT_TOKEN_URL", url + "/oauth/token")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    CG._MODELS.update(t=0.0, rows=[])
    CG._LIMIT.clear()
    yield m
    srv.shutdown()


def write_auth(tmp_path, access, refresh="rt-1", last="2026-11-13T00:00:00Z"):
    d = tmp_path / "codex"
    d.mkdir(exist_ok=True)
    (d / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": None, "last_refresh": last, "tokens": {
        "id_token": jwt(time.time() + 3600), "access_token": access, "refresh_token": refresh,
        "account_id": "acct-42"}}))
    return d / "auth.json"


def body(tools=False):
    b = {"model": "deepseek-v4.1-flash", "max_tokens": 900, "temperature": 0.3,
         "messages": [{"role": "system", "content": "You are Meshpool Code."}, {"role": "user", "content": "hi"}]}
    if tools:
        b["tools"] = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}]
        b["messages"][-1]["content"] = "USE A TOOL"
    return b


def test_request_shape_headers_and_reply(mock, tmp_path):
    tok = next(iter(mock.valid))
    write_auth(tmp_path, tok)
    got = []
    r = CG.chat(body(), task_id="t-1-esc2", on_text=got.append)
    assert got == ["hel", "lo"]
    from meshpool_harness.core.wire import from_responses
    c = from_responses(r)
    assert c["choices"][0]["message"]["content"] == "hello"
    assert c["usage"]["prompt_tokens"] == 50 and c["usage"]["prompt_tokens_details"]["cached_tokens"] == 40
    _, path, h, raw = [s for s in mock.seen if s[1] == "/codex/responses"][0]
    sent = json.loads(raw)
    assert h["authorization"] == f"Bearer {tok}" and h["chatgpt-account-id"] == "acct-42"
    assert h["originator"] == "meshpool" and h["user-agent"].startswith("Meshpool/") and h["session_id"] == "t-1"
    assert sent["store"] is False and sent["stream"] is True
    assert sent["instructions"] == "You are Meshpool Code."
    assert not any(i.get("role") == "system" for i in sent["input"])
    assert "max_output_tokens" not in sent and "temperature" not in sent
    assert sent["model"] == CG.DEFAULT_MODEL                   # a Meshpool model id is replaced by the ChatGPT default
    assert mock.token_calls == []                              # a fresh token is not refreshed


def test_tool_calls_come_back_in_chat_shape(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    from meshpool_harness.core.wire import from_responses
    m = from_responses(CG.chat(body(tools=True)))["choices"][0]["message"]
    assert m["tool_calls"][0]["function"] == {"name": "bash", "arguments": '{"command": "ls"}'}
    assert m["tool_calls"][0]["id"] == "c1"


def test_expired_token_is_refreshed_first_and_written_back(mock, tmp_path):
    p = write_auth(tmp_path, jwt(time.time() - 10))          # expired
    CG.chat(body())
    assert len(mock.token_calls) == 1 and mock.token_calls[0]["refresh_token"] == "rt-1"
    saved = json.loads(p.read_text())
    assert saved["tokens"]["refresh_token"] == "rt-2"          # the rotated pair, for the Codex CLI too
    assert saved["tokens"]["access_token"] in mock.valid
    assert saved["last_refresh"].startswith(time.strftime("%Y-"))
    assert (p.stat().st_mode & 0o777) == 0o600


def test_401_refreshes_once_then_succeeds(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    mock.reject_next = True
    from meshpool_harness.core.wire import from_responses
    assert from_responses(CG.chat(body()))["choices"][0]["message"]["content"] == "hello"
    assert len(mock.token_calls) == 1


def test_a_pair_another_program_rotated_is_adopted_not_refreshed_again(mock, tmp_path):
    fresh = next(iter(mock.valid))
    write_auth(tmp_path, fresh, refresh="rt-9")              # e.g. the Codex CLI refreshed while we waited
    tok, _ = CG.credentials(force=True, failed="the-token-that-got-401")
    assert tok == fresh and mock.token_calls == []


def test_used_up_refresh_token_asks_to_sign_in_again(mock, tmp_path):
    write_auth(tmp_path, jwt(time.time() - 10))
    mock.refresh_status = 400
    with pytest.raises(CG.NotSignedIn, match="Sign in again"):
        CG.chat(body())


def test_api_key_login_is_not_a_chatgpt_sign_in(mock, tmp_path):
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex" / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x", "tokens": None}))
    with pytest.raises(CG.NotSignedIn, match="API key"):
        CG.chat(body())


def test_model_list_and_choice(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    assert CG.models() == ["gpt-6.1-sol", "gpt-6-luna"]
    assert CG.resolve_model("gpt-6-luna") == "gpt-6-luna"
    assert CG.resolve_model("deepseek-v4.1-flash") == CG.default_model()


def test_meshpool_wire_uses_the_sign_in_when_chatgpt_is_the_provider(mock, tmp_path, monkeypatch):
    write_auth(tmp_path, next(iter(mock.valid)))
    monkeypatch.setenv("MH_PROVIDER", "chatgpt")
    monkeypatch.setenv("MESHPOOL_API_KEY", "")
    from meshpool_harness.core.wire import SimpleLedger
    r = SimpleLedger("t").chat(model="x", messages=[{"role": "user", "content": "hi"}], task_id="job-7")
    assert r["choices"][0]["message"]["content"] == "hello"


def test_meshpool_code_agent_streams_on_the_sign_in(mock, tmp_path, monkeypatch):
    write_auth(tmp_path, next(iter(mock.valid)))
    monkeypatch.setenv("MH_PROVIDER", "chatgpt")
    monkeypatch.setenv("MESHPOOL_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    from meshpool_harness.agent import core
    from meshpool_harness.agent.core import Agent
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")   # fixed at import: keep the session in tmp
    ev = []
    a = Agent(str(tmp_path), emit=ev.append, mode="auto")
    a.send("say hello")
    assert "".join(e["text"] for e in ev if e.get("type") == "text_delta") == "hello"


# ---------- the local relay (core/chatgpt_relay.py): Pi and Hermes reach the sign-in through it -------------------

def _relay_call(path, body=None, token=None, stream=False):
    import urllib.error
    import urllib.request
    from meshpool_harness.core import chatgpt_relay as R
    url, tok = R.ensure()
    req = urllib.request.Request(url.removesuffix("/v1") + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token if token is not None else tok}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (raw.decode() if stream else json.loads(raw))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_relay_answers_chat_completions_without_exposing_the_sign_in(mock, tmp_path):
    tok = next(iter(mock.valid))
    write_auth(tmp_path, tok)
    code, r = _relay_call("/v1/chat/completions", body())
    assert code == 200 and r["object"] == "chat.completion"
    assert r["choices"][0]["message"]["content"] == "hello" and r["usage"]["prompt_tokens"] == 50
    from meshpool_harness.core import chatgpt_relay as R
    assert tok not in json.dumps(r) and R.ensure()[1] != tok              # agents get the relay token, not ChatGPT's


def test_relay_streams_text_then_tool_calls_and_usage(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    b = dict(body(), stream=True, stream_options={"include_usage": True})
    code, raw = _relay_call("/v1/chat/completions", b, stream=True)
    evs = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and "[DONE]" not in line]
    assert code == 200 and raw.rstrip().endswith("data: [DONE]")
    text = "".join((c["choices"][0]["delta"].get("content") or "") for c in evs if c["choices"])
    assert text == "hello"
    assert [c["choices"][0]["finish_reason"] for c in evs if c["choices"]][-1] == "stop"
    assert evs[-1]["usage"]["completion_tokens"] == 5
    tb = dict(body(tools=True), stream=True)
    _, raw = _relay_call("/v1/chat/completions", tb, stream=True)
    evs = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and "[DONE]" not in line]
    calls = [tc for c in evs if c["choices"] for tc in c["choices"][0]["delta"].get("tool_calls") or []]
    assert calls == [{"index": 0, "id": "c1", "type": "function",
                      "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]
    assert [c["choices"][0]["finish_reason"] for c in evs if c["choices"]][-1] == "tool_calls"


def test_relay_refuses_without_its_token_and_lists_models(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    code, r = _relay_call("/v1/chat/completions", body(), token="wrong")
    assert code == 401 and r["error"]["type"] == "authentication_error"
    assert not [s for s in mock.seen if s[1] == "/codex/responses"]      # nothing went to ChatGPT
    code, r = _relay_call("/v1/models")
    assert code == 200 and "gpt-6-luna" in [m["id"] for m in r["data"]]


def test_relay_says_sign_in_again_when_the_sign_in_is_gone(mock, tmp_path):
    (tmp_path / "codex").mkdir()                                          # no auth.json
    code, r = _relay_call("/v1/chat/completions", body())
    assert code == 401 and "Sign in" in r["error"]["message"]


def test_pi_and_hermes_point_at_the_relay(mock, tmp_path, monkeypatch):
    write_auth(tmp_path, next(iter(mock.valid)))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    (tmp_path / "mh").mkdir()
    monkeypatch.setenv("MESHPOOL_API_KEY", "")
    from meshpool_harness import providers as PV
    from meshpool_harness.core import chatgpt_relay as R
    from meshpool_harness.harnesses import pi_rpc
    assert "chatgpt" in PV.SUPPORTS["pi"] and "chatgpt" in PV.SUPPORTS["hermes"]
    url, tok = R.ensure()
    assert PV.endpoint("chatgpt") == (url, tok)
    home = pi_rpc.prepare(tmp_path)
    prov = json.loads((home / "models.json").read_text())["providers"]["chatgpt"]
    assert prov["baseUrl"] == url and prov["apiKey"] == "$MESHPOOL_CHATGPT_RELAY_KEY"
    assert "gpt-6.1-sol" in [m["id"] for m in prov["models"]]
    assert pi_rpc._pi_model("chatgpt:gpt-6-luna") == ("chatgpt", "gpt-6-luna")
    assert next(iter(mock.valid)) not in (home / "models.json").read_text()   # no ChatGPT token in Pi's files


def test_hermes_config_points_at_the_relay(mock, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    import yaml
    write_auth(tmp_path, next(iter(mock.valid)))
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    (tmp_path / "mh").mkdir()
    from meshpool_harness import harnesses as HR
    from meshpool_harness.core import chatgpt_relay as R
    HR.prepare_hermes("chatgpt:gpt-6-luna")
    cfg = yaml.safe_load((HR.hermes_home() / "config.yaml").read_text())
    url, tok = R.ensure()
    assert cfg["model"]["base_url"] == url and cfg["model"]["api_key"] == tok
    assert cfg["model"]["default"] == "gpt-6-luna"
    assert cfg["auth"]["adopt_external_logins"] is False



# ---------- found live: the model list needs a Codex-format version; a used-up plan must not hang a chat -----------

def test_model_list_sends_a_codex_compatible_version_and_the_default_follows_the_plan(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    assert CG.models() == ["gpt-6.1-sol", "gpt-6-luna"]
    path = [s[1] for s in mock.seen if s[1].startswith("/codex/models")][0]
    assert f"client_version={CG.CODEX_COMPAT_VERSION}" in path      # "0" or Meshpool's 0.1.0 get an empty list
    assert CG.default_model() == "gpt-6.1-sol"                        # the plan's first model, not a fixed name


def test_a_model_the_plan_does_not_offer_falls_back_to_one_it_does(mock, tmp_path, monkeypatch):
    write_auth(tmp_path, next(iter(mock.valid)))
    CG.models()
    monkeypatch.setenv("MH_MODEL", "chatgpt:gpt-retired")
    assert CG.default_model() == "gpt-6.1-sol"
    assert CG.resolve_model("gpt-retired") == "gpt-6.1-sol"


def test_used_up_plan_is_reported_once_not_retried(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    mock.limit = True
    with pytest.raises(CG.UsageLimit) as e:
        CG.chat(body())
    msg = str(e.value)
    assert "Usage limit reached on ChatGPT Go" in msg and "resets in 11 days" in msg and "another provider" in msg
    assert "HTTP 429" not in msg                                     # so no client loop treats it as a short rate limit
    assert len([s for s in mock.seen if s[1] == "/codex/responses"]) == 1
    assert CG.limit_note().startswith("Usage limit reached")          # the model menu shows it under the ChatGPT tab


def test_relay_answers_a_used_up_plan_with_no_quota(mock, tmp_path):
    write_auth(tmp_path, next(iter(mock.valid)))
    mock.limit = True
    code, r = _relay_call("/v1/chat/completions", body())
    assert code == 429 and r["error"]["code"] == "insufficient_quota" and "resets" in r["error"]["message"]


def test_meshpool_code_stops_at_once_on_a_used_up_plan(mock, tmp_path, monkeypatch):
    write_auth(tmp_path, next(iter(mock.valid)))
    mock.limit = True
    monkeypatch.setenv("MH_PROVIDER", "chatgpt")
    monkeypatch.setenv("MESHPOOL_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    from meshpool_harness.agent.core import Agent
    ev = []
    a = Agent(str(tmp_path), emit=ev.append, mode="auto")
    t0 = time.time()
    try:
        a.send("say hello")
    except CG.UsageLimit:
        pass
    assert time.time() - t0 < 5                                       # no 65 s rate-limit waits
    shown = " ".join(str(e.get("text") or e.get("message") or "") for e in ev if e.get("type") in ("error", "status"))
    assert "Usage limit reached" in shown and "Rate limited" not in shown, shown
    assert len([s for s in mock.seen if s[1] == "/codex/responses"]) == 1
