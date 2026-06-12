# Requirements Document

## Introduction

The Deep Quant agent ("Alpha-Quant") is a ReAct loop in which an LLM calls quantitative tools and commits trade decisions (BUY / SELL / HOLD) that are recorded, scored, and aggregated by a SQLite Trade_Journal. A recent multi-symbol/multi-timeframe backtest showed the rule set carries genuine edge on the daily timeframe (≈46% win rate, +0.38R on RELIANCE 1d) but degrades toward break-even on fast intraday timeframes (≈25–33% win rate on 1m/5m). The working hypothesis is that the agent and the Backtest_Seeder rules lose money by trading in choppy, rangebound, or low-volatility "regimes" where trend and momentum setups fail. A veteran trader's primary skill is knowing when NOT to trade.

This feature adds a **Regime Detection Gate**: a cheap, deterministic, pure-math classifier that labels the current market regime (trend state plus volatility state) from candle data already available to the system, and uses that label to push the agent toward standing aside (HOLD) when the regime is unfavorable for the proposed setup type. The classifier is exposed to the agent as a new Analysis_Tool (`get_market_regime`) that mirrors the existing tool pattern exactly: it returns a validated Tool_Result_Contract, re-validated on receipt by `validate_contract` in `tools.py`, and degrades gracefully to an honest "unavailable / insufficient-data" marker that never blocks a decision. The regime is wired into the agent's tool list and system prompts, captured in the defensibility record, surfaced as a verification step in the event stream, added as a new dimension of the Trade_Journal setup fingerprint, and integrated into the Backtest_Seeder so the journal can measure win-rate and expectancy WITH versus WITHOUT the gate. All regime thresholds are configurable via environment variables rather than hardcoded.

The Regime Detection Gate is a **filter / calibration aid, not a trade generator**. It never fabricates data and never, on its own, forces a trade to be taken.

## Glossary

- **Deep_Quant_Agent**: The LangGraph ReAct agent ("Alpha-Quant") in `agents/deep-quant-loop/` that calls Analysis_Tools and commits decisions.
- **Regime_Classifier**: The pure-Python, deterministic component that maps a sequence of OHLCV candles plus a configuration to a Regime_Label. Lives in the `agents/deep-quant-loop/` Python layer.
- **Regime_Label**: The structured output of the Regime_Classifier, comprising a Trend_State, a Volatility_State, the underlying numeric regime measures, and a Favorability assessment.
- **Trend_State**: A categorical classification of directional structure, one of `trending`, `ranging`, or `transitional`.
- **Volatility_State**: A categorical classification of realized volatility, one of `low`, `normal`, or `high`.
- **Regime_Measure**: A named, deterministic scalar computed from candles used to derive a Regime_Label (e.g. ADX / directional movement, efficiency ratio, choppiness index, ATR-percentile / realized volatility, Bollinger-band width).
- **Favorability**: A derived classification stating whether the current regime is `favorable`, `unfavorable`, or `neutral` for trend/momentum setups, given the configured thresholds.
- **Market_Regime_Tool**: The `get_market_regime` Analysis_Tool exposed to the Deep_Quant_Agent, which produces a Regime_Label result conforming to its Tool_Result_Contract.
- **Tool_Result_Contract**: The structural contract a tool result must satisfy, re-validated on receipt by `validate_contract` in `tools.py`.
- **Regime_Gate**: The combination of system-prompt guidance and defensibility surfacing that biases the agent toward HOLD when Favorability is `unfavorable` for the proposed setup type.
- **Unavailable_Marker**: An honest, non-fatal result indicating the regime could not be computed (e.g. insufficient candles), mirroring the existing graceful-degradation pattern (`{"unavailable": true, "reason": ...}`).
- **Defensibility_Record**: The evidence record assembled by `build_defensibility_record` in `graph.py` and attached to every committed decision.
- **Declared_Trade**: The committed BUY / SELL / HOLD decision produced by the Deep_Quant_Agent for an analysis session.
- **Trade_Journal**: The SQLite measurement/feedback store (`journal.py`) that records, scores, and aggregates decisions.
- **Setup_Fingerprint**: The coarse, low-cardinality setup tag set derived by `journal.derive_setup_tags`, used to group trades for per-setup statistics.
- **Backtest_Seeder**: The `backtest.py` module that replays historical candles through deterministic rule-based setups and seeds the Trade_Journal tagged `source='backtest'`.
- **Verification_Step**: A `VERIFICATION_STEP` event emitted by `stream_events.py` describing a self-verification / risk-manager check and its outcome.
- **Rust_Tool_Server**: The Rust service at `http://localhost:8084` that serves authoritative quantitative computations.

