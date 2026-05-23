# 🌌 Strat: Institutional Quantitative Trading Terminal & AI platform

Strat is an institutional-grade, high-frequency AI-powered trading platform executing advanced quantitative strategies on the NSE (National Stock Exchange of India). Combining high-speed Rust-based ingestion engines, mathematical consensus, predictive price curves, real-time sentiment analysis, and a unified reasoning layer powered by the **"Seasoned Historian" V3 prompt core**, Strat delivers high-probability directional conviction with zero compromise on capital preservation.

---

## 🏗️ System Architecture & Data Flow

Strat is built on a distributed, low-latency asynchronous architecture utilizing Rust, Fastify, Kafka, Redis, and QuestDB.

```mermaid
graph TD
    %% Ingestion & Live Streams
    subgraph StreamLayer ["Data Ingestion & Microservices"]
        Kite[Zerodha Kite WS API] -->|Binary Ticks| Ingest[ingestion service / Rust]
        Ingest -->|Raw Ticks Stream| Kafka[Apache Kafka]
    end

    %% Storage & Database
    subgraph DBStore ["High-Velocity DB & Cache"]
        Kafka -->|Tick Ingestion| QDB[(QuestDB: live_ticks)]
        RedisCache[(Redis Cache)]
    end

    %% Analytical Agents
    subgraph Analytics ["Quantitative calculations & Agents"]
        AgentTech[Technical Agent / Rust] -->|Protobuf| Kafka
        AgentPred[Predictive Agent / Rust] -->|OLS / Curve fit| Kafka
        NewsAPI[Google News RSS / Local API] --> SentimentService[Sentiment Agent / Node.js]
        SentimentService -->|Claude API| RedisCache
    end

    %% Desktop Interface & IPC Bridge
    subgraph DesktopClient ["Desktop Terminal / Tauri + Next.js"]
        UI[React/Next.js Dashboards] <-->|Zustand Telemetry| Zustand[useTradeStore / useQuantStore]
        TauriCore[Tauri Rust Core] <-->|Zero-Latency IPC bincode| UI
        TauriCore -->|Pg PG Pool| QDB
    end

    %% Unified Reasoning Core
    subgraph Reasoning ["Deep Quant Reasoning Engine"]
        TauriCore -->|1. Data Fusion: Daily ∪ Intraday ∪ Live| Fusion[Deduplicated Candle Array]
        Fusion -->|2. Microstructures| IndicatorState[technical compilation]
        IndicatorState -->|3. Curvature| VWEPR[VWEPR Curved Acceleration]
        IndicatorState -->|4. Rolling Scans| Patterns[Candlestick rolling scan]
        SentimentService -->|5. Sentiment Context| SentimentContext[RAG sentiment Context]
        
        ReasoningCore[llm.rs: Seasoned Historian V3]
        
        VWEPR & Patterns & SentimentContext & IndicatorState -->|Verify 18 RAG Variables| ReasoningCore
        ReasoningCore -->|DeepSeek / Hugging Face Router| ExecutionPlan[Structured JSON: AiExecutionPlan]
        ExecutionPlan -->|Emit Events| UI
    end

    style StreamLayer fill:#1e1e2e,stroke:#313244,stroke-width:2px;
    style DBStore fill:#181825,stroke:#313244,stroke-width:2px;
    style Analytics fill:#1e1e2e,stroke:#313244,stroke-width:2px;
    style DesktopClient fill:#181825,stroke:#313244,stroke-width:2px;
    style Reasoning fill:#11111b,stroke:#a6e3a1,stroke-width:2px;
```

---

## 🧠 Deep Quant Analytical Foundation

Strat's reasoning core is driven by a sophisticated multi-variable RAG (Retrieval-Augmented Generation) pipeline that feeds into the **Seasoned Historian V3 System Prompt**. Instead of leaving numeric parameters to LLM hallucination, they are mathematically calculated in the native Rust engine and injected verbatim.

### 1. VWEPR (Volume-Weighted Exponential Price Regression) Curvature
The terminal utilizes the **VWEPR** regression system to predict support/resistance and trend exhaustion using polynomial fitting. By mapping a sliding window of historical bars, we fit a quadratic curve:

$$y = a x^2 + b x + c$$

Where:
*   **$a$ (Acceleration Coefficient)** represents trend curvature:
    *   **$a > 0$ (Positive Curvature)**: Parabolic velocity, indicating accelerating bullish momentum.
    *   **$a < 0$ (Negative Curvature)**: Exhaustion curve / Rounding Top, signaling high-probability momentum stalling.
*   The fitting matrix is solved using low-level Cramer's rule determinant equations:

$$\det(A) = N(S_{x^2}S_{x^4} - S_{x^3}^2) - S_x(S_x S_{x^4} - S_{x^2}S_{x^3}) + S_{x^2}(S_x S_{x^3} - S_{x^2}^2)$$

```rust
// In quant/vwepr.rs — Fit solver using determinants
let det_a = n * (s_x2 * s_x4 - s_x3 * s_x3) - s_x * (s_x * s_x4 - s_x2 * s_x3) + s_x2 * (s_x * s_x3 - s_x2 * s_x2);
if det_a.abs() > 1e-9 {
    let a = (n_y * (s_x2 * s_x4 - s_x3 * s_x3) - s_x * (s_xy * s_x4 - s_x2 * s_x2y) + s_x2 * (s_xy * s_x3 - s_x2y * s_x2)) / det_a;
    // a represents VWEPR quadratic curvature
}
```

### 2. Multi-Source Pipeline Fusion & Deduplication
To maintain real-time accuracy while providing deep historical context, the pipeline implements a **V3 fusion strategy** that merges daily historical archives, chart cached intraday bars, and sample ticks:

