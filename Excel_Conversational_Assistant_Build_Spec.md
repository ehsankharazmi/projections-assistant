# Build specification: Conversational Workbook Assistant

Revision: 2026-10-21 (AI provider changed from OpenAI to the Anthropic Claude API). Target: local development, invitation-only remote testing through Cloudflare Tunnel, and optional later migration to an Oracle Cloud VM. Sections 15–16 define required deployment and AI integration behavior.

## 1. Instructions to the coding agent

Build a working standalone prototype from this specification. Implement and verify it; do not stop at an architecture proposal. Do not use Copilot, an existing Excel AI assistant, or an Excel add-in. The user interacts through a browser application. Excel files remain import/export artifacts and contain the calculation logic.

Make routine implementation choices autonomously. Begin with the included synthetic demonstration workbook because the real workbook has not yet been supplied. Clearly separate demo mappings from imported workbook mappings. Record assumptions and limitations in the README. Use current official documentation for dependency and API details; pin tested dependency versions in lockfiles. Do not claim tests or compatibility checks that were not performed.

Deliver a small, understandable application rather than an enterprise platform. Prioritize one reliable end-to-end workflow: upload → understand → clarify → preview → apply → export → undo.

## 2. Product goal and target user

The user understands her work and communicates requirements well, but is unfamiliar with Excel formulas and mechanics. She should explain what she wants, judge whether results make sense, and correct the assistant using ordinary language.

The assistant translates those requests into explicit changes to a workbook, runs its formulas, and explains the resulting outputs. It must help establish the meaning of a request rather than silently choosing consequential assumptions.

Example interaction:

- User: “Increase labor costs by 8%, keep prices unchanged, and tell me which products lose money.”
- Assistant: “Do you mean increase hourly wages, hours worked, or both?”
- User: “Hourly wages only, for manufacturing.”
- Assistant previews the affected inputs and calculated product margins, with an explanation of the assumptions.
- User: “Actually make that 5%.”
- Assistant replaces the pending preview with a 5% scenario based on the same starting version; it must not compound 8% and 5%.
- User selects Apply. A new workbook version becomes active and can be downloaded.

Success means the user can complete this workflow without referring to cell addresses or writing formulas.

## 3. MVP scope

### Required

1. Import a supported `.xlsx` workbook and retain the original unchanged.
2. Inspect sheets, used cells, formulas, stored values, named ranges, tables, hidden content, and workbook features relevant to compatibility.
3. Establish a human-readable workbook map identifying editable inputs, calculated outputs, units, constraints, and business terms.
4. Chat about mapped inputs, outputs, formulas, and assumptions with source references.
5. Create scenarios by editing approved input values; recalculate real formulas.
6. Show a before/after preview, checks, and a plain-language explanation.
7. Apply a preview as an immutable version, compare scenarios, restore a previous version, and export `.xlsx`.
8. Persist conversation, mappings, scenarios, and history across restarts.
9. Provide a clearly labeled demo mode that works without an AI API key, plus real conversational mode when configured.

### Excluded from the MVP

Arbitrary formula rewriting, structural sheet edits, new business-model generation, VBA, macros, Power Query refresh, external data refresh, unrestricted code execution, goal-seeking/optimization, real-time collaboration, cloud account connectors, voice input, and a full spreadsheet editor.

When asked to change a formula, explain the current logic and describe the requested change, but do not apply it. Formula editing can be a later phase with separate review and validation.

## 4. Architecture and suggested stack

Default to a local-first browser application, runnable through Docker Compose:

| Layer | Suggested implementation |
| --- | --- |
| Frontend | React, TypeScript, Vite; simple accessible components |
| Backend | Python, FastAPI, Pydantic |
| Persistence | SQLite for metadata; local filesystem for immutable workbook versions |
| Workbook inspection and controlled edits | `openpyxl`, with explicit preservation limitations |
| Recalculation | LibreOffice Calc through a separate headless worker |
| AI | Anthropic Claude API through the official `anthropic` Python SDK (Messages API) with strict tool use and structured outputs; model configured through environment |
| Tests | pytest, frontend component checks as needed, Playwright for the main workflow |

These are implementation recommendations for the downstream project, not dependencies on this document's authoring environment. Avoid adding an agent framework, vector database, Redis, or a task-queue service unless a demonstrated need arises. A persisted jobs table and a single worker are sufficient initially.

Define a `WorkbookEngine` interface for `inspect`, `apply_inputs`, `recalculate`, `read_results`, and `validate_round_trip`. Ship one functioning LibreOffice implementation. Leave a documented extension point for native Excel automation when the real workbook requires Excel-specific behavior; do not build a second engine for the MVP.

The AI interprets and explains. Application code enforces permissions, computes deterministic changes and comparisons, and invokes the calculation engine. The model must not invent numerical results or implement Excel formulas itself.

## 5. Workbook compatibility and calculation fidelity