## Requirements

### Requirement 1: Regime Classifier math

**User Story:** As a quantitative developer, I want a deterministic pure-math regime classifier computed from candle data, so that the system can label the current market regime without any new external data source.

#### Acceptance Criteria

1. THE Regime_Classifier SHALL compute the Regime_Label exclusively from a provided sequence of OHLCV candles and a provided configuration, performing zero network calls and reading zero data sources other than the two provided inputs.
2. WHEN invoked two or more times with a candle sequence whose values are element-wise identical and a configuration whose values are identical, THE Regime_Classifier SHALL return a Regime_Label, Trend_State, Volatility_State, and Favorability that are each identical across all such invocations.
3. IF the provided candle sequence contains fewer candles than the largest configured lookback period required by any Regime_Measure, THEN THE Regime_Classifier SHALL reject the input, return an error indication that identifies the insufficient-data condition, and leave the provided candle sequence and configuration unmodified.
4. WHEN the candle sequence contains at least the largest configured lookback period, THE Regime_Classifier SHALL compute a directional-strength Regime_Measure (ADX or directional-movement based) over the configured directional-strength lookback period from the candle sequence.
5. WHEN the candle sequence contains at least the largest configured lookback period, THE Regime_Classifier SHALL compute a choppiness/efficiency Regime_Measure (a choppiness index or efficiency ratio) over the configured choppiness lookback period from the candle sequence.
6. WHEN the candle sequence contains at least the largest configured lookback period, THE Regime_Classifier SHALL compute a realized-volatility Regime_Measure (an ATR-percentile or realized-volatility measure) over the configured volatility lookback period from the candle sequence.
7. WHEN the candle sequence contains at least the largest configured lookback period, THE Regime_Classifier SHALL compute a Bollinger-band-width Regime_Measure over the configured Bollinger lookback period from the candle sequence.
8. THE Regime_Classifier SHALL classify the Trend_State as exactly one of `trending`, `ranging`, or `transitional` by comparing the directional-strength Regime_Measure and the choppiness/efficiency Regime_Measure against the corresponding thresholds in the provided configuration.
9. THE Regime_Classifier SHALL classify the Volatility_State as exactly one of `low`, `normal`, or `high` by comparing the realized-volatility Regime_Measure and the Bollinger-band-width Regime_Measure against the corresponding thresholds in the provided configuration.
10. THE Regime_Classifier SHALL derive a Favorability of exactly one of `favorable`, `unfavorable`, or `neutral` from the Trend_State, the Volatility_State, and the configured thresholds, such that every combination of Trend_State and Volatility_State maps to exactly one Favorability value.
11. THE Regime_Classifier SHALL implement all Regime_Classifier functions as pure functions that produce no observable change to their input candle sequence or configuration.

### Requirement 2: Regime Classifier robustness and bounds

**User Story:** As a quantitative developer, I want the regime classifier to handle degenerate and insufficient inputs deterministically, so that it can be contract-validated and property-tested in isolation.

#### Acceptance Criteria

