#!/usr/bin/env bash
# Install (or replace) the daily/weekly collection jobs for the current user.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MARK="# city-bank-intel"
JOBS="12 6 * * * $ROOT/scripts/daily.sh $MARK
24 7 * * 0 $ROOT/scripts/weekly.sh $MARK"

current="$(crontab -l 2>/dev/null || true)"
kept="$(printf '%s\n' "$current" | grep -v "$MARK" || true)"
{
  [ -n "$kept" ] && printf '%s\n' "$kept"
  printf '%s\n' "$JOBS"
} | crontab -

echo "Installed:"
crontab -l | grep "$MARK"
echo "Logs: $ROOT/data/state/daily.log, $ROOT/data/state/weekly.log"
