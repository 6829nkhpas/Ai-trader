# 🌌 Strat: Institutional Quantitative Trading Terminal & AI Platform

Strat is an institutional-grade, high-frequency AI-powered trading platform executing advanced quantitative strategies on the NSE (National Stock Exchange of India). Combining high-speed Rust-based ingestion engines, mathematical consensus, predictive price curves, real-time sentiment analysis, and a unified reasoning layer powered by the **"Self-Defending Hunter" V3 prompt core (Alpha-Quant)**, Strat delivers high-probability directional conviction with zero compromise on capital preservation.

---

## 🏗️ System Architecture & Data Flow

Strat is built on a distributed, low-latency asynchronous architecture utilizing Rust, SQLite, Python (FastAPI, LangGraph, and PyTorch), Apache Kafka, Redis, and QuestDB.

```mermaid
graph TD
    %% Ingestion & Live Streams
    subgraph StreamLayer ["Data Ingestion & Control (Rust)"]
        Kite[Zerodha Kite WS API] -->|Binary Ticks| Ingest[ingestion service / Rust]
        IngestControl[TCP Control Server: 8085] -->|Diff Subscriptions| Ingest
    end

    %% Storage & Database
    subgraph DBStore ["High-Velocity DB & Cache"]
        Ingest -->|Raw Equity Ticks| Kafka[Apache Kafka: market.ticks]
        Ingest -->|SQL Inserts| QDB[(QuestDB: live_ticks)]
        Ingest -->|Spawned Task Option Ticks| QDB_Opt[(QuestDB: option_ticks)]
        Ingest -->|Periodic Snapshots| QDB_Snap[(QuestDB: option_chain_snapshots)]
        RedisCache[(Redis Cache)]
    end

    %% Analytical Agents
    subgraph Analytics ["Quantitative Calculations & Analytics"]
        AgentTech[Technical Agent / Rust] -->|Protobuf| Kafka
        AgentPred[Predictive Agent / Rust] -->|OLS / Curve fit| Kafka
        NewsAPI[Google News RSS / Local API] --> SentimentService[Sentiment Agent / Node.js]
        SentimentService -->|Claude API| RedisCache
    end

    %% Desktop Interface & IPC Bridge
    subgraph DesktopClient ["Desktop Terminal / Tauri + Next.js (Rust & React)"]
        UI[React/Next.js Dashboards] <-->|Zustand Telemetry| Zustand[useTradeStore / useQuantStore]
        TauriCore[Tauri Rust Core] <-->|Zero-Latency IPC bincode| UI
        TauriCore -->|SQLite read/write| SQLite[(SQLite: instruments & workspace)]
        TauriCore -->|Pg PG Pool| QDB
    end

    %% Unified Reasoning Core
    subgraph Reasoning ["LangGraph Reasoning Loop: FastAPI 8086"]
        TauriCore -->|Data Fusion / Tool API| ToolServer[Rust Tool Server: 8084]
        ToolServer -->|get_candles / get_consensus_report| QuantLoop[LangGraph Agent: Alpha-Quant V3]
        QuantLoop -->|watch_price_condition / declare_trade| ToolServer
        QuantLoop -->|SSE Streams| TauriCore
        TauriCore -->|Emit Events| UI
    end

    style StreamLayer fill:#1e1e2e,stroke:#313244,stroke-width:2px;
    style DBStore fill:#181825,stroke:#313244,stroke-width:2px;
    style Analytics fill:#1e1e2e,stroke:#313244,stroke-width:2px;
    style DesktopClient fill:#181825,stroke:#313244,stroke-width:2px;
    style Reasoning fill:#11111b,stroke:#a6e3a1,stroke-width:2px;
```

### Core Execution Flow
1. **Dynamic Ingestion Subscription**: The ingestion service boots with zero active subscriptions and listens on TCP control port `8085`. Tauri triggers subscriptions based on symbols active in the UI.
2. **Binary Tick Parsing**: High-speed tick data is ingested from Zerodha's Kite WebSocket API, decoded from its big-endian binary layout into internal data structures, and routed according to its contract class.
3. **Dual-Sink Routing & Fault Isolation**:
   - **Equity Path**: Raw equity ticks are published to Kafka (`market.ticks`) and QuestDB (`live_ticks`).
   - **Option Path**: Option ticks are processed concurrently in spawned async tasks, inserting ticks to `option_ticks` and saving latest prices and open interest in-memory. A separate periodic background task writes `option_chain_snapshots` to QuestDB.
