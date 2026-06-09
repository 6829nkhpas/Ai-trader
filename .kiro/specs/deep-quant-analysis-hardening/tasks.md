# Implementation Plan: Deep-Quant Analysis Hardening

## Overview

This plan hardens the deep-quant analysis core across four runtimes already present in the repo: the Python LangGraph service (`agents/deep-quant-loop/` — `graph.py`, `main.py`, `tools.py`), the Rust Tool Server (`frontend/src-tauri/src/quant/` — `tool_server.rs`, `mod.rs`), the Rust analysis engines (`agents/technical/src/signal_engine.rs`, `agents/quant-rag/`, `agents/predictive/`), and the Node sentiment service (`agents/sentiment/`).

The work is additive and refactoring-oriented. It is sequenced so the pure, deterministic cores (tool-call extractor, loop router, Trade_Validator, conviction scoring, SR engine, watcher predicate, sufficiency classifier) land first and are property-tested early, then external engines and endpoints are wired in, then the glass-box stream, Trade Q&A, and the offline evaluation harness are integrated and verified end-to-end.

Property-based tests use Hypothesis (Python, `max_examples=100`) and proptest (Rust, `cases = 100`). Each property test is its own sub-task, references a numbered design property, and is tagged `Feature: deep-quant-analysis-hardening, Property {n}: {property_text}`. Test-related sub-tasks are marked optional with `*`.

## Tasks

- [ ] 1. Project setup and shared Rust contracts
  - [x] 1.1 Add property-test tooling to all runtimes
    - Add `hypothesis` to `agents/deep-quant-loop/requirements.txt` and a test directory
    - Add `proptest` as a dev-dependency in `frontend/src-tauri/Cargo.toml`, `agents/technical/Cargo.toml`, `agents/quant-rag/Cargo.toml`, and `agents/predictive/Cargo.toml`
    - _Requirements: 15.5_

  - [x] 1.2 Implement the shared timeframe validator and contract helpers (Rust, `quant/mod.rs`)
    - Add a `validate_timeframe(tf) -> Result<(), TimeframeError>` returning a descriptive error naming the offending timeframe and logging the failure
    - Add reusable `finite_opt`-style serialization helpers for numeric-or-null indicator fields
    - _Requirements: 4.5_

  - [x]* 1.3 Write property test for the timeframe validator
    - **Property 16: Unsupported timeframes are rejected with a descriptive error**
    - **Validates: Requirements 4.5**

- [ ] 2. Tool-call extraction (`graph.py`)
  - [x] 2.1 Implement `extract_tool_calls` with `ExtractedCall`/`ToolCallExtraction`
    - Replace `parse_deepseek_custom_tool_calls` and inline cleanup with a single structured extractor
    - Native `tool_calls` path is primary (no text extraction); custom-token markup path classifies each call as `ok`/`parse_failure`/`invalid_tool` and preserves source order
    - Feed only `ok` calls to the ToolNode; turn `parse_failure`/`invalid_tool` into synthetic `ToolMessage`s
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x]* 2.2 Write property test for native tool-call bypass
    - **Property 1: Native tool calls bypass text extraction**
    - **Validates: Requirements 1.1**

  - [x]* 2.3 Write property test for custom-token round-trip
    - **Property 2: Custom-token tool calls round-trip through extraction**
    - **Validates: Requirements 1.2**

  - [x]* 2.4 Write property test for malformed-args handling
    - **Property 3: Malformed tool-call args become parse-failures without dropping or terminating**
    - **Validates: Requirements 1.3**

  - [x]* 2.5 Write property test for unregistered tool names
    - **Property 4: Unregistered tool names are flagged invalid**
    - **Validates: Requirements 1.4**

  - [x]* 2.6 Write property test for call preservation and ordering
    - **Property 5: Extraction preserves every call in order**
    - **Validates: Requirements 1.5**

