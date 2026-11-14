"""Read the files people work with as text an agent can use: PDF, Word, PowerPoint, Excel, CSV, Power BI (.pbix,
.pbit), OpenDocument, notebooks, email, HTML and more. Code and plain text read as they are.

extract(path) returns Markdown-ish text. Every reader is optional: a missing library or a damaged file gives a
clear message, never a crash.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

DOC_EXT = {".msg", ".zip",".pdf", ".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".pbix", ".pbit",
           ".odt", ".ods", ".odp", ".rtf", ".ipynb", ".eml", ".html", ".htm", ".epub", ".doc", ".ppt"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
MAX = 200_000


def is_document(p: Path) -> bool:
    return p.suffix.lower() in DOC_EXT


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT


def extract(path: str | Path, max_chars: int = MAX, sheet: str | None = None, pages: str | None = None,
            lang: str = "auto", ocr: str = "auto") -> str:
    """ocr: "auto" reads scanned pages and pictures with OCR, "always" also OCRs pages that have some text
    (mixed scans), "never" skips OCR."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"file not found: {path}")
    ext = p.suffix.lower()
    fn = {".pdf": _pdf, ".docx": _docx, ".docm": _docx, ".pptx": _pptx, ".pptm": _pptx, ".xlsx": _xlsx,
          ".xlsm": _xlsx, ".xls": _xls, ".csv": _csv, ".tsv": _csv, ".pbix": _pbix, ".pbit": _pbix,
          ".odt": _odf, ".ods": _odf, ".odp": _odf, ".rtf": _rtf, ".ipynb": _ipynb, ".eml": _eml,
          ".html": _html, ".htm": _html, ".epub": _epub, ".msg": _msg, ".zip": _zip}.get(ext)
    if ext in (".doc", ".ppt"):
        return (f"{p.name} is an old binary Office file ({ext}). Save it as {ext}x in Word or PowerPoint, or convert it "
                f"with LibreOffice (soffice --headless --convert-to {ext[1:]}x), then read that.")
    if ext in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        return (f"{p.name} is an audio or video file ({p.stat().st_size // 1_000_000} MB). Meshpool does not transcribe "
                "media yet; ask for a transcript file, or describe what you need from it.")
    if ext in IMAGE_EXT:
        from meshpool_harness import ocr
        ocr._heif()
        size = f"{p.stat().st_size // 1024} KB"
        try:
            from PIL import Image
            with Image.open(p) as im:
                size = f"{im.width}x{im.height} px, {size}"
        except Exception:  # noqa: BLE001
            pass
        return f"# {p.name}\nImage, {size}.\n\n" + ocr.describe(p, p.name, lang)
    if not fn:
        return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    try:
        kw = {}
        if fn in (_xlsx, _xls) and sheet:
            kw["sheet"] = sheet
        if fn is _pdf:
            kw.update(pages=pages, lang=lang, ocr_mode=ocr)
        if fn in (_docx, _pptx):
            kw["ocr_mode"] = ocr
        out = fn(p, **kw)
    except ImportError as e:
        return f"Meshpool cannot read {ext} files in this install ({e})."
    except Exception as e:  # noqa: BLE001  damaged or unusual files
        return f"Could not read {p.name}: {type(e).__name__}: {e}"
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n\n[... cut at {max_chars} characters of {len(out)}. Ask for specific pages or sheets.]"
    return out


# ---------- PDF ---------------------------------------------------------------------------------------------------