$$\text{Candles} = \text{Daily Archive} \cup \text{Intraday Cache} \cup \text{Live Tick Aggregates}$$

1.  **Extraction**: Pull from daily tables (`historical_candles`), intraday charts (`historical_intraday`), and live samples (`live_ticks`).
2.  **Deduplication**: Sort ascending by timestamp, and resolve collisions by source priority ($\text{Live} > \text{Intraday} > \text{Daily}$). If two bars share a timestamp, the higher-priority source overrides, eliminating half-formed live bar discrepancies.

---

## 🏛️ The "Seasoned Historian" Reasoning Persona

The LLM is configured with a ruthless quantitative trader persona that evaluates RAG parameters against historical memories. 

### Injected Prompt Variable Map (Exactly 18 Parameters)
The system prompt resolves and format-injects the exact quantitative metrics of the market, structured as follows:

| # | Injected Parameter | Rust Type | Description |
|---|---|---|---|
| 1 | `symbol` | `&str` | Trading ticker name (e.g. `RELIANCE`, `NIFTY 50`) |
| 2 | `timeframe` | `&str` | Scanning interval (e.g. `10m`, `1d`) |
| 3 | `macro_context` | `&str` | Broader index direction fetched via QuestDB (e.g., `NIFTY 50 is trending up +0.8% today`) |
| 4 | `latest_close` | `f64` | The last closing price from our deduplicated array |
| 5 | `vwap_val` | `f64` | Volume Weighted Average Price |
| 6 | `ofi_val` | `f64` | Order Flow Imbalance (-1.0 Ask pressure, +1.0 Bid pressure) |
| 7 | `vol_multiplier` | `f64` | Volume spike above the 20-period average (walked backward to find active volume) |
| 8 | `atr_val` | `f64` | Average True Range (14-period volatility baseline) |
| 9 | `bb_upper` | `f64` | Bollinger Bands Upper threshold |
| 10 | `bb_mid` | `f64` | Bollinger Bands Middle baseline |
| 11 | `bb_lower` | `f64` | Bollinger Bands Lower threshold |
| 12 | `rsi_val` | `f64` | Relative Strength Index (14-period momentum metric) |
| 13 | `macd_val` | `f64` | MACD Line value |
| 14 | `macd_signal` | `f64` | MACD Signal line |
| 15 | `ema9_val` | `f64` | 9-period Exponential Moving Average |
| 16 | `ema21_val` | `f64` | 21-period Exponential Moving Average |
| 17 | `acceleration_coeff` | `f64` | VWEPR quadratic curvature acceleration ($a$) |
| 18 | `detected_patterns` | `&str` | Comma-separated candlestick patterns found inside a rolling window |

---

## 📂 Core Component Directory Map

```text
/
├── agents/
│   ├── technical/        # Rust Technical Indicator calculations (RSI, Bollinger Bands, MACD, EMA)
│   ├── predictive/       # Rust OLS and Regression projection calculator
│   └── sentiment/        # Claude-powered real-time news evaluation engine with Redis Cache
├── aggregator/           # Decision broadcasting, Dynamic Weighting & Conflict Resolution (Rust)
├── ingestion/            # High-speed binary Zerodha Kite WS tick aggregator (Rust)
├── backend/              # KYC Database, user profile & Identity state machine
├── auth/                 # Fastify identity engine using Argon2id passwords and RS256 JWT tokens
└── frontend/
    ├── src-tauri/        # Tauri Native Bridge
    │   ├── src/
    │   │   ├── commands/ # Tauri IPC Commands (charts.rs, deep_quant.rs, ticker.rs)
    │   │   └── services/ # LLM Service (llm.rs), Instrument Master and history loaders
    │   └── tests/        # Contract API & Mock Testing suite (mockito mock server)
    └── src/
        ├── app/          # Dashboards and User Interface
        ├── store/        # Telemetry State management (useTradeStore, useQuantStore)
        └── components/   # Interactive Trading charts and quantitative panels
```

---

## 🛠️ Developer Setup & Deployment

### Environment Configurations
Create a `.env` file at the root:
```env
# ── database configuration ───────────────────────────────
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ai_trader"
QUESTDB_HTTP_URL="http://127.0.0.1:9000"
QUESTDB_PG_URL="postgresql://admin:quest@localhost:8812/qdb"

# ── Zerodha Kite credentials ─────────────────────────────
KITE_API_KEY="your_kite_api_key"
KITE_ACCESS_TOKEN="your_daily_kite_access_token"

# ── LLM Inference Provider ──────────────────────────────
LLM_API_URL="https://router.huggingface.co/v1/chat/completions"
LLM_API_KEY="hf_xxxxxxxxxxxxxxxxxxxxxxxx"
LLM_MODEL="deepseek-ai/DeepSeek-V3-0324"

# ── Redis & Kafka Brokers ────────────────────────────────
REDIS_URL="redis://127.0.0.1:6379"
KAFKA_BROKER_URL="127.0.0.1:9092"
```

### Dev Launch Pipeline
1.  **Stand up Core Infrastructure**:
    ```bash
    docker-compose up -d
    ```
    This registers PostgreSQL, Redis, Apache Kafka, and QuestDB.

2.  **Verify Backend API Contract Tests**:
    ```bash
    cd frontend/src-tauri
    cargo test --test api_tests
    ```

3.  **Boot Desktop Terminal**:
    ```bash
    cd frontend
    npm run tauri dev
    ```

---

> [!IMPORTANT]
> **Dynamic Weighting Conflict Resolution**: When technical indicators diverge significantly from Claude-evaluated sentiment scores, the aggregator applies dynamic weighting multipliers, shifting capital allocations toward **Hold/Capital Preservation** to prevent trade execution on unconfirmed setups.