- [ ] 3. ReAct loop control and termination (`graph.py`)
  - [x] 3.1 Extend `AgentState` and rewrite `should_continue` routing precedence
    - Add `decision`, `reasoning_turns`, `market_data_seen` fields and `MAX_REASONING_TURNS` constant (default 3)
    - Enforce precedence: pending `ok` tool calls → tools; else `decision` set → terminate; else active watch → suspend; else bounded reasoning loop; else inject HOLD `no-decision-reached`
    - Read completion only from `state["decision"]`, never keyword matching
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 3.2 Implement first-turn data-acquisition gating
    - Set `market_data_seen` when any market-data tool returns data; block `declare_trade` finalize until true and continue the loop; produce HOLD when directional data is missing
    - Record tool error results and continue with remaining tools; treat `Unavailable` sentiment as non-blocking
    - _Requirements: 3.1, 3.2, 3.3, 5.1, 5.3, 10.4_

  - [x]* 3.3 Write property test for pending tool-call routing
    - **Property 6: Pending tool calls route to execution**
    - **Validates: Requirements 2.1**

  - [x]* 3.4 Write property test for decision-driven termination
    - **Property 7: A finalized decision terminates the run**
    - **Validates: Requirements 2.2**

  - [x]* 3.5 Write property test for bounded reasoning
    - **Property 8: Bounded reasoning is allowed before forcing termination**
    - **Validates: Requirements 2.3**

  - [x]* 3.6 Write property test for pending-work precedence
    - **Property 9: Pending work takes precedence over the reasoning cap**
    - **Validates: Requirements 2.4**

  - [x]* 3.7 Write property test for exhausted-reasoning HOLD
    - **Property 10: Exhausted reasoning yields a HOLD with no-decision-reached**
    - **Validates: Requirements 2.5**

  - [x]* 3.8 Write property test for watch suspension
    - **Property 11: A registered price watch suspends rather than terminates**
    - **Validates: Requirements 2.6**

  - [x]* 3.9 Write property test for keyword-immune termination
    - **Property 12: Termination ignores decision-like keywords in prose**
    - **Validates: Requirements 2.7**

  - [x]* 3.10 Write property test for market-data gating
    - **Property 13: No decision before market data is seen**
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [x]* 3.11 Write property test for tool-error resilience
    - **Property 17: Tool errors are recorded without aborting the run**
    - **Validates: Requirements 5.1**

  - [x]* 3.12 Write property test for missing-directional HOLD
    - **Property 19: Missing directional data forces a HOLD with a stated limitation**
    - **Validates: Requirements 5.3**

  - [x]* 3.13 Write property test for non-blocking unavailable sentiment
    - **Property 38: Unavailable sentiment does not block a decision**
    - **Validates: Requirements 10.4**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Trade_Validator pure module (Rust + Python mirror)
  - [x] 5.1 Implement the Rust Trade_Validator (`quant/mod.rs`)
    - Add `ExecutionLevels`, `ValidatorOutcome`, `ValidatorReason`, and `validate_trade(action, levels, atr_14)`
    - Rules: MissingLevels (R6.1), RiskRewardTooLow `< 2.0` (R6.2), StopTooTight `< 1.5×ATR` (R6.3), direction consistency per side (R6.4/R6.5); HOLD bypasses level checks
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 5.2 Implement the Python Trade_Validator mirror (`agents/deep-quant-loop/validator.py`)
    - Mirror the Rust rules exactly so `declare_trade` can validate on the Python side before/with the server
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x]* 5.3 Write property test for missing-level rejection (Rust)
    - **Property 20: Missing-level trades are rejected**
    - **Validates: Requirements 6.1**

  - [x]* 5.4 Write property test for the risk-reward boundary (Rust)
    - **Property 21: Risk-reward below 1:2 is rejected at the boundary**
    - **Validates: Requirements 6.2**

  - [x]* 5.5 Write property test for the stop-distance boundary (Rust)
    - **Property 22: Stops tighter than 1.5×ATR are rejected at the boundary**
    - **Validates: Requirements 6.3**

  - [x]* 5.6 Write property test for per-side direction consistency (Rust)
    - **Property 23: Direction consistency is enforced per side**
    - **Validates: Requirements 6.4, 6.5**

