"""Forge renderer regressions: charts draw what the data says (signs, zero baselines, precision, axis crossings),
and a bad slide never aborts the deck."""
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from pptx import Presentation  # noqa: E402

from engines.forge import render as R  # noqa: E402


def _charts(path):
    return [shp.chart for sl in Presentation(path).slides for shp in sl.shapes if shp.has_chart]


def test_numeric_categories_fall_back_to_stats_without_aborting(tmp_path):
    line = {"archetype": "line_trend", "title": "Revenue rose every year since the 2022 relaunch",
            "chart": {"kind": "line", "categories": [2022, 2023, 2024],
                      "series": [{"name": "Revenue", "values": [1.5, 2.0, 2.4]}], "unit": "$m"},
            "takeaway": {"text": "Growth held"}, "bullets": ["Price rose"]}
    r = R.render([line], str(tmp_path / "d.pptx"))
    assert r["slides"] == 1
    st = R.stat_stack(line["chart"])
    assert [i["label"] for i in st["items"]] == ["2022", "2023", "2024"]
    assert st["items"][0]["value"] == "$1.5m"


def test_stat_stack_keeps_the_unit_and_sign():
    st = lambda unit, vals: [i["value"] for i in R.stat_stack(  # noqa: E731
        {"categories": ["A", "B"], "series": [{"name": "Metric", "values": vals}], "unit": unit})["items"]]
    assert st("%", [12.5, 3]) == ["12.5%", "3%"]
    assert st("x", [12.5, 3]) == ["12.5x", "3x"]
    assert st("pp", [1.5, 2]) == ["1.5 pp", "2 pp"]
    assert st("€bn", [2.5, 3]) == ["€2.5bn", "€3bn"]
    assert st("$m", [-4.2, 3]) == ["-$4.2m", "$3m"]
    assert st("stores", [450, 12]) == ["450 stores", "12 stores"]


def test_waterfall_below_zero_and_signed_labels(tmp_path):
    s = {"archetype": "waterfall_bridge", "title": "Losses turned the cash balance negative before the raise",
         "chart": {"kind": "waterfall", "categories": ["Start", "Loss", "Raise", "End"],
                   "series": [{"name": "Cash", "values": [5, -8, 4, 1]}], "unit": "$m"},
         "takeaway": {"text": "The raise restored a positive balance"}, "bullets": ["Burn was $8m"]}
    out = tmp_path / "w.pptx"
    r = R.render([s], str(out))
    assert not [e for e in r["errors"] if "malformed" in e], r["errors"]
    ch = _charts(str(out))[0]
    base, above, below = (list(se.values) for se in ch.plots[0].series)
    # running totals 5 -> -3 -> 1: Loss spans -3..5 (crosses zero), Raise spans -3..1 (crosses zero)
    assert (above[1], below[1]) == (5, -3) and (above[2], below[2]) == (1, -3)
    assert above[0] == 5 and above[3] == 1
    labels = [pt.data_label.text_frame.text for se in list(ch.plots[0].series)[1:]
              for j, pt in enumerate(se.points) if pt.data_label.has_text_frame]
    assert "-8" in labels and "+4" in labels


def test_columns_start_at_zero_and_keep_decimals(tmp_path):
    s = {"archetype": "bar_takeaway", "title": "Margins sit in a narrow band across the four regions",
         "chart": {"kind": "column", "categories": ["N", "S", "E", "W"],
                   "series": [{"name": "Margin", "values": [30.25, 31.5, 32.75, 31.0]}]},
         "takeaway": {"text": "Narrow band"}, "bullets": ["East leads"]}
    out = tmp_path / "c.pptx"
    R.render([s], str(out))
    ch = _charts(str(out))[0]
    assert ch.value_axis.minimum_scale == 0
    assert ch.plots[0].series[0].data_labels.number_format == "#,##0.00"


def test_bubble_axes_cross_at_the_reference_values(tmp_path):
    s = {"archetype": "bar_takeaway", "title": "Two segments combine growth and margin above the portfolio",
         "chart": {"kind": "bubble", "x_range": [0, 10], "y_range": [0, 40], "x_cross": 5, "y_cross": 20,
                   "points": [{"x": 2, "y": 10, "size": 3, "label": "A"}, {"x": 8, "y": 30, "size": 5, "label": "B"},
                              {"x": 6, "y": 15, "size": 2, "label": "C"}]},
         "takeaway": {"text": "B leads"}, "bullets": ["B grows fastest"]}
    out = tmp_path / "b.pptx"
    R.render([s], str(out))
    ch = _charts(str(out))[0]
    assert ch.value_axis.crosses_at == 20                  # the x axis crosses the y axis at y = 20
    assert ch.category_axis.crosses_at == 5


def test_table_as_chart_never_drops_columns():
    t = {"columns": ["Region", "Revenue", "Margin"], "rows": [["N", "$10m", "30%"], ["S", "$12m", "28%"]]}
    assert R._table_as_chart(t) is None                    # mixed units: keep the table
    t = {"columns": ["Region", "2023", "2024", "2025", "2026"],
         "rows": [["N", "1", "2", "3", "4"], ["S", "2", "3", "4", "5"]]}
    assert R._table_as_chart(t) is None                    # four value columns: keep the table
    t = {"columns": ["Region", "2024", "2025"], "rows": [["N", "1.5", "2"], ["S", "2", "3"]]}
    c = R._table_as_chart(t)
    assert len(c["series"]) == 2 and c["decimals"] == 1


def test_benchmark_without_median_aligns_bars_with_labels(tmp_path):
    s = {"archetype": "benchmark", "title": "The company trades below its peers on both multiples",
         "benchmark": {"companies": ["A", "B", "C"],
                       "panels": [{"title": "EV/EBITDA", "values": [10, 12, 8]}]},
         "takeaway": {"text": "Discount"}, "bullets": ["Lowest multiple"]}
    out = tmp_path / "bm.pptx"
    r = R.render([s], str(out))
    assert not [e for e in r["errors"] if "malformed" in e], r["errors"]
    ch = _charts(str(out))[0]
    assert len(list(ch.plots[0].categories)) == 4          # three companies + the Median label row
    bad = dict(s, benchmark={"companies": ["A", "B", "C"], "panels": [{"title": "EV/EBITDA", "values": [10, 12]}]})
    assert any("one value per company" in e for e in R.schema_errors(bad))
