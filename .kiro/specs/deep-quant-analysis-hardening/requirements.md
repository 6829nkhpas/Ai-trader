# Requirements Document

## Introduction

This feature hardens the **core deep-quant analysis engine** of the AI trading system so that it reliably helps a trader find and defend high-probability trades on NSE (Indian) equities. The core is a multi-agent stack: a Python LangGraph ReAct loop (`agents/deep-quant-loop`) acting as the "Alpha-Quant" reasoning brain, a Rust tool server (`:8084`) exposing market-data and decision tools, a Rust Consensus Engine producing technical indicators, a Rust Signal Engine producing conviction scores, a chart-pattern engine, a news/sentiment service, a RAG engine (`quant-rag`), and a predictive forecast engine (`predictive`).

The system today is uneven: the LLM tool-calling loop relies on fragile regex parsing and monologue-counting heuristics that can terminate prematurely or miss tool calls; conviction scoring uses only RSI and VWAP with hardcoded buckets while a far richer indicator set is available; support/resistance is recomputed in Python instead of a single authoritative engine; news sentiment is naive keyword matching while a dedicated sentiment agent is underused; the RAG and predictive engines are not integrated into the decision loop; trade declarations are not programmatically validated for internal consistency (risk/reward, stop sizing, level alignment); partial-data scenarios can still produce low-quality trades; and end-to-end prediction accuracy and trade defensibility are unmeasured.

This document defines the requirements to make the deep-quant core **strong, reliable, accurate at price prediction, and able to defend a declared trade** — covering tool/agent correctness, data-contract integrity, scoring quality, RAG/predictive integration, LLM loop robustness, error handling and graceful degradation, and measurable end-to-end effectiveness.

The scope is the deep-quant analysis core and the agents/services it directly depends on. It excludes broker order execution, authentication, charting UI rendering, and historical data backfill except where those systems supply data the core consumes.

## Glossary

- **Deep_Quant_Agent**: The Python LangGraph ReAct agent ("Alpha-Quant") in `agents/deep-quant-loop` that orchestrates analysis, runs in FIND or VERIFY mode, and emits decisions.
- **ReAct_Loop**: The reason-act control loop in `graph.py` (`call_model` + `should_continue` routing) governing when the Deep_Quant_Agent calls tools, continues reasoning, or finishes.
- **Tool_Server**: The Rust HTTP service on `localhost:8084` exposing market-data and decision tools to the Deep_Quant_Agent.
- **Analysis_Tool**: Any of the eight callable tools: get_candles, get_consensus_report, get_multi_tf_trend, get_chart_patterns, get_support_resistance, get_news_context, watch_price_condition, declare_trade.
- **Consensus_Engine**: The Rust component (`ConsensusEngine::compile_consensus`) computing the indicator/consensus report (trend_score, momentum, RSI, MACD, Bollinger, ATR, VWAP, OBV, CMF, etc.).
- **Consensus_Report**: The structured output of the Consensus_Engine returned by get_consensus_report.
- **Signal_Engine**: The Rust component in `agents/technical/src/signal_engine.rs` producing the `technical_conviction_score`.
- **Conviction_Score**: An integer 0–100 representing directional confidence in a trade setup.
- **SR_Engine**: The authoritative support/resistance computation source (to be consolidated into the Rust Tool_Server).
- **Sentiment_Service**: The dedicated news/sentiment agent (`agents/sentiment`, Claude-based) producing classified sentiment for a symbol.
- **RAG_Engine**: The `agents/quant-rag` service performing chart-pattern detection and LLM retrieval-augmented context.
- **Predictive_Engine**: The `agents/predictive` service producing forward price forecasts.
- **Trade_Validator**: A component that programmatically validates a proposed or declared trade against risk rules before it is committed.
- **Declared_Trade**: The final structured decision produced by declare_trade (action, conviction_score, setup_validation, execution_plan, and execution levels).
- **Risk_Reward_Ratio**: The ratio of expected reward (|take_profit − entry|) to risk (|entry − stop_loss|).
- **ATR**: Average True Range over 14 periods (`atr_14`), a volatility measure.
- **Evaluation_Harness**: An offline component that replays historical candles to measure prediction accuracy and trade-quality metrics of the deep-quant core.
- **Tool_Result_Contract**: The agreed JSON schema and value ranges each Analysis_Tool must return.
- **Data_Sufficiency_Check**: A pre-analysis validation that confirms enough valid candle data exists to compute requested indicators.
- **FIND_Mode**: Operating mode where the Deep_Quant_Agent discovers a new trade.
- **VERIFY_Mode**: Operating mode where the Deep_Quant_Agent critiques a user-proposed trade as co-pilot.
- **Glass_Box_Stream**: The ordered Server-Sent Events stream emitted by the deep-quant-loop service (`main.py` `event_generator`) that exposes the Deep_Quant_Agent's processing to the user interface, including run lifecycle, reasoning text, tool invocations, tool results, and errors.
- **Stream_Event**: A single Server-Sent Event in the Glass_Box_Stream, carrying an event name (such as RUN_STARTED, REASONING, TOOL_CALL_START, TOOL_CALL_RESULT, TOOL_CALL_END, VERIFICATION_STEP, DECISION, RUN_FINISHED, ERROR) and a JSON data payload.
- **Reasoning_Trace**: The Deep_Quant_Agent's natural-language internal monologue (the "think out loud" output), surfaced as a distinct event class separate from tool-call markup.
- **Tool_Invocation_Record**: The combined record for a single Analysis_Tool call within the Glass_Box_Stream, consisting of the tool name, the arguments supplied, the returned result or result summary, and the terminal status (success or failure).
- **Verification_Step**: A single check from the self-verification / risk-manager protocol (volatility-based stop check, macro-trend-alignment check, risk-reward check, level-alignment check) surfaced to the user with its outcome.
- **Trade_QA_Mode**: A conversational follow-up mode in which the user asks free-form questions about the analysis and the Declared_Trade, and the Deep_Quant_Agent answers grounded in the current session's analysis evidence.
- **Session_Analysis_Context**: The accumulated analysis evidence for a given thread_id persisted by the LangGraph MemorySaver checkpointer — including multi-timeframe trend bias, support/resistance levels, indicator values, detected patterns, news sentiment, and the Declared_Trade with its defensibility record — that grounds Trade_QA_Mode answers.

