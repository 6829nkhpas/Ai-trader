# System Architecture & Technical Stack

## Monorepo Directory Tree

- `/ingestion` - Market data ingestion services
  - **Data Source:** Zerodha Kite Connect WebSocket API (`wss://ws.kite.trade`). All live tick data is sourced from Kite's proprietary binary protocol in Full mode (184-byte packets), replacing all synthetic load testers for production data flow.
  - **Binary Parser:** Decodes Kite's big-endian binary tick frames into `ParsedTick` structs with LTP, OHLC, volume, bid/ask depth, and exchange timestamps.
  - **Dual Sink Architecture:** Each parsed tick is concurrently published to Kafka (`market.ticks` topic as Protobuf) and QuestDB (PG wire + ILP TCP) for real-time processing and historical archival.
  - **Configuration:** Instrument tokens are read from `KITE_INSTRUMENT_TOKENS` environment variable as `"token:SYMBOL,..."` pairs (e.g., `"738561:RELIANCE,260105:BANKNIFTY,256265:NIFTY 50"`). No hardcoded symbols.
  - **Authentication:** Supports both pre-set `KITE_ACCESS_TOKEN` and automatic OAuth exchange via `KITE_REQUEST_TOKEN` + `KITE_API_SECRET`.
- `/agents/technical` - Quantitative technical analysis agent
- `/agents/sentiment` - NLP/LLM-based news sentiment agent
- `/agents/predictive` - Consumes `market.ohlc.10m`, runs predictive math/ML, and outputs future price targets to `signals.predictive`.
  - **Math Engine:** Uses a 14-period rolling window of 10-minute closing prices.
  - **Algorithm:** Standard Least-Squares Linear Regression to project the $n+1$ candle (the next 10-minute close).
  - **Confidence Score:** Calculated using the $R^2$ (Coefficient of Determination) mapped to a 1-100 scale.
  - **WebSocket:** Port 8082 — broadcasts PredictiveSignal JSON for frontend Ghost Line rendering.
- `/agents/quant-rag` - Serverless AI insights agent (Rust)
  - **LLM Backend:** DeepSeek v4 Pro via NVIDIA NIM REST API (`https://integrate.api.nvidia.com/v1/chat/completions`). OpenAI-compatible `chat/completions` endpoint with `NVIDIA_API_KEY` authentication.
  - **Anomaly Trigger:** Monitors `market.ohlc.10m` for absolute price swings >= 2.0%. Computes `|close − open| / open × 100` per candle.
  - **Pipeline:** Anomaly detected → DeepSeek v4 Pro LLM generates headline, analysis_text, and sentiment_score (1–100) → publishes `MarketInsight` Protobuf to Kafka `signals.insights` → broadcasts JSON via WebSocket port 8083.
  - **WebSocket:** Port 8083 — broadcasts MarketInsight JSON for Swing/Investor HUD rendering.
  - **JSON Mode:** System prompt enforces structured JSON output with keys: `headline`, `analysis_text`, `sentiment_score`. Code-fence stripping handles edge cases.
  - **Error Visibility:** LLM API failures are caught and broadcast to the frontend as system-level anomalies to prevent silent UI failures. When the DeepSeek API returns an error, a fallback `MarketInsight` with `headline: "LLM API Failure"` and the error details in `analysis_text` is published to Kafka and broadcast over WebSocket, ensuring the frontend always receives actionable data.
- `/aggregator` - Core decision fusion engine
- `/alpha-terminal` - V2 Predictive Engine (Rust, WebSocket port 8081)
  - Consumes raw ticks from `market.ticks` via Kafka, aggregates into 10-minute OHLC candles using a tumbling window engine, and publishes completed candles to `market.ohlc.10m`.
  - Broadcasts candle JSON over WebSocket port 8081 for frontend chart rendering.
