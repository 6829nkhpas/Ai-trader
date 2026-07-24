#!/bin/bash
set -e
cd /root/Ai-trader
# Stop any in-flight deploy
pkill -f deploy.sh 2>/dev/null || true
sleep 2
# Bump all Rust builder images to latest stable (edition2024 support)
for f in ingestion/Dockerfile aggregator/Dockerfile alpha-terminal/Dockerfile \
         agents/quant-rag/Dockerfile agents/predictive/Dockerfile agents/technical/Dockerfile; do
  sed -i -E 's|FROM rust:1\.[0-9]+(-slim-bookworm)|FROM rust:1-slim-bookworm|' "$f"
done
echo "patched ingestion -> $(grep -m1 'FROM rust' ingestion/Dockerfile)"
# Relaunch the sequential build+up, fully detached so it survives SSH close
setsid bash -c 'nohup /root/deploy.sh > /root/deploy.log 2>&1 < /dev/null' &
sleep 1
echo "relaunched deploy"
