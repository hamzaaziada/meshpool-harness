"""Agent sessions for the web app: each session is an Agent running in a thread, with its own MCP servers.
The page polls for events and answers approval requests."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.agent import mcp as M
from meshpool_harness.agent.core import SESSIONS, Agent
from meshpool_harness import harnesses as HR
from meshpool_harness.harnesses.acp import ACPAgent
from meshpool_harness.harnesses.pi_rpc import PiAgent

LIVE: dict[str, "Session"] = {}
LOCK = threading.Lock()
DELETED: set[str] = set()                            # deleted this run: never reopened, even by a late page poll


def events_from_messages(messages: list) -> list:
    """Rebuild a transcript from saved messages (for sessions reopened after the app restarted)."""
    evs, outputs = [], {m.get("tool_call_id"): m.get("content", "") for m in messages if m.get("role") == "tool"}
    for m in messages:
        r = m.get("role")
        if r == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            evs.append({"type": "user", "text": str(c).split("\n\n\u200b")[0]})
        elif r == "assistant":
            if m.get("content") and m["content"] != "(no reply)":
                evs.append({"type": "assistant", "text": m["content"], "streamed": False})
            for c in m.get("tool_calls") or []:
                f = c.get("function") or {}
                try:
                    from meshpool_harness.agent.core import _summary
                    summ = _summary(f.get("name", ""), json.loads(f.get("arguments") or "{}"))
                except ValueError:
                    summ = ""
                out = outputs.get(c.get("id"), "")
                evs.append({"type": "tool_start", "id": c.get("id"), "name": f.get("name", ""), "summary": summ,
                            "mcp": f.get("name", "").startswith("mcp__"), "diff": ""})
                evs.append({"type": "tool_end", "id": c.get("id"), "name": f.get("name", ""),
                            "ok": not out.startswith(("ERROR", "The user denied")),
                            "denied": out.startswith("The user denied"), "output": out[:6000]})
    for i, e in enumerate(evs):
        e["i"] = i
    return evs


MODES = ("ask", "edits", "auto")


def _meta_path(sid: str) -> Path:
    return SESSIONS / sid / "meta.json"


def read_meta(sid: str) -> dict:
    try:
        return json.loads(_meta_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(sid: str, **kw) -> dict:
    s = LIVE.get(sid)
    if s is None and not (SESSIONS / sid).is_dir():
        return {}                                     # deleted thread: do not recreate its folder
    m = read_meta(sid)
    m.update({k: v for k, v in kw.items() if v is not None})
    _meta_path(sid).parent.mkdir(parents=True, exist_ok=True)
    _meta_path(sid).write_text(json.dumps(m), encoding="utf-8")
    return m


def _image_text(images: list) -> str:
    """OCR of attached images, added to the message so every model (with or without vision) gets their text."""
    try:
        import base64
        from meshpool_harness import ocr
        if not ocr.available():
            return ""
        out = []
        for im in images[:6]:
            r = ocr.read_image(base64.b64decode(im.get("data", "")))
            if r["text"].strip():
                out.append(f"[Text in the attached image {im.get('name', 'image')} (OCR)]\n{r['text'][:4000]}")
        return ("\n\n\u200b" + "\n\n".join(out)) if out else ""
    except Exception:  # noqa: BLE001  OCR is a helper; the image still goes to the model
        return ""


class Session:
    def __init__(self, workdir: str | None = None, resume_id: str | None = None, mode: str | None = None,
                 harness: str | None = None, kind: str = "code", model: str | None = None, plan: bool = False):
        self.events: list[dict] = []
        self.epoch = 0
        self.plan = bool(plan)
        self.cp = None
        self.lock = threading.Lock()
        self.state = "starting"
        self.queue: list = []                       # messages sent while a harness without mid-turn input works
        self.ctx_pending = not resume_id             # instructions and memory go with the first message
        self.scheduled: dict | None = None           # the scheduled task that started this thread
        self.pending: dict | None = None
        self.mcp = None
        self.agent = None
        self.touched = time.time()                   # last view, send or event: idle threads are closed (_reap)
        self.mode = mode if mode in MODES else os.environ.get("MH_AGENT_PERMISSIONS", "ask")
        self._n = 0
        kw = dict(emit=self.emit, approve=self.approve, mode=self.mode)
        if resume_id:
            data = _saved(resume_id)
            if not data:
                raise KeyError(resume_id)
            self.harness = HR.get(data.get("harness") or "meshpool-code")
            self.kind = data.get("kind") or self.harness.get("mode", "code")
            if self.harness["type"] in ("acp", "pi"):
                cls = PiAgent if self.harness["type"] == "pi" else ACPAgent
                self.agent = cls.resume(data, self.harness, **kw)
                with self.lock:
                    self.events = [dict(e, i=i) for i, e in enumerate(self.agent.events)]
            else:
                a = Agent.resume(data["workdir"], resume_id, **kw)
                if not a:
                    raise KeyError(resume_id)
                self.agent = a
                with self.lock:
                    self.events = events_from_messages(a.messages[1:])
        else:
            if not workdir or not Path(workdir).expanduser().is_dir():
                raise ValueError(f"Project folder not found: {workdir or '(empty)'}")
            hid = harness or HR.default_for(kind)
            if hid == "meshpool-auto":                     # Auto picks per task; without one, use the mode default
                hid = "meshpool-code" if kind == "code" else "meshpool-work"
            self.harness = HR.get(hid)
            self.kind = kind if kind in HR.MODES else self.harness.get("mode", "code")
            if self.harness["type"] in ("acp", "pi"):
                if not self.harness.get("available"):
                    raise ValueError(f"{self.harness['name']} is not installed on this computer. "
                                     + (self.harness.get("install") or ""))
                cls = PiAgent if self.harness["type"] == "pi" else ACPAgent
                self.agent = cls(self.harness, workdir, **kw)
            else:
                self.agent = Agent(workdir, profile="work" if self.harness["id"] == "meshpool-work" else "code", **kw)
            self.agent.save()
        self.id, self.workdir = self.agent.id, str(self.agent.root)
        if resume_id:
            self.kind = read_meta(self.id).get("kind") or self.kind
        else:
            write_meta(self.id, kind=self.kind)          # the mode (Cowork or Code) the thread was started in
        if model:
            self.set_model(model)
        if resume_id:
            meta = read_meta(self.id)
            self.plan = bool(meta.get("plan", self.plan))
            if meta.get("model") and not getattr(self.agent, "model", None):
                self.set_model(meta["model"])
        if hasattr(self.agent, "plan"):
            self.agent.plan = self.plan
        from meshpool_harness.checkpoints import Checkpoints
        self.cp = Checkpoints(self.workdir, SESSIONS / self.id / "checkpoints")
        if self.harness["type"] in ("acp", "pi"):
            self.state = "idle"
            name = self.harness["name"]
            if not self.events:
                self.emit({"type": "status", "text": f"{name} is ready. "
                           + (self.harness.get("settings_note") or "")})
        else:
            threading.Thread(target=self._start_mcp, daemon=True).start()

    def _start_mcp(self):
        self.emit({"type": "status", "text": "Starting MCP servers..."})
        try:
            extra = {}
            if getattr(self.agent, "profile", "") == "work":
                # Meshpool Work gets the office server Hermes and Pi get: Word documents (make_document), PDFs,
                # conversions and document reading. Its own office_task covers slides, sheets and data only.
                from meshpool_harness.harnesses.acp import office_server
                spec = office_server([self.workdir])
                extra = {spec["name"]: {"command": spec["command"], "args": spec["args"],
                                        "env": {e["name"]: e["value"] for e in spec["env"]}}}
            self.mcp = M.Manager(roots=[self.workdir], extra=extra).start(
                timeout=float(os.environ.get("MH_MCP_START_S", "45")))
            self.agent.mcp = self.mcp
            ok = [s for s in self.mcp.status() if s["status"] == "connected"]
            bad = [s for s in self.mcp.status() if s["status"] == "error"]
            txt = ("MCP: " + ", ".join(f"{s['name']} ({len(s['tools'])} tools)" for s in ok)) if ok else "No MCP servers enabled."
            if bad:
                txt += ". Failed: " + "; ".join(f"{s['name']}: {s['error']}" for s in bad)
            if self.mcp.config_error:
                txt += ". " + self.mcp.config_error
            self.emit({"type": "status", "text": txt})
        except Exception as e:
            self.emit({"type": "status", "text": f"MCP did not start: {e}"})
        if self.state == "starting":
            self.state = "idle"

    def emit(self, ev: dict):
        if ev.get("type") == "interrupted" and getattr(self, "_interrupting", False):
            self._interrupting = False               # the new message is being answered: do not say "send a message"
            ev = dict(ev, text="Stopped the previous request.")
        with self.lock:
            ev = dict(ev, i=len(self.events), t=time.time())
            self.events.append(ev)

    def approve(self, kind, title, detail, options=None):
        with self.lock:
            self._n += 1
            p = {"id": self._n, "kind": kind, "title": title, "detail": detail, "ev": threading.Event(),
                 "decision": "no", "note": "", "options": options}
            self.pending = p
        self.state = "waiting"
        self.emit({"type": "approval", "id": p["id"], "kind": kind, "title": title, "detail": detail,
                   "options": options})
        if self.scheduled or time.time() - getattr(self, "t_turn", time.time()) > 60:
            _notify(f"{self.agent.title or 'A task'} needs your approval", title)
        while not p["ev"].wait(0.3):
            if self.agent._stop:
                break
        self.pending = None
        self.state = "working"
        shown = p["decision"] if p.get("answered") else "stopped"
        self.emit({"type": "approval_done", "id": p["id"], "decision": shown})
        return p["decision"], p["note"]

    def answer(self, aid: int, decision: str, note: str = ""):
        p = self.pending
        if not p or p["id"] != aid:
            raise ValueError("That approval is no longer waiting.")
        allowed = {"yes", "always", "no"} | {o["id"] for o in (p.get("options") or [])}
        p["decision"] = decision if decision in allowed else "no"
        p["note"] = note
        p["answered"] = True
        p["ev"].set()

    def _body(self, text: str, images: list | None) -> str:
        body = text
        if self.harness["type"] in ("acp", "pi"):
            try:
                from meshpool_harness import skills as SK
                body = SK.expand_mentions(text)
            except Exception:  # noqa: BLE001
                body = text
        if images:
            body += _image_text(images)
        return body

    def send(self, text: str, images: list | None = None, interrupt: bool = False) -> dict:
        """Start a turn. While the agent works, the message is added to the current task instead: the built-in
        agent and Pi read it before their next step; other harnesses get it as soon as the current turn ends.
        interrupt=True stops the current work first and sends the message as a new turn."""
        if not text.strip() and not images:
            raise ValueError("Type a message first.")
        self.touched = time.time()
        if self.state in ("working", "waiting") and interrupt:
            self.emit({"type": "status", "text": "Interrupted. Working on your new message instead."})
            self._interrupting = True
            with self.lock:                          # messages waiting in the queue go with the new one, not lost
                queued = [q[0] if isinstance(q, (list, tuple)) else str(q) for q in self.queue]
            if queued:
                text = "\n\n".join([*queued, text])
            self.stop()
            end = time.time() + 15
            while self.state in ("working", "waiting") and time.time() < end:
                time.sleep(0.1)
            if self.harness.get("type") == "acp":
                # ACP agents (Hermes, the subscriptions) can merge the cancelled message into the new one and carry
                # on with the old task. Say plainly that it was stopped; the note is hidden in the chat (after \u200b).
                text = text + ("\n\n\u200b[Meshpool] I stopped your previous request. Do not continue it; "
                               "answer this message instead.")
        if self.state in ("working", "waiting"):
            if hasattr(self.agent, "steer") and self.harness["type"] != "acp":
                self.agent.steer(self._body(text, images), images=images)
                return {"queued": False, "added": True}
            with self.lock:
                self.queue.append((text, images))
            self.emit({"type": "queued", "text": text, "n": len(self.queue)})
            return {"queued": True}
        self._start_turn(text, images)
        return {"queued": False}

    def _start_turn(self, text: str, images: list | None):
        C.load_into_env(override=True)              # newest settings (model, key) for this turn
        self.state = "working"
        if hasattr(self.agent, "plan"):
            self.agent.plan = self.plan

        self.t_turn = time.time()

        def go():
            idx = len(self.agent.messages) if self.harness["type"] not in ("acp", "pi") else None
            try:
                if self.cp:
                    try:
                        t = self.cp.begin_turn(text.strip().splitlines()[0] if text.strip() else "Images", idx)
                        if t is None and self.cp.disabled:
                            self.emit({"type": "status", "text": self.cp.disabled})
                    except Exception as e:  # noqa: BLE001
                        self.emit({"type": "status", "text": f"Could not record a checkpoint: {e}"})
                body = self._body(text, images)
                if self.ctx_pending:
                    self.ctx_pending = False
                    try:
                        from meshpool_harness import assistant as AS
                        body += "\n\n\u200b" + AS.context_block()
                    except Exception:  # noqa: BLE001
                        pass
                if images:
                    self.agent.send(body, images=images)
                else:
                    self.agent.send(body)
            finally:
                if self.cp:
                    try:
                        self.cp.end_turn()
                        self.emit({"type": "checkpoint", "turns": self.cp.turns()[-1:]})
                    except Exception:  # noqa: BLE001
                        pass
                write_meta(self.id, model=getattr(self.agent, "model", None), plan=self.plan)
                nxt = None
                with self.lock:
                    if self.queue and not getattr(self.agent, "_stop", False):
                        items, self.queue = self.queue, []
                        nxt = ("\n\n".join(t for t, _ in items), [im for _, ims in items for im in (ims or [])])
                    elif self.queue:
                        self.queue = []
                if nxt:
                    self.emit({"type": "queued", "text": "", "n": 0})
                    self._start_turn(*nxt)               # stays "working": the queued message goes straight in
                else:
                    self.state = "idle"
                    took = time.time() - self.t_turn
                    if (self.scheduled or took > 45) and not getattr(self.agent, "_stop", False):
                        last = next((e.get("text") for e in reversed(self.events)
                                     if e.get("type") == "assistant" and e.get("text")), "") or "Finished."
                        _notify(("Done: " if not self.scheduled else "Scheduled task done: ")
                                + (self.agent.title or "task")[:60], last.strip()[:200])
        threading.Thread(target=go, daemon=True).start()

    def set_model(self, model: str):
        model = (model or "").strip()
        if hasattr(self.agent, "set_model"):
            self.agent.set_model(model)
        else:
            self.agent.model = model or None
        write_meta(self.id, model=model)

    def set_plan(self, on: bool):
        self.plan = bool(on)
        if hasattr(self.agent, "plan"):
            self.agent.plan = self.plan
        write_meta(self.id, plan=self.plan)

    def revert(self, turn: int) -> dict:
        if self.state in ("working", "waiting"):
            raise ValueError("Stop the agent before undoing a turn.")
        if not self.cp:
            raise ValueError("No checkpoints for this thread.")
        r = self.cp.revert_to_before(int(turn))
        idx = r.get("message_index")
        if self.harness["type"] not in ("acp", "pi") and isinstance(idx, int) and 0 < idx <= len(self.agent.messages):
            self.agent.messages = self.agent.messages[:idx]
            self.agent.save()
            with self.lock:
                self.events = events_from_messages(self.agent.messages[1:])
        else:
            with self.lock:
                # the harness keeps its own history; keep the transcript up to that turn
                users = [i for i, e in enumerate(self.events) if e.get("type") == "user"]
                cut = users[int(turn) - 1] if 0 < int(turn) <= len(users) else len(self.events)
                self.events = [dict(e, i=i) for i, e in enumerate(self.events[:cut])]
                if hasattr(self.agent, "events"):
                    self.agent.events = [{k: v for k, v in e.items() if k not in ("i", "t")} for e in self.events]
                    self.agent.save()
        self.epoch += 1
        def _n(k, word):
            n = len(r.get(k) or [])
            return f"{n} file{'' if n == 1 else 's'} {word}"
        parts = [s for s in (_n("restored", "restored") if r.get("restored") else "",
                             _n("deleted", "removed") if r.get("deleted") else "") if s]
        msg = f"Undid turn {turn} and everything after it: " + (", ".join(parts) if parts else
                                                                 "the agent changed no files in those turns")
        if r.get("kept"):
            names = ", ".join(r["kept"][:5]) + (" ..." if len(r["kept"]) > 5 else "")
            msg += f". Left as they are, because they changed after the agent: {names}"
        self.emit({"type": "status", "text": msg + "."})
        return r

    def stop(self):
        with self.lock:
            had = bool(self.queue)
            self.queue = []
        if had:
            self.emit({"type": "queued", "text": "", "n": 0})
        if self.agent:
            self.agent.stop()
        if self.pending:
            self.pending["ev"].set()

    def set_mode(self, mode: str):
        self.agent.mode = self.mode = mode if mode in MODES else "ask"

    def view(self, since: int) -> dict:
        self.touched = time.time()
        with self.lock:
            evs = self.events[since:]
        a = self.agent
        acp = self.harness["type"] in ("acp", "pi")
        meta = read_meta(self.id)
        return {"id": self.id, "workdir": self.workdir, "state": self.state, "mode": a.mode,
                "title": meta.get("title") or a.title, "pinned": bool(meta.get("pinned")),
                "usage": a.usage, "context": getattr(a, "context", None), "kind": self.kind,
                "harness": {k: self.harness.get(k) for k in ("id", "name", "type", "description", "models")},
                "model": (a.current_model() if hasattr(a, "current_model") else
                          getattr(a, "model", None) or a.agent_info.get("name", self.harness["name"])),
                "harness_models": getattr(a, "available_models", None), "plan": self.plan,
                "epoch": self.epoch, "turns": self.cp.turns() if self.cp else [],
                "checkpoints_note": self.cp.disabled if self.cp else "",
                "events": evs, "total": since + len(evs),
                "mcp": self.mcp.status() if self.mcp else None}

    def close(self):
        self.stop()
        if self.mcp:
            self.mcp.close()
        if hasattr(self.agent, "close"):
            self.agent.close()


# ---------- module API used by server.py ----------------------------------------------------------------------------

def create(body: dict) -> dict:
    from meshpool_harness.ui.desktop import default_project
    wd = (body.get("workdir") or "").strip().strip('"')
    if not wd or Path(wd).expanduser().resolve() == default_project().resolve():
        wd = str(default_project())
        Path(wd).mkdir(parents=True, exist_ok=True)
    harness, routed = body.get("harness") or None, None
    kind = body.get("kind") or "code"
    if (harness or HR.default_for(kind)) == "meshpool-auto":
        from meshpool_harness import harness_router as HRT
        routed = HRT.route(body.get("task") or "", kind)
        harness = routed["harness"]
    model = body.get("model") or None
    try:
        if HR.get(harness or HR.default_for(kind)).get("models") != "meshpool":
            model = None                            # its own account picks the model; a Meshpool id would not exist
    except (KeyError, ValueError):
        pass
    s = Session(workdir=wd, mode=body.get("mode"), harness=harness,
                kind=kind, model=model, plan=bool(body.get("plan")))
    if routed:
        s.emit({"type": "status", "text": f"Meshpool Auto picked {routed['name']}. {routed['reason']}"})
        write_meta(s.id, routed=routed["harness"])
    with LOCK:
        LIVE[s.id] = s
    _start_reaper()
    try:
        from meshpool_harness.ui import workbench as W
        W.add_project(s.workdir)
    except Exception:  # noqa: BLE001
        pass
    return s.view(0)


_REAPER: list = []


def _reap_once(idle_s: float) -> list:
    """Close threads nobody has used for idle_s seconds: not working, not waiting for an answer, not viewed. Their
    MCP servers and harness processes end; opening the thread again resumes it from its saved state."""
    now, closed = time.time(), []
    with LOCK:
        idle = [(sid, s) for sid, s in LIVE.items()
                if s.state not in ("working", "waiting", "starting") and not s.pending and not s.queue
                and now - getattr(s, "touched", now) > idle_s]
        for sid, _ in idle:
            LIVE.pop(sid, None)
    for sid, s in idle:
        try:
            s.close()
        except Exception:  # noqa: BLE001 - a helper that is already gone is fine
            pass
        closed.append(sid)
    return closed


def _start_reaper():
    """MH_IDLE_CLOSE_S (default 900; 0 = never): how long an unused thread keeps its helper processes."""
    idle_s = float(os.environ.get("MH_IDLE_CLOSE_S", "900") or 0)
    if _REAPER or idle_s <= 0:
        return

    def loop():
        while True:
            time.sleep(min(60.0, max(5.0, idle_s / 4)))
            _reap_once(idle_s)
    th = threading.Thread(target=loop, daemon=True, name="meshpool-reaper")
    _REAPER.append(th)
    th.start()


def get(sid: str) -> Session:
    if sid in DELETED:
        raise KeyError(sid)
    with LOCK:
        s = LIVE.get(sid)
    if s:
        return s
    s = Session(resume_id=sid)                   # reopen a saved session
    with LOCK:
        LIVE[sid] = s
    return s


def listing() -> list:
    rows = {}
    if SESSIONS.exists():
        for p in SESSIONS.glob("*/session.json"):
            if p.parent.name.startswith("sub-"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows[d["id"]] = {"id": d["id"], "title": d.get("title") or "New thread", "workdir": d.get("workdir"),
                             "updated": d.get("updated", 0), "state": "saved", "kind": d.get("kind") or "code",
                             "harness": d.get("harness") or "meshpool-code", "model": d.get("model")}
    with LOCK:
        for sid, s in LIVE.items():
            rows[sid] = {"id": sid, "title": s.agent.title or "New thread", "workdir": s.workdir,
                         "updated": time.time() if s.state != "idle" else rows.get(sid, {}).get("updated", time.time()),
                         "state": s.state, "kind": s.kind, "harness": s.harness["id"]}
    out = []
    for r in rows.values():
        if r["id"] in DELETED:
            continue
        m = read_meta(r["id"])
        if m.get("deleted"):
            continue
        r["title"] = m.get("title") or r["title"]
        r["pinned"], r["archived"] = bool(m.get("pinned")), bool(m.get("archived"))
        r["kind"] = m.get("kind") or r.get("kind") or "code"
        out.append(r)
    return sorted(out, key=lambda r: -r["updated"])[:400]


def thread_action(sid: str, body: dict) -> dict:
    act = body.get("action")
    if act == "rename":
        write_meta(sid, title=(body.get("title") or "").strip()[:120] or None)
    elif act in ("pin", "unpin"):
        write_meta(sid, pinned=act == "pin")
    elif act in ("archive", "unarchive"):
        write_meta(sid, archived=act == "archive")
    elif act == "delete":
        with LOCK:
            DELETED.add(sid)
            s = LIVE.pop(sid, None)
        if s:
            s.deleted = True
            s.agent.deleted = True                    # its last save after stopping must not recreate the thread
            s.close()
            end = time.time() + 6
            while s.state in ("working", "waiting") and time.time() < end:
                time.sleep(0.1)
        import shutil
        d = SESSIONS / sid
        if d.is_dir() and d.parent == SESSIONS:
            shutil.rmtree(d, ignore_errors=True)
    else:
        raise ValueError("Unknown thread action.")
    return {"ok": True}


def search(q: str) -> list:
    q = q.strip().lower()
    if not q:
        return []
    hits = []
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        low = raw.lower()
        i = low.find(q)
        if i < 0:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if read_meta(d["id"]).get("deleted") or any(h["id"] == d["id"] for h in hits):
            continue
        title = read_meta(d["id"]).get("title") or d.get("title") or "New thread"
        # Search what the user reads (titles, messages, replies), not the saved JSON around it.
        texts = [title]
        for m in d.get("messages") or []:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            if isinstance(c, str) and m.get("role") in ("user", "assistant"):
                texts.append(c.split("\n\n\u200b")[0])
        for e in d.get("events") or []:
            if isinstance(e, dict) and e.get("type") in ("user", "assistant") and isinstance(e.get("text"), str):
                texts.append(e["text"])
        hit = next((s for s in texts if q in s.lower()), None)
        if hit is None:
            continue
        j = hit.lower().find(q)
        snippet = " ".join(hit[max(0, j - 60): j + 100].split())
        snippet = ("…" if j > 60 else "") + snippet + ("…" if j + 100 < len(hit) else "")
        hits.append({"id": d["id"], "title": title, "workdir": d.get("workdir"), "updated": d.get("updated", 0),
                     "snippet": snippet})
    return sorted(hits, key=lambda r: -r["updated"])[:50]


def mcp_info() -> dict:
    path = M.config_file()
    text = path.read_text(encoding="utf-8") if path.exists() else '{\n  "mcpServers": {}\n}\n'
    try:
        cfg = M.load_config(path)
        servers = [{"name": n, "enabled": not s.get("disabled"), "description": s.get("description", ""),
                    "kind": "http" if s.get("url") else "stdio",
                    "target": s.get("url") or " ".join([s.get("command", "")] + [str(a) for a in s.get("args", [])])}
                   for n, s in cfg.items() if isinstance(s, dict)]
        err = ""
    except ValueError as e:
        servers, err = [], f"mcp.json is not valid JSON: {e}"
    return {"path": str(path), "text": text, "servers": servers, "error": err}


def mcp_save(body: dict) -> dict:
    path = M.config_file()
    if "text" in body:
        try:
            json.loads(body["text"])
        except ValueError as e:
            raise ValueError(f"Not valid JSON: {e}")
        path.write_text(body["text"].rstrip() + "\n", encoding="utf-8")
    elif "name" in body:
        data = json.loads(path.read_text(encoding="utf-8"))
        key = "mcpServers" if "mcpServers" in data else "servers"
        srv = data.get(key, {}).get(body["name"])
        if srv is None:
            raise ValueError(f"No server named {body['name']}")
        if body.get("enabled"):
            srv.pop("disabled", None)
        else:
            srv["disabled"] = True
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return mcp_info()


def mcp_test(body: dict) -> dict:
    C.load_into_env(override=True)
    wd = (body.get("workdir") or "").strip() or str(Path.home())
    m = M.Manager(roots=[wd if Path(wd).is_dir() else str(Path.home())]).start(timeout=90, use_health=False)
    try:
        return {"status": m.status(), "error": m.config_error}
    finally:
        m.close()


def close_all():
    with LOCK:
        for s in LIVE.values():
            s.close()


def _notify(title: str, body: str):
    try:
        from meshpool_harness.ui.desktop import notify
        notify(title, body)
    except Exception:  # noqa: BLE001
        pass


def run_scheduled(t: dict) -> str:
    """Start a new thread for a scheduled task and send its prompt. Returns the thread id."""
    from meshpool_harness import assistant as AS
    folder = t.get("folder") or ""
    if not folder or not Path(folder).expanduser().is_dir():
        folder = ""
    v = create({"workdir": folder, "harness": t.get("harness") or None, "kind": t.get("kind") or "cowork",
                "task": t.get("prompt", "")})
    s = get(v["id"])
    s.scheduled = t
    write_meta(s.id, title=f"{t.get('name') or 'Scheduled task'} ({time.strftime('%d %b %H:%M')})", scheduled=t["id"])
    s.send(t["prompt"] + ("\n\n\u200b(This is a scheduled run. The user is not watching: make reasonable choices, say "
                          "which ones you made, and finish with a short summary.)"))
    AS.set_thread(t["id"], s.id)
    return s.id


def probe_mcp():
    """At app start, start every enabled MCP server once and remember which fail, so threads skip broken ones."""
    try:
        time.sleep(3)
        C.load_into_env(override=True)
        M.Manager(roots=[str(Path.home())]).start(timeout=45, use_health=False).close()
    except Exception:  # noqa: BLE001
        pass


def scheduler_loop():
    from meshpool_harness import assistant as AS
    while True:
        try:
            for t in AS.due():
                try:
                    run_scheduled(t)
                except Exception as e:  # noqa: BLE001
                    _notify(f"Scheduled task could not start: {t.get('name')}", str(e)[:200])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(20)


def _saved(sid: str) -> dict | None:
    p = SESSIONS / sid / "session.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def harness_info() -> dict:
    HR.ensure_defaults()
    return {"harnesses": [{k: v for k, v in h.items() if k not in ("env",)} for h in HR.load_all()],
            "examples": HR.examples(), "folder": str(HR.folder()),
            "defaults": {m: HR.default_for(m) for m in HR.MODES}}


def harness_change(body: dict) -> dict:
    if body.get("text"):
        HR.add(body["text"])
    elif body.get("example"):
        HR.add_example(body["example"])
    elif body.get("remove"):
        HR.remove(body["remove"])
    elif body.get("default_mode") in HR.MODES:
        hid = body.get("id") or ""
        HR.get(hid)                                  # raises KeyError for an unknown harness
        C.write({f"MH_HARNESS_{body['default_mode'].upper()}": hid})
        C.load_into_env(override=True)
    return harness_info()