4. **Local Tool Server & Quant Computations**: Tauri runs a local Rust Tool Server (port `8084`) that computes VWAP, EMAs, pivot support/resistance, Volume Profiles (POC/VAH/VAL), and detects 19 candlestick patterns client-side.
5. **Stateful ReAct Loop (LangGraph)**: A FastAPI service (port `8086`) compiles the LangGraph state machine. It consumes the Rust Tool Server APIs to retrieve market microstructure, macro trends, and economic indicators.
6. **Self-Verification & UI Event Streaming**: The reasoning loop evaluates proposed setups against rigorous risk rules (expectancy, volatility, indices, clocks) and streams structured updates back to the Tauri frontend via Server-Sent Events (SSE).

---

## 🧠 Deep Quant Analytical Foundation

Strat's reasoning core is driven by a sophisticated multi-variable RAG (Retrieval-Augmented Generation) pipeline that feeds into the **"Self-Defending Hunter" V3 System Prompt (Alpha-Quant)**. All indicators and market calculations are mathematically resolved in the native Rust engine and injected verbatim.

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
let det_a = n * (s_x2 * s_x4 - s_x3 * s_x3) - s_x * (s_x * s_x4 - s_x2 * s_x3) + s_x2 * (s_xy * s_x3 - s_x2y * s_x2);
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

## 🏛️ Options Data Foundation (F&O Sync)

Strat implements a fully automated, end-to-end Options Data Foundation (Phase F1) that syncs derivatives contract lists, resolves option strike chains, and coordinates subscription parameters.

### 1. Bounded Option Chain Resolution (`option_chain.rs`)
Pure, deterministic, clock-free library that constructs the strike-ordered CE/PE ladder for underlyings:
* **ATM Strike Selection**: Locates the listed strike nearest to the underlying spot price (resolving equidistant ties to the lower strike).
* **Strike Band Window**: Filters a contiguous, sorted, and de-duplicated strike band around the ATM strike bounded by a configurable half-width $M$ (delivering a strike band size $\le 2M + 1$).
* **Nearest Expiries**: Filters the $N$ nearest non-expired expiries.
* **Bounded Selection**: Computes the cross product (Selected Expiries $\times$ Clamped Strike Band $\times$ CE/PE Option Types) to form the bounded subset, ensuring subscription size is rigidly limited.

### 2. Bounded Option Chain Subscriber (`option_chain_subscriber.rs`)
A background service running inside the Tauri native bridge that links the database to the ingestion control port:
* **Spot Price Resolution**: Polls QuestDB `live_ticks` every 15 seconds to fetch the latest spot prices for configured underlyings (skipping and logging any underlying whose spot is unavailable to prevent mis-centered bands).
* **SQLite Instrument Load**: Queries NFO contracts from the local SQLite `nfo_instruments` cache.
* **ATM Recenter Gate**: Calculates the new chain selection and compares it against the active subscription. It triggers a control port update only when the ATM strike shifts past a configured threshold.
* **TCP Control Handshake**: Sends a newline-delimited `option_chain_set` command JSON payload to TCP port `8085`.

### 3. Ingestion Subscription Diffing (`ingestion/src/main.rs`)
The ingestion control server parses incoming `option_chain_set` instructions and dynamically synchronizes active subscriptions:
* **Delta Subscriptions**: Diffs the new option token set against the current selection. It sends `subscribe` and `mode=full` commands for added tokens and `unsubscribe` commands for removed tokens to Zerodha's WebSocket API.
* **Fault-Isolated Sinks**: Saves option metadata and latest states in-memory. Incoming option ticks are routed away from the main equity execution threads into `option_ticks` table SQL inserts, preventing option-side latency or DB stalls from degrading equity execution.
* **In-Memory Snapshots**: A separate thread polls the snapshot interval and inserts consolidated `option_chain_snapshots` table rows containing price and open interest (preserving nulls, never fabricating zero) to QuestDB.