def _page_set(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out = []
    for part in str(spec).replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += range(max(1, int(a or 1)) - 1, min(n, int(b or n)))
        elif part:
            out.append(int(part) - 1)
    return [i for i in out if 0 <= i < n]


def _pdf(p: Path, pages: str | None = None, lang: str = "auto", ocr_mode: str = "auto") -> str:
    from pypdf import PdfReader
    r = PdfReader(str(p))
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:  # noqa: BLE001
            return f"{p.name} is password-protected."
    n = len(r.pages)
    meta = r.metadata or {}
    head = f"# {p.name}\n{n} pages" + (f", title: {meta.get('/Title')}" if meta.get("/Title") else "") + "\n"
    idx = _page_set(pages, n)
    texts: list[str] = []
    for i in idx:
        try:
            t = r.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if len(t.strip()) < 20:
            try:                                   # pdfminer handles some layouts pypdf misses
                from pdfminer.high_level import extract_text
                t2 = extract_text(str(p), page_numbers=[i]) or ""
                t = t2 if len(t2.strip()) > len(t.strip()) else t
            except Exception:  # noqa: BLE001
                pass
        texts.append(t.strip())
    # The text layer is always kept. "auto" OCRs only pages with no or almost no text (scans, perhaps with a stamped
    # page number); "always" OCRs every page as well and adds what it reads next to the text layer.
    blank = [i for i, t in zip(idx, texts) if not t]
    if ocr_mode == "always":
        want = blank + [i for i, t in zip(idx, texts) if t]
    elif ocr_mode == "auto":
        want = [i for i, t in zip(idx, texts) if len(t) < 50]
    else:
        want = []
    has_text = {i: bool(t) for i, t in zip(idx, texts)}
    want = sorted(dict.fromkeys(want), key=lambda i: (has_text[i], i))     # pages with no text first
    ocr_text: dict[int, str] = {}
    if want:
        try:
            from meshpool_harness import ocr
            if not ocr.available():
                raise RuntimeError("OCR is not included in this install")
            for i, img in ocr.pdf_page_images(p, sorted(want[:40])):
                ocr_text[i] = ocr.describe(img, f"page {i + 1}", lang)
            if ocr_text:
                head += f"\n{len(ocr_text)} page{'s' if len(ocr_text) != 1 else ''} read with OCR.\n"
            elif len(blank) == len(idx):
                head += "\nNo text layer and no page images found in this PDF.\n"
            if len(want) > 40:
                head += "\nOnly the first 40 pages were read with OCR; ask for specific pages for the rest.\n"
        except Exception as e:  # noqa: BLE001
            if blank:
                head += (f"\n{len(blank)} of {len(idx)} pages have no text layer (probably scanned) and OCR did not "
                         f"run ({e}).\n")
            elif ocr_mode == "always":
                head += f"\nOCR did not run ({e}); the text layer is shown.\n"
    parts = []
    for i, t in zip(idx, texts):
        o = ocr_text.get(i)
        found = o.split("\n", 1)[1] if o and o.startswith("Text in ") and "\n" in o else ""
        if o is not None and not t:
            parts.append(f"\n## Page {i + 1} (scanned, read with OCR)\n" + (found or o).strip())
        elif found:
            parts.append(f"\n## Page {i + 1}\n{t}\n\n[text in the page image (OCR)]\n{found.strip()}")
        else:
            parts.append(f"\n## Page {i + 1}\n{t}")
    return head + "\n".join(parts)


# ---------- Word ---------------------------------------------------------------------------------------------------

def _picture_text(blobs: list[tuple[str, bytes]], ocr_mode: str) -> list[str]:
    """OCR the larger pictures inside a Word or PowerPoint file (screenshots, scanned pages, charts as images)."""
    if ocr_mode == "never" or not blobs:
        return []
    try:
        from meshpool_harness import ocr
        if not ocr.available():
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for name, data in sorted(blobs, key=lambda b: -len(b[1]))[:8]:
        if len(data) < 30_000 and ocr_mode != "always":
            continue
        try:
            r = ocr.read_image(data)
        except Exception:  # noqa: BLE001
            continue
        if len(r["text"].strip()) > 15:
            out.append(f"[text in picture {name} (OCR)]\n{r['text']}")
    return out


def _docx(p: Path, ocr_mode: str = "auto") -> str:
    import docx
    d = docx.Document(str(p))
    out = [f"# {p.name}"]
    body = d.element.body
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, d)
            t = para.text.strip()
            if not t:
                continue
            st = (para.style.name if para.style is not None else "") or ""
            if st.startswith("Heading"):
                lvl = re.sub(r"\D", "", st) or "1"
                out.append("#" * min(6, int(lvl) + 1) + " " + t)
            elif st.startswith("List") or st.startswith("Bullet"):
                out.append("- " + t)
            else:
                out.append(t)
        elif tag == "tbl":
            out.append(_md_table([[c.text.strip() for c in row.cells] for row in Table(child, d).rows]))
    for s in d.sections:
        for part in (s.header, s.footer):
            t = " ".join(x.text for x in part.paragraphs if x.text.strip())
            if t:
                out.append(f"[{'header' if part is s.header else 'footer'}] {t}")
                break
    blobs = [(Path(r.target_ref).name, r.target_part.blob) for r in d.part.rels.values()
             if "image" in r.reltype and not r.is_external]
    out += _picture_text(blobs, ocr_mode)
    try:
        comments = [c.text for c in d.comments] if hasattr(d, "comments") else []
        if comments:
            out.append("\n## Comments\n" + "\n".join(f"- {c}" for c in comments))
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(out)