This is a compatibility-limited prototype, not a universal Excel implementation. `openpyxl` reads/writes workbook structures but does not calculate formulas. Setting a recalculate-on-open flag does not produce results for the application.

Implement these rules:

- Accept `.xlsx` only, initially up to 10 MiB and 100,000 populated cells. Enforce compressed and expanded ZIP limits, member-count limits, and parsing timeouts independently.
- Reject encrypted/password-protected files and macro-bearing packages, including misleading extensions. Never bypass worksheet protection or silently unhide content.
- Block mutation of workbooks with external references, data connections, unsupported calculation features, or unsupported embedded objects. Allow inspection when safe and label the reason clearly.
- Initially support ordinary scalar arithmetic, `SUM`, `IF`, and basic cross-sheet references demonstrated by fixtures. Additional formula families require compatibility fixtures. Treat formula-name scanning as a preflight heuristic, not proof of compatibility.
- Flag dynamic arrays, data tables, cube functions, custom functions, iterative calculations, volatile/date/random-dependent calculations, and complex named formulas as unsupported until explicitly tested.
- Before enabling edits, perform a no-change round trip through the actual editing/recalculation pipeline. Compare cell types, formulas, mapped output values, names, tables, formats, and relevant workbook structures against the source.
- Compare formula results with imported Excel cached values where available; missing caches mean compatibility is unverified. Permit inspection, but require an administrator to provide trusted reference outputs or confirm baseline results before scenario editing.
- Compare numerics with configurable tolerances (default absolute `1e-8`, relative `1e-6`), dates and text according to their types. Report baseline errors separately from newly introduced errors.
- Preserve formulas semantically. If engine normalization changes formula text, retain both representations and flag the change; block edits unless an explicit tested normalization rule establishes equivalence.
- Do not promise full preservation of charts, drawings, formatting, or advanced Excel features. Detect what can be detected, block known unsupported features, and document residual fidelity limits. Never destroy the original to make an import work.

The LibreOffice worker must force a full recalculation, wait for a successful save, then reopen the resulting file to read updated values. Use isolated profiles and directories per job, separate input/output paths, explicit timeouts, and a non-root worker with network access disabled and resource limits. Disable macro execution and external-link updates. Do not expose its automation interface publicly. A successful subprocess exit alone is insufficient; verify a valid output workbook and results.

## 6. Workbook map and setup flow

Provide an “Understand this workbook” setup screen with two levels:

- User view: business labels, descriptions, input/output roles, units, examples, and short confirmation questions.
- Administrator detail: sheet names, cell/range addresses, formulas, validation rules, mapping source, and compatibility diagnostics.

Infer candidate meanings using labels, formulas, number formats, named ranges, and tables. Never infer that every non-formula cell is an editable input. AI-suggested mappings remain unconfirmed until a person reviews them. Formatting colors may provide hints, not authority.

Persist a versioned map containing:

```json
{
  "schema_version": 1,
  "workbook_id": "generated-id",
  "source_version_id": "generated-version-id",
  "fields": [
    {
      "id": "manufacturing_hourly_rate",
      "label": "Manufacturing hourly wage",
      "description": "Base hourly wage used for manufacturing labor costs",
      "sheet": "Inputs",
      "address": "B4",
      "role": "input",
      "value_type": "number",
      "unit": "USD/hour",
      "minimum": 0,
      "maximum": null,
      "aliases": ["manufacturing wages", "hourly labor rate"],
      "editable": true,
      "confirmed": true
    }
  ],
  "output_field_ids": [],
  "business_rules": []
}
```

Extend this schema for output tables and row labels; do not force a product-margin table into a single scalar field. Represent dates, percentages, currencies, nulls, and text explicitly. Store percentages consistently as fractions, with clear display conversion. Define whether a requested percentage change is relative or percentage points. Mapping edits must invalidate affected pending previews.

## 7. Conversation and tool design

Define every operation below as a Claude tool with `strict: true`, `additionalProperties: false` on every object, and explicit `required` lists, and validate every tool input again with Pydantic on the server. Claude's strict schemas do not support numeric or string-length constraints (`minimum`, `maximum`, `minLength`, and similar), so value ranges, units, and lengths must be enforced in server code, never assumed from the schema. Schema validity does not imply business correctness; the server must independently check authorization and meaning.

Expose narrowly scoped operations:

| Operation | Behavior |
| --- | --- |
| `get_workbook_summary` | Returns verified structure, confirmed map, compatibility status, and active version |
| `read_fields` | Reads specified mapped fields from an explicit version |
| `read_range` | Bounded inspection of an explicit sheet/range; never dumps whole workbooks automatically |
| `explain_field` | Returns formulas and bounded dependency evidence for explanation |
| `propose_input_changes` | Produces a validated patch without changing the active workbook |
| `run_scenario` | Calculates a candidate version and structured comparison |
| `compare_versions` | Computes differences between explicit versions |

Applying or restoring versions must be a server-controlled action triggered by a visible user action, outside autonomous model tool execution. The chat may offer an Apply button linked to a specific scenario ID. Typed approval can also show that button; it must not grant blanket future permission.

