# Teardown: opendatalab/MinerU

Commit `f0497e0c6e9575fa553ce57cc9aa867906a00572` (2026-11-13). Licence: Apache-2.0 **plus additional terms**
(`LICENSE.md`). Read only; not installed. Labels: [observation] seen in the repo; [inference] my reading; [unknown] not
established.

## Tested setup

Not installed (the brief said install only if cheap; MinerU's default tiers download 0.8-3 GB of models and its CPU
path for the better tiers runs a llama.cpp VLM). Read `README.md`, `LICENSE.md`, `pyproject.toml`, `docker/compose.yaml`,
`mineru/cli/telemetry.py`, `mineru/tasks/http.py`, `mineru/kit/`. [observation]

## How it would run as an isolated service

- Entry points (`pyproject.toml [project.scripts]`): `mineru` (CLI, talks to a local background service:
  `mineru server start`), `mineru-api` (FastAPI task server: `POST /tasks` returns 202 + task id, `GET /tasks/{id}`,
  `POST /tasks/{id}/cancel` in `mineru/tasks/http.py`), `mineru-openai-server` (a VLM behind an OpenAI-compatible
  endpoint), `mineru-router` (worker pool admin), `mineru-models-download`. [observation]
- Docker: `docker/compose.yaml` defines `mineru-api` (port 8000) and `mineru-openai-server` (port 30000, NVIDIA GPU
  reservation) from an image `mineru:4`, with `MINERU_MODEL_SOURCE: local`; country-specific Dockerfiles under
  `docker/china/` for other accelerators. [observation]
- Model tiers (README): `basic` ONNX ~0.8 GB, 2 GB RAM, CPU works; `standard`/`advanced` ONNX + llama.cpp ~2 GB, 8 GB RAM,
  "CPU works, Vulkan recommended"; PyTorch/vLLM variants want a GPU with 8 GB+ VRAM. [observation]
- **Telemetry is on by default** ("Doclib telemetry is enabled by default: locally aggregated usage, coarse environment
  categories, and performance metrics", `mineru/cli/telemetry.py`); disable with `MINERU_TELEMETRY_DISABLED=1` before
  first start. [observation]

Recommended shape for us [inference]: a separate container (`mineru-api`, `basic` or `standard` tier, models baked into
the image with `mineru-models-download` at build time, `MINERU_MODEL_SOURCE=local`, `MINERU_TELEMETRY_DISABLED=1`, no
outbound network at run time), called over HTTP from `meshpool_harness/docread.py` only as an optional fallback for
scans and complex layouts. It must never be imported into the harness process: its dependency tree (onnxruntime,
llama.cpp, optional torch/vLLM) is large and its licence terms differ from Apache-2.0.

## What it does well

- Async task API with cancellation suits long PDFs; a router for multiple workers. [observation]
- CPU-capable tiers and automatic accelerator detection. [observation]
- [unknown] Table and reading-order quality on our documents: not measured here (see docling and marker notes for
  measured parsers).

## Where it breaks (for us)

- Licence conditions beyond Apache-2.0 (below). [observation]
- Default-on telemetry. [observation]
- Heavy models; the VLM tiers are slow without a GPU (README: Vulkan/GPU recommended). [observation]

## What we take

Only the service pattern (async task endpoint + cancel, models baked in, telemetry off). Keep MinerU as a candidate
fallback parser behind an HTTP boundary; evaluate on scanned Egyptian filings only if docling's OCR proves weak.

## Licence note (what the ledger terms mean for us)

Ledger: "Apache-2.0 plus extra terms: commercial licence above 100M MAU or $20M monthly revenue; attribution
required". From `LICENSE.md` at the pin [observation]:
1. Commercial use is allowed without a separate licence **unless** we and our affiliates, consolidated, exceed
   100 million monthly active users **or** USD 20 million total monthly revenue; then a separate commercial licence is
   needed before continuing.
2. **Attribution**: if we provide an online service to third parties based on MinerU, we must state clearly and
   prominently in the product interface or public documentation that MinerU is used.
3. Breaking either term terminates the licence automatically.
For us [inference]: usable today as an internal or hosted parser if we add a "uses MinerU" line to public docs; the
revenue threshold is consolidated across affiliates, so check it again before any acquisition or large client; keep it
isolated so it can be removed without code changes elsewhere. Model weights may carry their own terms [unknown: not
checked per model].
