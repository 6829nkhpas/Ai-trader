# alpha-terminal/Dockerfile — Multi-stage Rust build for the Alpha Terminal (OHLC Engine)
#
# Build context MUST be the monorepo root so that ../shared_protos/ is reachable.
#   docker-compose.yml sets:
#     build:
#       context: .
#       dockerfile: alpha-terminal/Dockerfile
#
# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: Builder
# ──────────────────────────────────────────────────────────────────────────────
FROM rust:1.80-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    pkg-config \
    libssl-dev \
    clang \
    libclang-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency pre-cache layer ────────────────────────────────────────────────
COPY alpha-terminal/Cargo.toml alpha-terminal/build.rs ./
COPY shared_protos/ ./shared_protos/

# Fix proto paths for Docker context (build.rs uses ../../shared_protos)
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs \
    && sed -i 's|../../shared_protos|./shared_protos|g' build.rs

RUN cargo build --release --features kafka 2>&1 || true
RUN rm -rf src target/release/alpha_terminal target/release/deps/alpha_terminal*

# ── Real source build ─────────────────────────────────────────────────────────
COPY alpha-terminal/src ./src

RUN cargo build --release --features kafka 2>&1

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Runtime
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/alpha_terminal /usr/local/bin/alpha_terminal

RUN useradd -r -s /bin/false app_user
USER app_user

EXPOSE 8081

ENTRYPOINT ["/usr/local/bin/alpha_terminal"]
