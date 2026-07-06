# Requirements Document

## Introduction

The Deep Quant agent ("Alpha-Quant") is a ReAct loop in which an LLM calls quantitative tools and commits trade decisions (BUY / SELL / HOLD) that are recorded, scored, and aggregated by a SQLite Trade_Journal. The agent currently analyzes each symbol in isolation — it has no awareness of the broader market. A veteran trader's principle is the opposite: trade the **strongest** stock **with** the market, never fight the index, and avoid buying laggards in a falling market or shorting leaders in a rising one. The same multi-timeframe backtest that motivated the regime gate showed the rule set decaying toward break-even on fast intraday timeframes; a large share of those losing trades are directional bets taken against the prevailing index move or in names that are underperforming their benchmark.

This feature adds **Relative Strength & Index Context**: a cheap, deterministic, pure-math calculator that, from candle data already available, measures the benchmark index's own direction, the symbol's relative strength versus that benchmark, and the symbol↔index correlation/beta, then labels a Relative_Strength_State and an Alignment of a proposed trade direction with that context. It is exposed to the agent as a new Analysis_Tool (`get_relative_strength`) that mirrors the established tool pattern exactly (contract-validated on receipt by `validate_contract`, graceful "unavailable" degradation that never blocks a decision). The result is wired into the agent's tool list and system prompts, captured in the defensibility record, surfaced as a verification step in the event stream, added as a new dimension of the Trade_Journal setup fingerprint, and integrated into the Backtest_Seeder so the journal can measure win-rate and expectancy WITH versus WITHOUT requiring index/relative-strength alignment. All parameters — relative-strength lookbacks, correlation/beta window, leader/laggard cutoffs, and the symbol→benchmark map — are configurable via environment variables with documented defaults.

This feature reuses the architecture established by the **regime-detection-gate** feature (pure-Python deterministic classifier + configurable thresholds + tool + contract + graph/prompt/defensibility/stream/journal/backtest integration) so the two compose cleanly: the regime says *when* to trade, relative strength says *what* to trade.

The Relative Strength & Index Context is a **filter / context aid, not a trade generator**. It never fabricates data and never, on its own, forces, blocks, or overrides a trade.

## Glossary

- **Deep_Quant_Agent**: The LangGraph ReAct agent ("Alpha-Quant") in `agents/deep-quant-loop/` that calls Analysis_Tools and commits decisions.
- **Benchmark_Index**: The market index used as the comparison baseline for a symbol (for example `NIFTY 50` or `BANKNIFTY`), whose candles are available in the same data source as the symbol's candles.
- **Benchmark_Map**: The configurable mapping from a symbol to its Benchmark_Index, with a documented default mapping and a documented fallback when no mapped benchmark candles are available.
- **Relative_Strength_Calculator**: The pure-Python, deterministic component that maps a symbol's candle sequence, its Benchmark_Index's candle sequence, and a configuration to a Relative_Strength_Label, or to an Unavailable_Marker.
- **Relative_Strength_Label**: The structured output of the Relative_Strength_Calculator, comprising the Index_Direction, the Relative_Strength_State, the named Relative_Strength_Measures, an Alignment assessment, and the Benchmark_Index used.
- **Relative_Strength_Measure**: A named, deterministic scalar computed from the symbol and benchmark candles (for example the RS ratio symbol/index and its slope, the relative return over a lookback, the correlation, and the beta).
- **Relative_Strength_State**: A categorical classification of the symbol versus its Benchmark_Index, one of `leader`, `inline`, or `laggard`.
- **Index_Direction**: A categorical classification of the Benchmark_Index's own trend, one of `up`, `down`, or `flat`.
- **Alignment**: A derived classification stating whether a proposed trade direction agrees with the Index_Direction and the Relative_Strength_State, one of `aligned`, `misaligned`, or `neutral`.
- **Relative_Strength_Tool**: The `get_relative_strength` Analysis_Tool exposed to the Deep_Quant_Agent, which produces a Relative_Strength_Label conforming to its Tool_Result_Contract.
- **Tool_Result_Contract**: The structural contract a tool result must satisfy, re-validated on receipt by `validate_contract` in `tools.py`.
- **Unavailable_Marker**: An honest, non-fatal result indicating relative strength could not be computed (for example a missing benchmark or insufficient candles), mirroring the existing graceful-degradation pattern (`{"unavailable": true, "reason": ...}`).
- **Defensibility_Record**: The evidence record assembled by `build_defensibility_record` in `graph.py` and attached to every committed decision.
- **Declared_Trade**: The committed BUY / SELL / HOLD decision produced by the Deep_Quant_Agent for an analysis session.
- **Trade_Journal**: The SQLite measurement/feedback store (`journal.py`) that records, scores, and aggregates decisions.
- **Setup_Fingerprint**: The coarse, low-cardinality setup tag set derived by `journal.derive_setup_tags`, used to group trades for per-setup statistics.
- **Backtest_Seeder**: The `backtest.py` module that replays historical candles through deterministic rule-based setups and seeds the Trade_Journal tagged `source='backtest'`.
- **Verification_Step**: A `VERIFICATION_STEP` event emitted by `stream_events.py` describing a self-verification / risk-manager check and its outcome.
- **Rust_Tool_Server**: The Rust service at `http://localhost:8084` that serves authoritative quantitative computations, including `/tools/get_candles`.

