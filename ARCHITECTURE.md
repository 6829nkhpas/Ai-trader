# System Architecture & Technical Stack

## Monorepo Directory Tree

- `/ingestion` - Market data ingestion services
- `/agents/technical` - Quantitative technical analysis agent
- `/agents/sentiment` - NLP/LLM-based news sentiment agent
- `/agents/predictive` - Consumes `market.ohlc.10m`, runs predictive math/ML, and outputs future price targets to `signals.predictive`.
  - **Math Engine:** Uses a 14-period rolling window of 10-minute closing prices.
  - **Algorithm:** Standard Least-Squares Linear Regression to project the $n+1$ candle (the next 10-minute close).
  - **Confidence Score:** Calculated using the $R^2$ (Coefficient of Determination) mapped to a 1-100 scale.
  - **WebSocket:** Port 8082 — broadcasts PredictiveSignal JSON for frontend Ghost Line rendering.
- `/aggregator` - Core decision fusion engine
- `/alpha-terminal` - V2 Predictive Engine (Rust, WebSocket port 8081)
- `/frontend` - Glass-Box trading UI
  - Features the V2 Alpha Predictive Chart, which ingests `OhlcCandle` data directly from the V2 WebSocket on port 8081, operating completely parallel to the V1 Aggregator feed on port 8080.
  - Renders AI forward-projections as dashed Ghost Lines using `PredictiveSignal` data from the Predictive WebSocket on port 8082.
- `/shared_protos` - Universal Protobuf data contracts

## Tech Stack

- **Ingestion & Math/Aggregator**: Rust (tokio, rdkafka) - Low latency and high performance
- **Sentiment Agent**: Node.js - Seamless interaction with Anthropic/Marketaux APIs
- **Frontend**: Next.js - Real-time WebSocket streaming and responsive UI

## Kafka Topic Routing

- `live_ticks` → **Technical Agent** → `technical_signals`
- `live_ticks` / `news_feed` → **Sentiment Agent** → `sentiment_signals`
- `market.ohlc.10m` → **Predictive Agent** → `signals.predictive`
- `technical_signals` + `sentiment_signals` → **Aggregator Engine** → `aggregated_decisions`
- `aggregated_decisions` → **Frontend (via WebSocket)** / **Execution Layer**

## V2 Objective

**Transitioning from Reactive (V1) to Predictive (V2 Alpha Suite).**

## Phase 7: The Edge Terminal

The `/frontend` is now a hybrid architecture. It can run as a standard Next.js web app, OR as a native desktop executable wrapped by Tauri (`/frontend/src-tauri`).

### Zero-Latency Rendering Pipeline

Charts bypass React State. WebSockets push data directly via the lightweight-charts .update() API to prevent DOM reconciliation bottlenecks.

### IPC Data Bridge

Frontend no longer makes network requests. Tauri Rust core handles WebSockets/Kafka and streams data to the UI entirely via native IPC emit_all events for zero-latency rendering.

## Phase 8: Universal Market Profiles

The UI layout and data subscriptions are governed by a global `TradeProfile` state (`Intraday`, `Swing`, `Investor`), allowing hot-swapping of terminal layouts.

### State Engine

A Zustand-managed `activeProfile: TradeProfile` slice drives the entire terminal mode. Switching profiles reconfigures:

- **Intraday (Scalp):** High-frequency 1m/5m charts, Order Book DOM, volatility heatmaps.
- **Swing (1H-4H):** Medium-term candlestick analysis, momentum oscillators, trend overlays.
- **Investor (Macro):** Daily/Weekly timeframes, macro sentiment dashboards, portfolio allocation views.

### Profile Switcher UI

A segmented control bar (`ProfileSwitcher.tsx`) is permanently mounted at the top of the terminal, acting as the master mode selector. Active profile is indicated with a neon-purple highlight and animated glow dot. Each chart section displays a color-coded mode badge reflecting the current profile.