---

## 🧠 LangGraph Stateful Agentic Reasoning Core

Strat's reasoning layer runs as an independent FastAPI service on port `8086` orchestrating a stateful LangGraph agent graph.

```mermaid
graph TD
    Start([Start Run]) --> Init[Initialize Agent State]
    Init --> CheckMode{Mode Selector}
    
    %% FIND Mode Flow
    CheckMode -->|FIND| Research[Research Phase: Gather data from Tool Server]
    Research --> CallTools[Call Technical / Regime / Forecast / Profile Tools]
    CallTools --> SaveEvidence[Save Shared Evidence to State]
    SaveEvidence --> CheckTurns{Turns < Max?}
    CheckTurns -->|Yes| DecideFind{Decide?}
    CheckTurns -->|No| ForceHold[Force HOLD: no-decision-reached]
    DecideFind -->|Declare Trade| Commit[Commit Declared Trade]
    DecideFind -->|Watch Price| Suspend[Watch Price: Suspend Run]
    DecideFind -->|More Info| Research
    
    %% VERIFY Mode Flow
    CheckMode -->|VERIFY| RiskAudit[Co-Pilot Risk Critique]
    RiskAudit --> VerifyChecks[Verify SL >= 1.5x ATR, RR >= 1:2, Regime & Clock]
    VerifyChecks --> DevilsAdvocate[Bear Agent: Devil's Advocate Audit]
    DevilsAdvocate --> VerifyVerdict[Format Verification JSON Plan]
    
    %% DEBATE Mode Flow
    CheckMode -->|DEBATE| DebatePrep[Debate Phase: Exclude Committing Tools]
    DebatePrep --> BullTurn[Bull Agent: Arguing Buy Case]
    BullTurn --> BearTurn[Bear Agent: Arguing Sell Case]
    BearTurn --> CheckRounds{Round < Max?}
    CheckRounds -->|Yes| BullTurn
    CheckRounds -->|No| JudgeTurn[Judge Agent: Classify Consensus & Conviction]
    JudgeTurn --> Verdict[Judge Final Decision]
    
    %% Q&A Mode Flow
    CheckMode -->|QA| QAPhase[Trade QA Phase: Session Context Grounding]
    QAPhase --> ReadCheckpoint[Load Persisted Thread via MemorySaver]
    ReadCheckpoint --> AnswerQA[Stream Grounded Answer, Immutable Decision]

    %% Terminal States
    ForceHold --> Finish([Run Finished])
    Commit --> Finish
    Suspend --> Finish
    VerifyVerdict --> Finish
    Verdict --> Finish
    AnswerQA --> Finish
```

