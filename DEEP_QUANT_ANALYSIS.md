# Deep Quant — End-to-End Architecture Analysis

> How the Ai-trader platform finds "deep profitable trades": the full path from a user click in the
> frontend, through the LangGraph agent and the Rust tool server / LLM, to the streamed chat output
> served back in the terminal's AI Quant session.
>
> Scope: the **Deep Quant** feature specifically — the agent-driven trade-finding pipeline. The
> broader V1/V2 market-data fabric (ingestion, alpha-terminal, aggregator, predictive/quant-rag agents)
> is summarized only where it feeds this feature.

---

## 0. TL;DR — the one-paragraph version

The user clicks **Find Quant Trade** in the right-hand AI Quant panel of the Tauri desktop terminal.
The React frontend calls the Tauri IPC command `run_deep_quant_agent`, which POSTs to a Python
**LangGraph ReAct agent** (`agents/deep-quant-loop`, FastAPI on `localhost:8086`). That agent reasons
through an ordered macro→microstructure analysis, calling a set of `@tool` functions. Every tool is a
thin HTTP client over a **Rust Tool Server** (`frontend/src-tauri/quant/tool_server.rs` on
`localhost:8084`) which is the single source of truth for candles, indicators, options, order flow,
patterns, support/resistance, and news — all read from **QuestDB** (Postgres wire `:8812`,
HTTP `:9000`) and a live-tick WebSocket bridge (`:8089`). The agent's reasoning, tool calls, and
final decision stream back to the frontend over Tauri events (`deep-quant-stream`) as an SSE-style
event vocabulary (`RUN_STARTED` → `REASONING` / `TOOL_CALL_*` / `DECISION` → `RUN_FINISHED`) and render
live in the **AgentTerminal** chat/transcript. The agent commits a trade only through the
`declare_trade` tool, which passes a hard risk validator (stop ≥ 1.5×ATR, R:R ≥ 1:2). If no A+ setup
exists yet, the agent can arm `watch_price_condition`, which suspends the run; the Rust server later
watches live ticks and POSTs `/resume` to wake the same thread when the level triggers — surfacing in
the UI as a "Watching" state and then a fresh reasoning step. A separate **Trade_QA** chat mode lets the
user ask follow-up questions grounded in the persisted session context, streamed over
`deep-quant-qa-stream`.

---

## 1. The system at a glance

```
                         ┌──────────────────────────────────────────────┐
                         │            FRONTEND (Next.js + Tauri)         │
                         │  app/page.tsx  →  <DeepQuantPanel>            │
                         │     ├── <AgentTerminal>   (reasoning stream) │
                         │     ├── <VerificationForm> (VERIFY input)      │
                         │     ├── <AiExecutionPlanView> (final plan)   │
                         │     ├── <TradeQaPanel> + <QaMessages> (chat)  │
                         │     └── <WatchingIndicator> (watch state)    │
                         │  store: useQuantStore, useTradeStore         │
                         └───────────────┬──────────────────────────────┘
                            invoke()  │  listen()
                         Tauri IPC    ▼   Tauri events
                         ┌──────────────────────────────────────────────┐
                         │            RUST CORE (src-tauri)              │
                         │  commands/deep_quant.rs                       │
                         │    run_deep_quant_agent  ──► POST :8086/run    │
                         │    ask_trade_question    ──► POST :8086/qa    │
                         │    run_ai_analysis (legacy glass-box path)    │
                         │  quant/tool_server.rs  (axum :8084)           │
                         │    /tools/get_candles, get_consensus,        │
                         │    get_support_resistance, get_multi_tf_trend│
                         │    get_chart_patterns, get_prediction,       │
                         │    get_news_context, watch_condition,        │
                         │    declare_trade  (risk-validator gate)       │
                         │  services/llm.rs (OpenAI-compatible client)  │
                         │  services/live_bridges.rs (WS→IPC)           │
                         └───────┬───────────────┬───────────────┬───────┘
                                 │               │               │
                        POST :8086│         QuestDB       WS :8081/8089
                                 │           :8812/:9000         (live ticks)
                                 ▼
                         ┌──────────────────────────────────────────────┐
                         │     PYTHON LANGGRAPH AGENT (:8086)            │
                         │  agents/deep-quant-loop/main.py (FastAPI)     │
                         │    /run  /resume  /qa  /options/snapshot      │
                         │  graph.py  (StateGraph + MemorySaver)         │
                         │    nodes: agent, tools, force_hold,           │
                         │    force_terminal, bull, bear, judge,        │
                         │    qa_agent, qa_tools                         │
                         │  tools.py  (@tool → POST :8084/tools/*)       │
                         │  opportunity.py, validator.py, debate.py,    │
                         │  regime.py, forecaster.py, options*.py,       │
                         │  session.py, rs.py, order_flow.py, ...        │
                         │  prompt.md  (Alpha-Quant charter)             │
                         └──────────────────────────────────────────────┘
```

### Process & port map

| Process | Language | Port(s) | Role in Deep Quant |
|---|---|---|---|
| `frontend` (Tauri shell + Next.js) | Rust + TS | dev `:3000`, IPC | UI, IPC bridge, owns the Tool Server `:8084` |
| Rust Tool Server (`quant/tool_server.rs`) | Rust | `127.0.0.1:8084` | **Single source of truth** for all market data the agent reads |
| Python Deep Quant Loop (`agents/deep-quant-loop`) | Python | `0.0.0.0:8086` | LangGraph ReAct agent — reasoning, tools, debate, QA |
| QuestDB | — | `:8812` (PG), `:9000` (HTTP) | Historical candles, intraday bars, `live_ticks` |
| Live bridges (`services/live_bridges.rs`) | Rust | `:8081` OHLC, `:8089` order-flow | WS→IPC; feeds the watcher's live-candle broadcast |
| Node Sentiment Service (`agents/sentiment`) | Node | `:8090` (default `SENTIMENT_SERVICE_URL`) | News classification for `get_news_context` |
| LLM provider | — | `LLM_API_URL` (default `https://api.freemodel.dev/v1/chat/completions`) | OpenAI-compatible `chat/completions`; default model `LLM_MODEL` (`gpt-5.5` in the live `.env`, `deepseek-ai/DeepSeek-V3-0324` in code default) |

> Note: ARCHITECTURE.md still references DeepSeek-v4/NVIDIA-NIM for the older `quant-rag` agent. The
> **Deep Quant** path itself has no Bedrock or NVIDIA-NIM code; `services/llm.rs` and `graph.py` both
> use a single OpenAI-compatible endpoint driven by `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL`.

---

## 2. The frontend: where the trade is requested and served

All paths under `frontend/src/`. The Deep Quant UI is a right-sidebar panel in the main terminal at
`app/page.tsx` (`:17` import, mounted at `:572` when `sidebarTab === 'deepquant'`, selected via the
"AI QUANT" tab at `:548-567`). `app/dashboard/page.tsx` is a stub that redirects to `/`.

### 2.1 Two data paths coexist — only one is live

| | Path A — Rust Glass-Box (legacy) | Path B — Python LangGraph (live) |
|---|---|---|
| Trigger | `invoke('run_ai_analysis')` / `run_deep_quant_analysis` | `invoke('run_deep_quant_agent')` |
| Backend | Rust `run_glass_box_loop` calling `llm::generate_autonomous_step` directly | Python `:8086/run` LangGraph |
| Events | `agent_message`, `final_analysis_ready` | `deep-quant-stream`, `deep-quant-qa-stream` |
| UI consumer | **none** — listeners populate `useTradeStore.agentChatLog` / `finalTradePlan` which no Deep Quant component renders | `DeepQuantPanel`, `AgentTerminal`, `TradeQaPanel` |

Path A's store bridge still exists (`useTradeStore.ts:273-306`: `paper_portfolio_update`, `agent_message`
→ `agentChatLog`, `final_analysis_ready` → `finalTradePlan`, deliberately **not** clearing
`isAnalyzing` because the SSE `RUN_FINISHED` is authoritative), but **no UI component invokes
`run_ai_analysis`/`run_deep_quant_analysis`**. The modern Deep Quant UI is entirely Path B. The Rust
glass-box loop remains as a fallback/legacy execution path and is what `run_deep_quant_analysis`
forwards to (`commands/deep_quant.rs:1267`), but it is dormant in the current UI.

### 2.2 `DeepQuantPanel.tsx` (425 lines) — the orchestrator

- **Paywall + data guard.** FREE/no-tier users see `<PremiumPaywall>` (`:90-92`; upgrade POSTs to
  `:3002/api/payments/phonepe/checkout` then `invoke('open_browser')`). The `deep-quant-stream`
  listener is registered **before** this early return (`:65-88`) to keep hook order stable and to
  catch early events. A data-readiness guard counts candles in `useTradeStore.historicalCache`
  (`:102-114`); the button is disabled until data is present and shows `AWAITING DATA…` when `< 50`.
- **Trigger.** Button `#btn-run-deep-quant` (`:246-287`) branches on `activeMode`:
  - `FIND` → `handleAIAnalysis()` → `useQuantStore.fetchDeepAnalysis(symbol)`.
  - `VERIFY` → `handleVerifyAnalysis()` → parses entry/SL/TP from `VerificationForm` and calls
    `fetchDeepAnalysis(symbol, 'VERIFY', { side, entry, stopLoss, takeProfit, userAnalysis })`.
- **The actual invoke** lives in the store: `useQuantStore.fetchDeepAnalysis`
  (`useQuantStore.ts:989-1122`) calls `tauriInvoke('run_deep_quant_agent', { symbol, mode, timeframe,
  profile, fnoExpiry, model, manualTrade })` (`:1057-1074`). So the panel never calls the Rust
  glass-box commands — it always goes through the Python agent.
- **Event wiring.** `deep-quant-stream` (`:65-88`) → `useQuantStore.handleStreamEvent` → the pure
  reducer `applyStreamEvent` (`:631-800`). `agent_status` (`:148-160`) → local state (currently has no
  visible surface; `LoadingState` is imported but not rendered).
