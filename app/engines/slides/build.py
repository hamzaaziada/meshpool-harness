"""CLI: python -m engines.slides.build deck.json out.pptx [--slides N] -> validate, render, QA. Exit 1 on any issue."""
import json
import sys

from engines.slides import qa
from engines.slides.renderer import RenderError, render

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: python -m engines.slides.build deck.json out.pptx")
    deck = json.load(open(sys.argv[1]))
    try:
        r = render(deck, sys.argv[2])
    except RenderError as e:
        print("SPEC/RENDER ERRORS:\n- " + str(e).replace("; ", "\n- "))
        sys.exit(1)
    issues = qa.check(sys.argv[2], deck.get("requested_slide_count"))
    for w in r["warnings"]:
        print("STYLE:", w)
    if issues:
        for i in issues:
            print(f"QA slide {i['slide']}: {i['check']} {i['detail']}")
        sys.exit(1)
    print(f"PASS: {r['slides']} slides rendered to {sys.argv[2]}, QA clean, {len(r['warnings'])} style warnings")