Each proposed change includes `field_id`, `operation` (`set`, `add`, or `multiply`), typed operand, rationale, base version, and mapping version. The server resolves the address, obtains the old value, computes the new value, checks constraints, and persists the resulting patch. Do not let the model choose arbitrary write addresses.

Conversation rules:

- Ask a focused clarification when different reasonable interpretations would change the outcome materially.
- State assumptions in the preview. Do not silently invent missing data or business rules.
- Resolve “that,” “instead,” and “make it 5%” using persisted conversation and scenario state.
- A revision of an unapplied scenario starts from its original base unless the user explicitly requests compounding. Applied changes start from the active version unless another base is selected.
- Treat a correction about business meaning as a proposed mapping/rule update and ask before persisting it for future requests.
- Explain formula logic without claiming a complete dependency graph when references cannot be resolved. Label incomplete traces.
- Link numerical claims to source fields/ranges and exact versions. Generate before/after values and deltas in code; use those results in the explanation. Distinguish percent change from percentage-point change and handle zero baselines explicitly.
- Explanations must distinguish observed changes, formula dependencies, and speculative causes.
- Bound retrieval volume and model/tool iterations. Handle malformed calls, refusals, unavailable models, API errors, and token limits gracefully.

## 8. State, versions, and transaction behavior

Persist workbooks, workbook versions, mapping versions, conversations, messages, scenarios, scenario changes, jobs, validations, and audit events. Keep immutable file hashes and parent version IDs.

Scenario lifecycle:

`draft → needs_clarification / validating → calculating → ready / failed → applied / discarded / superseded`

Document allowed transitions. A preview changes neither the active workbook nor the original. Applying atomically updates the active-version pointer only after checking the preview's base and mapping versions against current state. If stale, require regeneration. Use idempotency keys so retries cannot duplicate changes.

Undo/restore updates the active pointer to an existing immutable version and records an event; it must restore the workbook and compatible mapping state together. Do not reverse floating-point changes arithmetically. Compare scenarios against an explicitly labeled common base. Persist failed jobs without leaving half-applied workbooks. On restart, mark interrupted calculations failed or safely retry them once.

## 9. User interface

Create a calm desktop-first interface that also works on a tablet:

- Left: workbook name, active version, saved scenarios, and history.
- Center: conversation with suggested starting questions and brief clarification choices.
- Right: verified result cards, output tables, and scenario preview.

Provide empty, uploading, compatibility-review, clarification, calculating, ready, failed, and applied states. Hide cell addresses by default, with “Show source” and administrator detail available. Use business labels throughout the main workflow.

Preview must show: scenario name, explicit base, interpreted request, assumptions, old/new inputs, output differences, warnings/checks, and Apply / Revise / Discard actions. Export identifies the exact exported version. An uncommitted preview may be downloaded only when clearly labeled as a scenario.

Use accessible labels, keyboard navigation, readable number formatting, and status messages beyond color. A formula error must have an understandable message and expandable diagnostic details. Do not expose API keys or stack traces.

## 10. API outline

Implement a small REST API with generated OpenAPI documentation:

- `POST /api/workbooks` — validated upload and inspection job.
- `GET /api/workbooks/{id}` — summary, compatibility, map, active version.
- `PUT /api/workbooks/{id}/map` — reviewed map update with expected mapping version.
- `POST /api/workbooks/{id}/messages` — conversational request and structured response.
- `POST /api/workbooks/{id}/scenarios` — validated proposal using an explicit base.
- `GET /api/scenarios/{id}` — status, patch, computed results, validations.
- `POST /api/scenarios/{id}/apply` — expected active version and idempotency key required.
- `GET /api/workbooks/{id}/versions` — history.
- `POST /api/workbooks/{id}/restore` — target version and expected active version.
- `GET /api/workbooks/{id}/export?version_id=...` — exact immutable artifact.
- `GET /api/jobs/{id}` — progress and recoverable errors.
- `DELETE /api/workbooks/{id}` — delete associated files, metadata, and conversations after explicit UI confirmation.

Polling is sufficient for jobs; streaming chat is optional. Errors should contain a stable code, user message, and diagnostic ID. Bind resource access to the local session; never accept arbitrary filesystem paths from the browser or model.

## 11. Privacy and operational boundaries

Default to localhost only. Describe this as a private prototype for an owner and one invited tester, not a production multi-tenant service. Bind published Docker ports to loopback. Implement the protected remote-testing profile in section 15 before sharing access; require authentication and explicit user/workbook ownership checks on every API request.

Keep the API key server-side in environment configuration. Provide `.env.example`, never commit secrets, and mask sensitive diagnostics. Display that relevant workbook excerpts and conversation text are sent to Anthropic's Claude API; local file storage does not mean local AI inference. Send only necessary context, with hidden content excluded by default.

