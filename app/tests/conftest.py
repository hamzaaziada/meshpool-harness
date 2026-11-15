"""Keep test runs from writing into the package folder (the failure catalog defaults to it)."""
import os

os.environ.setdefault("MH_FAILURES_LOG", "off")
os.environ.setdefault("MH_RECOVER_MIN", "0")        # an outage in a test fails at once instead of waiting 30 min

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_gateway(monkeypatch, tmp_path):
    """No test probes the real endpoint, and no endpoint state leaks between tests."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.core import gateway as GW
    GW.reset()
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "gateway-slots"))   # never the machine's real temp folder
    monkeypatch.setattr(Agent, "_probe", lambda self, base, key, model: "HTTP 0: offline in tests")
    yield
    GW.reset()