## Requirements

### Requirement 1: Reliable LLM Tool-Call Extraction

**User Story:** As a trader, I want the agent to reliably recognize and execute every tool the model intends to call, so that analysis is never silently skipped or corrupted by parsing failures.

#### Acceptance Criteria

1. WHEN the language model returns native structured tool calls, THE Deep_Quant_Agent SHALL execute those tool calls without applying text-based extraction.
2. IF the language model returns tool calls only as in-content custom-token markup, THEN THE Deep_Quant_Agent SHALL extract each tool name and its JSON arguments and execute the corresponding Analysis_Tool.
3. IF a tool call's arguments cannot be parsed into a valid JSON object, THEN THE Deep_Quant_Agent SHALL record a parse-failure result for that tool call, SHALL NOT execute the malformed tool call, and SHALL continue the ReAct_Loop without terminating the run.
4. WHEN a tool name extracted from model output does not match a registered Analysis_Tool, THE Deep_Quant_Agent SHALL record an invalid-tool result for that tool call.
5. THE Deep_Quant_Agent SHALL preserve every successfully parsed tool call from a single model response so that no intended tool call is dropped.

### Requirement 2: Deterministic ReAct Loop Termination

**User Story:** As a trader, I want the agent to finish only when it has reached a real decision, so that it neither stops prematurely nor loops indefinitely.

#### Acceptance Criteria

1. WHEN the most recent model message contains one or more tool calls, THE ReAct_Loop SHALL route execution to tool execution.
2. WHEN the most recent model message contains a finalized decision produced through declare_trade, THE ReAct_Loop SHALL terminate the run.
3. WHILE the Deep_Quant_Agent has produced reasoning text without a tool call and without a finalized decision, THE ReAct_Loop SHALL allow continued reasoning up to a configured maximum of consecutive reasoning turns.
4. WHEN the most recent model message contains pending tool calls or a finalized decision, THE ReAct_Loop SHALL process those tool calls or that decision before applying the maximum-consecutive-reasoning-turns rule.
5. IF the configured maximum of consecutive reasoning turns is reached with no pending tool call and no finalized decision, THEN THE ReAct_Loop SHALL terminate the run with a HOLD decision and a stated reason of no-decision-reached.
6. WHEN the Deep_Quant_Agent registers a price condition through watch_price_condition, THE ReAct_Loop SHALL suspend the run in a resumable state rather than terminate it.
7. THE ReAct_Loop SHALL determine completion from the structured decision state rather than from keyword matching on reasoning text.