Treat workbook text, comments, formulas, names, and AI output as untrusted data. Embedded instructions must not override application policy. Do not grant shell, filesystem, network, arbitrary Python, or arbitrary SQL tools to the model. Escape displayed content, prevent path traversal, and keep calculation network isolation separate from the AI-enabled backend. Reject formulas supplied as input strings rather than permitting formula injection.

## 12. Demo and acceptance tests

Include a clearly synthetic product-margin workbook with Inputs, Products, and Summary sheets. Provide prices, volumes, manufacturing hours, hourly wages, materials, fixed overhead, and formulas for revenue, cost, profit, and margin. Use realistic but entirely invented data, and documented overhead allocation. Include at least one product that becomes unprofitable under the wage-increase scenario.

Create trusted expected results independently of the workbook formula evaluation, so tests can catch mistakes in either implementation. Include baseline and modified outputs.

Acceptance checks:

1. Upload, confirm map, run an 8% manufacturing-wage increase, preview correct output changes, apply, export, and reopen with formulas intact.
2. Ask an ambiguous “increase labor costs” question; the assistant clarifies before proposing changes.
3. Revise the pending 8% scenario to 5%; verify it is 5% relative to the original base, not compounded.
4. Explain an output using actual formulas, references, and verified version values.
5. Restore a previous version; verify exact file identity and compatible mapping state.
6. Reject formula-cell writes, formula injection, unknown fields, invalid units/types, and out-of-range inputs at the backend.
7. Catch new formula errors; block Apply on failed validation and retain the original.
8. Handle missing caches, unsupported features, baseline mismatches, engine timeout, and unavailable calculation service honestly.
9. Reject stale previews and repeated Apply requests safely; test mapping changes and simultaneous tabs.
10. Restart the app and recover conversation, history, mappings, and scenario status.
11. Test malicious workbook instructions, excessive ZIP expansion, and path traversal without external data access.
12. Delete a workbook and verify associated local records and files are removed.

Use deterministic fake model responses for CI, including structured-call failures and clarification paths. Include a genuine end-to-end test with the real LibreOffice worker. Live AI smoke testing is optional and credential-dependent; disclose whether it ran. Demo mode must be explicitly scripted and labeled, never presented as genuine AI.

## 13. Implementation sequence and deliverables

1. Scaffold the repo, Compose services, settings, persistence, and demo workbook generator.
2. Implement inspection, compatibility checks, mappings, recalculation, and deterministic scenario tests before AI integration.
3. Implement scenario versions, transactional Apply, restore, export, and restart behavior.
4. Build the user interface and complete the full workflow with scripted demo responses.
5. Integrate structured AI tools, bounded context retrieval, clarifications, and follow-up revisions.
6. Run meaningful tests and document actual limitations, supported workbook features, and real-file onboarding.

Suggested structure: `frontend/`, `backend/app/`, `backend/tests/`, `worker/`, `demo/`, `docs/`, `docker-compose.yml`, `.env.example`, and `README.md`. Keep the API and engine interface documented, but avoid needless service abstraction.

Deliver source code, lockfiles, migrations, sample data generator, automated tests, setup instructions, troubleshooting, and a short walkthrough of the wage-change scenario. `docker compose up --build` should start the application without an API key in demo mode; explain how to enable real AI. Exclude local user data and secrets from git.

Definition of done: the complete demo workflow works in the browser, recalculation is real, tests pass with disclosed exceptions, exported workbooks retain the tested formulas, and the README clearly identifies the compatibility gate required before using the real workbook. Also deliver and test the local and protected remote profiles and AI integration defined below. Prepare deployment configuration and instructions; do not create cloud resources, publish a live tunnel, purchase a domain, or incur API charges without the owner's authorization and credentials.

## 14. Later phases

After reviewing the actual workbook and user sessions, consider native Excel calculation, reviewed formula editing, richer domain rules, new data imports, optimization, recurring reports, and authenticated multi-user hosting. Do not build these until the MVP establishes which are necessary.

## 15. Required development and hosting plan

### Phase A: local development

Run the complete website on the owner's computer using Docker Compose. She accesses it locally during development, then through a protected remote URL in Phase B. No paid hosting or separate frontend hosting is required initially.

Provide three services: a web gateway serving built React assets and proxying `/api`, a FastAPI backend, and the calculation worker. Use a shared job directory with atomic job/result manifests between backend and worker so the worker can run with `network_mode: none`. Keep SQLite writes in the backend; the worker must not independently mutate application tables. Use named persistent volumes or documented bind mounts for the database, original workbooks, versions, and job artifacts. Never store durable user data only in a container's writable layer.

Only the gateway publishes a host port, bound to `127.0.0.1`. The backend is reachable only inside the Compose network; the worker has no listening service. Backend internet egress is required for the Claude API (`api.anthropic.com`); restrict it to that destination where practical. Do not expose the Docker socket, database, LibreOffice interfaces, or host filesystem to users or the model.

