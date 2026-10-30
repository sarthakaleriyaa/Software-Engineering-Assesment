# One image for the API, the ingestion worker and the migration job;
# docker-compose.yml picks the command for each service.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    UPLOAD_DIR=/data/uploads \
    DOCUMIND_DISABLE_DOTENV=1

# Unprivileged user; uploads live on a volume owned by that user.
RUN useradd --create-home --uid 10001 documind \
    && mkdir -p /data/uploads \
    && chown documind:documind /data/uploads

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

USER documind

EXPOSE 8000

# PORT is set by most hosting platforms; defaults to 8000 locally.
# RUN_MIGRATIONS_ON_START=true runs `alembic upgrade head` first (for hosts
# without a pre-deploy step); if it fails, the server does not start.
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS_ON_START:-false}\" = true ]; then alembic upgrade head || exit 1; fi; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
