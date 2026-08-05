# status-api/Dockerfile — Admin-panel status endpoint (:9110)
#
# Build context MUST be the monorepo root so the sibling `service-metrics` path
# dependency (../service-metrics) is reachable. docker-compose.prod.yml sets:
#   build:
#     context: .
#     dockerfile: status-api/Dockerfile
#
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder
# ──────────────────────────────────────────────────────────────────────────────
FROM rust:1-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY service-metrics/ ./service-metrics/
COPY status-api/ ./status-api/

WORKDIR /build/status-api
RUN cargo build --release 2>&1

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# ca-certificates: reqwest is built with rustls, which needs the trust store even
# though every call this service makes today is plain HTTP inside the compose
# network.
#
# curl: unlike the other Rust services, this one carries an HTTP client in the
# runtime image on purpose — it is the service whose own healthcheck matters
# most. If status-api is wedged, the panel shows stale data with no indication
# that it is stale, which is the one failure mode this whole stack must not have.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/status-api/target/release/status-api /usr/local/bin/status-api

RUN useradd -r -s /bin/false status_api_user
USER status_api_user

# One port, two jobs: the panel's API and this service's own Prometheus surface.
# Nothing else in the fleet shares a port that way, so it is worth stating —
# /metrics, /health and /ready ride the same axum router as /api/status.
EXPOSE 9110

ENTRYPOINT ["/usr/local/bin/status-api"]