### Requirement 3: First-Turn Data Acquisition

**User Story:** As a trader, I want the agent to always gather market data before reasoning, so that no decision is made on assumptions.

#### Acceptance Criteria

1. WHEN a new analysis run starts in FIND_Mode, THE Deep_Quant_Agent SHALL call at least one market-data Analysis_Tool before producing a finalized decision.
2. WHEN a new analysis run starts in VERIFY_Mode, THE Deep_Quant_Agent SHALL call at least one market-data Analysis_Tool before approving or rejecting the user-proposed trade.
3. THE Deep_Quant_Agent SHALL NOT produce a finalized decision until at least one market-data Analysis_Tool has returned data in the current run.

### Requirement 4: Tool Result Contract Validation

**User Story:** As a trader, I want every tool result validated against an expected schema, so that malformed or out-of-range data never reaches the reasoning step.

#### Acceptance Criteria

1. WHEN an Analysis_Tool returns data, THE Tool_Server SHALL produce a result conforming to that tool's Tool_Result_Contract.
2. WHEN get_consensus_report returns a Consensus_Report, THE Tool_Server SHALL include every documented indicator field with a numeric value or an explicit null marker.
3. IF a numeric indicator value is non-finite, THEN THE Tool_Server SHALL represent that field with an explicit unavailable marker rather than a fabricated number.
4. WHEN get_candles returns candle data, THE Tool_Server SHALL return candles in ascending chronological order with each candle containing timestamp_ms, open, high, low, close, and volume.
5. IF a tool receives a timeframe value outside the supported set, THEN THE Tool_Server SHALL return a descriptive error identifying the invalid timeframe and SHALL log the validation failure.

### Requirement 5: Graceful Degradation on Partial or Missing Data

**User Story:** As a trader, I want the agent to abstain rather than invent a trade when data is insufficient, so that I am never shown a fabricated setup.

#### Acceptance Criteria

1. WHEN an Analysis_Tool returns an error result, THE Deep_Quant_Agent SHALL record the failure and continue analysis using the remaining available tools.
2. IF the Data_Sufficiency_Check determines that fewer candles are available than the minimum required to compute requested indicators, THEN THE Tool_Server SHALL return a data-insufficiency error for the affected tool, EXCEPT WHERE the shortfall is within the configured minimal-shortfall tolerance, in which case THE Tool_Server SHALL proceed and attach a data-shortfall warning to the result.
3. IF required market data for a directional decision is unavailable, THEN THE Deep_Quant_Agent SHALL declare a HOLD decision stating the data limitation.
4. THE Deep_Quant_Agent SHALL base every finalized decision only on values returned by Analysis_Tools and SHALL NOT introduce indicator values absent from tool results.
5. WHEN the language model stream fails during a run, THE Deep_Quant_Agent SHALL surface an analysis-unavailable error without emitting a fabricated trade plan.

### Requirement 6: Programmatic Trade Validation

**User Story:** As a trader, I want every declared trade checked against hard risk rules before it is committed, so that I can trust that a shown trade is internally consistent and defensible.

#### Acceptance Criteria

1. WHEN declare_trade is called with action BUY or SELL, THE Trade_Validator SHALL verify that the execution plan contains an entry price, a stop-loss price, and a take-profit price.
2. IF a BUY or SELL Declared_Trade has a Risk_Reward_Ratio below 1:2, THEN THE Trade_Validator SHALL reject the declaration and return a risk-reward-violation reason.
3. IF a BUY or SELL Declared_Trade has a stop-loss distance from entry smaller than 1.5 times the current ATR, THEN THE Trade_Validator SHALL reject the declaration and return a stop-too-tight reason.
4. WHEN a BUY Declared_Trade is validated, THE Trade_Validator SHALL verify that the stop-loss is below the entry and the take-profit is above the entry.
5. WHEN a SELL Declared_Trade is validated, THE Trade_Validator SHALL verify that the stop-loss is above the entry and the take-profit is below the entry.
6. IF a Declared_Trade fails any Trade_Validator check, THEN THE Deep_Quant_Agent SHALL continue the ReAct_Loop to revise the setup rather than commit the failing trade.
7. WHEN a Declared_Trade passes all Trade_Validator checks, THE Tool_Server SHALL commit the decision and emit the final-analysis decision event.

