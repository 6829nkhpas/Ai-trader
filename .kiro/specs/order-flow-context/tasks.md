# Implementation Plan: Order Flow Context

## Overview

This plan implements the Order Flow Context feature as a pure-Python `Order_Flow_Calculator` (new `order_flow.py`) wired through the existing Deep Quant layers exactly as `regime-detection-gate` and `relative-strength-context` were: the `get_order_flow` tool and `validate_contract` (`tools.py`), graph wiring/defensibility/prompts (`graph.py`), the order-flow verification step (`stream_events.py`), the journal setup fingerprint (`journal.py`), and the with-filter/without-filter backtest comparison (`backtest.py`).

Work proceeds bottom-up: parameter resolution first, then the candle-derived proxy measure functions, then the tick-rule `compute_tick_ofi` (mirroring the authoritative Rust `compute_order_flow_imbalance`), then classification and the top-level `classify_order_flow`, then the tool and contract, then the graph/audit/measurement consumers, and finally the proxy-only backtest comparison and end-to-end wiring. The calculator is the single source of truth for the order-flow math; both the live tool path and the backtest path call `order_flow.classify_order_flow` and `order_flow.resolve_order_flow_config`.

All code is Python under `agents/deep-quant-loop/`. Property-based tests use the repo's existing `hypothesis` setup (note the `agents/deep-quant-loop/.hypothesis` cache); exactly one property test implements each of design Properties 1–38, each tagged with a `# Feature: order-flow-context, Property {n}: {property_text}` comment and running ≥ 100 examples.

## Tasks

- [ ] 1. Parameter resolution foundation in `order_flow.py`
  - [~] 1.1 Create `order_flow.py` with `OrderFlowConfig` and `resolve_order_flow_config`
    - Create the new module `agents/deep-quant-loop/order_flow.py` (no `httpx`, no file/clock access), mirroring the structure of `regime.py` / `rs.py`
    - Define documented default constants (`DEFAULT_OF_LOOKBACK=20`, `DEFAULT_OF_MIN_CANDLES=20`, `DEFAULT_OF_BUY_PRESSURE_THRESHOLD=0.58`, `DEFAULT_OF_SELL_PRESSURE_THRESHOLD=0.42`, `DEFAULT_OF_OFI_BUY_THRESHOLD=0.20`, `DEFAULT_OF_OFI_SELL_THRESHOLD=-0.20`, `DEFAULT_OF_MIN_TICKS=10`)
    - Define the frozen `OrderFlowConfig` dataclass with the `largest_lookback` property
    - Implement `resolve_order_flow_config()`: read each parameter from its own env var (`OF_LOOKBACK`, `OF_MIN_CANDLES`, `OF_BUY_PRESSURE_THRESHOLD`, `OF_SELL_PRESSURE_THRESHOLD`, `OF_OFI_BUY_THRESHOLD`, `OF_OFI_SELL_THRESHOLD`, `OF_MIN_TICKS`); fall back to the documented default on unset/empty/unparseable/out-of-range; revert BOTH pressure thresholds to defaults when `sell_pressure_threshold >= buy_pressure_threshold` (and apply the same ordering guard to the Tick_OFI buy/sell thresholds); never raise
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [~] 1.2 Write property test for per-parameter default fallback
    - **Property 34: Each parameter falls back to its documented default**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4**

  - [~] 1.3 Write property test for pressure-threshold ordering enforcement
    - **Property 35: Pressure-threshold ordering is enforced**
    - **Validates: Requirements 13.5**

  - [~] 1.4 Write property test for deterministic, path-independent resolution
    - **Property 36: Parameter resolution is deterministic and path-independent**
    - **Validates: Requirements 13.6**

