# tool-server/Dockerfile — Standalone market-data tool server (:8084)
#
# Build context MUST be the monorepo root so the sibling `quant-core` path
# dependency (../quant-core) is reachable. docker-compose.prod.yml sets:
#   build:
#     context: .
#     dockerfile: tool-server/Dockerfile
#
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder
# ──────────────────────────────────────────────────────────────────────────────
FROM rust:1-slim-bookworm AS builder

# pkg-config/libssl only needed transitively by some crates; rustls is used for
# TLS so no OpenSSL runtime is required, but keep ca-certificates + build tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy the three crates (tool-server + its quant-core and service-metrics path
# dependencies).
COPY quant-core/ ./quant-core/
COPY service-metrics/ ./service-metrics/
COPY tool-server/ ./tool-server/

WORKDIR /build/tool-server
RUN cargo build --release 2>&1

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime — minimal image, no build tools
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# ca-certificates — TLS for the Google News RSS + sentiment service calls.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/tool-server/target/release/tool-server /usr/local/bin/tool-server

RUN useradd -r -s /bin/false tool_server_user
USER tool_server_user

ENV QUANT_TOOL_SERVER_PORT=8084
# 8084 — the tool API. 9107 — the Prometheus surface (/metrics, /health, /ready).
EXPOSE 8084 9107

ENTRYPOINT ["/usr/local/bin/tool-server"]