Provide a one-command demo startup and a documented switch to real AI. Verify the images on the owner's platform if known; otherwise document x86-64 and Arm64 support and any untested platform. Include a health endpoint that reports component readiness without returning secrets, filenames, user information, or paths.

### Phase B: protected sharing through Cloudflare Tunnel

Deliver an optional Compose profile containing `cloudflared`, connected to the gateway over the private Compose network. Use a named tunnel and a stable hostname for ongoing testing. The owner supplies a domain configured with Cloudflare, the tunnel token, and Cloudflare Access settings. The domain can have a registration cost even when tunnel service is free. Do not assume the owner already has a domain or Cloudflare account.

Document the setup in `docs/remote-access.md`:

1. Create/configure a Cloudflare account and an owned domain, or identify an existing suitable domain.
2. Configure a self-hosted Cloudflare Access application for the entire application hostname, including `/api`, uploads, exports, and job polling.
3. Add an explicit allowlist for the owner's and tester's email addresses. Use email one-time codes or a configured identity provider. No unrestricted Allow/Bypass rule; deny others by default.
4. Create a named tunnel routed to the private gateway. Keep its token in an ignored secret file or environment variable available only to `cloudflared`.
5. Configure backend Access-token verification with expected issuer/team domain and application audience. Verify JWT signatures against Cloudflare's published signing keys using a maintained library, plus issuer, audience, expiry, and allowed identity. Trust neither a raw email header nor an unverified JWT. Map the verified identity to a local allowlisted user record.
6. Start the remote profile and verify access from a separate browser/device. Unauthenticated, expired, wrong-audience, and disallowed-identity requests must fail, including direct API and export requests.
7. Document how to stop sharing and rotate the tunnel token. No inbound router port forwarding is needed.

Keep local development authentication a separate explicit mode. A localhost-only developer identity may be permitted in the local profile, but remote startup must refuse that bypass. Document that Docker network traffic cannot be treated as proof of user identity. Configure one allowed public origin, CSRF protection on state-changing browser requests, safe proxy-header handling, and secure/HttpOnly/SameSite cookies if sessions are used. Do not require a fragile third-party-cookie arrangement: serve frontend and API on the same origin.

Quick Tunnels can be documented as an optional temporary demo mechanism only when application-level login already protects every route. Do not substitute an obscure/random URL for authentication or use a Quick Tunnel as the default durable deployment.

State operational limits plainly: the owner's computer must remain powered on, awake, online, and running Docker and the tunnel. A tunnel provides connectivity; it does not host the application or keep a sleeping computer available. Internet traffic passes through Cloudflare, and relevant AI context goes to Anthropic through the Claude API. Do not promise that all data stays entirely on the local machine.

### Phase C: optional Oracle migration

Provide `docs/oracle-migration.md`; do not provision automatically. Deploy the same containers on a Linux VM with persistent volumes and the same protected tunnel. Check current Always Free quotas and regional availability in the owner's account before choosing an instance; do not hard-code historical free-tier allocations or promise capacity or uptime. Build/pull Arm64-compatible images when using Ampere hardware.

Transfer a consistent database backup and all referenced workbook files; retain hashes and verify a restore before switching the hostname. Keep secrets out of backups, recreate them on the destination, and rotate credentials as appropriate. Test login, workbook export, real recalculation, restart recovery, and job isolation after migration. Keep public application ports closed when the tunnel provides access. Document VM updates, backups, free-tier reclamation risk, resource monitoring, rollback, and stopping the old instance to avoid divergent copies.

### Required operational deliverables and tests

- `docs/local-setup.md`, `docs/remote-access.md`, `docs/backup-restore.md`, and `docs/oracle-migration.md`.
- `.env.example` with harmless placeholders; no real emails, credentials, or active tunnel IDs.
- Consistent backup/restore commands: pause mutations or use SQLite's backup API; include an artifact manifest and verify referenced files/hashes. Never advise copying a live SQLite file blindly or running `down -v` during upgrades.
- A remote-readiness check that rejects missing Access configuration, local auth bypass, unsafe origins, or missing AI settings in real mode.
- Tests for identity verification, authorization on workbook/export/job endpoints, persistent volumes, backup/restore, and remote-profile configuration. Mock Access signing keys for automated tests; disclose any manual Cloudflare setup not exercised.

## 16. Required AI model API integration (Anthropic Claude API)

The application uses the Anthropic Claude API exclusively, through the official `anthropic` Python SDK and the Messages API (`POST /v1/messages`). Do not add OpenAI-compatible shims, a second provider, an agent framework, or raw HTTP calls alongside the SDK. Model behavior, parameters, and prices below reflect Anthropic documentation as of this revision; re-verify every one of them against current official documentation at implementation time and record the date checked.

### Credential ownership and setup

Use an owner-funded Claude Console organization with a dedicated workspace for this application. The invited tester needs no API key and no Anthropic account: she signs into the website, and the backend makes authorized calls with the owner's workspace-scoped key. Do not put a bring-your-own-key input in the tester's UI.