- [ ] 2. Candle-derived proxy measure functions in `order_flow.py`
  - [~] 2.1 Implement the pure, candle-only proxy measure functions
    - Implement `compute_close_location_value(candle)` → `((close - low) - (high - close)) / (high - low)` in `[-1.0, 1.0]`; `None` when `high == low`
    - Implement `compute_candle_delta_proxy(candle)` → close-location value × volume; `None` when the close-location value is `None`
    - Implement `compute_cvd_proxy(candles, lookback)` → running sum of the per-candle delta proxy over the last `lookback` valid candles (None-delta candles contribute 0); `None` when no valid candle is available
    - Implement `compute_up_down_volume(candles, lookback)` → up-volume (volume on candles closing above open) and down-volume (closing below open); candles closing exactly at open contribute to neither
    - Implement `compute_buying_pressure_ratio(candles, lookback)` → up_volume / (up_volume + down_volume) in `[0.0, 1.0]`, clamped; `None` when total directional volume is zero
    - Exclude candles with non-finite/non-numeric OHLCV fields from all computations; keep every function pure (no input mutation) and non-raising; perform zero network calls
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 4.2, 4.3, 4.4, 4.5_

  - [~] 2.2 Write property test for present, finite-or-null, correct proxy measures
    - **Property 3: Proxy measures are present, finite-or-null, and correct**
    - **Validates: Requirements 1.2, 1.3, 1.4, 4.3**

  - [~] 2.3 Write property test for bounded-measure clamping
    - **Property 4: Bounded measures are clamped within their range**
    - **Validates: Requirements 4.4**

- [ ] 3. Tick-rule `compute_tick_ofi` in `order_flow.py`
  - [~] 3.1 Implement `compute_tick_ofi` (mirrors the Rust implementation, AD-8)
    - Implement `compute_tick_ofi(ticks, config)` over a sequence of `(last_price, cumulative_volume, best_bid, best_ask)`: per-tick traded size is the POSITIVE delta of cumulative volume between consecutive ticks (skip negative/session-reset deltas)
    - Sign each delta by the tick rule (uptick=+1 buy, downtick=-1 sell, zero-tick inherits the previous sign), refined by quote location when a usable best bid/ask is present (above mid → +1, below → -1, at mid → tick sign; Lee-Ready style)
    - OFI = net signed volume / total signed volume, clamped to `[-1.0, 1.0]`; never return a non-finite value
    - Return `None` (unavailable) when ticks is empty, has fewer than `config.min_ticks` usable ticks, or yields zero total signed volume — never a fabricated neutral `0.0`
    - Exclude ticks with non-finite/non-numeric fields; keep pure (no input mutation), deterministic, and non-raising
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.2, 14.6_

  - [~] 3.2 Write property test for the normalized signed-volume imbalance within bounds
    - **Property 6: Tick_OFI is the normalized signed-volume imbalance within bounds**
    - **Validates: Requirements 2.1, 2.4**

  - [~] 3.3 Write property test for Lee-Ready quote-location sign refinement
    - **Property 7: Quote location refines the tick sign (Lee-Ready)**
    - **Validates: Requirements 2.2**

  - [~] 3.4 Write property test for insufficient/degenerate ticks yielding unavailable (never fabricated neutral)
    - **Property 8: Insufficient or degenerate ticks yield an unavailable Tick_OFI, never a fabricated neutral**
    - **Validates: Requirements 2.3, 14.6**

  - [~] 3.5 Write unit test that Python `compute_tick_ofi` mirrors the Rust `compute_order_flow_imbalance`
    - Run a shared representative tick fixture through both and assert the OFI matches within floating tolerance (anchors AD-8)
    - _Requirements: 2.1, 2.2_

