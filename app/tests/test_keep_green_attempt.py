"""v2 #3: an attempt whose last check after the last edit passed is kept instead of escalated."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.code.engine import checks_green  # noqa: E402


def test_green_after_last_edit():
    ws = SimpleNamespace(edits=2, commands=[{"kind": "check", "after_edit": 1, "code": 1},
                                            {"kind": "check", "after_edit": 2, "code": 0}])
    assert checks_green(ws)


def test_red_or_stale_check_escalates():
    assert not checks_green(SimpleNamespace(edits=2, commands=[{"kind": "check", "after_edit": 2, "code": 1}]))
    assert not checks_green(SimpleNamespace(edits=2, commands=[{"kind": "check", "after_edit": 1, "code": 0}]))
    assert not checks_green(SimpleNamespace(edits=0, commands=[]))
