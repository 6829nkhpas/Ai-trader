#!/usr/bin/env bash
# redeploy.sh — one-shot full redeploy on the droplet.
#
# Syncs the repo to origin/main, rebuilds every service, and (re)starts the whole
# stack so any pushed change takes effect. Safe to run repeatedly.
#
#   • Untracked files (notably the gitignored .env) are PRESERVED by the hard
#     reset, so server secrets/config survive.
#   • Rust/Python builds reuse Docker layer cache, so unchanged services rebuild
#     near-instantly; only what actually changed is recompiled.
#   • Old containers keep serving until each new image is built, then `up -d`
#     swaps them — minimal downtime.
#
# Usage (on the droplet):
#   bash /root/Ai-trader/redeploy.sh            # sync + build changed + restart
#   bash /root/Ai-trader/redeploy.sh --force    # also force-recreate ALL containers
#   FORCE_RECREATE=1 bash redeploy.sh           # same as --force
set -euo pipefail

cd "$(dirname "$0")"
export DOCKER_BUILDKIT=1
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml"

FORCE_RECREATE="${FORCE_RECREATE:-0}"
[ "${1:-}" = "--force" ] && FORCE_RECREATE=1

log() { echo "=== [$(date +'%Y-%m-%d %H:%M:%S')] $* ==="; }

log "Redeploy start"

# ── 1. Sync to origin/main (preserves untracked .env) ────────────────────────
log "Syncing to origin/main"
git fetch origin
git reset --hard origin/main
git log --oneline -1

# ── 2. Build every service (sequential — memory-safe on the 8 GB box; cached
#       layers make unchanged services fast) ──────────────────────────────────
for svc in ingestion alpha-terminal technical aggregator predictive quant-rag sentiment tool-server deep-quant; do
  log "Building $svc"
  $COMPOSE build "$svc"
done

# ── 3. (Re)start the stack ───────────────────────────────────────────────────
if [ "$FORCE_RECREATE" = "1" ]; then
  log "Starting stack (force-recreate ALL)"
  $COMPOSE up -d --force-recreate
else
  log "Starting stack (recreate changed)"
  $COMPOSE up -d
fi

log "DEPLOY DONE"
$COMPOSE ps