- [ ] 6. Enriched conviction scoring (`agents/technical/src/signal_engine.rs`)
  - [x] 6.1 Replace RSI+VWAP bucket logic with the weighted confluence model
    - Add `ConvictionInputs`/`ConvictionResult`; compute signed votes for momentum, trend, volatility, and volume families; renormalize over present families; map aggregate to `[0,100]` with agreement amplification; report `missing_indicators`
    - Keep it a pure function (no clock/RNG/ambient state)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x]* 6.2 Write property test for four-family dependence
    - **Property 28: Conviction score depends on all four indicator families**
    - **Validates: Requirements 8.1**

  - [x]* 6.3 Write property test for score range
    - **Property 29: Conviction score stays within [0, 100]**
    - **Validates: Requirements 8.2**

  - [x]* 6.4 Write property test for alignment amplification
    - **Property 30: Aligned indicators produce more extreme scores than conflicting ones**
    - **Validates: Requirements 8.3**

  - [x]* 6.5 Write property test for missing-indicator tolerance
    - **Property 31: Missing indicators are tolerated and reported**
    - **Validates: Requirements 8.4**

  - [x]* 6.6 Write property test for scoring determinism
    - **Property 32: Conviction scoring is deterministic**
    - **Validates: Requirements 8.5**

- [ ] 7. Authoritative SR_Engine (`quant/mod.rs` + `tool_server.rs`)
  - [x] 7.1 Implement `compute_sr(candles, timeframe) -> SrLevels` pure function (`quant/mod.rs`)
    - Classic pivot formulas from the shared candle source; set `ordering_exception` when data forces an ordering violation; add intraday opening range + daily macro pivot
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x]* 7.2 Write property test for formula-derived levels
    - **Property 33: SR levels are derived by formula from the candle source**
    - **Validates: Requirements 9.1**

  - [x]* 7.3 Write property test for ordering or flagged exception
    - **Property 34: SR levels are ordered or the exception is flagged**
    - **Validates: Requirements 9.2**

  - [x]* 7.4 Write property test for intraday extra levels
    - **Property 35: Intraday SR adds opening range and daily macro levels**
    - **Validates: Requirements 9.3**

  - [ ]* 7.5 Write property test for SR determinism
    - **Property 36: SR computation is deterministic**
    - **Validates: Requirements 9.4**

  - [x] 7.6 Add the `POST /tools/get_support_resistance` endpoint (`tool_server.rs`)
    - Resolve candles via the shared `load_candles_from_db`; return the `SrLevels` contract through the shared timeframe validator
    - _Requirements: 9.1, 9.3_

