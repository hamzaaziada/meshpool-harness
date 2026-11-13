#!/bin/bash
# Resume after a container restart, inside the owner's $4 total for this key: finish the observability A/B, then as
# many remaining test tasks as fit under a $3.65 total key spend (one task can overshoot a stop by about $0.22).
cd "$(dirname "$0")"
D=/tmp/claude-0/-home-user-meshpool-harness/ce2fe6c8-dd03-5938-a04c-c06b54de422f/scratchpad/ver/verified.json
DONE=$(python3 -c "import json; print(','.join(sorted({json.loads(l)['task'] for l in open('results/ver_dev_obs1.jsonl')})))" 2>/dev/null)
IDS=$(python3 -c "
done=set('$DONE'.split(','))
ids='sphinx-doc__sphinx-10449,pytest-dev__pytest-7490,django__django-15098,django__django-14771,scikit-learn__scikit-learn-10297,django__django-12774,scikit-learn__scikit-learn-15100,django__django-11066,django__django-12708,pydata__xarray-3677'.split(',')
print(','.join(i for i in ids if i not in done))")
[ -n "$IDS" ] && python3 ver_bench.py --data $D --ids "$IDS" --arms v5,v5o,v5x,app,appx \
  --out results/ver_dev_obs1.jsonl --budget 1.6 >> results/ver_dev_obs1.log 2>&1
LEFT=$(python3 -c "from spend import spend; s=spend() or 9; print(max(0.0, round(3.65 - s, 2)))")
echo "test budget left: \$$LEFT" >> results/ver_test_r1.log
python3 -c "import sys; sys.exit(0 if $LEFT > 0.25 else 1)" && python3 ver_bench.py --data $D \
  --ids "$(cat /tmp/claude-0/rest_ids.txt)" --arms app,pi,codex,v5 --out results/ver_test_r1.jsonl \
  --budget $LEFT >> results/ver_test_r1.log 2>&1
