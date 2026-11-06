#!/usr/bin/env bash
# Daily incremental collection: rotated news + governance, plus a few NFRA pages.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/state
. .venv/bin/activate 2>/dev/null || true
python3 -m cbi.graph_agents --mode daily --limit "${NEWS_LIMIT:-20}" \
  --penalties "${PENALTY_PAGES:-10}" --concurrency "${CBI_CONCURRENCY:-4}" \
  >> data/state/daily.log 2>&1

if [ "${CBI_AUTO_DEPLOY:-1}" = "1" ]; then
  scripts/deploy.sh >> data/state/daily.log 2>&1 || echo "deploy skipped (no remote or push failed)"
fi