## Requirements

### Requirement 1: Relative-strength and index-context math

**User Story:** As a quantitative developer, I want a deterministic pure-math relative-strength calculator computed from a symbol's candles and its benchmark's candles, so that the system can place a trade in market context without any new external data source.

#### Acceptance Criteria

1. THE Relative_Strength_Calculator SHALL compute the Relative_Strength_Label exclusively from a provided symbol candle sequence, a provided Benchmark_Index candle sequence, and a provided configuration, performing zero network calls and reading zero data sources other than the three provided inputs.
2. WHEN invoked two or more times with a symbol candle sequence, a benchmark candle sequence, and a configuration whose values are each element-wise identical, THE Relative_Strength_Calculator SHALL return an Index_Direction, a Relative_Strength_State, an Alignment, and a Relative_Strength_Label that are each identical across all such invocations.
3. THE Relative_Strength_Calculator SHALL compute a relative-strength-ratio Relative_Strength_Measure (the symbol price divided by the benchmark price) and the slope of that ratio over the configured relative-strength lookback period.
4. THE Relative_Strength_Calculator SHALL compute a relative-return Relative_Strength_Measure equal to the symbol's return minus the benchmark's return over the configured relative-strength lookback period.
5. THE Relative_Strength_Calculator SHALL compute a correlation Relative_Strength_Measure and a beta Relative_Strength_Measure of the symbol versus the Benchmark_Index over the configured correlation/beta window, computed from time-aligned candles common to both sequences.
6. THE Relative_Strength_Calculator SHALL classify the Index_Direction as exactly one of `up`, `down`, or `flat` from the Benchmark_Index candle sequence and the configured thresholds.
7. THE Relative_Strength_Calculator SHALL classify the Relative_Strength_State as exactly one of `leader`, `inline`, or `laggard` by comparing the relative-strength Relative_Strength_Measures against the configured leader/laggard cutoffs.
8. THE Relative_Strength_Calculator SHALL derive an Alignment of exactly one of `aligned`, `misaligned`, or `neutral` from the Index_Direction, the Relative_Strength_State, and a provided proposed trade direction, such that every combination maps to exactly one Alignment value.
9. WHEN no proposed trade direction is provided, THE Relative_Strength_Calculator SHALL still return the Index_Direction, the Relative_Strength_State, and the Relative_Strength_Measures, and SHALL report the Alignment as `neutral`.
10. THE Relative_Strength_Calculator SHALL implement all Relative_Strength_Calculator functions as pure functions that produce no observable change to their input candle sequences or configuration.

### Requirement 2: Symbol-to-benchmark mapping

**User Story:** As a quantitative developer, I want a configurable symbol-to-benchmark mapping with a sensible default, so that each symbol is compared against the correct index.

#### Acceptance Criteria

1. THE Benchmark_Map SHALL resolve a Benchmark_Index for a given symbol using a configurable mapping with documented default entries.
2. WHERE a symbol has no explicit Benchmark_Map entry, THE Benchmark_Map SHALL resolve the symbol to the documented default Benchmark_Index.
3. THE Benchmark_Map SHALL be extensible to additional symbol→benchmark entries via configuration without code changes, while requiring documented defaults only for the Benchmark_Indices whose candles are available in the data source.
4. IF the resolved Benchmark_Index has no available candle data, THEN THE Relative_Strength_Calculator path SHALL return an Unavailable_Marker whose reason identifies the missing benchmark, rather than fabricating relative-strength values.

