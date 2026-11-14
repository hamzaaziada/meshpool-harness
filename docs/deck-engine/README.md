# Forge: consulting-grade decks and workbooks for the Meshpool agent

Forge is the Meshpool agent's deck engine and its deck/workbook quality layer. The model writes structured content only. A
deterministic renderer builds native OOXML, and deterministic validators decide whether a file ships. That way the
quality doesn't depend on which model writes the content.

| Document | What it holds |
|---|---|
| [design-grammar.md](design-grammar.md) | the design system every deck gets, and what keeps it from breaking |
| [engine-spec.md](engine-spec.md) | slide JSON schema, archetype library, renderer contract, workbook block schema, all rules (P0–P20, X0–X14) |

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
python -m pytest tests/test_forge.py
```

Thresholds: `app/engines/forge/rules.yaml`. Pass a client override with `--rules client.yaml`.
