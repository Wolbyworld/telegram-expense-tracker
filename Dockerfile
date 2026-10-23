FROM python:3.12-slim

# WeasyPrint + OpenCV system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libffi-dev libcairo2 \
    libglib2.0-0 libseccomp2 clamav && \
    rm -rf /var/lib/apt/lists/*

# Application dependency only: no host daemon, service, cron, or network changes.
# Fresh signatures are required for email/CLI extraction and fail closed if stale.
RUN freshclam --stdout

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

ARG VCS_REF=unrecorded
LABEL org.opencontainers.image.revision=$VCS_REF

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY run_polling.py ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