def _num(v) -> str:
    """A cell or chart value as text. Floats keep their full precision (15 significant digits, so 15432198.75 stays
    15432198.75 and 0.1 + 0.2 shows 0.3); whole numbers lose the trailing .0."""
    if isinstance(v, float) and not isinstance(v, bool):
        return f"{v:.15g}"
    return "" if v is None else str(v)


def _md_table(rows: list[list[str]]) -> str:
    rows = [[str(c).replace("|", "\\|").replace("\n", " ") for c in r] for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]])


# ---------- PowerPoint ----------------------------------------------------------------------------------------------

def _pptx(p: Path, ocr_mode: str = "auto") -> str:
    from pptx import Presentation
    prs = Presentation(str(p))
    out = [f"# {p.name}\n{len(prs.slides)} slides"]
    blobs = []
    for i, s in enumerate(prs.slides, 1):
        lines = []
        title = s.shapes.title.text.strip() if s.shapes.title is not None and s.shapes.title.has_text_frame else ""
        for sh in s.shapes:
            lines += _shape_text(sh, title)
        notes = ""
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            notes = s.notes_slide.notes_text_frame.text.strip()
        out.append(f"\n## Slide {i}: {title or '(no title)'}\n" + "\n".join(lines) + (f"\n[notes] {notes}" if notes else ""))
        for sh in s.shapes:
            if getattr(sh, "shape_type", None) == 13:
                try:
                    blobs.append((f"on slide {i}", sh.image.blob))
                except Exception:  # noqa: BLE001
                    pass
    out += _picture_text(blobs, ocr_mode)
    return "\n".join(out)


def _shape_text(sh, title: str) -> list[str]:
    out = []
    if getattr(sh, "shape_type", None) == 6 and hasattr(sh, "shapes"):          # group
        for x in sh.shapes:
            out += _shape_text(x, title)
        return out
    if getattr(sh, "has_text_frame", False) and sh.has_text_frame:
        for para in sh.text_frame.paragraphs:
            t = "".join(r.text for r in para.runs).strip()
            if t and t != title:
                out.append(("  " * para.level) + "- " + t)
    if getattr(sh, "has_table", False) and sh.has_table:
        out.append(_md_table([[c.text for c in row.cells] for row in sh.table.rows]))
    if getattr(sh, "has_chart", False) and sh.has_chart:
        ch = sh.chart
        try:
            cats = list(ch.plots[0].categories)
            series = [(s.name, list(s.values)) for s in ch.plots[0].series]
            rows = [["", *[str(c) for c in cats]]] + [[n, *[_num(v) for v in vals]]
                                                      for n, vals in series]
            out.append("[chart]\n" + _md_table(rows))
        except Exception:  # noqa: BLE001
            out.append("[chart]")
    if getattr(sh, "shape_type", None) == 13:
        out.append(f"[picture: {sh.name}]")
    return out


# ---------- Excel and CSV -----------------------------------------------------------------------------------------

def _xlsx(p: Path, sheet: str | None = None, rows: int = 200) -> str:
    import openpyxl
    vals = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    forms = None
    try:
        try:
            forms = openpyxl.load_workbook(str(p), data_only=False, read_only=True)
        except Exception:  # noqa: BLE001
            forms = None
        return _xlsx_text(p, vals, forms, sheet, rows)
    finally:
        for wb in (vals, forms):
            if wb is not None:
                try:
                    wb.close()
                except Exception:  # noqa: BLE001
                    pass