- `/frontend` - Glass-Box trading UI
  - Features the V2 Alpha Predictive Chart, which ingests `OhlcCandle` data directly from the V2 WebSocket on port 8081, operating completely parallel to the V1 Aggregator feed on port 8080.
  - Renders AI forward-projections as dashed Ghost Lines using `PredictiveSignal` data from the Predictive WebSocket on port 8082.
  - **Live Data Only:** All synthetic mock data generators (setInterval random price walks, hardcoded order books) have been purged. UI components only update state when real IPC/WebSocket data arrives from the backend.
  - **Institutional Charting Canvas** (`AlphaPredictiveChart.tsx`):
    - **Dark-Mode Canvas:** Deep slate-900 (`#0F172A`) background with `#CBD5E1` axis text and subtle `rgba(51,65,85,0.4)` grid lines. Crosshair uses dashed slate lines with dark label backgrounds.
    - **Volume Histogram:** Pinned to the bottom 20% of the chart via `priceScaleId: ''` + `scaleMargins: { top: 0.8, bottom: 0 }`. Volume bars are conditionally colored — green (`#22c55e35`) for bullish candles, red (`#ef444430`) for bearish candles.
    - **EMA 9/21 Momentum Ribbons:** Two line series overlaid on the candlestick chart — EMA 9 (cyan `#38bdf8`, lineWidth 2) and EMA 21 (pink `#f472b6`, lineWidth 2). EMAs are calculated client-side using an Exponential Moving Average engine with SMA-seeded initialization. Values update dynamically as new candles arrive via WebSocket. Current EMA values are displayed as color-coded badges in the chart header.
    - **Zero-Latency Rendering:** Charts bypass React State. Lightweight-charts `.setData()` and `.update()` are called directly from the data sync effect, preventing DOM reconciliation bottlenecks.
  - **Modular Workspace Architecture** (`react-resizable-panels` v4.11):
    - All three profile layouts (Intraday, Swing, Investor) use `Group` / `Panel` / `Separator` from `react-resizable-panels` for drag-to-resize split panes.
    - Primary chart panel (75% default) and sidebar panel (25% default, 15% minimum) with a styled vertical grabber handle (`bg-slate-800` → `bg-slate-600` on hover → `bg-emerald-500/60` on active drag).
    - Sidebar is fully collapsible via the `PanelImperativeHandle` API — `panel.collapse()` / `panel.expand()` — with a toggle button (PanelRightClose/PanelRightOpen icons). Collapse state is tracked via `onResize` callback.
  - **System Status Console** (`SystemConsole.tsx`):
    - A bottom drawer diagnostic panel (collapsed: 32px status bar, expanded: 192px log viewer).
    - **Status Bar:** Displays real-time connection status for Kafka, Zerodha, and DeepSeek services with color-coded status dots (🟢 connected / 🟡 connecting / 🔴 disconnected). Also shows pipeline latency in ms.
    - **Log Viewer:** Terminal-like monospace text area displaying a rolling log of system events (INFO/WARN/ERROR) with timestamps. Auto-scrolls to bottom. Reads from `systemLogs[]` in the Zustand store, which is populated by all WebSocket connection handlers.
- `/shared_protos` - Universal Protobuf data contracts
- `/tools/load_tester` - Chaos Engine: high-frequency Kafka load tester with anomaly injection for end-to-end stress testing

## Tech Stack

- **Ingestion & Math/Aggregator**: Rust (tokio, rdkafka) - Low latency and high performance
- **Sentiment Agent**: Node.js - Seamless interaction with HuggingFace/NewsData APIs
- **Frontend**: Next.js - Real-time WebSocket streaming and responsive UI

## Data Flow: Zerodha → Frontend

```
Zerodha Kite WS (binary) → /ingestion (parser) → Kafka (market.ticks) → /alpha-terminal (OHLC engine)
                                                                        → /aggregator (decision fusion)
                                                                        
/alpha-terminal → Kafka (market.ohlc.10m) → /agents/predictive (LinReg → WS 8082)
                                          → /agents/quant-rag (anomaly → DeepSeek → WS 8083)
                → WS 8081 → Frontend Chart
```

## Kafka Topic Routing

- `market.ticks` ← **Kite Ingestion** (live Zerodha data)
- `market.ticks` → **Alpha Terminal** → `market.ohlc.10m` (tumbling window)
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

Features a Level-2 Order Book DOM (`OrderBook.tsx`) alongside the primary WebGL charts in a dedicated 12-column grid layout (`IntradayLayout.tsx`), designed for high-frequency scalping. The order book awaits live market depth data from the backend IPC `orderbook-update` events. The grid allocates 9 columns to the predictive chart and 3 columns to the order book sidebar.

