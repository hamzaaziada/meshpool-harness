---
name: pull-request
description: Commit the current work on a new branch, push it, and open a pull request with a clear title and description.
---

# Open a pull request

1. Check the state: `git status`, `git branch --show-current`, `git remote -v`. Stop and tell the user if this is not a Git repository or there is no remote.
2. If you are on the default branch (main or master), create a branch named after the change: `git switch -c <short-kebab-name>`.
3. Run the project's tests or build if there is an obvious command, and fix failures caused by the change.
4. Stage and commit with a message in the imperative mood that says what changed and why: `git add -A && git commit -m "..."`.
5. Push: `git push -u origin HEAD`.
6. Open the pull request:
   - With the GitHub CLI: `gh pr create --fill` or `gh pr create --title "..." --body "..."`.
   - Or with the GitHub MCP server, if it is on.
   - Otherwise give the user the compare URL printed by `git push`.
7. The description says what changed, why, how it was tested, and anything a reviewer should look at first.
8. Reply with the pull request URL.
