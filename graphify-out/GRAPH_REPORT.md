# Graph Report - .  (2026-05-06)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 333 nodes · 549 edges · 46 communities (44 shown, 2 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 88 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fb8a9154`
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
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 27|Community 27]]

## God Nodes (most connected - your core abstractions)
1. `getPool()` - 29 edges
2. `main()` - 17 edges
3. `run()` - 14 edges
4. `calculate_decision()` - 10 edges
5. `getClient()` - 9 edges
6. `evaluate_signal()` - 9 edges
7. `make_tech()` - 9 edges
8. `useAuth()` - 9 edges
9. `make_sentiment()` - 8 edges
10. `findUserProfileByUserId()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `run_ohlc_pipeline()`  [INFERRED]
  agents/predictive/src/main.rs → aggregator/src/ohlc_server.rs
- `main()` --calls--> `run_consumer_loop()`  [INFERRED]
  agents/predictive/src/main.rs → aggregator/src/consumer.rs
- `main()` --calls--> `run_consumer()`  [INFERRED]
  agents/predictive/src/main.rs → alpha-terminal/src/consumer.rs
- `main()` --calls--> `generate_access_token()`  [INFERRED]
  agents/predictive/src/main.rs → ingestion/src/kite_auth.rs
- `main()` --calls--> `init_pool()`  [INFERRED]
  agents/predictive/src/main.rs → ingestion/src/questdb_sink.rs

## Communities (46 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (25): run_consumer(), run_consumer_loop(), base_case_no_sentiment_100pct_tech(), base_weights_70_30_normal(), calculate_decision(), conflict_resolution_penalizes_toward_neutral(), conflict_with_conviction_override_trusts_news(), conviction_override_inverts_weights() (+17 more)

### Community 1 - "Community 1"
Cohesion: 0.1
Nodes (21): AuthenticationError, DuplicateEmailError, NotFoundError, PasswordComplexityError, TokenReuseError, registerErrorHandler(), registerAuthRoutes(), BillingSyncEngine (+13 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (15): gate(), handleEmailBlur(), handlePassBlur(), validateEmail(), validatePassword(), OAuthCompleteInner(), SessionProvider(), useAuth() (+7 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (20): rsi_warm_up_gating(), update_rsi(), update_vwap(), vwap_basic_calculation(), vwap_no_volume_returns_none(), publish_tick(), generate_access_token(), KiteSessionData (+12 more)

### Community 4 - "Community 4"
Cohesion: 0.18
Nodes (13): handleGenerateMfa(), handleGoogleLogin(), handleHealth(), handleLogin(), handleLogout(), handleRefresh(), handleRegister(), handleSession() (+5 more)

### Community 5 - "Community 5"
Cohesion: 0.14
Nodes (13): confidence_is_clamped(), flat_prices_yield_high_confidence(), PredictionEngine, returns_none_when_window_incomplete(), returns_prediction_at_full_window(), window_never_exceeds_capacity(), CandleAccumulator, init_tick_consumer() (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.13
Nodes (15): handleGetProfile(), handleGetUploadUrl(), handleLivenessCheck(), handleUpsertProfile(), handleVerifyPan(), verifyPan(), requireVerified(), encryptProfileData() (+7 more)

### Community 7 - "Community 7"
Cohesion: 0.26
Nodes (10): hashPassword(), verifyPassword(), findUserByEmail(), getPasswordHash(), insertCredential(), insertUser(), loginUser(), registerUser() (+2 more)

### Community 8 - "Community 8"
Cohesion: 0.21
Nodes (7): loadPrivateKey(), loadPublicKey(), verifyAccessToken(), authGuard(), isJtiBlacklisted(), registerBillingRoutes(), getRedisClient()

### Community 9 - "Community 9"
Cohesion: 0.2
Nodes (6): handlePolarWebhook(), verifySignature(), handleKycVendorWebhook(), requireActiveSubscription(), registerTradeRoutes(), registerWebhookRoutes()

### Community 10 - "Community 10"
Cohesion: 0.38
Nodes (7): encryptSymmetric(), getKey(), activateMfaRecord(), findMfaRecord(), upsertMfaRecord(), generateMfa(), verifyMfa()

### Community 11 - "Community 11"
Cohesion: 0.42
Nodes (8): evaluate_signal(), fields_propagated_correctly(), neutral_signal(), overbought_above_vwap(), oversold_below_vwap(), strong_bearish_signal(), strong_bullish_signal(), vwap_distance_calculation()

## Knowledge Gaps
- **3 isolated node(s):** `KiteSessionResponse`, `KiteSessionData`, `ParsedTick`
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 0` to `Community 1`, `Community 7`?**
  _High betweenness centrality (0.159) - this node is a cross-community bridge._
- **Why does `main()` connect `Community 3` to `Community 0`, `Community 11`, `Community 5`?**
  _High betweenness centrality (0.152) - this node is a cross-community bridge._
- **Why does `getPool()` connect `Community 4` to `Community 1`, `Community 6`, `Community 9`?**
  _High betweenness centrality (0.119) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `getPool()` (e.g. with `handleRegister()` and `handleLogin()`) actually correct?**
  _`getPool()` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 15 inferred relationships involving `main()` (e.g. with `run_listener()` and `update_rsi()`) actually correct?**
  _`main()` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `run()` (e.g. with `connectProducer()` and `loadNewsSentimentType()`) actually correct?**
  _`run()` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `KiteSessionResponse`, `KiteSessionData`, `ParsedTick` to the rest of the system?**
  _3 weakly-connected nodes found - possible documentation gaps or missing edges._