# Implementation Plan: Regime Detection Gate

## Overview

This plan implements the Regime Detection Gate as a pure-Python `Regime_Classifier` (new `regime.py`) wired through the existing Deep Quant layers: the `get_market_regime` tool and `validate_contract` (`tools.py`), graph wiring/defensibility/prompts (`graph.py`), the regime verification step (`stream_events.py`), the journal setup fingerprint (`journal.py`), and the with-gate/without-gate backtest comparison (`backtest.py`). Work proceeds bottom-up: the pure classifier and its threshold resolution first (testable in isolation), then the tool and contract, then graph/audit/measurement consumers, and finally the backtest comparison and end-to-end wiring. Property-based tests use the repo's existing `hypothesis` setup; exactly one property test implements each of design Properties 1–32. All code is Python under `agents/deep-quant-loop/`.

## Tasks

- [ ] 1. Threshold resolution foundation in `regime.py`
  - [-] 1.1 Create `regime.py` with `RegimeConfig` and `resolve_regime_config`
    - Create the new module `agents/deep-quant-loop/regime.py`
    - Define documented default constants (ADX cutoff, chop cutoff, vol low/high percentiles, min candles, lookback periods)
    - Define the frozen `RegimeConfig` dataclass with the `largest_lookback` property
    - Implement `resolve_regime_config()`: read each threshold from its own env var; fall back to the documented default on unset/empty/unparseable/out-of-range; revert BOTH volatility-percentile cutoffs to defaults when `vol_low_pctl >= vol_high_pctl`; never raise
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [~] 1.2 Write property test for per-threshold default fallback
    - **Property 28: Each threshold falls back to its documented default**
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4**

  - [~] 1.3 Write property test for volatility-percentile ordering enforcement
    - **Property 29: Volatility-percentile ordering is enforced**
    - **Validates: Requirements 11.5**

  - [~] 1.4 Write property test for deterministic, path-independent resolution
    - **Property 30: Threshold resolution is deterministic and path-independent**
    - **Validates: Requirements 11.6**

- [ ] 2. Regime measure functions in `regime.py`
  - [~] 2.1 Implement the pure measure functions
    - Implement `compute_directional_strength`, `compute_choppiness`, `compute_efficiency_ratio`, `compute_atr_percentile`, `compute_bb_width`
    - Exclude candles with non-finite/non-numeric OHLCV fields from all computations
    - Clamp bounded measures into range (efficiency [0,1], choppiness [0,100], atr-percentile [0,100])
    - Return `None` when a denominator is zero (e.g. zero range over the window); return finite values otherwise; never raise
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 2.2, 2.5, 2.6_

  - [~] 2.2 Write property test for bounded-measure clamping
    - **Property 4: Bounded measures are clamped within their range**
    - **Validates: Requirements 2.5**

  - [~] 2.3 Write property test for non-finite candle exclusion
    - **Property 7: Non-finite candles are excluded without affecting the result**
    - **Validates: Requirements 2.2**

- [ ] 3. Classification functions and `classify_regime` in `regime.py`
  - [~] 3.1 Implement classification and the top-level entry point
    - Implement `classify_trend_state` (trending/ranging/transitional), `classify_volatility_state` (low/normal/high), and `derive_favorability` (favorable/unfavorable/neutral) per the design mapping tables — each a total function
    - Implement `classify_regime(candles, config)`: compute measures from valid candles only and assemble a `Regime_Label`; return an `Unavailable_Marker` (with received vs required counts) when valid candles are fewer than the largest lookback; return an `Unavailable_Marker` when every required measure is `None`
    - Keep all functions pure (no input mutation), deterministic, and non-raising; emit only a label or marker (never a BUY/SELL/HOLD action, conviction, or decision field)
    - _Requirements: 1.1, 1.2, 1.3, 1.8, 1.9, 1.10, 1.11, 2.1, 2.3, 2.4, 2.7, 2.8, 12.1, 12.2, 12.3, 12.4_

  - [~] 3.2 Write property test for classification determinism
    - **Property 1: Classification is deterministic**
    - **Validates: Requirements 1.2, 2.8**

  - [~] 3.3 Write property test for classifier purity
    - **Property 2: Classifier functions are pure (no input mutation)**
    - **Validates: Requirements 1.1, 1.11, 12.2, 12.4**

  - [~] 3.4 Write property test for present, finite-or-null measures
    - **Property 3: Computed measures are present and finite-or-null**
    - **Validates: Requirements 1.4, 1.5, 1.6, 1.7, 2.4**

  - [~] 3.5 Write property test for well-formed states matching the mapping
    - **Property 5: Label states are well-formed and match the threshold mapping**
    - **Validates: Requirements 1.8, 1.9**

  - [~] 3.6 Write property test for total favorability derivation
    - **Property 6: Favorability is a total function of Trend_State and Volatility_State**
    - **Validates: Requirements 1.10**

  - [~] 3.7 Write property test for insufficient-candle unavailability
    - **Property 8: Insufficient valid candles yield an Unavailable_Marker with counts**
    - **Validates: Requirements 1.3, 2.1, 2.3**

  - [~] 3.8 Write property test for zero-denominator and all-null handling
    - **Property 9: Zero-denominator measures are null, and all-null yields unavailable**
    - **Validates: Requirements 2.6, 2.7**

  - [~] 3.9 Write property test that the classifier never emits a trade decision
    - **Property 31: The classifier never emits a trade decision**
    - **Validates: Requirements 12.1, 12.3**