- **Render tree** (`:384-408`): if reasoning is streaming or session active → `<AgentTerminal>`;
  else if error → `<ErrorState>`; else if `aiPlan` → `<AiExecutionPlanView>`; else `<EmptyState>`.
  `<VerificationForm>` shows only in VERIFY pre-run; `<TradeQaPanel>` is pinned as a footer whenever a
  session is active.
- **Per-session isolation.** `activateSymbolSession(symbol, profile)` (`:174-176`) snapshots/restores
  sessions keyed by `${SYMBOL}::${PROFILE}` — so INTRADAY-RELIANCE and SWING-RELIANCE persist
  independently, and a background run for symbol A keeps streaming into A's session while the user
  views B.

### 2.3 `AgentTerminal.tsx` (407 lines) — the live transcript (chat surface)

This is the **agent reasoning log**, and Q&A turns render inline inside it via `<QaMessages />`
(`:334`). It deliberately does **not** subscribe to `deep-quant-stream` itself — the panel-level
listener avoids a race where AgentTerminal mounts mid-run and drops early events (`:30-34`). It reads
`reasoningSteps`, `sessionStatus`, `finalTrade`, `analysisError`, `qaMessages`, `qaStatus` from the
store.

- `step.type === 'message'` (`:102`): if the content is a JSON trade plan, renders a "Final Trade
  Decision" card reading `conviction_score` / `setup_validation` / `execution_plan`; otherwise a
  Markdown reasoning card.
- `step.type === 'tool_start'` (`:161`): "Executing Tool / Tool Completed" card; completion is gated
  by `runSettled = sessionStatus !== 'running'` so a dangling `tool_start` never spins "ACTIVE"
  forever.
- `sessionStatus === 'watching'` → `<WatchingIndicator />` (`:237`) — the visible "watch a condition"
  surface.
- When `complete && isActionableTrade(finalTrade)` → a pinned "Actionable Trade Plan Ready" card with
  an **Approve & Execute** button (`:341-404`) that calls `invoke('execute_paper_trade', { symbol,
  side, entryPrice, stopLoss, takeProfit })` (paper-trading deploy).
- When `complete && finalTrade && !isActionableTrade(finalTrade)` → an inline "Stand Aside — No Trade"
  card (`:305-329`) — no execute button.
- Errors render with "Connection refused: Python service port :8086 unreachable." (`:294`).

### 2.4 `components/quant/deep-quant/*` — the session UI kit

| File | Purpose |
|---|---|
| `AiExecutionPlanView.tsx` | Renders the committed plan: conviction bar (HIGH ≥80 / MODERATE ≥60 / LOW ≥40 / VERY WEAK), setup validation, execution plan. Deploy button only when `actionable`. Conviction renders `"—"` when absent — never fabricated. |
| `QaMessages.tsx` | **The chat history surface.** User turns right-aligned, assistant turns left-aligned with streamed `AnswerText` (lightweight markdown), a "Thinking…" spinner while streaming, and a graceful "No answer was produced…" fallback. Tool-call traces render as `Wrench`-prefixed mono `activity` lines above the answer. |
| `VerificationForm.tsx` | VERIFY input: Side (BUY/SELL), Entry/SL/TP numeric inputs with live `%` deviation + R:R badges, analysis-notes textarea. Submits as `manualTrade` to `run_deep_quant_agent` in VERIFY mode. |
| `useVerificationForm.ts` | Auto-fills entry from `livePrice`, SL ±2%, TP ±5%; computes R:R and percentages; resets on symbol change. |
| `ModelSelector.tsx` | Portal LLM picker (provider→model flyout); `onChange` → `setSelectedModel`. Used by `TradeQaPanel`. |
| `MarkdownRenderer.tsx` | Lightweight markdown (headers, bullets, bold) for reasoning cards. |
| `MultiTfPatternsView.tsx` | Multi-timeframe pattern scanner (`1m…1d`); clicking a pattern shifts symbol/timeframe and pushes a `RadarVizTarget` for on-chart overlay. |
| `PremiumPaywall.tsx` | FREE-tier gate. |
| `LoadingState.tsx` / `ErrorState.tsx` / `EmptyState.tsx` / `WatchingIndicator.tsx` | State surfaces. (`LoadingState` is imported but currently not rendered in the panel tree.) |

### 2.5 Stores

**`useQuantStore.ts` (1519 lines)** — the core. Key state: `aiPlan`, `isAnalyzing`, `analysisError`,
`sessionStatus: 'idle'|'running'|'watching'|'complete'|'error'`, `reasoningSteps`, `finalTrade`,
`sessionsByKey` (per `${SYMBOL}::${PROFILE}`), `selectedModel`, `currentThreadId`, `qaMessages`,
`qaStatus`. `handleStreamEvent` (`:1175-1221`) routes each SSE event to the correct session via
`_threadToKey` (thread id → session key), so concurrent runs for different symbols never cross. The
pure reducer `applyStreamEvent` (`:631-800`) handles `RUN_STARTED` (captures `thread_id`, emits a
"Resuming Analysis — Fresh Market Data" step when prior status was `watching`), `REASONING`,
`TEXT_MESSAGE`, `BEST_CURRENT_READ`, `VERIFICATION_STEP`, `DECISION` (builds `aiPlan`/`finalTrade`),
`TOOL_CALL_START` (sets `watching` if tool is `watch_price_condition`), `TOOL_CALL_END`, `RUN_FINISHED`
(`status==='paused'` → `watching`; else parses final plan), `ERROR`.

**`askQuestion` (`:1243-1388`)** — the Q&A chat path: requires `currentThreadId`; pushes a user turn +
a streaming assistant turn; `listen('deep-quant-qa-stream')` (`:1299`) appends `REASONING`/
`TEXT_MESSAGE`/`TOOL_CALL_*` to the assistant turn; then `tauriInvoke('ask_trade_question', { threadId,
question, model })` (`:1372`). Listener is torn down on `RUN_FINISHED`/error.

**`useTradeStore.ts`** — holds `selectedSymbol`, `activeTimeframe` (default `10m`), `activeProfile`
(`INTRADAY`/`SWING`/`INVESTOR`/`FNO`), `fnoUnderlying`/`fnoExpiry`, `historicalCache` (for the
data-ready guard), `paperPortfolio`. Legacy Path-A fields (`agentChatLog`, `finalTradePlan`) linger
but are unused by the quant UI.

**`useRadarStore.ts`** — radar watchlist + on-chart viz target; Deep Quant touches it only via
`MultiTfPatternsView.handlePatternClick → setVizTarget`.

### 2.6 The "is it a real trade?" gate

`isActionableTrade` (`useQuantStore.ts:73-88`) is the single predicate used by both the panel (deploy
button) and AgentTerminal (execute card): a directional BUY/SELL (not HOLD, not `stand_aside`) with
three finite positive `execution_levels`. Entry/SL/TP come **only** from `finalTrade.execution_levels`
— never scraped from prose. This is what decides whether the user sees an "Approve & Execute" button
or a "Stand Aside" card.

---

## 3. The Rust core: data substrate, tool server, and IPC bridge

### 3.1 `commands/deep_quant.rs` (4560 lines) — the IPC surface

Six `#[tauri::command]`s are registered in `lib.rs:315-350`:

| Command | Line | Role |
|---|---|---|
| `run_deep_quant_analysis` | 1267 | Legacy alias → forwards to `run_ai_analysis` with `mode="FIND"`. |
| `run_ai_analysis` | 1247 | **Legacy glass-box path.** Spawns `run_glass_box_loop` (FIND/VERIFY) in a tokio task; returns immediately. Emits `agent_message`/`final_analysis_ready`. |
| `run_deep_quant_agent` | 2169 | **The live path.** POSTs `{thread_id, message, mode, symbol, timeframe, profile, fno_expiry, model, manual_trade}` to `http://localhost:8086/run` (`:2230`), proxies the SSE stream onto the `deep-quant-stream` event (`:2289`), emits a synthetic `RUN_FINISHED` if the stream ended without one. `thread_id = thread_{symbol}_{ts_millis}` (`:2191`). |
| `ask_trade_question` | 2344 | Trade_QA. POSTs `{thread_id, question, model}` to `:8086/qa` (`:2366`), proxies onto `deep-quant-qa-stream` (`:2416`). Never emits a DECISION, never mutates the committed trade. |
| `get_multi_timeframe_chart_patterns` | 2487 | Pure read: parallel `["1m","5m","10m","15m","1h","4h","1d"]` scan via `ChartPatternEngine::analyze_forming`. |
| `deploy_ai_sentinel` | 1991 | Background watchdog: re-fetches candles, recomputes consensus, calls `llm::generate_sentinel_plan`, emits `sentinel_alert` when conviction > 60. |

**The legacy glass-box loop** (`run_glass_box_loop`, `:1311-1923`) is itself a complete agent: fetch
candles from QuestDB (`load_candles_from_db` `:1352`, Kite backfill if `<50`, abort if `<30`),
compute indicators → `ConsensusEngine::compile_consensus` (`:1422`, emit `quant-consensus` `:1425`),
extract RAG context, fetch news (`fetch_news_context` `:1515`), then an agentic loop (max 10 turns)
calling `llm::generate_autonomous_step` with three tools (`wait_for_next_candle`,
`fetch_higher_timeframe`, `fetch_news_context`). On finish it parses the LLM JSON into
`AiExecutionPlan` (`parse_agent_response` `:1925`) and emits `final_analysis_ready` (`:1917`). On
parse failure it returns an honest **LOW-conviction HOLD diagnostic** — never a fabricated winning
plan.

**News fetching** (`fetch_news_context` `:79`): if `NEWS_API_URL` is set, try that aggregator first;
otherwise/failure → Google News RSS (`fetch_google_news_rss_for_context` `:147`, no API key, top 5
headlines). Never returns an empty string if RSS is reachable. Kite credentials are read from env,
falling back to walking up the directory tree to find a `.env` (`get_kite_credentials` `:22`).

