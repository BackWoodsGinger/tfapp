#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_db_common.sh
source "$SCRIPT_DIR/_db_common.sh"

PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")

load_db_env "$PROJECT_DIR"

export PGPASSWORD="$DB_PASSWORD"

BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.dump"

pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -F c \
    -b \
    -f "$BACKUP_FILE" \
    "$DB_NAME"

find "$BACKUP_DIR" -name "*.dump" -mtime +30 -delete

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "$(date): Backup completed: $(basename "$BACKUP_FILE") ($SIZE)"