- [~] 4. Checkpoint - classifier core
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. `get_market_regime` tool and contract in `tools.py`
  - [~] 5.1 Extend `validate_contract` with a `get_market_regime` branch
    - Add the regime enum sets and measure field names
    - Pass conforming labels and Unavailable_Markers through unchanged; return `{"error", "contract_violation"}` naming the offending field on non-conforming results; keep it wrapped so validation never raises
    - _Requirements: 3.5, 3.6, 3.7, 3.8_

  - [~] 5.2 Implement the `get_market_regime` tool
    - Add the `@tool`-decorated `get_market_regime(symbol, timeframe)` following the existing tool pattern
    - Validate args (empty/whitespace symbol or unsupported timeframe → structured error); resolve config via `regime.resolve_regime_config()`; fetch candles from the Rust Tool_Server with a sufficient limit; classify via `regime.classify_regime`; re-validate via `validate_contract`
    - Return an `Unavailable_Marker` (citing the cause) on retrieval timeout/failure or any processing error; omit trend/volatility/favorability when unavailable; never propagate an exception
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.9, 4.1, 4.2, 4.3, 4.5, 4.6, 12.2_

  - [~] 5.3 Write unit tests for tool shape
    - Assert `get_market_regime` is `@tool`-decorated, named `get_market_regime`, and accepts `symbol` and `timeframe`
    - _Requirements: 3.1, 3.2_

  - [~] 5.4 Write property test for invalid-argument rejection
    - **Property 10: The tool rejects invalid arguments without raising**
    - **Validates: Requirements 3.3**

  - [~] 5.5 Write property test for a well-formed successful result
    - **Property 11: A successful tool result is well-formed**
    - **Validates: Requirements 3.4**

  - [~] 5.6 Write property test for contract identity on conforming results/markers
    - **Property 12: validate_contract is the identity on conforming results and markers**
    - **Validates: Requirements 3.5, 3.7**

  - [~] 5.7 Write property test for contract rejection naming the field
    - **Property 13: validate_contract rejects non-conforming results, naming the field**
    - **Validates: Requirements 3.6**

  - [~] 5.8 Write property test that validate_contract never raises
    - **Property 14: validate_contract never raises on a regime result**
    - **Validates: Requirements 3.8**

  - [~] 5.9 Write property test for graceful degradation to Unavailable_Marker
    - **Property 15: The tool degrades to an Unavailable_Marker on any retrieval or processing failure**
    - **Validates: Requirements 4.1, 4.5**

  - [~] 5.10 Write property test that an Unavailable_Marker carries no fabricated states
    - **Property 16: An Unavailable_Marker never carries fabricated states**
    - **Validates: Requirements 4.3, 4.6**

