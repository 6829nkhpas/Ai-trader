# Strat AI — Complete Feature Catalogue

**Version 1.0 · August 2026 · Verified against commit `ccf29b5`**

Every feature below was read out of the source, not from a marketing brief. Where the existing
docs, the README or the agent's own system prompt disagree with the code, **the code wins and the
divergence is flagged**. Section 11 lists every divergence in one table.

---

## How to read this document

| Marker | Meaning |
| --- | --- |
| ✅ | Verified in code, with file and line references |
| ⚠️ | Built but inert, incomplete, or duplicated across layers |
| ❌ | Claimed in docs, **not** present in this repository |
| 🔒 | Regulated surface — gated to the RESEARCH SKU (see `business/COMPANY_REGISTRATION_PLAN.md`) |

**Compute layers.** Strat AI is not one engine. Analytics live in four places, and they are not
always in sync:

1. **Rust `quant-core/`** — candle indicators, consensus scoring, chart patterns, pivot S/R, risk validators, VWEPR/OLS projections. Consumed by `tool-server`.
2. **Rust `aggregator/src/quant/`** — a near-verbatim **copy** of the quant-core indicator matrix for the streaming fusion path. Two sources of truth for the same numbers.
3. **Python `agents/deep-quant-loop/`** — the analytics the LLM calls as tools: regime, order flow, relative strength, session, events, options, volume profile, validator mirror.
4. **TypeScript `frontend/src/charting/engines/`** — browser indicator registry, footprint engine, volume profile engine, and a ported regime classifier.

---

## Top-line corrections to the existing narrative

Three claims in circulation are wrong, and two of them understate the product.

**There is not one Ghost Line. There are eight distinct projection engines.** Four are
user-selectable in the browser, one is an ML-signal interpolator, three live in Rust, and one is a
Python probabilistic forecaster. They use genuinely different mathematics, different windows and
different confidence scales. Section 3 documents each.

**The pattern scanner emits 26 completed pattern labels across 5 categories, not 19 across 3.** The
agent's own system prompt (`graph.py:456-459`) tells the model there are 19 in three categories,
which means **the LLM is instructed to disregard the 5 harmonic and 3 institutional patterns the
engine actually returns**. That is a live defect, not a documentation nit.

**The conviction fusion has five rules, not one.** The documented 70/30 technical/sentiment blend is
correct but incomplete: there is a conviction override that *inverts* the weights to 30/70, and the
conflict-forced HOLD is **asymmetric** — it fires only on bearish-technical-against-bullish-news, not
the mirror case. Section 6 gives the full algorithm.
---

## 1. Market data pipeline

### 1.1 Binary tick ingestion ✅

`ingestion/src/parser.rs` decodes the Zerodha Kite WebSocket binary frames directly — no JSON, no
REST polling.

**Frame layout:** 2-byte big-endian packet count, then per packet a 2-byte length followed by the
body. **Mode is inferred from packet length, not a flag:** `MODE_LTP = 8`, `MODE_QUOTE = 44`,
`MODE_FULL = 184`.

**Field offsets** (big-endian, prices in paise ÷ 100): token u32 at 0–3 · LTP i32 at 4–7 · volume at
16–19 · buy/sell quantity at 20–27 · OHLC at 28–43 · open interest at 48 (Full mode only).

**Five-level market depth** (Full mode): `DEPTH_BID_OFFSET = 64`, `DEPTH_ASK_OFFSET = 124`,
`DEPTH_ENTRY_LEN = 12`, `DEPTH_LEVELS = 5`.

The parser carries an unusually good piece of engineering history. The depth offsets were previously
BID=84 / ASK=124 / ENTRY_LEN=10, which is arithmetically impossible — with ASK=124 and 5 asks filling
184 bytes an entry must be `(184−124)/5 = 12`, so bids must start at `124−60 = 64`. Reading at 84
landed 1.67 entries into the bid block, so `best_bid` was garbage while `best_ask` parsed fine
(quantity+price are the first 8 bytes of an entry regardless of stride). The comment records the real
poisoned rows: `RELIANCE ltp 1305.0 best_bid 7.81 best_ask 1305.1`.

**Honest-absence design:** `open_interest` stays `Option<u64>` and is `None` on LTP/Quote packets or
any short read — never fabricated as `Some(0)`.

⚠️ **`timestamp_ms` is local wall-clock, not the exchange timestamp.** The code documents this as a
pending item awaiting offset verification against live data. Any claim of exchange-accurate tick
timestamps is currently unsupported.

### 1.2 Dual-sink architecture ✅

`ingestion/src/main.rs` draws the topology in its own header:

```
[Kite WebSocket] ──binary frame──► parser::parse_binary_frame
                                          │  Vec<proto::Tick>
                       per tick, 2 concurrent tokio::spawn:
                            ├─► kafka_producer::publish_tick  → topic market.ticks
                            └─► questdb_sink::insert_tick     → live_ticks table (:8812)
              legacy high-throughput path:
                            └─► questdb_writer::write_tick    → ILP TCP :9009
```

Plus `option_sink` → `option_ticks` / `option_chain_snapshots`. `CHANNEL_CAPACITY = 10_000` ticks
absorbs bursts without blocking the WebSocket reader. Kafka is a cargo feature (default on) so the
service builds on Windows without CMake.

### 1.3 Dynamic subscription control ✅

Newline-delimited TCP control port (default 8085, `INGESTION_CONTROL_PORT`):

- `subscribe:TOKEN:SYMBOL\n` — called by the frontend on symbol switch
- `option_chain_set:{json}` — replaces the bounded option-chain selection for one underlying, carrying `snapshot_interval_secs` and a bounded token list with expiry/strike/type. Parsed by the pure, unit-tested `parse_option_chain_set_line`; the WebSocket writer diffs it against the current selection and subscribes/unsubscribes exactly that set in Full mode

Tick routing is a **pure function** (`TickRoute::Option` vs `TickRoute::Equity`) so equity routing is
provably independent of option-side state, and an empty option map sends everything down the equity
path.

### 1.4 Candle aggregation ✅ / ⚠️

`aggregator/src/ohlc_server.rs` builds **only one interval server-side**:
`CANDLE_INTERVAL_MS = 60_000` — 1-minute base candles. Higher timeframes (5m, 15m, 1h, 1D) are
aggregated **client-side** from these, per the code's own comment. Bucketing is integer truncation.
Two broadcast triggers: every tick, plus a `FLUSH_INTERVAL_MS = 5_000` heartbeat so sparse markets
still look live. Output is JSON over WebSocket **:8081**.

⚠️ **Unresolved:** the predictive agent and load tester both consume `market.ohlc.10m`, and the
frontend's default timeframe is `10m`, but no producer for that topic was found in `ohlc_server.rs`.

### 1.5 Historical data ✅

`aggregator/src/kite_api.rs`. Supported intervals: `day`, `minute`, `3minute`, `5minute`,
`10minute`, `15minute`, `60minute`. Default start 1 year ago. Instrument cache TTL **24 hours**, disk
persisted so it survives restarts, plus a **60-second per-exchange failure cooldown** so an invalid
token cannot cause per-request hammering. Kite rate limiting is **per request, not per instrument**,
which is why quotes are batched. `tool-server/src/candles.rs` is a Tauri-free read-only
reimplementation that unions three candle sources and dedupes on timestamp.

⚠️ No measured end-to-end latency is documented anywhere in code — only buffer capacities and a
"target 60 FPS under load" note in the load tester.

### 1.6 Real-time transport to the UI ✅

Three WebSockets plus SSE:

| Channel | Port | Carries |
| --- | --- | --- |
| OHLC candles | 8081 | 1-minute candles + 5s heartbeat flush |
| Predictive signals | 8082 | Ghost-line ML signal (§3.7) |
| Market insights | 8083 | Anomaly-triggered LLM commentary (§7.3) |
| SSE | HTTP | Agent run streaming, typed events incl. `RUN_FINISHED` |

Order-flow ticks arrive on a dedicated WebSocket with 3-second reconnect, payload shape validation,
and a **ring buffer capped at 5000 ticks**.
---

## 2. Projection engines — all eight types

This is the feature most under-described in existing docs. There is no single "Ghost Line."

### Summary table

| # | Engine | Where | Mathematics | Window | Confidence | Timeframe binding |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **OLS** (`linear`) | Browser | Unweighted least squares, straight line | 50 bars | none | any |
| 2 | **VWLR** (`volume`) | Browser | Volume-weighted least squares, straight line | 50 bars | none | any |
| 3 | **VWEPR** (`curved`) | Browser | Volume-weighted **quadratic** | 50 bars | none | any |
| 4 | **Forecaster** (`forecast`) | Browser | Regime-conditioned EWMA log-return drift, geometric | 30 bars drift | none | any |
| 5 | **ML interpolator** (Path 1) | Browser | Linear interpolation to the backend's predicted close | n/a | inherits #7 | any (forecast mode only) |
| 6 | **quant-core dual/triple** | Rust | OLS + VWLR + VWEPR, returns acceleration coefficient | 50 bars | none | caller-supplied |
| 7 | **Predictive LinReg agent** | Rust service | 14-bar OLS predicting exactly one bar ahead | 14 bars | **R², 1–100** | **hard-bound to 10-minute** |
| 8 | **Volatility-Aware Forecaster** | Python tool | EWMA drift + volatility + ATR, logistic probability | 20 bars | **0.0–1.0** | any |

**Note the two confidence scales differ.** Engine 7 reports R² mapped to `[1, 100]`; engine 8 reports
`Forecast_Confidence` in `[0.0, 1.0]`. Engines 1–6 emit **no confidence value at all** — only the Rust
VWEPR fit surfaces an `accelerationCoefficient`.

### 2.1 The four browser engines ✅

All in `frontend/src/hooks/ghostLineComputation.ts`, selected by `ghostLineMode`
(`'linear' | 'volume' | 'curved' | 'forecast'`, default `'curved'`).

**Shared setup.** `REGRESSION_WINDOW = 50` bars, pinned deliberately to the Rust constants
(`predictive::OLS_MAX_WINDOW` and `vwepr::MAX_WINDOW`, both 50) so *"the agent's read of a projection
and the user's must not disagree."* Minimum 20 candles or it returns nothing. Projection length is
**dynamic with zoom**: `PROJECTION_FRACTION = 0.12` of visible bars, clamped to
`[MIN_PROJECTION_BARS = 3, MAX_PROJECTION_BARS = 20]`, counting *actual bars* not raw seconds so it is
immune to overnight and weekend gaps.

**1 · OLS (`linear`).** `slope = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)`, intercept computed, then a
`correction` term re-anchors the line onto the last close. Requires n ≥ 5. Prices floored at 0.01.

**2 · VWLR (`volume`).** Same straight-line model, weighted least squares with `w = max(volume, 1)`.
Degenerate when `|denom| < 1e-12`.

**3 · VWEPR (`curved`).** Volume-weighted quadratic `y = a₀ + a₁x + a₂x²` via the 3×3 normal
equations, solved by Gaussian elimination with partial pivoting. Falls back to OLS if singular. The
Rust twin explains the quadratic-not-cubic choice: a cubic *"flies off the screen"*.