1. IF the candle sequence contains fewer valid candles than the configured minimum required for the longest Regime_Measure lookback, THEN THE Regime_Classifier SHALL return an Unavailable_Marker whose reason identifies the insufficient-data condition and includes the count of valid candles received and the configured minimum required, without raising an exception.
2. WHEN a candle in the sequence contains a non-finite or non-numeric OHLCV field, THE Regime_Classifier SHALL exclude that candle from all Regime_Measure computations without raising an exception.
3. IF excluding candles with non-finite or non-numeric OHLCV fields leaves fewer valid candles than the configured minimum required for the longest Regime_Measure lookback, THEN THE Regime_Classifier SHALL return an Unavailable_Marker whose reason identifies the insufficient-data condition, without raising an exception.
4. WHILE every computed Regime_Measure is finite, THE Regime_Classifier SHALL include each named Regime_Measure as a finite numeric value in the Regime_Label.
5. WHERE a Regime_Measure is defined on a bounded range (for example an efficiency ratio in [0.0, 1.0]), THE Regime_Classifier SHALL clamp that measure to the nearest boundary value of its range whenever its computed value would otherwise fall outside the range, so that the measure reported in the Regime_Label lies within its defined bounds for all valid inputs.
6. IF a Regime_Measure cannot be computed because its denominator is zero (for example zero price range over the window), THEN THE Regime_Classifier SHALL represent that measure as null in the Regime_Label and SHALL NOT raise an exception.
7. IF every required Regime_Measure is null because none could be computed, THEN THE Regime_Classifier SHALL return an Unavailable_Marker whose reason identifies that no Regime_Measure could be computed, rather than returning a Regime_Label.
8. WHEN the same candle sequence and configuration are supplied on repeated invocations, THE Regime_Classifier SHALL return an identical Regime_Label or Unavailable_Marker.

### Requirement 3: Market Regime Tool and contract

**User Story:** As the Deep_Quant_Agent, I want a `get_market_regime` tool that returns a validated regime result, so that I can consult the current regime during analysis.

#### Acceptance Criteria

1. THE Market_Regime_Tool SHALL be exposed to the Deep_Quant_Agent as an `@tool`-decorated function named `get_market_regime` following the existing tool pattern in `tools.py`.
2. THE Market_Regime_Tool SHALL accept a `symbol` argument and a `timeframe` argument.
3. IF the `symbol` argument is empty or whitespace-only, OR the `timeframe` argument is not one of the supported timeframes, THEN THE Market_Regime_Tool SHALL return a structured error result and SHALL NOT raise an exception.
4. WHEN the Market_Regime_Tool successfully computes a regime, THE Market_Regime_Tool SHALL return a result containing `trend_state` (one of `trending`, `ranging`, `transitional`), `volatility_state` (one of `low`, `normal`, `high`), `favorability` (one of `favorable`, `unfavorable`, `neutral`), and the named Regime_Measures each present as a finite number or null.
5. WHEN a `get_market_regime` result conforms to the Tool_Result_Contract, THE `validate_contract` function in `tools.py` SHALL return that result unchanged.
6. IF a `get_market_regime` result does not conform to the Tool_Result_Contract, THEN THE `validate_contract` function SHALL return a structured `{"error", "contract_violation"}` result that identifies the offending field.
7. WHEN a `get_market_regime` result carries an Unavailable_Marker, THE `validate_contract` function SHALL treat it as an honest non-fatal result and pass it through unchanged.
8. THE `validate_contract` function SHALL NOT raise an exception while validating a `get_market_regime` result, including when the result is malformed, missing fields, or not an object.
9. WHERE the regime computation is delegated to the Rust_Tool_Server, THE Market_Regime_Tool SHALL re-validate the returned result against the same Tool_Result_Contract before returning it to the Deep_Quant_Agent.

### Requirement 4: Graceful degradation of the Market Regime Tool

**User Story:** As a trader relying on the agent, I want an unavailable regime to be a non-blocking missing input, so that the absence of a regime never fabricates data or falsely blocks a decision.

#### Acceptance Criteria

1. IF the Market_Regime_Tool cannot retrieve candle data within its configured retrieval timeout, THEN THE Market_Regime_Tool SHALL return an Unavailable_Marker whose reason field identifies the retrieval-failure cause.
2. IF fewer than the configured minimum candle count required to classify the regime are available, THEN THE Market_Regime_Tool SHALL return an Unavailable_Marker whose reason field cites insufficient data and states the number of candles available and the number required.
3. WHEN the Market_Regime_Tool returns an Unavailable_Marker, THE Market_Regime_Tool SHALL omit Trend_State, Volatility_State, and Favorability rather than populate them with default, placeholder, or otherwise fabricated values.
4. WHEN a regime result is unavailable, THE Deep_Quant_Agent SHALL treat the regime as a missing optional input, SHALL proceed with the remaining analysis, and SHALL NOT abort, fail, or block the decision solely because the regime is unavailable.
5. IF the Market_Regime_Tool encounters any error while retrieving or processing candle data, THEN THE Market_Regime_Tool SHALL return an Unavailable_Marker and SHALL NOT propagate an exception into the Deep_Quant_Agent loop.
6. WHEN the Market_Regime_Tool returns an Unavailable_Marker, THE Deep_Quant_Agent SHALL NOT substitute a fabricated or default Trend_State, Volatility_State, or Favorability in place of the unavailable regime.

