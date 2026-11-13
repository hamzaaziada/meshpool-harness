"""Deterministic router rules. Decides most requests with no model call.

Output: (engines: list[str], confidence: float, reason: str). Engines in chain order.
Chain order when several output kinds are requested: research -> code -> data -> sheets -> slides (research collects
outside sources first; the deliverable engines then build on its sourced report and sources.json).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ORDER = ["research", "code", "data", "sheets", "slides"]

EXT = {
    "slides": {".pptx", ".ppt", ".key", ".odp"},
    "sheets": {".xlsx", ".xlsm", ".xls", ".ods"},
    "data": {".csv", ".tsv", ".parquet", ".json", ".jsonl", ".sav", ".dta", ".feather", ".txt", ".docx", ".pdf", ".md", ".vtt", ".srt"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".cc", ".cpp", ".h",
             ".cs", ".swift", ".scala", ".sh", ".sql", ".toml", ".lock", ".gradle", ".yaml", ".yml", ".ipynb"},
}

# (pattern, weight). Output-format words weigh most because they name the deliverable.
OUT = {
    "slides": [(r"\b(deck|slides?|slideshow|powerpoint|pptx|presentation|keynote|pitch ?deck|board ?pack)\b", 3.0)],
    "sheets": [(r"\b(spreadsheet|excel|xlsx|workbook|google sheets?|financial model|three[- ]statement|"
                r"budget (model|template)|tracker|cap table|dcf|p&l model|pivot table)\b", 3.0)],
    "data": [(r"\b(analy[sz]e|analysis|statistic(s|al)?|regression|correlat\w*|significan\w*|distribution|"
              r"trend|cohort|segment\w*|cluster\w*|forecast\w*|a/b test|hypothesis|chart|plot|visuali[sz]\w*|"
              r"insights?|summari[sz]e (the )?(data|survey|interviews?|responses|feedback)|themes?|thematic|"
              r"coding (the )?(transcripts?|interviews?|responses)|codebook|sentiment|survey|interviews?|"
              r"transcripts?|nps|churn rate|dataset|csv)\b", 1.5)],
    "code": [(r"\b(bug|fix(es|ing)?|failing test|tests? (fail|pass)|stack ?trace|traceback|exception|segfault|"
              r"refactor\w*|implement\w*|function|class|method|endpoint|api|repo(sitory)?|pull request|"
              r"\bpr\b|commit|compile|build (fails|error|is broken)|lint|type ?error|unit tests?|cli|script|"
              r"dockerfile|makefile|ci pipeline|terminal|bash|shell command|install|dependency|npm|pip)\b", 1.5)],
}
# research: the request needs outside sources (the web, filings) rather than the attached files alone
OUT["research"] = [
    (r"\b(research (report|brief|memo|note|summary)|sourced (report|memo|brief)|with (sources|citations)|"
     r"cite (your |the )?sources|primer on|deep dive (on|into)|industry (overview|primer)|due diligence on|"
     r"market (map|landscape|sizing)|competitive landscape)\b", 3.0),
    (r"\b(research|investigate|find out|look into|dig into|diligence|landscape|competitors?|industry (analysis|"
     r"report|trends)|market (size|share|outlook|trends)|tam\b|what('s| is| are) (the )?(latest|current)|"
     r"latest (news|developments)|recent developments|earnings (call|release|results)|guidance|10-k|10-q|"
     r"annual report|filings?|consensus|peers?|precedent transactions)\b", 1.5)]
# phrases that look like one engine but are not ("data" in "data pipeline code" is code)
NEG = {
    "data": [r"\bdata (pipeline|loader|class|model) (code|bug|test)", r"\bcsv (parser|reader|writer) (bug|code)"],
    "sheets": [r"\b(read|parse|write|load|open) (an? |this |the )?(excel|xlsx) (file )?(in|with|using) (python|pandas|openpyxl)"],
}
CREATE = (r"\b(make|create|build|generate|produce|put together|prepare|draft|design|set up|update|add|turn|"
          r"convert|give me|need|want|write)\b")
TABULAR = {".csv", ".tsv", ".parquet", ".json", ".jsonl", ".xlsx", ".xls", ".feather"}


def _wants(e: str, t: str) -> bool:
    """True if the request asks to PRODUCE engine e's artifact (a creation verb before the artifact word),
    not merely mentions one as an input ('from this tracker', 'what does this spreadsheet say')."""
    pat = OUT[e][0][0]
    for m in re.finditer(pat, t):
        before = t[max(0, m.start() - 80):m.start()]
        if re.search(r"\b(from|using|in) (this|the|my|our|that) [\w ]{0,15}$", before):
            continue
        if re.search(CREATE, before) or re.search(r"\b(in|as|into) (an? )?$", before):
            return True
    return False


CODE_INTENT_VERBS = r"\b(fix|debug|refactor|implement|patch|write a (python |bash )?(script|function)|add (a )?test)\b"


def _score_text(text: str) -> dict[str, float]:
    t = text.lower()
    sc = {e: 0.0 for e in ORDER}
    for e, pats in OUT.items():
        for p, w in pats:
            sc[e] += w * len(re.findall(p, t))
    for e, pats in NEG.items():
        for p in pats:
            if re.search(p, t):
                sc[e] = max(0.0, sc[e] - 3.0)
                sc["code"] += 2.0
    return sc


def _repo_signal(workdir: str | None) -> bool:
    if not workdir or not os.path.isdir(workdir):
        return False
    markers = (".git", "pyproject.toml", "setup.py", "package.json", "go.mod", "Cargo.toml", "pom.xml", "Makefile")
    return any(os.path.exists(os.path.join(workdir, m)) for m in markers)


def decide(request: str, files: list[str] | None = None, output_format: str | None = None,
           workdir: str | None = None) -> tuple[list[str], float, str]:
    files = files or []
    t = request.lower()
    sc = _score_text(request)
    reasons = []

    # explicit output format beats everything
    fmt = (output_format or "").lower().lstrip(".")
    fmt_map = {"pptx": "slides", "ppt": "slides", "deck": "slides", "slides": "slides", "xlsx": "sheets",
               "xls": "sheets", "excel": "sheets", "sheets": "sheets", "csv": "data", "report": "data",
               "md": "data", "patch": "code", "diff": "code"}
    if fmt in fmt_map:
        sc[fmt_map[fmt]] += 5
        reasons.append(f"output_format={fmt}")

    # attachments are inputs: they suggest what to read, not what to produce
    for f in files:
        ext = Path(f).suffix.lower()
        for e, exts in EXT.items():
            if ext in exts:
                sc[e] += 1.0 if e in ("slides", "sheets") else 1.2
                reasons.append(f"file {Path(f).name}->{e}")
    # an input spreadsheet/deck asked to be *analysed* is data work, unless a new workbook/deck is requested
    if any(Path(f).suffix.lower() in EXT["sheets"] for f in files) and re.search(r"\b(analy|insight|trend|why|explain)", t):
        sc["data"] += 1.5

    if files and sc["research"] < 3:              # attached files answer the question unless it asks for research
        sc["research"] = 0.0

    if _repo_signal(workdir):
        sc["code"] += 1.0 if re.search(CODE_INTENT_VERBS, t) else 0.4
        reasons.append("repo present")

    if any(Path(f).suffix.lower() in EXT["sheets"] for f in files) and not _wants("sheets", t) \
            and re.search(r"\b(what|why|which|how|tell us|explain|insight)", t):
        sc["sheets"] = 0.0
        sc["data"] += 2.0
    ranked = sorted(sc.items(), key=lambda kv: -kv[1])
    top_e, top = ranked[0]
    if top <= 0:
        return ["general"], 0.3, "no engine signal"

    # chain: every output-kind engine with a strong deliverable signal, plus data when it feeds a deliverable
    deliverables = [e for e in ("slides", "sheets") if (sc[e] > 0 and _wants(e, t)) or fmt_map.get(fmt) == e]
    chain = []
    if deliverables:
        analytic = sc["data"] >= 1.5 and re.search(r"\b(analy[sz]e|analysis|from (the|this|my) (survey|"
                                                    r"interviews?)|based on (the|this) (data|analysis)|themes?|"
                                                    r"statistic|regression|segment\w*|insights?|findings)", t)
        tabular_in = any(Path(f).suffix.lower() in TABULAR for f in files)
        if analytic or (tabular_in and deliverables == ["slides"] and not any(
                Path(f).suffix.lower() in EXT["sheets"] for f in files)):
            chain.append("data")
        if sc["code"] >= 3 and re.search(CODE_INTENT_VERBS, t):
            chain.insert(0, "code")
        if sc["research"] >= 1.5:
            chain.insert(0, "research")
        chain += deliverables
    else:
        chain = [top_e]
        if top_e == "data" and sc["code"] >= sc["data"] * 0.9 and re.search(CODE_INTENT_VERBS, t):
            chain = ["code"]
    chain = sorted(dict.fromkeys(chain), key=ORDER.index)

    second = ranked[1][1]
    margin = (top - second) / (top + 1e-9)
    conf = 0.55 + 0.4 * min(1.0, margin) + (0.05 if top >= 3 else 0)
    if len(chain) > 1:
        conf = max(conf, 0.75)
    conf = round(min(conf, 0.99), 2)
    reason = f"scores {dict((k, round(v, 1)) for k, v in sc.items())}; " + ", ".join(reasons[:4])
    return chain, conf, reason