def _xlsx_text(p: Path, vals, forms, sheet: str | None, rows: int) -> str:
    out = [f"# {p.name}\nSheets: " + ", ".join(vals.sheetnames)]
    for name in ([sheet] if sheet else vals.sheetnames):
        ws = vals[name]
        fws = forms[name] if forms else None
        # read-only sheets saved without a <dimension> report max_row/max_column as None: count while reading
        size = f" ({ws.max_row} rows x {ws.max_column} columns)" if ws.max_row and ws.max_column else ""
        out.append(f"\n## Sheet: {name}{size}")
        table, nform, more = [], 0, False
        frows = fws.iter_rows(max_row=rows) if fws is not None else None
        for r_i, row in enumerate(ws.iter_rows(values_only=True)):
            if r_i >= rows:
                more = True
                break
            frow = next(frows, None) if frows is not None else None
            cells = []
            for c_i, v in enumerate(row):
                f = frow[c_i].value if frow is not None and c_i < len(frow) else None
                s = _num(v)
                if isinstance(f, str) and f.startswith("="):
                    nform += 1
                    s = f"{s} [{f}]" if s else f"[{f}]"
                cells.append(s)
            table.append(cells)
        while table and not any(table[-1]):
            table.pop()
        out.append(_md_table(table) if table else "(empty)")
        if more:
            total = f" of {ws.max_row}" if ws.max_row else ""
            out.append(f"[first {rows}{total} rows; ask for a sheet or use analyze_data for the rest]")
        if nform:
            out.append(f"[{nform} formulas shown in brackets after their values]")
    return "\n".join(out)


def _xls(p: Path, sheet: str | None = None, rows: int = 200) -> str:
    import xlrd
    wb = xlrd.open_workbook(str(p))
    out = [f"# {p.name}\nSheets: " + ", ".join(wb.sheet_names())]
    for name in ([sheet] if sheet else wb.sheet_names()):
        sh = wb.sheet_by_name(name)
        out.append(f"\n## Sheet: {name} ({sh.nrows} rows x {sh.ncols} columns)")
        out.append(_md_table([[str(c) for c in sh.row_values(r)] for r in range(min(rows, sh.nrows))]) or "(empty)")
    return "\n".join(out)


def _csv(p: Path, rows: int = 300) -> str:
    with open(p, "rb") as fh:                      # only the start of a big file, never all of it
        raw = fh.read(5_000_000).decode("utf-8-sig", errors="replace")
    delim = ","
    if p.suffix.lower() == ".tsv":
        delim = "\t"
    elif raw.strip():
        try:
            delim = csv.Sniffer().sniff(raw[:4000], delimiters=",;\t|").delimiter
        except csv.Error:                          # one column, or no consistent delimiter
            delim = ","
    data = list(csv.reader(io.StringIO(raw), delimiter=delim))
    out = f"# {p.name}\n{len(data)} rows x {max((len(r) for r in data), default=0)} columns\n\n" + _md_table(data[:rows])
    if len(data) > rows:
        out += f"\n[first {rows} of {len(data)} rows; use analyze_data for the full file]"
    if p.stat().st_size > 5_000_000:
        out += "\n[only the first 5 MB of this file were read; use analyze_data for the full file]"
    return out


# ---------- Power BI --------------------------------------------------------------------------------------------------

