<img src="app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Contributing

Plain rules, learned from building this harness:

1. **One change per pull request**, with a unit test, and all tests passing (`pytest -q tests`).
2. **Point to a failure.** Every change should name the failure it fixes (a task id, a transcript line, a count).
3. **Never tune on the test split.** Use the dev split; test tasks are for final measurement only.
4. **Measure cost, not just solves.** Report tokens or dollars per task when behaviour changes.
5. **Never commit keys.** Use `.env` (gitignored) or CI secrets.
6. **Write plainly.** Short sentences, no jargon.

See `docs/dev/PLUGINS.md` for the extension points (skills, model profiles, engines).
