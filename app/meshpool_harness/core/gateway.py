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


_CFG = {"t": 0.0, "v": None}


def _max_inflight() -> int:
    """The setting from the config file (read at most every 2 s), so a change in Settings reaches this process and
    the assistant servers without a restart; else the environment; else 4."""
    now = time.time()
    if now - _CFG["t"] > 2:
        _CFG["t"] = now
        try:
            from meshpool_harness import config as C
            vals = C.read()
            # saved, even empty (cleared, meaning the default): the file decides, not a stale environment
            _CFG["v"] = (vals.get("MH_MAX_INFLIGHT") or "4") if "MH_MAX_INFLIGHT" in vals else None
        except Exception:  # noqa: BLE001
            _CFG["v"] = None
    raw = _CFG["v"] or os.environ.get("MH_MAX_INFLIGHT") or "4"
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


class _Slots:
    """MH_MAX_INFLIGHT across processes: the app and every assistant server (where sub-agents run) take one of N
    lock files per endpoint and key. The operating system drops a lock when its process ends, so a crash never
    leaks a slot."""
    def __init__(self, name: str):
        import tempfile
        self.dir = os.path.join(tempfile.gettempdir(), "meshpool-gateway-slots")
        self.name = name

    def _try(self, i: int):
        path = os.path.join(self.dir, f"{self.name}-{i}.lock")
        f = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            f.close()
            return None

    def take(self, n, check_stop, waiting):
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            return None                              # no shared folder: the in-process gate still holds
        told = False
        while True:
            for i in range(n() if callable(n) else n):         # the setting as it is now, on every pass
                try:
                    f = self._try(i)
                except OSError:
                    return None
                if f:
                    return f
            if not told:
                told = True
                waiting()
            time.sleep(0.25)
            check_stop()

    @staticmethod
    def give(f):
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            f.close()                                # closing drops a POSIX flock


class Gate:
    def __init__(self, name: str = ""):
        self.slots = _Slots(name) if name and os.environ.get("MH_SHARED_SLOTS", "1") == "1" else None
        self.cond = threading.Condition()
        self.inflight = 0
        self.degraded = False
        self.ok_streak = 0

    @property
    def limit(self) -> int:
        return 1 if self.degraded else _max_inflight()  # the setting as it is now

    def degrade(self):
        with self.cond:
            self.degraded, self.ok_streak = True, 0  # while something recovers: one call at a time

    def acquire(self, check_stop, emit=None):
        told = False
        with self.cond:
            while self.inflight >= self.limit:
                if emit and not told:
                    told = True
                    emit({"type": "status", "text": "Waiting for another chat's model call to finish (the endpoint "
                                                    "is recovering, so calls go one at a time)." if self.degraded
                                                    else
                                                    f"Waiting for a free slot: {self.inflight} model calls are "
                                                    "already running on this key (MH_MAX_INFLIGHT)."})
                self.cond.wait(0.25)
                self.cond.release()
                try:
                    check_stop()
                finally:
                    self.cond.acquire()
            self.inflight += 1
        if self.slots is None:
            return None
        try:
            return self.slots.take(_max_inflight, check_stop, lambda: emit and emit({
                "type": "status", "text": "Waiting for a free slot: sub-agents or another Meshpool window are using "
                                          "this key's model calls (MH_MAX_INFLIGHT)."}))
        except BaseException:
            with self.cond:                          # Stop while waiting for a shared slot: give the local one back
                self.inflight = max(0, self.inflight - 1)
                self.cond.notify_all()
            raise

    def release(self, ok: bool, slot=None):
        _Slots.give(slot)
        with self.cond:
            self.inflight = max(0, self.inflight - 1)
            if ok:
                self.ok_streak += 1
                if self.ok_streak >= 3:
                    self.degraded = False
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
        return self.gate.acquire(check_stop, emit)

    def release(self, ok: bool, slot=None):
        if ok:
            with self.cond:
                if self.down_since:
                    self._mark_up()
        self.gate.release(ok, slot)

    # ---------- is it up -----------------------------------------------------------------------------------------
    def is_down(self) -> bool:
        with self.cond:
            return self.down_since > 0

    def check(self, probe, wait_s: float = 60, check_stop=None) -> str:
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
                if check_stop:
                    check_stop()                    # Stop works while waiting for another chat's probe
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
            import hashlib
            g = _GATES[k] = Gate(hashlib.sha256(k.encode("utf-8")).hexdigest()[:16])
        h = _REG.get(k + "|" + model)
        if h is None:
            h = _REG[k + "|" + model] = Health(g)
        return h


def reset():
    """Tests: forget every endpoint's state."""
    with _REG_LOCK:
        _REG.clear()
        _GATES.clear()