**4 · Volatility-aware Forecaster (`forecast`).** Not a regression. Takes log-returns over
`driftLookback = 30` bars, computes an EWMA mean drift (span alpha `2/(n+1)`), then **regime-conditions
it** via a faithful JS port of `regime.py` — `REGIME_ADX_PERIOD = 14` / cutoff 25.0,
`REGIME_CHOP_PERIOD = 14` / cutoff 61.8. Weight is `TREND_CONTINUATION_WEIGHT = 1.5` in trending,
`RANGE_REVERSION_WEIGHT = 0.5` in ranging, 1.0 transitional. Output is geometric:
`anchor · exp(drift · i)`. Requires n ≥ 6.

### 2.2 Engine 5 — the ML-signal interpolator (Path 1) ✅

When `ghostLineMode === 'forecast'` **and** a live predictive signal exists for the symbol, the
projection is not fitted at all — it is straight linear interpolation from `last.close` to the
backend's `predicted_close_price`, terminal point pinned to `sig.target_timestamp_ms`.

**Guards:** deviation `|predicted − last.close| / last.close` must be `< 0.20`, predicted must be
positive and finite, and the target timestamp must not be stale. The comment notes this used to
override all four engines and was narrowed to `forecast` mode only so the toggle actually works.

### 2.3 Post-processing shared by every browser variant ✅

Four stages, each with a documented reason:

1. **Live-price anchor shift** — price-only. Deliberately does *not* re-derive the anchor's time; doing so slid the line off the displayed candles.
2. **Volatility clamp** — applied **only** to `curved` and `forecast`; straight-line engines are skipped so a per-step clamp cannot bend them into curves. `avgStep` = mean absolute bar-to-bar delta over the last 20 closes; `maxStep = avgStep × 12`; `maxTotal = maxStep × (points−1) × 2`. Full precision is carried forward — reading back a 2dp-rounded value quantised every step to one paisa and produced the **"ladder" staircase artefact**.
3. **NSE session alignment** — projected timestamps walk forward on the 09:15–15:30 IST grid (`IST_OFFSET_SEC = 19800`, `NSE_OPEN_IST = 33300`, `NSE_CLOSE_IST = 55800`), jumping over the overnight and weekend gap.
4. **Strictly-forward safety net** — if span ≤ 0 or timestamps are non-increasing, rebuild a clean forward ramp. *"Guarantees the line can NEVER render vertically."*

### 2.4 Engine 6 — quant-core Rust dual/triple projection ✅

`quant-core/src/predictive.rs` + `vwepr.rs`. `calculate_ols` (`OLS_MAX_WINDOW = 50`),
`calculate_vwlr` (weights floored at 1.0), and `vwepr::calculate_vwepr_with_accel` — quadratic with
weights `w_i = volume_i × ALPHA^(window−1−i)`, `ALPHA = 0.90`, `MAX_WINDOW = 50`.

`calculate_dual_projection` returns `ProjectionPayload { linear_points, volume_points, curved_points,
acceleration_coefficient }`. The **acceleration coefficient is the quadratic term a₂** and is injected
into the agent's prompts as a momentum signal. Exposed on the consensus report as `vwepr_value`,
`vwepr_slope`, `ols_value`, `ols_slope`.

### 2.5 Engine 7 — the Predictive LinReg microservice ✅

`agents/predictive/src/math.rs`. `WINDOW_SIZE = 14` rolling closes in a `VecDeque`;
`predicted_close = m·14 + b` — it predicts **exactly one bar ahead**, the 15th. Returns `None` below
14 closes.

**This is the only engine that computes R².** `r_squared = 1 − ss_res/ss_tot`, with `ss_tot ≈ 0`
(flat prices) treated as a perfect fit → R² = 1, then `confidence_score = (r_squared × 100).clamp(1.0,
100.0)`.

**Hard-bound to 10-minute candles:** `TEN_MINUTES_MS = 600_000`, `MODEL_VERSION = "alpha-linreg-v1"`.
Consumes `market.ohlc.10m`, publishes `signals.predictive`, serves WebSocket **:8082**. The frontend
store states it plainly: the ML engine operates exclusively on 10-minute candles, *"making '10m' the
primary timeframe for all AI overlays."*

`window_fill()` is exported as the `predictive_window_fill` metric specifically so monitoring can tell
a warming-up agent from a wedged one — otherwise both look like "candles in, nothing out."

### 2.6 Engine 8 — the Volatility-Aware Forecaster ✅

`agents/deep-quant-loop/forecaster.py`, exposed to the LLM as `get_forecast`.

**Config:** `DRIFT_LOOKBACK = 20`, `VOL_LOOKBACK = 20`, `ATR_PERIOD = 14`, `FLAT_BAND = 0.25`,
`MIN_CANDLES = 30`, `PROB_BINS = 10`, `PROB_SCALE = 2.0` — all env-overridable with range clamps.

**Chain:** log returns → EWMA mean and std → drift, volatility, ATR → **regime-conditioned drift**
(same 1.5 / 0.5 / 1.0 weights) → standardised drift `z` → direction classification → **logistic
up-probability** → forecast confidence → expected move in ATR units → alignment.

**Output (a `Forecast_Label`):** `Projected_Direction ∈ {up, down, flat}` ·
`Up_Probability ∈ [0,1]` · `Expected_Move_ATR` (signed, in ATR units) ·
`Forecast_Confidence ∈ [0,1]` · `Forecast_Alignment`. Or an explicit unavailable marker.

It calls `regime.classify_regime` rather than reimplementing it — single source of truth. The JS
`forecast` engine is a *port* of this conditioning, so the two can drift.

**Hard scope guard, from the docstring:** *"never a trade generator… never emits BUY/SELL/HOLD, never
overrides a committed decision, never blocks a trade."*

### 2.7 Rendering and gating ✅ 🔒

Rendered to a dedicated line series, colour `#f59e0b`, orchestrated by `useGhostLine.ts` with an
in-flight guard and a range-change pulse check to avoid recompute thrash. Toggle UI in
`GhostLineToggle.tsx`.

**Feature gating:** both the plan flag (`canAccessGhostline`) and the deployment switch
(`ENABLE_GHOSTLINE`) must be on; a test asserts *switch on + plan flag missing → false*. Ghost Lines
sit in the **unregulated TERMINAL SKU** — they are deterministic, reproducible and carry no directional
recommendation.

**Data guards:** `< 20` candles → nothing · n < 5 (OLS/VWLR/VWEPR) or n < 6 (forecast) → nothing ·
non-finite or non-positive last close → nothing.
---

## 3. The AI agent system

### 3.1 Reasoning nodes ✅

A single LangGraph `StateGraph(AgentState)` in `agents/deep-quant-loop/graph.py`. Ten nodes, of which
four are LLM-backed personas and two are deterministic terminals.

| Node | Role | Consumes | Emits | Can commit a trade? |
| --- | --- | --- | --- | --- |
| **`agent`** (Alpha-Quant) | The main ReAct analyst | messages, mode, profile | reasoning + tool calls | ✅ **Yes** — via `declare_trade` |
| `tools` | Tool executor | tool calls | `ToolMessage`s | n/a |
| **`bull`** (Bull_Agent) | Strongest long case | Shared_Evidence only, no re-gathering | `bull_stance` | ❌ No — read-only tool binding |
| **`bear`** (Bear_Agent) | Strongest short / no-trade case, rebuts Bull | Shared_Evidence + Bull stance | `bear_stance` | ❌ No |
| **`judge`** (Judge_Agent) | Synthesis and sole committer | both stances + evidence + deterministic synthesis | verdict, conviction, decision | ✅ **Yes — only committer in DEBATE** |
| **`qa_agent`** | Trade Q&A over persisted context | Session_Analysis_Context | answer, optional read-only fetch | ❌ No |
| `qa_tools` | Read-only executor for Q&A | forbidden: `declare_trade`, `watch_price_condition` | `ToolMessage`s | ❌ No |
| **VERIFY devil's advocate** | Bear run against the user's own proposed trade | user trade + evidence | an `AIMessage` only | ❌ No |
| `force_hold` | Deterministic HOLD injector | exhausted reasoning budget | HOLD, reason `no-decision-reached` | Forced |
| `force_terminal` | Deterministic stand-aside | Watch_Cap / Session_Budget exhausted | terminal stand-aside | Forced |

Bull and Bear share one implementation (`_run_debate_role`); a role failure yields
`available = False` so the debate proceeds rather than fabricating a stance. The code comment is
explicit: *"NEVER set update['decision'] here — only the Judge commits."*

**Per-role models are configurable:** `DEBATE_BULL_MODEL`, `DEBATE_BEAR_MODEL`, `DEBATE_JUDGE_MODEL`,
each falling back to the system `LLM_MODEL`. Base LLM is `ChatOpenAI`-compatible, temperature 0.2,
`LLM_TIMEOUT_SECS` default 90s, `LLM_MAX_RETRIES` default 4 honouring provider `Retry-After` on 429s
(which absorbs per-minute throttles but explicitly cannot rescue per-day quota exhaustion).

### 3.2 The four modes ✅

Routed by `route_entry` as a conditional entry point.

**FIND** (default) — `agent ⇄ tools`, terminating at end, `force_hold`, or `force_terminal`. The
autonomous discovery loop.

**VERIFY** — same agent loop plus the manual-trade verification path and the **Bear devil's advocate**.
The Bear returns *only a message*; the risk-manager verdict path remains the sole decision authority.

**DEBATE** — `agent ⇄ tools` with **declaration suppressed** (research phase), then handoff →
`bull` → `bear` → (loop for another round, or) `judge` → end. *"DEBATE is the ONLY trigger for the
debate; nothing runs it implicitly."*

**QA** — `qa_agent ⇄ qa_tools`, reusing the same `thread_id`, answering from persisted context without
re-running analysis. Neither node can set a decision, *"so the committed trade stays immutable."*

### 3.3 Deterministic debate synthesis ✅

The consensus classification is **pure maths, not an LLM judgement** (`debate.py`). Strengths are
integers in `[0, 100]`; with `hi = max`, `lo = min`, `gap = hi − lo`:

| Consensus | Condition |
| --- | --- |
| **contested** | `lo ≥ 60` **and** `gap ≤ 15` |
| **strong_agree** | `gap ≥ 30` **and** `hi ≥ 60` |
| **lean** | everything else |

`STRONG_GAP (30) > CONTESTED_GAP (15)` guarantees the two regions are mutually exclusive, so the
classifier is total and unambiguous.

**Conviction derivation:**

```
conviction = clamp( round(0.7 × winning_strength + 0.3 × separation)
                    − (25 if consensus == "contested" else 0),
                    0, 100 )
```

`W_BASE + W_SEP = 1.0` keeps the unpenalised term inside `[0, 100]`; the `CONTESTED_PENALTY = 25`
ensures a contested debate is *strictly less convicted* than strong agreement over comparable
evidence.

### 3.4 The tool registry — 18 tools ✅

Bound at `graph.py:1094-1113`. Sixteen analysis tools plus two control tools.

