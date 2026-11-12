# Security

- Never put API keys in code, issues or pull requests. Use `.env` locally and repository secrets in CI.
- The harness runs shell commands chosen by a model. Run it in a container or a disposable checkout.
- Report security problems privately to the maintainers rather than in a public issue.
