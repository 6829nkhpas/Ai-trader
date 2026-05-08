# Dynamic Sprint Board

**Phase:** Perfection Phase 5 — Alpha Suite

**System Health:** V1 Core is fully operational (Ingestion, Tech, Sentiment, Aggregator, UI).

**Current Objective:** Perfection Phase 5 — Production Build & Deployment.

**Current Status:** Perfection Phase 5 Complete. Backend is Dockerized. Tauri is configured for production bundling. The Alpha Suite V2 monorepo is officially complete and ready for deployment.

**Key Changes (Phase 5):**
- `docker-compose.yml` — Full production stack: Redpanda (Kafka), QuestDB, PostgreSQL, Redis + 5 Rust microservices (ingestion, alpha-terminal, aggregator, predictive-agent, quant-rag-agent).
- Multi-stage Dockerfiles for all Rust services: `ingestion`, `alpha-terminal`, `aggregator`, `agents/predictive`, `agents/quant-rag`.
- `tauri.conf.json` — Identifier: `com.alphasuite.terminal`, version 2.0.0, 1440×900 window with CSP, bundle targets: all (MSI/APP/DEB).
- `PRODUCTION_SETUP.md` — Step-by-step deployment guide covering .env configuration, Docker Compose startup, Tauri build, E2E verification, and troubleshooting.

**Deployment Summary:**
1. Configure `.env` with `NVIDIA_API_KEY`, `KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `KITE_INSTRUMENT_TOKENS`.
2. `docker-compose up -d --build` — Starts the entire backend brain.
3. `npm run tauri build` (in /frontend) — Generates the desktop executable.

**Deprecated:**
Explicitly note that `MASTER_CONTEXT.md` and `SESSION_MEMORY.md` are now obsolete and should be ignored entirely by the system.
Google Gemini 1.5 Flash has been fully deprecated and replaced by DeepSeek v4 Pro (via NVIDIA NIM). The `GEMINI_API_KEY` environment variable is no longer used.
