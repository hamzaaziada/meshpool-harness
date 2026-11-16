"""LibreOffice recalculation must not trust values cached in the file (engines/forge/common.py soffice_convert)."""
import re
import shutil
import zipfile

import pytest

from engines.forge.common import find_soffice, soffice_convert


@pytest.mark.skipif(not find_soffice(), reason="LibreOffice not installed")
def test_a_workbook_with_stale_cached_values_is_recalculated(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"] = 10, "=A1*2"
    wb.save(tmp_path / "a.xlsx")
    first = soffice_convert(str(tmp_path / "a.xlsx"), "xlsx", str(tmp_path / "once"))   # now A2 caches 20
    stale = tmp_path / "stale.xlsx"
    with zipfile.ZipFile(first) as zin, zipfile.ZipFile(stale, "w") as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode()
                xml = re.sub(r'(<c r="A1"[^>]*>\s*<v>)10(</v>)', r"\g<1>15\g<2>", xml)    # input edited, cache kept
                assert "<v>15</v>" in xml and "<v>20</v>" in xml
                data = xml.encode()
            zout.writestr(it, data)
    out = soffice_convert(str(stale), "xlsx", str(tmp_path / "again"))
    v = openpyxl.load_workbook(out, data_only=True).active["A2"].value
    assert v == 30, f"stale value {v}"
    shutil.rmtree(tmp_path / "once", ignore_errors=True)
