"""Hold each text role to the density mode's number of font sizes (validator rule P4).

The renderer picks sizes slide by slide (the density scale, growing short text, shrinking long text to fit), so a
deck can end up with seven body sizes where a reader sees noise. After the deck is saved this pass measures it the
way the validator does and, for any role over its cap, keeps the smallest size plus the most used others and moves
every other size DOWN to the nearest kept one. Text never gets larger, so nothing that fitted can overflow."""
from __future__ import annotations

from collections import Counter

ROLES = ("body", "subtitle", "footnote")


def _plan(m: dict, R: dict) -> dict:
    """{(mode, role): {size: new_size}} for every role over its cap."""
    import re
    from .validate_pptx import slide_density
    caps = R["fonts"].get("max_sizes_per_role_by_density") or {}
    tag_re = re.compile(R["density"].get("mode_regex") or r"density:([a-z]+)")
    for s in m["slides"]:                                 # the mode the validator holds each slide to
        s["density"] = slide_density(s, R, None) if tag_re.search(s.get("name") or "") else None
    seen: dict = {}
    for s in m["slides"]:
        for role, c in (s.get("font_sizes") or {}).items():
            if role in ROLES:
                seen.setdefault((s.get("density"), role), Counter()).update(c)
    plan = {}
    for (mode, role), c in seen.items():
        cap = (caps.get(mode) or {}).get(role, R["fonts"]["max_sizes_per_role"])
        sizes = sorted(c)
        if len(sizes) <= cap or cap < 1:
            continue
        keep = {sizes[0]} | {z for z, _ in sorted(((z, n) for z, n in c.items() if z != sizes[0]),
                                                  key=lambda t: (-t[1], -t[0]))[:cap - 1]}
        plan[(mode, role)] = {z: max(k for k in keep if k <= z) for z in sizes if z not in keep}
    return plan


def harmonize(path: str) -> int:
    """Rewrite run sizes in place; returns how many runs changed (0 when every role is within its cap)."""
    from pptx import Presentation
    from pptx.util import Pt
    from .common import load_rules
    from .measure_pptx import measure
    R = load_rules()["pptx"]
    m = measure(path)
    plan = _plan(m, R)
    if not plan:
        return 0
    prs = Presentation(path)
    changed = 0

    def walk(shapes):
        for sh in shapes:
            yield sh
            if sh.shape_type == 6:                       # group
                yield from walk(sh.shapes)

    tf = R["text_fit"]
    for rec, slide in zip(m["slides"], prs.slides):
        roles = {x["id"]: x["role"] for x in rec.get("shapes", [])}
        body_floor = (tf.get("min_dense_pt", tf["min_body_pt"]) if rec.get("archetype") in tf.get("dense_archetypes", [])
                      else tf["min_body_pt"])
        floor = {"footnote": tf["min_footnote_pt"]}
        for sh in walk(slide.shapes):
            # measure counts a shape it later infers to be a title as body text
            role = {"footer": "footnote", "title_inferred": "body"}.get(roles.get(sh.shape_id), roles.get(sh.shape_id))
            mp = plan.get((rec.get("density"), role))
            if not mp or not sh.has_text_frame:
                continue
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.size is None:
                        continue
                    new = mp.get(round(r.font.size.pt, 2)) or mp.get(r.font.size.pt)
                    if new and new < floor.get(role, body_floor) <= r.font.size.pt:
                        continue                         # never push legal text below the minimum (P5)
                    if new:
                        r.font.size = Pt(new)
                        changed += 1
    if changed:
        prs.save(path)
    return changed