Provide `docs/ai-setup.md` with these owner steps:

1. Sign in to the Claude Console (platform.claude.com), set up API billing, and create a dedicated workspace for this application (Settings → Workspaces; only organization admins can create workspaces). Do not use the Default Workspace: it cannot have a spend limit.
2. Create an API key bound to that single workspace only: a personal key for local development, or a service-account key for a deployed backend. Never use an Admin API key (`sk-ant-admin…`) or a multi-workspace key for model requests.
3. Place the key in a local ignored `.env` file, or a protected Compose secret file, as `ANTHROPIC_API_KEY`. Supply it only to the backend. Never ask the owner to paste it into chat, source code, a browser form, screenshots, or git.
4. Set a monthly workspace spend limit and spend alerts (Workspaces page → select the workspace → Spend limits) and, optionally, workspace rate limits (Rate limits page). Set the application's own budgets below the workspace limit.
5. Configure the model. The default is `claude-opus-5-5` (Claude Opus 5.5). `claude-sonnet-5-5` (Claude Sonnet 5.5) is a lower-cost alternative the owner may choose after comparing quality on the acceptance scenarios. Use exact model IDs as published; never append date suffixes. Document the exact model tested.
6. Run the connection check: `python -m app.ai.check` (or equivalent) calls `client.models.retrieve(<configured model>)` (no token charges) to verify the key, workspace, and model availability, and with an explicit `--live` flag sends one minimal Messages request to confirm end-to-end operation. It prints success and the `anthropic-workspace-id` response header, never the key. Document how to revoke/rotate a key in the Console and restart the backend.

The backend constructs the client with the key passed explicitly from validated settings (`anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0, timeout=…)`). Do not rely on the SDK's implicit credential discovery: the SDK also honours `ANTHROPIC_AUTH_TOKEN`, local `ant auth login` profiles, and workload-identity variables, which could silently route requests to a different identity. The backend container must not mount `~/.config/anthropic/` and should unset those other variables.

Browser code, frontend build arguments, `VITE_*` variables, logs, analytics, worker environments, exported files, and API responses must never contain the key. Limit any local secret file to its owner's access. Include secret-file patterns in `.gitignore` and `.dockerignore`; do not bake credentials into Docker layers. Cloudflare tunnel credentials and the Anthropic API key are different secrets with different scopes.

### Configuration contract

Implement equivalent settings to the following; these are suggested application defaults, not provider quotas or model pricing:

```dotenv
AI_MODE=demo                       # demo | anthropic
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-5-5
ANTHROPIC_EFFORT=medium            # low | medium | high | xhigh | max
ANTHROPIC_REFUSAL_FALLBACK=default # default | off
AI_MAX_OUTPUT_TOKENS=8000          # per request; includes thinking tokens
AI_MAX_MODEL_ROUNDS=6
AI_MAX_TOOL_CALLS_PER_TURN=12
AI_TIMEOUT_SECONDS=120
AI_MAX_RETRIES=2
AI_REQUESTS_PER_USER_PER_MINUTE=10
AI_MAX_CONCURRENT_TURNS=1
AI_DAILY_BUDGET_USD=2.00
AI_MONTHLY_BUDGET_USD=20.00
```

`demo` makes no Anthropic API calls; `anthropic` requires a key and a configured model. Reject invalid configuration (unknown mode, missing key or model, effort value outside the allowed set) rather than silently falling back to scripted answers. The owner can change the suggested budgets.

Model-specific parameter rules for the default model (Claude Opus 5.5), which the provider adapter must respect:

- Thinking is always on. Omit the `thinking` parameter or send `{"type": "adaptive"}`; `{"type": "disabled"}` and `budget_tokens` are rejected with a 400. Control depth and cost with `output_config.effort`. The model default is `medium`; always set it explicitly from `ANTHROPIC_EFFORT`. Thinking tokens are billed as output tokens and count toward `max_tokens`.
- Thinking `display` defaults to `"omitted"` (thinking blocks arrive with empty text). The UI does not show model reasoning, so keep the default.
- Forced tool choice (`tool_choice` of type `any` or `tool`) is rejected with a 400. Use `auto` and steer tool use through the instructions.
- Assistant-message prefill and non-default sampling parameters (`temperature`, `top_p`, `top_k`) are rejected.
- Do not assume another model accepts the same optional parameters; keep per-model parameter rules in the adapter and test them when the model is changed.

Put model price metadata in a separate versioned config file with currency, unit (USD per million tokens), source URL, effective date, and rates for base input, 5-minute cache writes, 1-hour cache writes, cache reads, and output, plus any other billing categories that apply. Example template values for Claude Opus 5.5 at the time of this revision: input $4.00, output $20.00, cache read $0.20, 5-minute cache write 1.25× input, 1-hour cache write 2× input. For Claude Sonnet 5.5: input $2.00, output $10.00, cache read $0.20. Verify against the official pricing page before use. Include an entry for every model that refusal fallback may route to. Require valid pricing metadata before enabling dollar-budget enforcement.