def _pbix(p: Path) -> str:
    out = [f"# {p.name}"]
    with zipfile.ZipFile(str(p)) as z:
        names = z.namelist()
        lay = next((n for n in names if n.lower() == "report/layout"), None)
        if lay:
            try:
                L = json.loads(z.read(lay).decode("utf-16-le", errors="replace").lstrip("\ufeff"))
                out.append(f"\n## Report: {len(L.get('sections') or [])} pages")
                for sec in L.get("sections") or []:
                    out.append(f"\n### Page: {sec.get('displayName', sec.get('name'))}")
                    for vc in sec.get("visualContainers") or []:
                        try:
                            cfg = json.loads(vc.get("config") or "{}")
                            sv = cfg.get("singleVisual") or {}
                            vt = sv.get("visualType", "visual")
                            fields = []
                            for role, proj in (sv.get("projections") or {}).items():
                                fields += [f"{role}: {x.get('queryRef')}" for x in proj]
                            title = ""
                            for o in ((sv.get("vcObjects") or {}).get("title") or []):
                                t = (((o.get("properties") or {}).get("text") or {}).get("expr") or {}).get("Literal", {})
                                title = str(t.get("Value", "")).strip("'")
                            out.append(f"- {vt}" + (f" \"{title}\"" if title else "") + (f": {'; '.join(fields)}" if fields else ""))
                        except ValueError:
                            continue
            except ValueError:
                out.append("(the report layout could not be read)")
        schema = next((n for n in names if n.lower() == "datamodelschema"), None)
        if schema:                                  # .pbit templates keep the model as JSON
            try:
                M = json.loads(z.read(schema).decode("utf-16-le", errors="replace").lstrip("\ufeff"))
                out.append(_model_from_schema(M.get("model") or {}))
            except ValueError:
                pass
    if p.suffix.lower() == ".pbix":
        try:
            out.append(_model_from_pbixray(p))
        except ImportError:
            out.append("\n(The data model needs the pbixray library, which is not in this install.)")
        except Exception as e:  # noqa: BLE001
            out.append(f"\n(The data model could not be decoded: {type(e).__name__}: {e})")
    return "\n".join(out)


def _model_from_schema(m: dict) -> str:
    out = ["\n## Data model"]
    for t in m.get("tables") or []:
        cols = [c.get("name") for c in t.get("columns") or [] if not str(c.get("name", "")).startswith("RowNumber")]
        out.append(f"\n### Table: {t.get('name')}\nColumns: {', '.join(cols)}")
        for ms in t.get("measures") or []:
            expr = ms.get("expression")
            expr = "\n".join(expr) if isinstance(expr, list) else expr
            out.append(f"- Measure {ms.get('name')} = {expr}")
    rels = m.get("relationships") or []
    if rels:
        out.append("\n### Relationships\n" + "\n".join(f"- {r.get('fromTable')}[{r.get('fromColumn')}] -> "
                                                       f"{r.get('toTable')}[{r.get('toColumn')}]" for r in rels))
    return "\n".join(out)


def _model_from_pbixray(p: Path, rows: int = 20) -> str:
    from pbixray import PBIXRay
    m = PBIXRay(str(p))
    out = ["\n## Data model"]
    try:
        out.append(f"Size: {m.size / 1e6:.1f} MB, tables: {', '.join(map(str, m.tables))}")
    except Exception:  # noqa: BLE001
        pass
    sch = getattr(m, "schema", None)
    if sch is not None and len(sch):
        out.append("\n### Columns\n" + _md_table([list(map(str, sch.columns))] + sch.head(400).astype(str).values.tolist()))
    for attr, title in (("dax_measures", "DAX measures"), ("dax_columns", "Calculated columns"),
                        ("relationships", "Relationships"), ("power_query", "Power Query (M)")):
        try:
            df = getattr(m, attr)
        except Exception:  # noqa: BLE001
            continue
        if df is None or not len(df):
            continue
        out.append(f"\n### {title}")
        for rec in df.to_dict("records")[:200]:
            out.append("- " + "; ".join(f"{k}: {v}" for k, v in rec.items() if v is not None and str(v) != "nan"))
    for t in list(m.tables)[:12]:
        try:
            df = m.get_table(t)
            out.append(f"\n### Sample of {t} ({len(df)} rows)\n" + _md_table([list(map(str, df.columns))] +
                                                                           df.head(rows).astype(str).values.tolist()))
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(out)


# ---------- other formats -----------------------------------------------------------------------------------------

def _xml_text(data: bytes) -> str:
    t = re.sub(rb"<text:(p|h)[^>]*>", b"\n", data)
    t = re.sub(rb"<table:table-row[^>]*>", b"\n", t)
    t = re.sub(rb"<table:table-cell[^>]*>", b" | ", t)
    end = t.rfind(b">") + 1
    t = re.sub(rb"<[^>]+>", b"", t[:end]) + t[end:]
    import html
    return html.unescape(t.decode("utf-8", errors="replace"))


def _odf(p: Path) -> str:
    with zipfile.ZipFile(str(p)) as z:
        return f"# {p.name}\n" + re.sub(r"\n{3,}", "\n\n", _xml_text(z.read("content.xml"))).strip()


