#!/bin/bash
# A/B of the observability tools on Verified dev, once the frozen test set has finished.
while pgrep -f "ver_test_r1|run_test_after_abl" >/dev/null; do sleep 30; done
cd "$(dirname "$0")"
python3 ver_bench.py --data /tmp/claude-0/-home-user-meshpool-harness/ce2fe6c8-dd03-5938-a04c-c06b54de422f/scratchpad/ver/verified.json \
  --ids sphinx-doc__sphinx-10449,pytest-dev__pytest-7490,django__django-15098,django__django-14771,scikit-learn__scikit-learn-10297,django__django-12774,scikit-learn__scikit-learn-15100,django__django-11066,django__django-12708,pydata__xarray-3677 \
  --arms v5,v5o,v5x,app,appx --out results/ver_dev_obs1.jsonl --budget 3 > results/ver_dev_obs1.log 2>&1