### Requirement 5: Graph wiring of the Market Regime Tool

**User Story:** As a quantitative developer, I want the regime tool registered in the graph, so that the agent can call it and the loop control treats it consistently with other market-data tools.

#### Acceptance Criteria

1. THE Deep_Quant_Agent SHALL include `get_market_regime` in the `tools` list bound to the model in `graph.py`.
2. THE Deep_Quant_Agent SHALL include `get_market_regime` in `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_market_regime` call is classified as a valid (not invalid-tool) call.
3. THE Deep_Quant_Agent SHALL include `get_market_regime` in `MARKET_DATA_TOOL_NAMES` in `graph.py`.
4. WHEN a `get_market_regime` result is usable data (neither an error result nor an explicit Unavailable_Marker), THE Deep_Quant_Agent SHALL set the `market_data_seen` flag.
5. IF a `get_market_regime` result is an error result or an Unavailable_Marker, THEN THE Deep_Quant_Agent SHALL NOT set the `market_data_seen` flag on the basis of that result.
6. WHILE the `market_data_seen` flag has been set true within a run, THE Deep_Quant_Agent SHALL keep it true for the remainder of that run.

### Requirement 6: Agent prompt integration of the Regime Gate

**User Story:** As a trader, I want the agent to consult the regime and stand aside in unfavorable regimes, so that it stops trading chop.

#### Acceptance Criteria

1. THE `DEEP_QUANT_SYSTEM_PROMPT` order_of_operations SHALL instruct the Deep_Quant_Agent to call `get_market_regime` for the symbol and the timeframe currently under analysis.
2. THE `DEEP_QUANT_SYSTEM_PROMPT` self_verification_protocol SHALL instruct the Deep_Quant_Agent to check the Favorability before committing a directional trade, where a directional trade is a BUY or SELL decision (excluding HOLD).
3. THE `DEEP_QUANT_SYSTEM_PROMPT` SHALL instruct the Deep_Quant_Agent that, when the Favorability is `unfavorable` for the proposed setup type, it must take exactly one of the following actions: lower its conviction score, wait for a better setup, or HOLD.
4. THE `DEEP_QUANT_SYSTEM_PROMPT` setup_validation_disclosure SHALL instruct the Deep_Quant_Agent to state the Trend_State, the Volatility_State, and the Favorability — taken from the `get_market_regime` result — in its setup_validation.
5. THE VERIFY-mode `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to consult `get_market_regime` while verifying a user-proposed trade.
6. THE VERIFY-mode `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to include an explicit warning statement in its verification output when a user-proposed trade is taken in an `unfavorable` regime.
7. WHEN the regime is unavailable, THE `DEEP_QUANT_SYSTEM_PROMPT` and the `RISK_MANAGER_PROMPT` SHALL instruct the Deep_Quant_Agent to note the regime as unavailable and proceed, rather than block the decision.

### Requirement 7: Regime in the defensibility record

**User Story:** As a trader auditing committed trades, I want every committed decision to cite the regime, so that I can see the regime context that informed the trade.

#### Acceptance Criteria