| # | Tool | Returns | Source |
| --- | --- | --- | --- |
| 1 | `get_candles` | OHLCV | candle archive / Kite historical |
| 2 | `get_consensus_report` | full raw indicator set | quant-core |
| 3 | `get_multi_tf_trend` | 1H/4H/1D directional bias | multi-timeframe candles |
| 4 | `get_chart_patterns` | patterns + confidence | quant-core pattern engine |
| 5 | `get_support_resistance` | S/R levels, degeneracy-checked | quant-core |
| 6 | `get_volume_profile` | POC, VAH/VAL, HVN/LVN | candles |
| 7 | `get_news_context` | headlines + sentiment | news RSS / sentiment agent |
| 8 | `get_prediction` | forward point + R² confidence | **Rust Predictive agent (§2.5)** |
| 9 | `get_market_regime` | trend state / volatility state | `regime.py` |
| 10 | `get_relative_strength` | RS vs benchmark + index context | `rs.py` |
| 11 | `get_order_flow` | tape pressure, CVD, Tick_OFI | `order_flow.py` + live ticks |
| 12 | `get_forecast` | Forecast_Label (§2.6) | `forecaster.py` |
| 13 | `get_session_context` | session phase + expiry proximity | `session.py` |
| 14 | `get_options_analytics` | chain analytics, OI, IV, bias | `options.py` + QuestDB |
| 15 | `get_event_risk` | scheduled-event proximity | NSE event calendar |
| 16 | `get_trade_performance` | realised track record | `journal.py` |
| 17 | `watch_price_condition` | **control** — arms a watcher and suspends the run | Rust watcher |
| 18 | `declare_trade` | **control** — commits through the validator | — |

Every payload passes `validate_contract(tool_name, payload)` with honest-unavailable markers — **no
tool is permitted to fabricate**.

### 3.5 Three distinct tool bindings ✅

Not documented elsewhere, and operationally important:

| Binding | Tools | Used by |
| --- | --- | --- |
| `llm_with_tools` | all 18 | main agent, Judge |
| `readonly_llm_with_tools` | minus `declare_trade`, `watch_price_condition` | Bull, Bear, VERIFY devil's advocate |
| `non_fno_llm_with_tools` | minus `get_options_analytics` | INTRADAY / SWING / INVESTOR profiles |

The third exists as the structural fix for *"NIFTY 50 keeps appearing"* on non-index intraday runs —
**if the tool is not bound, the model physically cannot call it.** The full set is restored for the
F&O workspace or when the analysed symbol is a spot index.

### 3.6 The order of operations — the real 15 steps ✅

`opportunity.py` `FULL_ORDER_OF_OPERATIONS_TOOLS` is the authoritative list, exactly 15 entries
mapping prompt steps 1→8 with lettered sub-steps:

1. `get_multi_tf_trend` — macro alignment (1H/4H/1D)
2. `get_consensus_report` — microstructure, full raw indicators
2b. `get_market_regime` · 2c. `get_relative_strength` · 2d. `get_session_context` ·
2e. `get_options_analytics` · 2f. `get_event_risk`
3. `get_support_resistance` — key levels · 3b. `get_volume_profile` — auction structure
4. `get_chart_patterns` — structural patterns
5. `get_candles` — price action
6. `get_forecast` (primary) and `get_prediction` (secondary) — predictive cross-check
7. `get_news_context` — news catalyst
8. `get_trade_performance` — track-record calibration

⚠️ **`get_order_flow` is a registered analysis tool deliberately excluded from this constant**, because
it is not one of the numbered steps in the prompt's order of operations. Any doc presenting a flat
15-item list that includes order flow is conflating two different sets.

### 3.7 Bounding and guaranteed termination ✅

`should_continue` enforces strict precedence: **pending tool calls always win** (so the loop is never
terminated by the cap while work pends) → a decision in state terminates → session budget exhausted →
reasoning budget remaining → exhausted.

| Bound | Value | Env override |
| --- | --- | --- |
| Consecutive reasoning turns | **6** | `DEEP_QUANT_MAX_REASONING_TURNS` |
| Q&A model turns | **3** | — |
| Debate rounds | **1**, clamped `[1, 5]` | `DEBATE_ROUNDS` |
| Debate turns | derived `rounds×2+1`, absolute cap **11** | `DEBATE_MAX_TURNS` (honoured only within range) |
| Judge clarification tool calls | **2**, clamped `[0, 5]` | `DEBATE_JUDGE_MAX_TOOL_CALLS` |
| Watch cycles per session | **3** | `OPPORTUNITY_WATCH_CAP` |
| Session model turns | **40** | `OPPORTUNITY_SESSION_MAX_TURNS` |
| Session wall clock | **3600 s** | `OPPORTUNITY_SESSION_MAX_WALL_SECS` |
| Heartbeats | disabled by default; 300 s cadence, max 6 | `OPPORTUNITY_HEARTBEAT_*` |
| Message pruning | keep 8 recent turns, ceiling 40 messages | `OPPORTUNITY_PRUNE_*` |
| LLM per call | 90 s timeout, 4 retries | `LLM_TIMEOUT_SECS`, `LLM_MAX_RETRIES` |

**Two forced terminals.** `force_hold` fires on reasoning-budget exhaustion and emits an explicit HOLD
*"rather than fabricating a trade"* — and it folds the `Best_Current_Read` (bias, key reference levels,
why standing aside) into the output so the stand-aside is **actionable rather than content-free**.
`force_terminal` fires on Watch_Cap or Session_Budget exhaustion, **answers the pending
`watch_price_condition` call** so no function call is orphaned, and closes the unbounded
`analyze → watch → invalidate → re-watch` loop.

Every environment resolver degrades to its documented default on unset, empty, unparseable or
out-of-range input and **never raises**, so the loop is always finitely bounded.

⚠️ **No `recursion_limit` is configured anywhere.** LangGraph's implicit default of 25 super-steps is
therefore the true outermost bound — not any project constant. With `OPPORTUNITY_SESSION_MAX_TURNS` at
its default of 40, the graph can hit `GraphRecursionError` before reaching its own documented budgets.

### 3.8 Adaptive Opportunity Engine ✅

`opportunity.py`. Tiers a setup and sizes it accordingly:

| Tier | Size factor | Min pattern confidence |
| --- | --- | --- |
| `a_plus` | 1.0 | 0.75 |
| `b_continuation` | 0.6 | 0.55 |
| `scalp` | 0.3 | 0.40 |
| `stand_aside` | — | — |

Setting `lower_tiers_enabled = False` and `heartbeat_enabled = False` restores the original
A+-only, cap-bounded hunter.

**Invalidation post-mortem.** After a resume on invalidation the engine forces a strategic pivot
rather than a blind re-arm: `thesis_fingerprint` compares symbol, timeframe, direction and quantised
level; a proposed re-arm within `REARM_LEVEL_ATR_TOLERANCE_MULT = 0.5` ATR of the just-invalidated
level is treated as the *same thesis* and blocked. A re-armed invalidation level is floored at
`VOL_FLOOR_ATR_MULT = 1.0` ATR so a noise-level stop cannot immediately re-trip. Both **fail open** —
a malformed re-arm is never falsely blocked.

**Delta_Recheck plans.** A resume re-checks a **non-empty strict subset** of the full 15-step scan, so
it always re-verifies something yet always less than a full re-scan. Resume kinds are normalised to
`target` / `invalidation` / `heartbeat`, with unknown values degrading to `target` — the fullest plan,
erring toward more re-verification.

### 3.9 Checkpointing ✅

Module default is `MemorySaver` so every import path gets a working graph with no configuration.
`compile_with(checkpointer)` lets the FastAPI lifespan swap in `AsyncSqliteSaver`. The comment records
why the obvious alternative fails, measured rather than assumed: the synchronous `SqliteSaver` can be
built at import time but its `aget_tuple` raises `NotImplementedError`, and this graph is driven
exclusively through `astream`.
---

## 4. Technical indicators

### 4.1 Rust `quant-core` — the agent-facing matrix ✅

`quant-core/src/lib.rs`, `IndicatorState`. **Every unavailable value is `f64::NAN`, converted to JSON
`null` on the wire — nothing is defaulted to a neutral number.**

| Indicator | Params | Output |
| --- | --- | --- |
| SMA 50 / 200 (+ previous bar) | mean of last N closes | value, NaN if short |
| Average volume | 20-period | value |
| RSI | period 14, **simple** average of gains/losses | value; 100.0 when avg loss ≈ 0 |
| EMA | `k = 2/(n+1)`, seeded with SMA of first n; computed for 9 and 21 | value |
| MACD | EMA12 − EMA26, signal = EMA9 of the MACD series; needs ≥ 35 candles | line, signal, histogram |
| ATR | period 14, **SMA** of true range | value |
| Bollinger Bands | period 20, 2.0σ, **population** stddev | upper, mid, lower |
| VWAP | `Σ((H+L+C)/3 × V) / ΣV` over the whole slice | value |
| Stochastic %K | period 14 | value only — no %D, no smoothing |
| OBV | ±volume on close change | current + previous (slope inferred by comparison) |
| CMF | period 20 | value |
| Parabolic SAR | AF start 0.02, step 0.02, cap 0.20 | value |
| Opening Range | **first 15 candles** high/low | `orb_high`, `orb_low` |

⚠️ **Two naming defects.** RSI is described as "Wilder-style" in the Python mirror but uses a simple
average, not Wilder smoothing. And `atr_20_ma` is populated with **ATR-14**, which affects the
volatility-state comparison.

### 4.2 Consensus states ✅

`ConsensusEngine`. Four derived states, and the `UNAVAILABLE` variants are a deliberate anti-fabrication
design with the reasoning written into the code.

**`trend_score`** — ±25 each for close vs SMA50, close vs SMA200, MACD histogram sign, and PSAR side;
clamped to ±100.

**`momentum_state`** — `OVERBOUGHT` / `OVERSOLD` / `NEUTRAL` / **`UNAVAILABLE`** from RSI 70/30 and
Stochastic 80/20. The comment explains why the fourth state exists: returning `NEUTRAL` when nothing
could be measured *"conflated two very different statements: 'RSI and Stochastic were measured and
neither is stretched' versus 'neither could be measured at all'"* — and the HUD and the LLM both read
the result as a finding.

**`volatility_state`** — `EXPANDING` / `SQUEEZING` / `NORMAL` / **`UNAVAILABLE`** from Bollinger versus
ATR. Same reasoning: it used to return `NORMAL`, *"asserting ordinary volatility without having
measured any."*

**`volume_flow_state`** — `ACCUMULATION` / `DISTRIBUTION` / `NEUTRAL` / `UNAVAILABLE` from CMF ±0.05
plus OBV direction. Here `NEUTRAL` is a legitimate measured outcome, so only the genuinely
unmeasurable case is separated out.

The consensus report also exposes **every raw indicator value** (`rsi_14`, `stoch_k`, `ema_9`,
`ema_21`, `sma_50`, `sma_200`, `macd_line`, `macd_signal`, `macd_histogram`, `bb_upper`, `bb_mid`,
`bb_lower`, `atr_14`, `vwap`, `obv`, `cmf`, `parabolic_sar`, `current_price`) plus the projection
values — so the LLM reasons on numbers, not labels.

