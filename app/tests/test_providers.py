"""Providers (what the models run on) are separate from harnesses (which agent runs).

The provider decides each harness's credentials: Codex runs on an API key or a ChatGPT sign-in, Claude Code on a Claude
sign-in or an Anthropic API key, and a Claude subscription never reaches Meshpool's own agents.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402

ENV = ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "MESHPOOL_BASE_URL", "MH_PROVIDER", "MH_HARNESS_CODE", "MH_MODEL",
       "MH_HARNESS_COWORK", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
       "GEMINI_CLI_HOME", "MH_AUTO_SUBSCRIPTIONS")


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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "ghome"))
    monkeypatch.setattr(HR, "_available", lambda d: True)        # npx present
    from meshpool_harness import providers as PV
    monkeypatch.setattr(PV, "list_models", lambda prov, refresh=False: (["m1"], ""))   # no network
    monkeypatch.setattr(HR.sys, "platform", "linux")             # no macOS keychain lookup
    return tmp_path


def sign_in(tmp_path, which):
    d, f = {"chatgpt": ("codex", "auth.json"), "claude": ("claude", ".credentials.json"),
            "google": ("ghome/.gemini", "oauth_creds.json")}[which]
    (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / d / f).write_text("{}")


def rows():
    return {h["id"]: h for h in HR.load_all()}


def test_harness_picker_lists_agents_not_subscriptions(home):
    r = rows()
    assert {"codex", "claude-code", "gemini-cli", "meshpool-code", "meshpool-work", "meshpool-auto"} <= set(r)
    assert r["codex"]["name"] == "Codex" and r["claude-code"]["name"] == "Claude Code"
    for hid in ("meshpool-auto", "meshpool-code", "meshpool-work", "codex", "claude-code", "gemini-cli"):
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
    assert not r["claude-code"]["ready"]                          # no Claude sign-in, no Anthropic key
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


def test_claude_subscription_runs_claude_code_only(home):
    sign_in(home, "claude")
    HR.use_provider("claude")
    r = rows()
    assert HR.ready()
    cc = r["claude-code"]
    assert cc["ready"] and cc["runs_on"] == "Claude subscription"
    assert {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} <= set(cc["env_unset"])
    assert not cc.get("env")                                      # Meshpool passes no Claude token anywhere
    for hid in ("meshpool-code", "meshpool-work", "meshpool-auto"):
        assert r[hid]["ready"] is False and "Claude Code only" in r[hid]["setup"]
    assert HR.default_for("code") == "claude-code" and HR.default_for("cowork") == "claude-code"
    assert C.read()["MH_HARNESS_CODE"] == "claude-code"


def test_claude_subscription_with_a_saved_key_keeps_meshpool_agents_on_the_key(home):
    sign_in(home, "claude")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("claude")
    r = rows()
    assert r["meshpool-code"]["ready"] and r["meshpool-code"]["runs_on"] == "Meshpool"
    assert HR.default_for("code") == "claude-code"                # the provider picked the default agent


def test_claude_code_on_an_anthropic_api_key(home):
    C.write({"MESHPOOL_API_KEY": "mk-123456789", "ANTHROPIC_API_KEY": "sk-ant-abcdefgh"})
    HR.use_provider("meshpool")
    cc = rows()["claude-code"]
    assert cc["ready"] and cc["runs_on"] == "Claude (Anthropic API key)"
    assert cc["env"] == {"ANTHROPIC_API_KEY": "sk-ant-abcdefgh"}


def test_anthropic_key_as_the_custom_provider_reaches_claude_code_not_codex(home):
    C.write({"MESHPOOL_API_KEY": "sk-ant-abcdefgh", "MESHPOOL_BASE_URL": "https://api.anthropic.com/v1"})
    HR.use_provider("custom")
    r = rows()
    assert r["claude-code"]["env"] == {"ANTHROPIC_API_KEY": "sk-ant-abcdefgh"}
    assert not r["codex"]["ready"]                                # Codex cannot run on an Anthropic key


def test_no_credentials_means_not_ready(home):
    HR.use_provider("chatgpt")                                    # chosen but not signed in
    assert not HR.ready()
    HR.use_provider("claude")
    assert not HR.ready()


def test_old_subscription_ids_still_open(home):
    sign_in(home, "claude")
    HR.use_provider("claude")
    assert HR.get("claude-subscription")["id"] == "claude-code"
    assert HR.get("chatgpt-subscription")["id"] == "codex"
    C.write({"MH_HARNESS_CODE": "claude-subscription"})          # a setting saved by an older version
    assert HR.default_for("code") == "claude-code"


def test_unknown_provider_is_rejected(home):
    with pytest.raises(ValueError):
        HR.use_provider("gemini")


def test_auto_stays_on_the_chosen_provider(home, monkeypatch):
    from meshpool_harness import harness_router as R
    sign_in(home, "claude")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    monkeypatch.setattr(R, "table", lambda: {"categories": {"code": {"keywords": ["bug"], "scores": {
        "claude-code": 92, "meshpool-code": 90}}}})
    assert R.route("fix the bug", "code")["harness"] == "meshpool-code"   # the Claude plan is not the provider
    C.write({"MH_AUTO_SUBSCRIPTIONS": "1"})
    assert R.route("fix the bug", "code")["harness"] == "claude-code"
    C.write({"MH_AUTO_SUBSCRIPTIONS": "0"})
    HR.use_provider("claude")                                     # the user chose that plan: Auto may use it
    assert R.route("fix the bug", "code")["harness"] == "claude-code"


def test_model_prefix_picks_the_provider(home):
    from meshpool_harness import providers as PV
    assert PV.split("deepseek-v4.1-flash") == ("key", "deepseek-v4.1-flash")
    assert PV.split("chatgpt:gpt-6.1-sol") == ("chatgpt", "gpt-6.1-sol")
    assert PV.split("gemini:gemini-3-pro") == ("gemini", "gemini-3-pro")
    assert PV.split("meta-llama/llama-4:free") == ("key", "meta-llama/llama-4:free")   # not a provider prefix
    assert PV.join("key", "x") == "x" and PV.join("anthropic", "claude-x") == "anthropic:claude-x"


def test_the_threads_model_decides_codex_credentials(home):
    sign_in(home, "chatgpt")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    assert HR.get("codex")["provider"] == "key"                              # default: the sign-up provider
    cx = HR.get("codex", "chatgpt:gpt-6.1-sol")                               # the thread picked a ChatGPT model
    assert cx["provider"] == "chatgpt" and cx["env"]["CODEX_HOME"] == str(home / "codex") and "prepare" not in cx
    assert HR.get("meshpool-code", "chatgpt:gpt-6.1-sol")["provider"] == "chatgpt"
    assert HR.credentials("pi", "chatgpt:gpt-6.1-sol")["provider"] == "key"  # Pi cannot: it stays on the key


def test_gemini_key_and_google_sign_in(home):
    C.write({"GEMINI_API_KEY": "AIza-123456789"})
    HR.use_provider("meshpool")
    r = rows()
    assert HR.ready()                                                         # a Gemini key alone is enough
    assert r["meshpool-code"]["provider"] == "gemini" and HR.credentials("pi")["provider"] == "gemini"
    g = r["gemini-cli"]
    assert g["provider"] == "gemini" and g["auth"]["methodId"] == "gemini-api-key"
    assert g["env"]["GEMINI_API_KEY"] == "AIza-123456789"
    C.write({"GEMINI_API_KEY": ""})
    sign_in(home, "google")
    g = rows()["gemini-cli"]
    assert g["provider"] == "google" and g["auth"] == {"methodId": "oauth-personal"}
    assert "GEMINI_API_KEY" in g["env_unset"] and not g.get("env")            # its own login; no token from Meshpool
    assert not rows()["meshpool-code"]["ready"]                               # a Google sign-in is Gemini CLI only


def test_sign_in_only_models_never_reach_meshpools_own_loop(home, monkeypatch):
    from meshpool_harness.core.wire import SimpleLedger
    sign_in(home, "claude")
    with pytest.raises(RuntimeError, match="only in"):
        SimpleLedger("t").chat(model="claude:default", messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(RuntimeError, match="only in"):
        SimpleLedger("t").chat(model="google:default", messages=[{"role": "user", "content": "hi"}])


def test_anthropic_and_gemini_keys_route_to_their_endpoints(home, monkeypatch):
    import urllib.request
    from meshpool_harness.core.wire import SimpleLedger
    C.write({"ANTHROPIC_API_KEY": "sk-ant-x", "GEMINI_API_KEY": "AIza-y"})
    seen = []

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {}}'

        def __iter__(self):
            return iter([b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}', b"data: [DONE]"])

    def fake(req, timeout=0):
        seen.append((req.full_url, req.get_header("Authorization"), __import__("json").loads(req.data)["model"]))
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("MH_STREAM", "0")
    SimpleLedger("t").chat(model="anthropic:claude-x", messages=[{"role": "user", "content": "hi"}])
    SimpleLedger("t").chat(model="gemini:gemini-y", messages=[{"role": "user", "content": "hi"}])
    assert seen[0] == ("https://api.anthropic.com/v1/chat/completions", "Bearer sk-ant-x", "claude-x")
    assert seen[1] == ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "Bearer AIza-y",
                       "gemini-y")


def test_model_groups_cover_every_provider(home):
    from meshpool_harness import providers as PV
    sign_in(home, "chatgpt")
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    g = {x["id"]: x for x in PV.groups([{"id": "deepseek-v4.1-flash"}], "")}
    assert list(g) == ["key", "chatgpt", "anthropic", "claude", "gemini", "google"]
    assert g["key"]["ready"] and g["chatgpt"]["ready"] and g["chatgpt"]["models"][0]["id"] == "chatgpt:m1"
    assert not g["anthropic"]["ready"] and "Anthropic API key" in g["anthropic"]["setup"]
    assert g["claude"]["only"] == "claude-code" and g["google"]["only"] == "gemini-cli"


def test_chatgpt_signup_makes_a_chatgpt_model_the_default(home):
    sign_in(home, "chatgpt")
    HR.use_provider("chatgpt")
    assert C.read()["MH_MODEL"].startswith("chatgpt:")
    HR.use_provider("meshpool")
    assert C.read()["MH_MODEL"] == ""
