"""Meshpool Office: an MCP server that gives any harness (Hermes, the built-in agents, other ACP agents) real
PowerPoint, Excel and Word output, using Meshpool's benchmarked office engines.

Tools
  make_deck(request, files?)          -> .pptx built and checked by the slides engine
  make_spreadsheet(request, files?)   -> .xlsx with live formulas, checked by the sheets engine
  analyze_data(request, files)        -> charts, tables and a written analysis from data files
  make_document(markdown, filename)   -> .docx from Markdown (headings, lists, tables, bold, code)
  make_pdf(markdown, filename)        -> .pdf from Markdown
  convert_document(path, to)          -> docx/pptx/xlsx to PDF and back, through LibreOffice when installed
  read_document(path, sheet?, pages?) -> text of a PDF, Word, PowerPoint, Excel, CSV or Power BI file
  office_job_status(job_id)           -> result of a job that was still running
  check_deck(path, slide_count?)      -> the forge deck rules (P0-P19), each failure as a fix instruction
  check_workbook(path)                -> the forge workbook rules (X0-X13) after a headless recalculation
  check_document(path)                -> does a .docx/.pptx/.xlsx open in Office without a repair prompt
  measure_office_file(path, pages?)   -> how a .pptx/.xlsx/.pdf is built (reverse-engineering a reference file)

Engine jobs run in the background. A call waits up to OFFICE_WAIT_S (default 100 s, under the common 120 s MCP
timeout; Meshpool's own client sets 280 s with a 330 s timeout); a longer job returns a job id to check with
office_job_status. Files go to "<project>/Meshpool outputs".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
JOBS: dict[str, dict] = {}
WAIT = float(os.environ.get("OFFICE_WAIT_S", "100"))   # under the common 120 s client timeout; a longer job returns
                                                       # a job id for office_job_status instead of timing out


def _project() -> Path:
    p = Path(os.environ.get("MESHPOOL_PROJECT") or os.getcwd()).expanduser()
    return p if p.is_dir() else Path.cwd()


def _resolve(f: str) -> Path:
    p = Path(f).expanduser()
    return p if p.is_absolute() else (_project() / p)


def _run_job(jid: str):
    j = JOBS[jid]
    env = dict(os.environ, PYTHONPATH=str(APP), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("MH_EVENT_LOG", None)
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(j["cmd"], cwd=str(j["out"]), env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=float(os.environ.get("OFFICE_JOB_TIMEOUT_S", "1800")), **kw)
        out = r.stdout
        res = json.loads(out[out.find("{"):]) if "{" in out else {}
        j["result"], j["stderr"] = res, r.stderr[-1500:]
    except subprocess.TimeoutExpired:
        j["result"], j["stderr"] = {}, "The engine took longer than 30 minutes and was stopped."
    except Exception as e:  # report, never crash the server
        j["result"], j["stderr"] = {}, f"{type(e).__name__}: {e}"
    j["done"] = time.time()


def _report(jid: str) -> tuple[str, bool]:
    j = JOBS[jid]
    if not j.get("done"):
        mins = int((time.time() - j["started"]) // 60)
        return (f"Still working on the {j['engine']} job ({mins} min so far). Job id: {jid}. "
                f"Call office_job_status with this id to get the result."), False
    out: Path = j["out"]
    made = [str(p) for p in sorted(out.rglob("*"))
            if p.is_file() and not any(x.startswith(".") for x in p.relative_to(out).parts)]
    res = j.get("result") or {}
    if not res:
        return f"The {j['engine']} engine failed. {j.get('stderr', '')}", True
    notes = "\n".join((s.get("answer") or "") for s in res.get("steps") or []).strip()
    checks = "passed" if res.get("ok") else "did not all pass (review the file)"
    lines = [f"The {j['engine']} engine finished; its quality checks {checks}.", "Files:"] + [f"- {m}" for m in made]
    if notes:
        lines.append("Engine notes: " + notes[:2500])
    try:                                    # forge QA on every produced deck/workbook (never fails the job)
        from engines.forge.tools import qa_summary
        qa = qa_summary([m for m in made if m.endswith((".pptx", ".xlsx"))])
        if qa:
            lines += ["Forge QA:", qa]
    except Exception:
        pass
    return "\n".join(lines), not made


def _engine(engine: str, request: str, files: list | None) -> tuple[str, bool]:
    paths = []
    for f in files or []:
        p = _resolve(f)
        if not p.exists():
            return f"Input file not found: {f}", True
        paths.append(str(p))
    jid = uuid.uuid4().hex[:8]
    out = _project() / "Meshpool outputs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{engine}"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "meshpool_harness", "run", request, "--engine", engine, "--out", str(out)]
    if paths:
        cmd += ["--files", *paths]
    JOBS[jid] = {"engine": engine, "cmd": cmd, "out": out, "started": time.time()}
    t = threading.Thread(target=_run_job, args=(jid,), daemon=True)
    t.start()
    t.join(WAIT)
    return _report(jid)


def make_deck(request: str, files: list | None = None):
    return _engine("slides", request, files)


def make_spreadsheet(request: str, files: list | None = None):
    return _engine("sheets", request, files)


def analyze_data(request: str, files: list | None = None):
    return _engine("data", request, files)


def office_job_status(job_id: str):
    if job_id not in JOBS:
        return f"No job with id {job_id} (jobs are kept while this server runs).", True
    j = JOBS[job_id]
    if not j.get("done"):
        deadline = time.time() + WAIT
        while not j.get("done") and time.time() < deadline:
            time.sleep(1)
    return _report(job_id)


# ---------- Markdown -> .docx ------------------------------------------------------------------------------------

def _runs(par, text: str):
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        else:
            par.add_run(part)


def make_document(markdown: str, filename: str = "document.docx", title: str | None = None):
    from docx import Document
    from docx.shared import Pt
    name = re.sub(r"[^\w .-]", "_", Path(filename).name).strip() or "document.docx"
    if not name.lower().endswith(".docx"):
        name += ".docx"
    out = _project() / "Meshpool outputs"
    out.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    if title:
        doc.add_heading(title, level=0)
    clarity_note = ""
    if os.environ.get("MH_CLARITY", "1") != "0":          # a second, smaller model reads the prose for clarity;
        try:                                               # numbers and facts are guarded, failures never block
            if str(APP) not in sys.path:
                sys.path.insert(0, str(APP))
            from engines.forge.clarity import review_markdown
            from meshpool_harness.core.client import LiveClient
            markdown, rep = review_markdown(markdown, LiveClient("clarity_doc"),
                                            main_model=os.environ.get("MH_MODEL"), task_id="clarity-doc")
            if rep.get("model"):
                clarity_note = (f" Clarity check ({rep['model']}): {rep['changed']} of {rep['checked']} lines made "
                                "clearer, numbers unchanged.")
        except Exception:
            pass
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            p = doc.add_paragraph()
            r = p.add_run("\n".join(block))
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{3,}", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            cols = max(len(r) for r in rows)
            t = doc.add_table(rows=len(rows), cols=cols)
            t.style = "Table Grid"
            for ri, row in enumerate(rows):
                for ci in range(cols):
                    cell = t.cell(ri, ci)
                    cell.text = ""
                    _runs(cell.paragraphs[0], row[ci] if ci < len(row) else "")
                    if ri == 0:
                        for r in cell.paragraphs[0].runs:
                            r.bold = True
        elif m := re.match(r"^(#{1,6})\s+(.*)", s):
            doc.add_heading(m.group(2).strip(), level=min(len(m.group(1)), 4))
        elif m := re.match(r"^[-*+]\s+(.*)", s):
            _runs(doc.add_paragraph(style="List Bullet"), m.group(1))
        elif m := re.match(r"^\d+[.)]\s+(.*)", s):
            _runs(doc.add_paragraph(style="List Number"), m.group(1))
        elif s.startswith(">"):
            _runs(doc.add_paragraph(style="Quote"), s.lstrip("> "))
        elif s in ("---", "***"):
            doc.add_paragraph()
        elif s:
            _runs(doc.add_paragraph(), s)
        i += 1
    path = out / name
    doc.save(path)
    return f"Saved the Word document: {path}.{clarity_note}", False


def read_document(path: str, sheet: str | None = None, pages: str | None = None, language: str | None = None,
                  ocr: str | None = None):
    from meshpool_harness import docread
    p = _resolve(path)
    if not p.exists():
        return f"File not found: {p}", True
    return docread.extract(p, sheet=sheet, pages=pages, lang=language or "auto", ocr=ocr or "auto"), False


def _md_inline(s: str) -> str:
    from xml.sax.saxutils import escape
    s = escape(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", s)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<link href='\2' color='#1A56DB'>\1</link>", s)


def make_pdf(markdown: str, filename: str = "document.pdf", title: str | None = None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle
    name = re.sub(r"[^\w .-]", "_", Path(filename).name).strip() or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    out = _project() / "Meshpool outputs"
    out.mkdir(parents=True, exist_ok=True)
    st = getSampleStyleSheet()
    flow = [Paragraph(_md_inline(title), st["Title"])] if title else []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i, bullets = 0, []

    def flush():
        if bullets:
            flow.append(ListFlowable([ListItem(Paragraph(_md_inline(b), st["BodyText"])) for b in bullets],
                                     bulletType="bullet", leftIndent=14))
            bullets.clear()
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            flow.append(Preformatted("\n".join(block), st["Code"]))
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{3,}", lines[i]):
                    rows.append([Paragraph(_md_inline(c.strip()), st["BodyText"]) for c in lines[i].strip().strip("|").split("|")])
                i += 1
            w = max(len(r) for r in rows)
            rows = [r + [""] * (w - len(r)) for r in rows]
            t = Table(rows, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3FA")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            flow += [t, Spacer(1, 8)]
            continue
        elif re.match(r"^#{1,6}\s", s):
            flush()
            lvl = len(s) - len(s.lstrip("#"))
            flow.append(Paragraph(_md_inline(s[lvl:].strip()), st["Heading%d" % min(lvl, 4)]))
        elif re.match(r"^([-*+]|\d+[.)])\s", s):
            bullets.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", s))
        elif s.startswith(">"):
            flush()
            flow.append(Paragraph("<i>" + _md_inline(s.lstrip("> ")) + "</i>", st["BodyText"]))
        elif s:
            flush()
            para = [s]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|[-*+]\s|\d+[.)]\s|\||```|>)", lines[i + 1].strip()):
                i += 1
                para.append(lines[i].strip())
            flow.append(Paragraph(_md_inline(" ".join(para)), st["BodyText"]))
        else:
            flush()
        i += 1
    flush()
    path = out / name
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                      bottomMargin=2 * cm, title=title or name).build(flow)
    return f"Saved the PDF: {path}", False


def _soffice() -> str | None:
    import shutil
    for c in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe"):
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def convert_document(path: str, to: str = "pdf"):
    src = _resolve(path)
    if not src.exists():
        return f"File not found: {src}", True
    to = to.lower().lstrip(".")
    if src.suffix.lower() in (".md", ".txt") and to in ("pdf", "docx"):
        text = src.read_text(encoding="utf-8", errors="replace")
        return (make_pdf if to == "pdf" else make_document)(text, src.stem + "." + to)
    exe = _soffice()
    if not exe:
        return ("Converting Office files needs LibreOffice, which is not installed. Install it from libreoffice.org, "
                "or build the file directly with make_pdf, make_document, make_deck or make_spreadsheet."), True
    out = _project() / "Meshpool outputs"
    out.mkdir(parents=True, exist_ok=True)
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    r = subprocess.run([exe, "--headless", "--convert-to", to, "--outdir", str(out), str(src)], capture_output=True,
                       text=True, timeout=300, **kw)
    dst = out / (src.stem + "." + to)
    if r.returncode != 0 or not dst.exists():
        return f"LibreOffice could not convert {src.name}: {(r.stderr or r.stdout)[-500:]}", True
    return f"Saved: {dst}", False


S = {"type": "string"}
FILES = {"type": "array", "items": S, "description": "Input files, relative to the project folder or absolute"}
TOOLS = {
    "make_deck": (make_deck, "Build a finished PowerPoint deck (.pptx) with Meshpool's slides engine, which plans "
                  "the storyline, lays out charts and checks the result. Describe the audience, slide count and "
                  "content in `request`. Takes 2 to 6 minutes.", {"request": S, "files": FILES}, ["request"]),
    "make_spreadsheet": (make_spreadsheet, "Build a finished Excel workbook (.xlsx) with live formulas, such as a "
                         "model, budget or tracker, with Meshpool's sheets engine (it recalculates and checks the "
                         "formulas). Takes 2 to 6 minutes.", {"request": S, "files": FILES}, ["request"]),
    "analyze_data": (analyze_data, "Analyze CSV or Excel data and produce charts, tables and a written summary "
                     "with Meshpool's data engine.", {"request": S, "files": FILES}, ["request", "files"]),
    "make_document": (make_document, "Save Markdown as a Word document (.docx). Supports headings, bullet and "
                      "numbered lists, tables, bold, italic, quotes and code blocks.",
                      {"markdown": S, "filename": S, "title": S}, ["markdown"]),
    "read_document": (read_document, "Read a PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls), CSV, Power BI "
                      "(.pbix, .pbit: pages, visuals, tables, DAX measures, Power Query and sample rows), OpenDocument, "
                      "notebook, email or HTML file as text, and reads the text in images and scanned PDFs with OCR "
                      "(Arabic, Chinese, English, Japanese, Korean, Cyrillic, Hindi, Thai, Greek and Latin-script "
                      "languages). `sheet` picks one Excel sheet; `pages` picks PDF pages such as \"1-3,7\"; "
                      "`language` (optional, e.g. ar, ru, ko, hi) forces an OCR language; `ocr` is auto (scanned pages and "
                      "pictures), always (also pages that mix text and scans) or never.",
                      {"path": S, "sheet": S, "pages": S, "language": S, "ocr": S}, ["path"]),
    "make_pdf": (make_pdf, "Save Markdown as a PDF (headings, paragraphs, lists, tables, code, links).",
                 {"markdown": S, "filename": S, "title": S}, ["markdown"]),
    "convert_document": (convert_document, "Convert a document to another format, for example .docx, .pptx or .xlsx "
                         "to PDF, or Markdown to PDF or Word. `to` is the target extension.",
                         {"path": S, "to": S}, ["path", "to"]),
    "office_job_status": (office_job_status, "Get the result of an office job that was still running.",
                          {"job_id": S}, ["job_id"]),
}


def _forge_tool(name):
    def call(path: str, **kw):
        from engines.forge import tools as FT
        return getattr(FT, name)(str(_resolve(path)), **kw)
    return call


try:                                        # forge checks and measurement (engines/forge), when the engines ship
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    from engines.forge.tools import TOOLS as _FORGE
    for _k, (_fn, _d, _p, _r) in _FORGE.items():
        TOOLS[_k] = (_forge_tool(_k), _d, _p, _r)
except Exception:
    pass


def handle(msg: dict) -> dict | None:
    m, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    if m == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        res = {"protocolVersion": ver, "capabilities": {"tools": {}},
               "serverInfo": {"name": "meshpool-office", "version": "1.0.0"}}
    elif m == "ping":
        res = {}
    elif m == "tools/list":
        res = {"tools": [{"name": k, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                         for k, (_, d, p, r) in TOOLS.items()]}
    elif m == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            text, err = TOOLS[name][0](**args)
        except Exception as e:
            text, err = f"{type(e).__name__}: {e}", True
        res = {"content": [{"type": "text", "text": text}], "isError": bool(err)}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {m}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": res}


def main():
    sys.path.insert(0, str(APP))
    try:
        from meshpool_harness import config as C
        C.load_into_env()                    # the Meshpool key and model, for the engines
    except Exception:
        pass
    lock = threading.Lock()

    def serve(line):
        try:
            out = handle(json.loads(line))
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if out is not None:
            with lock:
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
    for line in sys.stdin:
        if line.strip():
            threading.Thread(target=serve, args=(line.strip(),), daemon=True).start()


if __name__ == "__main__":
    main()
