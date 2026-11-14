<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Providers and harnesses

The message box has two separate choices:

- **Agent (harness):** who does the work in a chat. Pick it in the CPU pill. Each agent has a one-line "best at":

  | Agent | Best at |
  |---|---|
  | Meshpool Auto | Picks the best agent per task: quality first, then cost and speed |
  | Meshpool Code | Coding in your project: more fixes solved, for less, than Codex CLI |
  | Meshpool Work | Word, Excel, PowerPoint and PDF, checked before you get them |
  | Pi | Quick, low-cost code fixes |
  | Hermes | Ongoing work: remembers what it learns across chats |
  | Codex | Terminal and command-line work, on GPT models |

- **Model (provider):** where inference runs. Pick it in the model pill. A row of tabs comes first: **Meshpool** (or your own endpoint) and **ChatGPT**. The models for that provider are listed below the tabs. A provider that is not set up shows **Sign in** or **Add a key** instead.

Sign-up asks for one provider, and that provider sets the defaults. You can connect more later in **Settings**, **Account and key**, **Providers**.

## Providers

| Provider | Credentials | Agents that can use it | Model id |
|---|---|---|---|
| Meshpool (or another OpenAI-compatible endpoint) | `MESHPOOL_API_KEY`, `MESHPOOL_BASE_URL` | every agent | `deepseek-v4.1-flash` |
| ChatGPT subscription | The Codex sign-in in `~/.codex/auth.json` | every agent (Pi and Hermes through the local relay) | `chatgpt:<model>` |

Meshpool does not support Claude or Gemini accounts: their makers' terms let a subscription sign-in run only their own agents, so Meshpool keeps to providers every agent can use.

A model's provider is a prefix on its id. An id with no prefix runs on the API key endpoint. `app/meshpool_harness/providers.py` turns the id into the endpoint, key and wire to use. It also lists each provider's models for the picker and records which agents can use which provider (`SUPPORTS`). `harnesses.credentials()` gives each agent the environment and config its process starts with, for the provider of the chat's model.

In a running Codex chat, a model from another provider starts a new chat, because Codex signs in once when it starts.

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
- **Models:** the list comes from the plan's Codex model list, `GET /models?client_version=0.169.0`. The endpoint needs a version in Codex's format, and it only lists models for clients at or above each model's minimum Codex version. With Meshpool's own version, 0.1.0, it returns no models. So Meshpool sends the Codex release whose request format it matches, and still names itself as Meshpool. The default model is the one chosen in the picker if the plan still offers it, otherwise the first model the plan lists.
- **Usage limit:** when the plan's limit is used up, ChatGPT answers 429 `usage_limit_reached` with the reset time. Meshpool shows "Usage limit reached on ChatGPT Go. It resets in 11 days, on 25 Nov. Pick another provider in the model menu" in the chat and under the ChatGPT tab. It does not retry, because a plan limit resets in hours or days, not seconds. The relay answers Pi and Hermes with 429 `insufficient_quota`, which OpenAI-style clients don't retry.
- **Checked live (14 Nov 2026, ChatGPT Go plan, limit used up):** the model list loaded (gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna), and the usage-limit message appeared in Meshpool Code within a second. A forced token refresh wrote the new tokens back to `auth.json`, and the Codex CLI still reported "Logged in using ChatGPT". A full reply, a tool call and Pi through the relay could not be checked live because the plan had no usage left. They are covered by the mock tests and the real-Pi run.

### The local relay for Pi and Hermes

Pi and Hermes speak OpenAI-style chat completions, so they reach the ChatGPT sign-in through a local relay (`app/meshpool_harness/core/chatgpt_relay.py`). The relay runs inside the Meshpool process:

- **Address:** an OpenAI-compatible server on `127.0.0.1`, on a random port, with `/v1/chat/completions` (streamed or not, tools included) and `/v1/models`.
- **Access:** each request needs a random bearer token made per Meshpool process. Meshpool hands the address and token to the agents it starts: Pi through its `models.json` and environment, Hermes through its config.
- **Inside:** the relay forwards each request to `core/chatgpt.py`, so it uses the same wire, headers and token refresh as Meshpool's own agents.

Pi and Hermes never see the ChatGPT token. Meshpool stays the only program that refreshes it, which matters because refresh tokens are single-use: two programs refreshing one sign-in log each other out. The relay stops with the Meshpool process.

Tested with a real Pi 1.5.2 in RPC mode against a mock ChatGPT endpoint: text streamed, and a tool call went from ChatGPT to Pi and its result came back. The relay itself is tested in `tests/test_chatgpt_wire.py`. Hermes is covered only by a test that checks the config Meshpool writes for it.

**Terms.** OpenAI allows this sign-in route for local and open-source apps that run on the user's own computer, and has never allowed it for hosted or commercial services. OpenAI recommends its official [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source) program, which registers the app as its own client and calls `api.openai.com/v1/responses`. Meshpool should move to that once it is registered.

Meshpool turns off Hermes's `auth.adopt_external_logins`, so Hermes never borrows another app's sign-in.

## Tests

- `app/tests/test_providers.py` covers:
  - model prefixes;
  - which agents run on which provider;
  - the agent switching that follows from the chat's model;
  - sign-in-only models never reaching Meshpool's own loop.
- `app/tests/test_chatgpt_wire.py` runs the ChatGPT wire against a local mock.

## Older settings

Old chats and settings may name agents that are gone: `chatgpt-subscription` opens Codex, and `claude-subscription`, `claude-code` and `gemini-cli` open Meshpool Code.
