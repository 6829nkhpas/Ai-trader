# Requirements Document

## Introduction

The Deep Quant agent ("Alpha-Quant") commits trade decisions from candle-derived indicators but is blind to **order flow** — who is actually pressing the trade, buyers or sellers. A veteran trader reads the tape: are upticks trading on volume, is cumulative delta confirming the move, is price being absorbed at a level. This feature gives the agent that read, within the honest limits of the data the system actually has.

The system's tick stream (`live_ticks` in QuestDB) carries, per tick, the last price, the day's cumulative volume, and the best bid / best ask. True order-flow imbalance is therefore a **live, tick-based** signal computable only from that intraday stream — there is no tick-level bid/ask history in the multi-year candle archive. To preserve the project's "prove the lift with a backtest" rigor (used by the regime gate and relative-strength features), this feature delivers **two complementary layers**:

1. **Candle-derived order-flow proxies** — a pure-math layer computed from OHLCV candles (a per-candle delta proxy from close-location × volume, a cumulative-volume-delta proxy, up/down volume, and a buying-pressure ratio). Because it needs only candles, it is deterministic, property-testable, and **fully backtestable** on the historical archive with the same with-proxy / without-proxy comparison.
2. **Live tick-based Order Flow Imbalance (OFI)** — a true tick-rule OFI (signed traded-volume by uptick/downtick, refined Lee-Ready-style by quote location when best bid/ask is present), read from `live_ticks` via the QuestDB HTTP API and computed in Python (mirroring the existing Rust `compute_order_flow_imbalance`). It is the real intraday edge but is honestly marked **unavailable** when the tick stream is absent (e.g. market closed), never fabricating a neutral value.

Both layers feed a single `Order_Flow_Label` (an Order_Flow_State plus an Alignment of a proposed trade direction with the flow) exposed to the agent as a new Analysis_Tool (`get_order_flow`), wired through the same layers as the preceding features: graph registration, system prompts, the defensibility record, a stream verification step, a Trade_Journal fingerprint dimension, and the Backtest_Seeder comparison (proxy layer only). All parameters are configurable via environment variables with documented defaults.

Order Flow Context is a **filter / context aid, not a trade generator**. It never fabricates data and never, on its own, forces, blocks, or overrides a trade. It reuses the architecture established by the regime-detection-gate and relative-strength-context features so the three compose.

## Glossary

- **Deep_Quant_Agent**: The LangGraph ReAct agent ("Alpha-Quant") in `agents/deep-quant-loop/` that calls Analysis_Tools and commits decisions.
- **Order_Flow_Calculator**: The pure-Python, deterministic component that maps OHLCV candles (and, when available, a tick sequence) plus a configuration to an Order_Flow_Label or an Unavailable_Marker.
- **Order_Flow_Proxy_Measure**: A named, deterministic scalar computed from OHLCV candles only (per-candle delta proxy from close-location × volume, cumulative-volume-delta proxy, up-volume, down-volume, buying-pressure ratio).
- **Tick_OFI**: The live order-flow-imbalance measure computed from a `live_ticks` tick sequence (last price, cumulative volume, best bid, best ask) using the tick rule refined by quote location; a value in [-1.0, 1.0], or unavailable.
- **Order_Flow_State**: A categorical classification of net pressure, one of `buying`, `selling`, or `balanced`.
- **Alignment**: A derived classification stating whether a proposed trade direction agrees with the Order_Flow_State, one of `aligned`, `misaligned`, or `neutral`.
- **Order_Flow_Label**: The structured output: the Order_Flow_State, the Alignment, the named Order_Flow_Proxy_Measures, the Tick_OFI (or its unavailable indication), and a flag indicating whether live tick data contributed.
- **Order_Flow_Tool**: The `get_order_flow` Analysis_Tool exposed to the Deep_Quant_Agent.
- **Live_Ticks_Source**: The `live_ticks` table read via the QuestDB HTTP API (`http://127.0.0.1:9000/exec`), providing recent ticks with last price, cumulative volume, best bid, and best ask.
- **Tool_Result_Contract**: The structural contract a tool result must satisfy, re-validated on receipt by `validate_contract` in `tools.py`.
- **Unavailable_Marker**: An honest, non-fatal result (`{"unavailable": true, "reason": ...}`) indicating order flow could not be computed.
- **Defensibility_Record**: The evidence record assembled by `build_defensibility_record` in `graph.py`.
- **Declared_Trade**: The committed BUY / SELL / HOLD decision produced by the Deep_Quant_Agent.
- **Trade_Journal**: The SQLite measurement/feedback store (`journal.py`).
- **Setup_Fingerprint**: The coarse, low-cardinality setup tag set derived by `journal.derive_setup_tags`.
- **Backtest_Seeder**: The `backtest.py` module that replays historical candles and seeds the Trade_Journal tagged `source='backtest'`.
- **Verification_Step**: A `VERIFICATION_STEP` event emitted by `stream_events.py`.
- **Rust_Tool_Server**: The Rust service at `http://localhost:8084` serving `/tools/get_candles`.

