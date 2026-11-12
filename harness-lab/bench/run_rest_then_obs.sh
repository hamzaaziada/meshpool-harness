#!/bin/bash
# After a container restart: finish the frozen Verified test set, then run the observability A/B.
cd "$(dirname "$0")"
D=/tmp/claude-0/-home-user-meshpool-harness/ce2fe6c8-dd03-5938-a04c-c06b54de422f/scratchpad/ver/verified.json
python3 ver_bench.py --data $D --ids "$(cat /tmp/claude-0/rest_ids.txt)" --arms app,pi,codex,v5 \
  --out results/ver_test_r1.jsonl --budget 3 >> results/ver_test_r1.log 2>&1
python3 ver_bench.py --data $D \
  --ids sphinx-doc__sphinx-10449,pytest-dev__pytest-7490,django__django-15098,django__django-14771,scikit-learn__scikit-learn-10297,django__django-12774,scikit-learn__scikit-learn-15100,django__django-11066,django__django-12708,pydata__xarray-3677 \
  --arms v5,v5o,v5x,app,appx --out results/ver_dev_obs1.jsonl --budget 3 > results/ver_dev_obs1.log 2>&1
