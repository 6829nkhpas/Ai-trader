# Dynamic Sprint Board

**Phase:** Perfection Phase 6 — Historical Data Ingestion

**System Health:** V1 Core is fully operational (Ingestion, Tech, Sentiment, Aggregator, UI).

**Current Objective:** Perfection Phase 6 — Historical Pipeline Integration.

**Current Status:** Historical Pipeline Integrated. QuestDB 5-Year Partitioning Active.

**Key Changes (Phase 6):**
- `backend/db/migrations/002_historical.sql` — QuestDB DDL: `historical_candles` table with `PARTITION BY YEAR`.
- `frontend/src-tauri/src/services/history_loader.rs` — Zerodha Kite Historical API client with 365-day chunking, rate limiting, deduplication, and bulk insert.
- `frontend/src-tauri/src/commands/charts.rs` — `get_historical_view` Tauri command: QuestDB → bincode → Uint8Array binary transfer.
- `frontend/src-tauri/src/lib.rs` — QuestDB managed state, migration runner, command registration.
- `frontend/src-tauri/Cargo.toml` — Added sqlx, bincode, chrono, reqwest, dotenvy dependencies.

**Historical Pipeline Summary:**
1. On Tauri startup: QuestDB pool initialized → `historical_candles` migration executed.
2. `history_loader::load_historical_data()` — fetches 5 years of daily candles from Kite API in 1-year chunks.
3. `get_historical_view` command — queries QuestDB, returns bincode-serialized binary buffer to frontend.

**Deprecated:**
Explicitly note that `MASTER_CONTEXT.md` and `SESSION_MEMORY.md` are now obsolete and should be ignored entirely by the system.
Google Gemini 1.5 Flash has been fully deprecated and replaced by DeepSeek v4 Pro (via NVIDIA NIM). The `GEMINI_API_KEY` environment variable is no longer used.
