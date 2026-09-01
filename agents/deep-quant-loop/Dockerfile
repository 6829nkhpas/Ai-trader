# agents/deep-quant-loop/Dockerfile — Deep Quant LangGraph FastAPI service (:8086)
#
# The glass-box AI analysis engine. The desktop thin client POSTs /run, /qa,
# /resume here; this service drives the LangGraph ReAct loop, calling the Rust
# Tool_Server (:8084) for market-data tools and QuestDB (HTTP :9000) for the
# options read layer — both reached over the internal 'stratai' Docker network.
#
# Build context = this directory (agents/deep-quant-loop). See docker-compose.prod.yml.

FROM python:3.12-slim AS runtime

# ca-certificates — TLS for the LLM provider + Google News RSS.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Durable state directory ──────────────────────────────────────────────────
# The four SQLite databases this service owns (compliance, sessions, LangGraph
# checkpoints, journal/telemetry) live HERE, not beside the module. Writing them
# into /app put them in a container layer, so every redeploy destroyed them —
# including the append-only compliance hash chain, which silently restarted from
# genesis each time.
#
# Deliberately NOT a `VOLUME /data` instruction: an anonymous volume declared in
# the image would be created fresh per container and would mask the named volume
# if compose ever failed to mount it — turning a mount misconfiguration into
# silent data loss instead of a visible one. docker-compose owns the mount
# (`deep_quant_data:/data`), and main.py's startup state report warns when a
# resolved DB path is not under DEEP_QUANT_STATE_DIRS.
RUN mkdir -p /data

# ── Dependency layer (cached unless requirements.txt changes) ────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ───────────────────────────────────────────────────────
COPY . .

# Bind on all interfaces inside the container; the compose network + Caddy
# gateway control who can actually reach it.
ENV DEEP_QUANT_HOST=0.0.0.0 \
    DEEP_QUANT_PORT=8086 \
    PYTHONUNBUFFERED=1

# 8086 — the LangGraph SSE API (/run, /resume, /qa, /options/snapshot).
# 9109 — Prometheus /metrics, /health and /ready, served from a dedicated thread
#        so a wedged asyncio loop can still be scraped and reported.
# Documentation only; docker-compose still has to publish or network these.
EXPOSE 8086 9109

# Run uvicorn directly (NOT `python main.py`, which enables --reload for dev).
# reload watches the source tree and is unsafe/wasteful in production.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8086"]
