"""Meshpool Office: an MCP server that gives any harness (Hermes, the built-in agents, other ACP agents) real
PowerPoint, Excel and Word output, using Meshpool's benchmarked office engines.

Tools
  make_deck(request, files?, brief)   -> .pptx built and checked by the slides engine (or BRIEF NEEDED questions)
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
  find_icon(query, set?, limit?)      -> bundled outline icons (Lucide, Tabler) that fit a meaning, with SVG paths

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
from pathlib import Path

from meshpool_harness.agent import office_jobs as OJ

APP = OJ.APP
JOBS = OJ.JOBS                              # one job table and build count for the server (shared runner: office_jobs)
REBUILD_LIMIT = OJ.REBUILD_LIMIT
WAIT = float(os.environ.get("OFFICE_WAIT_S", "100"))   # under the common 120 s client timeout; a longer job returns
                                                       # a job id for office_job_status instead of timing out


def _project() -> Path:
    p = Path(os.environ.get("MESHPOOL_PROJECT") or os.getcwd()).expanduser()
    return p if p.is_dir() else Path.cwd()


def _resolve(f: str) -> Path:
    p = Path(f).expanduser()
    return p if p.is_absolute() else (_project() / p)


def _run_job(jid: str):
    OJ.run(jid)


def _report(jid: str) -> tuple[str, bool]:
    return OJ.report(jid)


def _engine(engine: str, request: str, files: list | None) -> tuple[str, bool]:
    paths, missing = OJ.resolve_files(_project(), files)
    if missing:
        return f"Input file not found: {missing}", True
    jid = OJ.new_job(engine, request, paths, _project())
    t = threading.Thread(target=_run_job, args=(jid,), daemon=True)
    t.start()
    t.join(WAIT)
    return _report(jid)


def make_deck(request: str, files: list | None = None, audience: str | None = None, purpose: str | None = None,
              density: str | None = None, slides: int | None = None, theme: str | None = None,
              opening: str | None = None, closing: str | None = None, sources: str | None = None,
              brief_ok: bool = False):
    """Settle the brief first (engines/forge/brief.py): if the request and fields leave audience, density, length
    or look open, return the questions (BRIEF NEEDED) instead of building. brief_ok, "use defaults" in the request,
    or a headless run (MH_ASK_AUTO=1) builds with the recommended answers."""
    fields = {k: v for k, v in dict(audience=audience, purpose=purpose, density=density, slides=slides, theme=theme,
                                    opening=opening, closing=closing, sources=sources).items() if v not in (None, "")}
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        from engines.forge import brief as BR
    except Exception:  # noqa: BLE001  engines not shipped: build from the request as it is
        return _engine("slides", request, files)
    qs = BR.questions(request, files, fields)
    if qs and not (brief_ok or BR.wants_defaults(request) or os.environ.get("MH_ASK_AUTO") == "1"):
        return BR.needed_text(qs, "make_deck"), False
    return _engine("slides", BR.complete(request, files, fields), files)


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


def make_document(markdown: str, filename: str = "document.docx", title: str | None = None, _clarity: bool = True):
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
    if _clarity and os.environ.get("MH_CLARITY", "1") != "0":   # a second, smaller model reads the prose for clarity;
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
        text = src.read_text(encoding="utf-8", errors="replace")      # a conversion keeps the words as they are:
        if to == "pdf":                                                # no clarity rewrite
            return make_pdf(text, src.stem + ".pdf")
        return make_document(text, src.stem + ".docx", _clarity=False)
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


def find_icon(query: str, set: str | None = None, limit: int = 8):
    """Icons from the bundled outline sets that fit a meaning, best first (read-only)."""
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    from engines import icons as IC
    try:
        rows = IC.search(query, set=set or None, limit=max(1, min(int(limit or 8), 40)))
    except ValueError as e:
        return str(e), True
    if not rows:
        return f"No icon matches {query!r}. Try a simpler word (money, growth, risk, people, time).", False
    lines = [f"{r['set']}:{r['name']}  score {r['score']}  {r['path']}" + (f"  tags: {', '.join(r['tags'][:5])}"
             if r["tags"] else "") for r in rows]
    return ("Use one set for the whole deck. Best matches (set:name, score, SVG path):\n" + "\n".join(lines)), False


S = {"type": "string"}
FILES = {"type": "array", "items": S, "description": "Input files, relative to the project folder or absolute"}
TOOLS = {
    "make_deck": (make_deck, "Build a finished PowerPoint deck (.pptx) with Meshpool's slides engine, which plans "
                  "the storyline, lays out charts and checks the result. Describe the content in `request` and give "
                  "the brief as fields (audience, purpose, density, slides, theme, opening, closing, sources). If "
                  "audience, density, length or look is left open, it does not build: it answers BRIEF NEEDED with "
                  "questions, options and a recommendation for each. Ask the user, then call again with the answers, "
                  "or pass brief_ok=true to build with the recommendations. Takes 2 to 6 minutes.",
                  {"request": S, "files": FILES,
                   "audience": {"type": "string", "description": "Who the deck is for, e.g. the board, investors"},
                   "purpose": {"type": "string", "description": "What it should achieve, e.g. approve the budget"},
                   "density": {"type": "string", "enum": ["present", "balanced", "read"],
                               "description": "present: visual, few words, to present live; balanced; read: a dense "
                                              "pre-read"},
                   "slides": {"type": "integer", "description": "Number of slides"},
                   "theme": {"type": "string", "description": "navy, ocean, forest, burgundy, graphite or mono"},
                   "opening": {"type": "string", "description": "Opening slides, e.g. title and agenda"},
                   "closing": {"type": "string", "description": "Closing slides, e.g. next steps, questions"},
                   "sources": {"type": "string", "description": "Sources to use or leave out"},
                   "brief_ok": {"type": "boolean", "description": "Build now: recommended answers for anything not "
                                                                  "given"}}, ["request"]),
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
    "find_icon": (find_icon, "Find icons for a slide or document by meaning (e.g. \"money\", \"supply chain\", "
                  "\"risk\"). Searches the bundled outline sets, Lucide (ISC) and Tabler (MIT), by name, tags and "
                  "synonyms, and returns set:name and the SVG path, best first. `set` (lucide or tabler) keeps a "
                  "deck to one set; `limit` is the number of results (default 8).",
                  {"query": S, "set": S, "limit": {"type": "integer"}}, ["query"]),
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


def _as_mcp(fn, props: dict):
    """An office tool (returns (text, failed)) as an mcp_stdio tool (returns text, raises on failure). Made-up
    arguments not in the schema (seen: engine=) are dropped with a note instead of failing the call."""
    from meshpool_harness.agent.mcp_stdio import ToolFailed

    def call(**args):
        extra = sorted(k for k in args if k not in props) if props else []
        text, err = fn(**{k: v for k, v in args.items() if k not in extra})
        if extra:
            text = f"(Ignored unknown argument(s): {', '.join(extra)}.)\n{text}"
        if err:
            raise ToolFailed(text)
        return text
    return call


def tools() -> dict:
    from meshpool_harness.agent.mcp_stdio import Tool
    return {k: Tool(_as_mcp(fn, p or {}), d, p, r) for k, (fn, d, p, r) in TOOLS.items()}


def handle(msg: dict, wait_s: float = 900) -> dict | None:
    """One JSON-RPC request -> the reply mcp_stdio.serve gives it (tests and in-process callers)."""
    import io
    from meshpool_harness.agent import mcp_stdio
    if msg.get("id") is None:
        return None
    out, old_in, old_out = io.StringIO(), sys.stdin, sys.stdout
    try:
        sys.stdin, sys.stdout = io.StringIO(json.dumps(msg) + "\n"), out
        mcp_stdio.serve("meshpool-office", tools())
        end = time.time() + wait_s                  # serve answers on its own thread
        while "\n" not in out.getvalue() and time.time() < end:
            time.sleep(0.01)
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    return json.loads(lines[0]) if lines else None


def main():
    sys.path.insert(0, str(APP))
    try:
        from meshpool_harness import config as C
        C.load_into_env()                    # the Meshpool key and model, for the engines
    except Exception:
        pass
    from meshpool_harness.agent import mcp_stdio
    mcp_stdio.serve("meshpool-office", tools())


if __name__ == "__main__":
    main()
