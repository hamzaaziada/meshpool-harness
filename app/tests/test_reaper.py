"""Idle threads release their helper processes (QA major M4: 89 idle helpers after 30 threads)."""
import time

from meshpool_harness.ui import agent_api as A


class Fake:
    def __init__(self, state="idle", age=0, pending=None, queue=None):
        self.state, self.pendings, self.queue = state, ({1: pending} if pending else {}), queue or []
        self.touched = time.time() - age
        self.closed = False

    def close(self):
        self.closed = True


def test_reap_closes_only_idle_unused_threads(monkeypatch):
    s = {"old": Fake(age=1000), "fresh": Fake(age=10), "busy": Fake("working", age=1000),
         "asking": Fake("waiting", age=1000), "pending": Fake(age=1000, pending={"id": 1}),
         "queued": Fake(age=1000, queue=[("hi", None)])}
    monkeypatch.setattr(A, "LIVE", dict(s))
    closed = A._reap_once(900)
    assert closed == ["old"] and s["old"].closed and "old" not in A.LIVE
    assert all(not s[k].closed for k in ("fresh", "busy", "asking", "pending", "queued"))


def test_reaper_off_with_zero(monkeypatch):
    monkeypatch.setenv("MH_IDLE_CLOSE_S", "0")
    monkeypatch.setattr(A, "_REAPER", [])
    A._start_reaper()
    assert A._REAPER == []