### Requirement 7: Trade Defensibility Record

**User Story:** As a trader, I want each committed trade to carry the evidence behind it, so that I can review and defend the trade when challenged.

#### Acceptance Criteria

1. WHEN a Declared_Trade is committed, THE Deep_Quant_Agent SHALL include in the setup_validation the multi-timeframe trend bias, the key support/resistance levels used, and the volatility basis for the stop-loss.
2. WHEN a Declared_Trade is committed, THE Deep_Quant_Agent SHALL record the Risk_Reward_Ratio value used for the trade.
3. WHERE a chart pattern with confidence above 0.6 informed the decision, THE Deep_Quant_Agent SHALL name that pattern in the setup_validation.
4. WHEN operating in VERIFY_Mode, THE Deep_Quant_Agent SHALL state for the user-proposed trade whether each Trade_Validator check passed or failed.

### Requirement 8: Enriched Technical Conviction Scoring

**User Story:** As a trader, I want the conviction score to reflect the full indicator picture, so that it is a trustworthy measure rather than a two-input heuristic.

#### Acceptance Criteria

1. THE Signal_Engine SHALL compute the Conviction_Score using momentum, trend, volatility, and volume indicators available in the Consensus_Report, not RSI and VWAP alone.
2. THE Signal_Engine SHALL produce a Conviction_Score in the range 0 to 100 inclusive.
3. WHEN every contributing indicator signals the same direction, THE Signal_Engine SHALL produce a Conviction_Score more extreme than when contributing indicators conflict.
4. IF one or more contributing indicators are unavailable, THEN THE Signal_Engine SHALL compute the Conviction_Score from the available indicators and report which indicators were missing.
5. WHEN identical indicator inputs are supplied, THE Signal_Engine SHALL produce an identical Conviction_Score.

### Requirement 9: Authoritative Support/Resistance Source

**User Story:** As a trader, I want support and resistance levels computed by one authoritative engine, so that levels used for entries and stops are consistent across the system.

#### Acceptance Criteria

1. WHEN get_support_resistance is called, THE SR_Engine SHALL compute pivot, support levels S1, S2, S3, and resistance levels R1, R2, R3 from the same candle source the Tool_Server uses for other indicators.
2. THE SR_Engine SHALL order the returned levels such that S3 ≤ S2 ≤ S1 ≤ pivot ≤ R1 ≤ R2 ≤ R3 whenever the candle data permits a consistent ordering, and WHERE market conditions make that ordering impossible THE SR_Engine SHALL return the most accurate computed levels and flag the ordering exception.
3. WHERE the requested timeframe is intraday, THE SR_Engine SHALL additionally return the opening-range high and low and the daily macro pivot levels.
4. WHEN the same symbol, timeframe, and candle data are supplied, THE SR_Engine SHALL return identical support and resistance levels on repeated calls.

### Requirement 10: News Sentiment Integration

**User Story:** As a trader, I want news context to come from the dedicated sentiment analysis service, so that catalyst sentiment is meaningful rather than naive keyword counting.

#### Acceptance Criteria

1. WHEN get_news_context is called for a symbol, THE Deep_Quant_Agent SHALL obtain a sentiment classification produced by the Sentiment_Service for that symbol.
2. WHEN the Sentiment_Service returns a classification, THE get_news_context result SHALL include the recent headlines and the sentiment classification with a directional label.
3. IF the Sentiment_Service is unavailable, THEN get_news_context SHALL return a sentiment-unavailable status without fabricating a sentiment classification.
4. THE Deep_Quant_Agent SHALL treat a sentiment-unavailable status as a missing input and SHALL NOT block a decision solely on its absence.

