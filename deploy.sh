#!/usr/bin/env bash
# deploy.sh — sequential build + launch of the Strat Ai backend stack.
#
# Builds each service one at a time (sequential, not parallel) so the host never
# runs multiple heavy Rust compilations concurrently and OOMs, then brings the
# whole stack up.
#
# Run on the server from the repo root:  bash deploy.sh
set -e
cd "$(dirname "$0")"
export DOCKER_BUILDKIT=1

# Which compose files to use. The default includes the 8 GB memory override,
# correct for the 4vCPU/8GB DigitalOcean droplet.
#
# On a 16 GB host, drop the override so the stack runs with the base file's real
# limits (the override trims ~9.5 GB of limits down to ~6.3 GB and exists only to
# fit an 8 GB box):
#
#   COMPOSE_FILES="-f docker-compose.prod.yml" bash deploy.sh
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.prod.yml -f docker-compose.8gb.yml}"
COMPOSE="docker compose $COMPOSE_FILES"

echo "=== compose files: $COMPOSE_FILES ==="

# Rust services first (heaviest builds), then the Python AI service. `deep-quant`
# is a light pip install, so building it last keeps peak memory during the Rust
# compiles unchanged.
for svc in ingestion alpha-terminal technical aggregator predictive quant-rag sentiment tool-server deep-quant; do
  echo "=== [$(date +%H:%M:%S)] building $svc ==="
  $COMPOSE build "$svc"
done

echo "=== [$(date +%H:%M:%S)] starting stack ==="
$COMPOSE up -d

echo "=== [$(date +%H:%M:%S)] DEPLOY DONE ==="
$COMPOSE ps