### 4.3 Frontend indicator registry — 19 indicators ✅

`frontend/src/charting/engines/indicatorEngine.ts`.

**Overlays (10):** SMA 20 · EMA 20 · WMA 20 · Bollinger 20/2σ · VWAP · Ichimoku 9/26/52/26 ·
Supertrend 10/×3 · PSAR 0.02/0.2 · Donchian 20 · Keltner 20/×2/ATR10

**Oscillators (9):** RSI 14 · MACD 12/26/9 · Stochastic 14/3/3 · ADX 14 · ATR 14 · OBV · CCI 20 ·
MFI 14 · Williams %R 14

### 4.4 Cross-layer coverage gaps ⚠️

| Indicator | Rust | Python | TypeScript |
| --- | --- | --- | --- |
| RSI, EMA, SMA, ATR, Bollinger, VWAP, OBV | ✅ | ✅ | ✅ |
| **ADX, Choppiness Index** | ❌ **absent** | ✅ `regime.py` | ✅ ported |
| VWEPR, VWLR | ✅ | ❌ | ✅ |
| CMF, Stochastic, MACD, PSAR, Opening Range | ✅ | ❌ | ✅ (most) |
| CCI, MFI, Williams %R, Ichimoku, Supertrend, Donchian, Keltner, WMA | ❌ | ❌ | ✅ frontend-only |

**The significant one:** ADX and the Choppiness Index — the two core regime inputs — **do not exist in
Rust at all**. The Rust consensus path therefore has no trend-strength measure and substitutes
SMA/MACD/PSAR voting. Python's `regime.py` is the only server-side implementation.

`agents/deep-quant-loop/eval/indicators.py` is a deliberately minimal deterministic replay mirror for
the offline eval harness — EMA, SMA, RSI, ATR, Bollinger, VWAP, OBV slope — returning `None` on
insufficient data. Additional Python indicator maths lives inside the analytics modules themselves.

---

## 5. Pattern scanner

### 5.1 What it actually detects — 26 completed labels ✅

`quant-core/src/chart_patterns.rs` (2098 lines). **The "19 patterns in 3 categories" claim is wrong.**

**Pipeline:** needs ≥ 20 candles → `find_swings` (rolling local extrema, `SWING_WINDOW = 5`) →
`alternate_swings` (enforce a Peak→Trough→Peak skeleton) → 16 detectors → sort by confidence
descending.

| Category | Count | Patterns | Confidence |
| --- | --- | --- | --- |
| **Harmonic** | 5 | Gartley, Bat, Butterfly, Crab, Shark | fixed **0.90** |
| **Reversal** | 8 | H&S Top, Inverse H&S, Double Top, Double Bottom, Triple Top, Triple Bottom, Rising Wedge, Falling Wedge | derived, see below |
| **Institutional** | 3 | Quasimodo Bullish, Quasimodo Bearish, Three Drives | fixed **0.85** / **0.90** |
| **Continuation** | 6 | Bull Flag, Bear Flag, Bull Pennant, Bear Pennant, Cup and Handle, Inverse Cup and Handle | derived |
| **Bilateral** | 4 | Ascending Triangle, Descending Triangle, Symmetrical Triangle, Rectangle | derived |

**Confidence formulas** (each capped):

- H&S / Inverse H&S: `0.5 + min(head_prominence, 0.3) + symmetry × 0.2`
- Double Top / Bottom: `0.55 + strictness × 0.25 + min(depth, 0.2)`
- Triple Top / Bottom: `0.6 + 0.3 × (1 − max_deviation/average)`
- Wedges: `0.45 + min(convergence, 0.4) × 0.5`, capped 0.9
- Flags: `0.50 + (1 − ratio) × 0.3`, capped 0.9; Pennants +0.05
- Cup and Handle: `0.55 + depth + (1 − handle/cup) × 0.15`, capped 0.95
- Triangles: `0.5 + |slope| × 30`, capped 0.85; Symmetrical `0.45 + convergence × 30`
- Rectangle: `0.45 + tightness × 0.4`, capped 0.9

**Harmonic classification** uses Fibonacci ratio matching with `FIB_TOLERANCE = 0.05`: Gartley
(AB/XA 0.618, AD/XA 0.786) · Bat (0.382 or 0.50, 0.886) · Butterfly (0.786, 1.272) · Crab
(0.33–0.66, 1.618) · Shark (1.08–1.66, 0.886).

**Tolerances:** `MATCH_TOLERANCE 0.015` · `SHOULDER_TOLERANCE 0.08` · `MIN_FLAGPOLE_CANDLES 5` ·
`FLAGPOLE_MIN_RANGE_RATIO 0.015` · `FLAT_SLOPE_THRESHOLD 0.0005` · `CUP_ASYMMETRY_TOLERANCE 0.05`.

### 5.2 Volume validation engine ✅

Three rules, applied per pattern family and reported in a `volume_validation` field:

| Rule | Test | Applies to |
| --- | --- | --- |
| **Reversal Exhaustion** | `v_final < v_first` | reversals, harmonics, Quasimodo |
| **Consolidation Drying** | `volume_slope < 0` | continuations, wedges, triangles |
| **Breakout Volume Boost** | `current_volume > 1.2 × SMA20(volume)` | all breakouts |

A pattern that fails its volume filter is **not emitted at all** in several detectors (H&S, Double
Top/Bottom, harmonics), rather than emitted with a lower score. Others report `"Unconfirmed"` or
`"Geometric Only"`.

Each pattern also carries `breakout_status` — `Confirmed Breakout` / `Pending Neckline Test` /
`Pending Breakout` / `Breaking Down` — computed against an interpolated neckline where applicable.

### 5.3 Forming (incomplete) pattern engine ✅

A separate entry point, `analyze_forming`, with its own provisional-swing logic:
`FORMING_SWING_WINDOW = 2` (instead of 5) to catch pivots closer to the current bar, plus
`add_provisional_swing` which treats the latest candle as an unconfirmed swing needing **left-side
confirmation only**.

Eight forming detectors cover Double Top/Bottom, H&S, Inverse H&S, Triple Top/Bottom, triangles,
wedges, flags/pennants, harmonics and rectangle. Forming confidences are lower and often fixed
(0.40–0.75), and results carry `is_forming: true` plus a `formation_progress` estimate (0.0–1.0),
sorted by progress then confidence.

This is a genuinely differentiated feature — most retail scanners only report completed patterns.

### 5.4 Confidence thresholds ✅

The engine itself applies **no threshold** — it only sorts. Thresholds are applied downstream:

| Consumer | Threshold |
| --- | --- |
| Agent defensibility record | `PATTERN_CONFIDENCE_THRESHOLD = 0.6` |
| Opportunity tier gates | A+ 0.75 · B-continuation 0.55 · Scalp 0.40 |
| Frontend display bands | high ≥ 0.75 · medium ≥ 0.5 · low below |

### 5.5 Candlestick patterns — separate engine ✅

`quant-core/src/patterns.rs` detects exactly 5, returned as bare strings with no confidence: Doji,
Hammer, Shooting Star, Bullish Engulfing, Bearish Engulfing. Duplicated in
`aggregator/src/quant/patterns.rs`.

### 5.6 The live defect ❌

The agent's system prompt states the engine identifies **19 patterns across three categories** —
Reversal (8), Continuation (6), Bilateral (4). The engine emits **26 across five**. The prompt
**omits the 5 harmonic and 3 institutional patterns entirely**, so the LLM is instructed to disregard
roughly a third of what the tool returns. There is also no enum field for category — it is only implied
by the free-text `structural_bias` string.

**Fix:** update the prompt to enumerate all 26 and add an explicit category field. This is a
prompt-and-schema change, not an engine change.
---

## 6. Order flow, volume profile and market structure

### 6.1 Candle-derived proxy layer ✅

`agents/deep-quant-loop/order_flow.py`. Every function is pure, returns `None` rather than a
fabricated zero when its denominator vanishes, and clamps bounded measures into range.

| Measure | Formula | Unavailable when |
| --- | --- | --- |
| **Close-location value (CLV)** | `((C−L) − (H−C)) / (H−L)`, clamped `[−1, 1]` | `H == L` |
| **Per-candle delta proxy** | `CLV × volume` | CLV is `None` |
| **CVD proxy** | running sum of delta proxy over `lookback` (default 20) | no valid candle |
| **Up / down volume** | volume on `close > open` vs `close < open`; equality counts for neither | — |
| **Buying pressure ratio** | `up / (up + down)`, clamped `[0, 1]` | zero directional volume |

The docstrings are explicit that these are **proxies, not true bid/ask deltas**.

### 6.2 Tick-level Order Flow Imbalance ✅

`compute_tick_ofi`. This is real microstructure, not a proxy:

1. Per-tick traded size is the **positive delta of the day's cumulative volume** between consecutive ticks; non-positive deltas (session resets) are skipped.
2. Each delta is signed by the **tick rule** — uptick +1, downtick −1, zero-tick inherits the previous sign, first sign seeds at +1.
3. Refined **Lee-Ready style by quote location** when a usable best bid/ask is present (`bid > 0`, `ask > 0`, `ask ≥ bid`): trade above the mid → +1, below → −1, exactly at the mid → the tick sign.
4. `OFI = net signed volume / total signed volume`, clamped `[−1, 1]`.

Returns `None` — never a fabricated neutral `0.0` — when there are fewer than `min_ticks` (default
**10**) usable ticks, or total volume falls below `1e-6`.

**Data source:** `_read_live_ticks` queries the `live_ticks` table over QuestDB HTTP, most-recent-first,
then **reverses to chronological order** because the cumulative-volume deltas must run forward in time.

### 6.3 Classification and tick-first priority ✅

`classify_order_flow_state` gives the live tick layer **priority over the proxy layer**: a finite
Tick_OFI decides against `ofi_buy_threshold 0.20` / `ofi_sell_threshold −0.20`; otherwise the pressure
ratio decides against `0.58` / `0.42`. Output is `buying` / `selling` / `balanced`.

`derive_alignment` is a **total function** over every (state × direction) combination →
`aligned` / `misaligned` / `neutral`. A BUY into net selling is `misaligned`; a `balanced` state or an
absent direction is `neutral`.

Every threshold is env-overridable (`OF_*`) with range validation. The tool docstring is clear on
scope: order flow *"never generates a trade, never blocks one, and never overrides your decision."*

⚠️ The docstring cites an "authoritative" Rust `compute_order_flow_imbalance` at
`frontend/src-tauri/src/commands/deep_quant.rs`. **That path does not exist in this repository** — the
Tauri desktop crate is an unchecked-out submodule. The Python function is currently the sole
implementation.

### 6.4 Footprint engine ✅

`frontend/src/charting/engines/footprintEngine.ts` — pure, 355 lines.

Per-candle price-level cells on exact tick multiples, each carrying **bid-initiated** and
**ask-initiated** volume, plus per-candle `delta` (ask − bid), `totalVolume`, `poc` (greatest-volume
level, ties broken toward the close), `imbalances`, and `hasOrderFlow`.

