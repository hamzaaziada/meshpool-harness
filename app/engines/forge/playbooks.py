"""Domain playbooks: the analytical conventions a senior banker or partner expects for a type of decision.

A stronger model does not know these on its own: a reviewer grading with a strong model marked our capital-allocation
decks 1-2 points below a published Goldman Sachs board book for the same reasons every time (an authorization read as
spending, liquidity as one subtraction from a stale balance, an earnings yield set against a Treasury yield as if it
valued a buyback, a fact stated on the cover and "to be verified" on the last page). The playbook goes into every
writer prompt (storyline, slides, revisions) when the brief matches, whatever the model.
"""
from __future__ import annotations

import json
import re

PLAYBOOKS = [
    (re.compile(r"(?i)\b(capital allocation|buy ?backs?|repurchase|special dividend|dividend|return (more )?cash|"
                r"excess cash|cash return|balance sheet|leverage|recapitali[sz]ation)\b"),
     "Capital allocation playbook (how a bank MD and a strategy partner test this decision):\n"
     "- Liquidity is a dated sources-and-uses schedule, never one subtraction from a point-in-time balance: start "
     "from the latest reported cash with its date, add cash generated and subtract each outflow in the period it is "
     "paid (the dividend on its payment date, capex as it is spent), and show the trough against a stated minimum "
     "operating cash (cite it, or derive it, e.g. weeks of operating costs) and any undrawn credit line.\n"
     "- A buyback authorization is a ceiling, not spending: state the amount remaining, the period, and the pace "
     "the board is asked to approve; never treat the whole authorization as a cash outflow on one date.\n"
     "- Value a buyback against intrinsic value, not against a Treasury yield: compare the share price with a value "
     "range (a DCF or the multiple range the facts support) or show the implied return of buying at today's price; "
     "an earnings yield is an EPS-accretion test only, and is labelled as that. Never set an earnings yield against "
     "a cash yield as proof of value. When the facts include 'implied return of buying shares at today's price' and "
     "'cost of equity (CAPM)', that pair is the buyback test: buy back while the implied return clears the cost of "
     "equity, show it again at lower growth, and name the price at which it stops clearing.\n"
     "- Compare like with like: a return on incremental capital against the cost of capital, a dividend's yield "
     "against the payout policy; book return on capital is not the return on the next dollar of capex.\n"
     "- A fact the decision rests on (a declared dividend, an authorization) is stated once with its primary source "
     "(8-K, press release, 10-K) and treated as fact everywhere; if it is not confirmed, say so on the page that "
     "uses it, never assert it on one page and 'verify' it on another.\n"
     "- Size the recommendation in one sentence the board can vote on: what, how much, by when, and the condition "
     "that would change it (the price above which buybacks stop, the cash trough that pauses them).\n"),
]


def playbook_for(brief: str) -> str:
    """The playbooks whose trigger matches the brief, as one block for the writer prompts ("" when none)."""
    return "\n".join(text for rx, text in PLAYBOOKS if rx.search(brief or ""))


_EY = re.compile(r"(?i)\b(earnings yield|buy ?backs? (earns?|yields?|returns?)|repurchases? (earns?|yields?))\b")
_CASH = re.compile(r"(?i)\b(t-?bills?|treasury|cash yield|cash earns|interest on cash|risk-free)\b")


def playbook_checks(slides: list[dict], brief: str) -> dict[int, list[str]]:
    """What the playbook forbids, caught in code (the writer reads the playbook, and still uses the easy argument):
    a capital-allocation slide that sets a buyback's earnings yield against a cash or Treasury yield."""
    if not playbook_for(brief):
        return {}
    out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        if s.get("fixed"):
            continue
        txt = json.dumps({k: v for k, v in s.items() if k not in ("notes", "prov", "source")}, ensure_ascii=False)
        if _EY.search(txt) and _CASH.search(txt) and not re.search(r"(?i)accretion test", txt):
            out.setdefault(n, []).append(
                "[playbook] this slide sets a buyback's earnings yield against a cash or Treasury yield; a reviewing MD "
                "rejects that as a valuation test. Value the buyback against intrinsic value (the implied return of "
                "buying at today's price, or the share price against a DCF or multiple range from the facts), and if "
                "earnings yield stays, label it an EPS-accretion test only, not a comparison with cash")
    return out
