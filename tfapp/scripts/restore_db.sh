#!/bin/bash

# ============================================================================
# PostgreSQL Restore Script
#
# Usage:
#   ./scripts/restore_db.sh backups/myproject_2026-07-02_02-00.dump
# ============================================================================

set -e

if [ $# -ne 1 ]; then
    echo "Usage: $0 <backup_file.dump>"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Backup file not found: $BACKUP_FILE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_db_common.sh
source "$SCRIPT_DIR/_db_common.sh"

PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

load_db_env "$PROJECT_DIR"

export PGPASSWORD="$DB_PASSWORD"

echo
echo "WARNING!"
echo "This will completely replace the contents of:"
echo "    $DB_NAME"
echo
read -p "Continue? (yes/no): " ANSWER

if [ "$ANSWER" != "yes" ]; then
    echo "Restore cancelled."
    exit 0
fi

echo
echo "Dropping existing connections..."

psql \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d postgres \
    -c "SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname='$DB_NAME'
        AND pid <> pg_backend_pid();"

echo "Recreating database..."

dropdb \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    "$DB_NAME"

createdb \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    "$DB_NAME"

echo "Restoring backup..."

pg_restore \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --verbose \
    "$BACKUP_FILE"

echo
echo "Restore completed successfully."
