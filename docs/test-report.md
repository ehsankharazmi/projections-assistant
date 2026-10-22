# Test report

**Run date:** 2026-10-22
**Machine:** Ubuntu 26.04 x86-64. Python 3.14.4, LibreOffice 26.2.6.3, Node 22.22.1, Docker 29.1.3 with Compose 2.40.3.

## Automated suite: `uv run pytest`

**Result: 100 passed, 0 skipped.** `ruff check` is clean.

| Area | File | What it covers |
|---|---|---|
| API workflow | `test_workflow.py` | Demo import and compatibility gate; the 8% preview checked against the independent model; Apply, export (formulas intact) and the original kept; 5% revision from the same base (not compounded); idempotent repeat of Apply, stale preview from a second tab, missing idempotency key; map change invalidating previews; restore (identical file bytes and hash, matching map); compare; delete (records, files and job folders removed); cross-user access |
| Backend rejections and malicious input | `test_rejections.py` | Writes to formula cells, formula injection, unknown fields, wrong types or units, out-of-range values; invalid maps; ZIP bomb, path traversal, too many parts, encrypted container, macros renamed to `.xlsx`, oversized upload; path traversal in storage; CSRF |
| Inspection | `test_inspection.py` | Demo model outcome (5% vs 8%); reference outputs cover every formula; blocking of volatile, dynamic-array, external and untested functions; hidden-sheet warning; cell limit; tolerances; formula normalization; detection of new formula errors |
| Calculation problems | `test_calculation_failures.py` | Missing stored results lead to baseline confirmation; wrong reference outputs block editing; a baseline mismatch makes the workbook inspect-only; unsupported features; engine timeout; new formula errors block Apply; calculation service unavailable (health shows "degraded", export still works) |
| Scripted demo conversation | `test_demo_chat.py` | Clarification before any change; 8% preview, then revision to 5%; explanation that uses formulas and values from the exact version; restart recovery (conversation, open clarification, interrupted turn) |
| Claude integration (fake client) | `test_ai.py` | Strict schemas; tool-call/result matching; thinking blocks passed back unchanged; full scenario turn with ledger token and cost accounting; request deduplication; unauthorized fields and schema failures as tool errors; workbook text treated as data; `max_tokens`, `refusal` and `pause_turn` stop reasons; malformed reply; an invented scenario id ignored; round limit; authentication, unknown-model and bad-request errors not retried; bounded retries; spend-cap 429 not retried; timeout reservations kept as uncertain; budget limits and concurrent reservations; fallback billed per serving model; per-user rate limit; slow-calculation resume; connection check doesn't leak the key; zero provider calls in demo mode |
| Authentication | `test_auth.py` | Cloudflare Access JWT checks with mocked signing keys: valid, missing, expired, wrong audience, wrong issuer, wrong key, malformed, disallowed email; raw email header ignored; remote profile refuses local auth; origin check; concurrent first requests (regression) |
| Operations | `test_ops.py` | Backup while the app runs; refusal to restore over existing data; `--replace` restore; restore into a fresh directory followed by an app restart; tamper detection; readiness check accept and reject cases; secret sentinel absent from logs and data, and the scanner detects a planted leak |
| **Real LibreOffice** | `test_libreoffice_e2e.py` | Recalculation after edited inputs matches the independent model; **a deliberately wrong stored value is corrected**, proving the forced recalculation setting works; full API workflow on the real engine; zero volumes produce new `#DIV/0!` errors that block Apply |
| **Browser (Playwright, Chromium)** | `test_browser_e2e.py` | Demo load, clarification, 8% preview with the turned-negative highlight, revision to 5%, Apply, download (formulas intact), Restore. Run against the in-process app and against the Docker stack (`E2E_BASE_URL=http://127.0.0.1:8080`). |

Most workflow tests use `fake_recalculate` (in `tests/conftest.py`), a labelled test double that recalculates the demo workbook with the Python model. The LibreOffice and browser tests use the real engine.

## Docker stack (`docker compose up --build`, local profile)

Checked manually on this machine:
- All three images build. The stack starts and `/api/health` reports database `ok`, worker `ok` and AI `demo`.
- Only `127.0.0.1:8080` is published, and the backend port is not reachable from the host.
- The worker runs with `network_mode: none`: it cannot reach the internet or even resolve `backend`.
- The backend and worker run as uid 10001.
- The backend has outbound HTTPS access (needed for the Claude API).
- The browser workflow test passes against the stack, starting from a fresh database.
- `scripts/check_secrets.py` finds nothing in `frontend/dist` or the Compose logs.
- `npm audit` reports 0 vulnerabilities after upgrading Vite to 6.4.5.

## Bugs found during verification (fixed, with regression coverage)

1. A conversation could hang if a calculation finished between the in-turn wait and the turn being saved as "waiting". Resuming now goes through an atomic claim.
2. The worker loop could crash when a finished job folder was cleaned up while it was listing jobs.
3. Concurrent first requests on a fresh database failed with a duplicate-user error, and the page showed "Something went wrong".
4. A message sent while the compatibility check was still running got a misleading "editing not enabled" reply. The chat is now disabled during the check, with a notice, and the backend message says the check is still running.

## Not tested

- **Live Claude API:** no real request was made, because no key was supplied. Run it with the owner's **Live check** on the Usage page after following [ai-setup.md](ai-setup.md). Model behaviour on free-form requests (clarification quality, correct tool use) has not been observed with the real model.
- **Prices in `pricing.json`:** transcribed from documentation, not checked against an invoice (`verified: false`).
- **Cloudflare Tunnel and Access:** dashboard setup, the real login and the tunnel connection. Only the backend's token verification is tested, with mocked keys.
- **Docker backup and restore commands** (`docker compose exec … app.ops.backup`): the same code is covered by `test_ops.py`, but the Compose command lines were not run.
- **Oracle VM migration and Arm64 images.**
- **The real workbook:** not supplied yet. See [real-workbook-onboarding.md](real-workbook-onboarding.md).
- **Accessibility:** there's no automated audit. The UI uses semantic landmarks, labels, a live region and text status labels, but was not checked with a screen reader.
