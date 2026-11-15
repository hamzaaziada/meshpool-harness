"""MH_MODEL_ALLOW caps spend: the client refuses any model not on the list, before any request is sent."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_client_refuses_models_outside_the_allow_list(monkeypatch):
    from meshpool_harness.core.client import LiveClient
    monkeypatch.setenv("MH_MODEL_ALLOW", "deepseek-v4.1-flash, glm-5.3")
    with pytest.raises(RuntimeError, match="not allowed"):
        LiveClient("t").chat(model="claude-sonnet-5.5", messages=[{"role": "user", "content": "hi"}])
