# Implementation Plan: Relative Strength & Index Context

## Overview

This plan implements the Relative Strength & Index Context as a pure-Python `Relative_Strength_Calculator` (new `rs.py`) wired through the existing Deep Quant layers: the `get_relative_strength` tool and `validate_contract` (`tools.py`), graph wiring/defensibility/prompts (`graph.py`), the relative-strength verification step (`stream_events.py`), the journal setup fingerprint (`journal.py`), and the with-filter/without-filter backtest comparison (`backtest.py`). Work proceeds bottom-up: parameter resolution and the Benchmark_Map first, then the pure measure and time-alignment functions, then classification and the top-level `classify_relative_strength`, then the tool and contract, then the graph/audit/measurement consumers, and finally the backtest comparison and end-to-end wiring.

The calculator is the single source of truth for the relative-strength math; both the live tool path and the backtest path call `rs.classify_relative_strength` and `rs.resolve_rs_config`. Property-based tests use the repo's existing `hypothesis` setup (note the `agents/deep-quant-loop/.hypothesis` cache); exactly one property test implements each of design Properties 1–35. All code is Python under `agents/deep-quant-loop/`.

## Tasks

- [x] 1. Parameter resolution and Benchmark_Map foundation in `rs.py`
  - [x] 1.1 Create `rs.py` with `RSConfig`, `resolve_rs_config`, and the Benchmark_Map
    - Create the new module `agents/deep-quant-loop/rs.py` (no `httpx`, no file/clock access)
    - Define documented default constants (`DEFAULT_RS_LOOKBACK`, `DEFAULT_RS_CORR_WINDOW`, `DEFAULT_RS_LEADER_CUTOFF`, `DEFAULT_RS_LAGGARD_CUTOFF`, `DEFAULT_RS_INDEX_FLAT_BAND`, `DEFAULT_RS_MIN_CANDLES`, `DEFAULT_BENCHMARK`, `DEFAULT_BENCHMARK_MAP`)
    - Define the frozen `RSConfig` dataclass with the `largest_lookback` property
    - Implement `resolve_rs_config()`: read each parameter from its own env var (`RS_LOOKBACK`, `RS_CORR_WINDOW`, `RS_LEADER_CUTOFF`, `RS_LAGGARD_CUTOFF`, `RS_INDEX_FLAT_BAND`, `RS_MIN_CANDLES`); fall back to the documented default on unset/empty/unparseable/out-of-range; revert BOTH cutoffs to defaults when `laggard_cutoff >= leader_cutoff`; never raise
    - Implement `resolve_benchmark(symbol, explicit=None)`: explicit non-empty argument wins; else `RS_BENCHMARK_MAP`/`DEFAULT_BENCHMARK_MAP` entry; else documented default Benchmark_Index; never raise
    - _Requirements: 2.1, 2.2, 2.3, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x] 1.2 Write property test for per-parameter default fallback
    - **Property 30: Each parameter falls back to its documented default**
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4**

  - [x] 1.3 Write property test for cutoff-ordering enforcement
    - **Property 31: Cutoff ordering is enforced**
    - **Validates: Requirements 12.5**

  - [x] 1.4 Write property test for deterministic, path-independent resolution
    - **Property 32: Parameter resolution is deterministic and path-independent**
    - **Validates: Requirements 12.6**

  - [x] 1.5 Write property test for unmapped-symbol benchmark resolution
    - **Property 33: Unmapped symbols resolve to the default benchmark**
    - **Validates: Requirements 2.2**

- [x] 2. Time-alignment and measure functions in `rs.py`
  - [x] 2.1 Implement `time_align` and the pure measure functions
    - Implement `time_align(symbol_candles, benchmark_candles)`: project both sequences to equal-length lists of valid rows whose timestamps are common to both, in ascending timestamp order; drop candles with non-finite/non-numeric OHLCV (including timestamp); never mutate inputs
    - Implement `compute_rs_ratio_slope`, `compute_relative_return`, `compute_correlation`, `compute_beta`, `compute_index_return` over time-aligned candles
    - Exclude candles with non-finite/non-numeric OHLCV fields from all computations
    - Clamp bounded measures into range (correlation into `[-1.0, 1.0]`)
    - Return `None` when a denominator is zero (zero benchmark price, zero return variance); return finite values otherwise; never raise
    - _Requirements: 1.3, 1.4, 1.5, 3.2, 3.4, 3.5, 3.7_

  - [x] 2.2 Write property test for bounded-measure clamping
    - **Property 4: Bounded measures are clamped within their range**
    - **Validates: Requirements 3.4**

  - [x] 2.3 Write property test for non-finite candle exclusion
    - **Property 8: Non-finite candles are excluded without affecting the result**
    - **Validates: Requirements 3.2**

  - [x] 2.4 Write property test for time-alignment over common timestamps
    - **Property 9: Time-alignment makes the result depend only on common-timestamp candles**
    - **Validates: Requirements 3.7**

