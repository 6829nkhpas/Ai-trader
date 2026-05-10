# Graph Report - Ai-trader  (2026-05-10)

## Corpus Check
- 160 files · ~82,705 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 505 nodes · 816 edges · 65 communities (58 shown, 7 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 99 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `eab1ec8b`
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
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 41|Community 41]]

## God Nodes (most connected - your core abstractions)
1. `main()` - 30 edges
2. `getPool()` - 30 edges
3. `run()` - 16 edges
4. `BillingRepository` - 13 edges
5. `useAuth()` - 13 edges
6. `calculate_decision()` - 10 edges
7. `getClient()` - 9 edges
8. `evaluate_signal()` - 9 edges
9. `make_tech()` - 9 edges
10. `findUserProfileByUserId()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `publish_tick()`  [INFERRED]
  tools/load_tester/src/main.rs → ingestion/src/kafka_producer.rs
- `main()` --calls--> `run_listener()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/kafka_consumer.rs
- `main()` --calls--> `evaluate_signal()`  [INFERRED]
  tools/load_tester/src/main.rs → agents/technical/src/signal_engine.rs
- `main()` --calls--> `run_ohlc_pipeline()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/ohlc_server.rs
- `main()` --calls--> `run_consumer_loop()`  [INFERRED]
  tools/load_tester/src/main.rs → aggregator/src/consumer.rs

## Communities (65 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (19): gate(), AuthLayout(), handleEmailBlur(), handlePassBlur(), validateEmail(), validatePassword(), OAuthCompleteInner(), OAuthCompletePage() (+11 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (25): hashPassword(), verifyPassword(), AuthenticationError, DuplicateEmailError, NotFoundError, PasswordComplexityError, TokenReuseError, registerErrorHandler() (+17 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (22): handleGenerateMfa(), handleGoogleLogin(), handleHealth(), handleLogin(), handleLogout(), handleRefresh(), handleRegister(), handleSession() (+14 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (19): rsi_warm_up_gating(), update_rsi(), update_vwap(), vwap_basic_calculation(), vwap_no_volume_returns_none(), run_listener(), generate_access_token(), KiteSessionData (+11 more)

### Community 4 - "Community 4"
Cohesion: 0.12
Nodes (23): handleGetProfile(), handleGetUploadUrl(), handleLivenessCheck(), handleUpsertProfile(), handleVerifyPan(), decryptSymmetric(), encryptSymmetric(), getKey() (+15 more)

### Community 5 - "Community 5"
Cohesion: 0.13
Nodes (13): confidence_is_clamped(), flat_prices_yield_high_confidence(), PredictionEngine, returns_none_when_window_incomplete(), returns_prediction_at_full_window(), window_never_exceeds_capacity(), CandleAccumulator, init_tick_consumer() (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.12
Nodes (16): analyzeSentiment(), getClient(), getClient(), isArticleProcessed(), markArticleProcessed(), getClient(), scoreArticle(), OhlcEngine (+8 more)

### Community 7 - "Community 7"
Cohesion: 0.2
Nodes (22): base_case_no_sentiment_100pct_tech(), base_weights_70_30_normal(), calculate_decision(), conflict_resolution_penalizes_toward_neutral(), conflict_with_conviction_override_trusts_news(), conviction_override_inverts_weights(), hold_action_on_neutral_blend(), init_consumer() (+14 more)

### Community 8 - "Community 8"
Cohesion: 0.13
Nodes (10): BillingController, loadPrivateKey(), loadPublicKey(), verifyAccessToken(), authGuard(), blacklistJti(), isJtiBlacklisted(), registerBillingRoutes() (+2 more)

### Community 9 - "Community 9"
Cohesion: 0.15
Nodes (7): run_consumer(), run_consumer_loop(), init_producer(), KafkaProducer, publish_candle(), publish_decision(), publish_tick()

### Community 10 - "Community 10"
Cohesion: 0.17
Nodes (14): BinaryCandle, broadcast_error(), get_historical_view(), load_historical(), bulk_insert(), ExistingRange, fetch_kite_candles(), HistoricalCandle (+6 more)

### Community 12 - "Community 12"
Cohesion: 0.42
Nodes (8): evaluate_signal(), fields_propagated_correctly(), neutral_signal(), overbought_above_vwap(), oversold_below_vwap(), strong_bearish_signal(), strong_bullish_signal(), vwap_distance_calculation()

### Community 14 - "Community 14"
Cohesion: 0.29
Nodes (3): aggregateCandles(), calculateEMA(), useHistoricalData()

### Community 17 - "Community 17"
Cohesion: 0.52
Nodes (6): make_ltp_packet(), parse_binary_frame(), parse_binary_tick(), test_frame_with_no_packets(), test_ltp_mode_parsing(), test_packet_too_short()

## Knowledge Gaps
- **10 isolated node(s):** `SymbolState`, `BinaryCandle`, `KiteHistoricalResponse`, `KiteHistoricalData`, `HistoricalCandle` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 7` to `Community 1`, `Community 10`, `Community 6`?**
  _High betweenness centrality (0.150) - this node is a cross-community bridge._
- **Why does `main()` connect `Community 3` to `Community 9`, `Community 12`, `Community 5`, `Community 17`?**
  _High betweenness centrality (0.132) - this node is a cross-community bridge._
- **Why does `getPool()` connect `Community 2` to `Community 1`, `Community 4`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Are the 15 inferred relationships involving `main()` (e.g. with `run_listener()` and `update_rsi()`) actually correct?**
  _`main()` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `getPool()` (e.g. with `handleRegister()` and `handleLogin()`) actually correct?**
  _`getPool()` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `run()` (e.g. with `run_migration()` and `load_historical_data()`) actually correct?**
  _`run()` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `useAuth()` (e.g. with `LoginPage()` and `OAuthCompleteInner()`) actually correct?**
  _`useAuth()` has 3 INFERRED edges - model-reasoned connections that need verification._