1. WHEN `build_defensibility_record` assembles a record, THE Deep_Quant_Agent SHALL include a regime entry containing the Trend_State, the Volatility_State, the Favorability, and the named Regime_Measures taken from the most recent `get_market_regime` result present in message history.
2. THE Deep_Quant_Agent SHALL populate the regime entry of the Defensibility_Record using only values returned by the Market_Regime_Tool and SHALL NOT substitute or infer any regime value not present in that result.
3. IF no `get_market_regime` result is present in message history when `build_defensibility_record` assembles a record, THEN THE Deep_Quant_Agent SHALL record the regime entry of the Defensibility_Record as unavailable and SHALL NOT populate the Trend_State, Volatility_State, Favorability, or Regime_Measures with substitute values.
4. WHEN the Favorability in the most recent `get_market_regime` result is `unfavorable` and the committed Declared_Trade has action BUY or SELL, THE Deep_Quant_Agent SHALL include in the Defensibility_Record an explicit statement that the committed trade opposes the regime assessment.

### Requirement 8: Regime as a stream verification step

**User Story:** As a user watching the agent stream, I want the regime check surfaced as a verification step, so that I can see the regime was evaluated before the trade was finalized.

#### Acceptance Criteria

1. WHEN building Verification_Steps for a decision, THE event stream SHALL emit exactly one regime `VERIFICATION_STEP` carrying a stable check identifier and an outcome value that is one of `pass`, `fail`, `informational`, or `not-evaluable`.
2. WHEN the Defensibility_Record regime Favorability is `favorable`, THE regime Verification_Step SHALL report an outcome of `pass`.
3. WHEN the Defensibility_Record regime Favorability is `unfavorable`, THE regime Verification_Step SHALL report an outcome of `fail`.
4. WHEN the Defensibility_Record regime Favorability is `neutral`, THE regime Verification_Step SHALL report an outcome of `informational`.
5. IF the regime is unavailable in the Defensibility_Record, THEN THE regime Verification_Step SHALL report an outcome of `not-evaluable` with an indication that the regime is unavailable, and SHALL NOT substitute a fabricated Favorability.
6. WHEN emitting decision events for a run, THE event stream SHALL order the regime Verification_Step before the `DECISION` event of that run.

### Requirement 9: Journal setup-fingerprint extension

**User Story:** As a quantitative developer, I want the regime added to the journal setup fingerprint, so that per-regime win-rate and expectancy are measurable.

#### Acceptance Criteria

1. WHEN `derive_setup_tags` derives a Setup_Fingerprint, THE Trade_Journal SHALL append exactly one regime dimension tag of the form `regime:<value>`, where `<value>` is read from the regime recorded in the decision's defensibility record, and SHALL place this tag at a fixed position in the tag sequence so that the resulting `setup_key` is deterministic for identical inputs.
2. IF the decision's defensibility record carries no regime, OR the recorded regime value is empty or not one of the predefined regime enumeration values, THEN THE Trade_Journal SHALL append the regime tag `regime:unknown`.
3. THE Trade_Journal SHALL constrain the regime tag to exactly one value drawn from a fixed, predefined enumeration containing at most 8 distinct values (including `unknown`), so that the regime-extended `setup_key` remains low-cardinality and individual `setup_key` groups can accumulate at least the low-sample threshold of scored trades.
4. WHEN aggregating statistics, THE Trade_Journal SHALL group scored (win or loss) trades by the regime-extended `setup_key` and SHALL report, for each group, a win-rate as the fraction of scored trades that are wins (a value from 0.0 to 1.0) and an expectancy as the mean R-multiple of the group's scored trades.
5. IF a regime-extended `setup_key` group contains fewer scored trades than the configured low-sample threshold, THEN THE Trade_Journal SHALL flag that group's reported statistics as a weak prior.

### Requirement 10: Backtest with-gate / without-gate comparison

**User Story:** As a quantitative developer, I want the backtest seeder to measure performance with versus without the regime gate, so that I can prove with numbers whether the gate improves expectancy.

#### Acceptance Criteria