- [x] 3. Classification functions and `classify_relative_strength` in `rs.py`
  - [x] 3.1 Implement classification and the top-level entry point
    - Implement `classify_index_direction` (`up`/`down`/`flat`), `classify_relative_strength_state` (`leader`/`inline`/`laggard`), and `derive_alignment` (`aligned`/`misaligned`/`neutral`) per the design mapping tables — each a total function; `derive_alignment` returns `neutral` for an absent proposed direction
    - Implement `classify_relative_strength(symbol_candles, benchmark_candles, config, proposed_direction=None, symbol=None, benchmark=None, timeframe=None)`: time-align, compute measures from valid candles only, and assemble a `Relative_Strength_Label`; return an `Unavailable_Marker` (with aligned-available vs required counts) when aligned valid candles are fewer than `largest_lookback`; return an `Unavailable_Marker` when every required measure is `None`; the marker omits `index_direction`/`relative_strength_state`/`alignment`
    - Keep all functions pure (no input mutation), deterministic, and non-raising; emit only a label or marker (never a BUY/SELL/HOLD action, conviction, or decision field)
    - _Requirements: 1.1, 1.2, 1.6, 1.7, 1.8, 1.9, 1.10, 3.1, 3.3, 3.6, 5.2, 5.3, 13.1, 13.3_

  - [x] 3.2 Write property test for classification determinism
    - **Property 1: Classification is deterministic**
    - **Validates: Requirements 1.2**

  - [x] 3.3 Write property test for calculator purity
    - **Property 2: Calculator functions are pure (no input mutation)**
    - **Validates: Requirements 1.1, 1.10**

  - [x] 3.4 Write property test for present, finite-or-null, correct measures
    - **Property 3: Computed measures are present, finite-or-null, and correct**
    - **Validates: Requirements 1.3, 1.4, 1.5, 3.3**

  - [x] 3.5 Write property test for well-formed states matching the threshold mapping
    - **Property 5: Label states are well-formed and match the threshold mapping**
    - **Validates: Requirements 1.6, 1.7**

  - [x] 3.6 Write property test for total alignment derivation
    - **Property 6: Alignment is a total function of its three inputs**
    - **Validates: Requirements 1.8**

  - [x] 3.7 Write property test for absent-direction neutral alignment
    - **Property 7: Absent proposed direction yields a neutral alignment with the other fields present**
    - **Validates: Requirements 1.9**

  - [x] 3.8 Write property test for insufficient-aligned-candle unavailability
    - **Property 10: Insufficient aligned candles yield an Unavailable_Marker with counts**
    - **Validates: Requirements 3.1, 5.2**

  - [x] 3.9 Write property test for zero-denominator and all-null handling
    - **Property 11: Zero-denominator measures are null, and all-null yields unavailable**
    - **Validates: Requirements 3.5, 3.6**

  - [x] 3.10 Write property test that an Unavailable_Marker carries no fabricated states
    - **Property 12: An Unavailable_Marker never carries fabricated states**
    - **Validates: Requirements 5.3**

  - [x] 3.11 Write property test that the calculator never emits a trade decision
    - **Property 34: The calculator never emits a trade decision**
    - **Validates: Requirements 13.1, 13.3**

