# Alpha Suite V2 — Production Setup Guide

> **Version:** 2.0.0  
> **Last Updated:** May 2026  
> **Architecture:** Rust microservices + Kafka + Next.js/Tauri desktop client

---

## Prerequisites

| Tool | Minimum Version | Purpose |
|---|---|---|
| **Docker Engine** | 24.0+ | Container orchestration |
| **Docker Compose** | v2.20+ | Multi-service management |
| **Node.js** | 20 LTS+ | Frontend build |
| **Rust** | 1.80+ | Tauri native shell (local dev only) |
| **CMake** | 3.20+ | rdkafka native build (local dev only) |

---

## Step 1: Configure Environment Variables

Copy the `.env.example` or create a `.env` file in the monorepo root:

```bash
cp .env.example .env
```

Edit `.env` and fill in the required keys:

```env
# ── Zerodha Kite Connect ────────────────────────────────────────────────
# Required for live market data ingestion from Zerodha.
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret
KITE_ACCESS_TOKEN=your_daily_access_token
KITE_INSTRUMENT_TOKENS=256265:RELIANCE,340481:HDFCBANK,408065:INFY

# ── NVIDIA NIM (DeepSeek v4 Pro) ────────────────────────────────────────
# Required for AI-powered market insights and anomaly detection.
NVIDIA_API_KEY=nvapi-your_nvidia_api_key_here

# ── Kafka Configuration ─────────────────────────────────────────────────
# When running via Docker Compose, use the internal broker address.
# For local development, use localhost:19092.
KAFKA_BROKERS=redpanda:29092

# ── QuestDB (Time-Series Archive) ───────────────────────────────────────
QUESTDB_URL=postgresql://questdb:8812/qdb

# ── PostgreSQL (Auth DB) ────────────────────────────────────────────────
POSTGRES_AUTH_PASSWORD=your_secure_password_here

# ── Logging ─────────────────────────────────────────────────────────────
RUST_LOG=info
```

> **⚠️ Important:** The `KITE_ACCESS_TOKEN` expires daily. You must regenerate it each trading session via the Kite Connect login flow or your OAuth service.

---

## Step 2: Start the Backend Brain (Docker)

From the monorepo root, build and start all backend microservices:

```bash
docker-compose up -d --build
```

This launches the following services:

| Service | Container | Port | Description |
|---|---|---|---|
| **Redpanda** | `alphasuite-redpanda` | `19092` | Kafka-compatible message broker |
| **QuestDB** | `alphasuite-questdb` | `9000`, `8812` | Time-series archive + Web console |
| **PostgreSQL** | `alphasuite-postgres` | `5432` | Authentication database |
| **Redis** | `alphasuite-redis` | `6379` | Session cache |
| **Ingestion** | `alphasuite-ingestion` | — | Kite WS → Protobuf → Kafka + QuestDB |
| **Alpha Terminal** | `alphasuite-alpha-terminal` | `8081` | OHLC engine → WebSocket |
| **Aggregator** | `alphasuite-aggregator` | `8080` | Decision fusion → WebSocket |
| **Predictive Agent** | `alphasuite-predictive` | `8082` | Ghost line projections → WebSocket |
| **Quant-RAG Agent** | `alphasuite-quant-rag` | `8083` | DeepSeek AI insights → WebSocket |

### Verify All Services Are Healthy

```bash
docker-compose ps
```

All services should show `Up` or `Up (healthy)`. Check logs for any service:

```bash
docker-compose logs -f ingestion
docker-compose logs -f quant-rag-agent
```

### Verify Kafka Topics

```bash
docker exec -it alphasuite-redpanda rpk topic list
```

Expected topics (auto-created on first message):
- `market.ticks` — Raw tick data from Kite
- `market.ohlc.10m` — 10-minute OHLC candles
- `signals.technical` — Technical indicator signals
- `signals.sentiment` — News sentiment signals

---

## Step 3: Build the Desktop Client (Tauri)

### Development Mode

```bash
cd frontend
npm install
npm run tauri dev
```

This starts the Next.js dev server on `http://localhost:3000` and opens the native Tauri window.

### Production Build

```bash
cd frontend
npm install
npm run tauri build
```

The build artifacts are output to:

| Platform | Output Path | Format |
|---|---|---|
| **Windows** | `frontend/src-tauri/target/release/bundle/msi/` | `.msi` installer |
| **macOS** | `frontend/src-tauri/target/release/bundle/macos/` | `.app` bundle |
| **Linux** | `frontend/src-tauri/target/release/bundle/deb/` | `.deb` package |

---

## Step 4: Verify End-to-End Data Flow

Once both the backend (Docker) and desktop client (Tauri) are running:

1. **Check the System Console** — Click the bottom status bar in the terminal. You should see:
   - 🟢 `Kafka: Connected`
   - 🟢 `Zerodha: Connected` (during market hours)
   - 🟢/🔵 `DeepSeek: Connected` or `Standby`

2. **Verify Chart Data** — Switch to the Intraday profile. The AlphaPredictiveChart should begin rendering live candles with:
   - Volume histogram (bottom 20%)
   - EMA 9 (cyan) and EMA 21 (pink) ribbons
   - Ghost line projections (when predictive agent emits)

3. **Verify AI Insights** — Switch to Swing or Investor profile. When a ≥2% price anomaly is detected, the Quant-RAG agent generates a DeepSeek-powered market insight.

---

## Troubleshooting

### Kafka Connection Refused
```
ERROR: Alpha OHLC WS connection error → ws://127.0.0.1:8081
```
**Cause:** Backend services haven't started yet.  
**Fix:** Wait for `docker-compose ps` to show all services as `Up`. Services auto-reconnect every 3 seconds.

### KITE_ACCESS_TOKEN Expired
```
ERROR: Kite WS auth failed — 403 Forbidden
```
**Fix:** Regenerate the access token via the Kite Connect login flow and update `.env`.

### DeepSeek API Timeout
```
ERROR: DeepSeek API failure: NVIDIA NIM request timeout
```
**Fix:** Check your `NVIDIA_API_KEY` is valid. Verify network connectivity to `https://integrate.api.nvidia.com`.

### CMake Not Found (Local Dev Only)
```
error: failed to run custom build command for `rdkafka-sys`
```
**Fix:** Install CMake. On Windows: `winget install Kitware.CMake`. On macOS: `brew install cmake`. On Linux: `apt install cmake`.

---

## Architecture Reference

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design, data flow diagrams, and component documentation.