- [ ] 4. Classification functions and `classify_order_flow` in `order_flow.py`
  - [~] 4.1 Implement classification and the top-level entry point
    - Implement `classify_order_flow_state(tick_ofi, buying_pressure_ratio, config)` → exactly one of `buying`/`selling`/`balanced` with tick-first priority (compare a usable finite Tick_OFI against the Tick_OFI thresholds; otherwise the buying-pressure ratio against the pressure thresholds; a `None` deciding signal → `balanced`) per the design mapping tables
    - Implement `derive_alignment(order_flow_state, proposed_direction)` → exactly one of `aligned`/`misaligned`/`neutral` as a total function (absent/HOLD direction → `neutral`)
    - Implement `classify_order_flow(candles, ticks, config, proposed_direction=None, symbol=None, timeframe=None)`: compute proxy measures from valid candles, compute the Tick_OFI, set `live_tick_contributed` true only when a usable Tick_OFI was produced, classify the Order_Flow_State (tick-first) and derive the Alignment, and assemble an `Order_Flow_Label`
    - Return an `Unavailable_Marker` (reason citing received-vs-required counts) when valid candles are fewer than `largest_lookback`, and when every candle-derived proxy is `null` AND the Tick_OFI is unavailable; the marker omits `order_flow_state`/`alignment`
    - Keep all functions pure (no input mutation), deterministic, non-raising; emit only a label or marker (never a BUY/SELL/HOLD action, conviction, or decision field)
    - _Requirements: 1.6, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.6, 6.3, 14.1, 14.3_

  - [~] 4.2 Write property test for classification determinism
    - **Property 1: Classification is deterministic**
    - **Validates: Requirements 1.6, 2.5**

  - [~] 4.3 Write property test for calculator purity (no input mutation, no network)
    - **Property 2: Calculator functions are pure (no input mutation)**
    - **Validates: Requirements 1.1, 1.7, 2.5**

  - [~] 4.4 Write property test for zero-denominator null and all-null-with-no-tick unavailability
    - **Property 5: Zero-denominator measures are null, and all-null-with-no-tick yields unavailable**
    - **Validates: Requirements 1.5, 4.5, 4.6**

  - [~] 4.5 Write property test for well-formed state matching the threshold mapping
    - **Property 9: Order_Flow_State is well-formed and matches the threshold mapping**
    - **Validates: Requirements 3.1**

  - [~] 4.6 Write property test for tick-first priority over the candle proxies
    - **Property 10: A usable Tick_OFI takes priority over the candle proxies**
    - **Validates: Requirements 3.2, 3.5**

  - [~] 4.7 Write property test for total alignment derivation
    - **Property 11: Alignment is a total function of state and proposed direction**
    - **Validates: Requirements 3.3**

  - [~] 4.8 Write property test for absent-direction neutral alignment with other fields present
    - **Property 12: Absent proposed direction yields a neutral alignment with the other fields present**
    - **Validates: Requirements 3.4, 3.5**

  - [~] 4.9 Write property test for non-finite candle/tick exclusion
    - **Property 13: Non-finite candles and ticks are excluded without affecting the result**
    - **Validates: Requirements 4.2**

  - [~] 4.10 Write property test for insufficient-candle unavailability with counts
    - **Property 14: Insufficient candles yield an Unavailable_Marker with counts**
    - **Validates: Requirements 4.1**

  - [~] 4.11 Write property test that an Unavailable_Marker carries no fabricated states
    - **Property 15: An Unavailable_Marker never carries fabricated states**
    - **Validates: Requirements 4.6, 6.3, 14.6**

  - [~] 4.12 Write property test that the calculator never emits a trade decision
    - **Property 37: The calculator never emits a trade decision**
    - **Validates: Requirements 14.1, 14.3**

