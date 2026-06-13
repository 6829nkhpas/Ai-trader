# Implementation Plan: Volatility-Aware Forecaster

## Overview

This plan implements the Volatility-Aware Forecaster as a pure-Python `Volatility_Aware_Forecaster` (new `forecaster.py`) wired through the existing Deep Quant layers exactly as `regime-detection-gate`, `relative-strength-context`, and `order-flow-context` were: the `get_forecast` tool and `validate_contract` (`tools.py`), graph wiring / defensibility / prompts (`graph.py`), the forecast verification step (`stream_events.py`), the journal setup fingerprint plus Up_Probability persistence (`journal.py`), and the with-forecast / without-forecast backtest comparison plus the reliability calibration measurement (`backtest.py`).

Work proceeds bottom-up: parameter resolution first, then the pure drift/volatility/ATR estimation functions, then the regime-conditioned blend and probabilistic output functions, then the top-level `forecast` entry point (which reuses the existing `regime.classify_regime`), then the tool and contract, then the graph / audit / measurement consumers, and finally the backtest comparison and calibration and end-to-end wiring. The forecaster is the single source of truth for the forecast math; the live tool path, the backtest comparison, and the calibration measurement all call `forecaster.forecast` and `forecaster.resolve_forecaster_config` (no path reimplements the math; they only feed different point-in-time candle windows).

All code is Python under `agents/deep-quant-loop/`. Property-based tests use the repo's existing `hypothesis` setup (note the `agents/deep-quant-loop/.hypothesis` cache); exactly one property test implements each of design Properties 1–39, each tagged with a `# Feature: volatility-aware-forecaster, Property {n}: {property_text}` comment and running ≥ 100 examples. This feature adds no new heavy dependency and requires no Rust rebuild.

## Tasks

- [x] 1. Parameter resolution foundation in `forecaster.py`
  - [x] 1.1 Create `forecaster.py` with `ForecasterConfig` and `resolve_forecaster_config`
    - Create the new module `agents/deep-quant-loop/forecaster.py` (no `httpx`, no file/clock access), mirroring the structure of `regime.py` / `rs.py` / `order_flow.py`; import `regime` to reuse the classifier
    - Define documented default constants (`DEFAULT_FORECAST_DRIFT_LOOKBACK=20`, `DEFAULT_FORECAST_VOL_LOOKBACK=20`, `DEFAULT_FORECAST_ATR_PERIOD=14`, `DEFAULT_FORECAST_FLAT_BAND=0.25`, `DEFAULT_FORECAST_MIN_CANDLES=30`, `DEFAULT_FORECAST_PROB_BINS=10`, `DEFAULT_FORECAST_PROB_SCALE=2.0`)
    - Define the frozen `ForecasterConfig` dataclass with the `largest_lookback` property (`max(drift_lookback, vol_lookback, atr_period) + 1`)
    - Implement `resolve_forecaster_config()`: read each parameter from its own env var (`FORECAST_DRIFT_LOOKBACK`, `FORECAST_VOL_LOOKBACK`, `FORECAST_ATR_PERIOD`, `FORECAST_FLAT_BAND`, `FORECAST_MIN_CANDLES`, `FORECAST_PROB_BINS`, `FORECAST_PROB_SCALE`) reusing the `regime._resolve_int` / `_resolve_float` parse-with-default-and-range helpers; fall back to the documented default on unset/empty/unparseable/out-of-range per the design's range table; never raise
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

  - [x] 1.2 Write property test for per-parameter default fallback
    - **Property 36: Each parameter falls back to its documented default**
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4**

  - [x] 1.3 Write property test for deterministic, path-independent resolution
    - **Property 37: Parameter resolution is deterministic and path-independent**
    - **Validates: Requirements 14.5**

