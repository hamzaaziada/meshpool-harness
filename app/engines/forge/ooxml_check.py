"""Will PowerPoint/Excel open this file without "repair"? Validate every XML part against the ECMA-376 Transitional
schemas (the standard Office implements) and check the cross-part rules Office enforces.

    python -m engines.forge.ooxml_check deck.pptx [--schemas DIR]

Schemas: the official ECMA-376 Part 4 Transitional XSDs. They are not bundled (Ecma copyright); fetch them once with
`python -m engines.forge.ooxml_check --install-schemas` (into engines/forge/schemas/ooxml, git-ignored) or point
FORGE_OOXML_SCHEMAS at an unzipped copy. Without them the cross-part checks still run and schema validation reports
`not_run`.

Where Office's own implementation differs from the published schema ([MS-OI29500]), Office's form wins, because the
question is "does Office open it", not "is it textbook XML". Each accepted form is one Office applications write
themselves:
- bullet size as an integer in thousandths of a percent (buSzPct val="70000", PowerPoint), range-checked here and
  then validated as "70%";
- xml:space="preserve" on shared-string text (Excel);
- workbookPr/@dateCompatibility (ISO/IEC 29500-1:2012 and later; LibreOffice and Excel);
- w:zoom without w:percent (Microsoft Word for Mac, observed in testkit/practical/fixtures/office/contract.docx).

What it checks:
- every XML part validates against its schema (slides, layouts, masters, notes, theme, charts, presentation;
  workbook, worksheets, styles, shared strings), after resolving markup-compatibility (mc:AlternateContent);
- every relationship target exists in the package and every part has a content type;
- shape ids (cNvPr/@id) are unique per slide; the slide list ids are unique and >= 256;
- every table row has as many cells as the table grid has columns;
- chart workbooks embedded in the deck are checked the same way.
"""
from __future__ import annotations

import os
import posixpath
import re
import sys
import zipfile
from functools import lru_cache
from pathlib import Path

from lxml import etree

MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
ROOT_SCHEMA = {
    "http://schemas.openxmlformats.org/presentationml/2006/main": "pml.xsd",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "dml-main.xsd",
    "http://schemas.openxmlformats.org/drawingml/2006/chart": "dml-chart.xsd",
    "http://schemas.openxmlformats.org/spreadsheetml/2006/main": "sml.xsd",
    "http://schemas.openxmlformats.org/drawingml/2006/chartDrawing": "dml-chartDrawing.xsd",
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": "wml.xsd",
}
XML_XSD = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="http://www.w3.org/XML/1998/namespace">
  <xs:attribute name="lang" type="xs:language"/><xs:attribute name="space"><xs:simpleType>
  <xs:restriction base="xs:NCName"><xs:enumeration value="default"/><xs:enumeration value="preserve"/>
  </xs:restriction></xs:simpleType></xs:attribute><xs:attribute name="base" type="xs:anyURI"/>
  <xs:attribute name="id" type="xs:ID"/></xs:schema>"""


def _schema_dir(explicit: str | None = None) -> Path | None:
    for c in (explicit, os.environ.get("FORGE_OOXML_SCHEMAS"), str(Path(__file__).parent / "schemas" / "ooxml")):
        if c and (Path(c) / "pml.xsd").exists():
            return Path(c)
    return None


@lru_cache(maxsize=1)
def _patched(folder: str) -> str:
    """Copy the schemas so every import of the W3C xml namespace points at a local xml.xsd (the published files
    import it without a location, which leaves xml:space undeclared)."""
    import tempfile
    out = Path(tempfile.mkdtemp(prefix="ooxml_xsd_"))
    (out / "xml.xsd").write_text(XML_XSD)
    for f in Path(folder).glob("*.xsd"):
        t = f.read_text(encoding="utf-8")
        t = re.sub(r'<xsd:import\s+namespace="http://www.w3.org/XML/1998/namespace"\s*/>',
                   '<xsd:import namespace="http://www.w3.org/XML/1998/namespace" schemaLocation="xml.xsd"/>', t)
        (out / f.name).write_text(t, encoding="utf-8")
    return str(out)


@lru_cache(maxsize=8)
def _schema(folder: str, name: str):
    return etree.XMLSchema(etree.parse(str(Path(_patched(folder)) / name)))


def _resolve_mc(root):
    """Apply markup compatibility like Office does: keep Choice if its namespace is understood, else Fallback;
    drop mc:Ignorable namespaces' attributes and elements."""
    ignorable = set()
    for el in root.iter():
        ig = el.get(f"{{{MC}}}Ignorable")
        if ig:
            for pfx in ig.split():
                ns = el.nsmap.get(pfx)
                if ns:
                    ignorable.add(ns)
            del el.attrib[f"{{{MC}}}Ignorable"]
    for ac in list(root.iter(f"{{{MC}}}AlternateContent")):
        parent = ac.getparent()
        fallback = ac.find(f"{{{MC}}}Fallback")
        idx = parent.index(ac)
        parent.remove(ac)
        if fallback is not None:
            for k, child in enumerate(list(fallback)):
                parent.insert(idx + k, child)
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).namespace in ignorable and el.getparent() is not None:
            el.getparent().remove(el)
            continue
        for a in list(el.attrib):
            if a.startswith("{") and a[1:].split("}")[0] in ignorable:
                del el.attrib[a]
    return root


A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
SCHEMA_URL = "https://ecma-international.org/wp-content/uploads/ECMA-376-4_5th_edition_december_2016.zip"