- [ ] 8. Tool Server contracts: candles, consensus, sufficiency, multi-TF
  - [x] 8.1 Enforce the consensus-report contract (`tool_server.rs` / consensus serialization)
    - Serialize every documented indicator field as a finite number or explicit `null`; never NaN/Inf
    - _Requirements: 4.2, 4.3_

  - [x]* 8.2 Write property test for the consensus contract
    - **Property 14: Consensus report fields are present and numeric-or-null**
    - **Validates: Requirements 4.2, 4.3**

  - [x] 8.3 Enforce the candle contract for `get_candles` (`tool_server.rs`)
    - Return candles in ascending `timestamp_ms` order, each with O/H/L/C/V
    - _Requirements: 4.4_

  - [x]* 8.4 Write property test for the candle contract
    - **Property 15: Candles are returned in ascending order with full OHLCV**
    - **Validates: Requirements 4.4**

  - [x] 8.5 Implement the data-sufficiency classifier (`quant/mod.rs`)
    - Three-branch rule over `(available, required, tolerance)`: `error` / `proceed-with-warning` / `ok`; attach a data-shortfall warning within tolerance
    - _Requirements: 5.2_

  - [x]* 8.6 Write property test for the sufficiency classifier
    - **Property 18: Data-sufficiency classification follows the three-branch rule**
    - **Validates: Requirements 5.2**

  - [x] 8.7 Implement multi-TF trend with per-horizon Neutral fallback (`tool_server.rs`)
    - Return a bias for 1H/4H/1D; return `Neutral` for any horizon whose MAs are uncomputable while still reporting computable horizons
    - _Requirements: 13.1, 13.2_

  - [x]* 8.8 Write property test for three-horizon coverage
    - **Property 43: Multi-TF response includes all three horizon biases**
    - **Validates: Requirements 13.1**

  - [x]* 8.9 Write property test for per-horizon Neutral fallback
    - **Property 44: Uncomputable horizons return Neutral while others compute**
    - **Validates: Requirements 13.2**

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Price watcher reliability (`tool_server.rs` / `quant/mod.rs`)
  - [x] 10.1 Implement the watcher registry, trigger predicate, and suspend/resume
    - Register watchers keyed by `thread_id`, suspend the run resumably; trigger predicate fires iff price condition AND `volume >= average_volume × volume_multiplier`; remove the watcher on fire; resume via `/resume`
    - _Requirements: 14.1, 14.2, 14.4_

  - [x]* 10.2 Write property test for watcher registration and suspension
    - **Property 46: Valid watch parameters register a watcher and suspend the run**
    - **Validates: Requirements 14.1**

  - [x]* 10.3 Write property test for the trigger predicate
    - **Property 47: The watcher trigger predicate is correct**
    - **Validates: Requirements 14.2**

  - [x]* 10.4 Write property test for watcher removal on fire
    - **Property 48: A fired watcher is removed from the registry**
    - **Validates: Requirements 14.4**

  - [x]* 10.5 Write unit test for registration-failure HOLD path
    - Registration failing after configured retries → agent declares HOLD and outputs no trade
    - _Requirements: 14.3_

- [ ] 11. External engine integrations (RAG, predictive, sentiment)
  - [x] 11.1 Pin RAG pattern contract at the boundary (`agents/quant-rag/src/patterns.rs`)
    - Ensure returned patterns carry `pattern_type`, `sentiment`, `description`, and `confidence` clamped to `[0.0, 1.0]`
    - _Requirements: 11.1, 11.2_

  - [x]* 11.2 Write property test for required pattern fields
    - **Property 39: RAG patterns carry the required fields**
    - **Validates: Requirements 11.1**

  - [x]* 11.3 Write property test for confidence range
    - **Property 40: Pattern confidence stays within [0.0, 1.0]**
    - **Validates: Requirements 11.2**

  - [x] 11.4 Add the `POST /tools/get_prediction` endpoint and projection (`agents/predictive/` + `tool_server.rs`)
    - Return `{projected_direction ∈ {Up,Down,Flat}, projected_value, confidence}`; mark unavailable on failure
    - _Requirements: 12.1, 12.2, 12.4_

  - [x]* 11.5 Write property test for projection shape
    - **Property 41: Predictive projection carries direction and value**
    - **Validates: Requirements 12.2**

  - [x] 11.6 Add the `POST /tools/get_news_context` sentiment proxy (`agents/sentiment/` + `tool_server.rs`)
    - Proxy to the Node Sentiment_Service; return recent headlines + a directional sentiment label; return `{"sentiment_summary": "Unavailable", ...}` on failure without fabrication
    - _Requirements: 10.1, 10.2, 10.3_

  - [x]* 11.7 Write property test for news mapping
    - **Property 37: News result maps service classification to headlines + directional label**
    - **Validates: Requirements 10.2**

  - [x]* 11.8 Write unit test for sentiment-unavailable marker
    - Sentiment service unreachable → `Unavailable` marker, no fabricated classification
    - _Requirements: 10.3_