## Requirements

### Requirement 1: Candle-derived order-flow proxy math

**User Story:** As a quantitative developer, I want deterministic order-flow proxies computed from OHLCV candles, so that order-flow context is available and backtestable without tick data.

#### Acceptance Criteria

1. THE Order_Flow_Calculator SHALL compute each Order_Flow_Proxy_Measure exclusively from a provided OHLCV candle sequence and a provided configuration, performing zero network calls within the proxy computation.
2. THE Order_Flow_Calculator SHALL compute a per-candle delta proxy as the close-location value `((close - low) - (high - close)) / (high - low)` multiplied by the candle volume, and SHALL represent the close-location value as null when `high == low`.
3. THE Order_Flow_Calculator SHALL compute a cumulative-volume-delta (CVD) proxy as the running sum of the per-candle delta proxy over the configured lookback period.
4. THE Order_Flow_Calculator SHALL compute an up-volume measure (volume on candles closing above their open) and a down-volume measure (volume on candles closing below their open) over the configured lookback period.
5. THE Order_Flow_Calculator SHALL compute a buying-pressure ratio in [0.0, 1.0] as up-volume divided by total directional volume over the lookback, and SHALL represent it as null when total directional volume is zero.
6. WHEN invoked two or more times with an element-wise-identical candle sequence and identical configuration, THE Order_Flow_Calculator SHALL return identical Order_Flow_Proxy_Measures.
7. THE Order_Flow_Calculator SHALL implement all proxy functions as pure functions that produce no observable change to their input candle sequence or configuration.

### Requirement 2: Live tick-based Order Flow Imbalance

**User Story:** As a trader, I want a true tick-based order-flow imbalance during market hours, so that the agent can read live buying/selling pressure.

#### Acceptance Criteria

1. THE Order_Flow_Calculator SHALL compute the Tick_OFI from a provided tick sequence of (last price, cumulative volume, best bid, best ask) by signing each tick's positive cumulative-volume delta with the tick-rule direction (uptick → buying, downtick → selling) and normalizing the net signed volume by total signed volume into a value in [-1.0, 1.0].
2. WHERE a tick carries a usable best bid and best ask, THE Order_Flow_Calculator SHALL refine that tick's sign using the trade's location relative to the bid/ask mid-price (Lee-Ready style) rather than price direction alone.
3. IF the tick sequence is empty, OR contains too few ticks to form a trustworthy imbalance, OR yields zero total signed volume, THEN THE Order_Flow_Calculator SHALL report the Tick_OFI as unavailable rather than returning a fabricated neutral value.
4. THE Order_Flow_Calculator SHALL clamp the Tick_OFI to [-1.0, 1.0] and SHALL never return a non-finite Tick_OFI.
5. THE Order_Flow_Calculator SHALL compute the Tick_OFI as a pure, deterministic function of the provided tick sequence and configuration, with no input mutation.

### Requirement 3: Order-flow state and alignment classification

**User Story:** As the Deep_Quant_Agent, I want a categorical order-flow state and an alignment with my proposed direction, so that I can act on the flow.

#### Acceptance Criteria