### Requirement 3: Calculator robustness, bounds, and determinism

**User Story:** As a quantitative developer, I want the calculator to handle degenerate, misaligned, and insufficient inputs deterministically, so that it can be contract-validated and property-tested in isolation.

#### Acceptance Criteria

1. IF the count of time-aligned candles common to the symbol and the Benchmark_Index is fewer than the configured minimum required for the longest lookback, THEN THE Relative_Strength_Calculator SHALL return an Unavailable_Marker whose reason identifies the insufficient-data condition and includes the count of aligned candles available and the configured minimum required, without raising an exception.
2. WHEN a candle in either sequence contains a non-finite or non-numeric OHLCV field, THE Relative_Strength_Calculator SHALL exclude that candle from all Relative_Strength_Measure computations without raising an exception.
3. WHILE every computed Relative_Strength_Measure is finite, THE Relative_Strength_Calculator SHALL include each named Relative_Strength_Measure as a finite numeric value in the Relative_Strength_Label.
4. WHERE a Relative_Strength_Measure is defined on a bounded range (for example a correlation in [-1.0, 1.0]), THE Relative_Strength_Calculator SHALL clamp that measure to the nearest boundary value whenever its computed value would otherwise fall outside the range.
5. IF a Relative_Strength_Measure cannot be computed because its denominator is zero (for example a zero benchmark price or zero variance over the window), THEN THE Relative_Strength_Calculator SHALL represent that measure as null in the Relative_Strength_Label and SHALL NOT raise an exception.
6. IF every required Relative_Strength_Measure is null because none could be computed, THEN THE Relative_Strength_Calculator SHALL return an Unavailable_Marker rather than a Relative_Strength_Label.
7. THE Relative_Strength_Calculator SHALL time-align the symbol and benchmark candles by their timestamps before computing correlation, beta, and relative return, so that mismatched-length or mismatched-timestamp sequences do not corrupt the measures.

### Requirement 4: Relative Strength Tool and contract

**User Story:** As the Deep_Quant_Agent, I want a `get_relative_strength` tool that returns a validated relative-strength result, so that I can consult market context during analysis.

#### Acceptance Criteria

1. THE Relative_Strength_Tool SHALL be exposed to the Deep_Quant_Agent as an `@tool`-decorated function named `get_relative_strength` following the existing tool pattern in `tools.py`.
2. THE Relative_Strength_Tool SHALL accept a `symbol` argument, an optional explicit `benchmark` argument, and a `timeframe` argument, and SHALL resolve the Benchmark_Index via the Benchmark_Map when no explicit benchmark is provided.
3. IF the `symbol` argument is empty or whitespace-only, OR the `timeframe` argument is not one of the supported timeframes, THEN THE Relative_Strength_Tool SHALL return a structured error result and SHALL NOT raise an exception.
4. THE Relative_Strength_Tool SHALL fetch both the symbol candles and the Benchmark_Index candles from the Rust_Tool_Server.
5. WHEN the Relative_Strength_Tool successfully computes relative strength, THE Relative_Strength_Tool SHALL return a result containing `index_direction` (one of `up`, `down`, `flat`), `relative_strength_state` (one of `leader`, `inline`, `laggard`), `alignment` (one of `aligned`, `misaligned`, `neutral`), the named Relative_Strength_Measures each present as a finite number or null, and the resolved Benchmark_Index.
6. WHEN a `get_relative_strength` result conforms to the Tool_Result_Contract, THE `validate_contract` function in `tools.py` SHALL return that result unchanged.
7. IF a `get_relative_strength` result does not conform to the Tool_Result_Contract, THEN THE `validate_contract` function SHALL return a structured `{"error", "contract_violation"}` result that identifies the offending field.
8. WHEN a `get_relative_strength` result carries an Unavailable_Marker, THE `validate_contract` function SHALL pass it through unchanged.
9. THE `validate_contract` function SHALL NOT raise an exception while validating a `get_relative_strength` result, including when the result is malformed, missing fields, or not an object.

### Requirement 5: Graceful degradation of the Relative Strength Tool

