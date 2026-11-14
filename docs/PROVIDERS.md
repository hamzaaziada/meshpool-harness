# Providers and harnesses

The message box has two separate choices:

- **Agent (harness):** who does the work in a thread. Pick it in the CPU pill. Each agent has a one-line "best at":

  | Agent | Best at |
  |---|---|
  | Meshpool Auto | General tasks: picks the best agent for each one |
  | Meshpool Code | Coding in your project, with browser, connectors and skills |
  | Meshpool Work | Documents, spreadsheets, decks and research |
  | Pi | Quick, low-cost code fixes |
  | Hermes | Ongoing work: remembers what it learns across threads |
  | Codex | Terminal and command-line work, on GPT models |
  | Claude Code | Hard, multi-file coding, on Claude models |
  | Gemini CLI | Large codebases and long context, on Gemini models |

- **Model (provider):** where inference runs. Pick it in the model pill. A row of tabs comes first: **Meshpool** (or your own endpoint), **ChatGPT**, **Claude** and **Gemini**. The models for that provider are listed below the tabs. A provider that is not set up shows **Sign in** or **Add a key** instead.

Sign-up asks for one provider, and that provider sets the defaults. You can connect more later in **Settings**, **Account and key**, **Providers**.

## Providers

| Provider | Credentials | Agents that can use it | Model id |
|---|---|---|---|
| Meshpool (or another OpenAI-compatible endpoint) | `MESHPOOL_API_KEY`, `MESHPOOL_BASE_URL` | all except Claude Code and Gemini CLI | `deepseek-v4.1-flash` |
| ChatGPT subscription | The Codex sign-in in `~/.codex/auth.json` | Meshpool Code, Work, Auto, Codex | `chatgpt:<model>` |
| Claude, Anthropic API key | `ANTHROPIC_API_KEY` | Meshpool Code, Work, Auto, Pi, Hermes, Claude Code | `anthropic:<model>` |
| Claude subscription | Claude Code's own sign-in | Claude Code only | `claude:default` |
| Gemini, API key | `GEMINI_API_KEY` (Google AI Studio) | Meshpool Code, Work, Auto, Pi, Hermes, Gemini CLI | `gemini:<model>` |
| Gemini, Google sign-in | Gemini CLI's own sign-in | Gemini CLI only | `google:default` |

A model's provider is a prefix on its id. An id with no prefix runs on the API key endpoint. `app/meshpool_harness/providers.py` turns the id into the endpoint, key and wire to use. It also lists each provider's models for the picker and records which agents can use which provider (`SUPPORTS`). `harnesses.credentials()` gives each agent the environment and config its process starts with, for the provider of the thread's model.

If you pick a model the current agent can't run, Meshpool switches the thread to an agent that can and says so. For example, a Claude-subscription model switches to Claude Code, and a ChatGPT model picked while Pi is the agent switches to Meshpool Code. In a running Codex, Claude Code or Gemini CLI thread, a model from another provider starts a new thread, because those agents sign in once when they start.

## ChatGPT subscription for Meshpool's own agents

A `chatgpt:` model sends Meshpool Code, Meshpool Work and the engines to `app/meshpool_harness/core/chatgpt.py`. It works like the open-source agents that offer "Sign in with ChatGPT" (Codex CLI, OpenCode, Hermes Agent):

- **Endpoint:** `POST https://chatgpt.com/backend-api/codex/responses`. It uses the Responses API with `stream: true` and `store: false`. System messages go into `instructions`. Output limits and temperature are not sent.
- **Headers:**
  - `Authorization: Bearer <access_token>`.
  - `ChatGPT-Account-ID`, read from the token's claims.
  - `originator: meshpool` and `User-Agent: Meshpool/<version>`. OpenAI asks third-party agents to name themselves.
- **Token refresh:**
  - The access token is refreshed shortly before it expires, and once after a 401, at `https://auth.openai.com/oauth/token`.
  - Refresh tokens are single-use and shared with the Codex CLI. So the refresh runs under a file lock, re-reads `auth.json` first, and keeps a token another program has already refreshed.
  - The new tokens are written back to `auth.json`, so Codex keeps working.
- **Models:** the list comes from the plan's Codex model list.

Pi and Hermes don't use the ChatGPT sign-in yet. Each would need its own copy of the single-use refresh token, and two programs refreshing one sign-in log each other out.

**Terms.** OpenAI allows this sign-in route for local and open-source apps that run on the user's own computer, and has never allowed it for hosted or commercial services. OpenAI recommends its official [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source) program, which registers the app as its own client and calls `api.openai.com/v1/responses`. Meshpool should move to that once it is registered.

## Claude and Google sign-ins: only in their makers' agents

- Anthropic's terms don't let other apps use a claude.ai sign-in or its usage limits.
- Google's terms forbid using a Gemini CLI or Antigravity sign-in from third-party tools. Google suspended accounts for this in 2026, some of them later reinstated.

So in Meshpool:

- A Claude sign-in runs only the official Claude Code agent, through `@agentclientprotocol/claude-agent-acp`.
- A Google sign-in runs only the official Gemini CLI (`@google/gemini-cli --acp`). Meshpool tells Gemini CLI which of its own logins to use through the ACP `authenticate` call.
- Meshpool only checks whether these sign-ins exist. It never reads, copies or forwards their tokens.
- Meshpool turns off Hermes's `auth.adopt_external_logins`. By default, Hermes borrows the Claude Code and Codex logins.
- API keys from Anthropic and Google are fine for every agent that can call them.

## Tests

- `app/tests/test_providers.py` covers:
  - model prefixes;
  - which agents run on which provider;
  - the agent switching that follows from the thread's model;
  - Gemini key and Google sign-in handling;
  - Anthropic and Gemini endpoint routing;
  - sign-in-only models never reaching Meshpool's own loop.
- `app/tests/test_chatgpt_wire.py` runs the ChatGPT wire against a local mock. It also checks that no request carries a Claude Code token.

## Older settings

Before this change, the subscriptions were harnesses called `claude-subscription` and `chatgpt-subscription`. Old threads and settings that name them now open `claude-code` and `codex`.
