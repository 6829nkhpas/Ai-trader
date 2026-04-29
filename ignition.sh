#!/bin/bash

# Trap SIGINT to gracefully shut down all background processes when the user hits Ctrl+C
trap 'echo "Shutting down system..."; kill $(jobs -p); docker-compose down; exit' SIGINT
# Export environment variables for all downstream processes
set -a
[ -f .env ] && source .env
set +a

echo "Starting infrastructure..."
docker-compose up -d

echo "Waiting 10 seconds for Kafka and QuestDB to initialize..."
sleep 10

echo "Starting Rust Ingestion Service..."
(cd ingestion && cargo run --release) &

echo "Starting Rust Technical Agent..."
(cd agents/technical && cargo run --release) &

echo "Starting Node Sentiment Agent..."
(cd agents/sentiment && npm start) &

echo "Starting Rust Aggregator..."
(cd aggregator && cargo run --release) &

echo "Starting Next.js Frontend..."
(cd frontend && npm run dev) &

echo "Power Phase 3.1: Infrastructure Orchestration & Ignition FULLY ENGAGED."
echo "Press Ctrl+C to stop all services."

# Wait for background processes to keep script running
wait