- [x] 2. Drift / volatility / ATR estimation functions in `forecaster.py`
  - [x] 2.1 Implement the pure, candle-only estimation functions
    - Implement `compute_log_returns(candles, lookback)` → `ln(close_t / close_{t-1})` over the last `lookback`+1 valid candles; exclude non-finite/non-numeric candles; return `[]` when fewer than two usable closes or a non-positive close is encountered
    - Implement `compute_drift(candles, config)` → the Drift_Estimate over the drift lookback (EWMA momentum and/or OLS slope of log-returns); finite when not `None`; `None` when no usable returns
    - Implement `compute_volatility(candles, config)` → the Volatility_Estimate over the volatility lookback (EWMA standard deviation of log-returns, corroborated by an ATR-based measure), STRICTLY NON-NEGATIVE; finite when not `None`; `None` when no usable returns
    - Implement `compute_atr(candles, period)` → Average True Range over `period` (the Expected_Move_ATR denominator); `None` when insufficient candles or zero range
    - Keep every function pure (no input mutation) and non-raising; perform zero network calls
    - _Requirements: 1.1, 1.2, 1.3, 4.2_

  - [x] 2.2 Write property test for present, finite-or-null measures with non-negative volatility
    - **Property 3: Drift and volatility measures are present, finite-or-null, and volatility is non-negative**
    - **Validates: Requirements 1.2, 1.3, 4.3**

  - [x] 2.3 Write property test for non-finite candle exclusion
    - **Property 12: Non-finite candles are excluded without affecting the result**
    - **Validates: Requirements 4.2**

- [x] 3. Regime-conditioned blend and probabilistic output functions in `forecaster.py`
  - [x] 3.1 Implement the blend and output functions
    - Implement `conditioned_drift(drift, volatility, trend_state, config)` → the regime-conditioned standardized drift `z` (`drift / volatility` re-weighted): trend-continuation when `trend_state == 'trending'`, mean-reversion when `'ranging'`, neutral (unweighted) when `'transitional'` or regime unavailable; total over the trend-state set, never raises
    - Implement `classify_direction(z, config)` → exactly one of `up`/`down`/`flat` per the flat-band mapping table (`flat` when `abs(z) <= flat_band`, else sign of `z`)
    - Implement `up_probability(z, config)` → `clamp(1 / (1 + exp(-prob_scale * z)), 0.0, 1.0)`, `0.5` exactly when `z == 0`, guaranteeing `up ⇒ p ≥ 0.5` and `down ⇒ p ≤ 0.5`
    - Implement `forecast_confidence(z, config)` → `clamp(2 * abs(up_probability - 0.5), 0.0, 1.0)`, a strictly increasing function of `abs(z)`, `0.0` when `z == 0`
    - Implement `derive_forecast_alignment(projected_direction, proposed_direction)` → exactly one of `aligned`/`misaligned`/`neutral` as a total function (absent/HOLD proposed direction → `neutral`) per the design alignment tables
    - Keep all functions pure, deterministic, non-raising
    - _Requirements: 2.2, 2.3, 2.4, 3.1, 3.2, 3.4, 3.5, 3.6, 4.4_

  - [x] 3.2 Write property test for regime conditioning (continuation in trends, reversion in ranges)
    - **Property 4: Regime conditioning weights continuation in trends and reversion in ranges**
    - **Validates: Requirements 2.2, 2.3**

  - [x] 3.3 Write property test for well-formed direction matching the flat-band mapping
    - **Property 6: Projected_Direction is well-formed and matches the flat-band mapping**
    - **Validates: Requirements 3.1**

  - [x] 3.4 Write property test for finite, bounded, clamped Up_Probability
    - **Property 7: Up_Probability is finite, bounded, and clamped**
    - **Validates: Requirements 3.2, 4.4**

  - [x] 3.5 Write property test for finite, bounded confidence increasing with drift strength
    - **Property 9: Forecast_Confidence is finite, bounded, and increases with drift strength**
    - **Validates: Requirements 3.4, 4.4**

  - [x] 3.6 Write property test for direction/probability consistency
    - **Property 10: Direction and probability are consistent**
    - **Validates: Requirements 3.5**

  - [x] 3.7 Write property test for total alignment derivation
    - **Property 11: Forecast_Alignment is a total function of projected and proposed direction**
    - **Validates: Requirements 3.6**

