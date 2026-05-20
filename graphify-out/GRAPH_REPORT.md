# Graph Report - Ai-trader  (2026-05-20)

## Corpus Check
- 188 files · ~152,403 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 883 nodes · 1423 edges · 84 communities (77 shown, 7 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 152 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `94308175`
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
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 56|Community 56]]

## God Nodes (most connected - your core abstractions)
1. `main()` - 32 edges
2. `getPool()` - 30 edges
3. `run()` - 22 edges
4. `base_state()` - 20 edges
5. `spawn()` - 18 edges
6. `generate_deep_quant_plan_with_url()` - 16 edges
7. `ConsensusEngine` - 13 edges
8. `BillingRepository` - 13 edges
9. `useAuth()` - 13 edges
10. `Candle` - 12 edges

## Surprising Connections (you probably didn't know these)
- `run()` --calls--> `spawn()`  [INFERRED]
  agents/quant-rag/src/engine.rs → frontend/src-tauri/src/services/instrument_master.rs
- `main()` --calls--> `run_listener()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/kafka_consumer.rs
- `main()` --calls--> `evaluate_signal()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/signal_engine.rs
- `main()` --calls--> `run_kite_api_server()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/kite_api.rs
- `main()` --calls--> `run_ohlc_pipeline()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/ohlc_server.rs

## Communities (84 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (37): hashPassword(), verifyPassword(), AuthenticationError, DuplicateEmailError, NotFoundError, PasswordComplexityError, TokenReuseError, registerErrorHandler() (+29 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (33): handleGenerateMfa(), handleGoogleLogin(), handleHealth(), handleLogin(), handleLogout(), handleRefresh(), handleRegister(), handleSession() (+25 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (31): run_consumer(), run_consumer_loop(), rsi_warm_up_gating(), update_rsi(), update_vwap(), vwap_basic_calculation(), vwap_no_volume_returns_none(), init_producer() (+23 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (52): fetch_news_context(), load_candles_from_db(), run_deep_quant_analysis(), is_audit_enabled(), log_api_error(), log_api_transaction(), build_request_body(), ChatChoice (+44 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (20): gate(), AuthLayout(), handleEmailBlur(), handlePassBlur(), validateEmail(), validatePassword(), ToolMenu(), OAuthCompleteInner() (+12 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (31): AiExecutionPlan, base_state(), candle(), compile_consensus_full_bullish(), compile_consensus_serializes_to_json(), ConsensusEngine, ConsensusReport, derive_bias_bearish() (+23 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (36): analyze_sentiment_via_llm(), fetch_google_news_rss(), fetch_news_headlines(), fetch_symbol_sentiment(), first_non_empty(), LlmSentimentResponse, mock_sentiment(), resolve_llm_endpoint() (+28 more)

### Community 7 - "Community 7"
Cohesion: 0.07
Nodes (22): aggregateCandles(), AlphaPredictiveChart(), calculateEMA(), useChartDataSync(), useChartInit(), useDrawingEngine(), useDrawingInteraction(), useDrawingRenderer() (+14 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (15): OhlcEngine, SymbolState, confidence_is_clamped(), flat_prices_yield_high_confidence(), PredictionEngine, returns_none_when_window_incomplete(), returns_prediction_at_full_window(), window_never_exceeds_capacity() (+7 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (25): handleGetProfile(), handleGetUploadUrl(), handleLivenessCheck(), handleUpsertProfile(), handleVerifyPan(), decryptSymmetric(), encryptSymmetric(), getKey() (+17 more)

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (19): ActiveSymbolState, notify_ingestion_subscribe(), send_subscribe_to_ingestion(), subscribe_ticker(), fetch_candles_for_symbol(), RadarAlert, spawn_radar_worker(), current_count() (+11 more)

### Community 11 - "Community 11"
Cohesion: 0.1
Nodes (24): BinaryCandle, broadcast_error(), get_historical_view(), HistorySource, load_historical(), InstrumentRecord, lookup_instrument_token(), resolve_instrument_token() (+16 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (17): fetchInstruments(), GET(), GET(), historical_handler(), HistoricalParams, Instrument, InstrumentCache, instruments_search() (+9 more)

### Community 13 - "Community 13"
Cohesion: 0.2
Nodes (10): Candle, detects_bearish_engulfing(), detects_bullish_engulfing(), detects_doji(), detects_hammer(), detects_shooting_star(), empty_history_returns_empty(), no_doji_on_large_body() (+2 more)

### Community 14 - "Community 14"
Cohesion: 0.11
Nodes (5): handler(), tauriInvoke(), hydrateWatchlist(), persistWatchlist(), invoke()

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (4): calculateEMA(), calculateRSI(), computeTrend(), useMultiTimeframeTrend()

### Community 16 - "Community 16"
Cohesion: 0.32
Nodes (14): base_indicators(), candle(), detects_death_cross(), detects_golden_cross(), detects_orb_breakdown_bearish(), detects_orb_breakout_bullish(), detects_vwap_bounce_bullish(), empty_history_returns_empty() (+6 more)

### Community 18 - "Community 18"
Cohesion: 0.29
Nodes (5): check_api_key_exists(), get_api_key_from_vault(), hydrate_key_cache(), save_api_key(), SecureKeyStore

### Community 19 - "Community 19"
Cohesion: 0.42
Nodes (8): evaluate_signal(), fields_propagated_correctly(), neutral_signal(), overbought_above_vwap(), oversold_below_vwap(), strong_bearish_signal(), strong_bullish_signal(), vwap_distance_calculation()

### Community 20 - "Community 20"
Cohesion: 0.28
Nodes (4): db_path(), DbState, dirs_fallback(), init_db()

### Community 25 - "Community 25"
Cohesion: 0.47
Nodes (3): getOrLoadVault(), readFromVault(), writeToVault()

### Community 27 - "Community 27"
Cohesion: 0.83
Nodes (3): generateMockCandles(), GET(), symToBasePrice()

## Knowledge Gaps
- **38 isolated node(s):** `Instrument`, `QuoteData`, `InstrumentSearchParams`, `QuoteParams`, `HistoricalParams` (+33 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 6` to `Community 0`, `Community 1`, `Community 10`, `Community 11`, `Community 20`?**
  _High betweenness centrality (0.246) - this node is a cross-community bridge._
- **Why does `spawn()` connect `Community 10` to `Community 8`, `Community 2`, `Community 6`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Why does `main()` connect `Community 2` to `Community 8`, `Community 10`, `Community 19`, `Community 12`?**
  _High betweenness centrality (0.109) - this node is a cross-community bridge._
- **Are the 17 inferred relationships involving `main()` (e.g. with `run_listener()` and `update_rsi()`) actually correct?**
  _`main()` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `getPool()` (e.g. with `handleRegister()` and `handleLogin()`) actually correct?**
  _`getPool()` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `run()` (e.g. with `init_db()` and `run_instrument_sync()`) actually correct?**
  _`run()` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `spawn()` (e.g. with `run()` and `main()`) actually correct?**
  _`spawn()` has 12 INFERRED edges - model-reasoned connections that need verification._