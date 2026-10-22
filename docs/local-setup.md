# Local setup (Phase A)

## With Docker (recommended)

Requirements: Docker Engine with Compose v2. Ubuntu: `sudo apt install docker.io docker-compose-v2`, then `sudo usermod -aG docker $USER` and log in again.

```bash
cp .env.example .env && chmod 600 .env
docker compose up --build            # first build downloads LibreOffice into the worker image
```

Open <http://127.0.0.1:8080>.

| Service | Role | Network |
|---|---|---|
| `gateway` | nginx serving the built React app, proxying `/api` | publishes `127.0.0.1:8080` only |
| `backend` | FastAPI API, SQLite, AI calls | private Compose network, plus outbound internet for the Claude API |
| `worker` | headless LibreOffice recalculation | **no network** (`network_mode: none`), non-root, 1 CPU / 1 GiB, read-only root filesystem |

Durable data lives in named volumes:
- `app-data`: database, original uploads, immutable versions, previews.
- `calc-jobs`: the shared job directory between the backend and the worker.
- `backups`: backup sets.

Nothing durable is kept in a container's writable layer.

Health: <http://127.0.0.1:8080/api/health> reports the database, the calculation worker (from its heartbeat file) and the AI mode. It shows no secrets, paths, file names or user information.

Useful commands:

```bash
docker compose logs -f backend worker
docker compose down          # stops containers; volumes (your data) are kept
```

Never run `docker compose down -v` unless you intend to **delete all data**. For upgrades, use `docker compose up -d --build`.

### Platforms

The images use multi-architecture base images (`python:3.14-slim`, `node:22-alpine`, `nginx:1.29-alpine`), and Debian's `libreoffice-calc-nogui` is available for amd64 and arm64. Images were built on x86-64 only. See [test-report.md](test-report.md); Arm64 (e.g. Oracle Ampere) is expected to work but is untested.

## Without Docker (development)

Requirements: Python 3.14, [uv](https://docs.astral.sh/uv/), LibreOffice (`sudo apt install libreoffice-calc-nogui`) and Node 22 (`sudo apt install nodejs npm`).

```bash
uv sync                                       # Python deps from uv.lock
WORKER_MODE=inprocess uv run python -m app.main   # API on http://127.0.0.1:8000, worker thread in-process
```

In a second terminal:

```bash
cd frontend && npm ci && npm run dev          # http://127.0.0.1:5173, proxies /api to :8000
```

When you use the Vite dev server, set `ALLOWED_ORIGIN=http://127.0.0.1:5173` in `.env`: state-changing requests must come from the configured origin. To run the worker as a separate process the way Docker does, run `uv run python -m app.calc.worker` with the same `JOBS_DIR`, and set `WORKER_MODE=external`.

Regenerate the demo files: `uv run python -m app.demo.generate demo/generated`.

## Tests

```bash
uv run pytest                     # full suite; LibreOffice tests are skipped if soffice is missing
uv run pytest -m libreoffice      # only the genuine LibreOffice end-to-end tests
uv run playwright install chromium   # once, for the browser test
uv run pytest -m browser          # browser workflow test (needs LibreOffice and `npm run build`)
E2E_BASE_URL=http://127.0.0.1:8080 uv run pytest -m browser   # same test against the running Docker stack
uv run ruff check backend scripts
cd frontend && npm run typecheck && npm run build
python scripts/check_secrets.py --sentinel "$SENTINEL" frontend/dist
```

To check for leaked secrets, set `ANTHROPIC_API_KEY` to a fake sentinel such as `sk-ant-SENTINEL-123`, build the frontend, exercise the app, then scan `frontend/dist` and your saved logs with `check_secrets.py`. Any hit is a leak.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Workbook stuck at "pending", then "calculation service is not responding" | The worker isn't running. Run `docker compose ps` and `docker compose logs worker`. |
| "LibreOffice is not installed on the calculation worker" | Development mode without LibreOffice: install `libreoffice-calc-nogui`. |
| Workbook is "inspect only" | Read the compatibility review. Unsupported features or a round-trip mismatch block editing; see [real-workbook-onboarding.md](real-workbook-onboarding.md). |
| Every change is rejected with "This request was blocked for security reasons" | `ALLOWED_ORIGIN` doesn't match the address in your browser. |
| Startup error `AI_MODE=anthropic requires ANTHROPIC_API_KEY` | Set the key or switch back to `AI_MODE=demo`. |
| "The AI service rejected the request" | Often the workspace spend limit; check the Claude Console. The diagnostics on the owner's Usage page show the request ID. |
| Port 8080 is in use | Change the left side of `127.0.0.1:8080:8080` in `docker-compose.yml`. |