1. THE Order_Flow_Calculator SHALL classify the Order_Flow_State as exactly one of `buying`, `selling`, or `balanced` by comparing the available order-flow signals (Tick_OFI when available, otherwise the candle-derived proxies) against the configured pressure thresholds.
2. WHERE a usable Tick_OFI is available, THE Order_Flow_Calculator SHALL prioritize the Tick_OFI over the candle-derived proxies when classifying the Order_Flow_State.
3. THE Order_Flow_Calculator SHALL derive an Alignment of exactly one of `aligned`, `misaligned`, or `neutral` from the Order_Flow_State and a provided proposed trade direction, such that every combination maps to exactly one Alignment value.
4. WHEN no proposed trade direction is provided, THE Order_Flow_Calculator SHALL report the Alignment as `neutral` while still returning the Order_Flow_State and the measures.
5. THE Order_Flow_Label SHALL include a flag indicating whether live tick data contributed to the classification or only the candle-derived proxies were used.

### Requirement 4: Calculator robustness, bounds, and determinism

**User Story:** As a quantitative developer, I want the calculator to handle degenerate and insufficient inputs deterministically, so that it can be contract-validated and property-tested in isolation.

#### Acceptance Criteria

1. IF the candle sequence contains fewer candles than the configured minimum required for the longest proxy lookback, THEN THE Order_Flow_Calculator SHALL return an Unavailable_Marker whose reason identifies the insufficient-data condition and includes the count of valid candles received and the configured minimum required, without raising an exception.
2. WHEN a candle or tick contains a non-finite or non-numeric field, THE Order_Flow_Calculator SHALL exclude that candle or tick from all computations without raising an exception.
3. WHILE every computed Order_Flow_Proxy_Measure is finite, THE Order_Flow_Calculator SHALL include each named measure as a finite numeric value in the Order_Flow_Label.
4. WHERE a measure is defined on a bounded range (the buying-pressure ratio in [0.0, 1.0] and the Tick_OFI in [-1.0, 1.0]), THE Order_Flow_Calculator SHALL clamp that measure to the nearest boundary value whenever its computed value would otherwise fall outside the range.
5. IF a measure cannot be computed because its denominator is zero, THEN THE Order_Flow_Calculator SHALL represent that measure as null in the Order_Flow_Label and SHALL NOT raise an exception.
6. IF every candle-derived proxy is null AND the Tick_OFI is unavailable, THEN THE Order_Flow_Calculator SHALL return an Unavailable_Marker rather than an Order_Flow_Label.

### Requirement 5: Order Flow Tool and contract

**User Story:** As the Deep_Quant_Agent, I want a `get_order_flow` tool that returns a validated order-flow result, so that I can consult the tape during analysis.

#### Acceptance Criteria

1. THE Order_Flow_Tool SHALL be exposed to the Deep_Quant_Agent as an `@tool`-decorated function named `get_order_flow` following the existing tool pattern in `tools.py`.
2. THE Order_Flow_Tool SHALL accept a `symbol` argument, a `timeframe` argument, and an optional `proposed_direction` argument.
3. IF the `symbol` argument is empty or whitespace-only, OR the `timeframe` argument is not one of the supported timeframes, THEN THE Order_Flow_Tool SHALL return a structured error result and SHALL NOT raise an exception.
4. THE Order_Flow_Tool SHALL fetch the symbol candles from the Rust_Tool_Server for the proxy layer and SHALL attempt to read recent ticks for the symbol from the Live_Ticks_Source for the Tick_OFI layer.
5. WHEN the Order_Flow_Tool successfully computes order flow, THE Order_Flow_Tool SHALL return a result containing `order_flow_state` (one of `buying`, `selling`, `balanced`), `alignment` (one of `aligned`, `misaligned`, `neutral`), the named Order_Flow_Proxy_Measures each present as a finite number or null, the Tick_OFI as a finite number or an unavailable indication, and the live-tick-contributed flag.
6. WHEN a `get_order_flow` result conforms to the Tool_Result_Contract, THE `validate_contract` function SHALL return that result unchanged.
7. IF a `get_order_flow` result does not conform to the Tool_Result_Contract, THEN THE `validate_contract` function SHALL return a structured `{"error", "contract_violation"}` result that identifies the offending field.
8. WHEN a `get_order_flow` result carries an Unavailable_Marker, THE `validate_contract` function SHALL pass it through unchanged.
9. THE `validate_contract` function SHALL NOT raise an exception while validating a `get_order_flow` result, including when the result is malformed, missing fields, or not an object.

### Requirement 6: Graceful degradation

**User Story:** As a trader relying on the agent, I want unavailable order flow (and specifically an absent live tick stream) to be a non-blocking missing input, so that its absence never fabricates data or falsely blocks a decision.