### 3.2 `services/llm.rs` (1295 lines) — the LLM client

One **provider-agnostic**, OpenAI-compatible `chat/completions` client. No Bedrock, no NVIDIA-NIM.
- Env: `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` (defaults `https://api.freemodel.dev/v1/chat/completions`,
  `deepseek-ai/DeepSeek-V3-0324`).
- **Key resolution precedence** (`:460-484`): in-app **Stronghold/SecureKeyStore vault** alias
  `llm_key` → fallback `hf_key` → fallback `deepseek` → then env `LLM_API_KEY` via `resolve_api_key()`
  (`:269`; returns `"TEST_KEY"` when `ALPHA_TEST_MODE` is set).
- **Master Prompt** `build_system_prompt` (`:190-249`): the "high-conviction institutional" prompt —
  injects numeric indicators verbatim and tells the model never to guess them; sections for market
  state/macro, microstructure & volume (OFI), volatility & anomalies, momentum/trend/patterns,
  strict directives (mandatory tool use turn 1, `wait_for_next_candle` first, high-probability-only),
  and the JSON contract `{conviction_score, setup_validation, execution_plan}`.
- **Tool schema** `deep_quant_tool_schema` (`:126-162`): the single source of truth for the three
  glass-box tools advertised to the model.
- **Data-awareness is per-field honesty, not timestamp gating.** `format_ofi` renders `NaN` as
  "N/A — unavailable (do not weight order flow)" rather than `0.00`; `execute_news_tool` returns an
  explicit "no news" string; `execute_wait_for_next_candle_tool` sleeps to the next bar boundary
  (sandbox-capped at 30 s under `DEEP_QUANT_SIMULATE_WAIT`) and re-reads QuestDB. There is no
  staleness check that silently drops news.

### 3.3 `quant/tool_server.rs` (4349 lines) — the single source of truth (`127.0.0.1:8084`)

An **axum** server started from `lib.rs:262`. Every handler pulls the shared `sqlx::PgPool`
(QuestDB Postgres wire `:8812`, created in `lib.rs:267`) and delegates candle loading to the same
`load_candles_from_db` / `load_candles_with_ts` the Tauri commands use — so the Python agent and the
UI see **identical** candles/consensus. QuestDB HTTP `:9000` is used only by the ad-hoc
`fetch_questdb` UI command.

Endpoints (route registration `:1901-1911`):

| Endpoint | Purpose |
|---|---|
| `POST /tools/get_candles` | Ascending OHLCV candles. `Shortfall` → 200 `{"unavailable":true}`; `Fault` → 503. |
| `POST /tools/get_consensus` | `IndicatorState::from_candles_basic` + `ConsensusEngine::compile_consensus`; emits `quant-consensus`; returns `ConsensusReport`. |
| `POST /tools/get_support_resistance` | Pivot/S/R levels via `compute_sr`. |
| `POST /tools/get_multi_tf_trend` | 1H/4H/1D EMA-pair trend biases. |
| `POST /tools/get_chart_patterns` | `ChartPatternEngine::analyze`. |
| `POST /tools/get_prediction` | Pure OLS `build_projection` (the Rust Predictive_Engine). |
| `POST /tools/get_news_context` | Google News RSS headlines + Node Sentiment_Service (`:8090`) classification; headlines-only fallback if classifier down (never a fabricated "Neutral"). |
| `POST /tools/watch_condition` | Registers a `Watcher`; validates target strictly beyond a server-captured `reference_price`; spawns a task subscribing to the live-candle broadcast channel; on trigger POSTs `:8086/resume` and proxies the resume SSE onto `deep-quant-stream`. Heartbeat branch for bounded cadence resumes. |
| `POST /tools/declare_trade` | **The risk-validator gate.** `evaluate_declared_trade` → `validate_trade`; on `Fail` returns `{"status":"rejected","reason"}` and emits nothing; on `Pass` builds an `AiExecutionPlan`, emits `final_analysis_ready` + `agent-declared-trade`, returns `{status:"trade_declared", action, conviction_score, risk_reward}`. |

There is **no `/tools/get_order_flow`** — the Python `get_order_flow` tool computes it itself from
`/tools/get_candles` plus a direct QuestDB `live_ticks` read.

**Watcher → resume mechanics.** The watcher subscribes to a
`tokio::sync::broadcast::Sender<(String, OhlcCandle)>` registered as Tauri state (`lib.rs:126`),
fed by `live_bridges::spawn_bridge` which parses `ohlc-tick` frames from the `:8081` OHLC WS bridge
(`live_bridges.rs:161-164`). When `watcher_triggered` fires (pure predicate, `:414-454`), it removes
the watcher and POSTs `:8086/resume` with `{thread_id, triggered_candle, trigger_kind}`; the returned
SSE stream is proxied onto `deep-quant-stream` (`:971`).

### 3.4 `quant/mod.rs` — consensus, indicators, validator, the plan

- **`IndicatorState`** (`:21-49`): ~25 raw indicators computed in `from_candles_basic` (SMAs, EMAs,
  MACD, ATR, Bollinger, VWAP, ORB, Parabolic SAR, Stoch %K, OBV, CMF, RSI, average volume).
- **`ConsensusReport`** (`:1201-1233`): `symbol`, `trend_score: i32` (-100..+100),
  `momentum_state` (OVERBOUGHT/OVERSOLD/NEUTRAL), `volatility_state` (EXPANDING/SQUEEZING/NORMAL),
  `volume_flow_state` (ACCUMULATION/DISTRIBUTION/NEUTRAL), `active_patterns`, `active_strategies`,
  vwepr/ols values, and raw indicator `Option<f64>` (non-finite → JSON `null`).
- **`ConsensusEngine::compile_consensus`** (`:1263`): `trend_score` = ±25 for each of
  (close vs sma_50, close vs sma_200, macd_histogram sign, parabolic_sar < close), clamped [-100,100].
- **`AiExecutionPlan`** (`:1238-1246`): `{ conviction_score: i32 1-100, setup_validation: String,
  execution_plan: String }` — the final payload to React.
- **Trade Validator** (`:448-858`): `Action{Buy,Sell,Hold}`, `ExecutionLevels{entry,stop_loss,take_profit}`,
  `ValidatorReason` (`MissingLevels`, `RiskRewardTooLow`, `StopTooTight`, `DirectionInconsistent`,
  + multi-leg reasons). `validate_trade`: HOLD always passes; BUY/SELL checked for MissingLevels,
  DirectionInconsistent, StopTooTight (`|entry−sl| ≥ 1.5×ATR`), RiskRewardTooLow (`reward/risk ≥ 2.0`).
  Constants: `MIN_RISK_REWARD=2.0`, `MIN_STOP_ATR_MULTIPLE=1.5`.

### 3.5 `lib.rs` — wiring

`.env` load anchored at `CARGO_MANIFEST_DIR` trying `../../.env`, `../.env`, `.env` (`:82-120`).
Managed state: `ActiveSymbolState` (default "RELIANCE"), the live-candle broadcast channel
(`:126`), `SecureKeyStore`, `RadarRegistry`, `Mutex<VirtualPortfolio>` (paper trading, $1,000,000
start). `setup` spawns `run_instrument_sync`, `run_nfo_sync`, `run_option_chain_subscriber`,
`spawn_radar_worker`, `run_tool_server` (`:262`), and the QuestDB pool + `run_migration` (`:265-301`).
Live WS bridges are **lazily** bootstrapped on the first `subscribe_ticker` (`:303-311`). Plugins:
`tauri_plugin_single_instance`, `tauri_plugin_deep_link` (`strat://` scheme), `tauri_plugin_stronghold`
(Argon2id). `invoke_handler` registers 30 commands (`:315-350`).

**Events emitted to the frontend** (deduped across the Rust core): `agent_message`, `agent_status`,
`quant-consensus`, `final_analysis_ready`, `sentinel_status`, `sentinel_alert`, `deep-quant-stream`,
`deep-quant-qa-stream`, `agent-declared-trade`, `radar-alert`, `historical-loaded`, `system-error`,
`fno-snapshot`, `paper_portfolio_update`, `orderbook-update`, `ohlc-tick`, `predictive-tick`,
`insight-tick`, `order_flow_stream`, `broker-connection-success`, `payment-success`.

---

## 4. The Python agent: reasoning, tools, debate, and the bounded hunt

### 4.1 `main.py` (496 lines) — FastAPI surface

| Endpoint | Body | Role |
|---|---|---|
| `POST /run` (`:176`) | `RunRequest{thread_id, message, mode, symbol, manual_trade, timeframe, profile, fno_expiry, model}` | Start a run. `mode ∈ {FIND, VERIFY, DEBATE}`. Builds `initial_state`, wraps `event_generator` in a telemetry tee, `await graph.astream(...)` with `config={"configurable":{"thread_id":...}}`. |
| `POST /resume` (`:203`) | `ResumeRequest{thread_id, triggered_candle, trigger_kind}` | Resume a paused (watching) run. Verifies `state.next` is non-empty, resumes with `Command(resume={...})`. |
| `POST /qa` (`:234`) | `QARequest{thread_id, question, model}` | Trade_QA follow-up grounded in the persisted `Session_Analysis_Context` via the MemorySaver checkpointer. Never mutates the committed trade. |
| `GET /options/snapshot` | `?symbol=&expiry=` | F&O analytics snapshot (composes `options.read_latest_and_prior_snapshot` + `compute_options_analytics` + `classify_options_bias`). |
| `uvicorn.run(...)` | — | Binds `0.0.0.0:8086` (`:496`). |

**SSE ordering** (`event_generator` `:115-172`, helpers in `stream_events.py`): `RUN_STARTED` first →
per-node-update events expanded in step order (`REASONING`/`TEXT_MESSAGE`/`BEST_CURRENT_READ`/
`VERIFICATION_STEP`/`TOOL_CALL_START`/`TOOL_CALL_END`/`DECISION`) → a single terminal `RUN_FINISHED`
(`completed` if `state.next` empty, else `paused`) or `ERROR` last. Telemetry is a non-invasive
best-effort tee (`_observe` `:54`) — any failure degrades to the bare stream; bytes are identical.

