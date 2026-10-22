# Setting up the Claude API (owner only)

The app uses Anthropic's Claude API through the official `anthropic` Python SDK. The application owner pays for it. The invited tester needs no Anthropic account or key: they sign in to the website, and the backend calls Claude with the owner's key. There is deliberately no "bring your own key" field anywhere in the UI.

Model behaviour and prices change. The details here were checked on **2026-10-21**; check them again against the links at the end before relying on them.

## 1. Create a dedicated workspace and set up billing

1. Sign in to the [Claude Console](https://platform.claude.com) and set up API billing for your organization.
2. Go to **Settings → Workspaces → Create workspace** and name it, for example, `projections-assistant`. Only organization admins can create workspaces.
   Use a dedicated workspace, **not the Default Workspace**: the Default Workspace cannot have a spend limit.

## 2. Create a key limited to that workspace

- **Local use:** create an API key in the new workspace. A personal key bound to that single workspace is fine.
- **A deployed backend** (e.g. the Oracle VM): prefer a service-account key bound to that single workspace.
- **Never** use an Admin API key (`sk-ant-admin…`) or a multi-workspace key for model requests.

## 3. Store the key for the backend only

```bash
cp .env.example .env      # if you haven't already
chmod 600 .env
```

Edit `.env`:

```dotenv
AI_MODE=anthropic
ANTHROPIC_API_KEY=sk-ant-...        # paste here, nowhere else
ANTHROPIC_MODEL=claude-opus-5-5
```

Only the backend container receives `.env`. The key never goes into browser code, frontend build variables (`VITE_*`), the calculation worker, logs, exports, or API responses. `.env` and `secrets/` are excluded from git and from Docker build contexts.

The backend passes the key to the SDK explicitly and ignores other credential sources (local `ant auth login` profiles, `ANTHROPIC_AUTH_TOKEN`). Make sure the backend container does not mount `~/.config/anthropic`.

Never paste the key into chat, source code, screenshots, or a browser form.

## 4. Set a spend limit and alerts

1. In the Console, open **Settings → Workspaces → (your workspace) → Spend limits**. Set a monthly cap and alert thresholds.
2. Optionally, cap requests and tokens per minute on the **Rate limits** page (choose the workspace in the dropdown).
3. Keep the app's own limits below the workspace cap. They're set in `.env`, and the defaults are:
   ```dotenv
   AI_DAILY_BUDGET_USD=2.00
   AI_MONTHLY_BUDGET_USD=20.00
   AI_REQUESTS_PER_USER_PER_MINUTE=10
   AI_MAX_CONCURRENT_TURNS=1
   ```
   Before every request the app reserves an upper-bound cost and stops before the budget would be exceeded. Its accounting is an **estimate** based on [backend/app/ai/pricing.json](../backend/app/ai/pricing.json).
4. Open the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) and check the rates in `pricing.json` against it. Set `"verified": true` and update `effective_date` when they match. The file includes the default model, `claude-sonnet-5-5`, and models that refusal fallback may route to.

## 5. Choose the model and settings

| Setting | Default | Notes |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Claude Opus 5.5. `claude-sonnet-5-5` costs about half; compare quality on the walkthrough before switching. |
| `ANTHROPIC_EFFORT` | `medium` | Opus 5.5 always thinks; effort (`low` to `max`) controls depth, latency and cost. |
| `ANTHROPIC_REFUSAL_FALLBACK` | `default` | If Claude's safety classifiers decline a request, Anthropic retries it on a fallback model in the same call (beta `server-side-fallback-2026-07-01`). The ledger bills the model that actually served the request. Set `off` to disable. |
| `AI_MAX_OUTPUT_TOKENS` | `8000` | Per request, including thinking tokens. |

These parameters are fixed in the adapter for Opus 5.5:
- Thinking is adaptive, because Opus 5.5 rejects disabling it.
- Tool choice is `auto`, because forced tool choice is rejected.
- No sampling parameters are sent.
- All tools are `strict`.
- The final reply uses structured outputs.

If you switch models, re-run the connection check and the walkthrough.

## 6. Run the connection check

Restart the app (`docker compose up -d`), sign in as the owner, and open **Usage**:

- **Check connection** calls the Models API for your configured model. It costs no tokens. It confirms the key, the workspace (the `anthropic-workspace-id` response header) and model availability.
- **Live check** sends one tiny message, which costs a fraction of a cent, and records it in the usage ledger.

The key is never displayed. If the check reports an authentication error, the key is wrong or revoked.

### Revoking or rotating the key

1. In the Console, create a new key in the same workspace.
2. Replace `ANTHROPIC_API_KEY` in `.env`.
3. Run `docker compose up -d backend` to restart the backend.
4. Delete the old key in the Console.

## What is sent to Anthropic

For each message, the backend sends:
- the system prompt (`backend/app/ai/prompts/system_v1.md`),
- the tool definitions,
- recent conversation text,
- the workbook values, labels and formulas that the assistant explicitly reads through its tools.

Hidden sheets, rows and columns are never sent, and the whole workbook file is never uploaded. Requests are processed and retained by Anthropic under its [API data-retention policy](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) and [commercial retention policy](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data). The Messages API keeps no conversation state, but that does **not** mean zero retention. Zero data retention is a separate arrangement with Anthropic and is not assumed here.

## References

- Models and pricing: <https://platform.claude.com/docs/en/about-claude/models/overview>, <https://platform.claude.com/docs/en/about-claude/pricing>
- Tool use and structured outputs: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>, <https://platform.claude.com/docs/en/build-with-claude/structured-outputs>
- Workspaces and spend limits: <https://platform.claude.com/docs/en/manage-claude/workspaces>
- Errors and rate limits: <https://platform.claude.com/docs/en/api/errors>, <https://platform.claude.com/docs/en/api/rate-limits>