### 1. User-Driven Action Modes
* **`FIND` Mode (Directional Hunter)**: Scans the active chart timeframe. Walks the qualitative tool pipeline (macro bias, volume profiles, patterns, OLS, and neural drift forecasts) to identify A+ trades. It commits a trade or places a price trigger via `watch_price_condition` (suspending the graph run until a target candle wakes it up).
* **`VERIFY` Mode (Co-Pilot)**: Evaluates a user-proposed trade bracket. Audits levels against live ATR volatility, Bollinger Bands, and support/resistance pivots. Runs a single-pass Bear Agent Devil's Advocate critique to identify hidden structural red flags.
* **`DEBATE` Mode (Consensus Debate)**: Bypasses single-agent bias. Spawns an internal debate round where a Bull Agent and a Bear Agent (Devil's Advocate) argue over the gathered evidence (both bound to a read-only toolset to prevent unauthorized execution). A Judge Agent classifies their stance as `strong_agree`, `lean`, or `contested`, and derives a numeric conviction score.
* **`QA` Mode (Interactive Auditing)**: Allows users to ask follow-up questions about prior trades. Answers are grounded in the thread's checkpointed session memory (`MemorySaver` checkpointer). The active `Declared_Trade` remains strictly immutable.

### 2. Multi-Agent Debate & Stance Judge (`debate.py` & `calibration.py`)
* **Arguing Roles**: The Bull and Bear agents conduct structured debate rounds over the shared evidence. They are restricted from committing trades (`declare_trade` is disabled).
* **Judge Consensus Classification**: The Judge agent applies a deterministic classification strategy over the stances:
  - `STRONG_FLOOR` or `STRONG_GAP` indicates clear directional dominance.
  - `CONTESTED_GAP` indicates a high degree of disagreement.
* **Conviction Calibration**: The Judge maps the classified consensus and relative distance of stances into a calibrated conviction score $[0, 100]$ using predefined weights. This prevents LLM hallucinations on confidence scoring.

### 3. Timezone-Aware Session & Expiry Engine (`session.py`)
* **NSE Session Phases**: Maps trades strictly to exchange timezone constraints (`Asia/Kolkata` / +05:30) and classifies the current phase:
  - `pre_open`, `opening` (violent mean-reversion, minutes 0–15), `morning`, `midday` (thin chop, low volume), `afternoon`, `closing`, and `post_close`.
* **Expiry Awareness**: Checks whether the current session is an expiry day (`is_expiry_day`) and computes the number of days until the nearest weekly/monthly option contract expiry (`days_until_expiry`).
* **Time-Based Gating**: Evaluates clocks to label `time_favorability` (e.g. flagging setups in the opening drive or late expiry afternoons as `unfavorable`). Gating is non-blocking: the agent is warned but can still proceed. Unfavorable windows require explicit disclosures in the committed defensibility record.

### 4. Trade exits & simulator (`trade_manager.py` & `journal.py`)
* **Managed Exit Simulation**: Evaluates proposed bracket trade setups over candle arrays using `trade_manager.simulate_plan`. It strictly models multi-leg target execution, stop-loss triggers, breakeven trigger thresholds, and trailing stop offsets.
* **Trade Expectancy Audit**: Keeps track of the agent's edge by checking its win rate and expectancy (measured in $R$) overall and per setup type using a local SQLite-backed trade journal (`journal.py`).
* **Calibrated Confidence**: The agent adjusts its `conviction_score` downward if its trade journal reports a historically negative expectancy for comparable setups.

### 5. Volatility-Aware Forecast Signal Filtering (`forecaster.py`)
* **Forecast Engine**: Employs regime- and volatility-aware mathematical forecasters (`get_forecast`) to compute a Projected Direction, an Up-Probability $[0.0, 1.0]$, and the Expected Move scaled in ATR units.
* **Forecast Gate Filtering**: Acts as the primary predictive check. A directional trade proposed against the forecast gate (misaligned Forecast Alignment or insufficient Up-Probability) triggers a reduction in conviction or forces a `HOLD`.

### 6. Glass-Box Execution Visibility & Budget Controls
* **Reasoning Budget**: Bounds the number of consecutive reasoning-only turns (default: 3) to prevent the LLM from entering infinite loop states when tools fail. Exceeding the budget automatically forces a `HOLD` with a `no-decision-reached` label.
* **Chronological SSE Stream**: Streams event frames containing the chronological reasoning logs, tool start, and tool results back to Tauri. Surfacing a clean `ERROR` event (rather than a fabricated JSON fallback plan) if the LLM provider becomes unreachable.

---

## 📂 Core Component Directory Map

```text
/
├── agents/
│   ├── deep-quant-loop/  # LangGraph FastAPI service (ReAct loop, Debate roles, Exits simulator)
│   ├── technical/        # Rust Technical Indicator calculations (RSI, BB, MACD, EMA)
│   ├── predictive/       # Rust OLS and naive Linear Regression projection engine
│   ├── quant-rag/        # DeepSeek anomaly analysis & headline insights via NVIDIA NIM
│   └── sentiment/        # News evaluation & Claude sentiment classifier with Redis cache
├── aggregator/           # Consensus broadcasting & Dynamic Weighting aggregator (Rust)
├── alpha-terminal/       # OHLC 10m window aggregation and WebSocket broadcaster (Rust)
├── ingestion/            # High-speed binary Kite WS tick dual-sink router (Rust, port 8085)
├── backend/              # QuestDB Schema migration runner
├── shared_protos/        # Protobuf data schemas & contracts (market_data.proto)
├── tools/                # load_tester (Stress testing chaos engine with anomaly injector)
└── frontend/             # Desktop HUD Terminal
    ├── src-tauri/        # Tauri Native Bridge (Instrument master sync, option chain subscriber)
    └── src/
        ├── app/          # Dashboard HUD layouts (Intraday, Swing, Investor profiles)
        ├── store/        # Telemetry State management (useTradeStore, useQuantStore)
        └── components/   # HTML5 Canvas chart overlays (Volume Profile, Level-2 Footprint)
```

---

## 🧪 Purity & Property-Based Testing Foundation

To ensure mathematical correctness, the platform adopts a strict **purity-first design**. All quantitative calculations, configurations, and state decisions are extracted into pure functions that are tested against arbitrary boundaries.

* **Property-Based Testing (Hypothesis & Proptest)**: Over 160 unit and property tests validate the behavior of the platform:
  - **F&O Config Totality**: Confirms `resolve_fno_config` never panics and resolves defaults safely for any environment variable state (R6.1–6.3).
  - **Option Selection Contiguity**: Confirms strike band clamping, ATM selection, and nearest expiries logic are correct over arbitrary random-walk candles.
  - **Debate Configuration & Stances**: Tests Judge decision loops, conviction calibration weights, and stance parsing over thousands of generated inputs.
  - **Session & Expiry Limits**: Assures timezone mapping, minutes since open/until close calculations, and expiry contexts do not suffer from offset or lookahead biases.
  - **Trade Manager Exit Logic**: Validates simulated exit brackets, stop adjustments, and trailing fills under synthetic price trajectories.
  - **Fault Isolation**: Validates that option-routing failures never block the main equity stream.

---

## 🛠️ Developer Setup & Deployment

### Environment Configurations
Create a `.env` file at the root:
```env
# ── Database Configuration ───────────────────────────────
QUESTDB_HTTP_URL="http://127.0.0.1:9000"
QUESTDB_POSTGRES_URL="postgresql://admin:quest@localhost:8812/qdb"

# ── Zerodha Kite Credentials ─────────────────────────────
KITE_API_KEY="your_kite_api_key"
KITE_ACCESS_TOKEN="your_daily_kite_access_token"

# ── LLM Inference Provider (Gemini / OpenAI Compatible) ────────────────────
LLM_API_URL="https://generativelanguage.googleapis.com/v1beta/openai"
LLM_API_KEY="AIzaSyXXXXXXXXXXXXXXXXXXXX"
LLM_MODEL="gemini-2.5-flash"
LLM_MAX_RETRIES="4"
LLM_TIMEOUT_SECS="90"

# ── F&O Ingestion Parameters ─────────────────────────────
FNO_UNDERLYINGS="NIFTY 50,BANKNIFTY"
FNO_NEAREST_EXPIRIES="2"
FNO_STRIKE_BAND_HALF_WIDTH="10"
FNO_ATM_RECENTER_THRESHOLD="1.0"
FNO_SNAPSHOT_INTERVAL_SECS="60"
INGESTION_CONTROL_PORT="8085"

# ── Redis & Kafka Brokers ────────────────────────────────
REDIS_URL="redis://127.0.0.1:6379"
KAFKA_BROKER_URL="127.0.0.1:19092"
```

### Dev Launch Pipeline

1. **Stand up Core Infrastructure**:
   ```bash
   docker-compose up -d
   ```
   Starts Redis, Redpanda (Kafka), and QuestDB.

2. **Boot Deep Quant ReAct Agent Service**:
   ```bash
   cd agents/deep-quant-loop
   pip install -r requirements.txt
   python main.py
   ```
   Launches the LangGraph reasoning API on `http://127.0.0.1:8086`.

3. **Verify Backend API & Option Chain Property Tests**:
   ```bash
   cd frontend/src-tauri
   cargo test
   ```

4. **Boot Ingestion Service**:
   ```bash
   cd ingestion
   cargo run
   ```

5. **Boot Desktop Terminal**:
   ```bash
   cd frontend
   npm run tauri dev
   ```

---

> [!IMPORTANT]
> **Dynamic Weighting Conflict Resolution**: When technical indicators diverge significantly from Claude-evaluated sentiment scores, the aggregator applies dynamic weighting multipliers, shifting capital allocations toward **Hold/Capital Preservation** to prevent trade execution on unconfirmed setups.