Imbalance detection uses `DEFAULT_IMBALANCE_RATIO = 3`, clamped `[1.5, 20]`. `cumulativeDelta` gives
running CVD across footprint candles.

**Honest absence, enforced by contract:** a candle with no ticks in its bucket produces **zero cells**
and `hasOrderFlow: false`, and the header comment explicitly forbids renderers from presenting those
zeros as measured balance. Nothing is estimated or interpolated.

**Rendering:** `FootprintChart.tsx` runs a `requestAnimationFrame` loop — display-refresh-driven, with
high-DPI scaling and a `tickSize`-driven grid. ⚠️ There is no fixed 60 FPS cap; "60 FPS" is a load-test
target, not a guarantee.

### 6.5 Volume profile ✅

Implemented twice, deliberately mirrored so the agent's levels equal what the trader sees rendered.

Agent side in `tools.py`, frontend side in `volumeProfileEngine.ts`.

- Volume is spread across each candle's high–low span, **split evenly over the rows it touches with the remainder to the top row** — volume is conserved
- **POC** = centre of the single greatest-volume row, lowest index on a tie
- **Value Area** grows outward from the POC, always absorbing the larger adjacent row (lower row on a tie) until it reaches the target percentage
- **VAL** = low edge of the lowest VA row · **VAH** = high edge of the highest
- `price_vs_value_area ∈ above / inside / below / unknown`
- **HVN** = top 3 rows by volume (acceptance shelves → S/R) · **LVN** = bottom 3 non-zero rows (rejection gaps → fast-move zones)

Defaults: `rows = 24` clamped `[1, 1000]`, `value_area_percent = 70.0` clamped `[1, 100]`.

### 6.6 Level-2 depth ⚠️

The tick stream carries `OrderFlowTick { price, bid_volume, ask_volume, timestamp }` and the parser
decodes 5 levels per side. But **no Level-2 depth-ladder analytic is computed anywhere** — the only
L2-derived artefact is the footprint bid/ask grid. The "live Level-2 order book" in the Intraday
profile is a rendering of the depth feed, not an analysed order book.

### 6.7 Support and resistance ✅

`quant-core/src/lib.rs` `compute_sr` — pure, Rust only. Classic floor-trader pivots from the **most
recent completed candle**:

```
pivot = (H + L + C) / 3
r1 = 2·pivot − L      s1 = 2·pivot − H
r2 = pivot + (H − L)  s2 = pivot − (H − L)
r3 = H + 2(pivot − L) s3 = L − 2(H − pivot)
```

Plus `recent_high` / `recent_low` over the window. Intraday timeframes (everything except `1d`)
additionally get the **opening range from the first `OPENING_RANGE_CANDLES = 15` candles** and a
`daily_pivot`.

**Ordering exception:** `detect_ordering_exception` verifies `s3 ≤ s2 ≤ s1 ≤ pivot ≤ r1 ≤ r2 ≤ r3` and,
on breach or any non-finite value, sets `ordering_exception` **while still returning the computed
levels**. Empty candles produce zeroed levels with the exception flagged — never fabricated levels.

⚠️ The agent prompt says the opening range is the **"first 3 candles"**; the code uses **15**.

### 6.8 Market regime ✅

`agents/deep-quant-loop/regime.py` (753 lines), ported to TypeScript, **absent in Rust**.

**Five inputs:** ADX (14) · Choppiness Index (14) · Efficiency Ratio · ATR percentile (ATR-14 ranked
over a 100-bar window) · Bollinger Band width (20).

**Output is an orthogonal pair, not a single label:**

- `trend_state ∈ trending | ranging | transitional` — ADX ≥ 25.0 trending, Choppiness ≥ 61.8 ranging
- `volatility_state ∈ low | normal | high` — ATR percentile < 25 low, > 75 high
- `favorability ∈ favorable | unfavorable | neutral` — derived from a full 3×3 table where **only trending + normal is `favorable`** and **every `ranging` row is `unfavorable`**

Minimum 50 candles. All cutoffs env-overridable (`REGIME_*`).

⚠️ The README describes four regimes — Trending / Ranging / **Volatile** / **Quiet** — as one enum.
There is no `volatile` or `quiet` label in the code.

### 6.9 Relative strength ✅

`agents/deep-quant-loop/rs.py` (867 lines). `resolve_benchmark` maps symbol → benchmark, default
**NIFTY 50**, overridable via `RS_BENCHMARK_MAP`. `time_align` aligns symbol and benchmark candles by
timestamp with **no lookahead**.

**Measures:** RS ratio slope (OLS on the ratio) · relative return · correlation · beta · index return ·
index direction classification.

**Cutoffs:** leader ≥ +0.02 · laggard ≤ −0.02 · index flat band 0.005 · lookback 20 · correlation
window 30 · minimum 30 aligned candles.

### 6.10 NSE session phases ✅

`agents/deep-quant-loop/session.py` (568 lines). **Seven phases:** `pre_open`, `opening`, `morning`,
`midday`, `afternoon`, `closing`, `post_close`.

Defaults: timezone `Asia/Kolkata` with a fixed-offset fallback table · open 09:15 · close 15:30 ·
opening drive 15 min · closing window 30 min · midday lull 11:30–13:30 · weekly expiry Thursday. All
env-overridable (`SESSION_*`).

Time favorability comes from a base table with an **expiry override** for the `afternoon` and `closing`
phases — the code knows expiry-day afternoon flow is distorted.

### 6.11 Event and earnings risk ✅

`event_calendar.py` (427 lines) hits the NSE event-calendar API with cookie priming, a 30-minute TTL,
a **6-hour stale grace period**, and a purposes filter defaulting to `("result",)`. A dedicated
`EventCalendarUnavailable` exception distinguishes a genuine outage.

`get_event_risk` additionally supports file, CSV and generic-API sources.

⚠️ **Gating is prompt-level, not code-enforced.** The prompt instructs that event risk *"only ever
tightens (down-sizes, shortens the horizon, or prefers stand-aside) — it never loosens any criterion and
never blocks or overrides your decision."* There is **no hard event veto in the validator**. The only
mechanical gate is the opportunity tier's regime/session favorability check.

---

## 7. Options analytics 🔒

`agents/deep-quant-loop/options.py` (2223 lines). **F&O workspace only** — the tool is not bound in
other profiles (§3.5).

| Analytic | Method |
| --- | --- |
| **Black-Scholes price** | closed form with normal CDF/PDF |
| **Implied volatility** | **bisection**, tolerance 1e-6, max 100 iterations, vol bounds `[0.005, 5.0]` |
| **Greeks** | delta, gamma, theta, vega, rho; risk-free rate default **6.5%**; expiry close 15:30 IST |
| **PCR (OI)** | `Σ put OI / Σ call OI`; `None` on zero denominator |
| **PCR (Volume)** | `Σ put volume / Σ call volume` |
| **Max pain** | strike minimising total writer payout across the chain |
| **IV skew** | per-strike solved IVs → put-minus-call skew around spot |
| **OI buildup** | ΔOI × Δprice quadrants → `long_buildup` / `short_buildup` / `short_covering` / `long_unwinding` / `neutral`, dead-banded by epsilons |
| **OI walls** | highest-OI call/put strikes above/below spot, with a minimum-OI floor |
| **Futures basis** | `future − spot` |

`_sanitize_numeric_leaves` guarantees **no NaN or Inf ever reaches the wire**.

**Data source:** QuestDB over HTTP, fed by the Rust `aggregator/src/option_chain.rs` (1436 lines) and
`option_chain_selector.rs` (683 lines) from the Kite API. `read_latest_and_prior_snapshot` is what makes
ΔOI and Δprice possible at all.

### 7.1 Options bias fusion ✅

`options_bias.py` (558 lines) takes **one vote each** from six signals — PCR (bullish ≥ 1.3, bearish
≤ 0.7) · OI buildup · max pain vs spot · OI wall proximity (≤ 1% of spot) · IV skew · futures basis —
and requires **`MIN_SIGNALS_FOR_BIAS = 2`** before emitting any non-neutral label. Output is
`bullish` / `bearish` / `neutral` plus an alignment against the proposed direction. All cutoffs
env-tunable.
---

## 8. Risk validators — the hard rules

### 8.1 Implemented twice, deliberately mirrored ✅

`quant-core/src/lib.rs` (Rust) and `agents/deep-quant-loop/validator.py` (Python). The Python header
states it *"must match the Rust constants exactly."* Identical constants, identical reason tags,
identical check order.

| Constant | Value | Applies to |
| --- | --- | --- |
| `MIN_RISK_REWARD` | **2.0** | SWING / INVESTOR / F&O, and any unknown profile (safe default) |
| `MIN_RISK_REWARD_INTRADAY` | **1.3** | INTRADAY only |
| `MIN_STOP_ATR_MULTIPLE` | **1.5** | **every profile — never relaxed** |
| `MIN_BLENDED_REWARD_TO_RISK` | **2.0** | multi-leg plans, env-overridable |

The intraday relaxation is reasoned in the code: intraday index and equity ranges are frequently too
tight for a 1:2 target to fit inside the session structure, so a swing-calibrated floor makes a
defensible intraday bracket *mathematically impossible* and forces perpetual HOLDs. **The stop-distance
floor stays at 1.5× ATR for all profiles.**

### 8.2 Check order for a single-target trade ✅

1. **HOLD bypasses everything** and passes with `risk_reward = 0.0`
2. **MissingLevels** — entry, stop and target all present and finite
3. **DirectionInconsistent** — BUY requires `stop < entry < target`; SELL requires `target < entry < stop`; plus a `risk ≤ 0` divide-by-zero guard
4. **StopTooTight** — `|entry − stop| ≥ 1.5 × ATR` when ATR is finite and positive
5. **RiskRewardTooLow** — `|target − entry| / |entry − stop| ≥ min_rr`; the boundary value passes

Eight failure reasons with stable machine-readable tags: `missing-levels`, `risk-reward-too-low`,
`stop-too-tight`, `direction-inconsistent`, `leg-fraction-out-of-range`,
`target-ordering-inconsistent`, `breakeven-out-of-range`, `blended-rr-too-low`.

### 8.3 Multi-leg management plan validation ✅

An optional layer on top. `ManagementPlan { entry, initial_stop, legs[], breakeven?, trailing? }`.

1. **MissingLevels** — base levels finite, at least one leg, every leg target and fraction finite
2. **LegFractionOutOfRange** — every fraction in `(0.0, 1.0]` and the sum ≤ 1.0 (with `1e-9` tolerance so an exact 1.0 is not rejected by float drift)
3. **TargetOrderingInconsistent** — BUY: `initial_stop < entry`, every target strictly above entry, targets **non-decreasing**; SELL is the mirror image
4. **StopTooTight** — the same 1.5× ATR floor
5. **BreakevenOutOfRange** — must resolve to a price strictly between entry and the **first** leg target on the profit side. A trigger given as an `r_multiple` is checked in R-space (`0 < r < first_target_r`); an explicit `price` takes precedence
6. **BlendedRrTooLow** — `Σ(fraction × |target − entry|) / |entry − initial_stop|` must clear the floor