### Requirement 11: RAG Pattern Context Integration

**User Story:** As a trader, I want the RAG engine's structural pattern intelligence available to the decision loop, so that the agent benefits from retrieval-augmented pattern context.

#### Acceptance Criteria

1. WHEN the Deep_Quant_Agent requests structural chart patterns, THE RAG_Engine SHALL return detected patterns with pattern_type, sentiment, confidence, and a description.
2. THE RAG_Engine SHALL return pattern confidence values in the range 0.0 to 1.0 inclusive.
3. WHERE a detected pattern has confidence above 0.6, THE Deep_Quant_Agent SHALL incorporate that pattern into its trade thesis.
4. IF the RAG_Engine returns no patterns, THEN THE Deep_Quant_Agent SHALL proceed using the remaining analysis inputs.

### Requirement 12: Predictive Forecast Integration

**User Story:** As a trader, I want the agent to consider a forward price forecast, so that its directional bias is informed by the predictive model.

#### Acceptance Criteria

1. WHEN the Deep_Quant_Agent performs directional analysis, THE Deep_Quant_Agent SHALL obtain a forward price projection from the Predictive_Engine for the analyzed symbol and timeframe.
2. WHEN the Predictive_Engine returns a projection, THE projection SHALL include a projected direction and a projected value.
3. IF the Predictive_Engine projection direction conflicts with the Deep_Quant_Agent directional bias, THEN THE Deep_Quant_Agent SHALL state the conflict in the setup_validation.
4. IF the Predictive_Engine is unavailable, THEN THE Deep_Quant_Agent SHALL proceed using the remaining analysis inputs and note the projection as unavailable.

### Requirement 13: Multi-Timeframe Trend Correctness

**User Story:** As a trader, I want the macro trend bias to be computed consistently across 1H, 4H, and 1D, so that the agent aligns trades with the dominant trend.

#### Acceptance Criteria

1. WHEN get_multi_tf_trend is called, THE Tool_Server SHALL return a directional bias for each of the 1H, 4H, and 1D horizons.
2. WHEN the moving averages required for a horizon's trend cannot be computed, THE Tool_Server SHALL return a Neutral bias for that specific horizon while returning the computed directional bias for the horizons whose moving averages are available.
3. IF a proposed trade direction opposes the 1D trend bias, THEN THE Deep_Quant_Agent SHALL state the macro-trend conflict in the setup_validation before committing the trade.

### Requirement 14: Price Watch Reliability

**User Story:** As a trader, I want the agent's "wait for a level" mechanism to reliably resume, so that staged setups are not lost.

#### Acceptance Criteria

1. WHEN watch_price_condition is called with a valid symbol, timeframe, price level, direction, and volume multiplier, THE Tool_Server SHALL register a watcher and suspend the run in a resumable state.
2. WHEN a live candle satisfies a registered watcher's price condition and volume condition, THE Tool_Server SHALL resume the suspended run with the triggering candle.
3. IF watcher registration with the Tool_Server fails after the configured retry attempts, THEN THE Deep_Quant_Agent SHALL declare a HOLD decision and SHALL NOT output a trade.
4. WHEN a watcher condition is satisfied, THE Tool_Server SHALL remove that watcher from the active registry.

### Requirement 15: End-to-End Effectiveness Measurement

**User Story:** As a trader, I want the prediction accuracy and trade quality of the core measured against historical data, so that improvements are verifiable rather than assumed.

#### Acceptance Criteria

1. WHEN the Evaluation_Harness replays a historical candle series for a symbol, THE Evaluation_Harness SHALL produce a directional-accuracy metric comparing predicted direction to realized direction.
2. THE Evaluation_Harness SHALL report the proportion of generated trades whose Risk_Reward_Ratio met or exceeded 1:2.
3. THE Evaluation_Harness SHALL report the proportion of generated trades that satisfied all Trade_Validator checks.
4. WHEN the Evaluation_Harness completes a run, THE Evaluation_Harness SHALL emit a summary report containing the directional-accuracy metric and the trade-quality metrics.
5. IF the Evaluation_Harness detects non-deterministic metric values when replaying the same historical dataset and configuration, THEN THE Evaluation_Harness SHALL abort the evaluation run and report a non-determinism failure.