### Swing Mode

Features a `SwingConfluencePanel` alongside the predictive chart in a 12-column grid layout (`SwingLayout.tsx`). The confluence panel provides:
  - **Multi-Timeframe Trend:** Displays trend bias (Bullish/Neutral/Bearish) with strength bars across 1H, 4H, 1D, and 1W timeframes.
  - **AI News Sentiment:** Scrollable feed of recent market news articles with per-item sentiment indicators (positive/negative/neutral dots) and an aggregate sentiment score (0–100) with a Fear/Greed gauge bar. Powered by DeepSeek AI via the Quant-RAG agent.

### Investor Mode

Features a `MacroSentimentPanel` alongside the predictive chart in a 12-column grid layout (`InvestorLayout.tsx`). The macro panel provides:
  - **Macro Indicators:** Real-time display of key economic metrics (Fed Funds Rate, Core CPI, 10Y Treasury, DXY, VIX, GDP) with directional change indicators.
  - **Portfolio Risk Metrics:** Key quantitative portfolio measures (Sharpe Ratio, Max Drawdown, Beta, Alpha).
  - **Quant-RAG Outlook:** AI-generated long-term sectoral analysis and allocation recommendations with probability-weighted scenario forecasting. Powered by DeepSeek AI.

## Phase 9: Serverless AI (Quant-RAG)

### Phase 9.1 — DeepSeek v4 Pro API Client (via NVIDIA NIM)
DeepSeek v4 Pro REST API client (`llm.rs`) using NVIDIA NIM's OpenAI-compatible `chat/completions` endpoint. System prompt enforces structured JSON output. Replaces Google Gemini for the quant-rag agent.

### Phase 9.2 — Anomaly Detection Engine
Kafka consumer loop monitoring `market.ohlc.10m` for ≥2% absolute price swings. On anomaly detection, invokes DeepSeek v4 Pro for AI-generated headline, analysis_text, and sentiment_score. Publishes `MarketInsight` protobuf to `signals.insights` and broadcasts JSON via WebSocket port 8083.

### Phase 9.3 — End-to-End Stress Test (The Crucible)
The `/tools/load_tester` Chaos Engine validates all pipelines under extreme institutional load.

- **Firehose:** Publishes 100 synthetic `OHLCCandle` messages/sec to `market.ohlc.10m` using a geometric random walk price engine.
- **Anomaly Injection:** Every 500th candle, injects a massive ±5% flash crash to force the DeepSeek LLM to trigger while charts are rendering at maximum throughput.
- **Pipeline Coverage:** Simultaneously exercises the Predictive Agent (LinReg math), Quant-RAG Agent (DeepSeek LLM), Alpha Terminal (OHLC WS), and Tauri Edge Terminal (60 FPS Canvas rendering).
- **CLI:** `cargo run -- --rate 100 --anomaly-every 500 --symbol RELIANCE --anomaly-pct 5.0`

## Perfection Phase 1: Error Visibility

**Error Visibility:** LLM API failures are caught and broadcast to the frontend as system-level anomalies to prevent silent UI failures. When the DeepSeek v4 Pro API returns an error (network timeout, invalid API key, rate limit, malformed response), the engine constructs a fallback `MarketInsight` with:
- `headline`: `"LLM API Failure"`
- `analysis_text`: `"DeepSeek Error: <detailed error message>"`
- `sentiment_score`: `50` (neutral)

## Perfection Phase 2: Live Data Hardening

**All synthetic mock data purged.** The system now exclusively processes live data from the Zerodha Kite WebSocket API:
- Order Book DOM (`OrderBook.tsx`) — removed 100ms `setInterval` mock engine. Now awaits real market depth via IPC.
- Stock feed (`LiveFeedPanel.tsx`) — already data-driven from Zustand store, no mocks.
- Chart components — already driven by real WebSocket data from backend.
- Load tester — all hardcoded `BTC/USD` references replaced with configurable NSE symbols (default: `RELIANCE`).

## Perfection Phase 3: Cold-Storage Ingestion (Historical Pipeline)

### QuestDB 5-Year Partitioning Strategy