- [ ] 12. Python tool client and contract revalidation (`tools.py`)
  - [x] 12.1 Convert SR/news/prediction tools to thin HTTP clients
    - Replace local computation in `get_support_resistance` and `get_news_context` with calls to the new Rust endpoints; add `get_prediction` client
    - _Requirements: 9.1, 10.1, 12.1_

  - [x] 12.2 Implement consumer-side `validate_contract(tool_name, payload)`
    - Re-validate each tool result against its contract on receipt; return a structured `{"error", "contract_violation"}` instead of raising; never pass malformed data to the model
    - _Requirements: 4.1, 5.1_

  - [x]* 12.3 Write unit test for contract-violation handling
    - A contract-violating payload yields a structured error result, not an exception, and never reaches the model
    - _Requirements: 4.1_

- [ ] 13. declare_trade commit and defensibility record
  - [x] 13.1 Wire Trade_Validator into the `declare_trade` commit path (`tool_server.rs`)
    - Commit and emit the final-analysis decision event only when validation passes; on any failure return the reason and do not commit
    - _Requirements: 6.6, 6.7_

  - [x] 13.2 Build the defensibility record in `graph.py`
    - Record multi-TF bias, key S/R levels used, volatility basis for the stop, RR value, named high-confidence patterns (`>0.6`), predictive-conflict statement, and macro-trend-conflict statement; VERIFY mode reports each validator check outcome
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 11.3, 12.3, 13.3_

  - [x]* 13.3 Write property test for commit-iff-pass
    - **Property 24: Commit happens exactly when validation passes**
    - **Validates: Requirements 6.6, 6.7**

  - [x]* 13.4 Write property test for the defensibility record
    - **Property 25: Committed trades carry a complete defensibility record**
    - **Validates: Requirements 7.1, 7.2**

  - [x]* 13.5 Write property test for naming high-confidence patterns
    - **Property 26: High-confidence patterns are named in the thesis**
    - **Validates: Requirements 7.3, 11.3**

  - [x]* 13.6 Write property test for stated predictive conflict
    - **Property 42: A projection conflicting with bias is stated**
    - **Validates: Requirements 12.3**

  - [x]* 13.7 Write property test for stated macro-trend conflict
    - **Property 45: A trade opposing the 1D trend states the macro conflict**
    - **Validates: Requirements 13.3**

  - [x]* 13.8 Write property test for VERIFY-mode per-check reporting
    - **Property 27: VERIFY mode reports an outcome for every validator check**
    - **Validates: Requirements 7.4**

  - [x]* 13.9 Write unit test for decision provenance
    - A fixed scenario's defensibility record cites only values present in tool results
    - _Requirements: 5.4_

