"""Client-side handling of a gateway that stalls (Harness v4, gateway health).

One Health per endpoint and key, shared by every chat and subagent in the process. It answers three questions:

- Is the endpoint up? One tiny probe tells a stuck gateway (the probe hangs or fails too) from a request that is only
  slow (the probe answers). A probe result is reused for a few seconds, so chats that fail together probe once.
- How many model calls may be in flight? A gateway that leaks a slot for every abandoned request locks the key once
  the slots run out. The gate keeps at most MH_MAX_INFLIGHT calls open (4), drops to 1 while the endpoint is
  recovering, and opens again after a few calls succeed.
- Who waits for it to come back? While it is down, one caller probes (with growing gaps) and every other caller
  waits on that result instead of sending requests that would only pile up behind it.
"""
from __future__ import annotations

import os
import threading
import time

PROBE_REUSE_S = 15


def _max_inflight() -> int:
    try:
        return max(1, int(os.environ.get("MH_MAX_INFLIGHT", "4")))
    except ValueError:
        return 4


class Health:
    def __init__(self):
        self.cond = threading.Condition()
        self.down_since = 0.0
        self.prober = False
        self.inflight = 0
        self.limit = _max_inflight()
        self.ok_streak = 0
        self.last_probe = (0.0, "")

    # ---------- is it up -----------------------------------------------------------------------------------------
    def is_down(self) -> bool:
        with self.cond:
            return self.down_since > 0

    def check(self, probe) -> str:
        """"up", "down" or "auth", from a fresh probe or one taken in the last few seconds."""
        with self.cond:
            t, got = self.last_probe
            if got and time.time() - t < PROBE_REUSE_S:
                return got
        res = probe()
        got = "up" if res == "ok" else "auth" if res == "auth" else "down"
        with self.cond:
            self.last_probe = (time.time(), got)
            if got == "down":
                self._mark_down()
        return got

    def _mark_down(self):
        if not self.down_since:
            self.down_since = time.time()
        self.limit, self.ok_streak = 1, 0           # while it recovers: one call at a time

    def _mark_up(self):
        self.down_since = 0.0
        self.last_probe = (time.time(), "up")
        self.cond.notify_all()

    def wait_until_up(self, probe, sleep, emit, budget_s: float, check_stop) -> bool:
        """Block until the endpoint answers a probe, for up to budget_s. One caller probes; the others wait for it.
        True when it is up. Stop works throughout (sleep and check_stop raise)."""
        t0, n, told = time.time(), 0, False
        while time.time() - t0 < budget_s:
            check_stop()
            with self.cond:
                if not self.down_since:
                    return True
                lead = not self.prober
                if lead:
                    self.prober = True
            if not lead:                            # another chat is probing: wait for its answer
                if not told:
                    told = True
                    emit({"type": "status", "text": "The model endpoint isn't answering. Another chat is already "
                                                    "checking it; this one carries on when it is back."})
                sleep(1)
                continue
            try:
                got = probe()
                if got == "auth":
                    raise RuntimeError("HTTP 401: the endpoint stopped accepting the API key while the task ran.")
                if got == "ok":
                    with self.cond:
                        self._mark_up()
                    emit({"type": "status", "text": "The model endpoint is answering again. Picking up where it "
                                                    "left off."})
                    return True
                n += 1
                wait = min(60, 10 * n)
                emit({"type": "status", "text": f"The model endpoint isn't answering ({str(got)[:80]}). Checking "
                                                f"again in {wait} s, for up to {int(budget_s // 60)} min. Press Stop "
                                                "to end."})
            finally:
                with self.cond:
                    self.prober = False
            sleep(wait)
        with self.cond:
            return not self.down_since

    # ---------- the in-flight gate -------------------------------------------------------------------------------
    def acquire(self, check_stop, emit=None):
        told = False
        with self.cond:
            while self.inflight >= self.limit:
                if emit and not told:
                    told = True
                    emit({"type": "status", "text": "Waiting for another chat's model call to finish (the endpoint "
                                                    "is recovering, so calls go one at a time)."})
                self.cond.wait(0.25)
                self.cond.release()
                try:
                    check_stop()
                finally:
                    self.cond.acquire()
            self.inflight += 1

    def release(self, ok: bool):
        with self.cond:
            self.inflight = max(0, self.inflight - 1)
            if ok:
                if self.down_since:
                    self._mark_up()
                self.ok_streak += 1
                if self.ok_streak >= 3:
                    self.limit = _max_inflight()
            self.cond.notify_all()


_REG: dict[str, Health] = {}
_REG_LOCK = threading.Lock()


def health(base: str, key: str) -> Health:
    k = (base or "").rstrip("/") + "|" + (key or "")[-8:]
    with _REG_LOCK:
        h = _REG.get(k)
        if h is None:
            h = _REG[k] = Health()
        return h


def reset():
    """Tests: forget every endpoint's state."""
    with _REG_LOCK:
        _REG.clear()
