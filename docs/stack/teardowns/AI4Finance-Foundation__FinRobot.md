# Teardown: AI4Finance-Foundation/FinRobot

Commit `63e1884a42ea7b729551a5424b4bd6a65af636e0` (2026-10-24), Apache-2.0 (root LICENSE and NOTICE;
`finrobot_desktop/pyproject.toml` also says Apache-2.0). Tested 2026-11-15 by reading code.
Labels: [observation] seen in the code; [inference] my reading; [unknown] not established.

## Tested setup

Not run. The production part (`finrobot_desktop/`, "V2", PydanticAI + FastAPI + Tauri) needs FMP/Finnhub-style data
keys and a desktop build; V1 (`finrobot_equity/`) is a ticker-to-HTML/PDF report web app; V0 (`finrobot_autogen/`) is
the AutoGen code behind the 2024 paper. I read V2's pipelines, compute operators, audits, contracts and agent
instructions, and skimmed V0/V1. [observation]

## How it is built (V2)

- **Fixed pipelines of enforced steps**, not a free agent loop: `engine/pipelines/equity_research.py:create_equity_research_pipeline`
  = data_collection -> catalyst_analysis -> peer_analysis -> financial_modeling -> ownership_governance_analysis ->
  technical_analysis -> thesis -> report. Other pipelines: comps, dcf, ddm, lbo, earnings_analysis, ic_memo
  (situation_overview -> financial_analysis -> investment_thesis -> risk_factors -> recommendation). [observation]
- Each `PipelineStep` (`engine/pipelines/step.py`) has an agent, a **validator** (`engine/pipelines/validators.py`:
  non-empty, has fields, has >= 3 peers, has thesis, report format), required data, and a `deterministic` flag:
  deterministic steps skip the validation-retry loop (a re-run gives the same bytes) but keep the exception-retry
  loop for transient provider errors; a critical step that exhausts retries aborts the run (`PipelineStepError`).
  [observation]
- **Numbers are code, prose is LLM**: DCF, DDM, residual income, multiples, ownership, catalysts, sensitivity and
  market-implied checks are pure Python operators in `engine/compute/operators/`. [observation]
