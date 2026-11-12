# 004: The two slowest MiniWoB tasks hit browser-tool bugs, not hard reasoning

Source: `harness-lab/mw_probe.py` drives our browser tool by hand on the real MiniWoB pages (pip package `miniwob`
1.1.0, seed 1). No model calls.

book-flight and terminal took 18–26 turns for every agent (harness, plain DeepSeek, Claude Code). Those are about
40% of the turns in a 34-task round. Driving the tool by hand showed why:

| Problem | What the model saw | Effect | Fix (flag, off by default) |
|---|---|---|---|
| Typing into a read-only field (date picker input) | 10 s wait, then `TimeoutError` | a wasted turn and 10 s per try | `MH_BROWSER_READONLY`: set the value at once and say so in a note |
| Autocomplete menu opens about 300 ms after typing | not listed; only "1 result is available" in the text | the model cannot see the item to pick | `MH_BROWSER_SETTLE`: wait until the page is quiet (350 ms, max 1.5 s) before the snapshot |
| A field marked bad only by a red outline (`class="error"`) | "no visible text change" after Search | the model cannot tell why nothing happened | `MH_BROWSER_STATES`: mark `(invalid)`, `(disabled)`, `(read-only)` |
| A widget that reads key presses (hidden input, terminal) | the typed text never appears; Enter runs an empty command | the model retries blindly | `MH_BROWSER_KEYTYPE`: if the text does not show after a direct set, type it as key presses |

With all four flags, both tasks reach reward 1 by hand in 7 actions (book-flight) and 2 actions (terminal).

These are general web problems: picker fields, delayed menus, red-outline validation, key-driven widgets. The fixes
are tool behaviour, not task hints, and the prompt is unchanged. Normal inputs and textareas keep the fast path
(tested). The read-only set is the one with a judgement call: a person would use the picker. The note tells the
model, so it can check that the page accepted the value.

Expected effect: book-flight and terminal drop from about 20 turns to about 4–8. Since they make up about 40% of a
round's turns, that is roughly 25–35% fewer MiniWoB calls and less time, with possibly +1–2 solves per 34 (both tasks
sometimes fail today).

Paid confirmation: one 10-task MiniWoB sentinel round (about $0.05 per arm), flags on against off.