On success the returned `risk_reward` is the **blended** figure, and it **replaces** the single-target
check.

⚠️ **`TrailingStop` is accepted and round-tripped but never validated**, in both implementations — it
carries no level-ordering constraint. Documented deliberately.

⚠️ **Two stale comments in both mirrors:** the `min_risk_reward_for_profile` docstring says the intraday
floor is "(1.5)" while the constant returned is **1.3**; and the `RiskRewardTooLow` display message
hardcodes "below the 1:2 minimum" even when the 1.3 intraday floor was the one applied.

---

## 9. Conviction fusion — the full five-rule algorithm

`aggregator/src/engine.rs`, `calculate_decision`. Pinned by a complete unit-test suite. The documented
70/30 is correct but **omits two of the five rules**.

| Constant | Value |
| --- | --- |
| `BASE_TECH_WEIGHT` / `BASE_SENT_WEIGHT` | 0.70 / 0.30 |
| `OVERRIDE_TECH_WEIGHT` / `OVERRIDE_SENT_WEIGHT` | 0.30 / 0.70 |
| `CONVICTION_OVERRIDE_THRESHOLD` | 85 |
| `EXTREME_BEARISH_TECH` | 20.0 |
| `EXTREME_BULLISH_SENT` | 80 |
| `CONFLICT_NEUTRAL` | 50.0 |
| `CONFLICT_PENALTY_FACTOR` | 0.60 |
| `BUY_THRESHOLD` / `SELL_THRESHOLD` | 65.0 / 35.0 |

**Rule 1 — no sentiment → 100% technical.** Weights become `(1.0, 0.0)`; the score passes through
unblended.

**Rule 2 — base blend.** `blended = tech × 0.70 + sentiment × 0.30`.

**Rule 3 — conviction override.** If the sentiment conviction score is **strictly greater than 85**,
weights **invert to 30% technical / 70% sentiment**. The rationale in the code: *"strong news breaks
technical patterns."* This is the rule the marketing narrative misses — a high-conviction news item can
flip a bearish technical read into a BUY.

**Rule 4 — conflict resolution, and it is asymmetric.**
`is_conflict = tech_score < 20.0 && sentiment_score > 80`. When it fires **and the override is not
active**, 60% of the distance to neutral is removed: `blended −= (blended − 50.0) × 0.60`.

Two things to note. First, **the conviction override suppresses the conflict penalty** — above sentiment
85 the engine trusts the news instead of neutralising. Second, **the rule fires only on
bearish-technical against bullish-sentiment**. Bullish technical against bearish news gets **no penalty
at all**. Any description of this as bidirectional conflict detection is wrong.

The pinned tests show both branches: tech 15 + sentiment 82 → blended 35.1 → 44 → **HOLD**; tech 15 +
sentiment 90 → inverted weights → 68 → **BUY**, no penalty.

**Rule 5 — range and action mapping.** `final_score.round().clamp(1.0, 100.0)` — the range is
**1–100, not 0–100**. Action thresholds compare against the **unclamped** score: `> 65` BUY, `< 35`
SELL, otherwise HOLD (so 35–65 inclusive is HOLD).

⚠️ Two implementation notes: the whole function is behind `#[cfg(feature = "kafka")]`, so a non-Kafka
build has **no fusion at all**; and the action test uses the pre-clamp float while the emitted score is
the clamped integer — divergent only at the extremes.

---

## 10. News and sentiment

### 10.1 Two independent paths ✅

**Path 1 — keyless Google News RSS.** `tool-server/src/news.rs`. Query `"{symbol} stock NSE India"`
against the Google News RSS endpoint with an 8-second timeout, scraping `<title>` tags by plain string
search, decoding five HTML entities and stripping CDATA. Caps at 10 headlines, filters noise (empty,
literal "Google News", quote-leading, length < 10). **Every failure path returns an empty vector** — the
caller degrades rather than errors.

**Path 2 — the deployed sentiment agent.** `agents/sentiment/` is a **Node/ESM service on :8090**, not
Python. Sources: **NewsData.io** for articles, and a single **Yahoo Finance chart** request supplying
company name, exchange and the 52-week high/low that a prompt rule compares price against (this
replaced two Finnhub calls, so the Finnhub key may now be vestigial on that path).

The frontend reconciles the two shapes explicitly, because *"the two producers disagree on shape."*

### 10.2 F&O → underlying resolution ✅

`news_subject()` — a small function with an instructive bug history. Both news paths keyed off the raw
tradingsymbol, so an F&O run searched for `"RELIANCE26AUG1290CE stock NSE India"` — no such article
exists — and asked the sentiment service for a ticker it has no profile for. **Every derivatives run
got an `Unavailable` news catalyst.**

The fix takes the leading run of ASCII letters: `RELIANCE26AUG1290CE → RELIANCE`,
`BANKNIFTY24DECFUT → BANKNIFTY`. It is gated on `is_fno_symbol` rather than cutting at the first digit
precisely because `V2RETAIL`, `A2ZINFRA` and `3MINDIA` would otherwise collapse to "V", "A" and
nothing — and `ACE` ends in "CE" but is an equity. Fully unit-tested.

⚠️ The same rule is implemented **three times** — Rust `news_subject`, `frontend/src/charting/symbolUtils.ts`,
and `sentimentSubject` in `useQuantStore.ts`. Drift risk.

### 10.3 Caching — three layers ✅

1. **Per-cycle request coalescing** — `CYCLE_TTL_MS = 60_000` with a metadata map. The comment is emphatic that this is request coalescing, **not** a data cache: *"a stale 52-week range would quietly corrupt the analyzer's read of where price sits in its range."* Negative results are cached too, so an unlisted ticker does not re-request every cycle.
2. **24-hour Redis article dedup**, plus in-cycle dedup. A Redis outage **fails open** — a dedup check failure is treated as *not seen*.
3. **In-memory verdict cache**, exposed as the `sentiment_cached_symbols` gauge.

### 10.4 Classification and the model-name caveat ✅

Score range is **1–100**. Labels are Bullish / Bearish / Neutral, and anything else *"folds into Neutral
rather than minting a series from model output."*

⚠️ **The field is named `claude_conviction_score` throughout the proto, but the runtime model is
provider-agnostic** — resolved from `LLM_API_URL` / `LLM_MODEL`. The quant-rag service documents exactly
why this matters: *"the only way to answer which model produced this insight for a given process is to
record what that process resolved to"* — citing the AI model governance doc. **Any statement that
sentiment is "classified by Claude" is describing a field name, not the model.**

### 10.5 Failure reporting ✅