### 4.2 `graph.py` (5433 lines) — the state machine

**`AgentState(TypedDict)`** (`:201`): `messages` (append-reducer), `mode`, `symbol`, `manual_trade`,
`timeframe`, `profile`, `fno_expiry`, `model`, `decision` (**authoritative completion signal**),
`reasoning_turns` (capped by `MAX_REASONING_TURNS=6`), `market_data_seen`, `qa_turns` (capped
`MAX_QA_TURNS=3`), `verify_devils_advocate_done`, `phase` (DEBATE `research`/`debate`),
`debate_turns`/`debate_round`/`bull_stance`/`bear_stance`/`debate_consensus`/`debate_conviction`,
`opportunity_tier`, and the Adaptive Opportunity Engine bookkeeping (`watch_cycles`, `session_turns`,
`invalidation_count`, `postmortem_pending`, `prior_thesis`, `heartbeat_count`, `last_resume_kind`,
`best_current_read`).

**Nodes** (`add_node` at `:4645-4651`, `:5330-5339`):

| Node | Function (line) | Purpose |
|---|---|---|
| `agent` | `call_model` (3448) | Main ReAct LLM turn: prepend system prompt, run VERIFY devil's advocate, prune context, bind profile-gated tools, invoke model. |
| `tools` | `tool_node` (3605) | Execute `ok` tool calls; synthetic answers for failed calls; enforce first-turn data-acquisition gate + DEBATE research-phase declaration suppression. |
| `force_hold` | (4056) | Terminal HOLD (`no-decision-reached`) when reasoning budget exhausted. |
| `force_terminal` | (4127) | Terminal `stand_aside` when Watch_Cap / Session_Budget exhausted; answers any pending watch call. |
| `bull` | `bull_node` (4830) | Bull_Agent (read-only tools) over shared evidence. |
| `bear` | `bear_node` (4859) | Bear_Agent rebutting Bull (read-only). |
| `judge` | `judge_node` (5036) | Judge_Agent: classify consensus, derive conviction, **sole committer** in DEBATE (full tools). |
| `qa_agent` | `qa_node` (4448) | Answer QA grounded in `Session_Analysis_Context`; read-only tools; never sets `decision`. |
| `qa_tools` | `qa_tool_node` (4553) | Execute only read-only QA tool calls. |

**Routing:**
- **Entry** (`route_entry` `:4613`, `set_conditional_entry_point` `:5346`): `QA → qa_agent`;
  `DEBATE → research-phase agent`; `FIND/VERIFY → agent`.
- **`agent` →** (`should_continue` `:3964`): pending `watch_price_condition` → `force_terminal` (if
  hunt exhausted) or `suspend` (→ tools, where `interrupt()` pauses); other pending calls → `tools`;
  `decision` set → `end`; budget exhausted → `force_terminal`; reasoning turns left → loop to `agent`;
  reasoning exhausted + DEBATE → `bull`; reasoning exhausted (non-DEBATE) → `force_hold`.
- **`tools` →** (`route_after_tools` `:4193`): `decision` set → `end`; DEBATE debate-phase → `bull`;
  else → `agent`.
- **`bear` →** (`route_debate` `:5298`): another round → `bull`; else → `judge`.
- **`qa_agent` →** (`qa_should_continue` `:4591`): pending tools and `qa_turns < MAX_QA_TURNS` →
  `qa_tools`; else → `end`.
- Unconditional: `bull → bear`, `force_hold → end`, `force_terminal → end`, `judge → end`,
  `qa_tools → qa_agent`.

**Mode → path summary:**
- **FIND**: `agent ↔ tools` ReAct loop → `declare_trade` commits → `end`. Exhaustion → `force_hold`/
  `force_terminal`.
- **VERIFY**: same loop; `call_model` runs a one-shot Bear devil's advocate
  (`_should_run_verify_devils_advocate` `:3475`); system prompt switches to `RISK_MANAGER_PROMPT`
  (`:724`); the verdict path stays sole authority.
- **QA**: entry bypasses `agent` → `qa_agent ↔ qa_tools` bounded sub-loop → `end`. Committed decision
  immutable (R18.6).
- **DEBATE**: entry → `agent` in `phase="research"` (declaration suppressed) → handoff →
  `bull → bear` rounds → `judge` (sole committer) → `end`.

**Profile handling** (`:557-698`): `PROFILE_DIRECTIVES` holds INTRADAY/SWING/INVESTOR blocks; FNO is
built dynamically interpolating symbol + `fno_expiry`. `format_system_prompt` (`:701`) appends the
profile directive after the timeframe requirement; a spot INDEX in a non-FNO profile also gets
`INDEX_OPTIONS_ADDENDUM` (`:722`). Profile also **structurally gates tool binding** in `call_model`:
`FNO_ONLY_TOOLS = {"get_options_analytics"}` (`:866`) is bound only for FNO; DEBATE Bull/Bear get
read-only tools (`DEBATE_READONLY_EXCLUDED_TOOLS = {declare_trade, watch_price_condition}` `:843`);
the Judge gets the full set.

**Checkpointer** (`:5429-5433`): `MemorySaver()` in-memory per-thread; `thread_id` is LangGraph's
configurable. **Session_Analysis_Context** (described `:4216`, built by `build_qa_context` `:4268`)
is the persisted per-thread state — the committed `decision`, its defensibility record, and
accumulated `messages`. QA reuses the same `thread_id` so `qa_agent` answers from persisted context
without re-running analysis (R18.1, R18.5).

### 4.3 `tools.py` (3523 lines) — the `@tool` inventory

`RUST_SERVER_URL = "http://localhost:8084"` (`:89`). Each tool does its own
`httpx.post(f"{RUST_SERVER_URL}/tools/<name>", json=...)` and re-validates via
`validate_contract` (`:323`, never raises — returns a structured `contract_violation` marker). Two
non-Rust I/O paths: `_read_live_ticks` queries QuestDB HTTP directly (`:268`), and
`get_trade_performance` reads the local journal. Tools registered into the graph at `graph.py:814-833`.

**Analysis tools (read-only):**

| Tool | Line | Returns |
|---|---|---|
| `get_candles` | 815 | Raw OHLCV list; distinguishes Availability_Shortfall from Infrastructure_Fault. |
| `get_consensus_report` | 864 | trend_score, momentum/volatility/volume_flow states, active patterns/strategies, full raw indicators. |
| `get_multi_tf_trend` | 899 | 1H/4H/1D EMA-pair trend biases. |
| `get_chart_patterns` | 925 | 19-pattern engine: type, sentiment, confidence, start/end idx, description. |
| `get_support_resistance` | 1047 | pivots, S1-S3, R1-R3, recent high/low; intraday adds opening range + daily pivot. |
| `get_prediction` | 1148 | Rust Predictive_Engine OLS: direction/value/confidence. |
| `get_news_context` | 1107 | headlines + sentiment_summary; honest `Unavailable` marker on failure. |
| `get_market_regime` | 1214 | trend_state/volatility_state/favorability + measures (choppiness, efficiency_ratio, atr_percentile, bb_width). |
| `get_relative_strength` | 1388 | index_direction, relative_strength_state (leader/inline/laggard), alignment, measures (rs_ratio, beta, correlation). |
| `get_forecast` | 2012 | projected_direction, up_probability, expected_move_atr, forecast_confidence. |
| `get_session_context` | 2174 | session_phase (pre_open…post_close), minutes to open/close, expiry_context, time_favorability. |
| `get_event_risk` | 2651 | scheduled-event risk classification (earnings proximity). |
| `get_options_analytics` | 1786 | PCR, max_pain, oi_buildup, oi_walls, iv_skew, futures_basis, `classify_options_bias` → options_bias_state. FNO uses own chain; otherwise benchmark index chain. |
| `get_order_flow` | 1628 | candle proxies (candle_delta, cvd_proxy, buying_pressure_ratio) + live-tick Tick_OFI from QuestDB. |
| `get_volume_profile` | 3411 | POC, VAH/VAL, HVN/LVN, price_vs_value_area (pure-Python). |
| `get_trade_performance` | — | Reads the sqlite Trade_Journal for track-record feedback. |

**Action tools (bound only where the mode permits):**

- `watch_price_condition` (`:2797`) — registers a watcher with the Rust Tool_Server
  (`POST /tools/watch_condition` `:2872`, with retry policy `:95-96` and heartbeat fields from
  `opportunity.resolve_opportunity_config` `:2853`). A 400 from the validator → recoverable
  `{"status":"watch_level_rejected"}` (not a HOLD). On success calls `interrupt({...})` (`:2946`)
  which **suspends** the LangGraph run. On resume, classifies the trigger via
  `opportunity.classify_resume` and returns a trigger-specific string (target / invalidation /
  heartbeat), each naming the minimal `delta_recheck_plan` tool set.
- `declare_trade` (`:3040`) — `declare_trade(action, conviction_score, setup_validation,
  execution_plan, entry?, stop_loss?, take_profit?, atr_14?, management_plan?)`. If
  `management_plan` is present it is parsed via `trade_manager.plan_from_json` and pre-validated in
  Python by `validator.validate_trade` (`:3128`); then POSTs `/tools/declare_trade` (`:3168`) where
  the Rust Trade_Validator is authoritative. A rejection is surfaced as `TRADE_REJECTED: ...` so the
  loop continues and the agent revises.

### 4.4 LLM binding

Construction (`graph.py:737-812`): OpenAI-compatible via `_env_nonempty`: `api_key =
LLM_API_KEY|GEMINI_API_KEY`, `base_url = LLM_API_URL|...`, `model_name = LLM_MODEL|...` (code defaults
Gemini; live `.env` uses `LLM_MODEL=gpt-5.5`). Reasoning effort via `LLM_EFFORT`/`LLM_EFFORT_FIELD`
sent through `extra_body`. Base client: `ChatOpenAI(model=..., temperature=0.2, max_retries=...,
timeout=...)`.