- [x] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 15. Glass-box SSE stream (`main.py`)
  - [x] 15.1 Implement the reasoning splitter and event vocabulary in `event_generator`
    - Emit `REASONING` (markup stripped), `TOOL_CALL_START`, `TOOL_CALL_RESULT`, `TOOL_CALL_END` (with `error_reason` on failure), `VERIFICATION_STEP`, and `DECISION`; ensure no raw tool-call markup leaks into `REASONING`
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8_

  - [x] 15.2 Implement run lifecycle, ordering guarantees, and the ERROR path
    - `RUN_STARTED` first; `TOOL_CALL_START` before its RESULT/END; events in step order; `RUN_FINISHED` last with `completed`/`paused`; LLM stream failure emits `ERROR` and no `DECISION`; every payload is a valid JSON object
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 5.5_

  - [x]* 15.3 Write property test for REASONING emission
    - **Property 53: Reasoning-only messages emit a REASONING event**
    - **Validates: Requirements 16.1**

  - [x]* 15.4 Write property test for TOOL_CALL_START
    - **Property 54: Tool calls emit START with name and args**
    - **Validates: Requirements 16.2**

  - [x]* 15.5 Write property test for TOOL_CALL_RESULT
    - **Property 55: Tool results emit RESULT with name and result/summary**
    - **Validates: Requirements 16.3**

  - [x]* 15.6 Write property test for TOOL_CALL_END status
    - **Property 56: Tool completion emits END with a terminal status**
    - **Validates: Requirements 16.4, 16.5**

  - [x]* 15.7 Write property test for VERIFICATION_STEP
    - **Property 57: Verification steps emit VERIFICATION_STEP with check and outcome**
    - **Validates: Requirements 16.6**

  - [x]* 15.8 Write property test for DECISION event
    - **Property 58: Finalized decisions emit DECISION with action, conviction, rationale**
    - **Validates: Requirements 16.7**

  - [x]* 15.9 Write property test for markup-free reasoning
    - **Property 59: Reasoning events contain no raw tool-call markup**
    - **Validates: Requirements 16.8**

  - [x]* 15.10 Write property test for RUN_STARTED ordering
    - **Property 60: RUN_STARTED is the first event**
    - **Validates: Requirements 17.1**

  - [x]* 15.11 Write property test for RUN_FINISHED finality
    - **Property 61: RUN_FINISHED is the final event with a status**
    - **Validates: Requirements 17.2, 17.6**

  - [x]* 15.12 Write property test for tool-event ordering
    - **Property 62: A tool call's START precedes its RESULT and END**
    - **Validates: Requirements 17.3, 17.4**

  - [x]* 15.13 Write property test for the failed-stream ERROR path
    - **Property 63: A failed LLM stream emits ERROR and no DECISION**
    - **Validates: Requirements 17.5**

  - [x]* 15.14 Write property test for JSON-object payloads
    - **Property 64: Every stream event payload is a valid JSON object**
    - **Validates: Requirements 17.7**

  - [x]* 15.15 Write unit test for LLM stream-failure end to end
    - Stream failure mid-run surfaces `ERROR` and emits no `DECISION`/trade plan
    - _Requirements: 5.5, 17.5_

- [ ] 16. Trade Q&A mode (`graph.py` + `main.py`)
  - [x] 16.1 Implement the Trade_QA_Mode handler reusing the MemorySaver context (`graph.py`)
    - Answer from the thread's `Session_Analysis_Context`; cite recorded entry/SL/TP/RR/volatility basis for level questions; state "no trade declared" when none exists; call the relevant tool or state unavailable rather than fabricate; never mutate the committed trade; preserve context across turns
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

  - [x] 16.2 Add the Q&A request route to `main.py`
    - Reuse the same `thread_id` and emit answers via the existing stream conventions
    - _Requirements: 18.7_

  - [ ]* 16.3 Write property test for context preservation
    - **Property 65: Q&A preserves the session analysis context**
    - **Validates: Requirements 18.5**

  - [ ]* 16.4 Write property test for trade immutability
    - **Property 66: Q&A never mutates the committed trade**
    - **Validates: Requirements 18.6**

  - [ ]* 16.5 Write property test for Q&A stream conventions
    - **Property 67: Q&A answers follow the run-transparency stream conventions**
    - **Validates: Requirements 18.7**

  - [ ]* 16.6 Write unit tests for Q&A grounding behaviors
    - Answer from context, state "no trade declared", and cite recorded level rationale (verify context is loaded/attached and guardrail branches are exercised)
    - _Requirements: 18.1, 18.2, 18.3, 18.4_

- [ ] 17. Evaluation harness (new offline component alongside `agents/deep-quant-loop/`)
  - [x] 17.1 Implement `EvalReport` replay over the deterministic layer
    - Feed historical candle series through SR_Engine, Signal_Engine, Predictive_Engine, and Trade_Validator (no live LLM); compute `directional_accuracy`, `rr_met_proportion`, `validator_pass_proportion`, `sample_count`; emit a summary report
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

  - [x] 17.2 Implement the determinism double-run guard
    - Re-run each dataset twice; abort with a non-determinism failure if metrics differ
    - _Requirements: 15.5_

  - [x]* 17.3 Write property test for directional-accuracy metric
    - **Property 49: Directional-accuracy metric is well-formed**
    - **Validates: Requirements 15.1**

  - [x]* 17.4 Write property test for trade-quality proportions
    - **Property 50: Trade-quality proportions equal the true proportions**
    - **Validates: Requirements 15.2, 15.3**

  - [x]* 17.5 Write property test for the summary report
    - **Property 51: A completed evaluation emits a full summary report**
    - **Validates: Requirements 15.4**

  - [x]* 17.6 Write property test for evaluation determinism
    - **Property 52: Evaluation metrics are deterministic across identical runs**
    - **Validates: Requirements 15.5**