**User Story:** As a trader relying on the agent, I want an unavailable relative-strength result to be a non-blocking missing input, so that its absence never fabricates data or falsely blocks a decision.

#### Acceptance Criteria

1. IF the Relative_Strength_Tool cannot retrieve the symbol candles or the Benchmark_Index candles within its configured retrieval timeout, THEN THE Relative_Strength_Tool SHALL return an Unavailable_Marker whose reason field identifies the retrieval-failure cause.
2. IF the aligned candle data is insufficient to compute relative strength, THEN THE Relative_Strength_Tool SHALL return an Unavailable_Marker whose reason cites insufficient data and states the number of aligned candles available and the number required.
3. WHEN the Relative_Strength_Tool returns an Unavailable_Marker, THE Relative_Strength_Tool SHALL omit Index_Direction, Relative_Strength_State, and Alignment rather than populate them with default, placeholder, or otherwise fabricated values.
4. WHEN a relative-strength result is unavailable, THE Deep_Quant_Agent SHALL treat it as a missing optional input, SHALL proceed with the remaining analysis, and SHALL NOT abort, fail, or block the decision solely because relative strength is unavailable.
5. IF the Relative_Strength_Tool encounters any error while retrieving or processing candle data, THEN THE Relative_Strength_Tool SHALL return an Unavailable_Marker and SHALL NOT propagate an exception into the Deep_Quant_Agent loop.

### Requirement 6: Graph wiring of the Relative Strength Tool

**User Story:** As a quantitative developer, I want the relative-strength tool registered in the graph, so that the agent can call it and the loop control treats it consistently with other market-data tools.

#### Acceptance Criteria

1. THE Deep_Quant_Agent SHALL include `get_relative_strength` in the `tools` list bound to the model in `graph.py`.
2. THE Deep_Quant_Agent SHALL include `get_relative_strength` in `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_relative_strength` call is classified as a valid (not invalid-tool) call.
3. THE Deep_Quant_Agent SHALL include `get_relative_strength` in `MARKET_DATA_TOOL_NAMES` in `graph.py`.
4. WHEN a `get_relative_strength` result is usable data (neither an error result nor an explicit Unavailable_Marker), THE Deep_Quant_Agent SHALL set the `market_data_seen` flag.
5. IF a `get_relative_strength` result is an error result or an Unavailable_Marker, THEN THE Deep_Quant_Agent SHALL NOT set the `market_data_seen` flag on the basis of that result.

### Requirement 7: Agent prompt integration of relative strength

**User Story:** As a trader, I want the agent to consult market context and avoid fighting the index, so that it trades the strongest names with the market.

#### Acceptance Criteria

1. THE `DEEP_QUANT_SYSTEM_PROMPT` order_of_operations SHALL instruct the Deep_Quant_Agent to call `get_relative_strength` for the symbol and the timeframe currently under analysis.
2. THE `DEEP_QUANT_SYSTEM_PROMPT` self_verification_protocol SHALL instruct the Deep_Quant_Agent to check the Index_Direction and the Relative_Strength_State for Alignment before committing a directional trade, where a directional trade is a BUY or SELL decision (excluding HOLD).
3. THE `DEEP_QUANT_SYSTEM_PROMPT` SHALL instruct the Deep_Quant_Agent that, when the Alignment is `misaligned` (for example a BUY in a `laggard` against a `down` index, or a SELL in a `leader` against an `up` index), it must take exactly one of the following actions: lower its conviction score, wait for a better setup, or HOLD.
4. THE `DEEP_QUANT_SYSTEM_PROMPT` setup_validation_disclosure SHALL instruct the Deep_Quant_Agent to state the Index_Direction, the Relative_Strength_State, and the Alignment — taken from the `get_relative_strength` result — in its setup_validation.
5. THE VERIFY-mode `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to consult `get_relative_strength` while verifying a user-proposed trade and to include an explicit warning statement when the user-proposed trade is `misaligned` with the index/relative-strength context.
6. WHEN relative strength is unavailable, THE `DEEP_QUANT_SYSTEM_PROMPT` and the `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to note relative strength as unavailable and proceed, rather than block the decision.

### Requirement 8: Relative strength in the defensibility record

**User Story:** As a trader auditing committed trades, I want every committed decision to cite the market context, so that I can see the index direction and relative strength that informed the trade.

#### Acceptance Criteria

