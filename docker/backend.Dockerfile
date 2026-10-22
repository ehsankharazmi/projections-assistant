# Backend API (FastAPI). Runs as a non-root user; holds the Anthropic API key at runtime only.
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
RUN uv sync --frozen --no-dev
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/jobs /backups && chown -R app:app /data /backups
USER app
ENV DATA_DIR=/data JOBS_DIR=/data/jobs DEMO_DIR=/data/demo
EXPOSE 8000
CMD ["python", "-m", "app.main"]
