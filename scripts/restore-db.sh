#!/usr/bin/env bash
set -euo pipefail

# Database restore script for SQLite snapshots created by backup-db.sh / POST /v1/admin/backup.
# Usage: ./scripts/restore-db.sh <backup_file> [target_db_path]

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_file.db> [target_db_path]" >&2
    exit 1
fi

BACKUP_FILE="$1"
TARGET_DB="${2:-${APP_DATABASE_PATH:-voice_platform.db}}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file '$BACKUP_FILE' not found." >&2
    exit 1
fi

echo "Verifying backup snapshot integrity before restore..."
INTEGRITY=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;")
if [ "$INTEGRITY" != "ok" ]; then
    echo "ERROR: Backup file '$BACKUP_FILE' failed integrity check: $INTEGRITY" >&2
    exit 1
fi

# If target DB currently exists, create a pre-restore safety copy
if [ -f "$TARGET_DB" ]; then
    PRE_RESTORE_BACKUP="${TARGET_DB}.pre-restore-$(date +%s)"
    echo "Creating safety pre-restore backup of existing database to '${PRE_RESTORE_BACKUP}'..."
    cp -p "$TARGET_DB" "$PRE_RESTORE_BACKUP"
fi

# Ensure target directory exists
mkdir -p "$(dirname "$TARGET_DB")"

echo "Restoring database from '$BACKUP_FILE' to '$TARGET_DB'..."
cp -p "$BACKUP_FILE" "$TARGET_DB"

# Remove any stale WAL / SHM files for the target DB
rm -f "${TARGET_DB}-wal" "${TARGET_DB}-shm"

# Verify restored database
RESTORED_INTEGRITY=$(sqlite3 "$TARGET_DB" "PRAGMA integrity_check;")
if [ "$RESTORED_INTEGRITY" != "ok" ]; then
    echo "ERROR: Restored database failed integrity check: $RESTORED_INTEGRITY" >&2
    exit 1
fi

KEY_COUNT=$(sqlite3 "$TARGET_DB" "SELECT count(*) FROM api_keys;" 2>/dev/null || echo "0")
echo "Restore completed successfully: '$TARGET_DB' (integrity: OK, keys: $KEY_COUNT)"
exit 0
