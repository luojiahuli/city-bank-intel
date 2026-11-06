#!/usr/bin/env bash
# Commit updated static data and push. GitHub Actions deploys Cloudflare Pages.
# Falls back to a direct wrangler deploy when no git remote is configured.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No git remote 'origin'. Create the GitHub repo and run:"
  echo "  git remote add origin git@github.com:luojiahuli/city-bank-intel.git"
  exit 1
fi

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "No data changes to deploy."
  exit 0
fi

git add frontend/data data/news data/enrichment data/governance data/penalties
git commit -m "chore: refresh public bank data $(date +%F)"
git push origin HEAD

if command -v wrangler >/dev/null 2>&1 && [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
  wrangler pages deploy frontend --project-name city-bank-intel --branch main
fi