#### Acceptance Criteria

1. IF the Live_Ticks_Source is unreachable, returns no rows for the symbol, or yields an untrustworthy Tick_OFI, THEN THE Order_Flow_Tool SHALL report the Tick_OFI as unavailable and SHALL still return the candle-derived proxy layer when it is computable.
2. IF the Order_Flow_Tool cannot retrieve the symbol candles within its configured retrieval timeout, THEN THE Order_Flow_Tool SHALL return an Unavailable_Marker whose reason identifies the retrieval-failure cause.
3. WHEN the Order_Flow_Tool returns an Unavailable_Marker, THE Order_Flow_Tool SHALL omit `order_flow_state` and `alignment` rather than populate them with fabricated values.
4. WHEN an order-flow result is unavailable, THE Deep_Quant_Agent SHALL treat it as a missing optional input, SHALL proceed with the remaining analysis, and SHALL NOT abort, fail, or block the decision solely because order flow is unavailable.
5. IF the Order_Flow_Tool encounters any error while retrieving or processing candle or tick data, THEN THE Order_Flow_Tool SHALL return an Unavailable_Marker and SHALL NOT propagate an exception into the Deep_Quant_Agent loop.
6. WHEN the Tick_OFI is unavailable but the candle-derived proxy layer is computable, THE Order_Flow_Tool SHALL return a usable Order_Flow_Label with the live-tick-contributed flag set false, rather than an Unavailable_Marker.

### Requirement 7: Graph wiring of the Order Flow Tool

**User Story:** As a quantitative developer, I want the order-flow tool registered in the graph, so that the agent can call it and the loop control treats it consistently with other market-data tools.

#### Acceptance Criteria

1. THE Deep_Quant_Agent SHALL include `get_order_flow` in the `tools` list bound to the model in `graph.py`.
2. THE Deep_Quant_Agent SHALL include `get_order_flow` in `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_order_flow` call is classified as a valid (not invalid-tool) call.
3. THE Deep_Quant_Agent SHALL include `get_order_flow` in `MARKET_DATA_TOOL_NAMES` in `graph.py`.
4. WHEN a `get_order_flow` result is usable data (neither an error result nor an explicit Unavailable_Marker), THE Deep_Quant_Agent SHALL set the `market_data_seen` flag.
5. IF a `get_order_flow` result is an error result or an Unavailable_Marker, THEN THE Deep_Quant_Agent SHALL NOT set the `market_data_seen` flag on the basis of that result.

### Requirement 8: Agent prompt integration

**User Story:** As a trader, I want the agent to read the tape and avoid trading against order flow, so that entries are backed by pressure rather than fighting it.

#### Acceptance Criteria

1. THE `DEEP_QUANT_SYSTEM_PROMPT` order_of_operations SHALL instruct the Deep_Quant_Agent to call `get_order_flow` for the symbol and the timeframe currently under analysis.
2. THE `DEEP_QUANT_SYSTEM_PROMPT` self_verification_protocol SHALL instruct the Deep_Quant_Agent to check the Order_Flow_State for Alignment before committing a directional trade, where a directional trade is a BUY or SELL decision (excluding HOLD).
3. THE `DEEP_QUANT_SYSTEM_PROMPT` SHALL instruct the Deep_Quant_Agent that, when the Alignment is `misaligned` (for example a BUY into net `selling` flow, or a SELL into net `buying` flow), it must take exactly one of the following actions: lower its conviction score, wait for a better setup, or HOLD.
4. THE `DEEP_QUANT_SYSTEM_PROMPT` setup_validation_disclosure SHALL instruct the Deep_Quant_Agent to state the Order_Flow_State, the Alignment, and whether live tick data contributed, in its setup_validation.
5. THE VERIFY-mode `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to consult `get_order_flow` while verifying a user-proposed trade and to include an explicit warning statement when the user-proposed trade is `misaligned` with order flow.
6. WHEN order flow is unavailable, THE `DEEP_QUANT_SYSTEM_PROMPT` and the `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to note order flow as unavailable and proceed, rather than block the decision.

### Requirement 9: Order flow in the defensibility record

**User Story:** As a trader auditing committed trades, I want every committed decision to cite the order-flow read, so that I can see the pressure context that informed the trade.

#### Acceptance Criteria

