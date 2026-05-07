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
- `/agents/quant-rag` - Serverless AI insights agent (Rust)
  - **LLM Backend:** Google Gemini 1.5 Flash REST API via `reqwest` with `application/json` strict schema generation (`responseMimeType`).
  - **Anomaly Trigger:** Monitors `market.ohlc.10m` for absolute price swings >= 2.0%. Computes `|close − open| / open × 100` per candle.
  - **Pipeline:** Anomaly detected → Gemini LLM generates headline, analysis, and sentiment score (1–100) → publishes `MarketInsight` Protobuf to Kafka `signals.insights` → broadcasts JSON via WebSocket port 8083.
  - **WebSocket:** Port 8083 — broadcasts MarketInsight JSON for Swing/Investor HUD rendering.
  - **JSON Mode:** Uses Gemini's native `generationConfig.responseMimeType: "application/json"` to enforce structured JSON output without regex post-processing.
- `/aggregator` - Core decision fusion engine
- `/alpha-terminal` - V2 Predictive Engine (Rust, WebSocket port 8081)
- `/frontend` - Glass-Box trading UI
  - Features the V2 Alpha Predictive Chart, which ingests `OhlcCandle` data directly from the V2 WebSocket on port 8081, operating completely parallel to the V1 Aggregator feed on port 8080.
  - Renders AI forward-projections as dashed Ghost Lines using `PredictiveSignal` data from the Predictive WebSocket on port 8082.
- `/shared_protos` - Universal Protobuf data contracts
- `/tools/load_tester` - Chaos Engine: high-frequency Kafka load tester with anomaly injection for end-to-end stress testing

## Tech Stack

- **Ingestion & Math/Aggregator**: Rust (tokio, rdkafka) - Low latency and high performance
- **Sentiment Agent**: Node.js - Seamless interaction with Anthropic/Marketaux APIs
- **Frontend**: Next.js - Real-time WebSocket streaming and responsive UI

## Kafka Topic Routing

- `live_ticks` → **Technical Agent** → `technical_signals`
- `live_ticks` / `news_feed` → **Sentiment Agent** → `sentiment_signals`
- `market.ohlc.10m` → **Predictive Agent** → `signals.predictive`
- `market.ohlc.10m` → **Quant-RAG Agent** (≥2% anomaly) → `signals.insights`
- `technical_signals` + `sentiment_signals` → **Aggregator Engine** → `aggregated_decisions`
- `aggregated_decisions` → **Frontend (via WebSocket)** / **Execution Layer**
- `signals.insights` → **Frontend (via WS 8083 / Tauri IPC `insight-tick`)**

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

A segmented control bar (`ProfileSwitcher.tsx`) is permanently mounted at the top of the terminal, acting as the master mode selector. Active profile is indicated with an emerald-green highlight. Each chart section displays a color-coded mode badge reflecting the current profile.

### Intraday Mode

Features a Level-2 Order Book DOM (`OrderBook.tsx`) alongside the primary WebGL charts in a dedicated 12-column grid layout (`IntradayLayout.tsx`), designed for high-frequency scalping. The Order Book runs a 100ms mock simulation engine for UI stress-testing with proper cleanup on unmount. The grid allocates 9 columns to the predictive chart and 3 columns to the order book sidebar.

### Swing Mode

Features a `SwingConfluencePanel` alongside the predictive chart in a 12-column grid layout (`SwingLayout.tsx`). The confluence panel provides:
  - **Multi-Timeframe Trend:** Displays trend bias (Bullish/Neutral/Bearish) with strength bars across 1H, 4H, 1D, and 1W timeframes.
  - **AI News Sentiment:** Scrollable feed of recent market news articles with per-item sentiment indicators (positive/negative/neutral dots) and an aggregate sentiment score (0–100) with a Fear/Greed gauge bar.

### Investor Mode

Features a `MacroSentimentPanel` alongside the predictive chart in a 12-column grid layout (`InvestorLayout.tsx`). The macro panel provides:
  - **Macro Indicators:** Real-time display of key economic metrics (Fed Funds Rate, Core CPI, 10Y Treasury, DXY, VIX, GDP) with directional change indicators.
  - **Portfolio Risk Metrics:** Key quantitative portfolio measures (Sharpe Ratio, Max Drawdown, Beta, Alpha).
  - **Quant-RAG Outlook:** AI-generated long-term sectoral analysis and allocation recommendations with probability-weighted scenario forecasting.

## Phase 9: Serverless AI (Quant-RAG)

### Phase 9.1 — Gemini API Client
Google Gemini 1.5 Flash REST API client (`llm.rs`) with `application/json` strict schema generation. Replaces all previous LLM providers for the quant-rag agent.

### Phase 9.2 — Anomaly Detection Engine
Kafka consumer loop monitoring `market.ohlc.10m` for ≥2% absolute price swings. On anomaly detection, invokes Gemini for AI-generated headline, analysis, and sentiment score. Publishes `MarketInsight` protobuf to `signals.insights` and broadcasts JSON via WebSocket port 8083.

### Phase 9.3 — End-to-End Stress Test (The Crucible)
The `/tools/load_tester` Chaos Engine validates all pipelines under extreme institutional load.

- **Firehose:** Publishes 100 synthetic `OHLCCandle` messages/sec to `market.ohlc.10m` using a geometric random walk price engine.
- **Anomaly Injection:** Every 500th candle, injects a massive ±5% flash crash to force the Gemini LLM to trigger while charts are rendering at maximum throughput.
- **Pipeline Coverage:** Simultaneously exercises the Predictive Agent (LinReg math), Quant-RAG Agent (Gemini LLM), Alpha Terminal (OHLC WS), and Tauri Edge Terminal (60 FPS Canvas rendering).
- **CLI:** `cargo run -- --rate 100 --anomaly-every 500 --symbol BTC/USD --anomaly-pct 5.0`
