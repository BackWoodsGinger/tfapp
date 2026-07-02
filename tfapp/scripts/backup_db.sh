#!/bin/bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")

# Ask Django for the database settings
eval "$(
cd "$PROJECT_DIR"

python manage.py shell -c "
from django.conf import settings
db = settings.DATABASES['default']
print(f'export DB_NAME=\"{db[\"NAME\"]}\"')
print(f'export DB_USER=\"{db[\"USER\"]}\"')
print(f'export DB_PASSWORD=\"{db[\"PASSWORD\"]}\"')
print(f'export DB_HOST=\"{db[\"HOST\"] or \"localhost\"}\"')
print(f'export DB_PORT=\"{db[\"PORT\"] or \"5432\"}\"')
"
)"

export PGPASSWORD="$DB_PASSWORD"

pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -F c \
    -b \
    -v \
    -f "$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump" \
    "$DB_NAME"

find "$BACKUP_DIR" -name "*.dump" -mtime +30 -delete

echo "$(date): Backup completed successfully."