- [x] 4. Checkpoint - calculator core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. `get_relative_strength` tool and contract in `tools.py`
  - [x] 5.1 Extend `validate_contract` with a `get_relative_strength` branch
    - Add the enum sets (`INDEX_DIRECTIONS`, `RELATIVE_STRENGTH_STATES`, `ALIGNMENT_VALUES`) and `_RS_MEASURE_FIELDS`
    - Pass conforming labels and Unavailable_Markers through unchanged; return `{"error", "contract_violation"}` naming the offending field on non-conforming results (out-of-enum state, missing required field, missing `benchmark` string, non-numeric/non-null measure); keep it wrapped so validation never raises
    - _Requirements: 4.6, 4.7, 4.8, 4.9_

  - [x] 5.2 Implement the `get_relative_strength` tool
    - Add the `@tool`-decorated `get_relative_strength(symbol, timeframe, benchmark="", proposed_direction="")` following the existing tool pattern
    - Validate args (empty/whitespace symbol or unsupported timeframe → structured error); resolve the benchmark via `rs.resolve_benchmark(symbol, benchmark)`; resolve config via `rs.resolve_rs_config()`
    - Fetch BOTH the symbol candles and the Benchmark_Index candles from `RUST_SERVER_URL/tools/get_candles`; classify via `rs.classify_relative_strength`; re-validate via `validate_contract`
    - Add the `_relative_strength_unavailable(symbol, timeframe, benchmark, reason)` helper that omits `index_direction`/`relative_strength_state`/`alignment`; return an `Unavailable_Marker` on a missing benchmark (naming it), on retrieval timeout/failure/error payload (citing the cause), or on any processing error; never propagate an exception
    - _Requirements: 2.4, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.5, 13.2_

  - [x] 5.3 Write unit test for tool shape
    - Assert `get_relative_strength` is `@tool`-decorated, named `get_relative_strength`, and accepts `symbol`, an optional `benchmark`, and `timeframe`
    - _Requirements: 4.1, 4.2_

  - [x] 5.4 Write unit test that the tool fetches both candle series from the Rust Tool_Server
    - Assert two `get_candles` calls (symbol and benchmark) with mocks, and that no options-chain/non-candle source is consumed
    - _Requirements: 4.4, 13.2_

  - [x] 5.5 Write unit test for Benchmark_Map resolution and override
    - Assert a known mapped symbol resolves to its benchmark (e.g. a bank symbol → `BANKNIFTY`) and that an `RS_BENCHMARK_MAP` override added via configuration is respected
    - _Requirements: 2.1, 2.3_

  - [x] 5.6 Write property test for invalid-argument rejection
    - **Property 13: The tool rejects invalid arguments without raising**
    - **Validates: Requirements 4.3**

  - [x] 5.7 Write property test for a well-formed successful result
    - **Property 14: A successful tool result is well-formed**
    - **Validates: Requirements 4.5**

  - [x] 5.8 Write property test for contract identity on conforming results/markers
    - **Property 15: validate_contract is the identity on conforming results and markers**
    - **Validates: Requirements 4.6, 4.8**

  - [x] 5.9 Write property test for contract rejection naming the field
    - **Property 16: validate_contract rejects non-conforming results, naming the field**
    - **Validates: Requirements 4.7**

  - [x] 5.10 Write property test that validate_contract never raises
    - **Property 17: validate_contract never raises on a relative-strength result**
    - **Validates: Requirements 4.9**

  - [x] 5.11 Write property test for graceful degradation on missing benchmark / retrieval / processing failure
    - **Property 18: The tool degrades to an Unavailable_Marker on missing benchmark or any retrieval/processing failure**
    - **Validates: Requirements 2.4, 5.1, 5.5**

- [x] 6. Checkpoint - tool and contract
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Graph wiring of the tool in `graph.py`
  - [x] 7.1 Register the tool and confirm the market-data gate
    - Add `get_relative_strength` to the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - Confirm the existing `market_data_seen` logic sets the flag on a usable result and not on an error/Unavailable_Marker, and that the flag stays true once set
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 7.2 Write unit test for tool registration
    - Assert `get_relative_strength` appears in the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 7.3 Write property test for the market-data gate classification and monotonicity
    - **Property 19: The market-data gate classifies relative-strength results correctly and stays monotone**
    - **Validates: Requirements 6.4, 6.5**

