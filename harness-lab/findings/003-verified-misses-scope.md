# 003: Both v3 Verified misses changed more than the fix needed

Source: saved patches from run C (`eval/results/h2h_v3/patches`). No model calls. These are test tasks: they explain
the problem, but the rule below was not fitted to them, and it must be measured on the dev split.

| Task | Agent | Changed lines | Files | Passed |
|---|---|---|---|---|
| pylint-4970 | harness v3 | 43 | similar.py, tests | no |
| pylint-4970 | plain DeepSeek | 8 | similar.py, tests | yes |
| pylint-4970 | Codex | 5 | similar.py | no |
| pylint-4970 | Claude Code | 11 | ChangeLog, similar.py, tests | yes |
| django-12308 | harness v3 | 18 | admin/utils.py, **forms/fields.py**, tests | no |
| django-12308 | plain DeepSeek | 18 | admin/utils.py, tests | yes |
| django-12308 | Codex | 3 | admin/utils.py | no |
| django-12308 | Claude Code | 15 | admin/utils.py, tests | yes |

What our patches added beyond the core fix:

- **pylint:** a second early return that zeroes the duplicate-line stats, and a help text change.
- **django:** an `and value` guard, so falsy JSON values skip the new path, and an edit to an unrelated file,
  `forms/fields.py`.

Codex's misses went the other way: its patches were too small.

Rule for `MH_SCOPE_CHECK`, set before measuring:

- It fires once, at the first finish.
- It fires when the change touches more than one non-test file, or more than 30 non-test lines.
- It shows the size per file and asks the model to revert edits the task does not need.

On these two tasks it would fire on django and not on pylint.

Risk: "verify before finish" and a reviewer both lost on solves before. This check adds at most one model call, and
only on tasks with a spread-out change. Keep it only if dev-split solves do not drop and the added calls stay under
5%.
