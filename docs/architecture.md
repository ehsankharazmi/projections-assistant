# Architecture

## Responsibilities

- **The AI interprets and explains.** It proposes tool calls and writes the explanation.
- **Application code decides.** It checks permissions and meaning, resolves mapped fields to cells, computes new values and every before/after figure, runs the calculation, validates results, and alone can apply or restore versions.
- **The workbook's own formulas** produce every result. LibreOffice does the calculation, never the model.

```
browser ──HTTPS──> gateway (nginx: static app + /api proxy)
                     │ private network
                     v
                  backend (FastAPI) ──HTTPS──> Claude API (anthropic mode only)
                     │  SQLite + immutable files (volume app-data)
                     │  shared jobs directory (volume calc-jobs)
                     v
                  worker (LibreOffice, network_mode: none)
```

## Backend modules (`backend/app`)

| Module | Purpose |
|---|---|
| `config.py` | Settings from environment. Invalid combinations are rejected (anthropic mode without a key, remote profile with local auth, …) |
| `models.py`, `migrations/` | SQLite schema via SQLAlchemy and Alembic, applied at startup |
| `storage.py` | Server-generated paths only; immutable version files; SHA-256 fingerprints |
| `workbook/package.py` | Upload checks: size, ZIP members and expansion, path names, encryption, macros |
| `workbook/inspect.py` | Inspection in a child process with a timeout; preflight compatibility classification |
| `workbook/engine.py` | `WorkbookEngine` interface and the `LibreOfficeEngine` implementation |
| `workbook/compare.py` | Tolerance comparisons, formula normalization rule, structural, error and input checks |
| `calc/protocol.py`, `calc/worker.py` | Shared-directory job protocol; LibreOffice worker process |
| `mapping/` | Workbook map schema, validation against the workbook, heuristic suggestions |
| `services/workbooks.py` | Import, compatibility gate, versions, map updates, restore, export, delete |
| `services/scenarios.py` | Scenario lifecycle, change resolution, comparison, transactional Apply |
| `services/explain.py` | Bounded evidence: field reads, range reads, formula dependency traces |
| `services/jobs.py` | Finalizes calculation results, deadlines, restart recovery, resume callbacks |
| `ai/` | Tool registry, Claude adapter, orchestrator, usage ledger, scripted demo responder, system prompt |
| `auth.py`, `api.py`, `main.py` | Authentication and CSRF checks, REST routes, application factory |
| `ops/` | Backup and restore, remote-readiness check |

## `WorkbookEngine` interface

```python
class WorkbookEngine(Protocol):
    def inspect(self, data: bytes) -> dict
    def apply_inputs(self, data: bytes, writes: list[CellWrite]) -> bytes
    def submit_recalculation(self, job_id: str, data: bytes) -> None
    def collect_recalculation(self, job_id: str) -> RecalcOutcome | None
    def recalculate(self, job_id: str, data: bytes, wait_seconds: float) -> RecalcOutcome | None
    def read_results(self, data: bytes, cells: list[tuple[str, str]]) -> dict
    def validate_round_trip(self, original: dict, recalculated: dict, reference_values) -> dict
```

`LibreOfficeEngine` is the only implementation.

- **`apply_inputs`** (openpyxl) refuses formula cells, protected cells, merged sub-cells and text that starts with `= + - @`.
- **Recalculation** runs in the worker. For each job the worker:
  - creates a fresh LibreOffice profile set to *always recalculate on load*, with macros disabled and external links never updated;
  - uses separate input and output directories;
  - kills the whole process group on timeout;
  - reopens the result to check that every formula cell has a calculated value.

**Extension point:** a native Excel engine for workbooks that need Excel-specific behaviour would implement the same protocol, for example by automating Excel on a Windows host behind the same job protocol. It is intentionally not built.

## Calculation job protocol

The backend writes `<jobs>/<job_id>/input.xlsx`, then `request.json`. The worker:
1. claims the job by creating `claim.json` exclusively,
2. writes `output.xlsx`,
3. writes `result.json` last.

Every manifest is written to a temporary name and renamed into place. The worker never touches the database.

The backend's job monitor finalizes results:
- **Round trip:** fills in the compatibility report.
- **Scenario:** runs the validations and the comparison.

It also fails jobs that are never claimed (60 s) or never finish (the timeout plus a grace period). If a worker restarts while holding a claimed job, the job is retried once.

## Scenario lifecycle