Unusually thorough, with outcome-labelled Prometheus counters:
`CLASSIFY_OUTCOMES = ['scored', 'neutral_no_news', 'failed']` · `NEWS_OUTCOMES` including `no_api_key` ·
`LLM_OUTCOMES` including `parse_error` (*"the provider answered but not with JSON — usually a model
change rather than an outage"*) · `PUBLISH_OUTCOMES` with the sharp note that a Kafka failure *"leaves
the verdict cached and still served over HTTP, so they are invisible to the tool-server while every
Kafka consumer goes blind"* · `ON_DEMAND_OUTCOMES` counted **per request, not per classification**,
because concurrent callers for one uncached symbol coalesce onto a single run.

`articles_deduped_total` carries a warning: if it falls to zero while `articles_total` rises, *"dedup
has broken and the agent is re-spending LLM credits on news it already scored."*

**Partial answers are distinguished from no answer.** The tool-server implements a
`headlines_only_fallback`: if RSS headlines exist it returns them with `"sentiment": "Unavailable"` and
`"sentiment_classified": false`, and **does not** count as unavailable. Only the fully-empty branch
increments the unavailable metric.

### 10.6 Anomaly detector ✅

`agents/quant-rag/src/main.rs`. Consumes protobuf OHLC candles from `market.ohlc.10m` → detects
**absolute price change ≥ 2%** → invokes the configured LLM for an insight → publishes `MarketInsight`
to Kafka `signals.insights` → broadcasts the same insight as JSON over WebSocket **:8083**.

The LLM returns `(headline, analysis_text, sentiment_score)` — genuinely distinct from the news scorer.
The UI renders it with colour-coding at ≥ 3%. ⚠️ One frontend log line still says "DeepSeek anomaly
detection active"; the model is configurable.

`agents/quant-rag/src/patterns.rs` is a **third** pattern implementation — a 100-candle rolling
structural engine with a pinned 4-field contract (`pattern_type`, `sentiment`, `description`,
`confidence` clamped to `[0,1]`).
---

## 11. Workspace profiles — there are four, not three

`frontend/src/utils/layoutHelpers.ts`. The three-profile description in existing docs **omits F&O**,
which is a first-class mode with its own isolation invariants and privileges.

| Profile | Label | Sidebar identity | Split view | R:R floor |
| --- | --- | --- | --- | --- |
| **INTRADAY** | "Scalp" | Order Book (emerald) | ✅ allowed | 1.3 |
| **SWING** | "1H-4H" | Confluence (amber) | ❌ blocked | 2.0 |
| **INVESTOR** | "Macro" | Macro Intelligence (cyan) | ❌ blocked | 2.0 |
| **FNO** | "Options" | Options Flow | ✅ allowed | 2.0 |

**Split view is gated at the store boundary, not in the UI.** `setSplitView(true)` is honoured only for
INTRADAY and FNO and is a **no-op** in SWING/INVESTOR; `setSplitView(false)` is always allowed. Enforced
by a property test.

**Tool binding differs by profile** — `get_options_analytics` is bound only for FNO or a spot index
(§3.5).

**Timeframes:** `1m, 5m, 10m, 15m, 1h, 1D, 1W`, default `10m` (matching the predictive engine's binding).
⚠️ No per-profile timeframe map exists — the shortcut strings are labels only.

### 11.1 Per-mode instrument persistence ✅

The recent behaviour change. `selectedSymbol` **used to** be in the "must not change on mode switch"
set; it is now **per-mode** via a `symbolByProfile` map — *"Investor on TCS while Swing is on INFY."* A
switch is now *expected* to move the symbol to the incoming mode's remembered instrument.

The isolation property survives in a sharper form: a switch may only add or keep an entry for the mode
being **entered**; every other mode's remembered instrument must be untouched. The test comment names
the bug this prevents: *"this is what stops a switch through Intraday from clobbering the instrument
Investor is parked on."*

**Persistence** round-trips through `save_workspace` / `load_workspace` via `bridgeInvoke`, with two
pseudo-symbols used as namespace keys rather than real instruments (`__WATCHLIST__` and a radar key,
debounced). A full restore recovers `symbolByProfile`, chart type, chart-type params, ghost-line mode,
sidebar and split layout; a null blob falls back to INTRADAY / RELIANCE / `10m`.

---

## 12. Portfolio and broker integration

### 12.1 Read-only, enforced by test ✅

Three read hooks only: `useMargins`, `usePositions`, `useOrderBook`. All `axios.get` with
`withCredentials: true`, gated on authentication.

`aggregator/src/kite_api.rs` contains only quote, instruments-CSV and search handlers — **no order
paths**. The strongest signal is `frontend/src/components/fno/__tests__/scopeBoundary.test.ts`, which
maintains an explicit **denylist asserted as absent**: `place_order`, `execute_trade`, `cancel_order`,
`modify_order`, `submit_order`, `close_position`, `square_off`.

Read-only is therefore **enforced by test, not merely by convention** — which is the artefact to show a
broker's compliance team.

The only trade-side write anywhere is `declare_trade`, which is a journal and plan commit — an intent
record, not a broker order.

### 12.2 Margin display ✅

Three tabs — Risk & Margins, positions, orders — each with its own skeleton and a refetch on tab
activation. The margin card renders `equity.net` as "Available Margin" with a fallback chain to
`equity.available.live_balance ?? equity.available.cash` when net is undefined or zero, plus `m2m`
(P&L-coloured), `debits` and `exposure`.

⚠️ **The documented 60-second margin cache was not found.** `useMargins` has no client-side TTL — it
fetches on mount and on tab activation. The 60-second constants that do exist are the Kite instruments
*failure cooldown* and the sentiment agent's per-cycle memo. A server-side cache may exist in a service
outside this repository.

❌ No holdings hook exists alongside the other three.

### 12.3 Broker OAuth ✅

`ingestion/src/kite_auth.rs`: user → Kite Connect login → redirect with `request_token` → POST to the
session endpoint with `X-Kite-Version: 3` and a checksum → `access_token` **valid until midnight IST**.
The module is skipped entirely when `KITE_ACCESS_TOKEN` is already set. A scheduled script handles the
daily refresh.

App auth is cookie-based via `withCredentials`. ⚠️ The hostname `auth.stratai.live` appears in a commit
message but not in the code paths inspected; the deployed sign-in surface lives outside this repository.

---

## 13. Platform layer

### 13.1 The bridge abstraction ✅

`frontend/src/lib/bridge/` routes every backend call through `bridgeInvoke(cmd, args)` — Tauri `invoke()`
on desktop, HTTP at the same origin in the browser. Commands with no web adapter fail as a typed
`BridgeUnsupportedError` rather than an opaque `TypeError`.

Verified routes include `fetch_symbol_sentiment`, `fetch_questdb` (returns raw **text**, matching the
Rust signature), `get_fno_analytics`, `search_instruments` (short-circuits empty queries with no request;
covers **all four Indian segments** NSE/BSE/NFO/BFO — BSE was a missing leg that made SENSEX and BANKEX
unfindable), `get_pool_status` (boolean that never throws), `open_browser` (with a `window.open` fallback
for popup blockers), `get_feature_switches`, `cancel_deep_quant_agent`, `set_radar_symbols`.

Reads stay tolerant — missing store equals missing key equals "no saved state" — while writes reject
explicitly.

### 13.2 Server-side feature switches ✅

`frontend/src/app/api/_featureSwitches.ts`. Switch names deliberately carry **no `NEXT_PUBLIC_` prefix**
so a user cannot flip a kill switch in devtools and render premium UI. They are runtime-resolved rather
than build-baked because *"this app's production build peaks north of 1 GB of RSS on a memory-tight
droplet."*

Client-side flags are labelled explicitly as *"a UI affordance"* — enforcement is server-side, and a
property test asserts the gate withholds regulated output on the website too, not only on desktop.

### 13.3 Desktop shell ⚠️

The Tauri crate (`src-tauri/`) is an **unchecked-out submodule**. Multiple files reference it — the
feature switches baked with `option_env!`, the `BinaryCandle` wire format in `commands/charts.rs`, the
original `load_candles_with_ts` that `tool-server/src/candles.rs` reimplements, and
`commands::sentiment::fetch_news_headlines`.

❌ **The credential vault (Argon2id + AES-256 in Tauri Stronghold) advertised on the website is not
present in this repository.** A search for `stronghold` and `argon2` across the frontend and terminal
trees returns zero hits, and the visible persistence path uses localStorage on web. It may exist in the
submodule; nothing here implements or invokes it. **Treat the marketing claim as unverified until the
submodule is inspected.**

### 13.4 Chart engine ✅

TradingView Charting Library vendored under `frontend/public/static/charting_library/`, with app-side
glue in `frontend/src/charting/`. Chart modes: `STANDARD` / `VOLUME_PROFILE` / `FOOTPRINT`. Chart types
include area and Renko (with `brickSize` in persisted params).

Graceful degradation rather than a dedicated offline mode: in-session state is preferred over defaults on
a backend miss, `get_pool_status` resolves false instead of throwing, news and sentiment failures return
empty.

---

## 14. Journal, calibration and telemetry

### 14.1 The three-phase journal ✅

`agents/deep-quant-loop/journal.py`. The reason it exists, from its own header: the agent had **no memory
of its own past trades and never found out whether it was right** — *"without that feedback every
'improvement' is a guess."*

1. **RECORD** — every committed decision persisted with the setup context that produced it
2. **SCORE** — open BUY/SELL trades scored lazily against subsequent candles: did price reach the target or the stop first?
3. **AGGREGATE** — realised win rate and expectancy in R multiples, **overall and per setup type**

**Storage rule that matters:** only BUY/SELL with **finite entry, stop and target** are stored as `open`
and therefore scoreable. Everything else — HOLD, or a directional trade missing levels — is stored as
`hold` and **excluded from win-rate and expectancy**. This is the right call: it stops unscoreable rows
diluting the statistics. `JOURNAL_EXPIRY_SECONDS` (default 7 days) marks a trade that hit neither level
as `expired`, also excluded.

**Arithmetic:** `scored = wins + losses` · `win_rate = wins/scored` · `expectancy_r = mean(r_multiple)`
over win/loss rows only. **Both are `None` rather than 0 when there is no data**, and consumers respect
the distinction.

**Exit simulation is not reimplemented.** A managed trade is scored by reconstructing its persisted
management plan and calling `trade_manager.simulate_plan` — single source of truth for the multi-leg
fill, breakeven and trail logic.

### 14.2 Setup fingerprinting ✅

`derive_setup_tags` builds a `setup_key` encoding direction, macro alignment, predictive agreement and
value-area location, plus two dimensions drawn from single-source-of-truth modules: an **opportunity tier
tag** and exactly **one management-style tag** from a fixed enumeration. The enumeration is fixed
deliberately to keep cardinality low, so the key stays deterministic and groups accumulate enough scored
trades to clear the low-sample threshold.

### 14.3 The feedback loop into conviction ✅

`get_trade_performance` is exposed as an agent tool, and its docstring is a **hard instruction, not a
hint**: *"if a comparable setup historically has NEGATIVE expectancy or a win rate that does not support
its Risk:Reward, you MUST reduce conviction, tighten the criteria, or HOLD."* Statistics are to be
treated as a **weak prior** when `low_sample` is true (fewer than 10 scored trades).

Open trades are scored against the latest candles **before** the statistics are returned, so the numbers
are current at call time. Failure is soft — the except branch returns zeroed stats with
`low_sample: True` so the agent proceeds without an edge estimate rather than crashing.

The system prompt reinforces it: take *"ONLY well-defined, corroborated, positive-expectancy trades"*,
with an explicit warning that capital preservation means avoiding **bad** trades, not **all** trades.

### 14.4 Feature attribution and live calibration ✅

`attribution.py` performs feature attribution and pruning over the journal across roughly twelve tags
(direction, macro, predictive, value area, regime, RS, forecast, timing, session, debate, options).

`calibration.py` handles live conviction calibration, and its docstring makes an honest and unusual
admission: **a multi-agent LLM debate cannot be replayed on the historical archive**, so validity is
proven by *live calibration* rather than backtest.

### 14.5 The discipline surface ✅

`stream_events.py` emits a named `"track-record"` check into the run's event stream with the detail
`win_rate=…, expectancy_r=… over N scored trade(s)` — so the calibration evidence appears in the
glass-box stream **as a named pre-trade gate alongside the others**, not buried in a database.

`frontend/src/hooks/useMacroIndicators.ts` documents removing Total Return, Win Rate, Max Drawdown and
Average Conviction from the user-facing dashboard, and replacing them with `computeDisciplineMetrics()`:
**Setups Audited, Setups Rejected, Forced Holds, plan adherence** — rendering `—` rather than `0` for
anything unmeasured. The reasoning is recorded: the removed metrics were derived from paper trades
against a hardcoded ₹1,00,000 balance, and the old "win rate" counted any positive price as a win.

Grepping the agent's HTTP surface for `win_rate|expectancy|backtest|sharpe` returns **no matches** —
there is no endpoint exposing journal statistics. Internal calibration is preserved; external performance
display is gone. 🔒

### 14.6 Telemetry separation ✅

`telemetry.py` writes to `telemetry.db`, a **dedicated file structurally separate from** the journal's
`trade_journal.db` — *"telemetry never opens, reads, or writes the journal's trades tables."* Same
10-second busy timeout so a concurrent writer waits for the lock rather than failing.

`service-metrics/` is a shared Rust crate exposing a Prometheus registry; `status-api` aggregates across
services keyed on the **metric name suffix**, so `ingestion_kite_ws_connected` and `aggregator_ws_clients`
map to stable service-agnostic names.

⚠️ One security note in `status-api`: it deliberately does **not** use wildcard CORS, and the comment
calls out that `aggregator/src/kite_api.rs` does — justified as a localhost-only dev proxy. **If that
aggregator endpoint is ever exposed beyond localhost, the wildcard becomes a real problem.**

---

## 15. Compliance and governance features 🔒

These are product features, not just paperwork, and they are the strongest differentiators to show a
regulator or a broker's compliance head. Full detail in `business/COMPANY_REGISTRATION_PLAN.md`.

| Feature | Implementation | Status |
| --- | --- | --- |
| **Immutable recommendation record** | `reco_store.py` — hash-chained, append-only triggers, no update/delete/purge path, full schema including `tool_inputs_json` and the `model_id`/`prompt_hash`/`prompt_set_hash` triple, idempotent per thread | ✅ ~85% |
| **Client interaction log** | `interaction_log.py` — same hash-chain primitive, request row written **before** work so refusals and crashes leave a trace, content stored verbatim, refusals first-class | ✅ ~85% |
| **Personalisation refusal guardrail** | `personalisation.py` — pure, total, deterministic, **pre-LLM**, eight ordered categories, NFKC normalisation, adjective slot so "my *entire* capital" cannot bypass "my capital" | ✅ ~95% |
| **Prompt/model versioning** | `prompt_version.py` hashes the guardrail rule into the prompt set, so changing the control changes the recorded analyst version | ✅ |
| **Honest-failure architecture** | Unavailable markers across every tool; `validate_contract` rejects fabrication; commit `82e0cb0` "remove every source of fabricated data" | ✅ |
| **Read-only broker posture** | Denylist test (§12.1) | ✅ |
| **AI governance documentation** | `docs/compliance/AI_MODEL_GOVERNANCE.md` (30KB), `AI_DISCLOSURE.md` (14KB), `BRAND_GUIDELINES.md` (33KB), `SECRET_ROTATION_RUNBOOK.md` (15KB) | ✅ written, ⚠️ not surfaced in product |
| **Server-side SKU gate** | `entitlements.py` — fail-closed, unknown modes gated by default | ⚠️ **built but inert**: `SKU_ENFORCE` defaults off and the entitlement endpoint does not exist |
| **Analyst of record** | `ANALYST_OF_RECORD` env var | ❌ unset → always NULL |
| **KYC / client onboarding** | — | ❌ absent, zero code |
| **In-product disclaimers** | — | ❌ absent from `frontend/src` |

The personalisation guardrail deserves particular note as a **feature**, not just a control. It refuses
eight categories — position sizing, holdings, capital, income, net worth, goals, third-party, suitability
— **before the LLM is invoked**, so the refusal is reproducible years later. The reasoning is in the
docstring: a rule inside a system prompt is *"a request, not a control: it is subject to temperature,
model swaps, prompt-injection in the user's own question, and context truncation."* Deliberate
non-detections are documented with reasons — bare "my trade" means this session's declared trade, and
"should I buy?" is exactly what impersonal research *is*, so refusing it *"would delete the product
rather than repackage it."*
---

## 16. SKU map — which features sit where

Drives the entitlement gate (`entitlements.py`, `frontend/src/lib/sku.ts`).

| SKU | Regulated? | Features |
| --- | --- | --- |
| **TERMINAL** | No | All 8 projection engines · all indicators (Rust, TS) · 26-pattern scanner + forming patterns · order-flow footprint · volume profile · Level-2 depth render · pivot S/R + opening range · regime label · relative strength · session context · **VERIFY maths on the user's own levels** · discipline dashboard · portfolio read-only · all 4 workspace profiles · chart engine |
| **RESEARCH** 🔒 | **Yes — needs INH** | **FIND mode** · **DEBATE mode** (Bull/Bear/Judge) · **conviction score** · **QA mode** · options analytics · event risk gate · track-record calibration · compliant research reports |
| **PLATFORM** | Licensee's | White-labelled combination of the above |

**Mode → SKU mapping** in `entitlements.py`: `TERMINAL_MODES = {VERIFY}` ·
`RESEARCH_MODES = {FIND, DEBATE, QA}`. **An unknown or empty mode returns `True` for
`mode_requires_research`** — so a newly added graph mode is gated by default rather than open by default.
That is the correct direction for a fail-safe.

---

## 17. Documentation divergences — every one found

Ordered by consequence. Each is a doc-or-prompt fix, not an engine rewrite.

| # | Claim | Reality | Consequence |
| --- | --- | --- | --- |
| 1 | **"19 chart patterns in 3 categories"** (agent system prompt) | **26 labels across 5 categories**; harmonics (5) and institutional (3) undocumented to the LLM | **High** — the model is told to disregard a third of the tool's output |
| 2 | **"Argon2id + AES-256 in Tauri Stronghold"** (website) | Zero occurrences of `stronghold` or `argon2` in this repo; visible persistence uses localStorage on web | **High** — a public security claim that cannot be verified here |
| 3 | **"Sub-millisecond order processing… execute at optimal price levels"** (tradingrw.com) | No order path exists; read-only is enforced by denylist test | **High** — advertises a regulated capability the product does not have |
| 4 | **"Classified by Claude"** | Field is *named* `claude_conviction_score`; runtime model resolves from `LLM_MODEL` | **Medium** — misstates the model in an AI-governance-relevant way |
| 5 | **Opening Range = "first 3 candles"** (agent prompt) | `OPENING_RANGE_CANDLES = 15` | **Medium** — the model reasons about the wrong level |
| 6 | **Conflict detection described as symmetric** | Fires **only** on bearish-tech + bullish-sentiment; the mirror case gets no penalty | **Medium** — overstates the guardrail |
| 7 | **70/30 fusion presented as the whole algorithm** | Five rules including a >85 conviction override that inverts to 30/70 | **Medium** — omits the rule that can flip a bearish read to BUY |
| 8 | **Regime = "Trending / Ranging / Volatile / Quiet"** (README) | Orthogonal `trend_state × volatility_state`; no `volatile` or `quiet` label | **Medium** |
| 9 | **"Authoritative Rust OFI" at `src-tauri/.../deep_quant.rs`** | That crate is not in the repo; Python is the sole implementation | **Medium** |
| 10 | **Three workspace profiles** | **Four** — FNO is a full mode with its own privileges | **Medium** |
| 11 | **Reasoning budget as the outer bound** | No `recursion_limit` set; LangGraph's default 25 super-steps is the real ceiling, below `SESSION_MAX_TURNS = 40` | **Medium** |
| 12 | **Multi-interval server-side candles** | Only 1-minute is aggregated server-side; higher timeframes roll up client-side | Low-Medium |
| 13 | **Exchange-accurate tick timestamps** | `timestamp_ms` is local wall clock; exchange timestamp is a pending item | Low-Medium |
| 14 | **"60s margin cache"** | Not found; `useMargins` has no TTL | Low |
| 15 | **Absorption / exhaustion detection** (README) | No dedicated detector in `order_flow.py`; the only exhaustion logic is the chart-pattern volume validator | Low |
| 16 | **ADX / Choppiness as core regime inputs** | Exist only in Python and TypeScript; **no Rust implementation**, so the Rust consensus path has no trend-strength measure | Low-Medium |
| 17 | `atr_20_ma` | Carries **ATR-14** | Low |
| 18 | **Intraday R:R floor "1.5"** (docstrings, both mirrors) | Constant returned is **1.3** | Low |
| 19 | **60 FPS footprint rendering** | `requestAnimationFrame` — display-refresh-driven, no fixed cap | Low |
| 20 | **Single quant engine / "single source of truth"** | Indicator matrix duplicated verbatim in `quant-core` and `aggregator/src/quant`; pattern engines exist in **three** places; F&O symbol rule in **three** places | Low-Medium |

### Recommended fix order

1. **#1** — update the prompt to enumerate all 26 patterns and add a category field. Highest value-per-hour item in this table: the engine already produces the output, the model is just told to ignore it.
2. **#3** and **#2** — website copy. See `business/MARKETING_PLAN.md` §2.
3. **#5** — one-word prompt fix, wrong-level reasoning.
4. **#11** — set an explicit `recursion_limit` so the project's own budgets are the real bound.
5. **#4**, **#6**, **#7**, **#8**, **#10** — correct the narrative docs; no code change needed.
6. **#20** — consolidate the duplicated indicator matrix behind a shared crate when convenient. Real drift risk, low urgency.

---

## 18. What genuinely differentiates this product

Stated plainly, and only where the code backs it.

**Eight projection engines with honest confidence.** Four user-selectable in the browser, a
regime-conditioned probabilistic forecaster, a dedicated Rust ML service reporting R², and a
volume-weighted quadratic that reports its own acceleration coefficient into the agent's reasoning. Most
retail tools ship one line and no confidence measure.

**A pattern scanner that reports what is *forming*, not only what has completed** — with a provisional
swing point on the current bar, a formation-progress estimate, and a volume-validation verdict per
pattern.

**Real tick-level order flow imbalance with a Lee-Ready quote refinement** — and, more unusually, it
reports `None` rather than a neutral 0.0 when the tick stream is thin. Most competitors present a
candle-derived proxy as if it were real delta.

**A multi-agent debate whose synthesis is deterministic.** Bull and Bear are LLMs; the consensus
classification and the conviction formula are pure maths with published thresholds. The contested penalty
means a genuinely close debate cannot produce false confidence.

**Risk rules implemented twice, in two languages, with identical constants and reason tags** — and a
volatility floor that no profile can relax.

**Hash-chained, append-only regulatory records carrying the model and prompt version that produced each
recommendation.** Any published recommendation is replayable years later and provably unaltered. This is
rare in registered shops, let alone pre-registration ones.

**A deterministic pre-LLM guardrail that keeps the product inside the Research Analyst perimeter by
construction** rather than by policy.

**Honest-failure architecture as a system-wide invariant, not a slogan** — `UNAVAILABLE` consensus states
that separate "measured and neutral" from "could not be measured", unavailable markers on every tool,
`validate_contract` rejecting fabrication, `finite_opt` guaranteeing no NaN reaches the wire, and
`None`-not-zero throughout the Python analytics.

**A calibration loop that feeds realised expectancy per setup type back into conviction**, with the
instruction to reduce conviction on negative-expectancy setups written as a requirement rather than a
suggestion. That dataset compounds and cannot be bought.

---

## 19. Source index

**Data pipeline:** `ingestion/src/parser.rs` · `ingestion/src/main.rs` · `ingestion/src/kite_auth.rs` ·
`aggregator/src/ohlc_server.rs` · `aggregator/src/kite_api.rs` · `tool-server/src/candles.rs`

**Projections:** `frontend/src/hooks/ghostLineComputation.ts` · `frontend/src/hooks/useGhostLine.ts` ·
`quant-core/src/predictive.rs` · `quant-core/src/vwepr.rs` · `agents/predictive/src/math.rs` ·
`agents/predictive/src/engine.rs` · `agents/deep-quant-loop/forecaster.py`

**Agents:** `agents/deep-quant-loop/graph.py` · `debate.py` · `opportunity.py` · `tools.py` ·
`stream_events.py` · `main.py`

**Analytics:** `quant-core/src/lib.rs` · `quant-core/src/chart_patterns.rs` · `quant-core/src/patterns.rs` ·
`aggregator/src/quant/mod.rs` · `agents/deep-quant-loop/regime.py` · `rs.py` · `order_flow.py` ·
`session.py` · `event_calendar.py` · `options.py` · `options_bias.py` ·
`frontend/src/charting/engines/indicatorEngine.ts` · `footprintEngine.ts` · `volumeProfileEngine.ts`

**Risk and fusion:** `quant-core/src/lib.rs` (validators) · `agents/deep-quant-loop/validator.py` ·
`trade_manager.py` · `aggregator/src/engine.rs` · `aggregator/src/consumer.rs`

**Sentiment:** `tool-server/src/news.rs` · `agents/sentiment/src/*` · `agents/quant-rag/src/main.rs` ·
`agents/quant-rag/src/llm.rs` · `agents/quant-rag/src/patterns.rs`

**Platform:** `frontend/src/lib/bridge/*` · `frontend/src/lib/sku.ts` · `frontend/src/lib/featureFlags.ts` ·
`frontend/src/app/api/_featureSwitches.ts` · `frontend/src/utils/layoutHelpers.ts` ·
`frontend/src/store/useTradeStore.ts` · `useChartUIStore.ts` · `frontend/src/hooks/useAlphaData.ts` ·
`frontend/src/hooks/useMacroIndicators.ts`

**Journal and compliance:** `agents/deep-quant-loop/journal.py` · `telemetry.py` · `attribution.py` ·
`calibration.py` · `reco_store.py` · `interaction_log.py` · `personalisation.py` · `hashchain.py` ·
`prompt_version.py` · `entitlements.py` · `docs/compliance/*`

**Related documents:** `docs/business/COMPANY_REGISTRATION_PLAN.md` ·
`docs/business/MARKETING_PLAN.md` · `docs/business/INVESTOR_BRIEF.md` ·
`docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` · `docs/business/PLAN_OF_ACTION.md`