- [x] 8. Relative strength in the defensibility record (`graph.py`)
  - [x] 8.1 Add the relative-strength entry to `build_defensibility_record`
    - Implement `_relative_strength_entry(results)` reading the most recent `get_relative_strength` result from message history; copy `index_direction`/`relative_strength_state`/`alignment`, the named measures, and the `benchmark` verbatim (no inference)
    - Mark the entry `{'available': False, ...}` (no substitute values) when no usable result is present
    - Add the explicit "trade fights the index / trades a laggard against its benchmark" statement when `alignment == "misaligned"` and the committed action is BUY or SELL; leave the decision's action and execution levels unchanged
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 13.4, 13.5_

  - [x] 8.2 Write property test that the entry mirrors the tool result
    - **Property 20: The defensibility relative-strength entry mirrors the tool result without fabrication**
    - **Validates: Requirements 8.1, 8.2**

  - [x] 8.3 Write property test that absent relative strength is recorded as unavailable
    - **Property 21: Absent relative strength is recorded as unavailable**
    - **Validates: Requirements 8.3**

  - [x] 8.4 Write property test for the misaligned-directional opposition statement
    - **Property 22: A misaligned directional trade records the opposition statement**
    - **Validates: Requirements 8.4**

  - [x] 8.5 Write property test that the context never modifies or blocks a decision
    - **Property 35: The relative-strength context never modifies or blocks a committed decision**
    - **Validates: Requirements 13.4, 13.5**

- [x] 9. Prompt integration of relative strength (`graph.py`)
  - [x] 9.1 Update the system and risk-manager prompts
    - Update `DEEP_QUANT_SYSTEM_PROMPT`: call `get_relative_strength` for the symbol/timeframe in order_of_operations; check Index_Direction/Relative_Strength_State Alignment before a directional (BUY/SELL) trade; require lower conviction / wait / HOLD when `misaligned`; disclose Index_Direction/Relative_Strength_State/Alignment in setup_validation; note unavailable-and-proceed
    - Update `RISK_MANAGER_PROMPT`: consult `get_relative_strength` while verifying; include the explicit misaligned warning statement; note unavailable-and-proceed
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 9.2 Write unit tests for prompt content
    - Assert the prompts contain the relative-strength-call, alignment-check, misaligned-guidance, disclosure, warning, and unavailable-and-proceed instructions
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 10. Relative-strength verification step in `stream_events.py`
  - [x] 10.1 Emit the relative-strength `VERIFICATION_STEP`
    - Implement `_relative_strength_step(record)` mapping the defensibility relative-strength entry to a step with stable check id `relative-strength`: `aligned`→`pass`, `misaligned`→`fail`, `neutral`→`informational`, unavailable→`not-evaluable` (with an unavailable indication, no fabricated Alignment)
    - Wire it into `_derive_find_mode_steps` (and VERIFY mode) so exactly one relative-strength step is emitted, ordered before the `DECISION` event
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 10.2 Write property test for the verification step outcome mapping
    - **Property 23: Exactly one relative-strength verification step with the correct outcome mapping**
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5**

  - [x] 10.3 Write property test that the step precedes the DECISION event
    - **Property 24: The relative-strength verification step precedes the DECISION event**
    - **Validates: Requirements 9.6**

- [x] 11. Journal setup-fingerprint extension in `journal.py`
  - [x] 11.1 Add the relative-strength tag to `derive_setup_tags`
    - Define the fixed `RS_TAG_VALUES` enumeration (≤ 8 values including `unknown`)
    - Implement `_relative_strength_tag(decision)` reading relative strength from `decision['defensibility']['relative_strength']` and collapsing (Relative_Strength_State × Alignment) into one fixed value; missing/empty/unrecognized → `rs:unknown`
    - Append exactly one `rs:<value>` tag at a fixed position (after the existing `regime:` tag) so `setup_key` stays deterministic and low-cardinality
    - _Requirements: 10.1, 10.2, 10.3_

  - [x] 11.2 Write property test for the single fixed-position low-cardinality tag
    - **Property 25: Exactly one low-cardinality relative-strength tag at a fixed position**
    - **Validates: Requirements 10.1, 10.2, 10.3**

  - [x] 11.3 Write property test for per-relative-strength aggregation metrics
    - **Property 26: Per-relative-strength aggregation reports correct win-rate and expectancy**
    - **Validates: Requirements 10.4, 10.5**

