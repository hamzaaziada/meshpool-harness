"""Workbooks print one page wide in landscape; numbers in Word tables align right."""
import os

from docx import Document
from openpyxl import load_workbook

from engines.sheets import builder as B
from meshpool_harness.agent import office_mcp_server as O


def test_workbooks_print_one_page_wide(tmp_path):
    wb = B.new_workbook()
    ws = wb["Calc"]
    ws["A1"], ws["B6"] = "Model", 1
    ws.freeze_panes = "B4"
    wb.save(tmp_path / "m.xlsx")
    w = load_workbook(tmp_path / "m.xlsx")["Calc"]
    assert w.page_setup.orientation == "landscape"
    assert w.sheet_properties.pageSetUpPr.fitToPage and w.page_setup.fitToWidth == 1 and w.page_setup.fitToHeight == 0
    assert w.print_title_rows == "$1:$3"                  # the rows above the frozen pane repeat on every page


def test_word_tables_align_numbers_right(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    md = ("| Company | EV ($m) | EV/EBITDA | Note |\n|---|---|---|---|\n| Acme | 1,234 | 12.5x | leader |\n"
          "| Beta | (45) | NM | loss-making |\n")
    O.make_document(markdown=md, filename="t.docx", _clarity=False)
    path = next(os.path.join(r, f) for r, _, fs in os.walk(tmp_path) for f in fs if f == "t.docx")
    rows = Document(path).tables[0].rows
    align = [str(c.paragraphs[0].alignment) for c in rows[1].cells]
    assert align[0] == "None" and align[3] == "None"      # text stays left
    assert "RIGHT" in align[1] and "RIGHT" in align[2]