1. WHEN `build_defensibility_record` assembles a record, THE Deep_Quant_Agent SHALL include an order-flow entry containing the Order_Flow_State, the Alignment, the named measures, the Tick_OFI (or its unavailable indication), and the live-tick-contributed flag, taken from the most recent `get_order_flow` result present in message history.
2. THE Deep_Quant_Agent SHALL populate the order-flow entry using only values returned by the Order_Flow_Tool and SHALL NOT substitute or infer any value not present in that result.
3. IF no `get_order_flow` result is present in message history, THEN THE Deep_Quant_Agent SHALL record the order-flow entry as unavailable and SHALL NOT populate its fields with substitute values.
4. WHEN the Alignment in the most recent `get_order_flow` result is `misaligned` and the committed Declared_Trade has action BUY or SELL, THE Deep_Quant_Agent SHALL include in the Defensibility_Record an explicit statement that the committed trade is taken against the prevailing order flow.

### Requirement 10: Order flow as a stream verification step

**User Story:** As a user watching the agent stream, I want the order-flow check surfaced as a verification step, so that I can see the tape was evaluated before the trade was finalized.

#### Acceptance Criteria

1. WHEN building Verification_Steps for a decision, THE event stream SHALL emit exactly one order-flow `VERIFICATION_STEP` carrying a stable check identifier and an outcome value that is one of `pass`, `fail`, `informational`, or `not-evaluable`.
2. WHEN the Defensibility_Record order-flow Alignment is `aligned`, THE order-flow Verification_Step SHALL report an outcome of `pass`.
3. WHEN the Defensibility_Record order-flow Alignment is `misaligned`, THE order-flow Verification_Step SHALL report an outcome of `fail`.
4. WHEN the Defensibility_Record order-flow Alignment is `neutral`, THE order-flow Verification_Step SHALL report an outcome of `informational`.
5. IF the order-flow entry is unavailable in the Defensibility_Record, THEN THE order-flow Verification_Step SHALL report an outcome of `not-evaluable` with an indication that order flow is unavailable, and SHALL NOT substitute a fabricated Alignment.
6. WHEN emitting decision events for a run, THE event stream SHALL order the order-flow Verification_Step before the `DECISION` event of that run.

### Requirement 11: Journal setup-fingerprint extension

**User Story:** As a quantitative developer, I want order flow added to the journal setup fingerprint, so that per-order-flow win-rate and expectancy are measurable.

#### Acceptance Criteria

1. WHEN `derive_setup_tags` derives a Setup_Fingerprint, THE Trade_Journal SHALL append exactly one order-flow dimension tag of the form `of:<value>`, where `<value>` is read from the order-flow entry recorded in the decision's defensibility record, and SHALL place this tag at a fixed position in the tag sequence so that the resulting `setup_key` is deterministic for identical inputs.
2. IF the decision's defensibility record carries no order-flow entry, OR the recorded value is empty or not one of the predefined enumeration values, THEN THE Trade_Journal SHALL append the tag `of:unknown`.
3. THE Trade_Journal SHALL constrain the order-flow tag to exactly one value drawn from a fixed, predefined enumeration containing at most 8 distinct values (including `unknown`), so that the order-flow-extended `setup_key` remains low-cardinality.
4. WHEN aggregating statistics, THE Trade_Journal SHALL group scored (win or loss) trades by the order-flow-extended `setup_key` and SHALL report, for each group, a win-rate as the fraction of scored trades that are wins (a value from 0.0 to 1.0) and an expectancy as the mean R-multiple of the group's scored trades.
5. IF an order-flow-extended `setup_key` group contains fewer scored trades than the configured low-sample threshold, THEN THE Trade_Journal SHALL flag that group's reported statistics as a weak prior.

### Requirement 12: Backtest with-filter / without-filter comparison (proxy layer)

**User Story:** As a quantitative developer, I want the backtest seeder to measure performance with versus without the candle-derived order-flow filter, so that I can prove with numbers whether the filter improves expectancy.

#### Acceptance Criteria

