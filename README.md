# Conversational Workbook Assistant

A private browser application for exploring an Excel workbook in plain language. You describe a change ("increase manufacturing wages by 8% and tell me which products lose money"). The assistant asks a question when the request is ambiguous, changes only approved input cells, recalculates the workbook's **own formulas** with LibreOffice, and shows a before/after preview. You can then Apply it (creating a new immutable version), Revise or Discard it, and export or restore any version.

The AI (Anthropic's Claude API) interprets requests and explains results. Application code validates every change, computes every number, runs the calculation, and controls Apply and Restore. Without an API key the app runs in a clearly labelled **scripted demo mode**.

> **Status:** private prototype for an owner and one invited tester, not a production service. See [Limitations](#assumptions-and-limitations) and [What has and has not been tested](#what-has-and-has-not-been-tested).

## Quick start (demo mode, no API key)

Requires Docker with Compose v2.

```bash
cp .env.example .env && chmod 600 .env     # optional; defaults are demo mode
docker compose up --build
```

Open <http://127.0.0.1:8080> and choose **Load synthetic demo workbook**. The gateway listens on 127.0.0.1 only.

Development without Docker (needs Python 3.14, [uv](https://docs.astral.sh/uv/), LibreOffice and Node 22) is described in [docs/local-setup.md](docs/local-setup.md).

## Turning on real AI (Claude)

1. Follow [docs/ai-setup.md](docs/ai-setup.md) to create a dedicated Claude Console workspace, a workspace-scoped key, and a monthly spend limit.
2. In `.env`, set `AI_MODE=anthropic` and `ANTHROPIC_API_KEY=...` (keep the file `chmod 600`). The default model is `claude-opus-5-5`, and `claude-sonnet-5-5` is a cheaper alternative.
3. Restart with `docker compose up -d`, then run the connection check on the owner's **Usage** page.

The key is only ever given to the backend container. It never reaches the browser, the worker, logs, or exports. The app refuses to start in `anthropic` mode without a key: it does not fall back to the scripted mode silently.

## Walkthrough: the wage-change scenario (demo workbook)

The demo workbook (`Inputs`, `Products`, `Summary`) is entirely invented: six furniture products, wages, a payroll burden rate, and fixed overhead allocated by manufacturing hours.

1. **Load the demo.** The app inspects it and runs the no-change round trip through LibreOffice. It finds that the recalculated results match both the values stored in the file and the independently computed reference outputs, and enables editing.
2. **Ask:** *"Increase labor costs by 8%, keep prices unchanged, and tell me which products lose money."*
   The assistant asks whether "labor costs" means hourly wages, hours, or payroll burden, and for which department, offering choices.
3. **Answer:** *"Hourly wages only, for manufacturing."*
   A preview appears. The manufacturing hourly wage goes from $32.00 to $34.56, and total profit and margins change. **Walnut Side Table becomes unprofitable**, highlighted with a text label as well as colour. All checks pass.
4. **Revise:** *"Actually make that 5%."*
   The preview is replaced by a 5% scenario computed from the **same starting version**: $33.60, not 8% and then 5% on top. Walnut Side Table stays (barely) profitable. The 8% preview is marked superseded.
5. **Apply.** Version 2 becomes active, and the original upload is unchanged.
6. **Export** version 2 and open it in Excel. The formulas are intact and the input cell holds the new value.
7. **Restore** version 1 from the history panel if you want to undo. Restoring moves the active pointer back to the exact original file, together with its map.

In demo mode the replies are scripted and labelled *"Demo mode: scripted responses, not AI"*. Only the phrasings in the suggested prompts are recognized. With `AI_MODE=anthropic`, Claude handles free-form requests through the same tools.

## How it works

| Layer | Implementation |
|---|---|
| Frontend | React + TypeScript + Vite, served by nginx (`gateway`) on the same origin as the API |
| Backend | Python 3.14, FastAPI, Pydantic, SQLAlchemy + Alembic, SQLite (`backend/app`) |
| Workbook inspection and edits | openpyxl. Only mapped, confirmed, editable input cells are written |
| Recalculation | Headless LibreOffice Calc in a separate `worker` container with **no network**, using a shared jobs directory |
| AI | Anthropic Claude API through the official `anthropic` SDK: Messages API, strict tools, structured outputs |

Detailed design (API, `WorkbookEngine` interface, scenario lifecycle, job protocol): [docs/architecture.md](docs/architecture.md).

```
frontend/        React app
backend/app/     API, services, calculation worker, AI integration, demo generator, migrations, ops tools
backend/tests/   pytest suite (API workflow, rejections, AI with a scripted fake client, auth, ops, LibreOffice e2e)
docker/          Dockerfiles (backend, worker, gateway)
gateway/         nginx config
docs/            setup, AI, remote access, backup/restore, Oracle migration, architecture, onboarding a real workbook
scripts/         check_secrets.py (secret-leak scan)
demo/            how to regenerate the synthetic demo workbook
```

## Using your real workbook: the compatibility gate

The real workbook has not been supplied yet. Before anyone edits it, it must pass the gate described in [docs/real-workbook-onboarding.md](docs/real-workbook-onboarding.md):

1. **Upload checks:** `.xlsx` only, at most 10 MiB and 100,000 populated cells, with ZIP-bomb and path checks. Encrypted and macro-enabled files are refused.
2. **Preflight:** external links, data connections, pivots, charts or drawings, array formulas, data tables, iterative calculation, dynamic-array, volatile, date or cube functions, and **any function other than `SUM` and `IF`** block editing. The file can still be inspected.
3. **Round trip:** the file goes through the real edit → LibreOffice → reopen pipeline with no changes. Formulas, names, tables, number formats and inputs must survive, and the recalculated results must match Excel's stored results within tolerance (absolute `1e-8`, relative `1e-6`).
4. If Excel's stored results are missing, an administrator must supply trusted reference outputs or confirm the baseline.
5. **Map review:** suggested fields start unconfirmed and not editable. A person confirms each input and output and its units and limits.

Expect the real workbook to need more formula families to be added. Each one needs its own compatibility fixtures first.

## Assumptions and limitations

- **Calculation fidelity:** LibreOffice is not Excel. Only `SUM`, `IF`, arithmetic and cross-sheet references are covered by tests. Everything else is blocked until fixtures prove it. Function-name scanning is a heuristic, and the round trip is the real gate.
- **Preservation:** openpyxl rewrites the file. Charts, images, drawings, pivots and similar features would be lost, so workbooks containing them stay inspect-only. Conditional formatting and some styling may not be preserved exactly. The original upload is never modified.
- **Exports** of applied versions are LibreOffice-saved files with formulas and freshly calculated stored values. Excel recalculates them on open.
- **Edits are input-value changes only.** No formula edits, structural changes, VBA, refresh of external data, or goal-seek. Asked to change a formula, the assistant explains it instead.
- **Percentages** are stored as fractions. "+8%" on an amount is relative (×1.08), and "points" on a rate are absolute.
- **Data:** files and history stay on the host running Docker. In AI mode, messages and the relevant workbook excerpts (hidden content excluded) are sent to Anthropic and handled under [Anthropic's data-retention policies](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention). This is not zero-retention. With the remote profile, traffic also passes through Cloudflare.
- **Costs:** the usage page shows estimates from token counts and [backend/app/ai/pricing.json](backend/app/ai/pricing.json), which was transcribed on 2026-10-21 and is marked `verified: false` until you check it. It is not an invoice. The Claude Console is authoritative.
- **Scale:** one backend process with in-process locks and SQLite, for one owner and one tester.
- **Demo-mode script** understands only the suggested phrasings.

## What has and has not been tested

Run the suite with `uv run pytest`. See [docs/local-setup.md](docs/local-setup.md#tests).

- **Automated tests (pytest)** cover the full workflow through the REST API, the acceptance checks in the specification, the Claude integration against a *scripted fake client* (no network, no charges), Cloudflare Access verification with mocked signing keys, backup and restore, and the readiness check.
- **Genuine end-to-end runs:** LibreOffice tests and a Playwright browser test of the whole workflow, run both in-process and against the Docker stack.
- **Calculation in most workflow tests** uses a clearly marked test double for the LibreOffice worker. It evaluates the demo workbook with the independent Python model. The genuine LibreOffice tests (`test_libreoffice_e2e.py`) are skipped automatically when `soffice` is missing.
- **Not run by the automated suite:** a live Claude API call (needs your key and approval), a real Cloudflare Tunnel and Access setup, and an Oracle VM migration. These are documented manual steps.

The exact status of each item on this machine is recorded in [docs/test-report.md](docs/test-report.md).