def install_schemas(dest: str | None = None) -> Path:
    """Download ECMA-376 Part 4 and unpack its Transitional XSDs."""
    import io
    import urllib.request
    out = Path(dest or Path(__file__).parent / "schemas" / "ooxml")
    out.mkdir(parents=True, exist_ok=True)
    outer = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(SCHEMA_URL, timeout=120).read()))
    inner_name = next(n for n in outer.namelist() if n.endswith("XMLSchema-Transitional.zip"))
    inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_name)))
    for n in inner.namelist():
        if n.endswith(".xsd"):
            (out / Path(n).name).write_bytes(inner.read(n))
    return out


def _office_compat(root, part, problems):
    """Rewrite values Office writes differently from the published schema (MS-OI29500), checking them first."""
    for el in root.iter(f"{A}buSzPct"):
        v = el.get("val", "")
        if v.isdigit():
            if not 25000 <= int(v) <= 400000:
                problems.append({"part": part, "check": "office_value", "detail": f"buSzPct {v} outside 25000-400000"})
            el.set("val", f"{max(25, min(400, round(int(v) / 1000)))}%")
    S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    XS = "{http://www.w3.org/XML/1998/namespace}space"
    for el in root.iter(f"{S}t"):
        el.attrib.pop(XS, None)
    for el in root.iter(f"{S}workbookPr"):
        el.attrib.pop("dateCompatibility", None)
    for el in root.iter(f"{W}zoom"):
        if el.get(f"{W}percent") is None:
            el.set(f"{W}percent", "100")


def _tables(root, part, problems):
    for tbl in root.iter(f"{A}tbl"):
        cols = len(tbl.findall(f"{A}tblGrid/{A}gridCol"))
        for i, tr in enumerate(tbl.findall(f"{A}tr")):
            n = len(tr.findall(f"{A}tc"))
            if n != cols:
                problems.append({"part": part, "check": "table_grid",
                                 "detail": f"row {i + 1} has {n} cells, grid has {cols} columns"})


def check(path, schemas: str | None = None, _name: str | None = None) -> dict:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    problems: list[dict] = []
    folder = _schema_dir(schemas)
    validated = 0

    # content types
    ct = etree.fromstring(z.read("[Content_Types].xml"))
    ns_ct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    overrides = {o.get("PartName").lstrip("/") for o in ct.iter(f"{ns_ct}Override")}
    defaults = {d.get("Extension").lower() for d in ct.iter(f"{ns_ct}Default")}
    for n in names:
        if n.endswith("/") or n == "[Content_Types].xml":
            continue
        if n not in overrides and n.rsplit(".", 1)[-1].lower() not in defaults:
            problems.append({"part": n, "check": "content_type", "detail": "part has no content type"})

    # relationships resolve
    for n in names:
        if not n.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(n))
        rels = etree.fromstring(z.read(n))
        for r in rels:
            if r.get("TargetMode") == "External":
                continue
            tgt = r.get("Target")
            full = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath(posixpath.join(base, tgt))
            if full not in names:
                problems.append({"part": n, "check": "broken_relationship", "detail": f"{r.get('Id')} -> {tgt}"})

    for n in sorted(names):
        if not n.endswith(".xml") or n.startswith("docProps/") or n == "[Content_Types].xml":
            continue
        try:
            root = etree.fromstring(z.read(n))
        except etree.XMLSyntaxError as e:
            problems.append({"part": n, "check": "xml_syntax", "detail": str(e)[:200]})
            continue
        # unique shape ids per slide-like part
        if re.match(r"ppt/(slides|slideLayouts|slideMasters|notesSlides)/", n):
            # mc:AlternateContent holds one shape twice (Choice and Fallback, same id); only one is ever used
            ids = [el.get("id") for el in root.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr")
                   if not any(a.tag == f"{{{MC}}}Fallback" for a in el.iterancestors())]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                problems.append({"part": n, "check": "duplicate_shape_id", "detail": f"ids {sorted(dup)[:5]}"})
        if n == "ppt/presentation.xml":
            sids = [int(e.get("id")) for e in root.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}sldId")]
            if len(set(sids)) != len(sids) or any(i < 256 for i in sids):
                problems.append({"part": n, "check": "slide_ids", "detail": str(sids)})
        if n.startswith("ppt/"):
            _tables(root, n, problems)
        if folder is None:
            continue
        _office_compat(root, n, problems)
        xsd = ROOT_SCHEMA.get(etree.QName(root).namespace)
        if not xsd:
            continue
        schema = _schema(str(folder), xsd)
        doc = _resolve_mc(root)
        if not schema.validate(doc):
            for e in list(schema.error_log)[:8]:
                problems.append({"part": n, "check": "schema", "detail": f"line {e.line}: {e.message[:220]}"})
        validated += 1
    # chart data workbooks
    for n in sorted(names):
        if n.endswith(".xlsx") and "/embeddings/" in n:
            import io
            sub = check(io.BytesIO(z.read(n)), schemas, _name=n)
            validated += sub["parts_validated"]
            problems += [{**p, "part": f"{n}!{p['part']}"} for p in sub["problems"]]
    return {"file": str(_name or path), "ok": not problems, "schema_validation": "run" if folder else "not_run",
            "parts_validated": validated, "problems": problems}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--schemas")
    ap.add_argument("--install-schemas", action="store_true")
    a = ap.parse_args()
    if a.install_schemas:
        print("schemas in", install_schemas(a.schemas))
    bad = 0
    for f in a.files:
        r = check(f, a.schemas)
        print(f"{'OK ' if r['ok'] else 'FAIL'} {f}  schema={r['schema_validation']} parts={r['parts_validated']} "
              f"problems={len(r['problems'])}")
        for p in r["problems"][:30]:
            print(f"   {p['check']:20} {p['part']}: {p['detail']}")
        bad += not r["ok"]
    sys.exit(1 if bad else 0)