- [x] 12. Checkpoint - audit and measurement consumers
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Backtest with-filter / without-filter comparison in `backtest.py`
  - [x] 13.1 Classify signals look-ahead-free and label seeded trades
    - Add `rs_filter_enabled` to `BacktestConfig` and reuse `rs.resolve_rs_config()`
    - Resolve the benchmark once per run via `rs.resolve_benchmark`; classify each signal's relative strength via `rs.classify_relative_strength` using only symbol and benchmark candles at or before the signal's candle timestamp (no look-ahead), passing the signal's direction as `proposed_direction`
    - Add `_relative_strength_defensibility_entry(rs_result)` to populate each seeded trade's `decision['defensibility']['relative_strength']` so `journal._relative_strength_tag` labels it
    - _Requirements: 11.1, 11.3, 11.5_

  - [x] 13.2 Implement the filter logic and `compare` entry point
    - With-filter run: drop signals whose `alignment` is `misaligned` for their direction; retain signals whose relative-strength result is an `Unavailable_Marker`
    - Add a `compare(...)` entry point (or extend the existing regime `compare`) running filtered and unfiltered over identical history/rules; report each run's win-rate (winning closed / closed) and expectancy (mean realized R); report `"n/a"` when a run has zero closed trades
    - _Requirements: 11.2, 11.4, 11.6, 11.7_

  - [x] 13.3 Write unit test that the backtest reuses the calculator
    - Assert `backtest.py` imports and calls `rs.classify_relative_strength` rather than reimplementing the math
    - _Requirements: 11.5_

  - [x] 13.4 Write property test for look-ahead-free classification
    - **Property 27: Backtest relative-strength classification is look-ahead-free**
    - **Validates: Requirements 11.1**

  - [x] 13.5 Write property test for filter exclusion/retention behavior
    - **Property 28: The enabled filter excludes misaligned signals and retains unavailable ones**
    - **Validates: Requirements 11.2, 11.6**

  - [x] 13.6 Write property test for comparison-mode consistency and metrics
    - **Property 29: Comparison-mode runs are consistent and metrics are well-defined**
    - **Validates: Requirements 11.3, 11.4, 11.7**

- [x] 14. Integration and end-to-end wiring
  - [x] 14.1 Write integration test for the non-blocking data gate
    - A single unavailable relative-strength ToolMessage alone does not satisfy the data gate nor force a decision
    - _Requirements: 5.4_

  - [x] 14.2 Write end-to-end example test for an aligned FIND-mode run
    - A mocked `aligned` relative-strength result produces a defensibility relative-strength entry, a `pass` relative-strength verification step ordered before the DECISION, and an `rs:leader-aligned` journal tag
    - _Requirements: 7.4, 8.1, 9.2, 9.6, 10.1_

  - [x] 14.3 Write smoke test for comparison-mode backtest
    - A comparison-mode backtest over a fixed candle fixture produces with-filter and without-filter summaries with the expected subset relationship
    - _Requirements: 11.2, 11.4_

- [x] 15. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirements (granular sub-clauses) for traceability.
- Property tests use the repo's existing `hypothesis` setup; exactly one property test implements each of design Properties 1–35, each tagged with a `# Feature: relative-strength-context, Property {n}: {property_text}` comment and running ≥ 100 examples.
- Checkpoints ensure incremental validation at the calculator, tool, consumer, and final layers.
- The calculator is the single source of truth for the relative-strength math; the tool path and the backtest path both call `rs.classify_relative_strength` and `rs.resolve_rs_config`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "1.2", "1.3", "1.4", "1.5"] },
    { "id": 2, "tasks": ["3.1", "2.2", "2.3", "2.4"] },
    { "id": 3, "tasks": ["5.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9", "3.10", "3.11"] },
    { "id": 4, "tasks": ["5.2"] },
    { "id": 5, "tasks": ["7.1", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11"] },
    { "id": 6, "tasks": ["8.1", "7.2", "7.3"] },
    { "id": 7, "tasks": ["9.1", "10.1", "11.1", "8.2", "8.3", "8.4", "8.5"] },
    { "id": 8, "tasks": ["13.1", "9.2", "10.2", "10.3", "11.2", "11.3"] },
    { "id": 9, "tasks": ["13.2"] },
    { "id": 10, "tasks": ["13.3", "13.4", "13.5", "13.6", "14.1", "14.2", "14.3"] }
  ]
}
```