def _rtf(p: Path) -> str:
    s = p.read_text(encoding="latin-1", errors="replace")
    s = re.sub(r"\\par[d]?", "\n", s)
    s = re.sub(r"\{\\\*[^{}]*\}|\\[a-z]+-?\d* ?|[{}]", "", s)
    return f"# {p.name}\n" + s.strip()


def _ipynb(p: Path) -> str:
    nb = json.loads(p.read_text(encoding="utf-8"))
    out = [f"# {p.name}"]
    for i, c in enumerate(nb.get("cells") or [], 1):
        src = "".join(c.get("source") or [])
        if c.get("cell_type") == "code":
            out.append(f"\n## Cell {i} (code)\n```\n{src}\n```")
            for o in c.get("outputs") or []:
                txt = "".join(o.get("text") or (o.get("data") or {}).get("text/plain") or [])
                if txt:
                    out.append("Output:\n```\n" + txt[:2000] + "\n```")
        else:
            out.append(f"\n## Cell {i} ({c.get('cell_type')})\n{src}")
    return "\n".join(out)


def _eml(p: Path) -> str:
    import email
    from email import policy
    m = email.message_from_bytes(p.read_bytes(), policy=policy.default)
    head = "\n".join(f"{k}: {m[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if m[k])
    body = m.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = _strip_html(text)
    atts = [a.get_filename() for a in m.iter_attachments()]
    return f"# {p.name}\n{head}\n\n{text}" + (f"\n\nAttachments: {', '.join(filter(None, atts))}" if atts else "")


def _strip_html(s: str) -> str:
    import html
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", s)
    end = s.rfind(">") + 1                   # text after the last '>' holds no tag; keeps this linear on bad input
    s = re.sub(r"<[^>]+>", " ", s[:end]) + s[end:]
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def _html(p: Path) -> str:
    return _strip_html(p.read_text(encoding="utf-8", errors="replace"))


def _epub(p: Path) -> str:
    out = [f"# {p.name}"]
    with zipfile.ZipFile(str(p)) as z:
        for n in sorted(x for x in z.namelist() if x.lower().endswith((".xhtml", ".html", ".htm"))):
            t = _strip_html(z.read(n).decode("utf-8", errors="replace"))
            if t:
                out.append(t)
    return "\n\n".join(out)


def _msg(p: Path) -> str:
    """Outlook .msg: sender, recipients, subject, body and attachment names (OLE property streams)."""
    import olefile
    ole = olefile.OleFileIO(str(p))

    def prop(code: str, base: str = "") -> str:
        for suffix, enc in (("001F", "utf-16-le"), ("001E", "latin-1")):
            name = f"{base}__substg1.0_{code}{suffix}"
            if ole.exists(name):
                return ole.openstream(name).read().decode(enc, errors="replace").rstrip("\x00")
        return ""
    head = [f"From: {prop('0C1A')} <{prop('5D01') or prop('0C1F')}>", f"To: {prop('0E04')}", f"Cc: {prop('0E03')}",
            f"Subject: {prop('0037')}"]
    body = prop("1000")
    if not body:
        html = ""
        if ole.exists("__substg1.0_10130102"):
            html = ole.openstream("__substg1.0_10130102").read().decode("utf-8", errors="replace")
        body = _strip_html(html) if html else ""
    atts = []
    for entry in ole.listdir(streams=False, storages=True):
        if entry and entry[0].startswith("__attach_version1.0_") and len(entry) == 1:
            n = prop("3707", entry[0] + "/") or prop("3704", entry[0] + "/")
            if n:
                atts.append(n)
    ole.close()
    return f"# {p.name}\n" + "\n".join(h for h in head if not h.endswith(": ") and h != "From:  <>") + \
        f"\n\n{body.strip()}" + (f"\n\nAttachments: {', '.join(atts)}" if atts else "")


def _zip(p: Path) -> str:
    with zipfile.ZipFile(str(p)) as z:
        rows = [i for i in z.infolist() if not i.is_dir()]
        out = [f"# {p.name}\n{len(rows)} files. Extract it (for example with bash: unzip) to read one."]
        out += [f"- {i.filename} ({i.file_size:,} bytes)" for i in rows[:300]]
        if len(rows) > 300:
            out.append(f"... and {len(rows) - 300} more")
    return "\n".join(out)
