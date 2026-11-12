"""CLI: python -m engines.sheets.check file.xlsx  -> prints PASS or the issues; exit 1 on issues."""
import json
import sys

from engines.sheets.qa import check

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m engines.sheets.check <file.xlsx>")
    r = check(sys.argv[1])
    if r["issues"]:
        for i in r["issues"][:40]:
            print(f"{i['check']}\t{i['cell']}\t{i['detail']}")
        print(f"FAIL: {len(r['issues'])} issues; stats {json.dumps({k: v for k, v in r['stats'].items() if k != 'recalculated_copy'})}")
        sys.exit(1)
    print(f"PASS: {r['stats']['formulas']} formulas, sheets {r['stats']['sheets']}, named ranges {r['stats']['named_ranges']}")
