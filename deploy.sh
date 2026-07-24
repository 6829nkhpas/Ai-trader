#!/usr/bin/env bash
# deploy.sh — sequential build + launch of the Strat Ai backend stack.
#
# Builds each service one at a time (sequential, not parallel) so the 4vCPU/8GB
# droplet never runs multiple heavy Rust compilations concurrently and OOMs,
# then brings the whole stack up with the 8 GB memory-tuned overrides.
#
# Run on the droplet from the repo root:  bash deploy.sh
set -e
cd "$(dirname "$0")"
export DOCKER_BUILDKIT=1
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml"

# Rust services first (heaviest builds), then the Python AI service. `deep-quant`
# is a light pip install, so building it last keeps peak memory during the Rust
# compiles unchanged.
for svc in ingestion alpha-terminal technical aggregator predictive quant-rag sentiment deep-quant; do
  echo "=== [$(date +%H:%M:%S)] building $svc ==="
  $COMPOSE build "$svc"
done

echo "=== [$(date +%H:%M:%S)] starting stack ==="
$COMPOSE up -d

echo "=== [$(date +%H:%M:%S)] DEPLOY DONE ==="
$COMPOSE ps
