"""Document grid (Hebbia Matrix / AlphaSense Generative Grid style): rows are documents or entities, columns are
questions, every cell is answered only from that row's passages, with passage-level citations, and checked.

    from engines.research import library as R, grid as G
    c = R.Corpus(".")
    g = G.grid(c, {"Apple FY26 10-K": ["S1"], "Microsoft FY26 10-K": ["S2"]},
               ["FY2026 revenue", "Main risks to gross margin", "Capex guidance for next year"])
    G.to_excel(g, c, "grid.xlsx")                 # answers with [S1.p12] ids, links, and a Sources sheet of quotes

A cell's answer is accepted only if every passage it cites was given to it and every number in it appears in those
passages (scale-aware); otherwise the cell reads "Not found in the documents" and keeps the model's attempt in its
note. One small model call per cell, run in parallel.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .library import Corpus, parse_numbers, same_number
from .verify import CITE_RE, citations_in

NOT_FOUND = "Not found in the documents"
ASK = ("Answer the question using ONLY the passages below, each tagged with its id. Reply with JSON: "
       '{"answer": short answer (one or two sentences, or a number with its unit and period), "citations": '
       '[passage ids you used]}. Copy numbers exactly as the passages state them. If the passages do not answer '
       'the question, reply {"answer": "", "citations": []}.')


@dataclass
class Cell:
    answer: str
    citations: list[str]
    ok: bool
    note: str = ""


@dataclass
class Grid:
    rows: list[str]
    questions: list[str]
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)

    def get(self, i: int, j: int) -> Cell:
        return self.cells[(i, j)]


def check_cell(answer: str, cites: list[str], given: dict[str, str]) -> str | None:
    """Why an answer is rejected, or None when it is backed by the passages it was given."""
    if not answer.strip():
        return "no answer in the passages"
    bad = [c for c in cites if c not in given]
    if bad:
        return f"cites passages it was not given: {', '.join(bad)}"
    if not cites:
        return "no citation"
    src = [n for c in cites for n in parse_numbers(given[c], c, keep_years=True)]
    for n in parse_numbers(CITE_RE.sub(" ", answer)):
        if not n.unit and n.scale == 1 and n.decimals == 0 and abs(n.value) < 10:
            continue
        if not any(same_number(n, s) for s in src):
            return f"{n.raw} is not in the cited passages"
    return None


def _cell(client, model, question: str, passages: list) -> Cell:
    if not passages:
        return Cell(NOT_FOUND, [], False, "no relevant passage in this row's documents")
    given = {p.id: p.text for p in passages}
    body = "\n\n".join(f"[{p.id}] {p.text}" for p in passages)
    try:
        r = client.chat(model=model, messages=[{"role": "system", "content": ASK},
                                               {"role": "user", "content": f"Question: {question}\n\nPassages:\n{body}"}],
                        max_tokens=400, temperature=0.0, task_id="grid", role="worker",
                        extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or "{}"
        d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception as e:                                     # noqa: BLE001 - one failed cell does not stop the grid
        return Cell(NOT_FOUND, [], False, f"model call failed: {type(e).__name__}")
    answer = str(d.get("answer") or "").strip()
    cites = [str(c).strip("[] ") for c in d.get("citations") or []] or citations_in(answer)
    why = check_cell(answer, cites, given)
    if why:
        return Cell(NOT_FOUND, [], False, f"{why}; model wrote: {answer[:200]}" if answer else why)
    return Cell(CITE_RE.sub("", answer).strip(), cites, True)


def grid(corpus: Corpus, rows: dict[str, list[str]] | list[str], questions: list[str], client=None, model=None,
         k: int = 6, workers: int = 8) -> Grid:
    """rows: {label: [source ids]} or a list of source ids (one row each). Retrieval runs per cell inside the row's
    sources only, so an answer can never borrow another company's numbers."""
    if isinstance(rows, list):
        rows = {corpus.sources[s].label[:60] if s in corpus.sources else s: [s] for s in rows}
    if client is None:
        from meshpool_harness.core.client import LiveClient
        client = LiveClient("research_grid")
    import os
    model = model or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
    labels = list(rows)
    g = Grid(labels, list(questions))

    def run(ij):
        i, j = ij
        allowed = set(rows[labels[i]])
        hits = [p for p in corpus.find(questions[j], k=k * 4, per_source=k, include_duplicates=True)
                if p.source_id in allowed][:k]
        return ij, _cell(client, model, questions[j], hits)
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for ij, cell in ex.map(run, [(i, j) for i in range(len(labels)) for j in range(len(questions))]):
            g.cells[ij] = cell
    return g


def to_excel(g: Grid, corpus: Corpus, path: str) -> str:
    """Grid sheet (answers with their passage ids; a link to the first cited source; the quoted passage in a
    comment) and a Sources sheet listing every cited passage with its source, page and text."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Grid"
    head = PatternFill("solid", fgColor="1B2333")
    miss = PatternFill("solid", fgColor="F2F2F2")
    ws.cell(row=1, column=1, value="Document / question").font = Font(bold=True, color="FFFFFF")
    ws.cell(row=1, column=1).fill = head
    for j, q in enumerate(g.questions, start=2):
        c = ws.cell(row=1, column=j, value=q)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[c.column_letter].width = 42
    ws.column_dimensions["A"].width = 34
    cited = []
    for i, lab in enumerate(g.rows, start=2):
        ws.cell(row=i, column=1, value=lab).font = Font(bold=True)
        for j in range(2, len(g.questions) + 2):
            cell = g.get(i - 2, j - 2)
            txt = cell.answer + (" " + "".join(f"[{c}]" for c in cell.citations) if cell.citations else "")
            x = ws.cell(row=i, column=j, value=txt)
            x.alignment = Alignment(wrap_text=True, vertical="top")
            if not cell.ok:
                x.fill = miss
                x.font = Font(italic=True, color="7F7F7F")
                if cell.note:
                    x.comment = Comment(cell.note[:500], "Meshpool grid")
                continue
            first = corpus.passage(cell.citations[0])
            src = corpus.sources.get(first.source_id) if first else None
            if src and src.url:
                x.hyperlink = src.url
            x.comment = Comment("\n\n".join(f"[{c}] {corpus.passage(c).text[:400]}" for c in cell.citations
                                            if corpus.passage(c))[:1500], "Meshpool grid")
            cited += [c for c in cell.citations if c not in cited]
        ws.row_dimensions[i].height = 75
    ws.freeze_panes = "B2"
    S = wb.create_sheet("Sources")
    for j, h in enumerate(["Passage", "Source", "Page", "URL", "Text"], start=1):
        c = S.cell(row=1, column=j, value=h)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head
    for r, pid in enumerate(cited, start=2):
        p = corpus.passage(pid)
        s = corpus.sources[p.source_id]
        for j, v in enumerate([pid, s.citation(), p.page, s.url, p.text], start=1):
            S.cell(row=r, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABCDE", (12, 60, 8, 50, 100)):
        S.column_dimensions[col].width = w
    wb.save(path)
    return path