- [~] 5. Checkpoint - calculator core
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. `get_order_flow` tool and contract in `tools.py`
  - [~] 6.1 Extend `validate_contract` with a `get_order_flow` branch
    - Add the enum set (`ORDER_FLOW_STATES = {"buying", "selling", "balanced"}`, reusing the existing `ALIGNMENT_VALUES`) and `_OF_MEASURE_FIELDS`
    - Pass `Unavailable_Markers` through unchanged (via the existing `_has_honest_marker`); pass conforming labels through unchanged (`order_flow_state`/`alignment` in their enums, each `_OF_MEASURE_FIELDS` measure present under `measures` as finite-number-or-null, `tick_ofi` finite-number-or-null, `live_tick_contributed` boolean)
    - Return `{"error", "contract_violation"}` naming the offending field on non-conforming results; keep it wrapped so validation never raises
    - _Requirements: 5.6, 5.7, 5.8, 5.9_

  - [~] 6.2 Implement the `get_order_flow` tool and the live-ticks read
    - Add the `@tool`-decorated `get_order_flow(symbol, timeframe, proposed_direction="")` following the existing tool pattern; validate args (empty/whitespace symbol or unsupported timeframe → structured error); resolve config via `order_flow.resolve_order_flow_config()`
    - Fetch the symbol candles from `RUST_SERVER_URL/tools/get_candles` for the proxy layer; on retrieval timeout/failure/error payload return an `Unavailable_Marker` citing the cause
    - Implement `_read_live_ticks(symbol, limit)` reading up to `OF_TICK_FETCH_LIMIT` recent ticks (`last_traded_price`, `volume`, `best_bid`, `best_ask`) for the symbol from the `live_ticks` table via the QuestDB HTTP `/exec` API, oldest-first; return `[]` on any failure/no rows/malformed (never raise)
    - Classify via `order_flow.classify_order_flow(candles, ticks, config, proposed_direction=...)`; re-validate via `validate_contract("get_order_flow", result)`
    - Add `_order_flow_unavailable(symbol, timeframe, reason)` (mirroring `_relative_strength_unavailable`) that omits `order_flow_state`/`alignment`; catch any unexpected exception and return an `Unavailable_Marker`; never propagate an exception into the agent loop
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.5, 6.6, 14.2_

  - [~] 6.3 Write unit test for tool shape
    - Assert `get_order_flow` is `@tool`-decorated, named `get_order_flow`, and accepts `symbol`, `timeframe`, and an optional `proposed_direction`
    - _Requirements: 5.1, 5.2_

  - [~] 6.4 Write unit test that the tool consults candles and the live-ticks source only
    - With mocks, assert the tool fetches symbol candles from the Rust Tool_Server and attempts a `live_ticks` read, and consumes no options-chain/other data source
    - _Requirements: 5.4, 14.2_

  - [~] 6.5 Write property test for invalid-argument rejection
    - **Property 16: The tool rejects invalid arguments without raising**
    - **Validates: Requirements 5.3**

  - [~] 6.6 Write property test for a well-formed successful result
    - **Property 17: A successful tool result is well-formed**
    - **Validates: Requirements 5.5, 3.5**

  - [~] 6.7 Write property test for contract identity on conforming results/markers
    - **Property 18: validate_contract is the identity on conforming results and markers**
    - **Validates: Requirements 5.6, 5.8**

  - [~] 6.8 Write property test for contract rejection naming the field
    - **Property 19: validate_contract rejects non-conforming results, naming the field**
    - **Validates: Requirements 5.7**

  - [~] 6.9 Write property test that validate_contract never raises on an order-flow result
    - **Property 20: validate_contract never raises on an order-flow result**
    - **Validates: Requirements 5.9**

  - [~] 6.10 Write property test for degradation to an Unavailable_Marker on candle-retrieval/processing failure
    - **Property 21: The tool degrades to an Unavailable_Marker on any candle-retrieval or processing failure**
    - **Validates: Requirements 6.2, 6.5**

  - [~] 6.11 Write property test that a missing tick stream degrades only the Tick_OFI
    - **Property 22: A missing tick stream degrades only the Tick_OFI, leaving a usable proxy-only label**
    - **Validates: Requirements 6.1, 6.6**

- [~] 7. Checkpoint - tool and contract
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Graph wiring of the tool in `graph.py`
  - [~] 8.1 Register the tool and confirm the market-data gate
    - Add `get_order_flow` to the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - Confirm the existing `market_data_seen` logic sets the flag on a usable result and not on an error/Unavailable_Marker, and that the flag stays true once set
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [~] 8.2 Write unit test for tool registration
    - Assert `get_order_flow` appears in the bound `tools` list, `REGISTERED_TOOL_NAMES`, and `MARKET_DATA_TOOL_NAMES`
    - _Requirements: 7.1, 7.2, 7.3_

  - [~] 8.3 Write property test for the market-data gate classification and monotonicity
    - **Property 23: The market-data gate classifies order-flow results correctly and stays monotone**
    - **Validates: Requirements 7.4, 7.5**

- [ ] 9. Order flow in the defensibility record (`graph.py`)
  - [~] 9.1 Add the order-flow entry to `build_defensibility_record`
    - Implement `_order_flow_entry(results)` (modelled on `_relative_strength_entry`) reading the most recent `get_order_flow` result from message history; copy `order_flow_state`/`alignment`, the named measures, `tick_ofi`, and `live_tick_contributed` verbatim (no inference)
    - Mark the entry `{'available': False, ...}` (no substitute values) when no usable result is present
    - Add the explicit "trade is taken against the prevailing order flow" statement when `alignment == "misaligned"` and the committed action is BUY or SELL; leave the decision's action and execution levels (entry, stop-loss, take-profit) unchanged
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 14.4, 14.5_

  - [~] 9.2 Write property test that the entry mirrors the tool result
    - **Property 24: The defensibility order-flow entry mirrors the tool result without fabrication**
    - **Validates: Requirements 9.1, 9.2**

  - [~] 9.3 Write property test that absent order flow is recorded as unavailable
    - **Property 25: Absent order flow is recorded as unavailable**
    - **Validates: Requirements 9.3**

  - [~] 9.4 Write property test for the misaligned-directional opposition statement
    - **Property 26: A misaligned directional trade records the opposition statement**
    - **Validates: Requirements 9.4**

  - [~] 9.5 Write property test that the context never modifies or blocks a committed decision
    - **Property 38: The order-flow context never modifies or blocks a committed decision**
    - **Validates: Requirements 14.4, 14.5**

