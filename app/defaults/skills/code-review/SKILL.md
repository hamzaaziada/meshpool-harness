---
name: code-review
description: Review the current changes (or a named file or branch) for bugs, risky edits and missing tests, and report findings ranked by severity.
---

# Code review

1. Find what changed. In a Git repository run `git status` and `git diff` (and `git diff --staged`). If the user named files or a branch, review those instead (`git diff main...HEAD`).
2. Read each changed file around the edits, not just the diff lines, so you understand the surrounding code.
3. Look for, in this order:
   - Correctness: wrong logic, off-by-one, unhandled errors, null or empty inputs, race conditions.
   - Security: secrets in code, injection, unsafe file or shell use, missing auth checks.
   - Breaking changes: renamed or removed public functions, changed formats, migrations.
   - Tests: behavior that changed without a test. Run the existing tests if there is an obvious command.
   - Readability: only when it hides a real problem.
4. Report each finding with the file and line, what goes wrong and when, and a concrete fix. Rank by severity. Say plainly if you found nothing serious.
5. Do not edit files unless the user asks you to fix the findings.