### Backend request lifecycle

Use the official `anthropic` Python SDK and `client.messages.create`. Implement one provider adapter, one versioned system-prompt template, a strict tool registry, and a bounded orchestration loop. No fine-tuning, vector database, or Managed Agents service is needed.

1. Authenticate the website user, then check workbook ownership, rate limits, budgets, and request deduplication.
2. Persist a conversation turn and load its explicit active, mapping, and scenario versions. Retrieve only relevant verified workbook fields and bounded conversation history. Summaries must not discard pending clarification or scenario-base information.
3. Build the request in this order: `system` (a stable, versioned instruction template with no timestamps or per-request IDs), then `tools` (the complete authorized tool list, in a fixed order, every tool `strict: true`), then `messages`. Prior turns are replayed as plain text only (the user's message and the assistant's final explanation), and older turns are replaced by a server-written summary when the history bound is reached. The current turn's state context (active version, pending scenario, open clarification) goes in the current user message, never in `system`. Place a `cache_control` breakpoint after the stable prefix so `system` and `tools` are served from Anthropic's prompt cache across turns. Set `output_config.format` to the JSON schema of the UI payload (step 6) and `output_config.effort` from configuration.
4. Inspect `stop_reason` before reading any content:
   - `tool_use`: for every `tool_use` block, parse its `input` as JSON (never by string matching), validate it with Pydantic, enforce ownership and field permissions again, and execute only registered operations. Return all results for that response in a single user message as `tool_result` blocks whose `tool_use_id` matches the originating call; return failures as `tool_result` with `is_error: true` rather than dropping them. Append the assistant's full `content` list unchanged, including `thinking` blocks, before the tool results. Serialize scenario-changing operations to avoid conflicting bases.
   - `end_turn`: read the structured payload from the text block and validate it (step 6).
   - `max_tokens`: treat the response as incomplete; do not execute partial tool calls or present partial output as a result.
   - `refusal`: the model declined; report this to the user without fabricating a result (see refusal fallback below).
   - `pause_turn` or any unrecognized value: stop the turn safely and report it.
5. Within one turn, the message list is append-only: never edit, reorder, or delete earlier blocks, the `system` text, or the `tools` list mid-turn. On Claude Opus 5.5, each thinking block is bound to the exact conversation prefix that produced it; for accounts created on or after 2026-08-31, replaying a thinking block after the prefix changed returns a 400. Because step 3 never replays earlier turns' thinking blocks, this binding only applies within a turn. Calculations run in the worker, never in the model. If a calculation is slow, persist the in-progress turn (the exact message list, including thinking blocks), return a calculating state to the UI, and when the job finishes resume by appending the `tool_result` to that persisted list. Do not keep a browser request open indefinitely. If a 400 reports an invalid thinking-block signature, strip all `thinking` blocks from the history once, retry, and log the event as a defect.
6. Return an assistant explanation plus a validated UI payload whose discriminator is one of `clarification`, `answer`, `scenario_pending`, `scenario_ready`, or `error`. The payload comes from Claude's structured outputs (`output_config.format`, which can be combined with strict tools in the same request) and is validated again with Pydantic; never parse action instructions out of prose. The backend overwrites or supplies authoritative version IDs, source references, figures, and Apply permissions.
7. Record usage and the request ID (`response._request_id`) for every API call, including intermediate rounds and retries: `input_tokens`, `output_tokens`, `cache_creation_input_tokens` (with its 5-minute/1-hour breakdown when present), `cache_read_input_tokens`, the model that actually served the response (`response.model`), and any fallback iterations reported in `usage`. Save no secrets and do not log full workbook context by default.

API calls to the model are distinct from executing workbook tools. The model proposes a tool call; backend code decides whether it is permitted and performs it. Do not give the model Anthropic server tools (web search, web fetch, code execution, computer use, bash, memory, MCP). Do not expose Apply/restore, shell, arbitrary network, arbitrary Python, or SQL execution as model tools. No direct browser-to-Anthropic requests and no whole-workbook upload to the API (including the Files API) by default.

### Refusal fallback

Claude's safety classifiers can decline a request (`stop_reason: "refusal"`, with a category in `stop_details`). Ordinary workbook questions should rarely trigger this, but the adapter must handle it. With `ANTHROPIC_REFUSAL_FALLBACK=default`, send requests through the SDK's beta Messages endpoint with the server-side fallback beta header and `fallbacks: "default"`, so Anthropic re-runs a declined request on a fallback model within the same call. Verify the current beta header name and request shape in the official refusals-and-fallback documentation before implementing. Record which model served each response and bill it at that model's rate in the ledger. A fallback model does not receive Claude Opus 5.5's thinking blocks. If the final `stop_reason` is still `refusal`, show a neutral message and leave the workbook unchanged. With `off`, a refusal ends the turn with that message.

### Data handling