- [x] 4. Top-level `forecast` entry point in `forecaster.py`
  - [x] 4.1 Implement `forecast` and its special cases
    - Implement `forecast(candles, config, proposed_direction=None, symbol=None, timeframe=None)`: compute drift and volatility from the valid candles, obtain the trend state from `regime.classify_regime(candles, regime.resolve_regime_config())` (treating a `transitional` state or an Unavailable_Marker as neutral), form the regime-conditioned `z`, then derive Projected_Direction, Up_Probability, Expected_Move_ATR (signed expected move / ATR; `null` when ATR is zero/unavailable), Forecast_Confidence, and Forecast_Alignment, assembling a `Forecast_Label` (including the `measures` object `drift`/`volatility`/`standardized_drift`/`atr` and `regime_trend_state`)
    - Insufficient-candle case: when valid candles are below `config.largest_lookback`/`min_candles`, return an `Unavailable_Marker` citing received-vs-required counts; the marker omits `projected_direction`/`up_probability`/`expected_move_atr`/`forecast_confidence`/`forecast_alignment`
    - Zero-volatility case: short-circuit to `projected_direction="flat"`, `up_probability=0.5`, `forecast_confidence=0.0`, never dividing by zero
    - Keep `forecast` pure, deterministic, non-raising; emit only a label or marker (never a BUY/SELL/HOLD action, conviction, or decision field)
    - _Requirements: 1.4, 1.5, 2.1, 2.5, 3.3, 4.1, 4.5, 4.6, 6.2, 6.3, 15.1, 15.2, 15.3_

  - [x] 4.2 Write property test for forecast determinism
    - **Property 1: Forecast is deterministic**
    - **Validates: Requirements 1.4, 4.6**

  - [x] 4.3 Write property test for forecaster purity (no input mutation, no network)
    - **Property 2: Forecaster functions are pure (no input mutation, no network)**
    - **Validates: Requirements 1.1, 1.5**

  - [x] 4.4 Write property test for neutral blend on transitional/unavailable regime
    - **Property 5: A transitional or unavailable regime applies a neutral blend without raising**
    - **Validates: Requirements 2.4**

  - [x] 4.5 Write property test for Expected_Move_ATR equalling signed move over ATR and null when unusable
    - **Property 8: Expected_Move_ATR equals the signed move over ATR, and is null exactly when ATR is unusable**
    - **Validates: Requirements 3.3**

  - [x] 4.6 Write property test for insufficient-candle Unavailable_Marker with counts
    - **Property 13: Insufficient valid candles yield an Unavailable_Marker with counts**
    - **Validates: Requirements 4.1, 6.2**

  - [x] 4.7 Write property test for the zero-variance flat / 0.5 / 0.0 short-circuit
    - **Property 14: A zero-variance window yields flat / 0.5 / 0.0 without dividing by zero**
    - **Validates: Requirements 4.5**

  - [x] 4.8 Write property test that an Unavailable_Marker carries no fabricated forecast fields
    - **Property 21: An Unavailable_Marker never carries fabricated forecast fields**
    - **Validates: Requirements 6.3**

  - [x] 4.9 Write property test that the forecaster never emits a trade decision
    - **Property 38: The forecaster never emits a trade decision**
    - **Validates: Requirements 15.1, 15.2, 15.3**

  - [x] 4.10 Write unit test that `forecast` reuses `regime.classify_regime`
    - Spy on the `regime` module to assert `forecast` obtains its trend state by calling `regime.classify_regime` rather than reimplementing regime math
    - _Requirements: 2.1, 2.5_