- [ ] 10. Prompt integration of order flow (`graph.py`)
  - [~] 10.1 Update the system and risk-manager prompts
    - Update `DEEP_QUANT_SYSTEM_PROMPT`: call `get_order_flow` for the symbol/timeframe in order_of_operations; check the Order_Flow_State for Alignment before a directional (BUY/SELL) trade; require exactly one of lower conviction / wait / HOLD when `misaligned`; disclose Order_Flow_State / Alignment / live-tick-contributed in setup_validation; note order flow unavailable-and-proceed
    - Update `RISK_MANAGER_PROMPT`: consult `get_order_flow` while verifying a user-proposed trade; include the explicit misaligned warning statement; note unavailable-and-proceed
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [~] 10.2 Write unit tests for prompt content
    - Assert the prompts contain the order-flow call, alignment-check-before-directional, misaligned guidance, setup-validation disclosure, the VERIFY-mode warning, and the unavailable-and-proceed instructions
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [ ] 11. Order-flow verification step in `stream_events.py`
  - [~] 11.1 Emit the order-flow `VERIFICATION_STEP`
    - Implement `_order_flow_step(record)` (modelled on `_relative_strength_step`) mapping the defensibility order-flow entry to a step with stable check id `order-flow`: `aligned`→`pass`, `misaligned`→`fail`, `neutral`→`informational`, unavailable→`not-evaluable` (with an unavailable indication, no fabricated Alignment)
    - Wire it into `_derive_find_mode_steps` (and VERIFY mode) so exactly one order-flow step is emitted, ordered before the `DECISION` event
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [~] 11.2 Write property test for the verification step outcome mapping
    - **Property 27: Exactly one order-flow verification step with the correct outcome mapping**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

  - [~] 11.3 Write property test that the step precedes the DECISION event
    - **Property 28: The order-flow verification step precedes the DECISION event**
    - **Validates: Requirements 10.6**

- [ ] 12. Journal setup-fingerprint extension in `journal.py`
  - [~] 12.1 Add the order-flow tag to `derive_setup_tags`
    - Define the fixed `OF_TAG_VALUES` enumeration (≤ 8 values including `unknown`: `buying-aligned`, `buying-misaligned`, `selling-aligned`, `selling-misaligned`, `balanced-neutral`, `aligned`, `misaligned`, `unknown`)
    - Implement `_order_flow_tag(decision)` reading order flow from `decision['defensibility']['order_flow']` and collapsing (Order_Flow_State × Alignment) into one fixed value; missing/empty/unavailable/unrecognized → `of:unknown`
    - Append exactly one `of:<value>` tag at a fixed position (after the existing `rs:` tag) so `setup_key` stays deterministic and low-cardinality
    - _Requirements: 11.1, 11.2, 11.3_

  - [~] 12.2 Write property test for the single fixed-position low-cardinality tag
    - **Property 29: Exactly one low-cardinality order-flow tag at a fixed position**
    - **Validates: Requirements 11.1, 11.2, 11.3**

  - [~] 12.3 Write property test for per-order-flow aggregation metrics
    - **Property 30: Per-order-flow aggregation reports correct win-rate and expectancy**
    - **Validates: Requirements 11.4, 11.5**

