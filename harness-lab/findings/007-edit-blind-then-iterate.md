# 007: The model edits nearly blind, then iterates

Source: 47 transcripts of our engine loop on Verified dev tasks (cycle 8 ablation, all v5 arms). No model calls.

| Phase | Calls per task |
|---|---|
| Before the first edit | 5.4 (median 4): about 6 reads or greps, 0.5 runs of a test or repro |
| After the first edit | 27.4: 7.2 edits, 3.4 failing test runs, 9.7 re-reads of code |

Few of the failing runs after an edit were syntax or name errors (2 of 157 classified), so an automatic lint after
each edit would buy little. The pattern is a diagnosis problem: the model changes code before it has seen the failure,
then learns what is wrong one test run and one re-read at a time.

Owner's idea (2026-11-12): put observability tools in front of the model, early, so it finds the problem before it
edits. Built behind `MH_OBSERVE` (off by default), in both loops:

- `probe <command>`: runs a test or script and returns a digest. It shows the failing tests, the exception, and the
  failing frames in the project's own code with local variable values (pytest `--tb=long --showlocals`), with library
  frames dropped. It counts as a check for finish. Aimed at the 9.7 re-reads and 3.4 failing runs.
- `trace <command>`: runs a Python test or script under a profiler limited to the project's files and lists the
  functions that ran, in first-call order (fault localization, as in AutoCodeRover and Agentless). Aimed at the
  7.2 edits: edit the right place first.
- A prompt line: reproduce with probe before the first edit; trace if the cause is unclear.

Mechanism metrics for the A/B (Verified dev, v5 against v5 + MH_OBSERVE, same time):

- calls after the first edit;
- edits per task;
- re-reads per task;
- solves;
- $ per solved and time per solved.
