# Providers and harnesses

Meshpool keeps two choices apart:

- The **harness** is the agent that does the work in a thread: Meshpool Code, Meshpool Work, Pi, Hermes, Codex or Claude Code. You pick it per thread in the message box. Meshpool Auto can also pick it for you.
- The **provider** is what the models run on. You pick it once at sign-up, and change it in **Settings**, **Account and key**, **Runs on**, or with `meshpool setup`.

Your provider also picks your default agent. With the Claude subscription, that is Claude Code. With any other provider, it is Meshpool Code for Code and Meshpool Work for Cowork.

## Providers

| Provider | Credentials | Default agent |
|---|---|---|
| Meshpool API key | `MESHPOOL_API_KEY` on `llm.meshpool.ai` | Meshpool Code / Meshpool Work |
| Another API key | Any OpenAI-compatible key and base URL | Meshpool Code / Meshpool Work |
| ChatGPT subscription | The Codex sign-in in `~/.codex/auth.json` | Meshpool Code / Meshpool Work |
| Claude subscription, runs Claude Code | Claude Code's own sign-in | Claude Code |

The setting is `MH_PROVIDER`: `meshpool`, `custom`, `chatgpt` or `claude`. The code is in `app/meshpool_harness/harnesses/__init__.py` (`credentials()`, `use_provider()`, `default_for()`).

## What each harness runs on

| Harness | Meshpool or other API key | ChatGPT sign-in | Claude sign-in |
|---|---|---|---|
| Meshpool Code, Meshpool Work, Auto | yes | yes, through `core/chatgpt.py` | no |
| Pi, Hermes | yes | not yet (see below) | no |
| Codex | yes, with its own config in the Meshpool data folder | yes, Codex's own sign-in | no |
| Claude Code | only an Anthropic API key | no | yes |

An agent that can't run on the chosen provider shows as **needs setup** in the pickers, with what to add. If you saved an API key earlier, Meshpool's own agents keep using it under the Claude subscription. Claude Code can also use an Anthropic API key, set in **Settings**, **Agents**, **Claude Code**.

## ChatGPT subscription for Meshpool's own agents

With `MH_PROVIDER=chatgpt`, every model call from Meshpool Code, Meshpool Work and the engines goes through `app/meshpool_harness/core/chatgpt.py`. It sits next to the Responses wire (`MH_WIRE=responses`) and works like the open-source harnesses that offer "Sign in with ChatGPT" (Codex CLI, OpenCode, Hermes Agent):

- **Endpoint:** `POST https://chatgpt.com/backend-api/codex/responses`. It uses the Responses API with `stream: true` and `store: false`. System messages go into `instructions`; the endpoint does not accept output limits or temperature.
- **Headers:** `Authorization: Bearer <access_token>` and `ChatGPT-Account-ID`, taken from the token's claims. Meshpool names itself with `originator: meshpool` and `User-Agent: Meshpool/<version>`, as OpenAI asks third-party harnesses to do.
- **Token refresh:**
  - The access token is refreshed shortly before it expires, and once after a 401.
  - The refresh uses `https://auth.openai.com/oauth/token` with the refresh token.
  - Refresh tokens are single-use and shared with the Codex CLI. So the refresh runs under a file lock, re-reads `auth.json` first, and keeps a pair another program already rotated. It writes the new pair back to `auth.json`, so Codex keeps working.
- **Models:** the list comes from the plan's Codex model list. A Meshpool model id is replaced by the ChatGPT default (`MH_CHATGPT_MODEL`).

Pi and Hermes don't use the ChatGPT sign-in yet: each would need its own copy of the single-use refresh token, and two programs refreshing one sign-in log each other out. They run on an API key when one is saved.

**Terms.** OpenAI allows this sign-in route for local and open-source apps that run on the user's own computer. It has never been allowed for hosted or commercial services. OpenAI recommends moving to its official [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source) program, which gives an app its own client registration and calls `api.openai.com/v1/responses`. Meshpool should move to that once it is registered.

## Claude subscription: Claude Code only

Anthropic's terms do not let third-party apps use a claude.ai sign-in or its usage limits for their own agents. So in Meshpool:

- The Claude subscription only runs the official Claude Code agent, through the ACP adapter (`@agentclientprotocol/claude-agent-acp`). Claude Code signs in and keeps its token itself.
- Meshpool only checks whether that sign-in exists. It never reads, copies or forwards Claude Code's token, and never uses it in its own agent loop.
- Meshpool turns off Hermes's `auth.adopt_external_logins`, so the bundled Hermes never borrows the Claude Code or Codex sign-in.
- `tests/test_chatgpt_wire.py` checks that no request carries a Claude Code token. `tests/test_providers.py` checks that the Claude subscription makes only Claude Code ready.

## Older settings

Before this change, the subscriptions were harnesses called `claude-subscription` and `chatgpt-subscription`. Old threads and settings that name them now open `claude-code` and `codex`.
