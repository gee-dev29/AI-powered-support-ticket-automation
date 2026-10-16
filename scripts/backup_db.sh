#!/usr/bin/env bash
# Database Backup and Rotation Script for Customer Support Automation
# Supports both PostgreSQL (production) and SQLite (staging/local)

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DATABASE_URL="${DATABASE_URL:-sqlite:///./support.db}"

mkdir -p "${BACKUP_DIR}"

echo "[INFO] Starting database backup at $(date -u +"%Y-%m-%dT%H:%M:%SZ")..."

if [[ "${DATABASE_URL}" == sqlite* ]]; then
    DB_PATH="${DATABASE_URL#sqlite:///}"
    TARGET_FILE="${BACKUP_DIR}/sqlite_backup_${TIMESTAMP}.db.gz"
    if [[ -f "${DB_PATH}" ]]; then
        gzip -c "${DB_PATH}" > "${TARGET_FILE}"
        echo "[SUCCESS] SQLite backup saved to: ${TARGET_FILE}"
    else
        echo "[WARN] SQLite database file ${DB_PATH} not found; skipping backup."
    fi
else
    # PostgreSQL pg_dump with custom compressed format
    TARGET_FILE="${BACKUP_DIR}/postgres_backup_${TIMESTAMP}.dump.gz"
    pg_dump "${DATABASE_URL}" --format=custom --no-owner --no-privileges | gzip > "${TARGET_FILE}"
    echo "[SUCCESS] PostgreSQL backup saved to: ${TARGET_FILE}"
fi

# Rotate old backups
echo "[INFO] Pruning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -type f -name "*_backup_*.gz" -mtime "+${RETENTION_DAYS}" -delete
echo "[SUCCESS] Backup rotation complete."
