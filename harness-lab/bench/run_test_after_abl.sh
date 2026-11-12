#!/bin/bash
# Start the frozen Verified test set once the dev ablation has finished (keeps load and disk moderate).
while pgrep -f ver_dev_abl1 >/dev/null; do sleep 30; done
cd "$(dirname "$0")"
python3 ver_bench.py --data /tmp/claude-0/-home-user-meshpool-harness/ce2fe6c8-dd03-5938-a04c-c06b54de422f/scratchpad/ver/verified.json \
  --ids django__django-11532,pydata__xarray-4695,sympy__sympy-16450,django__django-13821,scikit-learn__scikit-learn-25102,pydata__xarray-3151,sympy__sympy-20154,pylint-dev__pylint-4970,sphinx-doc__sphinx-8621,django__django-13363,django__django-12741,astropy__astropy-8872,django__django-13516,scikit-learn__scikit-learn-12682,sympy__sympy-13031,django__django-15572,django__django-12308,django__django-15161,django__django-14500,pydata__xarray-4094 \
  --arms app,pi,codex,v5 --out results/ver_test_r1.jsonl --budget 4 > results/ver_test_r1.log 2>&1
