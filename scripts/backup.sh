#!/usr/bin/env bash
# Daily backup of the Turso libsql database.
#
# Pipeline:
#   1. Sync local.db replica from Turso (point-in-time consistent).
#   2. Atomic sqlite .backup → snapshot.tmp.db (safe even if app is writing).
#   3. Write two outputs to backups/:
#        space-monitor-YYYY-MM-DD.sql.gz   (text dump, diffable, future-proof)
#        space-monitor-YYYY-MM-DD.db.gz    (binary, faster to restore)
#   4. Optionally upload both to Google Drive via the gws CLI, if
#      BACKUP_DRIVE_FOLDER_ID is set in .env.
#   5. Prune local backups older than 30 days and (if Drive is wired up)
#      prune Drive backups older than 30 days too.
#
# Drive upload setup (one-time):
#   1. gws auth login -s drive,docs          # refresh OAuth token
#   2. gws drive files create --json '{"name":"space-monitor-backups","mimeType":"application/vnd.google-apps.folder"}' --format json
#      -> note the "id" field in the response
#   3. echo "BACKUP_DRIVE_FOLDER_ID=<that-id>" >> .env
#
# Wire into systemd-user timer (see space-monitor-backup.{service,timer}).
# Logs land in logs/backup-YYYY-MM-DD.log under the repo.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[backup] .env not found at $REPO/.env" >&2
  exit 1
fi

LOG_DIR="$REPO/logs"
BAK_DIR="$REPO/backups"
mkdir -p "$LOG_DIR" "$BAK_DIR"

DATE=$(date -u +%Y-%m-%d)
LOG="$LOG_DIR/backup-$DATE.log"

# Single-flight lock so two timer ticks can't collide.
LOCK="/tmp/space-monitor-backup.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[backup] another run is in progress, skipping" >> "$LOG"
  exit 0
fi

drive_enabled=0
if [[ -n "${BACKUP_DRIVE_FOLDER_ID:-}" ]]; then
  drive_enabled=1
fi

{
  echo "===== $(date -u +'%Y-%m-%dT%H:%M:%SZ') backup start ====="

  SNAP="$BAK_DIR/snapshot.tmp.db"
  SQL_BAK="$BAK_DIR/space-monitor-$DATE.sql.gz"
  DB_BAK="$BAK_DIR/space-monitor-$DATE.db.gz"

  echo "[backup] syncing + snapshotting + dumping (one Python pass)..."
  rm -f "$SNAP"
  SNAP="$SNAP" SQL_BAK="$SQL_BAK" DB_BAK="$DB_BAK" python3 - <<'PYEOF'
import gzip
import os
import shutil
import sqlite3
import libsql_experimental as libsql

# 1. Pull remote into local.db (read replica becomes consistent point-in-time)
conn = libsql.connect(
    "local.db",
    sync_url=os.environ["TURSO_DATABASE_URL"],
    auth_token=os.environ["TURSO_AUTH_TOKEN"],
)
conn.sync()

# 2. Atomic snapshot using sqlite3's online backup API. This is safe even
#    if another process is writing to local.db — sqlite handles locking.
src = sqlite3.connect("local.db")
dst = sqlite3.connect(os.environ["SNAP"])
src.backup(dst)
src.close()
dst.close()

# 3. Write binary form (gzipped). Faster restore.
with open(os.environ["SNAP"], "rb") as fin, gzip.open(os.environ["DB_BAK"], "wb", compresslevel=6) as fout:
    shutil.copyfileobj(fin, fout, length=1 << 20)

# 4. Write SQL form (gzipped). Diffable, text-restorable.
snap = sqlite3.connect(os.environ["SNAP"])
with gzip.open(os.environ["SQL_BAK"], "wt", encoding="utf-8", compresslevel=6) as fout:
    for line in snap.iterdump():
        fout.write(line)
        fout.write("\n")
snap.close()

os.unlink(os.environ["SNAP"])
PYEOF

  echo "[backup] sizes:"
  du -h "$SQL_BAK" "$DB_BAK"

  if [[ "$drive_enabled" -eq 1 ]]; then
    echo "[backup] uploading to Drive folder $BACKUP_DRIVE_FOLDER_ID"
    for f in "$SQL_BAK" "$DB_BAK"; do
      name=$(basename "$f")
      if gws drive files create \
           --upload "$f" \
           --json "{\"name\": \"$name\", \"parents\": [\"$BACKUP_DRIVE_FOLDER_ID\"]}" \
           --format json > /dev/null 2>>"$LOG"; then
        echo "[backup]   uploaded $name"
      else
        echo "[backup]   WARN: Drive upload failed for $name"
      fi
    done
  else
    echo "[backup] BACKUP_DRIVE_FOLDER_ID not set — skipping Drive upload"
  fi

  echo "[backup] pruning local backups older than 30 days"
  find "$BAK_DIR" -maxdepth 1 -name "space-monitor-*.gz" -mtime +30 -print -delete

  if [[ "$drive_enabled" -eq 1 ]]; then
    echo "[backup] pruning Drive backups older than 30 days"
    CUTOFF=$(date -u -d "30 days ago" +%Y-%m-%dT%H:%M:%S)
    list_json=$(gws drive files list \
      --params "{\"q\": \"'$BACKUP_DRIVE_FOLDER_ID' in parents and trashed = false and modifiedTime < '$CUTOFF' and name contains 'space-monitor-'\", \"fields\": \"files(id,name,modifiedTime)\", \"pageSize\": 100}" \
      --format json 2>>"$LOG") || true
    if [[ -n "$list_json" ]]; then
      printf '%s' "$list_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for f in data.get("files", []) or []:
    print(f["id"], f["name"])
' | while read -r fid fname; do
        if [[ -z "$fid" ]]; then continue; fi
        if gws drive files delete --params "{\"fileId\": \"$fid\"}" > /dev/null 2>>"$LOG"; then
          echo "[backup]   deleted Drive: $fname"
        else
          echo "[backup]   WARN: Drive delete failed for $fname"
        fi
      done
    fi
  fi

  echo "===== $(date -u +'%Y-%m-%dT%H:%M:%SZ') backup done ====="
} >> "$LOG" 2>&1
