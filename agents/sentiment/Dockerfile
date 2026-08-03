# agents/sentiment/Dockerfile — Node.js build for the Sentiment Analysis Agent
#
# Build context MUST be the monorepo root so that shared_protos/ is reachable.
# protoLoader.js loads ../../../shared_protos/sentiment_data.proto at runtime
# (relative to agents/sentiment/src/), so the shared_protos directory must be
# present in the image at the same relative location.
#
#   docker-compose.yml sets:
#     build:
#       context: .
#       dockerfile: agents/sentiment/Dockerfile
#
# ARM note: node:20-alpine is multi-arch; all deps (axios, kafkajs, protobufjs,
# redis) are pure-JS or ship aarch64 prebuilds — builds cleanly on Ampere A1.
#
# ──────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS runtime

WORKDIR /app/agents/sentiment

# ── Dependency layer (cached unless package manifests change) ────────────────
COPY agents/sentiment/package.json agents/sentiment/package-lock.json ./
RUN npm ci --omit=dev

# ── Application source ───────────────────────────────────────────────────────
COPY agents/sentiment/src ./src

# ── Shared Protobuf contracts (loaded at runtime by protoLoader.js) ──────────
# Placed at /app/shared_protos so ../../../shared_protos resolves from src/.
COPY shared_protos/ /app/shared_protos/

# Run as the built-in non-root node user for hardening
USER node

# 8090 — on-demand sentiment HTTP API (read by the Rust tool-server's
#        get_news_context proxy).
# 9108 — Prometheus /metrics, /health and /ready.
# Documentation only; docker-compose still has to publish or network these.
EXPOSE 8090 9108

CMD ["node", "src/index.js"]