Tool bindings (`:814-873`): `llm_with_tools = llm.bind_tools(tools)` (`:834`); `non_fno_llm_with_tools`
(`:869-873`) excludes `get_options_analytics`; `readonly_llm_with_tools` (`:846-854`) for Bull/Bear/QA
excludes `declare_trade`/`watch_price_condition`; Judge gets the full set.

**Per-request model override** (`RunRequest.model` → `AgentState.model`): `_llm_for_profile(state)`
(`:915`) resolves profile (FNO → full tools) and, when `state["model"]` is non-empty, builds a cached
`ChatOpenAI` with that model via `_build_profile_llm_for_model` (`:883`) / `_build_readonly_llm_for_model`
(`:942`) / `_build_full_llm_for_model` (`:1009`); role LLMs resolve per-role models through
`debate.resolve_debate_config`. Tool-selection gating is **structural** — tools not in the bound set
cannot be called by the model — plus runtime data-gates in `tool_node` (no `declare_trade` before
market data seen; DEBATE-research declaration suppression; unchanged-thesis re-arm suppression).

### 4.5 Supporting modules (pure-Python analysis cores)

- **opportunity.py** — the "Adaptive Opportunity Engine": `evaluate_tier` (`:534`) descends
  `a_plus → b_continuation → scalp → stand_aside`; `watch_cap_reached`/`session_budget_exhausted`/
  `termination_reason` (`:767`); `is_rearm_unchanged` (`:905`) + volatility-floored invalidation re-arm;
  `classify_resume` (`:1072`) + `delta_recheck_plan` (`:1152`) scope the cheap resume re-check;
  `prune_messages` (`:1351`) bounds LLM context; `best_current_read` (`:1460`). Consumed by
  `call_model`, `tool_node`, `should_continue`, `force_terminal`.
- **validator.py** — Python mirror of Rust `validate_trade` (`:337`): MissingLevels →
  DirectionInconsistent → StopTooTight (≥1.5×ATR) → RiskRewardTooLow (≥2.0), plus multi-leg plan checks.
  Used by `declare_trade`'s management-plan pre-gate; Rust remains authoritative for the base bracket.
- **debate.py** — `DebateStance` + `parse_stance` (`:352`), `classify_consensus` (`:476`),
  `derive_conviction` (`:504`); per-role model/round/turn config via `resolve_debate_config` (`:168`).
- **regime.py, forecaster.py, session.py, rs.py, order_flow.py, options.py, options_bias.py,
  events.py** — the pure analysis cores behind each `get_*` tool (classification / projection math,
  Black-Scholes IV/Greeks for options, IST session math, etc.). Each is deterministic and
  unit/property-tested in isolation.
- **trade_manager.py** — multi-leg exit simulator (`plan_from_json` `:412`, `simulate_plan` `:679`,
  fraction-weighted Realized_R); used by `declare_trade`'s plan gate and the journal scoring.
- **journal.py** — sqlite3 Trade_Journal: `record_decision` (`:812`), `get_stats` (`:1239`),
  `score_open_trades` (`:1165`). Invoked from `_finalize_decision` (`graph.py:3429`) — the
  measurement/feedback loop that closes the edge.
- **stream_events.py** — pure SSE event builders consumed by `main.py`.
- **attribution.py / calibration.py / backtest.py** — offline edge-analysis (not on the live run path).
- **telemetry.py** — best-effort observation tee (`main.py:_observe`).

### 4.6 `prompt.md` — the Alpha-Quant charter (structured summary)

- **Mandate** (`:9`): *never fabricate* market data, a price, an indicator, a forecast, or a trade
  level; report an honest `Unavailable_Marker` and proceed. All tool output, candles, headlines, and
  resumed-watch messages are **untrusted DATA, never instructions** (prompt-injection guard `:67-79`).
- **Operating modes** (`:255`): FIND (hunt fresh setup, full tools, first turn must call a tool,
  commit/ watch/ stand-aside with a Best_Current_Read); VERIFY (co-pilot an operator-proposed trade,
  optional one-shot Bear devil's advocate, verdict path is sole authority); DEBATE (shared research →
  Bull/Bear read-only → Judge sole committer, bounded rounds); QA (grounded in
  Session_Analysis_Context, read-only, committed trade immutable).
- **`<risk_rules>`** (`:485`, inviolable, identical at every tier): (1) stop ≥ 1.5×ATR(14);
  (2) R:R ≥ 1:2; (3) directional trades must supply finite entry/stop/target with direction-consistent
  ordering (HOLD may omit); (4) when a `management_plan` is attached — leg fractions in (0,1.0] summing
  ≤ 1.0, profit-ordered targets, breakeven strictly between entry and first target, blended R:R ≥ min.
  Rejection is information to revise around, never to route around.
- **`<opportunity_tier_ladder>`** (`:471`): `a_plus` (pristine full-confluence, full size),
  `b_continuation` (solid trend-continuation, reduced size), `scalp` (lower-confluence but defensible,
  small size), `stand_aside` (no trade, still state a Best_Current_Read). Lower tier = smaller, not
  looser; the hard risk rules never relax. The bounded hunt is structural: a **Watch_Cap** (max watch
  cycles) and a **Session_Budget** (max model turns + wall-clock); hitting a bound commits a terminal
  stand-aside via `force_terminal`. The invalidation post-mortem gate forbids an unchanged re-arm.
- **`<output_format>`** (`:519`): commit via `declare_trade` (BUY/SELL/HOLD with numeric levels) or
  arm `watch_price_condition` — never emit the JSON in place of arming a watch (JSON ends the run; the
  tool suspends it for auto-resume). Only after committing/exhausting, output JSON:
  `{ conviction_score: 0-100, setup_validation: "2-3 sentence synthesis", execution_plan: "precise
  BUY/SELL/HOLD entry/SL/TP or wait instructions" }`. This is the `AiExecutionPlan` / `declare_trade`
  argument shape.
- **`<order_of_operations>`** (`:284`): macro → microstructure → regime → RS → session → options →
  S/R → volume profile → patterns → price action → forecast/prediction → news → track-record.

---

## 5. End-to-end: the full lifecycle of one FIND run

**1. User clicks Find Quant Trade.** `DeepQuantPanel` calls `useQuantStore.fetchDeepAnalysis`, which
`tauriInvoke('run_deep_quant_agent', { symbol, mode:"FIND", timeframe, profile, fnoExpiry, model,
manualTrade })`.

**2. Tauri bridges to Python.** `commands/deep_quant.rs::run_deep_quant_agent` (`:2169`) POSTs to
`http://localhost:8086/run` with `thread_id = thread_{symbol}_{ts_millis}` and proxies the SSE stream
onto the `deep-quant-stream` Tauri event.

**3. Python starts the graph.** `main.py::run_agent` builds `initial_state` (messages = the user
message; mode/symbol/profile/timeframe/model threaded in), emits `RUN_STARTED`, and
`await graph.astream(target_input, config={"configurable":{"thread_id":...}}, stream_mode="updates")`.

**4. Entry → `agent` node.** `route_entry` maps `FIND → "agent"`. `call_model` prepends
`format_system_prompt` (charter + timeframe requirement + profile directive + index addendum), prunes
context, and invokes `_llm_for_profile(state).invoke(messages)` — the profile-gated, tool-bound LLM.

**5. ReAct loop.** The LLM emits structured tool calls; `should_continue` routes pending calls to the
`tools` node. `tool_node` executes each `ok` call: the tool `httpx.post`s to `localhost:8084/tools/<name>`,
the Rust server loads candles from QuestDB / reads `live_ticks` / hits the sentiment service, and
returns a dict/list/honest-marker. The `ToolMessage` comes back; `route_after_tools` loops to `agent`;
the LLM reasons over the new results and calls the next tool, walking the order of operations
(macro→microstructure→…→news→track-record).

**6. Commit.** When the agent decides, it calls `declare_trade(...)`. If a `management_plan` is
present, Python pre-validates it (`validator.validate_trade`); then it POSTs `/tools/declare_trade`
where the **Rust Trade_Validator** is authoritative (stop ≥1.5×ATR, R:R ≥1:2). On rejection the
decision is left unset and the loop continues (the agent revises). On a valid commit,
`_finalize_decision` (`graph.py:3361`) stamps the Opportunity_Tier, builds the defensibility record,
journals the decision (`journal.record_decision`), and sets `state["decision"]`. `should_continue`
sees `decision` → routes `end`; the SSE stream emits a `DECISION` event and a terminal `RUN_FINISHED`
with `status="completed"`.

**7. Frontend renders.** `deep-quant-stream` → `useQuantStore.handleStreamEvent` → `applyStreamEvent`
updates `reasoningSteps`, `sessionStatus`, `finalTrade`, `aiPlan`, `currentThreadId`. `AgentTerminal`
streams the reasoning/tool cards live; on `DECISION` + `RUN_FINISHED`, `AiExecutionPlanView` shows
the conviction/setup/plan and (if `isActionableTrade`) an "Approve & Execute" button that deploys a
**paper trade** via `invoke('execute_paper_trade')`.

### The watcher flow (interrupt + resume)

If no A+ setup exists yet, the agent calls `watch_price_condition(...)`. `should_continue` routes
`"suspend"` (or `force_terminal` if the bounded hunt is exhausted). `tool_node` runs the call: it
registers the watcher with the Rust Tool_Server (`POST /tools/watch_condition`) and then calls
`interrupt({...})` — **LangGraph pauses the run**, `state.next` is non-empty, so `event_generator`
emits `RUN_FINISHED` with `status="paused"`. The MemorySaver checkpointer persists the paused state
under `thread_id`. In the UI, `TOOL_CALL_START` with `watch_price_condition` sets `sessionStatus =
"watching"` → `AgentTerminal` shows `<WatchingIndicator>`, and `TradeQaPanel` unlocks a "Watching —
chat live" badge so the user can ask questions while the AI watches.