```
draft ──> validating ──> calculating ──> ready ──> applied
  │                          │            │  └──> discarded
  └─> needs_clarification     └─> failed   └─────> superseded
```

Allowed transitions are enforced in `services/scenarios.py` (`TRANSITIONS`).

- **Revisions:** a revision of an unapplied scenario starts from the **same base version** and supersedes the scenario it replaces, so changes never compound.
- **Map edits:** any edit to the workbook map supersedes all pending scenarios.
- **Apply** checks, under a per-workbook lock:
  - the scenario is ready and its validations passed;
  - its base is still the active version;
  - its map version is still the active map;
  - the preview file's hash is unchanged;
  - an `Idempotency-Key` header is present. A repeat with the same key returns the original response.

  It then writes a new immutable version and moves the active pointer in the same transaction.
- **Restore** moves the active pointer, and the matching map version, back to an existing version. Nothing is reversed arithmetically.

## Conversation turns (anthropic mode)

The **request** is built in this order:
- `system`: a stable, versioned template.
- `tools`: all eight tools in a fixed order, each `strict`.
- `messages`: earlier turns as plain text only, then the current user message wrapped in a server-written `<application_state>` block.

The settings are `output_config.format` (UI payload schema), `output_config.effort`, automatic prompt caching, and refusal fallback (beta) when enabled.

The **turn loop:**
- **Before each request:** an upper-bound cost is reserved, using the input measured by `count_tokens` plus `max_tokens`.
- **After each request:** the reservation is reconciled with the reported usage. Retries are bounded and done only by the application; the SDK's own retries are off. If the outcome of a request is unknown (timeout or connection loss), its reservation stays counted as *uncertain*.
- **`tool_use`:**
  - every tool call is validated with Pydantic, and ownership is checked again;
  - results go back in **one** user message, matched by `tool_use_id`;
  - the assistant's content, including thinking blocks, is appended unchanged, so the in-turn history is append-only.
- **`end_turn`:** the structured payload is validated, then made authoritative: scenario status and figures are attached from the database, and a claimed scenario that doesn't exist is dropped.
- **`max_tokens`, `refusal`, `pause_turn`:** the turn ends with a plain message and the workbook is unchanged.
- **Slow calculations:** if `run_scenario` is still calculating after the in-turn wait, the exact message list is saved and the turn waits for the job. When the job finishes, the turn resumes by appending the tool result. An atomic claim guarantees exactly one resume.

## REST API

Interactive documentation is at `/api/docs`, with the OpenAPI spec at `/api/openapi.json`. Errors look like `{"error": {"code", "message", "diagnostic_id", "details?"}}`.

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Component readiness (no secrets, paths or users) |
| `GET /api/me` | Current user, owner flag, AI mode, privacy notice, suggested prompts |
| `GET /api/workbooks` / `POST /api/workbooks` / `POST /api/workbooks/demo` | List; upload (validated upload plus round-trip job); load the synthetic demo |
| `GET /api/workbooks/{id}[?admin=true]` | Summary, compatibility, map, active version, active results |
| `PUT /api/workbooks/{id}/map` | Reviewed map update with `expected_mapping_version_id` |
| `POST /api/workbooks/{id}/confirm-baseline`, `PUT …/reference-outputs` | Administrator baseline confirmation and trusted outputs |
| `POST /api/workbooks/{id}/messages`, `GET …/messages?after=` | Conversation (turns can be deduplicated with `request_key`) |
| `POST /api/workbooks/{id}/scenarios`, `GET …/scenarios` | Direct validated proposal from an explicit base, then calculation; list |
| `GET /api/scenarios/{id}`, `POST …/apply`, `POST …/discard`, `GET …/download` | Status, patch, results, validations; apply (expected active version plus `Idempotency-Key`); discard; labelled preview download |
| `GET /api/workbooks/{id}/versions`, `POST …/restore`, `GET …/compare`, `GET …/export?version_id=` | History, restore, comparison, exact immutable export |
| `GET /api/jobs/{id}` | Job progress and errors |
| `DELETE /api/workbooks/{id}` | Deletes the workbook, its files and its records. Requires `X-Confirm-Delete: <id>` |
| `GET /api/admin/usage`, `POST /api/admin/ai-check` | Owner only: spend estimates, ledger, Claude connection check |

State-changing requests require `X-Requested-With: projections-assistant` and, when an `Origin` header is sent, `Origin` must equal `ALLOWED_ORIGIN`.
