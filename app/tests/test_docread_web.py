"""Document reading and page fetching regressions: number precision, PDF text layers, CSV and Excel edge cases,
content-type extensions, hostile HTML and fetches to local addresses."""
import io
import re
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import docread as D, web as W  # noqa: E402


def test_floats_keep_full_precision():
    assert D._num(15432198.75) == "15432198.75" and D._num(3.0) == "3" and D._num(0.1 + 0.2) == "0.3"
    assert D._num(None) == "" and D._num(7) == "7"


def _xlsx(tmp_path, drop_dimension=False):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Revenue", 15432198.75])
    ws.append(["Margin", 0.123456789])
    p = tmp_path / "book.xlsx"
    wb.save(p)
    if drop_dimension:                          # some exporters write no <dimension>: read-only max_row is None
        src = zipfile.ZipFile(p).infolist()
        data = {i.filename: zipfile.ZipFile(p).read(i.filename) for i in src}
        data["xl/worksheets/sheet1.xml"] = re.sub(rb"<dimension[^>]*/>", b"", data["xl/worksheets/sheet1.xml"])
        with zipfile.ZipFile(p, "w") as z:
            for k, v in data.items():
                z.writestr(k, v)
    return p


def test_xlsx_values_and_sheets_without_dimension(tmp_path):
    out = D.extract(_xlsx(tmp_path))
    assert "15432198.75" in out and "0.123456789" in out and "1.54322e+07" not in out
    out = D.extract(_xlsx(tmp_path, drop_dimension=True))
    assert "15432198.75" in out and "Could not read" not in out


def test_single_column_csv_and_big_files(tmp_path):
    p = tmp_path / "names.csv"
    p.write_text("name\nalice\nbob\n")
    out = D.extract(p)
    assert "Could not read" not in out and "| alice |" in out and "3 rows x 1 columns" in out


def _pdf(tmp_path, text):
    from reportlab.pdfgen import canvas
    p = tmp_path / "short.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 720, text)
    c.save()
    return p


def test_pdf_keeps_a_short_text_layer_without_ocr(tmp_path, monkeypatch):
    from meshpool_harness import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)
    p = _pdf(tmp_path, "Total due 42")                     # under 50 characters: used to be blanked for OCR
    for mode in ("auto", "always"):
        out = D.extract(p, ocr=mode)
        assert "Total due 42" in out and "looks scanned" not in out and "no text layer" not in out


def test_pdf_always_mode_merges_ocr_with_the_text_layer(tmp_path, monkeypatch):
    from meshpool_harness import ocr
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "pdf_page_images", lambda path, pages: [(i, object()) for i in pages])
    monkeypatch.setattr(ocr, "describe", lambda img, name, lang="auto": f"Text in {name} (OCR):\nStamp: PAID")
    out = D.extract(_pdf(tmp_path, "Invoice 7 for consulting services rendered in October"), ocr="always")
    assert "Invoice 7" in out and "Stamp: PAID" in out


def test_document_extension_from_content_type():
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert W._doc_ext(docx, b"PK\x03\x04", "/download") == ".docx"
    assert W._doc_ext("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK", "/x") == ".xlsx"
    assert W._doc_ext("application/octet-stream", b"%PDF-1.7", "/file") == ".pdf"
    assert W._doc_ext("application/vnd.ms-excel", b"a,b", "/data.csv") == ".csv"   # the link's extension wins
    assert W._doc_ext("application/octet-stream", b"", "/x") == ".bin"


def test_meta_tags_parse_quickly_on_hostile_pages():
    page = '<meta property="og:title" content="Hello &amp; you"><META NAME=description CONTENT="d">'
    assert W._metas(page) == {"og:title": "Hello & you", "description": "d"}
    for bad in ('<html><head><meta name="x" ' + "a" * 300_000 + "</head><body>hi</body></html>",
                "<meta " * 100_000, '<p>x</p>' + "<b" * 200_000):
        t = time.time()
        W.html_to_text(bad)
        assert time.time() - t < 3


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                                 "http://10.0.0.1/", "http://192.168.1.1/", "http://[::1]/", "http://0.0.0.0/"])
def test_fetch_refuses_local_and_private_addresses(url, monkeypatch):
    monkeypatch.delenv("MH_ALLOW_PRIVATE_FETCH", raising=False)
    with pytest.raises(W.WebError, match="refused"):
        W.fetch(url, refresh=True)


def test_redirects_to_private_addresses_are_refused(monkeypatch):
    import urllib.request
    monkeypatch.delenv("MH_ALLOW_PRIVATE_FETCH", raising=False)
    req = urllib.request.Request("https://example.com/")
    with pytest.raises(W.WebError, match="refused"):
        W._PublicRedirects().redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")


def test_private_fetch_opt_in(monkeypatch):
    monkeypatch.setenv("MH_ALLOW_PRIVATE_FETCH", "1")
    W.check_public("http://127.0.0.1:9/")                 # no error


def test_bounded_decompression():
    import gzip
    bomb = gzip.compress(b"\0" * 5_000_000)
    assert len(W._inflate(bomb, 16 + 15, 1000)) == 1000
    assert W._inflate(gzip.compress(b"hello")[:-6], 16 + 15, 1000).startswith(b"hel")   # cut off: no EOFError
