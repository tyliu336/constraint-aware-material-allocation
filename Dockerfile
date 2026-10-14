FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY --chown=app:app pyproject.toml README.md alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app src ./src
COPY --chown=app:app scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
RUN python -m pip install ".[pilot]" && chmod 0555 ./scripts/docker-entrypoint.sh

USER app
EXPOSE 8000
ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
