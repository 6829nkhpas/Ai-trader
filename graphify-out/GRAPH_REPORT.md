# Graph Report - Ai-trader  (2026-05-06)

## Corpus Check
- 143 files · ~68,322 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 423 nodes · 723 edges · 54 communities (49 shown, 5 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 96 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f51d60fc`
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
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 31|Community 31]]

## God Nodes (most connected - your core abstractions)
1. `getPool()` - 30 edges
2. `main()` - 22 edges
3. `run()` - 14 edges
4. `BillingRepository` - 13 edges
5. `useAuth()` - 12 edges
6. `calculate_decision()` - 10 edges
7. `getClient()` - 9 edges
8. `evaluate_signal()` - 9 edges
9. `make_tech()` - 9 edges
10. `findUserProfileByUserId()` - 9 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `run_listener()`  [INFERRED]
  ingestion/src/main.rs → agents/technical/src/kafka_consumer.rs
- `main()` --calls--> `evaluate_signal()`  [INFERRED]
  ingestion/src/main.rs → agents/technical/src/signal_engine.rs
- `main()` --calls--> `run_ohlc_pipeline()`  [INFERRED]
  ingestion/src/main.rs → aggregator/src/ohlc_server.rs
- `main()` --calls--> `run_consumer_loop()`  [INFERRED]
  ingestion/src/main.rs → aggregator/src/consumer.rs
- `main()` --calls--> `run_consumer()`  [INFERRED]
  ingestion/src/main.rs → alpha-terminal/src/consumer.rs

## Communities (54 total, 5 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (19): gate(), AuthLayout(), handleEmailBlur(), handlePassBlur(), validateEmail(), validatePassword(), OAuthCompleteInner(), OAuthCompletePage() (+11 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (27): handleGetProfile(), handleGetUploadUrl(), handleLivenessCheck(), handleUpsertProfile(), handleVerifyPan(), handleKycVendorWebhook(), decryptSymmetric(), encryptSymmetric() (+19 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (21): rsi_warm_up_gating(), update_rsi(), update_vwap(), vwap_basic_calculation(), vwap_no_volume_returns_none(), run_listener(), generate_access_token(), KiteSessionData (+13 more)

### Community 3 - "Community 3"
Cohesion: 0.13
Nodes (18): handleGenerateMfa(), handleGoogleLogin(), handleHealth(), handleLogin(), handleLogout(), handleRefresh(), handleRegister(), handleSession() (+10 more)

### Community 4 - "Community 4"
Cohesion: 0.1
Nodes (15): OhlcEngine, SymbolState, confidence_is_clamped(), flat_prices_yield_high_confidence(), PredictionEngine, returns_none_when_window_incomplete(), returns_prediction_at_full_window(), window_never_exceeds_capacity() (+7 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (20): registerErrorHandler(), registerAuthRoutes(), BillingSyncEngine, analyzeSentiment(), getClient(), getClient(), isArticleProcessed(), markArticleProcessed() (+12 more)

### Community 6 - "Community 6"
Cohesion: 0.12
Nodes (18): hashPassword(), verifyPassword(), AuthenticationError, DuplicateEmailError, NotFoundError, PasswordComplexityError, TokenReuseError, findUserByEmail() (+10 more)

### Community 7 - "Community 7"
Cohesion: 0.21
Nodes (21): base_case_no_sentiment_100pct_tech(), base_weights_70_30_normal(), calculate_decision(), conflict_resolution_penalizes_toward_neutral(), conflict_with_conviction_override_trusts_news(), conviction_override_inverts_weights(), hold_action_on_neutral_blend(), init_consumer() (+13 more)

### Community 8 - "Community 8"
Cohesion: 0.13
Nodes (10): BillingController, loadPrivateKey(), loadPublicKey(), verifyAccessToken(), authGuard(), blacklistJti(), isJtiBlacklisted(), registerBillingRoutes() (+2 more)

### Community 9 - "Community 9"
Cohesion: 0.15
Nodes (7): run_consumer(), run_consumer_loop(), init_producer(), KafkaProducer, publish_candle(), publish_decision(), publish_tick()

### Community 10 - "Community 10"
Cohesion: 0.42
Nodes (8): evaluate_signal(), fields_propagated_correctly(), neutral_signal(), overbought_above_vwap(), oversold_below_vwap(), strong_bearish_signal(), strong_bullish_signal(), vwap_distance_calculation()

## Knowledge Gaps
- **4 isolated node(s):** `SymbolState`, `KiteSessionResponse`, `KiteSessionData`, `ParsedTick`
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 7` to `Community 5`, `Community 6`?**
  _High betweenness centrality (0.145) - this node is a cross-community bridge._
- **Why does `main()` connect `Community 2` to `Community 9`, `Community 10`, `Community 4`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Why does `getPool()` connect `Community 3` to `Community 1`, `Community 5`, `Community 6`?**
  _High betweenness centrality (0.102) - this node is a cross-community bridge._
- **Are the 21 inferred relationships involving `getPool()` (e.g. with `handleRegister()` and `handleLogin()`) actually correct?**
  _`getPool()` has 21 INFERRED edges - model-reasoned connections that need verification._
- **Are the 15 inferred relationships involving `main()` (e.g. with `run_listener()` and `update_rsi()`) actually correct?**
  _`main()` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `run()` (e.g. with `connectProducer()` and `loadNewsSentimentType()`) actually correct?**
  _`run()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `useAuth()` (e.g. with `LoginPage()` and `OAuthCompleteInner()`) actually correct?**
  _`useAuth()` has 3 INFERRED edges - model-reasoned connections that need verification._