#!/usr/bin/env bash
set -euo pipefail

# Database backup script using SQLite online VACUUM INTO
# Safe to run while WAL database is actively serving reads and writes.

DB_PATH="${APP_DATABASE_PATH:-voice_platform.db}"
if [ ! -f "$DB_PATH" ]; then
    if [ -f "data/app.db" ]; then
        DB_PATH="data/app.db"
    fi
fi

BACKUP_DIR="${BACKUP_DIR:-data/backups}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/app-backup-${TIMESTAMP}-$$.db"
rm -f "$BACKUP_FILE"

echo "Creating atomic database snapshot from '${DB_PATH}' to '${BACKUP_FILE}'..."

sqlite3 "$DB_PATH" "VACUUM INTO '${BACKUP_FILE}';"

# Verify snapshot integrity
INTEGRITY=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;")
if [ "$INTEGRITY" != "ok" ]; then
    echo "ERROR: Backup integrity verification failed: ${INTEGRITY}" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi

SIZE=$(ls -lh "$BACKUP_FILE" | awk '{print $5}')
echo "Backup completed successfully: ${BACKUP_FILE} (size: ${SIZE}, integrity: OK)"

# Retain the 20 most recent backups; remove older ones
if [ -d "$BACKUP_DIR" ]; then
    ls -t "${BACKUP_DIR}"/app-backup-*.db 2>/dev/null | tail -n +21 | xargs rm -f 2>/dev/null || true
fi

exit 0