- [~] 6. Checkpoint - tool and contract
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Graph wiring of the tool in `graph.py`
  - [~] 7.1 Register the tool and confirm the market-data gate
    - Add `get_market_regime` to the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - Confirm the existing `_market_data_seen` logic sets the flag on a usable result and not on an error/Unavailable_Marker, and that the flag stays true once set
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [~] 7.2 Write unit tests for tool registration
    - Assert `get_market_regime` appears in the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - _Requirements: 5.1, 5.2, 5.3_

  - [~] 7.3 Write property test for the market-data gate classification and monotonicity
    - **Property 17: The market-data gate classifies regime results correctly and stays monotone**
    - **Validates: Requirements 5.4, 5.5, 5.6**

- [ ] 8. Regime in the defensibility record (`graph.py`)
  - [~] 8.1 Add the regime entry to `build_defensibility_record`
    - Implement `_regime_entry` reading the most recent `get_market_regime` result from message history; copy trend/volatility/favorability and named measures verbatim (no inference)
    - Mark the entry unavailable (no substitute values) when no usable result is present
    - Add the explicit "trade opposes the regime assessment" statement when favorability is `unfavorable` and the committed action is BUY or SELL; leave the decision's action and execution levels unchanged
    - _Requirements: 4.4, 7.1, 7.2, 7.3, 7.4, 12.5, 12.6_

  - [~] 8.2 Write property test that the entry mirrors the tool result
    - **Property 18: The defensibility regime entry mirrors the tool result without fabrication**
    - **Validates: Requirements 7.1, 7.2**

  - [~] 8.3 Write property test that absent regime is recorded as unavailable
    - **Property 19: Absent regime is recorded as unavailable**
    - **Validates: Requirements 7.3**

  - [~] 8.4 Write property test for the unfavorable-directional opposition statement
    - **Property 20: An unfavorable directional trade records the opposition statement**
    - **Validates: Requirements 7.4**

  - [~] 8.5 Write property test that the gate never modifies or blocks a decision
    - **Property 32: The regime gate never modifies or blocks a committed decision**
    - **Validates: Requirements 12.5, 12.6**

- [ ] 9. Prompt integration of the Regime Gate (`graph.py`)
  - [~] 9.1 Update the system and risk-manager prompts
    - Update `DEEP_QUANT_SYSTEM_PROMPT`: call `get_market_regime` in order_of_operations; check favorability before a directional (BUY/SELL) trade; require lower conviction / wait / HOLD when unfavorable for the setup type; disclose Trend/Volatility/Favorability in setup_validation; note unavailable-and-proceed
    - Update `RISK_MANAGER_PROMPT`: consult `get_market_regime` while verifying; include the explicit unfavorable-regime warning statement; note unavailable-and-proceed
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [~] 9.2 Write unit tests for prompt content
    - Assert the prompts contain the regime-call, favorability-check, unfavorable-guidance, disclosure, warning, and unavailable-and-proceed instructions
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

- [ ] 10. Regime verification step in `stream_events.py`
  - [~] 10.1 Emit the regime `VERIFICATION_STEP`
    - Implement `_regime_step` mapping the defensibility regime entry to a step with stable check id `market-regime`: favorable→`pass`, unfavorable→`fail`, neutral→`informational`, unavailable→`not-evaluable` (with an unavailable indication, no fabricated favorability)
    - Wire it into `_derive_find_mode_steps` (and VERIFY mode) so exactly one regime step is emitted, ordered before the `DECISION` event
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [~] 10.2 Write property test for the regime verification step outcome mapping
    - **Property 21: Exactly one regime verification step with the correct outcome mapping**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5**

  - [~] 10.3 Write property test that the step precedes the DECISION event
    - **Property 22: The regime verification step precedes the DECISION event**
    - **Validates: Requirements 8.6**

- [ ] 11. Journal setup-fingerprint extension in `journal.py`
  - [~] 11.1 Add the regime tag to `derive_setup_tags`
    - Define the fixed `REGIME_TAG_VALUES` enumeration (≤ 8 values including `unknown`)
    - Implement `_regime_tag` reading the regime from the decision's defensibility record and collapsing (Trend_State × Favorability) into one fixed value; missing/empty/unrecognized → `regime:unknown`
    - Append exactly one `regime:<value>` tag at a fixed position so `setup_key` stays deterministic and low-cardinality
    - _Requirements: 9.1, 9.2, 9.3_

  - [~] 11.2 Write property test for the single fixed-position low-cardinality tag
    - **Property 23: Exactly one low-cardinality regime tag at a fixed position**
    - **Validates: Requirements 9.1, 9.2, 9.3**

  - [~] 11.3 Write property test for per-regime aggregation metrics
    - **Property 24: Per-regime aggregation reports correct win-rate and expectancy**
    - **Validates: Requirements 9.4, 9.5**

