"""Practical test: real tasks a user would type, run through the app's own API on the real model, graded by checks.

  python practical.py BASE_URL OUT_DIR [--only code|work] [--harness a,b] [--case id,id] [--budget USD]

Each run gets a fresh copy of its fixture, auto-approves every request (as a user clicking Allow would), and is
graded on the files it leaves behind. Runs go one at a time so the key's spend can be split per run. Writes one JSON
line per run to OUT_DIR/results.jsonl.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"


def spend() -> float | None:
    """Key spend from the gateway's X-Litellm-Key-Spend header (one 1-token call, about $0.000002)."""
    import os
    base = os.environ.get("BENCH_BASE_URL", "https://llm.meshpool.ai/v1")
    body = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "1"}], "max_tokens": 1}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer dummy", "Content-Type": "application/json"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                v = r.headers.get("X-Litellm-Key-Spend")
                return float(v) if v else None
        except Exception:  # noqa: BLE001
            continue
    return None


def client(base):
    tok = re.search(r'token: "([^"]+)"', urllib.request.urlopen(base + "/").read().decode()).group(1)

    def call(path, body=None, timeout=120):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"_http": e.code, "_body": e.read()[:300].decode(errors="replace")}
    return call


def run_turn(c, harness, kind, text, workdir, limit):
    v = c("/api/agent", {"workdir": str(workdir), "harness": harness, "kind": kind, "mode": "auto"})
    if "id" not in v:
        return {"error": v}, 0, 0
    sid = v["id"]
    time.sleep(1.5)
    c(f"/api/agent/{sid}/send", {"text": text})
    asked = 0
    seen_q: set = set()
    t0 = time.time()
    while time.time() - t0 < limit:
        v = c(f"/api/agent/{sid}?since=0")
        evs = v.get("events", [])
        done = {e["id"] for e in evs if e["type"] == "approval_done"}
        for e in evs:
            if e["type"] == "approval" and e["id"] not in done:
                asked += 1
                c(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "yes"})
        answered = {e["id"] for e in evs if e["type"] == "question_done"}
        for e in evs:                                # ask_user: pick the recommended options, as a user in a hurry
            if e["type"] == "question" and e["id"] not in answered and e["id"] not in seen_q:
                seen_q.add(e["id"])
                picks = [{"selected": [o["label"] for o in q.get("options", []) if o.get("recommended")]
                          or [q["options"][0]["label"]], "other": ""} for q in e.get("questions") or []]
                c(f"/api/agent/{sid}/reply", {"question_id": e["id"], "answers": picks})
        if v.get("state") == "idle" and any(e["type"] == "turn_end" for e in evs):
            break
        time.sleep(1)
    else:
        c(f"/api/agent/{sid}/stop", {})
        v["timed_out"] = True
    return v, asked, time.time() - t0


def reply(v):
    t = [e.get("text") or "" for e in v.get("events", []) if e["type"] == "assistant"]
    return "\n".join(t)


def find(w: Path, name: str | None, ext: str, before: set):
    hits = [p for p in w.rglob(f"*{ext}") if p not in before and "~$" not in p.name and ".run" not in p.parts]
    if name:
        exact = [p for p in w.rglob(name)]
        if exact:
            return exact[0], True
    return (max(hits, key=lambda p: p.stat().st_mtime), False) if hits else (None, False)


def sh(cmd, cwd, timeout=120):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-800:]


def recalc(xlsx: Path) -> Path:
    """LibreOffice recalculation, so formula cells have values to check."""
    out = xlsx.parent / ".recalc"
    out.mkdir(exist_ok=True)
    subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(out), str(xlsx)],
                   capture_output=True, timeout=120)
    return out / xlsx.name


# ---------------------------------------------------------------- code cases
def g_todo(w, v, before):
    for f in ("todos.json",):
        (w / f).unlink(missing_ok=True)
    sh('python3 todo.py add a && python3 todo.py add b && python3 todo.py add c', w)
    rc, out = sh("python3 todo.py done 2", w)
    ok = rc == 0 and re.search(r"2\. \[x\] b", out) and re.search(r"1\. \[ \] a", out)
    return bool(ok), out[-200:]


def g_shop(w, v, before):
    code = ("from cart import cart_total as t\n"
            "assert float(t([{'price': 1.005, 'qty': 1}], tax_rate=0)) == 1.01, t([{'price': 1.005, 'qty': 1}], tax_rate=0)\n"
            "assert float(t([{'price': 10.0, 'qty': 2}])) == 24.0\n"
            "assert float(t([{'price': 100.0, 'qty': 1}], coupon='SAVE10')) == 108.0, t([{'price': 100.0, 'qty': 1}], coupon='SAVE10')\n"
            "print('hidden ok')")
    rc, out = sh(f'python3 -c "{code}"', w)
    rc2, out2 = sh("python3 -m pytest -q", w)
    added = len(re.findall(r"def test_", (w / "test_cart.py").read_text())) > 1 or any(
        p.name.startswith("test_") and p.name != "test_cart.py" for p in w.glob("*.py"))
    return rc == 0 and rc2 == 0 and added, (out + out2)[-300:] + f" tests_added={added}"


