# Calibrating the partner and MD reviewers

## Why

The bar is "overall 8 or more, no dimension under 7". With DeepSeek decks stuck near 6 we checked what the same
reviewers give real work. We used a Goldman Sachs board discussion book that is a public SEC filing: Antero
Midstream, Schedule 13E-3/A, Exhibit (c)(23), June 2018, filed one image per page.

| Pages graded | Partner | MD |
|---|---|---|
| 1-12 (cover, disclaimer, contents, terms) | 4.2 | 5.25 |
| 7-22 (the analysis) | 4.47 | 6.03 |
| 7-9, with the calibration note in the prompt | 4.45 | 5.7 |
| 10-19, with the calibration note in the prompt | 5.8 | 5.92 |

Both reviewers also raise "blocking" issues against it (no football field, a label title, an exchange-ratio
check). Raw, a published top-bank book scores about 4.8 (partner) and 5.7 (MD): the raw bar asked for more than
Goldman's own board materials.

## What changed

- The reviewers' prompt states the scale's anchor: published work from the best firms is an 8, and the other
  house's conventions (label titles on bank back-up pages, action titles on consulting pages) are not faults.
- `review.personas.bar.calibration` in `app/engines/forge/rules.yaml` shifts each reviewer's scores by the amount
  that puts the reference at 8: partner +3.2, MD +2.3. Sign-off uses the calibrated score; raw scores are kept and
  reported. With a calibration, blocking issues are still listed and sent to the writer, but they do not stop
  sign-off on their own, because the reference draws them too.
- `MH_REVIEW_CALIBRATION=0` grades raw.

## Limits

- One reference book, one bank. The partner's anchor is a banking book, which his consulting checklist dislikes,
  so his offset is generous; a published consulting deck is the missing second anchor.
- Workbook reviews use the same per-persona offsets. They measure how harsh each reviewer is and were taken on a
  deck; no published reference workbook was available to measure a workbook offset directly.
- The reviewers are DeepSeek v4.1 flash; another reviewer model needs its own calibration
  (`testkit/calibration/reviewers.py` measures it).