1. WHEN `build_defensibility_record` assembles a record, THE Deep_Quant_Agent SHALL include a relative-strength entry containing the Index_Direction, the Relative_Strength_State, the Alignment, the named Relative_Strength_Measures, and the Benchmark_Index, taken from the most recent `get_relative_strength` result present in message history.
2. THE Deep_Quant_Agent SHALL populate the relative-strength entry using only values returned by the Relative_Strength_Tool and SHALL NOT substitute or infer any value not present in that result.
3. IF no `get_relative_strength` result is present in message history when `build_defensibility_record` assembles a record, THEN THE Deep_Quant_Agent SHALL record the relative-strength entry as unavailable and SHALL NOT populate its fields with substitute values.
4. WHEN the Alignment in the most recent `get_relative_strength` result is `misaligned` and the committed Declared_Trade has action BUY or SELL, THE Deep_Quant_Agent SHALL include in the Defensibility_Record an explicit statement that the committed trade fights the index or trades a laggard against its benchmark.

### Requirement 9: Relative strength as a stream verification step

**User Story:** As a user watching the agent stream, I want the relative-strength check surfaced as a verification step, so that I can see the market context was evaluated before the trade was finalized.

#### Acceptance Criteria

1. WHEN building Verification_Steps for a decision, THE event stream SHALL emit exactly one relative-strength `VERIFICATION_STEP` carrying a stable check identifier and an outcome value that is one of `pass`, `fail`, `informational`, or `not-evaluable`.
2. WHEN the Defensibility_Record relative-strength Alignment is `aligned`, THE relative-strength Verification_Step SHALL report an outcome of `pass`.
3. WHEN the Defensibility_Record relative-strength Alignment is `misaligned`, THE relative-strength Verification_Step SHALL report an outcome of `fail`.
4. WHEN the Defensibility_Record relative-strength Alignment is `neutral`, THE relative-strength Verification_Step SHALL report an outcome of `informational`.
5. IF the relative-strength entry is unavailable in the Defensibility_Record, THEN THE relative-strength Verification_Step SHALL report an outcome of `not-evaluable` with an indication that relative strength is unavailable, and SHALL NOT substitute a fabricated Alignment.
6. WHEN emitting decision events for a run, THE event stream SHALL order the relative-strength Verification_Step before the `DECISION` event of that run.

### Requirement 10: Journal setup-fingerprint extension

**User Story:** As a quantitative developer, I want relative strength added to the journal setup fingerprint, so that per-relative-strength win-rate and expectancy are measurable.

#### Acceptance Criteria

1. WHEN `derive_setup_tags` derives a Setup_Fingerprint, THE Trade_Journal SHALL append exactly one relative-strength dimension tag of the form `rs:<value>`, where `<value>` is read from the relative-strength entry recorded in the decision's defensibility record, and SHALL place this tag at a fixed position in the tag sequence so that the resulting `setup_key` is deterministic for identical inputs.
2. IF the decision's defensibility record carries no relative-strength entry, OR the recorded value is empty or not one of the predefined relative-strength enumeration values, THEN THE Trade_Journal SHALL append the tag `rs:unknown`.
3. THE Trade_Journal SHALL constrain the relative-strength tag to exactly one value drawn from a fixed, predefined enumeration containing at most 8 distinct values (including `unknown`), so that the relative-strength-extended `setup_key` remains low-cardinality.
4. WHEN aggregating statistics, THE Trade_Journal SHALL group scored (win or loss) trades by the relative-strength-extended `setup_key` and SHALL report, for each group, a win-rate as the fraction of scored trades that are wins (a value from 0.0 to 1.0) and an expectancy as the mean R-multiple of the group's scored trades.
5. IF a relative-strength-extended `setup_key` group contains fewer scored trades than the configured low-sample threshold, THEN THE Trade_Journal SHALL flag that group's reported statistics as a weak prior.

### Requirement 11: Backtest with-filter / without-filter comparison

**User Story:** As a quantitative developer, I want the backtest seeder to measure performance with versus without requiring index/relative-strength alignment, so that I can prove with numbers whether the filter improves expectancy.

#### Acceptance Criteria

