# agents/technical/Dockerfile — Multi-stage Rust build for the Technical Indicator Agent
#
# Build context MUST be the monorepo root so that shared_protos/ is reachable.
#   docker-compose.yml sets:
#     build:
#       context: .
#       dockerfile: agents/technical/Dockerfile
#
# ARM note: this image builds natively on aarch64 (Oracle Ampere A1). rdkafka is
# compiled from source via CMake, so the first build is slow (~several minutes)
# but fully supported on ARM. No cross-compilation required — build on the host.
#
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder
# ──────────────────────────────────────────────────────────────────────────────
FROM rust:1-slim-bookworm AS builder

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
COPY agents/technical/Cargo.toml agents/technical/Cargo.lock* agents/technical/build.rs ./
COPY shared_protos/ ./shared_protos/

# Path dependency: Cargo.toml declares service-metrics = { path = "../../service-metrics" },
# which resolves to /service-metrics from this crate's /app workdir. Copied before
# the pre-cache build so the dependency graph resolves in that layer too.
COPY service-metrics/ /service-metrics/

# Fix proto paths for Docker context (build.rs uses ../../shared_protos)
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs \
    && sed -i 's|../../shared_protos|./shared_protos|g' build.rs

RUN cargo build --release --features kafka 2>&1 || true
RUN rm -rf src target/release/technical target/release/deps/technical*

# ── Real source build ─────────────────────────────────────────────────────────
COPY agents/technical/src ./src

RUN cargo build --release --features kafka 2>&1

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    libcurl4 \
    libsasl2-2 \
    libzstd1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/technical /usr/local/bin/technical

RUN useradd -r -s /bin/false app_user
USER app_user

# Prometheus surface: /metrics, /health, /ready. Override with METRICS_PORT.
EXPOSE 9104

ENTRYPOINT ["/usr/local/bin/technical"]