- [x] 5. Checkpoint - forecaster core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. `get_forecast` tool and contract in `tools.py`
  - [x] 6.1 Extend `validate_contract` with a `get_forecast` branch
    - Add the enum set (`FORECAST_DIRECTIONS = {"up", "down", "flat"}`, reusing the existing `ALIGNMENT_VALUES`) and `_FORECAST_MEASURE_FIELDS = ("drift", "volatility", "standardized_drift", "atr")`
    - Pass Unavailable_Markers through unchanged (via the existing `_has_honest_marker`); pass conforming labels through unchanged (`projected_direction` in its enum, `up_probability` finite number in `[0.0, 1.0]`, `expected_move_atr` finite-number-or-null, `forecast_confidence` finite number in `[0.0, 1.0]`, `forecast_alignment` in its enum, each `_FORECAST_MEASURE_FIELDS` measure present under `measures` as finite-number-or-null), reusing the existing numeric-bounds pattern for the `[0.0, 1.0]` checks
    - Return `{"error", "contract_violation"}` naming the offending field on non-conforming results; keep it wrapped so validation never raises
    - _Requirements: 5.6, 5.7, 5.8, 5.9_

  - [x] 6.2 Implement the `get_forecast` tool
    - Add the `@tool`-decorated `get_forecast(symbol, timeframe, proposed_direction="")` following the existing tool pattern; validate args (empty/whitespace symbol or unsupported timeframe → structured error); resolve config via `forecaster.resolve_forecaster_config()`
    - Fetch the symbol candles from `RUST_SERVER_URL/tools/get_candles` (limit large enough for `largest_lookback`); on retrieval timeout/failure/error payload return an Unavailable_Marker citing the cause
    - Classify via `forecaster.forecast(candles, config, proposed_direction=proposed_direction or None, symbol=symbol, timeframe=timeframe)`; re-validate via `validate_contract("get_forecast", result)`
    - Add `_forecast_unavailable(symbol, timeframe, reason)` (mirroring `_relative_strength_unavailable` / `_order_flow_unavailable`) that omits `projected_direction`/`up_probability`/`expected_move_atr`/`forecast_confidence`/`forecast_alignment`; catch any unexpected exception and return an Unavailable_Marker; never propagate an exception into the agent loop
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.3, 6.5, 15.2_

  - [x] 6.3 Write unit test for tool shape
    - Assert `get_forecast` is `@tool`-decorated, named `get_forecast`, and accepts `symbol`, `timeframe`, and an optional `proposed_direction`
    - _Requirements: 5.1, 5.2_

  - [x] 6.4 Write unit test that the tool consults the candle source only
    - With mocks, assert the tool fetches symbol candles from the Rust Tool_Server `/tools/get_candles` and derives its result from candle data only — no options-chain/other data source is consulted
    - _Requirements: 5.4, 15.2_

  - [x] 6.5 Write property test for invalid-argument rejection
    - **Property 15: The tool rejects invalid arguments without raising**
    - **Validates: Requirements 5.3**

  - [x] 6.6 Write property test for a well-formed successful result
    - **Property 16: A successful tool result is well-formed**
    - **Validates: Requirements 5.5**

  - [x] 6.7 Write property test for contract identity on conforming results/markers
    - **Property 17: validate_contract is the identity on conforming results and markers**
    - **Validates: Requirements 5.6, 5.8**

  - [x] 6.8 Write property test for contract rejection naming the field
    - **Property 18: validate_contract rejects non-conforming results, naming the field**
    - **Validates: Requirements 5.7**

  - [x] 6.9 Write property test that validate_contract never raises on a forecast result
    - **Property 19: validate_contract never raises on a forecast result**
    - **Validates: Requirements 5.9**

  - [x] 6.10 Write property test for degradation to an Unavailable_Marker on retrieval/processing failure
    - **Property 20: The tool degrades to an Unavailable_Marker on any retrieval or processing failure**
    - **Validates: Requirements 6.1, 6.5**