The Messages API is stateless: Anthropic does not keep conversation state between requests, so the application persists all conversation state locally. Requests are still processed and retained by Anthropic under its current commercial data-retention and usage policies. Do not describe the integration as zero-retention or claim that data never leaves the machine. Zero data retention is a separate arrangement with Anthropic and is not assumed. Link the current Anthropic policy in `docs/ai-setup.md` and in the in-app privacy notice.

### Cost control and recovery

Create a usage ledger tied to user, turn, Anthropic request ID, serving model, token categories (input, cache write, cache read, output), and price-config version. Show estimated daily/monthly spend in an owner-only settings page. Label estimates clearly and reconcile periodically with the Claude Console usage and cost pages (or the Usage and Cost Admin API, which requires an Admin key and is outside the backend's runtime credentials). Local estimates are not an authoritative invoice.

Before each call, atomically reserve an estimated upper bound: input tokens measured with `client.messages.count_tokens` for the exact request (never a third-party tokenizer), priced at the uncached input rate, plus `AI_MAX_OUTPUT_TOKENS` at the output rate of the most expensive model the request could be served by, including fallback models. Reconcile the reservation with actual `usage`. Usage from timeouts or dropped connections is uncertain and must stay reserved or tracked as uncertain until reconciled. Stop before starting another call if a budget, round, tool, or deadline limit would be exceeded. The workspace spend limit is an additional control; no local ledger can guarantee exact provider billing in every network failure case.

Construct the SDK client with `max_retries=0` and implement retries once, in the application, so nested retry loops cannot multiply calls and every attempt gets its own budget reservation. Retry only transient failures (`anthropic.RateLimitError` / 429, HTTP 529 overloaded, other 5xx `anthropic.APIStatusError`, `anthropic.APIConnectionError`, `anthropic.APITimeoutError`) with bounded exponential backoff and jitter, honouring the `retry-after` header and the overall deadline. Catch typed SDK exceptions most-specific-first; never string-match error messages for control flow. Do not retry `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404, e.g. an unknown model), `BadRequestError` (400, e.g. a malformed schema or a rejected parameter), or exhausted workspace spend/credit limits. Distinguish spend-limit or credit exhaustion from transient throttling using the documented error type and status (check the current errors documentation). A timeout may already have incurred usage; never retry application mutations or scenario execution without deduplication.

Show useful messages such as "AI access is not configured," "Today's AI allowance has been reached," or "The AI service timed out; your workbook is unchanged." Preserve draft state, allow deterministic export and history operations when AI is unavailable, and provide owner-only diagnostics, including Anthropic request IDs for support. Handle incomplete responses (`max_tokens`) and refusals without fabricating a successful scenario.

### AI acceptance tests and deliverables

Deliver provider adapter code, the system prompt template, tool and payload schemas, usage ledger migration, mocked Messages API responses, pricing configuration template, `.env.example`, and `docs/ai-setup.md`.

Test:

- `tool_use_id`/`tool_result` correlation, including several tool calls in one response returned in a single message.
- Schema and Pydantic validation failures, and out-of-range values that strict schemas cannot express.
- Unauthorized fields; missing credentials; unknown models; invalid configuration.
- Each `stop_reason` path (`end_turn`, `tool_use`, `max_tokens`, `refusal`, `pause_turn`).
- Model errors mapped to the correct typed exceptions, and bounded application retries with SDK retries disabled.
- Timeout reservations, budget concurrency, and token accounting across rounds, including cache-write and cache-read tokens and fallback-served responses.
- Append-only history within a turn, including resumption after a slow calculation.
- Zero API calls in demo mode.

Add a script that checks built frontend assets and logs for a synthetic secret sentinel. Run live smoke tests only with explicitly supplied credentials and authorization; disclose whether they ran, and do not claim a live test from a mock.

## Technical references

Verify current behavior against official documentation during implementation:

- Claude models overview (IDs, context windows): https://platform.claude.com/docs/en/about-claude/models/overview
- Claude pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Claude tool use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Claude structured outputs and strict tool use: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Claude adaptive thinking and effort: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking and https://platform.claude.com/docs/en/build-with-claude/effort
- Claude prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Claude token counting: https://platform.claude.com/docs/en/build-with-claude/token-counting
- Claude API errors: https://platform.claude.com/docs/en/api/errors
- Claude rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Claude workspaces (workspace keys, spend and rate limits): https://platform.claude.com/docs/en/manage-claude/workspaces
- Claude model migration guide (per-model parameter changes): https://platform.claude.com/docs/en/about-claude/models/migration-guide
- Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
- Cloudflare Tunnel: https://developers.cloudflare.com/tunnel/
- Cloudflare Access: https://developers.cloudflare.com/cloudflare-one/access-controls/
- Oracle Always Free resources: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- openpyxl formula handling: https://openpyxl.readthedocs.io/en/stable/simple_formulae.html
- LibreOffice conversion filters: https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html

These references describe component capabilities; they do not establish compatibility with an unseen workbook.
