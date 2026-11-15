# Teardown: para-lost/AutoPresent

Pinned commit `98e0c012e89469863d9c3c8bc87eac967d82b2e6`. The paper's code: an LLM writes python-pptx programs per slide, optionally using SlidesLib helpers. It also includes SlidesBench, refinement, and a fine-tuned Llama-3.1-8B. Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Isolated venv (`uv`, Python 3.12): `requirements.txt` plus `pip install -e .` (SlidesLib).
- Install problems found:
  - `SlidesLib` does not import as shipped. `SlidesLib/__init__.py` imports `mysearchlib`, which exists nowhere in the repo; the only other mention is a string replace in `scripts/clean_generated_code.py`.
  - Undeclared dependencies: `chromedriver_py`, `google_images_download`, `bing_image_downloader`, `transformers` (for BLIP VQA), `matplotlib`.
  - I installed those (no torch) and added a one-line shim `mysearchlib.py` (`from SlidesLib.llm import LLM`) on `PYTHONPATH`, outside the repo.
  - A stub `unoconv` on PATH, because the repo shells out to `unoconv` after each slide.
- Generation: `generate/create_slide.py` per slide, in the "creative" (high-level instruction) setting with the SlidesLib docs (`library/library.txt`, `prompt/example_hl_wlib.txt`), `--no_image`, `--num_samples 1`.
  - Model: `deepseek-v4.1-flash` through `OPENAI_BASE_URL`.
  - I wrote one high-level instruction per slide from the Costco brief (`slidesbench/examples/costco/slide_N/instruction_high_level.txt`), as SlidesBench does.
- Not run:
  - Refinement (`refinement/refine.py`) sends the rendered slide image to a vision model, which the budget rule excludes.
  - The AutoPresent 8B model needs a GPU.
  - Web search and DALL-E helpers are blocked or out of scope.
- Then ran forge `validate(render=True)` on each produced slide.

## What it does well

- [observation] The idea is simple and flexible: the model writes free python-pptx code against a short helper API (`add_title`, `add_text`, `add_bullet_points`, `set_background_color`). When code runs, the result can be visually rich. Slide 1 was a dark KPI-card cover with all six facts; slide 3 had valuation cards plus a "if all cash were used" panel with correct arithmetic (18.3M shares, 4.1%, 53.1x, EPS $20.83).
- [inference] SlidesBench (10 domains, 585 reference slides with instructions, `slidesbench/`) and its reference-based and reference-free scorers (`evaluate/`) could serve as a secondary visual-design benchmark for forge.

## Where it breaks

- [observation] **2 of 5 slides executed.**
  - Slide 2: the library's `add_title` assumes a title placeholder; on a blank layout `slide.shapes.title` is `None` → `AttributeError` (`generate/library/library_basic.py` L23).
  - Slide 4: `IndexError` in the model's own table loop.
  - Slide 5: `extract_code_pieces` (`generate/utils.py`) concatenates every ```` ```python ```` block, including a shell line (`python -c "exec(...)"`), giving a `SyntaxError`.
- [observation] Retrying (the paper's 3-sample default, done as 2 extra single-sample runs) returned identical failures at zero added spend. The gateway appears to cache identical prompts, so resampling at temperature default did nothing here.
- [observation] The two slides that ran:
  - Slide 1: title clipped at the top edge (P1).
  - Slide 3: title not visibly rendered, KPI-card labels missing, footnote below the slide bottom (P5 `autogrow_off_slide`).
  - Topic titles ("Buyback Impact at Today's Valuation", P11) and overlaps (P8).
  - Every colour is RGB; 16×9 in custom slide size.
- [observation] No deck concept: each slide is a separate `Presentation` saved to its own file, with no shared grid, title position or theme across slides.
- [observation] SlidesLib depends on Selenium + Google screenshots, Bing image download, DALL-E, and BLIP VQA. None is usable offline, and none fits a client-data setting.

## What we take

1. [inference] **SlidesBench as an eval set only**: its reference decks and instructions (`slidesbench/examples/*/slide_*/instruction*.txt`), plus the reference-free design metrics in `evaluate/`. Run forge on the same instructions and compare. Lives outside the forge code (eval harness). Low priority, since the benchmark's slides are generic rather than consulting decks.
2. [observation → do not take] Free-form code generation per slide. It contradicts forge's "model writes structured content only" contract (spec §1.1), and this run shows why: a 60% execution failure rate with a flash model.
3. [observation] Regression idea: forge's content extraction should never concatenate multiple fenced blocks. Parse exactly one JSON object per slide and reject otherwise. Check `engine.py` (model output parsing).

## Licence note

MIT (per the ledger). SlidesBench's slide decks come from the web; [unknown] the licence of individual reference decks, so check before redistributing them.