- [x] 7. Checkpoint - tool and contract
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Graph wiring of the tool in `graph.py`
  - [x] 8.1 Register the tool and confirm the market-data gate
    - Add `get_forecast` to the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`; import the forecast enum sets (`FORECAST_DIRECTIONS`, `_FORECAST_MEASURE_FIELDS`) alongside the existing regime/RS/order-flow imports
    - Confirm the existing `market_data_seen` logic sets the flag on a usable result and not on an error/Unavailable_Marker, and that the flag stays true once set
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 8.2 Write unit test for tool registration
    - Assert `get_forecast` appears in the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 8.3 Write property test for the market-data gate classification and monotonicity
    - **Property 22: The market-data gate classifies forecast results correctly and stays monotone**
    - **Validates: Requirements 6.4, 7.4, 7.5**

- [x] 9. Forecast in the defensibility record (`graph.py`)
  - [x] 9.1 Add the forecast entry to `build_defensibility_record`
    - Implement `_forecast_entry(results)` (modelled on `_relative_strength_entry`) reading the most recent `get_forecast` result from message history; copy `projected_direction`/`up_probability`/`expected_move_atr`/`forecast_confidence`/`forecast_alignment` and the named measures verbatim (no inference)
    - Mark the entry `{'available': False, ...}` (no substitute values) when no usable result is present
    - Add the explicit "committed trade opposes the forecast" statement when `forecast_alignment == "misaligned"` and the committed action is BUY or SELL; leave the decision's action and execution levels (entry, stop-loss, take-profit) unchanged
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 15.4, 15.5_

  - [x] 9.2 Write property test that the entry mirrors the tool result
    - **Property 23: The defensibility forecast entry mirrors the tool result without fabrication**
    - **Validates: Requirements 9.1, 9.2**

  - [x] 9.3 Write property test that absent forecast is recorded as unavailable
    - **Property 24: Absent forecast is recorded as unavailable**
    - **Validates: Requirements 9.3**

  - [x] 9.4 Write property test for the misaligned-directional opposition statement
    - **Property 25: A misaligned directional trade records the opposition statement**
    - **Validates: Requirements 9.4**

  - [x] 9.5 Write property test that the forecast never modifies or blocks a committed decision
    - **Property 39: The forecast never modifies or blocks a committed decision**
    - **Validates: Requirements 15.4, 15.5**

- [x] 10. Prompt integration of the forecast (`graph.py`)
  - [x] 10.1 Update the system and risk-manager prompts
    - Update `DEEP_QUANT_SYSTEM_PROMPT`: call `get_forecast` for the symbol/timeframe as the primary predictive cross-check in order_of_operations while retaining `get_prediction` as secondary; check Forecast_Alignment and Up_Probability before a directional (BUY/SELL) trade in self_verification_protocol; require exactly one of lower conviction / wait / HOLD when `misaligned` or the probability does not support the direction; disclose Projected_Direction / Up_Probability / Expected_Move_ATR / Forecast_Alignment in setup_validation; note forecast unavailable-and-proceed
    - Update `RISK_MANAGER_PROMPT`: consult `get_forecast` while verifying a user-proposed trade; include the explicit misaligned warning statement; note unavailable-and-proceed
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 10.2 Write unit tests for prompt content
    - Assert the prompts contain the primary-cross-check call (with `get_prediction` retained secondary), the alignment/probability check before a directional trade, the misaligned/unsupportive guidance, the setup-validation disclosure of direction/probability/expected-move/alignment, the VERIFY-mode warning statement, and the unavailable-and-proceed instructions
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 11. Forecast verification step in `stream_events.py`
  - [x] 11.1 Emit the forecast `VERIFICATION_STEP`
    - Implement `_forecast_step(record)` (modelled on `_relative_strength_step`) mapping the defensibility forecast entry to a step with stable check id `forecast`: `aligned`→`pass`, `misaligned`→`fail`, `neutral`→`informational`, unavailable→`not-evaluable` (with an unavailable indication, no fabricated alignment)
    - Wire it into `_derive_find_mode_steps` (and VERIFY mode via the same record entry) so exactly one forecast step is emitted, using the existing "append only when not already present" guard, ordered before the `DECISION` event
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 11.2 Write property test for the verification step outcome mapping
    - **Property 26: Exactly one forecast verification step with the correct outcome mapping**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

  - [x] 11.3 Write property test that the step precedes the DECISION event
    - **Property 27: The forecast verification step precedes the DECISION event**
    - **Validates: Requirements 10.6**

- [x] 12. Journal fingerprint extension and probability persistence in `journal.py`
  - [x] 12.1 Add the forecast tag to `derive_setup_tags`
    - Define the fixed `FC_TAG_VALUES` enumeration (≤ 8 values including `unknown`: `aligned-strong`, `aligned-weak`, `misaligned-strong`, `misaligned-weak`, `neutral-strong`, `neutral-weak`, `unknown`)
    - Implement `_forecast_tag(decision)` reading forecast from `decision['defensibility']['forecast']` and collapsing (Forecast_Alignment × Up_Probability confidence band) into one fixed value (`strong` when `abs(up_probability - 0.5)` is at/above a fixed split, else `weak`); missing/empty/unavailable/unrecognized → `fc:unknown`
    - Append exactly one `fc:<value>` tag at a fixed position (after the existing `of:` tag) so `setup_key_from_tags` stays deterministic and low-cardinality
    - _Requirements: 11.1, 11.2, 11.3_

  - [x] 12.2 Persist the forecast Up_Probability
    - Add a nullable `forecast_up_probability REAL` column to the journal schema; in `_init_db` add it via a guarded `ALTER TABLE` so existing journals upgrade in place (additive, backward-compatible)
    - In `record_decision` (and `record_backtest_trade`) read `decision['defensibility']['forecast']['up_probability']` and persist it, writing `NULL` when the forecast entry is unavailable
    - _Requirements: 11.4_

  - [x] 12.3 Write property test for the single fixed-position low-cardinality tag
    - **Property 28: Exactly one low-cardinality forecast tag at a fixed position**
    - **Validates: Requirements 11.1, 11.2, 11.3**

  - [x] 12.4 Write property test that the Up_Probability round-trips through persistence
    - **Property 29: The forecast Up_Probability round-trips through persistence**
    - **Validates: Requirements 11.4**

  - [x] 12.5 Write property test for per-forecast aggregation metrics
    - **Property 30: Per-forecast aggregation reports correct win-rate and expectancy**
    - **Validates: Requirements 11.5**

- [x] 13. Checkpoint - audit and measurement consumers
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Backtest with-forecast / without-forecast comparison in `backtest.py`
  - [x] 14.1 Classify signals look-ahead-free and label seeded trades
    - Add `forecast_filter_enabled: bool` to `BacktestConfig` and reuse `forecaster.resolve_forecaster_config()`
    - In `generate_and_score`, classify each signal's forecast via `forecaster.forecast` using only `candles[: i + 1]` (the window at/before the signal bar — no look-ahead), passing the signal's direction as `proposed_direction`
    - Add `_forecast_defensibility_entry(fc_result)` (mirroring `_relative_strength_defensibility_entry`) to populate each seeded trade's `decision['defensibility']['forecast']` so `journal._forecast_tag` labels it and `journal.record_backtest_trade` persists its Up_Probability
    - _Requirements: 13.1, 13.5, 11.4_

  - [x] 14.2 Implement the filter logic and `compare_forecast` entry point
    - With-forecast run: drop a signal via `_signal_is_forecast_misaligned(decision)` when `forecast_alignment == "misaligned"` for its direction (advancing cooldown exactly as a taken signal would); RETAIN a signal whose forecast result is an Unavailable_Marker
    - Add a `compare_forecast(...)` entry point (mirroring `compare_relative_strength`) running filtered and unfiltered over identical candle history and identical setup rules; report each run's win-rate (winning closed / closed) and expectancy (mean realized R) via the existing `_run_metrics`; report `"n/a"` when a run has zero closed trades
    - _Requirements: 13.2, 13.3, 13.4_

  - [x] 14.3 Write unit test that the backtest reuses the forecaster
    - Assert `backtest.py` imports and calls `forecaster.forecast` (with point-in-time windows) rather than reimplementing the forecast math
    - _Requirements: 13.5_

  - [x] 14.4 Write property test for look-ahead-free classification
    - **Property 33: Backtest forecast classification is look-ahead-free**
    - **Validates: Requirements 13.1**

  - [x] 14.5 Write property test for filter exclusion/retention behavior
    - **Property 34: The enabled filter excludes misaligned signals and retains unavailable ones**
    - **Validates: Requirements 13.2, 13.4**

  - [x] 14.6 Write property test for comparison-mode consistency and metrics
    - **Property 35: Comparison-mode runs are consistent and metrics are well-defined**
    - **Validates: Requirements 13.3**

- [x] 15. Backtest calibration (reliability) measurement in `backtest.py`
  - [x] 15.1 Implement the calibration binning and `calibrate_forecast` entry point
    - Implement the pure `_calibration_from_records(records, bins)` helper: partition `[0, 1]` into `bins` equal-width bins; for each non-empty bin report the mean predicted `up_probability` and the realized fraction of `went_up`; report each empty bin as `not-applicable` (never dividing by zero); report a scalar `calibration_error` = mean absolute (predicted − realized) over the non-empty bins; pure and non-raising
    - Implement `calibrate_forecast(...)`: walk history, compute each signal's forecast from candles at/before the signal bar (no look-ahead) via `forecaster.forecast`, pair each predicted Up_Probability with the realized direction of the next bar (`close_{i+1} > close_i`), and pass the records to `_calibration_from_records`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 15.2 Write unit test that calibration reuses the forecaster
    - Assert `calibrate_forecast` imports and calls `forecaster.forecast` (point-in-time) rather than reimplementing the forecast math
    - _Requirements: 12.5_

  - [x] 15.3 Write property test for look-ahead-free, next-bar-paired calibration
    - **Property 31: Calibration is look-ahead-free and pairs each prediction with the realized next bar**
    - **Validates: Requirements 12.1**

  - [x] 15.4 Write property test for calibration binning statistics, scalar error, and not-applicable empty bins
    - **Property 32: Calibration binning reports correct per-bin statistics, a scalar error, and not-applicable empty bins**
    - **Validates: Requirements 12.2, 12.3, 12.4**

- [x] 16. Integration and end-to-end wiring
  - [x] 16.1 Write integration test for the non-blocking data gate
    - A single unavailable forecast ToolMessage alone does not satisfy the data gate nor force a decision; the agent proceeds with the remaining analysis
    - _Requirements: 6.4_

  - [x] 16.2 Write end-to-end example test for an aligned FIND-mode run
    - A mocked `aligned` forecast result produces a defensibility forecast entry, a `pass` forecast verification step ordered before the DECISION, an `fc:aligned-strong` (or `-weak`) journal tag, and a persisted Up_Probability
    - _Requirements: 8.4, 9.1, 10.2, 10.6, 11.1, 11.4_

  - [x] 16.3 Write smoke test for the forecast tool against a stubbed endpoint
    - Against a stubbed Rust candle endpoint, the tool returns a contract-valid Forecast_Label for a known symbol/timeframe
    - _Requirements: 5.5_

  - [x] 16.4 Write smoke test for comparison-mode and calibration backtests
    - A comparison-mode backtest over a fixed candle fixture produces with-forecast and without-forecast summaries with the expected subset relationship, and a calibration run over the same fixture produces a well-formed reliability report
    - _Requirements: 13.3, 12.2_

- [x] 17. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirements (granular sub-clauses) for traceability.
- Property tests use the repo's existing `hypothesis` setup; exactly one property test implements each of design Properties 1–39, each tagged with a `# Feature: volatility-aware-forecaster, Property {n}: {property_text}` comment and running ≥ 100 examples.
- Checkpoints ensure incremental validation at the forecaster core, tool, consumer, and final layers.
- The forecaster is the single source of truth for the forecast math; the live tool path, the backtest comparison, and the calibration measurement all call `forecaster.forecast` and `forecaster.resolve_forecaster_config`, feeding only different point-in-time candle windows (no look-ahead).
- The regime trend state is reused from the existing `regime.classify_regime` rather than reimplemented; a `transitional` state or an unavailable regime maps to a neutral blend and never blocks the forecast.
- The forecaster is a predictive cross-check and calibration aid: it produces only a Forecast_Label or an Unavailable_Marker, never a BUY/SELL/HOLD decision, never overrides or blocks a committed trade, and never fabricates forecast fields when a forecast cannot be computed.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "1.2", "1.3"] },
    { "id": 2, "tasks": ["3.1", "2.2", "2.3"] },
    { "id": 3, "tasks": ["4.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7"] },
    { "id": 4, "tasks": ["6.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10"] },
    { "id": 5, "tasks": ["6.2"] },
    { "id": 6, "tasks": ["8.1", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "6.10"] },
    { "id": 7, "tasks": ["9.1", "8.2", "8.3"] },
    { "id": 8, "tasks": ["10.1", "11.1", "12.1", "9.2", "9.3", "9.4", "9.5"] },
    { "id": 9, "tasks": ["12.2", "10.2", "11.2", "11.3", "12.3"] },
    { "id": 10, "tasks": ["14.1", "12.4", "12.5"] },
    { "id": 11, "tasks": ["14.2"] },
    { "id": 12, "tasks": ["15.1", "14.3", "14.4", "14.5", "14.6"] },
    { "id": 13, "tasks": ["15.2", "15.3", "15.4", "16.1", "16.2", "16.3", "16.4"] }
  ]
}
```