def g_slug(w, v, before):
    rc, out = sh("node --test", w)
    same = (w / "slug.test.js").read_text() == (FIX / "slug" / "slug.test.js").read_text()
    return rc == 0 and same, out[-200:] + f" test_file_unchanged={same}"


def g_tip(w, v, before):
    f = w / "index.html"
    if not f.exists():
        return False, "no index.html"
    js = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto('file://' + process.argv[2]);
  const nums = await p.$$('input[type=number], input:not([type])');
  if (nums.length < 1) { console.log('NOINPUT'); await b.close(); return; }
  await nums[0].fill('100'); await nums[0].dispatchEvent('input');
  const btn = await p.$('button:has-text("20")'); if (btn) await btn.click();
  const ppl = nums.length > 1 ? nums[nums.length - 1] : null;
  if (ppl) { await ppl.fill('4'); await ppl.dispatchEvent('input'); await ppl.dispatchEvent('change'); }
  await p.waitForTimeout(500);
  console.log((await p.innerText('body')).replace(/\s+/g, ' ') + ' ERRS:' + errs.join('|'));
  await b.close();
})();"""
    (w.parent / "tip_check.js").write_text(js)
    rc, out = sh(f"node {w.parent / 'tip_check.js'} {f}", w)
    return "30.00" in out and "ERRS:" in out and out.strip().endswith("ERRS:"), out[-300:]


CODE = [
    ("todo", "todo", 'When I run "python todo.py done 2" it ticks the wrong item. Please fix it.', g_todo),
    ("shop", "shop", "Customers say order totals are sometimes a cent off. Prices come in as floats like 1.005 that "
     "mean exactly 1.005; money must round half-up to the cent. Fix cart.py so totals are exact, and add a coupon: "
     "cart_total(lines, tax_rate=0.2, coupon=None), where coupon 'SAVE10' takes 10% off the subtotal before tax. "
     "Add tests for both.", g_shop),
    ("slug", "slug", "npm test is failing. Fix the code (not the tests).", g_slug),
    ("tip", None, "Make a tip calculator as a single index.html file: a bill amount input, tip buttons for 10%, 15% "
     "and 20%, a number-of-people input, and it shows the total per person with 2 decimals. Plain HTML and JS.",
     g_tip),
]


# ---------------------------------------------------------------- work cases
def g_proposal(w, v, before):
    import docx
    f, named = find(w, "proposal.docx", ".docx", before)
    if not f:
        return False, "no .docx"
    d = docx.Document(f)
    heads = [p for p in d.paragraphs if p.style.name.lower().startswith(("heading", "title"))]
    lists = [p for p in d.paragraphs if "list" in p.style.name.lower() or p._p.pPr is not None and p._p.pPr.numPr is not None]
    words = sum(len(p.text.split()) for p in d.paragraphs) + sum(
        len(c.text.split()) for t in d.tables for r in t.rows for c in r.cells)
    ok = len(heads) >= 4 and len(d.tables) >= 2 and len(lists) >= 2 and words >= 300
    return ok, f"{f.name} named={named} headings={len(heads)} tables={len(d.tables)} list_items={len(lists)} words={words}"


def _expected_region():
    import csv
    rev, cost = {}, {}
    for r in csv.DictReader(open(FIX / "office" / "sales.csv")):
        rev[r["region"]] = rev.get(r["region"], 0) + int(r["units"]) * float(r["unit_price"])
        cost[r["region"]] = cost.get(r["region"], 0) + float(r["cost"])
    return rev, cost


def g_sales_xlsx(w, v, before):
    import openpyxl
    f, named = find(w, "sales_report.xlsx", ".xlsx", before)
    if not f:
        return False, "no .xlsx"
    wb = openpyxl.load_workbook(f)
    formulas = sum(1 for ws in wb for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("="))
    charts = sum(len(ws._charts) for ws in wb)
    money = any("$" in (c.number_format or "") or "#,##0" in (c.number_format or "")
                for ws in wb for row in ws.iter_rows() for c in row)
    frozen = any(ws.freeze_panes for ws in wb)
    vals = openpyxl.load_workbook(recalc(f), data_only=True)
    nums = {round(c.value) for ws in vals for row in ws.iter_rows() for c in row if isinstance(c.value, (int, float))}
    rev, _ = _expected_region()
    found = sum(1 for x in rev.values() if any(abs(n - x) <= 1 for n in nums))
    ok = formulas >= 4 and charts >= 1 and found == 4
    return ok, (f"{f.name} named={named} sheets={wb.sheetnames} formulas={formulas} charts={charts} money_fmt={money} "
                f"frozen={frozen} region_totals_found={found}/4")


def g_deck(w, v, before):
    from pptx import Presentation
    from pptx.util import Emu
    f, named = find(w, "sales_deck.pptx", ".pptx", before)
    if not f:
        return False, "no .pptx"
    pr = Presentation(f)
    n = len(pr.slides)
    visuals = sum(1 for s in pr.slides for sh_ in s.shapes if sh_.has_chart or sh_.shape_type == 13)
    W, H = pr.slide_width, pr.slide_height
    off = sum(1 for s in pr.slides for x in s.shapes
              if x.left is not None and (x.left + x.width > W * 1.01 or x.top + x.height > H * 1.01))
    out = f.parent / ".render"
    out.mkdir(exist_ok=True)
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out), str(f)], capture_output=True,
                   timeout=180)
    ok = 6 <= n <= 9 and visuals >= 1 and off == 0
    return ok, f"{f.name} named={named} slides={n} charts_or_pictures={visuals} shapes_off_slide={off}"


def g_contract(w, v, before):
    import docx
    f = w / "contract.docx"
    d = docx.Document(f)
    text = "\n".join(p.text for p in d.paragraphs)
    title_ok = d.paragraphs[0].style.name == "Title" and d.paragraphs[0].text == "Services Agreement"
    h = [p for p in d.paragraphs if p.style.name == "Heading 1"]
    conf = [p for p in h if "confidential" in p.text.lower()]
    fees = [p for p in d.paragraphs if "per month" in p.text]
    italic = bool(fees) and all(r.italic for r in fees[0].runs if r.text.strip())
    order = [p.text for p in h]
    after_law = conf and order.index(conf[0].text) > max(i for i, t in enumerate(order) if "Governing" in t)
    ok = ("Acme" not in text and text.count("Globex") >= 3 and "4,500" in text and "4,000" not in text
          and title_ok and bool(conf) and after_law and italic and len(h) == 7)
    return ok, (f"acme_left={'Acme' in text} globex={text.count('Globex')} fee={'4,500' in text} title_ok={title_ok} "
                f"headings={len(h)} confidentiality={bool(conf)} after_law={bool(after_law)} fees_italic_kept={italic}")


def g_invoices(w, v, before):
    import openpyxl
    f, named = find(w, "invoices.xlsx", ".xlsx", before)
    said = bool(re.search(r"812\.50?\b", reply(v)))
    if not f:
        return False, f"no .xlsx said_total={said}"
    vals = openpyxl.load_workbook(recalc(f), data_only=True)
    nums = [c.value for ws in vals for row in ws.iter_rows() for c in row if isinstance(c.value, (int, float))]
    rows = sum(1 for ws in vals for row in ws.iter_rows() if any(isinstance(c.value, str) and "INV-" in c.value for c in row))
    total = any(abs(n - 812.5) < 0.01 for n in nums)
    return total and said and rows >= 6, f"{f.name} named={named} invoice_rows={rows} total_in_sheet={total} said_total={said}"


def g_pdf(w, v, before):
    from pypdf import PdfReader
    f, named = find(w, "contract.pdf", ".pdf", before)
    if not f:
        return False, "no pdf"
    t = "".join(p.extract_text() or "" for p in PdfReader(f).pages)
    return "Services Agreement" in t and "Governing law" in t, f"{f.name} named={named} chars={len(t)}"


def g_letters(w, v, before):
    import docx
    new = [p for p in w.rglob("*.docx") if p not in before and ".run" not in p.parts]
    want = {"Bluebird Cafe": "205", "Harbor Books": "420", "Pine Dental": "187.5"}
    hit = {}
    for p in new:
        t = "\n".join(x.text for x in docx.Document(p).paragraphs) + "\n".join(
            c.text for tb in docx.Document(p).tables for r in tb.rows for c in r.cells)
        for k, tot in want.items():
            if k in t and tot in t.replace(",", ""):
                hit[k] = p.name
    return len(hit) == 3 and len(new) >= 3, f"new_docx={len(new)} correct_letters={len(hit)}/3"


WORK = [
    ("proposal", None, "Write a project proposal as a Word document, proposal.docx, for a 3-month project to build a "
     "customer portal: title, executive summary, goals as bullet points, a timeline table (phase, weeks, "
     "deliverable), a budget table with a total, risks, and next steps.", g_proposal),
    ("sales_xlsx", "office", "From sales.csv, make sales_report.xlsx: a Data sheet with the raw data, and a Summary "
     "sheet with revenue (units x unit price), cost and margin by region using live formulas, money formatted as "
     "currency, a chart of revenue by region, and a frozen header row.", g_sales_xlsx),
    ("deck", "office", "Make a 6 to 8 slide PowerPoint deck, sales_deck.pptx, presenting sales.csv to leadership: a "
     "title slide, the key numbers, a chart of revenue by month, a region comparison, and recommendations.", g_deck),
    ("contract", "office", "In contract.docx, change the client from Acme Corp to Globex Inc everywhere, change the "
     "fee to 4,500 USD, and add a new clause \"7. Confidentiality\" after Governing law. Keep the existing "
     "formatting and save over the same file.", g_contract),
    ("invoices", "office", "Read the three invoice PDFs and make invoices.xlsx with one row per invoice line "
     "(invoice, customer, item, qty, unit price, amount) and a total row. Tell me the grand total.", g_invoices),
    ("pdf", "office", "Make a PDF copy of contract.docx.", g_pdf),
    ("letters", "office", "Write a short thank-you letter as a separate Word file for each customer in the invoice "
     "PDFs, naming their invoice number and the invoice total.", g_letters),
]

HARN = {"code": [("meshpool-code", "code"), ("pi", "code"), ("hermes", "cowork"), ("meshpool-auto", "code")],
        "work": [("meshpool-work", "cowork"), ("hermes", "cowork"), ("pi", "code"), ("meshpool-auto", "cowork")]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("out")
    ap.add_argument("--only")
    ap.add_argument("--harness")
    ap.add_argument("--case")
    ap.add_argument("--budget", type=float, default=3.9, help="stop when the key's total spend reaches this")
    ap.add_argument("--limit", type=int, default=480)
    ap.add_argument("--regrade", action="store_true", help="grade the existing work folders again; no model calls")
    a = ap.parse_args()
    if a.regrade:
        out = Path(a.out)
        rows = [json.loads(x) for x in open(out / "results.jsonl")]
        G = {cid: g for cid, _, _, g in CODE + WORK}
        for r in rows:
            if a.case and r["case"] not in a.case.split(","):
                continue
            w = out / "work" / f"{r['case']}-{r['harness']}"
            v = {"events": [{"type": "assistant", "text": r.get("reply", "")}]}
            ok, detail = G[r["case"]](w, v, set())
            r["ok"], r["detail"] = bool(ok), detail
            print(f"{'PASS' if ok else 'FAIL'} {r['case']} [{r['harness']}] {detail}")
        (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = client(a.base)
    for suite, cases in (("code", CODE), ("work", WORK)):
        if a.only and a.only != suite:
            continue
        for cid, fix, prompt, grade in cases:
            if a.case and cid not in a.case.split(","):
                continue
            for hid, kind in HARN[suite]:
                if a.harness and hid not in a.harness.split(","):
                    continue
                s0 = spend()
                if s0 is not None and s0 >= a.budget:
                    print("budget reached", s0, flush=True)
                    return
                w = out / "work" / f"{cid}-{hid}"
                shutil.rmtree(w, ignore_errors=True)
                if fix:
                    shutil.copytree(FIX / fix, w)
                else:
                    w.mkdir(parents=True)
                sh("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init -q", w)
                before = set(w.rglob("*"))
                v, asked, secs = run_turn(c, hid, kind, prompt, w, a.limit)
                time.sleep(20)  # the gateway posts spend a little after the call
                s1 = spend()
                try:
                    ok, detail = grade(w, v, before)
                except Exception as e:  # noqa: BLE001
                    ok, detail = False, f"grader error {type(e).__name__}: {e}"
                errs = [e.get("text") for e in v.get("events", []) if e.get("type") == "error"]
                row = {"suite": suite, "case": cid, "harness": hid, "ok": bool(ok), "secs": round(secs, 1),
                       "cost": round(s1 - s0, 5) if s0 is not None and s1 is not None else None,
                       "approvals": asked, "usage": v.get("usage"), "timed_out": bool(v.get("timed_out")), "errors": errs[:3],
                       "detail": detail, "reply": reply(v)[-600:]}
                with open(out / "results.jsonl", "a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(f"{'PASS' if ok else 'FAIL'} {suite}/{cid} [{hid}] {secs:.0f}s ${row['cost']} {detail}"
                      + (f" ERR {errs[:1]}" if errs else ""), flush=True)


if __name__ == "__main__":
    main()