- [~] 12. Checkpoint - audit and measurement consumers
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Backtest with-gate / without-gate comparison in `backtest.py`
  - [~] 13.1 Classify signals look-ahead-free and label seeded trades
    - Add `regime_gate_enabled` to `BacktestConfig` and reuse `regime.resolve_regime_config()`
    - Classify each signal's regime via `regime.classify_regime` using only candles at or before the signal's candle timestamp (no look-ahead)
    - Populate each seeded trade's `decision['defensibility']['regime']` so `journal._regime_tag` labels it
    - _Requirements: 10.1, 10.3, 10.5, 11.6_

  - [~] 13.2 Implement the gate logic and `compare` entry point
    - With-gate run: drop signals whose favorability is `unfavorable` for their setup type; retain signals whose regime is an `Unavailable_Marker`
    - Add a `compare(...)` entry point running gated and ungated over identical history/rules; report each run's win-rate (winning closed / closed) and expectancy (mean realized R); report `"n/a"` when a run has zero closed trades
    - _Requirements: 10.2, 10.4, 10.6, 10.7_

  - [~] 13.3 Write unit test that the backtest reuses the classifier
    - Assert `backtest.py` imports and calls `regime.classify_regime` rather than reimplementing the math
    - _Requirements: 10.5_

  - [~] 13.4 Write property test for look-ahead-free classification
    - **Property 25: Backtest regime classification is look-ahead-free**
    - **Validates: Requirements 10.1**

  - [~] 13.5 Write property test for gate exclusion/retention behavior
    - **Property 26: The enabled gate excludes unfavorable signals and retains unavailable ones**
    - **Validates: Requirements 10.2, 10.6**

  - [~] 13.6 Write property test for comparison-mode consistency and metrics
    - **Property 27: Comparison-mode runs are consistent and metrics are well-defined**
    - **Validates: Requirements 10.3, 10.4, 10.7**

- [ ] 14. Integration and end-to-end wiring
  - [~] 14.1 Write integration test for the non-blocking data gate
    - A single unavailable regime ToolMessage alone does not satisfy the data gate nor force a decision
    - _Requirements: 4.4_

  - [~] 14.2 Write end-to-end example test for a favorable FIND-mode run
    - A mocked favorable regime produces a defensibility regime entry, a `pass` regime verification step ordered before the DECISION, and a `regime:trend-favorable` journal tag
    - _Requirements: 6.4, 7.1, 8.2, 8.6, 9.1_

  - [~] 14.3 Write smoke test for comparison-mode backtest
    - A comparison-mode backtest over a fixed candle fixture produces with-gate and without-gate summaries with the expected subset relationship
    - _Requirements: 10.2, 10.4_

- [~] 15. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirements (granular sub-clauses) for traceability.
- Property tests use the repo's existing `hypothesis` setup; exactly one property test implements each of design Properties 1–32, each tagged with a `# Feature: regime-detection-gate, Property {n}` comment and running ≥ 100 examples.
- Checkpoints ensure incremental validation at the classifier, tool, consumer, and final layers.
- The classifier is the single source of truth for the regime math; the tool path and backtest path both call `regime.classify_regime` and `regime.resolve_regime_config`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["3.1", "2.2", "2.3"] },
    { "id": 3, "tasks": ["5.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9"] },
    { "id": 4, "tasks": ["5.2"] },
    { "id": 5, "tasks": ["7.1", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "5.10"] },
    { "id": 6, "tasks": ["8.1", "7.2", "7.3"] },
    { "id": 7, "tasks": ["9.1", "10.1", "11.1", "8.2", "8.3", "8.4", "8.5"] },
    { "id": 8, "tasks": ["13.1", "9.2", "10.2", "10.3", "11.2", "11.3"] },
    { "id": 9, "tasks": ["13.2"] },
    { "id": 10, "tasks": ["13.3", "13.4", "13.5", "13.6", "14.1", "14.2", "14.3"] }
  ]
}
```
