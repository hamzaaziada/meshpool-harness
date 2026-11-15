"""Client-side handling of a gateway that stalls (Harness v4, gateway health).

Shared by every chat and sub-agent in the process:

- A Gate per endpoint and key: how many model calls may be open at once. A gateway that leaks a slot for every
  abandoned request locks the key once its slots run out, so at most MH_MAX_INFLIGHT calls are open (4). While a
  model on the key is recovering from an outage, calls go one at a time; the limit opens again after three calls
  go through.
- A Health per endpoint, key and model: is it serving? One tiny probe after a failure tells a stuck endpoint (the
  probe fails too) from a request that is only slow (the probe answers). A gateway can fail one model and serve the
  rest, so each model has its own: one model's outage never holds back a chat on another model. While it is down,
  one caller probes at a time on a backoff shared by every caller, and the others wait for its answer instead of
  sending requests that would only pile up behind it.
"""
from __future__ import annotations

import os
import threading
import time

PROBE_REUSE_S = 15
BACKOFF_S = 10                                      # gaps between probes while down: 10, 20 ... 60 s


def _max_inflight() -> int:
    try:
        return max(1, int(os.environ.get("MH_MAX_INFLIGHT", "4")))
    except ValueError:
        return 4


class Gate:
    def __init__(self):
        self.cond = threading.Condition()
        self.inflight = 0
        self.limit = _max_inflight()
        self.ok_streak = 0

    def degrade(self):
        with self.cond:
            self.limit, self.ok_streak = 1, 0       # while something recovers: one call at a time

    def acquire(self, check_stop, emit=None):
        told = False
        with self.cond:
            while self.inflight >= self.limit:
                if emit and not told:
                    told = True
                    emit({"type": "status", "text": "Waiting for another chat's model call to finish (the endpoint "
                                                    "is recovering, so calls go one at a time)." if self.limit == 1
                                                    and self.limit < _max_inflight() else
                                                    f"Waiting for a free slot: {self.inflight} model calls are "
                                                    "already running on this key (MH_MAX_INFLIGHT)."})
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
                self.ok_streak += 1
                if self.ok_streak >= 3:
                    self.limit = _max_inflight()
            self.cond.notify_all()


class Health:
    def __init__(self, gate: Gate | None = None):
        self.gate = gate or Gate()
        self.cond = threading.Condition()
        self.down_since = 0.0
        self.prober = False
        self.last_probe = (0.0, "")
        self.next_probe = 0.0                       # while down: no one probes before this (shared backoff)
        self.fails = 0
        self.up_at = 0.0

    # the gate is shared by every model on the key
    @property
    def inflight(self) -> int:
        return self.gate.inflight

    @property
    def limit(self) -> int:
        return self.gate.limit

    def acquire(self, check_stop, emit=None):
        self.gate.acquire(check_stop, emit)

    def release(self, ok: bool):
        if ok:
            with self.cond:
                if self.down_since:
                    self._mark_up()
        self.gate.release(ok)

    # ---------- is it up -----------------------------------------------------------------------------------------
    def is_down(self) -> bool:
        with self.cond:
            return self.down_since > 0

    def check(self, probe, wait_s: float = 60) -> str:
        """"up", "down" or "auth" after a call failed: a probe taken in the last few seconds, another caller's probe
        that is running now, or a fresh one. While the endpoint is already marked down this does not probe: the
        caller goes on to wait_until_up."""
        end = time.time() + wait_s
        with self.cond:
            while True:
                if self.down_since:
                    return "down"
                t, got = self.last_probe
                if got and time.time() - t < PROBE_REUSE_S:
                    return got
                if not self.prober:
                    break
                if time.time() >= end:              # a probe that never returns: answer for it
                    return "down"
                self.cond.wait(0.25)                # another chat is probing: use its answer
            self.prober = True
            started = time.time()
        got = None                                  # stays None when the probe raised (Stop): record nothing
        try:
            res = probe()
            got = "up" if res == "ok" else "auth" if res == "auth" else "down"
        finally:
            with self.cond:
                self.prober = False
                if got is None:
                    pass
                elif got == "down" and self.up_at > started:   # an answer from before it came back: stale
                    got = "up"
                else:
                    self.last_probe = (time.time(), got)
                    if got == "down":
                        self._mark_down()
                        self._failed()              # this probe counts: the next one waits its turn
                    elif got == "up" and self.down_since:
                        self._mark_up()
                self.cond.notify_all()
        return got

    def _failed(self) -> float:
        self.fails += 1
        wait = min(6 * BACKOFF_S, BACKOFF_S * self.fails)
        self.next_probe = time.time() + wait
        self.last_probe = (time.time(), "down")
        return wait

    def _mark_down(self):
        if not self.down_since:
            self.down_since = time.time()
        self.gate.degrade()

    def _mark_up(self):
        self.down_since, self.next_probe, self.fails = 0.0, 0.0, 0
        self.up_at = time.time()
        self.last_probe = (self.up_at, "up")
        self.cond.notify_all()

    def wait_until_up(self, probe, sleep, emit, budget_s: float, check_stop) -> bool:
        """Block until the endpoint answers a probe, for up to budget_s. One caller probes at a time, on a backoff
        shared by every caller (10, 20 ... 60 s); the others wait for its answer. With no budget (recovery off) the
        caller probes once at once, whatever the backoff, so a past outage never blocks the app. True when it is
        up. Stop works throughout (sleep and check_stop raise)."""
        t0, told, first = time.time(), False, True
        while True:
            check_stop()
            with self.cond:
                if not self.down_since:
                    return True
                now = time.time()
                lead = not self.prober and (now >= self.next_probe or (first and budget_s <= 0))
                if not lead and now - t0 >= budget_s:
                    return False
                if lead:
                    self.prober = True
                prober = self.prober
            first = False
            if not lead:                            # another caller is probing, or it is not time yet
                if not told and prober:
                    told = True
                    emit({"type": "status", "text": "The model endpoint isn't answering. Another chat is already "
                                                    "checking it; this one carries on when it is back."})
                sleep(min(1.0, max(0.05, budget_s - (time.time() - t0))))
                continue
            wait, got = 0.0, ""
            try:
                got = probe()
                if got == "auth":
                    raise RuntimeError("HTTP 401: the endpoint stopped accepting the API key while the task ran.")
                with self.cond:
                    if got == "ok":
                        self._mark_up()
                    else:
                        wait = self._failed()
            finally:
                with self.cond:
                    self.prober = False
                    self.cond.notify_all()
            if got == "ok":
                emit({"type": "status", "text": "The model endpoint is answering again. Picking up where it left off."})
                return True
            if time.time() - t0 + wait < budget_s:
                emit({"type": "status", "text": f"The model endpoint isn't answering ({str(got)[:80]}). Checking "
                                                f"again in {wait:.0f} s, for up to {max(1, int(budget_s // 60))} min. "
                                                "Press Stop to end."})


_GATES: dict[str, Gate] = {}
_REG: dict[str, Health] = {}
_REG_LOCK = threading.Lock()


def health(base: str, key: str, model: str = "") -> Health:
    """The Health of one model on one endpoint and key; every model on the key shares one Gate."""
    k = (base or "").rstrip("/") + "|" + (key or "")[-8:]
    with _REG_LOCK:
        g = _GATES.get(k)
        if g is None:
            g = _GATES[k] = Gate()
        h = _REG.get(k + "|" + model)
        if h is None:
            h = _REG[k + "|" + model] = Health(g)
        return h


def reset():
    """Tests: forget every endpoint's state."""
    with _REG_LOCK:
        _REG.clear()
        _GATES.clear()
