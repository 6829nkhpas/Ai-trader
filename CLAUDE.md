# Ai-trader — Project Context

> Deep project reference so a fresh session can work without re-exploring the whole tree.
> Working dir for most tasks is `frontend/`. Paths below are relative to repo root `D:\Strat Ai\Ai-trader\` unless noted.

---

## 0. Branching — read before committing

**`main` is production. Never commit or push to it directly.** Code reaches
`main` only through an approved PR from `staging`.

```
feature/* → develop → staging → main
```

- Branch off **`develop`** for all new work (`feature/`, `fix/`, `chore/`,
  `refactor/`, `docs/`). Urgent production fixes use `hotfix/*` off `main` and
  must be back-merged into `staging` then `develop`.
- Merging to `main` **triggers a production deploy** (`deploy-server.yml` SSHes
  into the droplet and runs `redeploy.sh`).
- `ci.yml` runs on PRs into `main`/`staging`/`develop` and on pushes to
  `develop`/`staging`. `branch-guard.yml` reports direct pushes to `main`/`staging`.
- GitHub branch protection is **not active** (private repo, free plan → the
  protection API 403s), so these rules are policy rather than enforced. Behave as
  though they were enforced.

Full rules: `CONTRIBUTING.md`.

---

## 1. What this is

Ai-trader (a.k.a. "Strat Ai" / Alpha Terminal) is a **desktop trading terminal** for the Indian market (NSE/NFO via Zerodha Kite Connect). It fuses live market data, an institutional charting suite (TradingView Advanced Charts), an F&O options-analytics workspace, and an LLM "deep quant" analysis agent.

The shipped app is a **Tauri 2 desktop shell** wrapping a **Next.js 15 / React 19** frontend, with a **Rust backend inside Tauri** (`frontend/src-tauri/`) plus several standalone Rust/Python microservices in the monorepo.

---

## 2. Monorepo layout (top level)

| Path | Language | Role |
|------|----------|------|
| `frontend/` | Next.js 15 + React 19 + Tauri 2 (Rust) | **The app.** UI + Tauri desktop shell + in-app Rust backend. Most work happens here. |
| `agents/deep-quant-loop/` | Python (FastAPI + LangGraph) | LLM trade-analysis agent + F&O options analytics (F1/F2/F3). FastAPI on **:8086**. |
| `aggregator/` | Rust | Core decision fusion + Kite REST proxy (`kite_api.rs`) + OHLC WS server. |
| `ingestion/` | Rust | Kite WebSocket tick ingestion → Kafka + QuestDB dual sink. |
| `alpha-terminal/` | Rust | V2 predictive engine, 10m OHLC aggregation, WS :8081. |
| `backend/`, `agents/` (other) | mixed | Older/auxiliary services. |
| `shared_protos/` | Protobuf | Cross-service data contracts. |
| `docs/`, `ARCHITECTURE.md`, `README.md` | docs | Background reading. |

**⚠️ Auth / payments are NOT in this repo.** The auth, broker-credential, credit,
and payment API is a **separate deployment** reachable at
`NEXT_PUBLIC_API_BASE_URL` (prod: `https://api-web.stratai.live`, prefix
`/api/v1`). The frontend consumes it over HTTP only — see `store/useAuthStore.ts`
and `lib/tauriFetch.ts`. There is no local auth service to start, and endpoints
like `/auth/desktop/session` exist only on that remote deployment, so failures
there must be debugged from its logs, not from this tree.

**⚠️ Non-standard Next.js:** `frontend/AGENTS.md` (loaded via `frontend/CLAUDE.md`) warns this Next.js has breaking changes vs. training data. **Read `node_modules/next/dist/docs/` before writing Next-specific code** (routing, server components, metadata). Don't assume App Router conventions from memory.

---

## 3. Frontend stack (`frontend/`)

- **Next.js 15** (App Router, Turbopack) + **React 19**
- **Zustand** for state (`src/store/`) — NOT Redux
- **Tailwind CSS v4** (`@import "tailwindcss"` + `@theme inline` tokens in `src/app/globals.css`)
- **Tauri 2** desktop shell (`src-tauri/`), Stronghold plugin for encrypted secrets
- **TradingView Advanced Charts** (proprietary lib under `public/static/charting_library/`) is the live chart — see §7
- `lightweight-charts` + custom engines in `src/charting/engines/` (legacy / footprint / volume-profile math)
- `lucide-react` + `react-icons`, `react-resizable-panels` v4 for layout, `framer-motion`, `three`/`@react-three/fiber`
- **Testing:** Vitest (unit), Playwright (`tests/`), **fast-check** (property-based — heavily used in `charting/__tests__` and `fno/__tests__`)

**Scripts** (`frontend/package.json`): `npm run dev` (Next dev), `tauri:dev` (desktop), `tauri:build`, `test` (`vitest run`), `test:watch`, `lint`.

**Dev API rewrites** (`frontend/next.config.ts`):
- `/kite/*` → `http://127.0.0.1:8087/api/kite/*` (broker/Kite proxy — served by `aggregator/kite_api.rs`, port configurable via `KITE_API_PORT`)
- `/questdb/*` → `http://127.0.0.1:9000/*` (QuestDB REST)
- In `ALPHA_TEST_MODE`: `/kite/*` → internal mock routes in `src/app/api/kite/`
- Turbopack root pinned to `frontend/` to silence monorepo lockfile warning.

---

## 4. Frontend source map (`frontend/src/`)

```
app/           Next App Router. page.tsx = terminal shell; layout.tsx; globals.css; api/kite/ (test mocks); dashboard/
charting/      TradingView datafeed + custom chart engines
  datafeed.ts        ← TV JS API datafeed adapter (history + live). CRITICAL, see §7
  datafeedTypes.ts   TV type shims
  symbolUtils.ts     isFnoSymbol() shared predicate
  engines/           chart-type / indicator / footprint / volume-profile / strategy engines (pure, property-tested)
  paneManager.ts, crosshair.ts, zoom.ts, workspace.ts, realtimePaint.ts
components/
  MainTerminalChart.tsx → chart/ChartSurface.tsx → chart/TradingViewWidget.tsx
  chart/       ChartPane, SplitChartContainer, ChartHeader, selectors, toolbar
  fno/         F&O workspace (see §8)
  panels/      left-panel/ (SymbolSearchBlock, WatchlistBlock), right sidebar panels
  layout/      TerminalLayout (header + left watchlist + main)
  auth/, broker/, profile/, orderbook/, quant/, settings/, skeletons/
hooks/         useTradingViewScript, useGhostLine, useHistoricalData (legacy), useTauriLiveData, useSymbolQuote, ...
store/         Zustand stores (see §5)
utils/         tvWidgetOptions.ts, tvThemeOverrides.ts, chartAggregation.ts, chartTypes.ts
lib/           env.ts (IS_PROD), featureFlags.ts (see §9)
types/
```

Tauri backend (`frontend/src-tauri/src/`):
```
lib.rs                 App bootstrap: .env load, deep-link handler, managed state, background tasks, invoke_handler![]
commands/              Tauri IPC commands (see §6)
  charts.rs   ticker.rs   instruments.rs   fno.rs   deep_quant.rs   quant.rs   radar.rs   sentiment.rs   security.rs
services/
  fno_service.rs             F&O snapshot/analytics build (Kite + QuestDB), expiry/contract resolution
  fno_config.rs              FNO_* env → FnoConfig (defaults: NIFTY, BANKNIFTY)
  option_chain.rs            pure chain-selection math (select_atm, build_chain_selection, strike band)
  option_chain_subscriber.rs periodic chain ingestion; resolve_nfo_underlying_name, read_spot, RequestedUnderlyings
  instrument_master.rs       daily NSE + NFO instrument CSV sync into SQLite (instruments, nfo_instruments)
  history_loader.rs          Kite historical → QuestDB (historical_candles daily, historical_intraday)
  live_bridges.rs            WS→IPC bridges (lazy, on first subscribe_ticker)
  llm.rs, audit_logger.rs
db/                    workspace SQLite init/state (DbState)
quant/                 tool_server (:8084), radar, vwepr
execution/paper.rs     paper trading
```

---

## 5. State stores (Zustand, `frontend/src/store/`)

- **`useTradeStore`** — `activeProfile` (INTRADAY/SWING/INVESTOR/FNO), `selectedSymbol`, `activeTimeframe`, `chartMode` (STANDARD/VOLUME_PROFILE/FOOTPRINT), decisions, portfolio, `historicalCache`, and **F&O selection**: `fnoUnderlying` (default `''`), `fnoExpiry` (`''` ⇒ backend picks nearest). `setFnoUnderlying` resets `fnoExpiry` to `''`.
- **`useChartUIStore`** — chart type, drawings, indicators, `splitView`, `activePaneId`, `panes`, `setPaneSymbol`, sidebar, fullscreen, `theme`, `ghostLineMode`.
- **`useQuantStore`** — consensus data, AI execution plan, reasoning steps, sentiment.
- **`useAuthStore`** — JWT auth, broker connection, user profile (auto-clears expired JWTs; `logout()` resets feature store).
- **`useRadarStore`** — user symbol radar for pattern/strategy detection.
- **`useFeatureStore`** — computed `FeatureAccessMap` (see §9).

---

## 6. Tauri IPC commands (registered in `src-tauri/src/lib.rs` `invoke_handler![]`)

Charts/data: `subscribe_ticker`, `search_instruments`, `get_historical_view` (symbol, timeframe → bincode `Vec<BinaryCandle>`), `load_historical`, `fetch_questdb`, `get_pool_status`.
Deep quant: `run_deep_quant_analysis`, `run_ai_analysis`, `run_deep_quant_agent`, `ask_trade_question`, `get_multi_timeframe_chart_patterns`, `deploy_ai_sentinel`.
Paper trade: `execute_paper_trade`, `get_paper_portfolio`. Sentiment: `fetch_symbol_sentiment`. Quant/radar: `compute_ghost_curve`, `scan_radar_symbol`, `scan_quant_radar`, `set_radar_symbols`, `get_radar_symbols`.
**F&O**: `get_fno_analytics`, `fno_list_chains`, `fno_request_underlying`, `fno_resolve_nearest_contract`, `fno_subscribe`, `fno_unsubscribe`.
Security/workspace: `save_api_key`, `check_api_key_exists`, `hydrate_key_cache`, `vault_store_token`, `open_browser`, `save_workspace`.

`get_kite_credentials()` (charts.rs) reads `KITE_API_KEY` / `KITE_ACCESS_TOKEN` from env then `.env`.

---

## 7. Charting & historical data (TradingView) — `src/charting/datafeed.ts`

The live chart is **TradingView Advanced Charts** (`public/static/charting_library/`), loaded by `hooks/useTradingViewScript.ts`, mounted in `components/chart/TradingViewWidget.tsx` via `utils/tvWidgetOptions.ts`. `MainTerminalChart → ChartSurface → TradingViewWidget`.

**Data flow:** TV calls `datafeed.getBars(symbolInfo, resolution, periodParams, ...)`. `periodParams.{from,to}` is the window TV requests; TV pages backward as the user scrolls left.

**Resolution maps** (`datafeed.ts`): `RESOLUTION_TO_KITE_INTERVAL` (TV res → Kite interval `minute/3minute/5minute/10minute/15minute/30minute/60minute/day`) and `RESOLUTION_TO_TIMEFRAME` (TV res → store TF string). `utils/tvThemeOverrides.ts` `TIMEFRAME_TO_RESOLUTION` is the reverse.

**Fetch pipeline (`fetchKiteBatch`)** — important, since this was iteratively fixed:
- Kite caps each `/instruments/historical` request per interval → `KITE_INTERVAL_MAX_DAYS` (minute 7d, 3/5/10m 30d, 15/30/60m 60d, day 2000d). A wider request returns `[]` silently.
- So the requested `[from,to]` is **sliced into Kite-sized pages** (oldest first) and each page fetched; results accumulate. A page erroring/empty stops paging but keeps what was already pulled.
- **Tauri path first:** `get_historical_view` (QuestDB cache) is tried; if it has bars in range, returns them. Otherwise falls through to Kite REST pages so scroll-back past the cache edge still works.
- **`scrollBackCache`** (per `SYMBOL::TIMEFRAME`, in-memory) persists merged bars across TV pagination calls so history isn't re-fetched/forgotten. `invalidateScrollBackCache(symbol)` is called on symbol change in `TradingViewWidget.tsx`.
- Bars are also mirrored into `useTradeStore.historicalCache` (key `SYMBOL::timeframe::kiteInterval`) so the deep-quant pipeline sees data.
- `getBars` returns `{ noData: true }` only when both cache and Kite genuinely return nothing.
- `let bars` (not `const`) — the dedup filter reassigns it (a past bug: "Assignment to constant variable").

**Kite REST proxy** (`aggregator/src/kite_api.rs`, `/api/kite/historical`) forwards `from`/`to` verbatim to Kite; token resolved from cached instruments, so per-interval caps are enforced by Kite. It returns `{ candles: [{time(sec),open,high,low,close,volume}] }`.

`get_historical_view` (charts.rs) proactively backfills intraday from Kite into QuestDB `historical_intraday` (keyed by symbol+timeframe), then reads historical + today's `live_ticks` (aggregated via `SAMPLE BY`), dedups by ts, returns bincode. 10m maps to base_tf `10m` / `10minute`.

---

## 8. F&O workspace (`frontend/src/components/fno/`)

`activeProfile === 'FNO'` swaps the main workspace to `FnoSection` and the sidebar to `FnoSidebarPanel` (see `app/page.tsx` `renderProfileContent` / `renderSidebarContent`). Split view is available in INTRADAY and FNO only.

- `FnoSection.tsx` — orchestrates: listens to `fno-snapshot` IPC event, invokes `get_fno_analytics`, `fno_list_chains`, `fno_subscribe`/`fno_unsubscribe`. Branches on `FnoViewState.kind`.
- `viewModel.ts` — `toFnoViewState(payload)` maps IPC payload → view state: `ready | partial | unavailable | service-error`. **Key distinction:** a transport `Err` (rejected invoke) ⇒ `service-error` (`FnoServiceState`, "service unreachable"); an `unavailable: true` marker ⇒ `unavailable` (`FnoUnavailableState`, honest empty). Every numeric leaf is `finite | null`, never fabricated.
- `FnoChartPanel.tsx` — charts the selected F&O contract; uses `useFnoAutoContract`.
- `useFnoAutoContract.ts` — when in FNO mode and `selectedSymbol` is NOT already a CE/PE/FUT contract, invokes `fno_resolve_nearest_contract(underlying)` and sets the returned tradingsymbol. Fires once per underlying (ref-guarded) so it doesn't loop.
- `symbolParser.ts` — `getUnderlyingFromSymbol`, `matchExpiryFromSymbol`, `getStrikeFromSymbol`, `getOptionTypeFromSymbol`.
- Auto-resolution also wired into `panels/left-panel/WatchlistBlock.tsx` and `SymbolSearchBlock.tsx` `routeSymbolToChart`.

**Backend F&O:**
- `nfo_instruments` SQLite table (created in `instrument_master.rs`): `instrument_token, tradingsymbol, name, underlying, instrument_type (CE/PE/FUT), strike, expiry (ISO YYYY-MM-DD), lot_size, segment, last_updated`. Populated daily from `https://api.kite.trade/instruments/NFO`, grouped by `derive_underlying` (uses `name`, falls back to tradingsymbol). Index names: NFO side uses short names (`NIFTY`, `BANKNIFTY`) while spot side uses `NIFTY 50`, `NIFTY BANK` — reconciled by `resolve_nfo_underlying_name`.
- `resolve_nearest_expiry(db, underlying)` (fno_service.rs) — distinct CE/PE expiries; returns soonest `>= today`, else latest past.
- `build_fno_snapshot` — resolves expiry (SQLite → QuestDB fallback → `no_expiry` marker, NOT an `Err`), fetches Kite quotes/QuestDB `option_chain_snapshots`, computes PCR / max pain / OI walls.
- `fno_resolve_nearest_contract(underlying)` — nearest expiry + ATM strike (`select_atm` on listed strikes vs `read_spot`), prefers CE, falls back to PE when CE has no OI (`fetch_snapshots_from_questdb`), widens ±2 strikes if ATM not listed; returns `{ tradingsymbol, underlying, expiry, strike, option_type }` or `None`.
- Python side (`agents/deep-quant-loop/`): `options.py` (F1/F2 read+analytics), `options_bias.py` (F3), `main.py` `GET /options/snapshot` composes them and emits `reason_code` (`no_expiry|no_snapshot|analytics_degraded`) markers. FastAPI :8086.

---

## 9. Feature gating (`frontend/src/lib/`)

Premium features (`deepseekGlm, multiModel, ghostline, footprint, topup, instantNews, advanceChart`) gated by **global env kill switch AND user plan `accessFlags`** (from `/credit` API):
- `NEXT_PUBLIC_PROD` (`lib/env.ts` → `IS_PROD`): in **dev everything is unlocked**; in prod a feature needs BOTH `NEXT_PUBLIC_ENABLE_<FEATURE>=true` AND the user's `accessFlag`.
- `lib/featureFlags.ts` `computeFeatureAccess(accessFlags)` → `FeatureAccessMap` (pure). Held in `useFeatureStore`, hydrated in `app/page.tsx` from `useCredit()`, reset on logout.
- `components/common/FeatureGate.tsx` wraps premium UI (locked placeholder + Upgrade CTA → `dashboard.stratai.live`); `useFeature(id)` for non-UI gates.
- Add features by extending `FeatureId` + `FEATURE_CONFIG` — don't sprinkle env reads.

---

## 10. Live data & services / ports

| Port | Service |
|------|---------|
| 8081 | OHLC candle WS (alpha-terminal / aggregator `ohlc_server`) |
| 8082 | Predictive signals WS (Ghost Line) |
| 8083 | Market insights WS (LLM) |
| 8084 | Local quant tool server (Tauri `quant/tool_server`) |
| 8085 | Option-chain ingestion control port |
| 8086 | Deep-quant FastAPI (`agents/deep-quant-loop/main.py`) — options snapshot + agent SSE |
| 8087 | Kite REST proxy (`aggregator/kite_api.rs`) — behind `/kite/*` rewrite |
| 8089 | Order-flow live bridge |
| 9000 | QuestDB REST — behind `/questdb/*` rewrite |
| 8812 | QuestDB PG wire (`QUESTDB_POSTGRES_URL`, default `postgresql://admin:quest@localhost:8812/qdb`) |

In Tauri builds, `live_bridges.rs` bridges WS servers → IPC events (lazy, on first `subscribe_ticker`); frontend `listen()`s for `fno-snapshot`, `quant-consensus`, `deep-quant-stream`, `order_flow_stream`, `radar-alert`, `broker-connection-success`, `payment-success`, `desktop-login-success`, `historical-loaded`, `system-error`. Browser fallback connects WS directly.

Data source: **Zerodha Kite Connect** (WS `wss://ws.kite.trade` for ticks; REST for historical/quotes). Ticks → Kafka `market.ticks` (Protobuf) + QuestDB. Deep links use scheme `strat://` (broker-callback / payment-success / login).

---

## 11. Data stores

- **QuestDB** — time series. `live_ticks` (cumulative day volume — use `last(volume)-first(volume)` per bucket), `historical_candles` (daily, PARTITION BY YEAR, DEDUP on ts+symbol), `historical_intraday` (PARTITION BY MONTH, DEDUP on ts+symbol+timeframe), `option_chain_snapshots`.
- **Workspace SQLite** (`db/`, `DbState`) — `instruments` (NSE EQ), `nfo_instruments` (derivatives), drawings/workspace persistence. Daily CSV sync via `instrument_master.rs` (`needs_refresh` = empty or >24h).

---

## 12. Conventions & gotchas

- **CSS tokens (Tailwind v4), not raw colors:** `bg-background/surface/card/elevated/muted`, `text-text-primary/secondary/muted`, `border-border-default/subtle`, `text-emerald-*`/`text-rose-*` for bull/bear. Dark default; `.light` on `<html>` flips (see `globals.css`).
- **Profile-driven UI:** `activeProfile` drives both main layout and sidebar. Split-chart (dual-pane) only in INTRADAY/FNO.
- **Auth gating:** `page.tsx` early-returns `AuthOverlay` if unauthenticated, then broker connect card if broker not connected.
- **Live data only** — no synthetic/mock generators in UI; components update only on real IPC/WS data. `ALPHA_TEST_MODE` env swaps Kite to internal mocks.
- **Charts bypass React state** for hot paths — TV/lightweight-charts `.setData()`/`.update()` called directly.
- **Bash on Windows:** shell is bash — use `/dev/null`, forward slashes. Line endings: repo is CRLF; git warns "LF will be replaced by CRLF" (harmless).
- **Verify after changes:** Rust — `cd frontend/src-tauri && cargo build --lib` / `cargo test --lib`. Frontend — `cd frontend && npx tsc --noEmit`, `npx vitest run src/charting` (330 tests), targeted `npx vitest run <file>`.
- **Pre-existing test failures** (fail on clean tree too, unrelated to recent work): `src/components/fno/__tests__/selectors.bounding.property.test.ts` and `scopeBoundary.test.ts`. `tsc --noEmit` also surfaces unrelated errors in `WatchlistPanel.tsx` and TV codegen assets — ignore those when checking your own changes.

---

## 13. Persistent memory

Auto-memory index lives at `C:\Users\yash\.claude\projects\D--Strat-Ai-Ai-trader\memory\MEMORY.md` with detailed notes: `project_overview.md`, `frontend_stack.md`, `frontend_conventions.md`, `feature_gate_infra.md`. This CLAUDE.md supersedes/expands them for day-to-day work.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
