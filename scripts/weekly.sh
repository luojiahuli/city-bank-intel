#!/usr/bin/env bash
# Weekly financial refresh: A-share statements plus report discovery for the batch.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/state
. .venv/bin/activate 2>/dev/null || true
python3 -m cbi.graph_agents --mode weekly --limit "${FIN_LIMIT:-20}" \
  --group "${FIN_GROUP:-city}" --concurrency "${CBI_CONCURRENCY:-3}" \
  >> data/state/weekly.log 2>&1

if [ "${CBI_AUTO_DEPLOY:-1}" = "1" ]; then
  scripts/deploy.sh >> data/state/weekly.log 2>&1 || echo "deploy skipped (no remote or push failed)"
fi
