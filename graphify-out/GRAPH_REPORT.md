# Graph Report - Ai-trader  (2026-05-18)

## Corpus Check
- 187 files · ~117,607 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 726 nodes · 1157 edges · 78 communities (70 shown, 8 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 109 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0b868680`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 53|Community 53]]

## God Nodes (most connected - your core abstractions)
1. `main()` - 31 edges
2. `getPool()` - 30 edges
3. `base_state()` - 20 edges
4. `run()` - 17 edges
5. `ConsensusEngine` - 13 edges
6. `BillingRepository` - 13 edges
7. `useAuth()` - 13 edges
8. `Candle` - 12 edges
9. `base_indicators()` - 11 edges
10. `calculate_decision()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `publish_tick()`  [INFERRED]
  tools/load_tester/src/main.rs → ingestion/src/kafka_producer.rs
- `main()` --calls--> `run_listener()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/kafka_consumer.rs
- `main()` --calls--> `evaluate_signal()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/signal_engine.rs
- `main()` --calls--> `run_kite_api_server()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/kite_api.rs
- `main()` --calls--> `run_ohlc_pipeline()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/ohlc_server.rs

## Communities (78 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (39): hashPassword(), verifyPassword(), AuthenticationError, DuplicateEmailError, NotFoundError, PasswordComplexityError, TokenReuseError, registerErrorHandler() (+31 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (20): gate(), AuthLayout(), handleEmailBlur(), handlePassBlur(), validateEmail(), validatePassword(), ToolMenu(), OAuthCompleteInner() (+12 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (25): rsi_warm_up_gating(), update_rsi(), update_vwap(), vwap_basic_calculation(), vwap_no_volume_returns_none(), run_listener(), generate_access_token(), KiteSessionData (+17 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (31): AiExecutionPlan, base_state(), candle(), compile_consensus_full_bullish(), compile_consensus_serializes_to_json(), ConsensusEngine, ConsensusReport, derive_bias_bearish() (+23 more)

### Community 4 - "Community 4"
Cohesion: 0.1
Nodes (29): run_consumer(), run_consumer_loop(), base_case_no_sentiment_100pct_tech(), base_weights_70_30_normal(), calculate_decision(), conflict_resolution_penalizes_toward_neutral(), conflict_with_conviction_override_trusts_news(), conviction_override_inverts_weights() (+21 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (22): handleGenerateMfa(), handleGoogleLogin(), handleHealth(), handleLogin(), handleLogout(), handleRefresh(), handleRegister(), handleSession() (+14 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (24): handleGetProfile(), handleGetUploadUrl(), handleLivenessCheck(), handleUpsertProfile(), handleVerifyPan(), decryptSymmetric(), encryptSymmetric(), getKey() (+16 more)

### Community 7 - "Community 7"
Cohesion: 0.1
Nodes (15): OhlcEngine, SymbolState, confidence_is_clamped(), flat_prices_yield_high_confidence(), PredictionEngine, returns_none_when_window_incomplete(), returns_prediction_at_full_window(), window_never_exceeds_capacity() (+7 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (12): aggregateCandles(), AlphaPredictiveChart(), calculateEMA(), useChartDataSync(), useChartInit(), useDrawingEngine(), useDrawingInteraction(), useDrawingRenderer() (+4 more)

### Community 9 - "Community 9"
Cohesion: 0.2
Nodes (10): Candle, detects_bearish_engulfing(), detects_bullish_engulfing(), detects_doji(), detects_hammer(), detects_shooting_star(), empty_history_returns_empty(), no_doji_on_large_body() (+2 more)

### Community 10 - "Community 10"
Cohesion: 0.13
Nodes (10): BillingController, loadPrivateKey(), loadPublicKey(), verifyAccessToken(), authGuard(), blacklistJti(), isJtiBlacklisted(), registerBillingRoutes() (+2 more)

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (14): fetchInstruments(), GET(), historical_handler(), HistoricalParams, Instrument, InstrumentCache, instruments_search(), InstrumentSearchParams (+6 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (4): calculateEMA(), calculateRSI(), computeTrend(), useMultiTimeframeTrend()

### Community 13 - "Community 13"
Cohesion: 0.15
Nodes (14): BinaryCandle, broadcast_error(), get_historical_view(), load_historical(), bulk_insert(), ExistingRange, fetch_kite_candles(), HistoricalCandle (+6 more)

### Community 14 - "Community 14"
Cohesion: 0.32
Nodes (14): base_indicators(), candle(), detects_death_cross(), detects_golden_cross(), detects_orb_breakdown_bearish(), detects_orb_breakout_bullish(), detects_vwap_bounce_bullish(), empty_history_returns_empty() (+6 more)

### Community 16 - "Community 16"
Cohesion: 0.3
Nodes (9): fetchFromKiteHistorical(), fetchFromQuestDB(), fetchKiteBatch(), fetchViaIpcProxy(), getQueries(), getQuestDbUrl(), isTauri(), parseQuestDBRows() (+1 more)

### Community 17 - "Community 17"
Cohesion: 0.2
Nodes (10): fetch_news_context(), load_candles_from_db(), run_deep_quant_analysis(), ChatChoice, ChatMessage, ChatMessageResponse, ChatRequest, ChatResponse (+2 more)

### Community 18 - "Community 18"
Cohesion: 0.42
Nodes (8): evaluate_signal(), fields_propagated_correctly(), neutral_signal(), overbought_above_vwap(), oversold_below_vwap(), strong_bearish_signal(), strong_bullish_signal(), vwap_distance_calculation()

### Community 19 - "Community 19"
Cohesion: 0.28
Nodes (4): db_path(), DbState, dirs_fallback(), init_db()

## Knowledge Gaps
- **24 isolated node(s):** `Instrument`, `QuoteData`, `InstrumentSearchParams`, `QuoteParams`, `HistoricalParams` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 4` to `Community 0`, `Community 19`, `Community 13`?**
  _High betweenness centrality (0.093) - this node is a cross-community bridge._
- **Why does `main()` connect `Community 2` to `Community 18`, `Community 11`, `Community 4`, `Community 7`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Why does `publish_tick()` connect `Community 4` to `Community 2`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `main()` (e.g. with `run_listener()` and `update_rsi()`) actually correct?**
  _`main()` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `getPool()` (e.g. with `handleRegister()` and `handleLogin()`) actually correct?**
  _`getPool()` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `run()` (e.g. with `init_db()` and `run_migration()`) actually correct?**
  _`run()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Instrument`, `QuoteData`, `InstrumentSearchParams` to the rest of the system?**
  _24 weakly-connected nodes found - possible documentation gaps or missing edges._