"""Keep test runs from writing into the package folder (the failure catalog defaults to it)."""
import os

os.environ.setdefault("MH_FAILURES_LOG", "off")
os.environ.setdefault("MH_RECOVER_MIN", "0")        # an outage in a test fails at once instead of waiting 30 min

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _no_slot_files_in_real_temp():
    """A whole run leaves nothing new in the slot folder real chats share for their MH_MAX_INFLIGHT cap."""
    from meshpool_harness.core import gateway as GW
    real = GW.default_slots_dir()

    def names():
        try:
            return set(os.listdir(real))
        except OSError:
            return set()
    before = names()
    yield
    new = sorted(names() - before)
    assert not new, (f"new slot files in the real {real}: {new[:10]}. A test ran without MH_SLOTS_DIR, or a Meshpool "
                     "window open during the run took a slot; close it and run again to tell which.")


@pytest.fixture(autouse=True)
def _offline_gateway(monkeypatch, tmp_path):
    """No test probes the real endpoint, and no endpoint state leaks between tests."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.core import gateway as GW
    GW.reset()
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "gateway-slots"))   # never the machine's real temp folder
    monkeypatch.setenv("MH_REVIEW_CALIBRATION", "0")    # reviewer tests grade raw; the calibration has its own test
    monkeypatch.setattr(Agent, "_probe", lambda self, base, key, model: "HTTP 0: offline in tests")
    yield
    GW.reset()
