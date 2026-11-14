"""Settings > Company: the company look decks are built in (engines/forge/brand.py), behind /api/company/*.

    GET  /api/company                      profiles, the active one, the colour roles
    POST /api/company/learn    {paths, name?}       learn from uploaded decks (paths from /api/upload); saved, active
    POST /api/company/save     {profile, active?}   edits from the form
    POST /api/company/activate {id | null}          the profile new decks use (null: the Meshpool look)
    POST /api/company/delete   {id}
    POST /api/company/logo?id=&name=  <image bytes>
    POST /api/company/sample   {id}                 a three-slide sample deck in the look, no model call
    POST /api/company/open     {id}                 open that sample deck on this computer

The server routes every /api/company path here after its token check; handle() returns (status, payload).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote

SAMPLES = "company-sample"                     # runs/<SAMPLES>/out/, served by the /files route


def _view(p: dict) -> dict:
    """A profile for the browser: the logo inlined (it is small), no server paths."""
    out = {k: v for k, v in p.items() if k not in ("logo", "template")}
    out["has_master"] = bool(p.get("template") and Path(p["template"]).exists())
    logo = p.get("logo")
    if logo and Path(logo).exists() and Path(logo).stat().st_size < 3_000_000:
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif"}.get(Path(logo).suffix.lower(),
                                                                                    "image/png")
        out["logo_data"] = f"data:{mime};base64," + base64.b64encode(Path(logo).read_bytes()).decode()
    return out


def state() -> dict:
    from engines.forge import brand as B
    ps = B.profiles()
    return {"active": ps["active"], "profiles": [_view(p) for p in ps["profiles"]],
            "roles": [{"key": k, "label": lb, "use": use} for k, lb, use in B.ROLES]}


def _uploads_only(paths: list) -> list[str]:
    """Learn only from files the app's upload route wrote (runs/_uploads), never from an arbitrary path."""
    from meshpool_harness.ui import server as S
    base = S.UPLOADS.resolve()
    out = []
    for p in paths or []:
        rp = Path(str(p)).resolve()
        if base in rp.parents and rp.is_file():
            out.append(str(rp))
    if not out:
        raise ValueError("Upload the decks first.")
    return out


def _sample_dir() -> Path:
    from meshpool_harness.ui import server as S
    d = S.RUNS / SAMPLES / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sample_path(prof: dict) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", prof.get("name") or "company").strip("-")[:40] or "company"
    return _sample_dir() / f"{slug}-sample-deck.pptx"


def handle(method: str, u, body: bytes) -> tuple[int, dict]:
    import json
    from engines.forge import brand as B
    path = u.path.rstrip("/")
    if method == "GET":
        if path == "/api/company":
            return 200, state()
        return 404, {"error": "Not found."}
    data = {}
    if path != "/api/company/logo" and body:
        data = json.loads(body.decode("utf-8"))
    if path == "/api/company/learn":
        prof = B.learn(_uploads_only(data.get("paths")), name=(data.get("name") or None))
        prof = B.save(prof, activate=True)                # just learned: new decks use it (one click to undo)
        return 200, {**state(), "learned": prof["id"]}
    if path == "/api/company/save":
        got = data.get("profile") or {}
        old = B.get(got.get("id", "")) if got.get("id") else None
        if got.get("id") and not old:
            raise ValueError("That company profile no longer exists.")
        prof = dict(old or {"logo": None, "template": None, "template_mode": "forge"})
        for k in ("name", "footer", "colors", "fonts", "template_mode"):
            if k in got:
                prof[k] = got[k]
        if got.get("remove_logo"):
            prof["logo"], prof["logo_on_master"] = None, False
        prof = B.save(prof, activate=data.get("active"))
        return 200, {**state(), "saved": prof["id"]}
    if path == "/api/company/activate":
        ix = B._index()
        pid = data.get("id")
        if pid and pid not in ix.get("profiles", {}):
            raise ValueError("That company profile no longer exists.")
        ix["active"] = pid or None
        B._write_index(ix)
        return 200, state()
    if path == "/api/company/delete":
        B.delete(str(data.get("id") or ""))
        return 200, state()
    if path == "/api/company/logo":
        q = parse_qs(u.query)
        B.set_logo(q.get("id", [""])[0], unquote(q.get("name", ["logo.png"])[0]), body)
        return 200, state()
    if path in ("/api/company/sample", "/api/company/open"):
        prof = B.get(str(data.get("id") or ""))
        if not prof:
            raise ValueError("Save the company first.")
        out = _sample_path(prof)
        if path == "/api/company/open":
            if not out.exists():
                raise ValueError("Build the sample deck first.")
            from meshpool_harness.ui.desktop import reveal
            try:
                reveal(out)
            except OSError as e:
                raise ValueError(f"Could not open it on this computer ({e}). It is at: {out}") from e
            return 200, {"ok": True}
        from meshpool_harness.ui import server as S
        res = B.sample_deck(prof, str(out))
        return 200, {"ok": True, "file": out.name, "errors": res.get("errors") or [],
                     "url": f"/files/{SAMPLES}/{out.name}?t={S.TOKEN}"}
    return 404, {"error": "Not found."}
