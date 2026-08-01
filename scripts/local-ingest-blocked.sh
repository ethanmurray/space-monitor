#!/usr/bin/env bash
# Ingest sources that are blocked from GitHub Actions IPs but reachable
# from this machine (Cloudflare and similar block GH IP ranges). Drives the
# same ingest CLI as the daily-ingest GH Actions workflow, just for the
# blocked subset.
#
# Symptom that a source belongs here: the daily workflow reports 403 on the
# feed, or discovers candidates but fails every body fetch, while the same
# URL returns 200 from this machine.
#
# Wire into cron (one-time setup):
#   crontab -e
#   0 14 * * *  /home/ethanmurray/repos/space-monitor/scripts/local-ingest-blocked.sh
#
# Logs land in logs/local-ingest-YYYY-MM-DD.log under the repo.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Load Turso + Anthropic creds from .env
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[local-ingest] .env not found at $REPO/.env" >&2
  exit 1
fi

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/local-ingest-$(date -u +%Y-%m-%d).log"

# Single-flight lock so a slow run doesn't get clobbered by the next cron tick.
LOCK="/tmp/space-monitor-local-ingest.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[local-ingest] another run is in progress, skipping" >> "$LOG"
  exit 0
fi

# Verified 2026-08-01: all of these return 200 from a residential IP but 403
# (or a silent body-fetch failure) from GitHub Actions runner IPs.
SOURCES=(spacenews satellitetoday nasaspaceflight uae)

{
  echo "===== $(date -u +'%Y-%m-%dT%H:%M:%SZ') local-ingest start (sources: ${SOURCES[*]}) ====="
  for src in "${SOURCES[@]}"; do
    echo
    echo ">>>>> $src"
    space-monitor ingest \
      --source "$src" \
      --since 2026-01-01 \
      --max-candidates 100 \
      --max-extractions 300 \
      --rate-limit-secs 1.5 \
      || echo "[local-ingest] $src exited non-zero"
  done
  echo
  echo "===== $(date -u +'%Y-%m-%dT%H:%M:%SZ') local-ingest done ====="
} >> "$LOG" 2>&1
