# Forge: Hebbia-grade decks and workbooks for Meshpool Work

Forge is the deck and workbook quality layer of Meshpool Work (Cowork). The model writes structured content only. A
deterministic renderer builds native OOXML, and deterministic validators decide whether a file ships. That way the
quality doesn't depend on which model writes the content.

**Where it stands (13 Nov 2026):**

- The validators, measurers, schemas and Cowork tools are built and tested.
- The current slides renderer does not yet meet the spec.
- The McKinsey one-shot rebuild test fails today, mainly on source lines, exhibit depth and type.
- No Hebbia file has been measured yet, because none was supplied.

| Document | What it holds |
|---|---|
| [engine-spec.md](engine-spec.md) | slide JSON schema, 28-archetype library, renderer contract, workbook block schema, all rules (P1–P19, X1–X13) |
| [pipeline-spec.md](pipeline-spec.md) | stage contracts, toolkit with versions and licences, Excel → dashboard → PBIP, cloud packaging |
| [hebbia-deck-anatomy.md](hebbia-deck-anatomy.md) | the measurement template for Hebbia files, pipeline hypotheses H1–H6, our deck measured the same way |
| [mckinsey-rebuild-test.md](mckinsey-rebuild-test.md) | the one-shot rebuild test, results and the bar to pass |
| [findings-ledger.md](findings-ledger.md) | every finding with label and Evidence IDs |
| [open-questions.md](open-questions.md) | exact Hebbia demo requests, open questions, next steps, step 1–5 checklist |
| [scorecard.csv](scorecard.csv) | every file × every rule (ours, references; Hebbia and comparators "not_supplied") |
| [reference-measurements.csv](reference-measurements.csv) | 17 public consulting decks measured (1,186 pages) |
| [visual-rubric.csv](visual-rubric.csv) | blind reviewer scores |

## Use it

In Meshpool Work, three tools on the `meshpool-office` MCP server are read-only and run without asking:

- `check_deck(path, slide_count?)`: rules P1–P19, each failure written as a fix instruction;
- `check_workbook(path)`: rules X1–X13 after a headless recalculation;
- `measure_office_file(path, pages?)`: how a .pptx, .xlsx or .pdf is built.

Every `make_deck` / `make_spreadsheet` job report now ends with a forge QA line.

From the command line (in `app/`):

```bash
python -m engines.forge.validate_pptx deck.pptx --text [--brief brief.json --provenance provenance.json]
python -m engines.forge.validate_xlsx model.xlsx --text [--recompute recompute.json --before old.xlsx]
python -m engines.forge.measure_pptx deck.pptx --summary
python -m engines.forge.measure_pdf reference.pdf [--pages 53-76]
python -m engines.forge.rebuild_eval reference.pdf out_dir --slides 12
python -m engines.forge.scorecard manifest.json scorecard.csv
python -m pytest tests/test_forge.py
```

Thresholds: `app/engines/forge/rules.yaml`. Pass a client override with `--rules client.yaml`.
