# ingestion/Dockerfile — Multi-stage Rust build for the AI-Trade ingestion service
#
# Build context MUST be the monorepo root (not ./ingestion) so that
# ../shared_protos/ is reachable. docker-compose.yml sets:
#   build:
#     context: .
#     dockerfile: ingestion/Dockerfile
#
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder
# ──────────────────────────────────────────────────────────────────────────────
FROM rust:1-slim-bookworm AS builder

# Install system dependencies required by rdkafka (cmake-build feature):
#   cmake          — builds librdkafka from source
#   pkg-config     — locates OpenSSL headers
#   libssl-dev     — OpenSSL headers (TLS for Kite WSS + Kafka SASL)
#   clang / libclang-dev — required by rdkafka bindgen step on some platforms
#   curl           — used only for Cargo vendor check (removed post-build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    build-essential \
    libcurl4-openssl-dev \
    libsasl2-dev \
    zlib1g-dev \
    libzstd-dev \
    pkg-config \
    libssl-dev \
    clang \
    libclang-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency pre-cache layer ────────────────────────────────────────────────
# Copy manifests and build script first. By building a "hollow" binary before
# copying the real source, we cache the (slow) dependency compilation layer.
# Subsequent rebuilds that only change src/ skip this expensive step entirely.
COPY ingestion/Cargo.toml ingestion/Cargo.lock* ingestion/build.rs ./
COPY shared_protos/ ./shared_protos/

# The `service-metrics` path dependency (../service-metrics in Cargo.toml).
# This crate is flattened into /app, so the sibling must sit at /service-metrics
# for that relative path to resolve — unlike tool-server, which keeps both
# crates side by side under /build.
COPY service-metrics/ /service-metrics/

# Create a minimal fake main so cargo build --release has something to compile.
# Also rewrite the proto path: build.rs uses ../shared_protos, but in this build
# context shared_protos is copied to ./shared_protos.
RUN mkdir src && echo 'fn main() {}' > src/main.rs \
    && sed -i 's|\.\./shared_protos|./shared_protos|g' build.rs

# Compile all dependencies (this layer is cached as long as Cargo.toml is unchanged)
RUN cargo build --release --features kafka 2>&1

# Remove the fake build artefacts so the real source compiles cleanly
RUN rm -rf src target/release/ingestion target/release/deps/ingestion*

# ── Real source build ─────────────────────────────────────────────────────────
COPY ingestion/src ./src

RUN cargo build --release --features kafka 2>&1

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime — minimal image, no build tools
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

# ca-certificates — required for TLS (Kite WSS endpoint)
# libssl3         — OpenSSL runtime shared library
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    libcurl4 \
    libsasl2-2 \
    libzstd1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the compiled binary from the builder stage
COPY --from=builder /app/target/release/ingestion /usr/local/bin/ingestion

# Run as a non-root user for security hardening
RUN useradd -r -s /bin/false ingestion_user
USER ingestion_user

# 8085 — TCP control port (subscription commands from desktop clients)
# 9101 — Prometheus /metrics + /health + /ready
EXPOSE 8085 9101

ENTRYPOINT ["/usr/local/bin/ingestion"]
