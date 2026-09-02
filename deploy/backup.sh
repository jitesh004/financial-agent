#!/usr/bin/env bash
#
# Nightly backup: the ledger AND the statement files.
#
# Both halves are needed, and this is the one thing about the PostgreSQL move
# that is easy to get wrong. The per-user snapshots the app takes before every
# destructive action cover rows only - they always did, even when the ledger
# was a single SQLite file. The statement files live on a volume beside it, and
# a manually uploaded statement is the one thing in this application that
# cannot be regenerated from anything. Restore needs the pair.
#
# Usage, from the directory holding docker-compose.prod.yml:
#
#     ./deploy/backup.sh                  # writes into ./backups
#     BACKUP_DIR=/mnt/big ./deploy/backup.sh
#
# Install as a cron job (03:15 daily), logging where you will see it:
#
#     crontab -e
#     15 3 * * * cd /opt/financial-agent && ./deploy/backup.sh >> /var/log/fa-backup.log 2>&1
#
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
COMPOSE="docker compose -f docker-compose.prod.yml"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%FT%TZ)] backing up to $BACKUP_DIR"

# --- the ledger ------------------------------------------------------------
# pg_dump runs inside the db container, as the superuser rather than the app's
# ordinary role: row-level security applies to the app role, so dumping as it
# would faithfully export nothing at all.
$COMPOSE exec -T db pg_dump \
    --username postgres \
    --dbname financial_agent \
    --format custom \
    --no-owner \
  > "$BACKUP_DIR/ledger.$STAMP.dump"

# --- the files -------------------------------------------------------------
# Statement PDFs, the Gmail cache and the app's own per-user snapshots.
tar -czf "$BACKUP_DIR/files.$STAMP.tar.gz" -C . data

# --- verify ----------------------------------------------------------------
# A backup nobody has read is a hope, not a backup. pg_restore --list fails
# loudly on a truncated or corrupt dump, which is most of what goes wrong.
$COMPOSE exec -T db pg_restore --list \
  < "$BACKUP_DIR/ledger.$STAMP.dump" > /dev/null
tar -tzf "$BACKUP_DIR/files.$STAMP.tar.gz" > /dev/null

LEDGER_SIZE="$(du -h "$BACKUP_DIR/ledger.$STAMP.dump" | cut -f1)"
FILES_SIZE="$(du -h "$BACKUP_DIR/files.$STAMP.tar.gz" | cut -f1)"
echo "  ledger $LEDGER_SIZE, files $FILES_SIZE - both verified"

# --- prune -----------------------------------------------------------------
find "$BACKUP_DIR" -maxdepth 1 -name 'ledger.*.dump'   -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'files.*.tar.gz'  -mtime "+$KEEP_DAYS" -delete

echo "[$(date -u +%FT%TZ)] done"

# ---------------------------------------------------------------------------
# To restore, with the stack stopped:
#
#     docker compose -f docker-compose.prod.yml up -d db
#     docker compose -f docker-compose.prod.yml exec -T db \
#         psql -U postgres -c 'DROP DATABASE IF EXISTS financial_agent'
#     docker compose -f docker-compose.prod.yml exec -T db \
#         psql -U postgres -c 'CREATE DATABASE financial_agent OWNER financial_agent'
#     docker compose -f docker-compose.prod.yml exec -T db \
#         pg_restore -U postgres -d financial_agent --no-owner < backups/ledger.<stamp>.dump
#     tar -xzf backups/files.<stamp>.tar.gz -C .
#     docker compose -f docker-compose.prod.yml up -d
#
# The app re-applies its schema and row-level security policies on the next
# boot, so a dump restored --no-owner comes back fully guarded.
# ---------------------------------------------------------------------------
