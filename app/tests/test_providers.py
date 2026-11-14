"""Providers (what the models run on) are separate from harnesses (which agent runs).

The provider decides each harness's credentials: every built-in agent runs on an API key (the Meshpool key or another
OpenAI-compatible one) or the user's ChatGPT sign-in.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402

ENV = ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "MESHPOOL_BASE_URL", "MH_PROVIDER", "MH_HARNESS_CODE", "MH_MODEL",
       "MH_HARNESS_COWORK", "CODEX_HOME", "MH_AUTO_SUBSCRIPTIONS")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty Meshpool data folder, a fake user home, and no keys in the environment."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "mh" / "meshpool.config"))
    (tmp_path / "mh").mkdir()
    for k in ENV:                         # recorded, so values the code under test sets are undone afterwards
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(HR, "_available", lambda d: True)        # npx present
    from meshpool_harness import providers as PV
    monkeypatch.setattr(PV, "list_models", lambda prov, refresh=False: (["m1"], ""))   # no network
    return tmp_path


def sign_in(tmp_path, which="chatgpt"):
    assert which == "chatgpt"                                     # the only sign-in Meshpool has
    (tmp_path / "codex").mkdir(parents=True, exist_ok=True)
    (tmp_path / "codex" / "auth.json").write_text("{}")


def rows():
    return {h["id"]: h for h in HR.load_all()}


def test_harness_picker_lists_agents_not_subscriptions(home):
    r = rows()
    assert {"codex", "meshpool-code", "meshpool-work", "meshpool-auto"} <= set(r)
    assert not {"claude-code", "gemini-cli"} & set(r)            # Meshpool has no Claude or Gemini agents
    assert r["codex"]["name"] == "Codex"
    for hid in ("meshpool-auto", "meshpool-code", "meshpool-work", "codex"):
        assert r[hid]["tagline"] and len(r[hid]["tagline"]) < 70        # the picker's one-liner
    assert not any("subscription" in h["id"] or "subscription" in h["name"] for h in r.values())
    assert not any(h.get("subscription") for h in r.values())


def test_meshpool_key_runs_meshpool_agents_and_codex(home):
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    r = rows()
    assert HR.ready()
    assert r["meshpool-code"]["ready"] and r["meshpool-code"]["runs_on"] == "Meshpool"
    cx = r["codex"]
    assert cx["ready"] and cx["prepare"] == "codex" and cx["models"] == "meshpool"
    assert cx["env"]["CODEX_HOME"] == str(HR.codex_home())       # Meshpool's own Codex config, not ~/.codex
    assert HR.default_for("code") == "meshpool-code" and HR.default_for("cowork") == "meshpool-work"


def test_chatgpt_signin_runs_codex_from_the_users_codex_home(home):
    sign_in(home, "chatgpt")
    HR.use_provider("chatgpt")
    cx = rows()["codex"]
    assert HR.ready()
    assert cx["ready"] and cx["runs_on"] == "ChatGPT subscription" and cx["models"] == "harness"
    assert "prepare" not in cx                                    # no Meshpool endpoint written over its config
    assert cx["env"]["CODEX_HOME"] == str(home / "codex")
    assert "OPENAI_API_KEY" in cx["env_unset"]                    # never bills an API key instead of the plan


def test_no_credentials_means_not_ready(home):
    HR.use_provider("chatgpt")                                    # chosen but not signed in
    assert not HR.ready()
    HR.use_provider("meshpool")                                   # no key saved
    assert not HR.ready()


def test_old_agent_ids_still_open(home):
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    assert HR.get("chatgpt-subscription")["id"] == "codex"
    for old in ("claude-subscription", "claude-code", "gemini-cli"):   # agents Meshpool no longer has
        assert HR.get(old)["id"] == "meshpool-code"
        C.write({"MH_HARNESS_CODE": old})                        # a setting saved by an older version
        assert HR.default_for("code") == "meshpool-code"


def test_unknown_provider_is_rejected(home):
    for p in ("claude", "anthropic", "gemini", "google"):
        with pytest.raises(ValueError):
            HR.use_provider(p)


def test_auto_stays_on_the_chosen_provider(home, monkeypatch):
    from meshpool_harness import harness_router as R
    sign_in(home)                                                 # a ChatGPT sign-in, but the key is the provider
    HR.use_provider("meshpool")
    monkeypatch.setattr(R, "table", lambda: {"categories": {"code": {"keywords": ["bug"], "scores": {
        "codex": 92, "meshpool-code": 90}}}})
    r = R.route("fix the bug", "code")
    assert r["harness"] == "meshpool-code" and not r["ranking"]  # the ChatGPT plan is not the provider
    C.write({"MH_AUTO_SUBSCRIPTIONS": "1"})
    assert R.route("fix the bug", "code")["harness"] == "codex"
    C.write({"MH_AUTO_SUBSCRIPTIONS": "0"})
    HR.use_provider("chatgpt")                                    # the user chose that plan: Auto may use it
    assert R.route("fix the bug", "code")["harness"] == "codex"


def test_model_prefix_picks_the_provider(home):
    from meshpool_harness import providers as PV
    assert PV.split("deepseek-v4.1-flash") == ("key", "deepseek-v4.1-flash")
    assert PV.split("chatgpt:gpt-6.1-sol") == ("chatgpt", "gpt-6.1-sol")
    assert PV.split("meta-llama/llama-4:free") == ("key", "meta-llama/llama-4:free")   # not a provider prefix
    assert PV.join("key", "x") == "x" and PV.join("chatgpt", "gpt-x") == "chatgpt:gpt-x"
    assert PV.PREFIXED == ("chatgpt",) and set(PV.SUPPORTS) == {
        "meshpool-code", "meshpool-work", "meshpool-auto", "pi", "hermes", "codex", "opencode"}
    assert all(v == ["key", "chatgpt"] for v in PV.SUPPORTS.values())


def test_the_threads_model_decides_codex_credentials(home):
    sign_in(home, "chatgpt")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    assert HR.get("codex")["provider"] == "key"                              # default: the sign-up provider
    cx = HR.get("codex", "chatgpt:gpt-6.1-sol")                               # the thread picked a ChatGPT model
    assert cx["provider"] == "chatgpt" and cx["env"]["CODEX_HOME"] == str(home / "codex") and "prepare" not in cx
    assert HR.get("meshpool-code", "chatgpt:gpt-6.1-sol")["provider"] == "chatgpt"
    assert HR.credentials("pi", "chatgpt:gpt-6.1-sol")["provider"] == "chatgpt"   # through the local relay


def test_model_groups_cover_every_provider(home):
    from meshpool_harness import providers as PV
    sign_in(home, "chatgpt")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    g = {x["id"]: x for x in PV.groups([{"id": "deepseek-v4.1-flash"}], "")}
    assert list(g) == ["key", "chatgpt"]
    assert g["key"]["ready"] and g["chatgpt"]["ready"] and g["chatgpt"]["models"][0]["id"] == "chatgpt:m1"


def test_settings_have_no_claude_or_gemini_keys():
    keys = {f[0] for f in C.FIELDS}
    assert not {"ANTHROPIC_API_KEY", "GEMINI_API_KEY"} & (keys | C.SECRET)
    assert set(HR.PROVIDER_NAMES) == {"meshpool", "custom", "chatgpt"} and HR.LOGINS == {"chatgpt": "codex"}
    with pytest.raises(ValueError):
        HR.login("claude")


def test_chatgpt_signup_makes_a_chatgpt_model_the_default(home):
    sign_in(home, "chatgpt")
    HR.use_provider("chatgpt")
    assert C.read()["MH_MODEL"].startswith("chatgpt:")
    HR.use_provider("meshpool")
    assert C.read()["MH_MODEL"] == ""


def test_hermes_keeps_its_own_environment_when_the_thread_has_a_model(home, monkeypatch):
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    env = {"PYTHONPATH": "/vendor/hermes", "HERMES_HOME": "/mh/hermes"}
    monkeypatch.setattr(HR, "_bundled_hermes", lambda: {
        "id": "hermes", "name": "Hermes", "mode": "cowork", "type": "acp", "prepare": "hermes", "models": "meshpool",
        "command": sys.executable, "args": ["-m", "acp_adapter"], "env": dict(env)})
    for model in (None, "deepseek-v4.1-flash"):
        h = HR.get("hermes", model)
        assert h["env"]["PYTHONPATH"] == "/vendor/hermes" and h["env"]["HERMES_HOME"] == "/mh/hermes"
        assert h["prepare"] == "hermes"


def test_an_openai_key_alone_never_goes_to_the_meshpool_endpoint(home, monkeypatch):
    from meshpool_harness import providers as PV
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-123")
    assert PV.key() == "" and PV.key_base() == "https://llm.meshpool.ai/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1/")
    assert PV.key() == "sk-openai-123" and PV.key_base() == "https://api.openai.com/v1"   # used as a pair
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    assert PV.key() == "mk-123456789" and PV.key_base() == "https://llm.meshpool.ai/v1"


def test_codex_gets_the_key_in_its_own_environment_only(home, monkeypatch):
    import os
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-123")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    HR.use_provider("custom")
    HR.prepare_codex()
    assert "MESHPOOL_API_KEY" not in os.environ                  # no process-wide change
    assert 'base_url = "https://api.openai.com/v1"' in (HR.codex_home() / "config.toml").read_text()
    cx = HR.get("codex", "gpt-x")
    assert cx["env"]["MESHPOOL_API_KEY"] == "sk-openai-123" and cx["prepare"] == "codex"


def test_hermes_config_and_its_backup_are_private_from_the_start(home):
    import os
    import stat
    pytest.importorskip("yaml")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.hermes_home().mkdir(parents=True)
    old = HR.hermes_home() / "config.yaml"
    old.write_text("model: {}\n")
    os.chmod(old, 0o644)
    HR.prepare_hermes("deepseek-v4.1-flash")
    backups = list((HR.hermes_home() / "backups" / "config").glob("config.yaml.good.*"))
    assert backups and "mk-123456789" in old.read_text()
    for f in [old, *backups]:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f


def test_a_chat_from_an_agent_meshpool_no_longer_has_continues_in_meshpool_code(home, monkeypatch):
    import json
    from meshpool_harness.ui import agent_api as A
    sessions = home / "runs" / "sessions"
    monkeypatch.setitem(A.Agent.__init__.__globals__, "SESSIONS", sessions)   # core, even if a test reloaded it
    monkeypatch.setattr(A, "SESSIONS", sessions)
    monkeypatch.setattr(A.Session, "_start_mcp", lambda self: None)
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    proj = home / "proj"
    proj.mkdir()
    (sessions / "old-1").mkdir(parents=True)
    (sessions / "old-1" / "session.json").write_text(json.dumps({     # saved by an older version (an ACP agent)
        "id": "old-1", "workdir": str(proj), "title": "Fix the parser", "harness": "claude-code", "kind": "code",
        "acp_session": "x", "events": [{"type": "user", "text": "fix the parser"},
                                       {"type": "tool_start", "id": "t1", "name": "edit"},
                                       {"type": "assistant", "text": "Fixed it."}]}))
    (sessions / "old-1" / "meta.json").write_text(json.dumps({"model": "claude:default"}))
    s = A.Session(resume_id="old-1")
    assert s.harness["id"] == "meshpool-code" and isinstance(s.agent, A.Agent)
    assert [m["role"] for m in s.agent.messages[1:]] == ["user", "assistant"]   # the conversation goes on
    assert s.agent.title == "Fix the parser" and getattr(s.agent, "model", None) is None   # not the old model
    texts = [e.get("text") for e in s.events]
    assert "fix the parser" in texts and any("continues in Meshpool Code" in (t or "") for t in texts)
    saved = json.loads((sessions / "old-1" / "session.json").read_text())
    assert saved["harness"] == "meshpool-code" and len(saved["messages"]) == 2   # opens normally from now on


def test_a_model_saved_for_a_retired_provider_falls_back_to_the_default(monkeypatch):
    from meshpool_harness import providers as PV
    monkeypatch.delenv("MH_PROVIDER", raising=False)
    assert PV.split("claude:default") == ("key", PV.DEFAULT_MODEL)
    assert PV.split("anthropic:claude-sonnet")[1] == PV.DEFAULT_MODEL
    assert PV.split("deepseek-v4.1-flash") == ("key", "deepseek-v4.1-flash")