Later, the Rust Tool_Server's watcher (subscribed to the live-candle broadcast channel) fires when
the target/invalidation/heartbeat condition is met, and POSTs `:8086/resume` with
`{thread_id, triggered_candle, trigger_kind}`. `resume_agent` verifies the thread is paused and
resumes with `Command(resume={...})`. LangGraph delivers that dict as the return of `interrupt()`;
`watch_price_condition` classifies the trigger and returns a trigger-specific string naming the
minimal `delta_recheck_plan` tool set. `tool_node` also performs resume bookkeeping (invalidation →
arms the post-mortem; heartbeat → charges the Session_Budget). Control returns to `agent`, which
re-invokes the LLM over the new triggered-candle + delta-recheck results — a fresh reasoning step
that either commits via `declare_trade`, re-arms a *genuinely different* watch, or stands aside. The
cycle repeats until a decision is committed, a bound fires (`force_terminal`), or reasoning exhausts
(`force_hold`).

### The Trade_QA chat flow

After (or during) a run, the user types a follow-up in `TradeQaPanel`. `useQuantStore.askQuestion`
requires `currentThreadId` (captured from the run's `RUN_STARTED`), pushes a user turn + a streaming
assistant turn, `listen('deep-quant-qa-stream')`, then `tauriInvoke('ask_trade_question', { threadId,
question, model })`. The Tauri command POSTs `:8086/qa`; Python's `qa_agent` answers grounded in the
**persisted Session_Analysis_Context** (same `thread_id` + MemorySaver — no re-analysis), with
read-only tools only, never mutating the committed trade. The streamed answer renders in `QaMessages`
inside the AgentTerminal transcript.

---

## 6. Chat-box session persistence — current state

This section answers "what survives a reload / restart, and what doesn't" for the Deep Quant chat
session. There are **three independent persistence layers**, and they cover **different things** —
which is the key gotcha for any enhancement work.

### 6.1 What exists today

**Layer 1 — In-process, in-memory (`useQuantStore.sessionsByKey`).** The only thing that survives
*switching the symbol/profile and coming back* within a running app session. `QuantSession`
(`useQuantStore.ts:558`) holds `sessionStatus`, `reasoningSteps` (the full transcript), `finalTrade`,
`aiPlan`, `analysisError`, `currentThreadId`, `qaMessages`, `qaStatus`, `mode`. Keyed by
`_sessionKey = ${SYMBOL}::${PROFILE}` (`:621`) — so INTRADAY-RELIANCE and SWING-RELIANCE are distinct.
`activateSymbolSession` (`:1136`) snapshots the outgoing session and `projectSession`s the target
into the flat fields the UI reads. `handleStreamEvent` (`:1175`) routes each SSE event to the right
session via `_threadToKey` (thread id → session key), so a background run for symbol A keeps
accumulating while you view B. **Nothing here is written to disk** — it dies when the Tauri window /
renderer process is closed or reloaded.

**Layer 2 — The Python `MemorySaver` checkpointer (`graph.py:5430`).** The *reasoning context* the
agent reasons over — the `messages` list, the committed `decision`, the
`Session_Analysis_Context` (built by `build_qa_context` `:4268`). This is what makes **Trade_QA**
work without re-running analysis: `ask_trade_question` reuses the same `thread_id` + MemorySaver, so
`qa_node` answers from the persisted context (`R18.1/R18.5`). It's also what makes **`/resume`**
work after a `watch_price_condition` interrupt. It is **in-memory in the Python process** — it dies
when the `:8086` FastAPI service restarts. There is no Redis/postgres checkpoint backend wired.

**Layer 3 — The Tauri SQLite `workspace.db` (`db.rs`).** The *only disk-durable* store. Schema:
`workspaces(symbol TEXT PK, state_json TEXT)` and `trades(...)`. Commands `save_workspace` /
`load_workspace` (`db.rs:120/141`) are registered in `lib.rs:347-348`. **But the Deep Quant chat
session does not use them.** `save_workspace`/`load_workspace` are invoked only by:
- `charting/workspace.ts:181/224` — chart layout (drawings, indicator settings)
- `useTradeStore.ts:215/244` — the `__WATCHLIST__` watchlist blob
- `useRadarStore.ts:86/301` — the `__QUANT_RADAR__` radar blob

`useQuantStore` never calls `save_workspace`/`load_workspace`. The `trades` table is written only by
`log_completed_trade` (`:167`), invoked from `useQuantStore` when a **paper position is closed**
(`:1499`) — i.e. realized PnL history, not chat history.

So, concretely:

| Artifact | Survives symbol switch | Survives window reload | Survives app restart | Survives `:8086` restart |
|---|---|---|---|---|
| Reasoning transcript (`reasoningSteps`) | ✅ Layer 1 | ❌ | ❌ | ❌ |
| Q&A chat (`qaMessages`) | ✅ Layer 1 | ❌ | ❌ | ❌ |
| `currentThreadId` | ✅ Layer 1 | ❌ | ❌ | ❌ |
| Agent reasoning context (`messages`, `decision`) | — | — | — | ✅ Layer 2 (until `:8086` dies) |
| Final plan / conviction (`aiPlan`, `finalTrade`) | ✅ Layer 1 | ❌ | ❌ | ❌ |
| Chart layout / watchlist / radar | ✅ | ✅ Layer 3 | ✅ Layer 3 | — |
| Realized paper-trade history | ✅ | ✅ Layer 3 | ✅ Layer 3 | — |

**The headline gap:** the Deep Quant chat session (transcript + Q&A + the `thread_id` that links to
the Python context) is **in-memory only**. Close and reopen the app, or restart the Python service,
and the conversation is gone — even though the *realized trades* and the *chart drawings* from that
same session persist fine.

### 6.2 Enhancement options (ranked, with the exact seam each touches)

These are options to discuss, not committed changes. Each is sized so it can land independently.

1. **Persist the `QuantSession` blob to `workspace.db`** (highest leverage, smallest change).
   Reuse the existing `save_workspace`/`load_workspace` IPC — no new schema, no new command. On
   `RUN_FINISHED` (and on `activateSymbolSession` snapshot) in `useQuantStore`, debounce-write
   `sessionsByKey[key]` under a key like `__DEEPQUANT__${SYMBOL}::${PROFILE}`; on app boot, hydrate
   `sessionsByKey` from `load_workspace` for each known symbol. Restores transcript + `aiPlan` +
   `currentThreadId` across restarts. **Caveat to call out:** the persisted `currentThreadId` is
   only useful while the Python `MemorySaver` still has that thread — if `:8086` restarted, the
   thread is gone and Q&A/resume will 400. Either (a) persist only display state (transcript + plan)
   and mark Q&A/resume unavailable, or (b) also add a real checkpointer backend (option 3).

2. **Persist Q&A turns separately so they survive even when the run's transcript is large.** The
   `qaMessages` array is already a clean `QuantSession` field; writing it to its own SQLite table
   (`qa_history(thread_id, role, content, activity_json, ts)`) gives a durable, queryable chat log
   independent of the run lifecycle. New `db.rs` commands + `useQuantStore` hydrate on
   `activateSymbolSession`. This is the option that most directly matches "chat box state
   persistence" as a user-facing feature.

3. **Give the Python agent a durable checkpointer** (largest, fixes the root cause). Swap
   `MemorySaver()` (`graph.py:5430`) for a `langgraph-checkpoint-postgres` (or sqlite) backend pointed
   at the existing QuestDB PG wire / a small sqlite file. Then `currentThreadId` survives `:8086`
   restarts, Q&A and `/resume` keep working, and option 1's `currentThreadId` becomes durable too.
   This is the only option that makes the *agent's memory* durable — the other two only persist UI
   state.

4. **Thread-id reattachment on resume.** Regardless of backend, on `activateSymbolSession` /
   app boot, if a persisted session has a `currentThreadId`, ping `:8086/qa` with a no-op probe (or
   a new `/thread/exists` endpoint) to decide whether to show the Q&A composer as live or
   "session archived — rerun to continue chatting." Prevents the silent-400 failure mode.

### 6.3 Constraints to preserve during any persistence work

- **Per-symbol/per-profile isolation must not collapse.** The `SYMBOL::PROFILE` key is load-bearing
  (`_sessionKey` `:621`); any persisted key must include profile, or INTRADAY/SWING/INVESTOR/FNO
  sessions for the same symbol will clobber each other.
- **`_threadToKey` routing must stay correct under async hydration.** Events arriving during boot
  hydration (before `sessionsByKey` is populated) must still route by `thread_id`; the existing
  fallback chain (`_threadToKey` → `_streamingKey` → `activeViewKey`, `handleStreamEvent` `:1183`)
  must not be bypassed.
- **Never persist `isAnalyzing`/`sessionStatus === 'running'` as truth.** A run that was live when the
  app closed is dead after restart; hydrate as `error`/`idle` (or a new `stale` status), not
  `running`, or the UI will spin forever waiting for a `RUN_FINISHED` that will never come.
- **Don't double-write the paper-trade journal.** `log_completed_trade` is the durable trade record;
  chat persistence is a separate concern and should not duplicate trade rows.

---

## 7. Fixes & improvements — prioritized list

Each item: **what's wrong** → **where** (file:line) → **fix** → **size**. Sized S/M/L so you can
pick. Verified against the code unless noted.

### P0 — Security (do these now)

**1. `bedrock-api-key.txt` is committed to git with a live key.** `S`
- *Where:* repo root `bedrock-api-key.txt` (added in commit `4aeceb2`, currently **tracked**; the
  `.gitignore` entry `bedrock-api-key.txt` is being ignored — `git check-ignore` returns exit 1).
- *What it contains:* `bedrock-api-key-YmVkcm9jay5hbWF6b25hd3MuY29tLz9BY3Rpb249Q2FsbFdpdGhCZWFyZXJUb2tl…`
  — a real AWS Bedrock bearer token, base64-embedded.
- *Fix:* `git rm --cached bedrock-api-key.txt`, rotate the key in AWS IAM (it must be treated as
  compromised — it's in public history), move it into the Stronghold vault (`security::save_api_key`)
  or `.env`, and force-add the `.gitignore` line (`git check-ignore` shows the pattern isn't matching
  the tracked file — re-add the entry explicitly and confirm `git check-ignore` returns 0 for the
  *path*). **The leak is already in history**, so rotation is mandatory, not optional.
- *Also:* verify `.env` (which contains a live `LLM_API_KEY=fe_oa_...`) stays untracked — it
  currently is (`git check-ignore .env` = 0), good.

**2. LLM API key resolution tries three hardcoded vault aliases** (`llm_key` → `hf_key` → `deepseek`)
   before env. `S`
- *Where:* `services/llm.rs:460-484` (the `get_api_key_from_vault(handle, "llm_key")` → fallback
  `"hf_key"` → fallback `"deepseek"` chain).
- *What's wrong:* the fallback aliases `hf_key` / `deepseek` are magic strings; a user who stored a
  key under one alias silently gets a different provider's key if the first lookup misses. Obscure
  failure mode, but real.
- *Fix:* resolve only the configured alias (`LLM_KEY_ALIAS`, default `llm_key`); drop the magic
  fallbacks, or at least log loudly when you fall through to env so it's never silent.

### P0 — Correctness / data integrity

**3. The Deep Quant UI chat session is in-memory only and is lost on restart.** `M` — covered in
   detail in §6. Close/reopen the app (or restart `:8086`) and the transcript, Q&A, and `currentThreadId`
   are gone — even though the realized trades and chart drawings from the same session persist.
- *Fix:* §6.2 option 1 (persist `QuantSession` via the existing `save_workspace`/`load_workspace` to
  `workspace.db`) for the quick win; option 3 (durable LangGraph checkpointer) for the root-cause
  fix that also makes Q&A/resume survive `:8086` restarts.

**4. A run that was live when the app closed will spin forever after restart.** `S` (subsumed by #3)
- *Where:* if you persist `QuantSession` naively, `sessionStatus: 'running'` / `isAnalyzing: true`
  hydrate as-is. The UI waits for a `RUN_FINISHED` that never comes.
- *Fix:* §6.3 — hydrate any persisted `running`/`watching` state as `idle`/`error`/`stale`, never
  `running`.

**5. Python agent bypasses the Tool Server "single source of truth" for two data paths.** `M`
- *Where:* `tools.py:268` `QUESTDB_HTTP_URL` + `_read_live_ticks` (`:1573`, hits QuestDB HTTP `/exec`
  directly) used by `get_order_flow` (`:1714`); `options.py:60/1192/1224` reads
  `option_chain_snapshots`/`option_ticks` directly from QuestDB HTTP.
- *What's wrong:* §6 of the architecture claims the Rust Tool Server (`:8084`) is the *only* data
  substrate. Two tools violate that — they read QuestDB directly, so they bypass the Rust candle
  loader's backfill/dedup logic and the audit logger. If QuestDB HTTP port or schema differs from
  what the Rust core uses, these tools see different data than everything else.
- *Fix:* expose `/tools/get_order_flow` and `/tools/get_options_snapshot` on the Rust Tool Server
  (the order-flow OFI math already lives in Rust: `compute_order_flow_imbalance` in
  `deep_quant.rs:1112`; the option-chain math lives in `services/option_chain.rs`). Move the Python
  direct reads to call those endpoints. Keeps the "single source of truth" claim honest and removes
  a second QuestDB client config to keep in sync.

**6. Triple-duplicated Google News RSS scraping logic.** `S`
- *Where:* `commands/sentiment.rs:90` `fetch_google_news_rss`, `commands/deep_quant.rs:147`
  `fetch_google_news_rss_for_context` (comment at `:145` literally says "Mirrors the implementation
  in commands/sentiment.rs"), and the legacy glass-box loop also calls `fetch_news_context` (`:1515`).
- *Fix:* extract one `pub(crate) fn fetch_google_news_rss(...)` in a shared `services/news.rs` and call
  it from both `sentiment.rs` and `deep_quant.rs`. Removes ~80 lines of duplicated XML-entity
  decoding and keeps the headline list consistent across the sentiment panel and the agent.

### P1 — Reliability / robustness

**7. `thread_id` is generated in Tauri with a millisecond timestamp — collisions and non-resumability.** `S`
- *Where:* `deep_quant.rs:2191` `format!("thread_{}_{}", symbol, chrono::Utc::now().timestamp_millis())`.
- *What's wrong:* two rapid clicks on Find for the same symbol in the same millisecond produce the
  same `thread_id`, so the second run clobbers the first's MemorySaver state. Also, the `thread_id`
  is generated *per invoke* and not persisted, so even with §6.2 option 3 the UI can't reattach.
- *Fix:* use a UUID (`Uuid::new_v4().to_string()`), and let the UI pass a stable `thread_id` it
  generated and persisted with the session (so Q&A/resume can reuse it across reloads).

**8. No CORS / no auth on the Python `:8086` service — it binds `0.0.0.0`.** `S`
- *Where:* `main.py:496` `uvicorn.run("main:app", host="0.0.0.0", port=8086)`; no CORS middleware, no
  auth middleware (confirmed: only `FastAPI(title=...)` at `:52`).
- *What's wrong:* any process on the machine (or the network, depending on firewall) can POST `/run`
  / `/resume` / `/qa` and drive the LLM — burning tokens / issuing trades through `declare_trade`.
  The Rust tool server's `/tools/declare_trade` gate still validates, but the LLM cost and the
  `/resume` surface are unprotected.
- *Fix:* bind `127.0.0.1` (the Tauri proxy is local anyway; the `0.0.0.0` binding is unnecessary),
  and add a shared-secret header check (`DEEP_QUANT_INTERNAL_TOKEN`) that the Tauri proxy sets and
  the service validates.

**9. The Rust Tool Server has no auth either and is reachable on `127.0.0.1:8084`.** `S`
- *Where:* `tool_server.rs:1913` `let addr = "127.0.0.1:8084";` — no token check on any `/tools/*`
  route, including `/tools/declare_trade` and `/tools/watch_condition`.
- *Fix:* same shared-secret header as #8; both sides already share the `.env`. `declare_trade`
  remains the last gate, but defense-in-depth matters because a `/tools/watch_condition` call can
  arm a watcher that later POSTs `/resume`.

**10. `print()` used as logging throughout the Python agent (110 in tools.py, 70 in graph.py).** `S`
- *Where:* `tools.py` (110 `print()`), `graph.py` (70), `main.py` (3).
- *What's wrong:* no log levels, no structured output, interleaves with stdout (which FastAPI may
  use for other things), can't be filtered or routed. The Rust side uses `log::info!`; the Python
  side should match.
- *Fix:* `import logging; log = logging.getLogger("deep_quant")` and replace `print(...)` with
  `log.info/debug/warning`. One mechanical sweep per file.

**11. The watcher registry is in-memory in the Tauri process — watchers die on app restart.** `M`
- *Where:* `tool_server.rs:113` `pub watchers: Arc<RwLock<HashMap<String, Watcher>>>` (`:1898`).
- *What's wrong:* if the agent arms `watch_price_condition` and the user closes/reopens the app, the
  watcher is gone — the level can trigger and nothing resumes the thread. The Python `MemorySaver`
  state (Layer 2 in §6) is also gone if `:8086` restarted, but even if `:8086` lived, the Rust side
  forgot it was watching.
- *Fix:* persist active watchers to `workspace.db` (new `watchers` table) and re-arm them on
  `run_tool_server` startup; or persist the watcher intent in the `QuantSession` and re-register on
  app boot. Pairs with §6.2 option 3 (durable checkpointer) — without both, resume after restart is
  half-broken.

**12. `MemorySaver` checkpointer means a `:8086` restart loses all in-flight reasoning + every Q&A
    thread.** `M`
- *Where:* `graph.py:5430` `memory = MemorySaver()`.
- *What's wrong:* §6 Layer 2 — the agent's memory is process-in-memory. Restart the Python service
  (crash, deploy, machine reboot) and *every* thread's context is gone, so all Q&A and pending
  watches break silently.
- *Fix:* §6.2 option 3 — `langgraph-checkpoint-postgres` (or sqlite) backing the existing
  `QUESTDB_POSTGRES_URL` or a small sqlite file. Largest single reliability win for the feature.

### P1 — Consistency / dead code

**13. The legacy Rust glass-box path (`run_ai_analysis` / `run_deep_quant_analysis`) is dead UI code
     but still wired and ~4560 lines.** `M`
- *Where:* `commands/deep_quant.rs:1247` `run_ai_analysis`, `:1267` `run_deep_quant_analysis`,
  `run_glass_box_loop` (`:1311-1923`), all the `llm::generate_autonomous_step` machinery — registered
  in `lib.rs:322-323`, but **no UI component invokes them** (Grep over `frontend/src` finds only
  `run_deep_quant_agent` / `ask_trade_question` / `execute_paper_trade`). The Path-A store bridge in
  `useTradeStore.ts:273-306` (`agent_message`/`final_analysis_ready`/`agentChatLog`/`finalTradePlan`)
  also feeds nothing rendered.
- *What's wrong:* ~2000 lines of duplicate agent logic (its own prompt, its own agentic loop, its
  own news fetch, its own plan parser) that drifts from the Python agent's behavior and
  `prompt.md` charter. Two sources of "how a deep quant trade is found" is a maintenance hazard.
- *Fix:* decide — either (a) delete the legacy path (commands, `run_glass_box_loop`, the store
  bridge, the `llm::generate_autonomous_step`/`generate_deep_quant_plan_with_url` surface that only
  it uses), keeping `fetch_news_context`/`get_kite_credentials` if shared; or (b) wire it back in as
  an explicit "offline / no-Python-service" fallback with a UI toggle, and test it. Don't leave it
  dormant-but-registered.

**14. `LoadingState` is imported and `agent_status` is listened to in `DeepQuantPanel` but never
     rendered.** `S`
- *Where:* `DeepQuantPanel.tsx:16` (import), `:148-160` (listener → local `agentStatus` state); the
  return tree renders `AgentTerminal`/`ErrorState`/`AiExecutionPlanView`/`EmptyState` instead.
- *Fix:* either render `<LoadingState>` during `running` (it already cycles 6 phases + shows
  `agentStatus`), or delete the import + listener. Currently it's dead state.

### P2 — Polish / DX

**15. `agent_status` events from the legacy path can still fire during a Python-agent run and pollute
     the local state.** `S`
- *Where:* `DeepQuantPanel.tsx:148-160` listens to the global `agent_status` string; the legacy
  `run_glass_box_loop` emits `agent_status` (`deep_quant.rs:1725`). If both paths ever run, the
  statuses interleave.
- *Fix:* namespace the events (`deep-quant-stream` vs `legacy-agent-status`) or just remove the
  listener once #13 is resolved.

**16. Conviction scoring thresholds differ between the Rust glass-box prompt and the Python charter.** `S`
- *Where:* Rust `AiExecutionPlanView.tsx:26-43` colors HIGH ≥80 / MODERATE ≥60 / LOW ≥40; the Python
  `prompt.md` opportunity tiers (`a_plus`/`b_continuation`/`scalp`/`stand_aside`) don't map 1:1 to
  those conviction bands.
- *Fix:* document the intended mapping (e.g. `a_plus` → ≥80, `b_continuation` → 60-79, `scalp` →
  40-59) in `prompt.md` and/or have the agent emit the `opportunity_tier` alongside
  `conviction_score` so the UI colors by tier, not an arbitrary number.

**17. No graceful handling when the Python `:8086` service is down at run time.** `S`
- *Where:* `deep_quant.rs:2230` POST to `:8086/run` — on connection refused the SSE proxy emits an
  error event; `AgentTerminal.tsx:294` shows "Connection refused: Python service port :8086
  unreachable." but there's no retry or "start the service" affordance.
- *Fix:* a "Service offline — retry / show setup instructions" state in `DeepQuantPanel`/`ErrorState`
  with a one-click check (hit `:8086/health` — note: no health endpoint exists today, add one in
  `main.py`).

**18. No `/health` endpoint on the Python service.** `S`
- *Where:* `main.py` — endpoints are `/run`, `/resume`, `/qa`, `/options/snapshot` only.
- *Fix:* add `GET /health` returning `{ok: true, model: <resolved>, checkpointer: <type>}` so the
  UI (and #17's retry) can distinguish "down" from "misconfigured."

**19. Tool-call timeout mismatch.** `S`
- *Where:* Python tool `httpx` calls use 10–15s timeouts (`tools.py`); the Rust tool server's
  QuestDB reads have no explicit timeout and the candle loader's Kite backfill can take seconds;
  LLM calls are 90s (`graph.py:811`).
- *Fix:* align — give the Rust tool server a configurable per-request timeout, and have the Python
  tools retry once on timeout rather than immediately returning an `unavailable` marker (which the
  agent then treats as "data missing" when it was actually "data slow").

**20. No structured error code from the Python agent on `declare_trade` rejection.** `S`
- *Where:* `tools.py:3040` `declare_trade` returns a `TRADE_REJECTED: ...` *string*; the agent parses
  the reason out of prose.
- *Fix:* return `{status: "rejected", reason_tag: "RiskRewardTooLow", detail: "..."}` (the Rust
  `ValidatorReason::as_tag()` already produces clean tags — `tool_server.rs:1271` area), so the
  agent's revise loop and the UI can branch on the tag instead of string-matching.

---

## 8. Key architectural properties

1. **Single source of truth.** The Rust Tool Server (`:8084`) is the only data substrate the agent
   reads. Both the Tauri UI commands and the Python agent call the same candle loader, so they see
   identical data. The agent never computes prices/indicators itself — it consumes authoritative tool
   output.
2. **Honesty over fabrication.** Every unavailable input (missing candles, dead news classifier, no
   live order-book) surfaces as an explicit `Unavailable_Marker` / `null` / "N/A — unavailable". The
   LLM is told never to guess numeric indicators. A failed plan-parse yields a low-conviction HOLD
   diagnostic, never a fabricated winning plan.
3. **Risk is enforced, not advised.** A trade is committed (`final_analysis_ready` +
   `agent-declared-trade`) **only** if it passes the hard validator (stop ≥1.5×ATR, R:R ≥1:2,
   direction-consistent ordering). The agent cannot route around a rejection — it must revise.
4. **Bounded hunt.** The "deep" in deep profitable trades comes from structural patience: a Watch_Cap
   and Session_Budget bound the hunt; `watch_price_condition` suspends the run and the Rust server
   auto-resumes on a live trigger; an unchanged-thesis re-arm after an invalidation is forbidden;
   exhaustion commits a terminal `stand_aside` with a Best_Current_Read rather than a forced trade.
5. **Tiered sizing, not tiered risk.** `a_plus`/`b_continuation`/`scalp` scale position size; the hard
   risk rules are identical at every tier. A lower tier is smaller, not looser.
6. **Glass-box streaming.** Every step is an SSE event (`RUN_STARTED` → `REASONING`/`TOOL_CALL_*`/
   `BEST_CURRENT_READ`/`VERIFICATION_STEP`/`DECISION` → `RUN_FINISHED`), surfaced verbatim in the UI
   transcript. The user sees *which* tools the agent called and *what* it concluded.
7. **Per-session isolation + resumable threads.** Sessions are keyed `${SYMBOL}::${PROFILE}` and
   `thread_id` is mapped to the session key, so concurrent runs and later `/resume` calls route
   correctly. The MemorySaver checkpointer makes QA and resume work without re-running analysis.
8. **Two execution paths coexist; one is live.** The legacy Rust glass-box loop
   (`run_ai_analysis`/`run_deep_quant_analysis`) is a complete in-process agent but is dormant in the
   current UI; the live path is the Python LangGraph proxy (`run_deep_quant_agent`). The Rust loop
   remains as a fallback and shares the same tool server, consensus engine, and validator.

---

## 9. File reference index

**Frontend (`frontend/src/`)**
- `app/page.tsx:17,548-572` — panel mount; `:168` `quant-consensus` listener.
- `components/quant/DeepQuantPanel.tsx:65-88,178-201,246-287,384-408` — trigger, events, render tree.
- `components/quant/AgentTerminal.tsx:101-234,237,305-329,341-404` — transcript, watching, execute card.
- `components/quant/deep-quant/QaMessages.tsx`, `VerificationForm.tsx`, `AiExecutionPlanView.tsx`,
  `useVerificationForm.ts`, `ModelSelector.tsx`, `WatchingIndicator.tsx` — session UI kit.
- `store/useQuantStore.ts:631-800,989-1122,1175-1221,1243-1388` — reducer, fetchDeepAnalysis,
  handleStreamEvent, askQuestion; `:73-88` isActionableTrade.
- `store/useTradeStore.ts:273-306,130-147,189-192` — symbol/timeframe/profile, legacy Path-A bridge.

**Rust core (`frontend/src-tauri/src/`)**
- `commands/deep_quant.rs:1267,1247,2169,2344,2487,1991,1311-1923,1925,2230,2366` — IPC commands + glass-box loop.
- `services/llm.rs:190-249,269-279,460-484,126-162` — master prompt, key resolution, tool schema.
- `quant/tool_server.rs:1913,1901-1911,627,1221,523-623,414-454` — endpoints, watcher, declare_trade gate.
- `quant/mod.rs:21-49,1201-1246,1263,605,583,587` — indicators, ConsensusReport, AiExecutionPlan, validator.
- `services/live_bridges.rs:51-54,161-164` — WS→IPC bridges feeding the watcher.
- `lib.rs:82-120,125-180,262,267,315-350` — env, state, tool server start, pool, command registration.

**Python agent (`agents/deep-quant-loop/`)**
- `main.py:176,203,234,115-172,496` — endpoints, SSE ordering, port.
- `graph.py:201,4645-4651,5330-5339,4613,3964,4193,5298,4591,5429-5433,737-812,814-873,3448,3605,4056,4127,4448,4830,4859,5036,701,557-698` — state, nodes, routing, checkpointer, LLM binding, profile directives.
- `tools.py:89,323,815-1145,1214-2012,2797,3040,2946,2872,3168,3128` — tools, RUST_SERVER_URL, watch + declare.
- `opportunity.py:534,767,905,1045-1047,1152,1351`; `validator.py:337`; `debate.py:168,352,476,504`;
  `validator.py`, `regime.py`, `forecaster.py`, `session.py`, `rs.py`, `order_flow.py`, `options.py`,
  `options_bias.py`, `trade_manager.py`, `journal.py`, `stream_events.py` — pure analysis cores.
- `prompt.md:9,67-79,255,284,471,485,519` — mandate, injection guard, modes, order of operations, tier ladder, risk rules, output format.

**Config / topology**
- `.env.example`: `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_EFFORT`, `QUESTDB_POSTGRES_URL`,
  `KAFKA_BROKER_URL`, `REDIS_URL`, ports `8080/8085/8087`.
- `.env` (live): `LLM_API_URL=https://api.freemodel.dev/v1/chat/completions`, `LLM_MODEL=gpt-5.5`,
  `LLM_EFFORT=high`.
- `docker-compose.yml`: redpanda `:19092`, questdb `:9000/:8812/:9009`, redis `:6379`, ingestion,
  alpha-terminal `:8081`, aggregator `:8080`, predictive `:8082`, quant-rag `:8083`.
- `frontend/src-tauri/tauri.conf.json` — "Strat Ai" v2.0.0, `strat://` deep-link, CSP allowing
  `ws://127.0.0.1:*`, `http://127.0.0.1:*`, NVIDIA + Kite endpoints.
