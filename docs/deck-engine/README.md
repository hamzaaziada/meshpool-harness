# Forge: consulting-grade decks and workbooks for the Meshpool agent

Forge is the Meshpool agent's deck engine and its deck/workbook quality layer. The model writes structured content only. A
deterministic renderer builds native OOXML, and deterministic validators decide whether a file ships. That way the
quality doesn't depend on which model writes the content.

**Where it stands (13 Nov 2026):**

- The design system (`design-grammar.md`), renderer, builder, validators, measurers and schemas are built and tested.
- Exact rebuilds of McKinsey reference pages and content-only builds on two topics pass the render check with no overlap or overflow.
- The first one-shot rebuild through the previous slides engine failed (`mckinsey-rebuild-test.md`); forge v2 replaced that engine.
- Every forge deck, the agent's Word documents and recalculated workbooks validate against the Office schemas
  (P0/X0), so PowerPoint, Word and Excel should open them without the repair prompt.
- No Hebbia file has been measured yet, because none was supplied.

| Document | What it holds |
|---|---|
| [design-grammar.md](design-grammar.md) | the design system every deck gets, measured on McKinsey reference pages, and what keeps it from breaking |
| [engine-spec.md](engine-spec.md) | slide JSON schema, 28-archetype library, renderer contract, workbook block schema, all rules (P0–P20, X0–X13) |
| [pipeline-spec.md](pipeline-spec.md) | stage contracts, toolkit with versions and licences, Excel → dashboard → PBIP, cloud packaging |
| [hebbia-deck-anatomy.md](hebbia-deck-anatomy.md) | the measurement template for Hebbia files, pipeline hypotheses H1–H6, our deck measured the same way |
| [mckinsey-rebuild-test.md](mckinsey-rebuild-test.md) | the one-shot rebuild test, results and the bar to pass |
| [findings-ledger.md](findings-ledger.md) | every finding with label and Evidence IDs |
| [open-questions.md](open-questions.md) | exact Hebbia demo requests, open questions, next steps, step 1–5 checklist |
| [scorecard.csv](scorecard.csv) | every file × every rule (ours, references; Hebbia and comparators "not_supplied") |
| [reference-measurements.csv](reference-measurements.csv) | 17 public consulting decks measured (1,186 pages) |
| [visual-rubric.csv](visual-rubric.csv) | blind reviewer scores |

## Use it

Every deck request to the Meshpool agent uses forge: `meshpool run "…" --engine slides` (or `--engine forge`) and
Meshpool Work's office task both go through it. There is no other deck engine: a forge failure is reported as an
error.

From the command line (in `app/`):

```bash
python -m engines.forge.build brief.md out_dir --slides 10          # brief -> checked deck.pptx
python -m engines.forge.validate_pptx deck.pptx --text [--brief brief.json --provenance provenance.json]
python -m engines.forge.validate_xlsx model.xlsx --text [--recompute recompute.json --before old.xlsx]
python -m engines.forge.ooxml_check deck.pptx report.docx model.xlsx   # opens in Office without repair?
python -m engines.forge.ooxml_check --install-schemas                   # once: fetch the ECMA-376 schemas
python -m engines.forge.measure_pptx deck.pptx --summary
python -m engines.forge.measure_pdf reference.pdf [--pages 53-76]
python -m engines.forge.rebuild_eval reference.pdf out_dir --slides 12
python -m engines.forge.scorecard manifest.json scorecard.csv
python -m pytest tests/test_forge.py
```

Thresholds: `app/engines/forge/rules.yaml`. Pass a client override with `--rules client.yaml`.