The `historical_candles` table stores daily OHLCV data fetched from the Zerodha Kite Historical API:

```sql
CREATE TABLE IF NOT EXISTS historical_candles (
    symbol    SYMBOL,
    ts        TIMESTAMP,
    open      DOUBLE,
    high      DOUBLE,
    low       DOUBLE,
    close     DOUBLE,
    volume    LONG
) timestamp(ts) PARTITION BY YEAR;
```

- **Partition Scheme:** `PARTITION BY YEAR` — each calendar year is stored in its own partition directory. 5 years of data = 5 partitions, enabling efficient range scans and lifecycle management.
- **Designated Timestamp:** `ts` is the ordered timestamp — QuestDB uses it for WAL routing, partition selection, and time-series ordering.

### History Loader Service (`frontend/src-tauri/src/services/history_loader.rs`)

- **Chunking:** Fetches daily candles in 365-day (1-year) windows, looping 5 times for full 5-year backfill.
- **API Endpoint:** `GET https://api.kite.trade/instruments/historical/{token}/day?from=YYYY-MM-DD&to=YYYY-MM-DD`
- **Rate Limiting:** 350ms delay between chunk requests (Kite limit: 3 req/sec).
- **Deduplication:** Before each chunk fetch, queries QuestDB for existing `min(ts)`/`max(ts)` range — chunks already covered are skipped entirely.
- **Insertion:** Parameterised INSERT via QuestDB PG wire protocol (port 8812).

### Binary IPC Transfer (Zero-Latency)

The `get_historical_view` Tauri command (`frontend/src-tauri/src/commands/charts.rs`):

1. Queries QuestDB for all daily candles matching a symbol.
2. Serializes the result as `bincode` (compact binary format) instead of JSON.
3. Returns `Vec<u8>` → Tauri auto-converts to `Uint8Array` on the frontend.
4. On error, emits a `system-error` event for frontend console visibility.

```
Frontend invoke("get_historical_view", { symbol: "RELIANCE" })
    → Tauri Command → QuestDB PG Query → bincode::serialize → Uint8Array
```

## Perfection Phase 4: Timeframe Routing Engine

The UI now includes a global **Timeframe Engine** that governs which OHLC data stream is rendered on the chart canvas and which AI overlays are visible.

### Zustand State (`ChartTimeframe`)

A new `ChartTimeframe` type (`'1m' | '5m' | '10m' | '15m' | '1H' | '1D'`) is stored in the Zustand `useTradeStore` as `activeTimeframe`, defaulting to `'10m'`. The `setActiveTimeframe` action updates this globally. All chart components and layout renderers read from this single source of truth.

### Timeframe Selector UI

A compact segmented control bar is rendered in the chart command bar (alongside the Profile Switcher). Buttons for all six timeframes are displayed with the active selection highlighted in `emerald-400` on a `slate-800` background. Selection triggers `setActiveTimeframe()` which propagates instantly to all subscribers.

### OHLC Data Routing

The backend currently emits **exclusively 10-minute candles** via the Alpha Terminal WebSocket on port 8081 (`market.ohlc.10m`). The chart's data merge pipeline implements a routing gate:

- **`activeTimeframe === '10m'`**: Live WebSocket candle stream is appended to the chart. Full real-time rendering.
- **All other timeframes**: Live stream is excluded. Only historical data (from QuestDB) is displayed. Backend generation for additional timeframe candles (1m, 5m, 15m, 1H, 1D) is planned for future phases.

### Predictive ML Overlay Constraint (Ghost Line)

> **CRITICAL:** The Predictive Ghost Line is **rigidly bound to the 10m view** to ensure mathematical integrity.

The Predictive Agent (`/agents/predictive`) uses a 14-period rolling window of **10-minute closing prices** for Least-Squares Linear Regression to project the next candle's close. This math is only valid on the 10m timeframe. Therefore:

- **`activeTimeframe === '10m'`**: Ghost Line renders normally (dashed sky-blue projection from last close to predicted close).
- **Any other timeframe**: Ghost Line data is cleared (`setData([])`) and the line is hidden entirely. No predictive overlay is rendered on non-10m charts.

This constraint prevents misleading projections from being displayed to the user when viewing timeframes that the ML model was not trained on.