1. WHEN the Backtest_Seeder generates a signal, THE Backtest_Seeder SHALL classify that signal by invoking the same Regime_Classifier functions used by the Market_Regime_Tool, computing the Regime_Label using only candles at or before the signal's candle timestamp and no later candles.
2. WHERE the regime gate is enabled, WHEN a generated signal's regime Favorability is `unfavorable` for the signal's setup type, THE Backtest_Seeder SHALL exclude that signal from the with-gate seeded trade set.
3. WHEN the Backtest_Seeder seeds a trade, THE Backtest_Seeder SHALL label that trade with its Regime_Label comprising the Trend_State, the Volatility_State, and the Favorability, so that per-regime win-rate and expectancy are measurable in the Trade_Journal.
4. WHEN run in comparison mode, THE Backtest_Seeder SHALL report, for both the with-gate run and the without-gate run computed over the identical candle history and identical setup rules, the win-rate defined as the count of winning closed trades divided by the count of closed trades and the expectancy defined as the mean realized R-multiple per closed trade.
5. THE Backtest_Seeder SHALL reuse the same Regime_Classifier functions rather than reimplementing the regime math.
6. WHERE the regime gate is enabled, IF a generated signal's regime result is an Unavailable_Marker, THEN THE Backtest_Seeder SHALL retain that signal in the with-gate seeded trade set and SHALL NOT exclude it on the basis of regime.
7. IF a comparison-mode run produces zero closed trades, THEN THE Backtest_Seeder SHALL report that run's win-rate and expectancy as not-applicable rather than computing a division by zero.

### Requirement 11: Configurable regime thresholds

**User Story:** As a quantitative developer, I want regime cutoffs configurable via environment variables, so that thresholds are tunable rather than hardcoded magic numbers.

#### Acceptance Criteria

1. THE Regime_Classifier SHALL read each of the following regime thresholds from its own environment variable: the directional-strength cutoff (expected as a decimal in the range 0.0 to 100.0), the choppiness/efficiency cutoff (expected as a decimal within the valid range of its underlying Regime_Measure), the low-volatility percentile cutoff and the high-volatility percentile cutoff (each expected as a decimal in the range 0.0 to 100.0), and the minimum required candle count (expected as an integer of at least 1).
2. WHERE a regime threshold environment variable is unset or holds an empty string, THE Regime_Classifier SHALL apply the documented default value for that threshold.
3. IF a regime threshold environment variable holds a value that cannot be parsed as the expected numeric type for that threshold, THEN THE Regime_Classifier SHALL apply the documented default value for that threshold and SHALL NOT raise an exception.
4. IF a regime threshold environment variable holds a value that parses as the expected numeric type but falls outside the valid range defined for that threshold in criterion 1, THEN THE Regime_Classifier SHALL apply the documented default value for that threshold and SHALL NOT raise an exception.
5. WHEN the Regime_Classifier resolves its thresholds, THE Regime_Classifier SHALL require the low-volatility percentile cutoff to be strictly less than the high-volatility percentile cutoff, and IF this ordering does not hold, THEN THE Regime_Classifier SHALL apply the documented default values for both volatility-percentile cutoffs without raising an exception.
6. WHEN resolving thresholds for the Market_Regime_Tool path and for the Backtest_Seeder path from identical environment variable values, THE Regime_Classifier SHALL apply identical resolved threshold values and identical documented defaults across both paths.

### Requirement 12: Scope boundary — filter, not generator

**User Story:** As a trader, I want the regime gate to remain a filter, so that it never fabricates data or becomes a trade generator.

#### Acceptance Criteria

1. THE Regime_Classifier SHALL produce only a Regime_Label or an Unavailable_Marker, and SHALL NOT emit, recommend, or score a BUY, SELL, or HOLD decision.
2. THE Market_Regime_Tool SHALL derive its result exclusively from OHLCV candle data and the configured thresholds, and SHALL NOT consume options-chain data or any other non-candle data source.
3. IF the regime is `favorable`, THEN THE Regime_Gate SHALL NOT, of itself, commit, generate, or trigger a trade; a trade SHALL be committed only by an explicit Deep_Quant_Agent decision.
4. THE Regime_Classifier SHALL classify regime using only candle-derived Regime_Measures and the configured thresholds, and SHALL NOT use any other input.
5. THE Regime_Gate SHALL NOT modify, override, or replace a committed Deep_Quant_Agent decision; its effect SHALL be limited to system-prompt guidance and defensibility surfacing.
6. WHILE the Favorability is `unfavorable` or `neutral`, THE Regime_Gate SHALL NOT, of itself, block a trade that the Deep_Quant_Agent decides to commit.