1. WHEN the Backtest_Seeder generates a signal, THE Backtest_Seeder SHALL classify that signal's order flow by invoking the same Order_Flow_Calculator proxy functions used by the Order_Flow_Tool, computing the proxy-only Order_Flow_Label using only candles at or before the signal's candle timestamp and no later candles.
2. THE Backtest_Seeder SHALL compute order flow for backtested signals from the candle-derived proxy layer only, and SHALL NOT require live tick data, because tick-level history is not available in the candle archive.
3. WHERE the order-flow filter is enabled, WHEN a generated signal's Alignment is `misaligned` for the signal's direction, THE Backtest_Seeder SHALL exclude that signal from the with-filter seeded trade set.
4. WHEN the Backtest_Seeder seeds a trade, THE Backtest_Seeder SHALL label that trade with its proxy-derived Order_Flow_State and Alignment so that per-order-flow win-rate and expectancy are measurable in the Trade_Journal.
5. WHEN run in comparison mode, THE Backtest_Seeder SHALL report, for both the with-filter run and the without-filter run computed over the identical candle history and identical setup rules, the win-rate defined as winning closed trades divided by closed trades and the expectancy defined as the mean realized R-multiple per closed trade, reporting not-applicable when a run produced zero closed trades.
6. WHERE the order-flow filter is enabled, IF a generated signal's proxy order-flow result is an Unavailable_Marker, THEN THE Backtest_Seeder SHALL retain that signal in the with-filter seeded trade set.
7. THE Backtest_Seeder SHALL reuse the same Order_Flow_Calculator proxy functions rather than reimplementing the order-flow math.

### Requirement 13: Configurable parameters

**User Story:** As a quantitative developer, I want order-flow parameters configurable via environment variables, so that they are tunable rather than hardcoded magic numbers.

#### Acceptance Criteria

1. THE Order_Flow_Calculator SHALL read each of the following parameters from its own environment variable: the proxy lookback period, the minimum required candle count, the buying-pressure threshold and the selling-pressure threshold, the Tick_OFI buying/selling thresholds, and the minimum required tick count for a trustworthy Tick_OFI, each with a documented expected type and valid range.
2. WHERE a parameter environment variable is unset or holds an empty string, THE Order_Flow_Calculator SHALL apply the documented default value for that parameter.
3. IF a parameter environment variable holds a value that cannot be parsed as the expected numeric type, THEN THE Order_Flow_Calculator SHALL apply the documented default value for that parameter and SHALL NOT raise an exception.
4. IF a parameter environment variable holds a value that parses as the expected numeric type but falls outside the valid range defined for that parameter, THEN THE Order_Flow_Calculator SHALL apply the documented default value for that parameter and SHALL NOT raise an exception.
5. WHEN resolving the buying-pressure and selling-pressure thresholds, THE Order_Flow_Calculator SHALL require the selling threshold to be strictly less than the buying threshold, and IF this ordering does not hold, THEN THE Order_Flow_Calculator SHALL apply the documented default values for both thresholds without raising an exception.
6. WHEN resolving parameters for the Order_Flow_Tool path and for the Backtest_Seeder path from identical environment variable values, THE Order_Flow_Calculator SHALL apply identical resolved parameter values and identical documented defaults across both paths.

### Requirement 14: Scope boundary — filter, not generator

**User Story:** As a trader, I want order flow to remain a filter, so that it never fabricates data or becomes a trade generator.

#### Acceptance Criteria

1. THE Order_Flow_Calculator SHALL produce only an Order_Flow_Label or an Unavailable_Marker, and SHALL NOT emit, recommend, or score a BUY, SELL, or HOLD decision.
2. THE Order_Flow_Tool SHALL derive its result exclusively from OHLCV candle data and `live_ticks` tick data and the configured parameters, and SHALL NOT consume options-chain data or any other data source.
3. IF the Order_Flow_State confirms a direction, THEN the order-flow context SHALL NOT, of itself, commit, generate, or trigger a trade; a trade SHALL be committed only by an explicit Deep_Quant_Agent decision.
4. THE order-flow context SHALL NOT modify, override, or replace a committed Deep_Quant_Agent decision; its effect SHALL be limited to system-prompt guidance and defensibility surfacing.
5. WHILE the Alignment is `misaligned` or `neutral`, the order-flow context SHALL NOT, of itself, block a trade that the Deep_Quant_Agent decides to commit.
6. THE Order_Flow_Calculator SHALL NOT fabricate a neutral Tick_OFI when live tick data is unavailable; it SHALL report the Tick_OFI as unavailable instead.
