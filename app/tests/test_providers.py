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

ENV = ("MESHPOOL_API_KEY", "OPENAI_API_KEY", "MESHPOOL_BASE_URL", "MH_PROVIDER", "MH_HARNESS_CODE",
       "MH_HARNESS_COWORK", "ANTHROPIC_API_KEY", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "MH_AUTO_SUBSCRIPTIONS")


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
    monkeypatch.setattr(HR, "_available", lambda d: True)        # npx present
    monkeypatch.setattr(HR.sys, "platform", "linux")             # no macOS keychain lookup
    return tmp_path


def sign_in(tmp_path, which):
    d = tmp_path / ("codex" if which == "chatgpt" else "claude")
    d.mkdir(exist_ok=True)
    (d / ("auth.json" if which == "chatgpt" else ".credentials.json")).write_text("{}")


def rows():
    return {h["id"]: h for h in HR.load_all()}


def test_harness_picker_lists_agents_not_subscriptions(home):
    r = rows()
    assert "codex" in r and "claude-code" in r
    assert r["codex"]["name"] == "Codex" and r["claude-code"]["name"] == "Claude Code"
    assert not any("subscription" in h["id"] or "subscription" in h["name"] for h in r.values())
    assert not any(h.get("subscription") for h in r.values())


def test_meshpool_key_runs_meshpool_agents_and_codex(home):
    C.write({"MESHPOOL_API_KEY": "mk-123456789"})
    HR.use_provider("meshpool")
    r = rows()
    assert HR.ready()
    assert r["meshpool-code"]["ready"] and r["meshpool-code"]["runs_on"] == "your Meshpool key"
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
    assert cx["ready"] and cx["runs_on"] == "your ChatGPT subscription" and cx["models"] == "harness"
    assert "prepare" not in cx                                    # no Meshpool endpoint written over its config
    assert cx["env"]["CODEX_HOME"] == str(home / "codex")
    assert "OPENAI_API_KEY" in cx["env_unset"]                    # never bills an API key instead of the plan


def test_claude_subscription_runs_claude_code_only(home):
    sign_in(home, "claude")
    HR.use_provider("claude")
    r = rows()
    assert HR.ready()
    cc = r["claude-code"]
    assert cc["ready"] and cc["runs_on"] == "your Claude subscription"
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
    assert r["meshpool-code"]["ready"] and r["meshpool-code"]["runs_on"] == "your Meshpool key"
    assert HR.default_for("code") == "claude-code"                # the provider picked the default agent


def test_claude_code_on_an_anthropic_api_key(home):
    C.write({"MESHPOOL_API_KEY": "mk-123456789", "ANTHROPIC_API_KEY": "sk-ant-abcdefgh"})
    HR.use_provider("meshpool")
    cc = rows()["claude-code"]
    assert cc["ready"] and cc["runs_on"] == "your Anthropic API key"
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
