#!/bin/sh
set -eu

if [ -n "${POSTGRES_DB:-}" ]; then
  echo "Waiting for PostgreSQL..."
  python - <<'PY'
import os
import sys
import time

import psycopg

for attempt in range(60):
    try:
        psycopg.connect(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ.get("POSTGRES_USER", "tfapp"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            host=os.environ.get("POSTGRES_HOST", "db"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            connect_timeout=3,
        ).close()
        break
    except Exception:
        time.sleep(1)
else:
    print("PostgreSQL not ready after 60s", file=sys.stderr)
    sys.exit(1)
PY
fi

echo "Running migrations..."
python manage.py migrate --noinput

exec "$@"
