"""Forge tools for Meshpool Work (Cowork): exposed through the meshpool-office MCP server.

check_deck(path, slide_count?)   -> P0-P19 report with repair messages (P0: opens in PowerPoint without repair)
check_workbook(path)             -> X0-X13 report after a headless recalculation (X0: opens in Excel without repair)
check_document(path)             -> does a .docx/.pptx/.xlsx open in Office without a repair prompt
measure_office_file(path)        -> anatomy of a .pptx, .xlsx or .pdf (how it is built), with evidence IDs
Each returns (text, is_error). The text is short enough for a model to act on. The tools only read: they never
write next to the user's files, so the agent can run them without asking.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)


def _sidecar(path: Path, name: str):
    p = path.with_name(f"{path.stem}.{name}.json")
    if not p.exists():
        p = path.parent / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def check_deck(path: str, slide_count: int | None = None, render: bool = True) -> tuple[str, bool]:
    from .validate_pptx import report_text, validate
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    brief = {"slide_count": int(slide_count)} if slide_count else _sidecar(p, "brief")
    res = validate(str(p), brief=brief, provenance=_sidecar(p, "provenance"), storyline=_sidecar(p, "storyline"),
                   render=render)
    return report_text(res, 30), False


def check_workbook(path: str) -> tuple[str, bool]:
    from .validate_xlsx import report_text, validate
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    res = validate(str(p), recompute=_sidecar(p, "recompute"))
    return report_text(res, 30), False


def check_document(path: str) -> tuple[str, bool]:
    from .ooxml_check import check
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    r = check(str(p))
    if r["ok"]:
        how = "schema-valid and" if r["schema_validation"] == "run" else "package checks only (schemas not installed);"
        return f"{p.name}: {how} no problems found in {r['parts_validated']} parts; Office should open it without repair.", False
    lines = [f"{p.name}: {len(r['problems'])} problem(s) that would make Office offer to repair the file:"]
    lines += [f"- {x['part']} ({x['check']}): {x['detail']}" for x in r["problems"][:20]]
    return "\n".join(lines), False


def measure_office_file(path: str, pages: str | None = None) -> tuple[str, bool]:
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}", True
    ext = p.suffix.lower()
    if ext in (".pptx", ".potx"):
        from .measure_pptx import measure, public
        summary = public(measure(str(p)))["summary"]
    elif ext in (".xlsx", ".xlsm"):
        from .validate_xlsx import validate
        summary = validate(str(p))["stats"]
    elif ext == ".pdf":
        from .measure_pdf import measure
        summary = measure(str(p), pages)["summary"]
    else:
        return "measure_office_file reads .pptx, .potx, .xlsx, .xlsm or .pdf.", True
    return json.dumps(summary, indent=1, default=str)[:8000], False


def qa_summary(files: list[str]) -> str:
    """One line per produced deck/workbook, for appending to an engine job's report."""
    lines = []
    for f in files:
        try:
            if f.endswith(".pptx"):
                from .validate_pptx import validate
                r = validate(f, render=False)
            elif f.endswith(".xlsx"):
                from .validate_xlsx import validate
                r = validate(f)
            elif f.endswith(".docx"):
                from .ooxml_check import check
                d = check(f)
                lines.append(f"- {Path(f).name}: " + ("opens without repair" if d["ok"] else
                             f"{len(d['problems'])} problem(s) Office would repair (run check_document)"))
                continue
            else:
                continue
            bad = [k for k, v in r["rules"].items() if v == "fail"]
            lines.append(f"- {Path(f).name}: forge rules failing: {', '.join(bad) or 'none'} "
                         f"(run check_deck/check_workbook for the repair list)")
        except Exception as e:  # QA must never break a finished job
            lines.append(f"- {Path(f).name}: forge QA could not run ({type(e).__name__})")
    return "\n".join(lines)


S = {"type": "string"}
TOOLS = {
    "check_deck": (check_deck, "Check a PowerPoint deck (.pptx) against Meshpool's 20 consulting-quality rules: opens "
                   "in PowerPoint without a repair prompt (every XML part against the Office schemas), native "
                   "charts and tables, theme fonts and colours, text fit, grid, density, action titles, storyline, "
                   "sourced numbers, text matching the charts, archetypes, slide count, editability, alt text. Returns "
                   "each failure as a fix instruction (e.g. 'Slide 4, Body 2: text overflows by 3 lines at 14pt; cut "
                   "to 45 words'). `slide_count` is the count the brief asked for.",
                   {"path": S, "slide_count": {"type": "integer"}}, ["path"]),
    "check_workbook": (check_workbook, "Check an Excel workbook (.xlsx) against Meshpool's 14 financial-model rules "
                       "after recalculating it: opens in Excel without a repair prompt, no hardcoded numbers in calculations, no numbers buried in formulas, no "
                       "balancing plugs, historicals tie, no #REF!/#NAME? errors, no circular references, consistent "
                       "formulas across periods, formatting conventions, a checks sheet with a master flag, no external "
                       "links or macros.", {"path": S}, ["path"]),
    "check_document": (check_document, "Check that a Word, PowerPoint or Excel file (.docx/.pptx/.xlsx) opens in "
                       "Microsoft Office without the 'found a problem with content ... repair' prompt: every XML part "
                       "against the Office schemas, relationships, content types and shape ids. Returns each problem "
                       "with the part it is in.", {"path": S}, ["path"]),
    "measure_office_file": (measure_office_file, "Measure how a .pptx, .xlsx or .pdf deck is built: generator, masters "
                            "and layouts, theme colours vs hardcoded colours, fonts and type scale, grid, native charts "
                            "and tables, text density, title style, source lines (decks); formulas vs constants, links, "
                            "names, cycles, checks (workbooks). Use it to reverse-engineer a reference file.",
                            {"path": S, "pages": S}, ["path"]),
}
