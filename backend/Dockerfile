FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGSMITH_TRACING=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    LOCAL_STORAGE_PATH=/var/lib/cka/storage \
    SESSION_UPLOAD_RETENTION_HOURS=24

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY config ./config
COPY prompts ./prompts
COPY scripts ./scripts
COPY alembic.ini ./

RUN mkdir -p /var/lib/cka/storage

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
