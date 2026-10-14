#!/bin/sh
set -eu

if [ "${MAC_ENVIRONMENT:-}" = "pilot" ]; then
  : "${MAC_DB_HOST:?MAC_DB_HOST is required in pilot mode}"
  : "${MAC_DB_NAME:?MAC_DB_NAME is required in pilot mode}"
  : "${MAC_DB_USER:?MAC_DB_USER is required in pilot mode}"
  : "${MAC_DB_PASSWORD_FILE:?MAC_DB_PASSWORD_FILE is required in pilot mode}"
  db_password="$(cat "$MAC_DB_PASSWORD_FILE")"
  if [ -z "$db_password" ]; then
    echo "Database password secret is empty." >&2
    exit 1
  fi
  encoded_password="$(python -c 'import os; from pathlib import Path; from urllib.parse import quote; print(quote(Path(os.environ["MAC_DB_PASSWORD_FILE"]).read_text().strip(), safe=""))')"
  export MAC_PILOT_DATABASE_URL="postgresql+psycopg://${MAC_DB_USER}:${encoded_password}@${MAC_DB_HOST}:5432/${MAC_DB_NAME}"
fi

python -m alembic upgrade head
exec python -m uvicorn material_allocation_copilot.api.app:create_app \
  --factory --host 0.0.0.0 --port 8000 --no-access-log