- [ ] 18. Integration wiring and verification
  - [x] 18.1 Wire all new tools into the agent registry and system prompt
    - Register `get_support_resistance`, `get_news_context`, `get_prediction` in `tools.py`/`graph.py`; add prompt rules for high-confidence patterns, predictive conflict, and macro-trend conflict so they surface in `setup_validation`
    - _Requirements: 10.1, 11.3, 12.1, 12.3, 13.3_

  - [ ]* 18.2 Write integration tests for external-service wiring
    - Mocked Sentiment_Service classification surfaced by `get_news_context` (R10.1); mocked predictive projection fetched during directional analysis (R12.1); seeded-QuestDB endpoint contract checks; watcher register → triggering candle → `/resume` fires once and is removed
    - _Requirements: 10.1, 12.1, 4.1, 14.2_

- [ ] 19. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP, but they encode the design's correctness properties and are recommended.
- Each correctness property is implemented by a single property-based test (Hypothesis `max_examples=100` / proptest `cases = 100`) tagged `Feature: deep-quant-analysis-hardening, Property {n}: {text}`.
- Generators must include the boundary inputs called out in the design: degenerate candle sets, RR exactly at 2.0, stop exactly at 1.5×ATR, sufficiency tolerance boundaries, intraday vs daily timeframes, and unicode/zero-width characters in tool-call markup.
- The LLM is never invoked in automated tests; the model layer is mocked so the deterministic pipeline is what is measured.
- Each task references specific requirements for traceability; checkpoints provide incremental validation points.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "6.1", "11.1"] },
    { "id": 1, "tasks": ["1.3", "2.2", "2.3", "2.4", "2.5", "2.6", "3.1", "5.1", "6.2", "6.3", "6.4", "6.5", "6.6", "11.2", "11.3"] },
    { "id": 2, "tasks": ["3.2", "5.2", "7.1", "8.1"] },
    { "id": 3, "tasks": ["3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "5.3", "5.4", "5.5", "5.6", "7.2", "7.3", "8.3", "8.5"] },
    { "id": 4, "tasks": ["7.4", "7.6", "8.2", "8.4", "8.6", "13.2"] },
    { "id": 5, "tasks": ["8.7", "11.4", "17.1"] },
    { "id": 6, "tasks": ["8.8", "8.9", "10.1", "11.5", "17.2"] },
    { "id": 7, "tasks": ["10.2", "10.3", "10.4", "10.5", "11.6", "17.3", "17.4", "17.5", "17.6"] },
    { "id": 8, "tasks": ["11.7", "11.8", "13.1"] },
    { "id": 9, "tasks": ["13.3", "13.4", "13.5", "13.6", "13.7", "13.8", "13.9"] },
    { "id": 10, "tasks": ["12.1"] },
    { "id": 11, "tasks": ["12.2", "15.1"] },
    { "id": 12, "tasks": ["12.3", "15.2"] },
    { "id": 13, "tasks": ["15.3", "15.4", "15.5", "15.6", "15.7", "15.8", "15.9", "15.10", "15.11", "15.12", "15.13", "15.14", "15.15", "16.1"] },
    { "id": 14, "tasks": ["16.2", "18.1"] },
    { "id": 15, "tasks": ["16.3", "16.4", "16.5", "16.6", "18.2"] }
  ]
}
```