- [~] 13. Checkpoint - audit and measurement consumers
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Backtest with-filter / without-filter comparison (proxy layer) in `backtest.py`
  - [~] 14.1 Classify signals look-ahead-free (proxy-only) and label seeded trades
    - Add `of_filter_enabled` to `BacktestConfig` and reuse `order_flow.resolve_order_flow_config()`
    - Classify each signal's order flow via `order_flow.classify_order_flow` using only `candles[: i + 1]` (the window at/before the signal bar — no look-ahead), passing `ticks=None` (tick history is unavailable in the candle archive — proxy-only) and the signal's direction as `proposed_direction`
    - Add `_order_flow_defensibility_entry(of_result)` (mirroring `_relative_strength_defensibility_entry`) to populate each seeded trade's `decision['defensibility']['order_flow']` so `journal._order_flow_tag` labels it
    - _Requirements: 12.1, 12.2, 12.4, 12.7_

  - [~] 14.2 Implement the filter logic and `compare_order_flow` entry point
    - With-filter run: drop a signal via `_signal_is_of_misaligned(decision)` when `alignment == "misaligned"` for its direction (advancing cooldown exactly as a taken signal would); RETAIN a signal whose order-flow result is an `Unavailable_Marker`
    - Add a `compare_order_flow(...)` entry point (mirroring `compare_relative_strength`) running filtered and unfiltered over identical candle history and identical setup rules; report each run's win-rate (winning closed / closed) and expectancy (mean realized R); report `"n/a"` when a run has zero closed trades
    - _Requirements: 12.3, 12.5, 12.6_

  - [~] 14.3 Write unit test that the backtest reuses the calculator
    - Assert `backtest.py` imports and calls `order_flow.classify_order_flow` (with `ticks=None`) rather than reimplementing the math, and the seeded label has `live_tick_contributed` false
    - _Requirements: 12.2, 12.7_

  - [~] 14.4 Write property test for look-ahead-free classification
    - **Property 31: Backtest order-flow classification is look-ahead-free**
    - **Validates: Requirements 12.1**

  - [~] 14.5 Write property test for filter exclusion/retention behavior
    - **Property 32: The enabled filter excludes misaligned signals and retains unavailable ones**
    - **Validates: Requirements 12.3, 12.6**

  - [~] 14.6 Write property test for comparison-mode consistency and metrics
    - **Property 33: Comparison-mode runs are consistent and metrics are well-defined**
    - **Validates: Requirements 12.4, 12.5**

- [ ] 15. Integration and end-to-end wiring
  - [~] 15.1 Write integration test for the non-blocking data gate
    - A single unavailable order-flow ToolMessage alone does not satisfy the data gate nor force a decision; the agent proceeds with the remaining analysis
    - _Requirements: 6.4_

  - [~] 15.2 Write end-to-end example test for an aligned FIND-mode run
    - A mocked `aligned` order-flow result produces a defensibility order-flow entry, a `pass` order-flow verification step ordered before the DECISION, and an `of:buying-aligned` journal tag
    - _Requirements: 8.4, 9.1, 10.2, 10.6, 11.1_

  - [~] 15.3 Write smoke test for the order-flow tool against stubbed endpoints
    - Against a stubbed Rust candle endpoint and a stubbed `live_ticks` query, the tool returns a contract-valid label; against a stubbed empty tick set it returns a usable proxy-only label with `live_tick_contributed` false
    - _Requirements: 5.5, 6.6_

  - [~] 15.4 Write smoke test for comparison-mode backtest
    - A comparison-mode backtest over a fixed candle fixture produces with-filter and without-filter summaries with the expected subset relationship
    - _Requirements: 12.3, 12.5_

- [~] 16. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirements (granular sub-clauses) for traceability.
- Property tests use the repo's existing `hypothesis` setup; exactly one property test implements each of design Properties 1–38, each tagged with a `# Feature: order-flow-context, Property {n}: {property_text}` comment and running ≥ 100 examples.
- Checkpoints ensure incremental validation at the calculator, tool, consumer, and final layers.
- The calculator is the single source of truth for the order-flow math; the live tool path and the backtest path both call `order_flow.classify_order_flow` and `order_flow.resolve_order_flow_config`. The backtest path passes `ticks=None` (tick history is unavailable in the candle archive) so seeded labels are proxy-only.
- Order flow is a filter / context aid: it produces only a label or an unavailable marker, never a BUY/SELL/HOLD decision, never overrides or blocks a committed trade, and never fabricates a neutral Tick_OFI when live ticks are absent.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["3.1", "2.2", "2.3"] },
    { "id": 3, "tasks": ["4.1", "3.2", "3.3", "3.4", "3.5"] },
    { "id": 4, "tasks": ["6.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11", "4.12"] },
    { "id": 5, "tasks": ["6.2"] },
    { "id": 6, "tasks": ["8.1", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "6.10", "6.11"] },
    { "id": 7, "tasks": ["9.1", "8.2", "8.3"] },
    { "id": 8, "tasks": ["10.1", "11.1", "12.1", "9.2", "9.3", "9.4", "9.5"] },
    { "id": 9, "tasks": ["14.1", "10.2", "11.2", "11.3", "12.2", "12.3"] },
    { "id": 10, "tasks": ["14.2", "14.3"] },
    { "id": 11, "tasks": ["14.4", "14.5", "14.6", "15.1", "15.2", "15.3", "15.4"] }
  ]
}
```
