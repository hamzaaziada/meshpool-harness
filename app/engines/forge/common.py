"""Shared pieces for the forge measurers and validators: rule config, failure records, evidence IDs, LibreOffice,
font metrics. Nothing here calls a model."""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
EMU_PER_IN = 914400


# ------------------------------------------------------------------ config
def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def load_rules(override: str | dict | None = None) -> dict:
    """rules.yaml, deep-merged with an optional client override (path or dict)."""
    rules = yaml.safe_load((HERE / "rules.yaml").read_text())
    if override:
        over = yaml.safe_load(Path(override).read_text()) if isinstance(override, (str, Path)) else override
        rules = _merge(rules, over or {})
    return rules


# ------------------------------------------------------------------ identity and evidence
def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def evidence_id(file_sha: str, key: str) -> str:
    """Stable ID for one measurement on one file: EV-<file sha8>-<metric>."""
    return f"EV-{file_sha[:8]}-{key}"


def failure(rule: str, check: str, message: str, *, slide=None, shape=None, sheet=None, cell=None, measured=None,
            limit=None, fix_scope: str = "content", severity: str = "error", status: str = "fail") -> dict:
    """One machine-readable failure. `message` is written so any LLM can act on it without other context."""
    f = {"rule": rule, "check": check, "status": status, "severity": severity, "fix_scope": fix_scope,
         "message": message}
    for k, v in (("slide", slide), ("shape", shape), ("sheet", sheet), ("cell", cell), ("measured", measured),
                 ("limit", limit)):
        if v is not None:
            f[k] = v
    return f


def summarize(failures: list[dict], rules_run: list[str]) -> dict:
    """Per-rule status: pass / fail / not_run (a rule that could not be evaluated is never reported as a pass)."""
    by = {r: "pass" for r in rules_run}
    for f in failures:
        r = f["rule"]
        if f["status"] == "fail" and f["severity"] == "error":
            by[r] = "fail"
        elif f["status"] == "not_run" and by.get(r) == "pass":
            by[r] = "not_run"
        elif f["status"] == "fail" and by.get(r) == "pass":
            by[r] = "warn"
    return by


# ------------------------------------------------------------------ LibreOffice
def find_soffice() -> str | None:
    for c in (os.environ.get("SOFFICE"), "soffice", "libreoffice",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if not c:
            continue
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def soffice_convert(src: str, to: str, out_dir: str, timeout: int = 240) -> Path:
    """Headless convert with a throwaway profile, so parallel runs never share a lock."""
    exe = find_soffice()
    if not exe:
        raise RuntimeError("LibreOffice not installed")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        r = subprocess.run([exe, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", to,
                            "--outdir", str(out_dir), str(src)], capture_output=True, text=True, timeout=timeout)
    ext = to.split(":")[0]
    out = Path(out_dir) / (Path(src).stem + "." + ext)
    if not out.exists():
        raise RuntimeError(f"LibreOffice could not convert {Path(src).name}: {(r.stderr or r.stdout)[-400:]}")
    return out


# ------------------------------------------------------------------ fonts
@lru_cache(maxsize=64)
def font_file(family: str, bold: bool = False, italic: bool = False, metric_map: tuple = ()) -> tuple[str | None, bool]:
    """Returns (path, exact). exact is False when a metric-compatible substitute or a fallback was used."""
    mapping = dict(metric_map)
    style = ("Bold" if bold else "") + ("Italic" if italic else "")
    for name, exact in ((family, True), (mapping.get(family), False)):
        if not name:
            continue
        fc = shutil.which("fc-match")
        if fc:
            try:
                q = f"{name}:style={style or 'Regular'}"
                r = subprocess.run([fc, "-f", "%{family}\t%{file}", q], capture_output=True, text=True, timeout=10)
                fam, _, path = r.stdout.partition("\t")
                if path and name.lower() in fam.lower():
                    return path, exact
            except Exception:
                pass
    fallback = "/usr/share/fonts/truetype/crosextra/Carlito-" + ({"": "Regular"}.get(style, style)) + ".ttf"
    if Path(fallback).exists():
        return fallback, False
    return None, False


@lru_cache(maxsize=512)
def _pil_font(path: str, size_pt: float):
    from PIL import ImageFont
    return ImageFont.truetype(path, max(1, int(round(size_pt * 10))))


def text_width_in(text: str, size_pt: float, path: str | None) -> float:
    if not path:
        return len(text) * size_pt * 0.5 / 72          # crude average advance when no font file exists at all
    return _pil_font(path, size_pt).getlength(text) / 10 / 72


def wrap_lines(text: str, width_in: float, size_pt: float, path: str | None, safety: float = 0.95) -> int:
    usable = max(width_in * safety, 0.05)
    n = 0
    for para in text.split("\n"):
        cur = ""
        lines = 1
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if not cur or text_width_in(trial, size_pt, path) <= usable:
                cur = trial
            else:
                lines += 1
                cur = word
        n += lines
    return n