1. WHEN the Backtest_Seeder generates a signal, THE Backtest_Seeder SHALL classify that signal's relative strength by invoking the same Relative_Strength_Calculator functions used by the Relative_Strength_Tool, computing the Relative_Strength_Label using only symbol candles and Benchmark_Index candles at or before the signal's candle timestamp and no later candles.
2. WHERE the relative-strength filter is enabled, WHEN a generated signal's Alignment is `misaligned` for the signal's direction, THE Backtest_Seeder SHALL exclude that signal from the with-filter seeded trade set.
3. WHEN the Backtest_Seeder seeds a trade, THE Backtest_Seeder SHALL label that trade with its Relative_Strength_Label comprising the Index_Direction, the Relative_Strength_State, and the Alignment, so that per-relative-strength win-rate and expectancy are measurable in the Trade_Journal.
4. WHEN run in comparison mode, THE Backtest_Seeder SHALL report, for both the with-filter run and the without-filter run computed over the identical candle history and identical setup rules, the win-rate defined as winning closed trades divided by closed trades and the expectancy defined as the mean realized R-multiple per closed trade.
5. THE Backtest_Seeder SHALL reuse the same Relative_Strength_Calculator functions rather than reimplementing the relative-strength math.
6. WHERE the relative-strength filter is enabled, IF a generated signal's relative-strength result is an Unavailable_Marker, THEN THE Backtest_Seeder SHALL retain that signal in the with-filter seeded trade set and SHALL NOT exclude it on the basis of relative strength.
7. IF a comparison-mode run produces zero closed trades, THEN THE Backtest_Seeder SHALL report that run's win-rate and expectancy as not-applicable rather than computing a division by zero.

### Requirement 12: Configurable parameters

**User Story:** As a quantitative developer, I want relative-strength parameters configurable via environment variables, so that they are tunable rather than hardcoded magic numbers.

#### Acceptance Criteria

1. THE Relative_Strength_Calculator SHALL read each of the following parameters from its own environment variable: the relative-strength lookback period, the correlation/beta window, the leader cutoff and the laggard cutoff, the Index_Direction flat-band threshold, and the minimum required aligned-candle count, each with a documented expected type and valid range.
2. WHERE a parameter environment variable is unset or holds an empty string, THE Relative_Strength_Calculator SHALL apply the documented default value for that parameter.
3. IF a parameter environment variable holds a value that cannot be parsed as the expected numeric type, THEN THE Relative_Strength_Calculator SHALL apply the documented default value for that parameter and SHALL NOT raise an exception.
4. IF a parameter environment variable holds a value that parses as the expected numeric type but falls outside the valid range defined for that parameter, THEN THE Relative_Strength_Calculator SHALL apply the documented default value for that parameter and SHALL NOT raise an exception.
5. WHEN resolving the leader cutoff and the laggard cutoff, THE Relative_Strength_Calculator SHALL require the laggard cutoff to be strictly less than the leader cutoff, and IF this ordering does not hold, THEN THE Relative_Strength_Calculator SHALL apply the documented default values for both cutoffs without raising an exception.
6. WHEN resolving parameters for the Relative_Strength_Tool path and for the Backtest_Seeder path from identical environment variable values, THE Relative_Strength_Calculator SHALL apply identical resolved parameter values and identical documented defaults across both paths.

### Requirement 13: Scope boundary — filter, not generator

**User Story:** As a trader, I want relative strength to remain a filter, so that it never fabricates data or becomes a trade generator.

#### Acceptance Criteria

1. THE Relative_Strength_Calculator SHALL produce only a Relative_Strength_Label or an Unavailable_Marker, and SHALL NOT emit, recommend, or score a BUY, SELL, or HOLD decision.
2. THE Relative_Strength_Tool SHALL derive its result exclusively from OHLCV candle data of the symbol and the Benchmark_Index and the configured parameters, and SHALL NOT consume options-chain data or any other non-candle data source.
3. IF the Alignment is `aligned`, THEN the relative-strength context SHALL NOT, of itself, commit, generate, or trigger a trade; a trade SHALL be committed only by an explicit Deep_Quant_Agent decision.
4. THE relative-strength context SHALL NOT modify, override, or replace a committed Deep_Quant_Agent decision; its effect SHALL be limited to system-prompt guidance and defensibility surfacing.
5. WHILE the Alignment is `misaligned` or `neutral`, the relative-strength context SHALL NOT, of itself, block a trade that the Deep_Quant_Agent decides to commit.