- **Number whitelist in the prompt + deterministic backstops**: the thesis prompt lists the numbers the model may cite
  (`engine/pipelines/_thesis_prompt.py`, "NARRATIVE ARGUMENT RULE: every paragraph ... MUST cite at least one number
  from this whitelist"); `engine/compute/operators/audit/narrative_numeric_grounding.py` flags paragraphs with no
  number (ignoring bare years and ordinals) as a warning; `equity_research.py:_reconcile_narrative_targets` rewrites
  any prose `$` amount that drifts more than `NARRATIVE_DRIFT_TOLERANCE` from the canonical target (unless it is a
  per-method mid or the market price) to the canonical value, or to "[target withheld]". [observation]
- **Whole-artifact contract** before saving (`artifact/contract.py`): pure `check(artifact) -> Finding | None` clauses,
  e.g. target/market ratio inside a calibrated band, methods corroborating each other, malformed amounts such as
  "$2172.062.06"; a hard failure withholds the value but keeps the verdict and stamps a warning, it never raises.
  [observation]
- **Debate agents with evidence ids**: `engine/agents/instructions/bull_agent.md` / `bear_agent.md`: "Every argument
  must attach at least one evidence_id ... You may not write any number yourself - to cite a number, cite its
  evidence_id and the render layer fills in the real value." `judge_agent.md` must give BUY/HOLD/SELL with conviction,
  a swing factor tied to a load-bearing assumption and a "change my mind" condition. At this commit no Python code
  references `evidence_id` or loads the bull/bear/judge instructions (grep of `finrobot_desktop`), so the debate is
  specified but not wired. [observation]
- **Report rules** (`instructions/report_agent.md`): fixed sections (executive summary, company overview, financial
  summary, peers, valuation, thesis, risks, appendix); "Uncertainty must survive into the report" (withheld targets,
  method disagreement, data warnings, simplified figures must be carried through); "no bare figures": every amount
  needs currency and units, every metric a period basis (LTM/TTM/NTM/FY1), else footnote it. [observation]
- **Audit transcript**: `audit/transcript.py` appends one JSON event per line to `~/.finrobot/sessions/<id>.jsonl`
  (user_msg, assistant_text, tool_call, tool_result, error), mode 0600, with a secret scrubber for api keys, bearer
  tokens and `sk-` keys before anything hits disk. [observation]
- **Untrusted text handling**: `engine/compute/coordinators/news.py:sanitize_untrusted_block` strips tags, leading
  markdown headings and control characters from news/10-K excerpts and callers wrap them in `<untrusted_*>` blocks
  (`UNTRUSTED_NEWS_PROMPT_NOTE`). [observation]
- **EDGAR without a paid key**: `engine/data/providers/edgar_provider.py` reads `companyfacts` for multi-year annual
  series (`_companyfacts_annual_series`, `_build_sec_yearly_financials`), XBRL segment members
  (`extract_segment_facts`) and builds RAG chunks from filing sections (`build_rag_chunks`). [observation]

## What it does well

- Clear split of responsibilities: deterministic compute, LLM narration, code audits that rewrite or withhold rather
  than trust. [observation]
- Degrade instead of crash, with the warning carried into the report. [observation]
- The "cite an id, never type a number" rule is the strongest anti-hallucination design among the repos reviewed;
  it removes number errors by construction instead of catching them afterwards. [inference]

## Where it breaks

- Equity-research shaped: built around price targets and valuation, not open questions like "what changed in capital
  allocation". [inference]
- Heavy: V2 is about 90k lines across three generations; the report pipeline depends on paid data providers (FMP etc.)
  for most inputs. [observation]
- The debate/evidence-id design is not implemented in code at this commit. [observation]
- Narrative grounding check only tests that a paragraph has some digit; it does not check the digit is right
  (documented as a light, non-blocking signal). [observation]

## What we lack

1. **Render numbers from fact ids.** Our dossier/forge facts already have ids (F12) and values; our engine report cites
   passages (S3.p12) and `verify.py` checks afterwards. Add a template form: the writer may write `{F12}` (or
   `{S3.p12#2}` for the 2nd number in a passage) and a renderer substitutes the stated figure with its unit and period.
   Pointers: `engines/research/dossier.py:to_markdown`/`brief_block` for facts; `engines/research/verify.py:check`
   gains a rule "a bare number not inside a placeholder or [calc:] is a problem". 
2. **Prior-period XBRL values.** `engines/forge/research.py:xbrl_facts` keeps only the latest value per tag. Add the
   same tag's value one fiscal year earlier (FinRobot's `_companyfacts_annual_series` idea) so "what changed" facts
   (dividends paid, buybacks, capex, LongTermDebt vs LongTermDebtNoncurrent vs DebtCurrent) are deterministic. In the
   gpt-researcher test, the paired DebtCurrent/LongTermDebtNoncurrent facts would have exposed the false
   "deleveraging" claim. [inference]
3. **Whole-report contract clauses** as pure functions: e.g. a claim of "reduced debt" must agree with the sign of the
   total-debt change; a period label on every figure; an Answer number that matches the body. Pointer: new clauses next
   to `verify.py:check` (rule list), kept zero-I/O.
4. **Uncertainty carry-through rule** in the engine's report instructions (`engines/research/engine.py:SYSTEM`, section
   8): figures we could not verify, withheld values and source conflicts must appear in "Conflicts and open questions"
   (we have the section; FinRobot's rule list is more explicit).
5. **Untrusted wrapping** of web text before it reaches prompts: `engines/forge/research.py:_pick_model` and
   `dossier.py:_chat_json` pass passage text inline; wrap it in a tagged block after stripping tags/headings.
6. Secret scrubbing in any research JSONL log we add (see dexter note).

## What we take

Ideas and, where useful, small functions (Apache-2.0): fact-id rendering, prior-period companyfacts series, pure
contract clauses, the "no bare figures" rule, untrusted-block sanitising, secret-scrubbed JSONL. Not the framework,
not the pipelines, not the paid-data providers.

## Licence note

Apache-2.0 at the pinned commit with a NOTICE file and a TRADEMARK_POLICY.md (ledger: "borrow ideas"). Copied code
must keep the licence header, carry NOTICE content and mark changes; do not use the FinRobot name or logo for our
product (trademark policy). [observation]
