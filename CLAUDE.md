# Ai-trader — Project Context

> Deep project reference so a fresh session can work without re-exploring the whole tree.
> Working dir for most tasks is `frontend/`. Paths below are relative to repo root `D:\Strat Ai\Ai-trader\` unless noted.

---

## 0. Branching — read before committing

**One branch: `main`.** Commit and push to it directly. `develop` and `staging`
were deleted, along with `branch-guard.yml` and the `.githooks/pre-push` hook that
enforced the old `feature/* → develop → staging → main` ladder.

```
main ──► push ──► CI + production deploy (concurrently)
```

- **Every push to `main` deploys to production** (`deploy-server.yml` SSHes into
  the droplet and runs `redeploy.sh`). There is no pre-production rung.
- `ci.yml` runs on pushes to `main` and on PRs into it — but **concurrently with
  the deploy, not before it**. A red CI means the bad commit is already live. So
  run the checks locally *before* pushing: `npx tsc --noEmit`, `npx vitest run`,
  `npm run build:web`, `cargo test` for the crate you touched.
- `npm run build:web` catches a class the other checks miss: a route can typecheck
  and test green and still fail to register in the production build.
- GitHub branch protection and rulesets are **unavailable** (private repo, free
  plan → both APIs 403), so nothing is enforced server-side. Discipline is the
  whole mechanism.

Full rules: `CONTRIBUTING.md`.

---

## 1. What this is

Ai-trader (a.k.a. "Strat Ai" / Alpha Terminal) is a **web trading terminal** for the Indian market (NSE/NFO via Zerodha Kite Connect). It fuses live market data, an institutional charting suite (TradingView Advanced Charts), an F&O options-analytics workspace, and an LLM "deep quant" analysis agent.

The shipped app is a **Next.js 15 / React 19 website** served at **https://app.stratai.live**, backed by standalone Rust/Python microservices in the monorepo. The browser never talks to those services directly: it calls same-origin Next.js **route handlers** in `frontend/src/app/api/`, which hold the gateway credential server-side and proxy onward (see §6).

**There is no desktop app.** `frontend/src-tauri/` — the Tauri 2 shell, its Rust
backend, the installer, and the auto-updater — was deleted, along with
`desktop-release.yml`. Anything you remember about Tauri IPC commands,
`invoke()`, `isTauri()`, Stronghold, or `strat://` deep links is gone; comments in
the tree that reference the desktop path are historical notes explaining *why*
the web path looks the way it does.

---

## 2. Monorepo layout (top level)

| Path | Language | Role |
|------|----------|------|
| `frontend/` | Next.js 15 + React 19 | **The app.** UI + the same-origin `/api/*` proxy tier. Most work happens here. |
| `agents/deep-quant-loop/` | Python (FastAPI + LangGraph) | LLM trade-analysis agent + F&O options analytics (F1/F2/F3). FastAPI on **:8086**. |
| `aggregator/` | Rust | Core decision fusion, Kite REST proxy (`kite_api.rs`, :8087), OHLC WS server, option-chain selection (`option_chain.rs` + `option_chain_selector.rs`). |
| `ingestion/` | Rust | Kite WebSocket tick ingestion → Kafka + QuestDB dual sink; `option_sink.rs` writes `option_ticks` / `option_chain_snapshots`. |
| `alpha-terminal/` | Rust | V2 predictive engine, 10m OHLC aggregation, WS :8081. |
| `tool-server/` | Rust | Quant tool HTTP server (:8084, every route under `/tools`) over `quant-core`. |
| `quant-core/` | Rust | Shared pattern/indicator library used by `tool-server` and `aggregator/quant`. |
| `agents/` (other), `backend/` | mixed | `predictive` (:8082), `quant-rag` (:8083), `sentiment` (:8090), `technical`; `backend/` is older/auxiliary. |
| `infra/` | Caddy + Terraform | `infra/caddy/Caddyfile` — the `app.stratai.live` vhost and the `app-api.stratai.live` gateway. |
| `shared_protos/` | Protobuf | Cross-service data contracts. |
| `docs/`, `ARCHITECTURE.md`, `README.md` | docs | Background reading. |

**⚠️ Auth / payments are NOT in this repo.** The auth, broker-credential, credit,
and payment API is a **separate deployment** reachable at
`NEXT_PUBLIC_API_BASE_URL` (prod: `https://api-web.stratai.live`, prefix
`/api/v1`). The frontend consumes it over HTTP only — see `store/useAuthStore.ts`
and `lib/api/client.ts`. There is no local auth service to start, so failures
there must be debugged from its logs, not from this tree.

**The terminal has NO login form.** Signing in happens on the dedicated auth
surface (`NEXT_PUBLIC_AUTH_URL`, prod `https://auth.stratai.live`, source in the
separate `thestratai/auth` repo). That deployment sets the session as an httpOnly
cookie pair scoped to `domain=.stratai.live`, so every Strat AI subdomain shares
one session and this origin is already authenticated when the user arrives back
from it. `page.tsx` redirects to `signInUrl()` on a confirmed `anonymous` status;
nothing is handed over in the URL and no token is readable from JavaScript. The
old `/auth/desktop/session` handshake (open a browser, poll a session, race a
`strat://` deep link, exchange for localStorage tokens) is gone — it existed to
carry a session into a Tauri shell that no longer ships.

**Deployment:** `frontend/Dockerfile` (Node 22 alpine, multi-stage, `next build
--turbopack`) → the `frontend` service in `docker-compose.prod.yml`
(`mem_limit: 192m` in the `docker-compose.8gb.yml` overlay) → the
`app.stratai.live` vhost in `infra/caddy/Caddyfile` (`reverse_proxy
frontend:3000`, `encode zstd gzip`, `frame-ancestors 'none'`). `NEXT_PUBLIC_*`
values are build **args** (Next inlines them textually); everything else is
runtime `environment:`.

**⚠️ Non-standard Next.js:** `frontend/AGENTS.md` (loaded via `frontend/CLAUDE.md`) warns this Next.js has breaking changes vs. training data. **Read `node_modules/next/dist/docs/` before writing Next-specific code** (routing, server components, metadata). Don't assume App Router conventions from memory.

---

## 3. Frontend stack (`frontend/`)

- **Next.js 15** (App Router, Turbopack) + **React 19**
- **Zustand** for state (`src/store/`) — NOT Redux
- **Tailwind CSS v4** (`@import "tailwindcss"` + `@theme inline` tokens in `src/app/globals.css`)
- **TradingView Advanced Charts** (proprietary lib under `public/static/charting_library/`) is the live chart — see §7
- `lightweight-charts` + custom engines in `src/charting/engines/` (legacy / footprint / volume-profile math)
- `lucide-react` + `react-icons`, `react-resizable-panels` v4 for layout, `framer-motion`, `three`/`@react-three/fiber`, `@base-ui/react`
- **Testing:** Vitest (unit), Playwright (`tests/`), **fast-check** (property-based — heavily used in `charting/__tests__`, `fno/__tests__`, `lib/bridge/__tests__`)

**Scripts** (`frontend/package.json`) — no `tauri*` script and no `@tauri-apps/*` dependency remains:
`dev` (`next dev`), `build` (`next build`), `build:web` (clean `.next` then `next build --turbopack` — the production build), `clean`, `start` (`next start`), `lint`, `test` (`vitest run`), `test:watch`.

**`--turbopack` on the production build is load-bearing, not a speed preference.**
The default webpack build dies with `FATAL ERROR: Committing semi space failed`
partway through this tree; the exhaustion is in EXTERNAL memory, so
`--max-old-space-size` (tried at 8 GB) does not help. Verify on a COLD build
(delete `.next` first) if you ever touch this.

**`frontend/next.config.ts`:**
- `output: 'standalone'` — the `/api/*` route handlers need a Node server, and standalone traces only what it uses so the Docker image stays lean. (The old `output: 'export'` / `NEXT_OUTPUT_EXPORT` branch, `trailingSlash`, and the custom `pageExtensions` are all gone with the desktop bundle.)
- Rewrites, both onto the same-origin handlers so the gateway credential stays server-side: `/kite/:path*` → `/api/kite/:path*`, `/questdb/:path*` → `/api/questdb/:path*`. They used to point straight at `127.0.0.1:8087` / `:9000`, which is why the browser reported `Failed to fetch` on any machine not running the local aggregator.
- `images: { unoptimized: true }`, `eslint: { ignoreDuringBuilds: true }` (lint separately via `npm run lint`; type-checking stays ON).
- Turbopack root pinned to `frontend/` to silence the monorepo lockfile warning.

---

## 4. Frontend source map (`frontend/src/`)

```
app/           Next App Router. page.tsx = terminal shell; layout.tsx; globals.css; dashboard/
  api/         SAME-ORIGIN proxy tier — server-only. See §6.
    _gateway.ts        upstream URL + basic-auth resolution (server-only, no NEXT_PUBLIC_)
    _proxy.ts          the one proxyRequest() body; canonicalizeSearch, resolveCatchAll
    _featureSwitches.ts  FEATURE_ENFORCEMENT / ENABLE_* reads + assertFeatureEnabled (see §9)
    kite/[...path]/route.ts      → aggregator kite_api (:8087)
    questdb/[...path]/route.ts   → QuestDB REST (:9000)
    deepquant/[...path]/route.ts → deep-quant FastAPI (:8086), SSE passthrough
    tools/[...path]/route.ts     → tool-server (:8084, /tools prefix restored)
    sentiment/route.ts           → sentiment service (:8090), shaped to SentimentPayload
    features/route.ts            → the resolved FeatureRuntimeConfig
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
  auth/, broker/, profile/, orderbook/, quant/, common/, skeletons/
hooks/         useTradingViewScript, useGhostLine, ghostLineComputation (pure TS projection),
               useHistoricalData (legacy), useSymbolQuote, useConnectionMonitor, ...
store/         Zustand stores (see §5)
utils/         tvWidgetOptions.ts, tvThemeOverrides.ts, tvSaveLoadAdapter.ts (localStorage),
               chartAggregation.ts, chartTypes.ts
lib/
  bridge/      THE backend-call layer. index.ts / webAdapters.ts / events.ts / fnoWeb.ts — see §6
  api/         client.ts, endpoints.ts, types.ts — the REMOTE auth/credit API (api-web.stratai.live)
  kiteFetch.ts   kiteFetch(path) → fetch(`/kite${path}`). Was tauriFetch.ts; the transport
                 shim is gone, this survives only as the "/kite lives here" fact.
  env.ts (IS_PROD), featureFlags.ts (see §9), sku.ts, redirect.ts, motionVariants.ts
types/
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

## 6. The bridge — how the frontend calls a backend (`frontend/src/lib/bridge/`)

There is no IPC. **Every** backend call goes through one decision point,
`bridgeInvoke(command, args)`, which dispatches to an HTTP adapter registry. The
`invoke`-compatible signature and the string command names are deliberately
retained — every call site was already written against them, so churning them
buys nothing.

**`index.ts`**
- `bridgeInvoke<T>(command, args?)` — looks the command up in `WEB_ADAPTERS` and calls it. No adapter ⇒ throws `BridgeUnsupportedError`, which names the command AND why it is missing (`native-browser-path | desktop-only | pending-server-route | no-frontend-caller | unknown-command`). The message is written to be shown to a user as-is — callers already render `err.message` (e.g. `useQuantStore.sentimentError`).
- `isCommandAvailable(command)` — for UI that would rather hide a control than let it fail.
- Re-exports everything from `events.ts` and `webAdapters.ts`, so `import { … } from '../lib/bridge'` is the only import form you need.

**`webAdapters.ts`** — the ONE place that knows how each command is served over
HTTP. `WEB_ADAPTERS: Record<string, WebAdapter>` plus four classification tables,
each entry carrying the *reason*:
- `NATIVE_BROWSER_PATH` — a first-class browser path already covers it: `compute_ghost_curve` (`hooks/ghostLineComputation.ts`, pure TS), `get_historical_view` / `load_historical` (the datafeed's paged Kite REST, see §7).
- `NOT_APPLICABLE_ON_WEB` — `check_for_update`, `install_update`, `relaunch_app` ("a page reload IS the update on the web").
- `PENDING_SERVER_ROUTE` — currently **empty**; kept as the honest landing place for a command whose route is not deployed yet.
- `NO_FRONTEND_CALLER` — `get_trade_history`, `deploy_ai_sentinel`.

`lib/bridge/__tests__/coverage.test.ts` scans `src/` off disk for every
`bridgeInvoke('…')` literal and asserts each one resolves to an adapter or a
classification table (and fails if a dynamically-built command name appears). Add
a command ⇒ add it to exactly one table.

What the adapters actually do, by group:
- **Market data** — `search_instruments` (parallel NSE+NFO `/api/kite/instruments`, equities first, mapped by the exported `rowsToSearchResults`), `fetch_questdb` (`/api/questdb/exec`), `get_pool_status` (does QuestDB answer `select 1`), `subscribe_ticker` (an honest **no-op**: live ticks arrive over `/ws/*`, see §10).
- **Deep quant** — `run_deep_quant_agent` / `run_ai_analysis` / `run_deep_quant_analysis` all drive `startAgentRun` against `/api/deepquant/*` and relay SSE onto the event bus; `ask_trade_question` → `/api/deepquant/qa`; `cancel_deep_quant_agent` aborts the local `AbortController` and best-effort POSTs `/api/deepquant/cancel`.
- **F&O** — `get_fno_analytics` → `/api/deepquant/options/snapshot`; `fno_subscribe`/`fno_unsubscribe` are no-ops (chain ingestion is server-side, §8); the remaining `fno_*` lookups are one QuestDB query over `option_chain_snapshots` (see `fnoWeb.ts`).
- **Radar & patterns** — `scan_radar_symbol`, `scan_quant_radar`, `get_multi_timeframe_chart_patterns` POST to `/api/tools/{scan_radar,scan_in_memory,get_multi_tf_chart_patterns}` rather than reimplementing the detection math in TS.
- **Local state** — `save_workspace` / `load_workspace` (localStorage; missing ⇒ `"{}"`), `set_radar_symbols` / `get_radar_symbols`, `open_browser` (`window.open`), `get_feature_switches` (`/api/features`), `fetch_symbol_sentiment` (`/api/sentiment`), `kite_fetch` / `api_fetch`. The paper-trading adapters (`execute_paper_trade` / `get_paper_portfolio` / `log_completed_trade`) were removed along with the simulated portfolio feature.

**`events.ts`** — the event bus. Backend pushes arrive two ways, neither of them IPC:
1. live market data over `/ws/*` (the one gateway prefix with no basic auth), connected directly by `useTradeStore.connectAlphaWebSocket` and friends;
2. agent/analysis frames over same-origin SSE, relayed onto the bus by `relaySse(body, onFrame, signal?)`.

`bridgeListen<T>(name, cb) → Promise<UnlistenFn>` subscribes;
`emitBridgeEvent(name, payload)` publishes; `hasBridgeListeners(name)` lets an
adapter skip work; `__resetBridgeBus()` is test-only. The envelope keeps Tauri's
shape (`{ event, payload, id: 0 }`) for the same "don't churn call sites" reason.
`relaySse` splits per the SSE spec (CRLF/CR/LF), joins multi-line `data:` blocks
with `\n` before parsing, yields `null` on unparseable data rather than dropping
the frame, and ignores blocks with no `event:` line.

Names actually listened for: `fno-snapshot`, `quant-consensus`,
`deep-quant-stream`, `deep-quant-qa-stream`,
`radar-alert`, `agent_message`, `agent_status`, `final_analysis_ready`,
`desktop-login-success`.

**`fnoWeb.ts`** — pure F&O chain math, no I/O: `underlyingCandidates` /
`spotSymbolCandidates` (the `NIFTY` ↔ `NIFTY 50` naming split), `isSafeName` /
`quote` / `underlyingClause` (QuestDB REST `/exec` takes a statement, not bind
parameters, so an allowlist at the boundary is the guard), `istToday`,
`nearestExpiry`, `selectAtm`, `pickContract` (ATM then ±1/±2, CE preferred unless
CE has zero OI and PE does not).

### The same-origin proxy tier (`frontend/src/app/api/`) — server-only

A browser cannot call the gateway directly: `infra/caddy/Caddyfile` puts
`/questdb/*`, `/deepquant/*`, `/kite/*` behind basic auth and emits **no CORS
headers** on them, so a cross-origin fetch fails at preflight regardless of
credential. Hence the route handlers, which hold the credential server-side.

- `_gateway.ts` — `upstreamBase(target)` for `'kite' | 'questdb' | 'deepquant' | 'tools' | 'sentiment'`, resolved by the ladder **explicit override env → `{STRATAI_HTTP_BASE_URL}/{prefix}` → `http://{STRATAI_SERVER_HOST}:{port}`**. Overrides: `KITE_API_URL`, `QUESTDB_HTTP_URL`, `DEEP_QUANT_URL`, `QUANT_TOOL_SERVER_URL` (the `/tools` suffix is appended if absent — omitting it 404s), `SENTIMENT_HTTP_URL`. Basic auth from `QUESTDB_USER` / `QUESTDB_PASSWORD`; `gatewayCredentialsMissing()` detects "targets the public gateway with the local-dev password" so a misconfigured deployment reports a *credential fault* instead of a blank panel. **Every var here is unprefixed** — that is what keeps the credential out of the JS bundle. Never import from a Client Component.
- `_proxy.ts` — one `proxyRequest(req, target, { path, stream })` for all five upstreams. Never buffers: `upstream.body` is handed straight back (matching Caddy's `flush_interval -1`), `stream: true` also skips the `PROXY_TIMEOUT_MS` (default 30s) because an agent run legitimately holds the connection for minutes. Transport failure ⇒ 502 `{ error }`; 401/403 ⇒ credential-fault message. Also exports `canonicalizeSearch` — Next normalizes the handler's request URL through a form-encoding serializer, so `NSE%3ANIFTY%2050` arrives as `NSE%3ANIFTY+50` and every symbol with a space silently returned no data until this re-encoded per RFC 3986.
- Handlers are all `runtime = 'nodejs'`, `dynamic = 'force-dynamic'`, `GET` + `POST`. `deepquant` additionally sets `maxDuration = 800`, treats `run|qa|resume|stream` as SSE (`isStreamingPath`), and gates `run|qa|resume|stream|cancel` behind the `deepseekGlm` feature (`isAgentPath` → `assertFeatureEnabled`) — deliberately NOT `/options/snapshot`, which is the un-gated F&O workspace.

Kite credentials themselves live with the aggregator:
`aggregator/src/kite_api.rs::get_kite_credentials()` reads `KITE_API_KEY` /
`KITE_ACCESS_TOKEN` from env then `.env`.

---

## 7. Charting & historical data (TradingView) — `src/charting/datafeed.ts`

The live chart is **TradingView Advanced Charts** (`public/static/charting_library/`), loaded by `hooks/useTradingViewScript.ts`, mounted in `components/chart/TradingViewWidget.tsx` via `utils/tvWidgetOptions.ts`. `MainTerminalChart → ChartSurface → TradingViewWidget`.

**Data flow:** TV calls `datafeed.getBars(symbolInfo, resolution, periodParams, ...)`. `periodParams.{from,to}` is the window TV requests; TV pages backward as the user scrolls left.

**Resolution maps** (`datafeed.ts`): `RESOLUTION_TO_KITE_INTERVAL` (TV res → Kite interval `minute/3minute/5minute/10minute/15minute/30minute/60minute/day`) and `RESOLUTION_TO_TIMEFRAME` (TV res → store TF string). `utils/tvThemeOverrides.ts` `TIMEFRAME_TO_RESOLUTION` is the reverse.

**Fetch pipeline (`fetchKiteBatch`)** — important, since this was iteratively fixed:
- Kite caps each `/instruments/historical` request per interval → `KITE_INTERVAL_MAX_DAYS` (minute 7d, 3/5/10m 30d, 15/30/60m 60d, day 2000d). A wider request returns `[]` silently.
- So the requested `[from,to]` is **sliced into Kite-sized pages** (oldest first) and each page fetched via `kiteFetch` → `/kite/historical` → `/api/kite/historical`. Pages are independent, so they run in **small concurrent batches** (`KITE_PAGE_CONCURRENCY`) — polite to Kite's 3 req/s ceiling while keeping the "stop once a batch comes back empty" early exit. A page whose symbol form returns nothing retries once with a resolved `instrument_token`.
- **F&O pages REST exactly like equities.** F&O used to short-circuit out of this loop on the belief that the REST proxy could not resolve NFO tokens; `kite_api.rs::resolve_token` detects an F&O tradingsymbol (digits + `CE`/`PE`/`FUT`) and queries the NFO exchange, so that early return only produced empty F&O charts and is gone. A zero-candle answer for an illiquid strike is a real absence of trades.
- There is **no QuestDB-cache-first hop** any more — `get_historical_view` and `load_historical` are `NATIVE_BROWSER_PATH` entries (§6), i.e. the paged Kite REST path above *is* the browser's history path.
- **`scrollBackCache`** (per `SYMBOL::TIMEFRAME`, in-memory) persists merged bars across TV pagination calls so history isn't re-fetched/forgotten. `getBars` only fetches the missing slice `[from, oldestCachedBar)` and returns without a network call when the cache already covers the window. `invalidateScrollBackCache(symbol)` is called on symbol change in `TradingViewWidget.tsx`.
- Bars are also mirrored (merged, not overwritten) into `useTradeStore.historicalCache` (key `SYMBOL::timeframe::kiteInterval`) so the deep-quant pipeline sees data.
- `noData` is a **two-strike** protocol: the first empty window returns `{ noData: false }` so TV retries; a second empty hit on the same window (`exhaustedWindows`) returns `{ noData: true }` plus a `nextTime` hint. F&O short-circuits to `noData: true` immediately when the whole window predates `earliestKnownBar`.
- `let bars` (not `const`) — the dedup filter reassigns it (a past bug: "Assignment to constant variable").

**Kite REST proxy** (`aggregator/src/kite_api.rs`, `/api/kite/historical`) forwards `from`/`to` verbatim to Kite; token resolved from a per-exchange instrument cache (24h TTL, persisted to `instruments_cache_{nse,nfo}.json` so it survives restarts), so per-interval caps are enforced by Kite. It returns `{ candles: [{time(sec),open,high,low,close,volume}] }`. Its other routes are `/api/kite/instruments` (symbol search, cash + tokenized derivative matching) and `/api/kite/quote`.

Server-side candle history is a **read-only** concern now: `tool-server/src/candles.rs` unions `historical_candles` / `historical_intraday` / `live_ticks` from QuestDB, dedups on timestamp keeping the highest-priority source, and never triggers a Kite backfill — ingestion fills those tables continuously.

---

## 8. F&O workspace (`frontend/src/components/fno/`)

`activeProfile === 'FNO'` swaps the main workspace to `FnoSection` and the sidebar to `FnoSidebarPanel` (see `app/page.tsx` `renderProfileContent` / `renderSidebarContent`). Split view is available in INTRADAY and FNO only.

- `FnoSection.tsx` — orchestrates: listens to the `fno-snapshot` bridge event, invokes `get_fno_analytics`, `fno_list_chains`, `fno_subscribe`/`fno_unsubscribe`. Branches on `FnoViewState.kind`.
- `viewModel.ts` — `toFnoViewState(payload)` maps the payload → view state: `ready | partial | unavailable | service-error`. **Key distinction:** a rejected `bridgeInvoke` (transport failure / 502) ⇒ `service-error` (`FnoServiceState`, "service unreachable"); an `unavailable: true` marker ⇒ `unavailable` (`FnoUnavailableState`, honest empty). Every numeric leaf is `finite | null`, never fabricated.
- `FnoChartPanel.tsx` — charts the selected F&O contract; uses `useFnoAutoContract`.
- `useFnoAutoContract.ts` — when in FNO mode and `selectedSymbol` is NOT already a CE/PE/FUT contract, invokes `fno_resolve_nearest_contract(underlying)` and sets the returned tradingsymbol. Fires once per underlying (ref-guarded) so it doesn't loop.
- `symbolParser.ts` — `getUnderlyingFromSymbol`, `matchExpiryFromSymbol`, `getStrikeFromSymbol`, `getOptionTypeFromSymbol`.
- Auto-resolution also wired into `panels/left-panel/WatchlistBlock.tsx` and `SymbolSearchBlock.tsx` `routeSymbolToChart`.

**How the chain gets ingested (server-side):**
- `aggregator/src/option_chain.rs` — the **pure** chain-selection math (moved verbatim from the retired `src-tauri/src/services/option_chain.rs`, property tests included; its only import is `chrono::NaiveDate`). No I/O, no clock, no globals: `resolve_ladder` (R3.1), `select_atm` (R3.2, nearest listed strike, lower-strike tie-break), `select_strike_band` (R3.3, ATM±M clamped to ≤2M+1), `select_nearest_expiries` (R3.4), plus `build_chain_selection` and `should_recenter`.
- `aggregator/src/option_chain_selector.rs` — drives that math on a timer. It lives in the aggregator because the aggregator already owns both inputs: the NFO instrument ladder (`KiteApiState::instruments_for`) and an authenticated Kite quote path for spot (`last_price_for`). Every 60s (`SELECTION_CYCLE`, after a 15s `STARTUP_GRACE`) it resolves the band and writes an `option_chain_set:{json}` line to the **ingestion control port** (`INGESTION_CONTROL_PORT`, default **8085**); `ingestion/src/option_sink.rs` does the actual QuestDB writing into `option_ticks` / `option_chain_snapshots`. Config keeps the old `FNO_*` env names (`FNO_UNDERLYINGS` → the 9 shipped underlyings, `FNO_NEAREST_EXPIRIES` → **7 nearest expiries**, band half-width 10, recenter threshold 1.0, 60s snapshots). The expiry count is what the F&O expiry dropdown can offer — that list is `SELECT DISTINCT expiry FROM option_chain_snapshots`, so it shows exactly what the selector follows and nothing more; it was 2, which is why every instrument appeared to have two expiries. The ceiling is `underlyings × expiries × (2 × band + 1) × 2` = 2646 against Kite's 3000-instrument WS limit, and `run` warns at startup if a config breaches it. `nfo_name` / `spot_quote_key` reconcile the naming split (NFO side `NIFTY`/`BANKNIFTY`, spot side `NIFTY 50`/`NIFTY BANK`).
- **If nothing pushes a selection, the F&O workspace goes silently blank** — an empty `option_chain_snapshots` is indistinguishable from a market with no open interest.
- Browser-side chain lookups read `option_chain_snapshots` directly (one query, because that table already carries the real tradingsymbol) via `lib/bridge/fnoWeb.ts`. The one honest difference from the old SQLite join: only *snapshotted* strikes resolve — the bounded band around ATM, not the full listed ladder.
- Python side (`agents/deep-quant-loop/`): `options.py` (F1/F2 read+analytics), `options_bias.py` (F3), `main.py` `GET /options/snapshot` composes them and emits `reason_code` (`no_expiry|no_snapshot|analytics_degraded`) markers. FastAPI :8086, reached from the browser through `/api/deepquant/options/snapshot`.

---

## 9. Feature gating (`frontend/src/lib/featureFlags.ts` + `app/api/_featureSwitches.ts`)

Premium features (`deepseekGlm, multiModel, ghostline, footprint, topup, instantNews, advanceChart`) need a **deployment kill switch AND the user's plan `accessFlags`** (from the remote `/credit` API).

**The switches are server-side.** `NEXT_PUBLIC_ENABLE_*` is gone: Next inlines a
`NEXT_PUBLIC_` var into the JS bundle at build time, so those switches shipped to
the browser (readable and flippable in devtools) and could not change without a
full rebuild. The authoritative names carry **no prefix**:
- `FEATURE_ENFORCEMENT` — whether this deployment enforces gating at all. Unset/false ⇒ treated as local development and **every feature is unlocked**. It defaults to **`true`** in `docker-compose.prod.yml`, deliberately, because the failure mode of forgetting it on a public site is silent revenue loss rather than an error. (Not named a bare `PROD`: that collides with `import.meta.env.PROD` in the Vitest tooling.)
- `ENABLE_DEEPSEEK_GLM`, `ENABLE_MULTI_MODEL`, `ENABLE_GHOSTLINE`, `ENABLE_FOOTPRINT`, `ENABLE_TOPUP`, `ENABLE_INSTANT_NEWS`, `ENABLE_ADVANCE_CHART` — one per feature. `envSwitchOn` accepts `true|1|yes|on` case-insensitively (the old code accepted only the exact string `'true'`, so `=1` read as disabled). Flipping one is a container restart, not a rebuild.

Flow: `app/api/features/route.ts` → `resolveFeatureConfig()` → `{ enforced, switches }`; the client fetches it via `bridgeInvoke('get_feature_switches')` in `useFeatureStore.hydrateConfig()` (called from `app/page.tsx`), coerced by `parseFeatureConfig` which **fails closed** (anything but an explicit `false` keeps enforcement on). The per-user side is `setAccessFlags(creditData?.accessFlags)`, also from `app/page.tsx` via `useCredit()`, reset on logout. `computeFeatureAccess(accessFlags, config)` is pure and ANDs switch with `accessFlag`; the pre-hydration default `UNRESOLVED_FEATURE_CONFIG` is `enforced: true` with every switch off.

**Client gating is an affordance; the binding gate is server-side.**
`assertFeatureEnabled(id, label)` runs in the request path and today protects the
deep-quant agent lifecycle (`/api/deepquant/{run,qa,resume,stream,cancel}` behind
`deepseekGlm`). Features computed in the browser (`footprint`, `ghostline`) have
no capability to gate. **Scope caveat:** the proxy routes do not resolve the
caller's identity (the JWT is minted by the separate api-web deployment and
`/api/v1/internal/entitlement/{user_id}` does not exist yet), so the server
enforces the deployment-wide switch only — a subscriber-level bypass of the
per-user gate remains possible on the web path. The one authoritative per-user
gate is `agents/deep-quant-loop/entitlements.py`.

- `components/common/FeatureGate.tsx` wraps premium UI (locked placeholder + "VIEW PLANS" CTA → `dashboard.stratai.live`); `useFeature(id)` for non-UI gates. `ResearchGate` / `useResearchCapability` are a *separate* concept — the RESEARCH SKU licensing boundary (`lib/sku.ts`), not an upsell.
- `NEXT_PUBLIC_PROD` → `IS_PROD` (`lib/env.ts`) still exists but now only backs `isFeatureStrictMode()` and the SKU enforcement default — it is no longer what gates features.
- Add features by extending `FeatureId` + `ACCESS_FLAG_BY_FEATURE` + `readSwitchEnv()` (literal `process.env.<NAME>` member expressions, deliberately not computed lookups) — don't sprinkle env reads.

---

## 10. Live data & services / ports

| Port | Service |
|------|---------|
| 3000 | The Next.js standalone server (`frontend`) — behind Caddy `app.stratai.live` |
| 8080 | Decisions WS (`aggregator/ws_server.rs`) |
| 8081 | OHLC candle WS (alpha-terminal / aggregator `ohlc_server`) |
| 8082 | Predictive signals WS (Ghost Line) |
| 8083 | Market insights WS (LLM, `quant-rag`) |
| 8084 | Quant tool server (`tool-server/`, every route under `/tools`) |
| 8085 | Ingestion control port — where `option_chain_selector.rs` pushes `option_chain_set` |
| 8086 | Deep-quant FastAPI (`agents/deep-quant-loop/main.py`) — options snapshot + agent SSE |
| 8087 | Kite REST proxy (`aggregator/kite_api.rs`) — behind `/kite/*` → `/api/kite/*` |
| 8089 | Order-flow WS (`NEXT_PUBLIC_ORDER_FLOW_WS_URL`) |
| 8090 | Sentiment service — behind `/api/sentiment` |
| 9000 | QuestDB REST — behind `/questdb/*` → `/api/questdb/*` |
| 8812 | QuestDB PG wire (`QUESTDB_POSTGRES_URL`, default `postgresql://admin:quest@localhost:8812/qdb`) |

**How pushes reach the browser** (no IPC — see §6): WebSockets are opened
**directly** from the client in `app/page.tsx` → `useTradeStore`
(`connectWebSocket`, `connectAlphaWebSocket`, `connectPredictiveWebSocket`,
`connectInsightWebSocket`, `connectOrderFlowWebSocket`), pointed at
`NEXT_PUBLIC_{AGGREGATOR,ALPHA,PREDICTIVE,INSIGHT,ORDER_FLOW}_WS_URL` (prod:
`wss://app-api.stratai.live/ws/*` — the one gateway prefix with **no** basic
auth; dev: `ws://127.0.0.1:808x`). Agent/analysis frames arrive as **SSE** over
`/api/deepquant/*` and are relayed onto the bridge event bus by `relaySse`;
components `bridgeListen()` for `fno-snapshot`, `quant-consensus`,
`deep-quant-stream`, `deep-quant-qa-stream`,
`radar-alert`, `agent_message`, `agent_status`, `final_analysis_ready`,
`desktop-login-success`.

Data source: **Zerodha Kite Connect** (WS `wss://ws.kite.trade` for ticks; REST for historical/quotes). Ticks → Kafka `market.ticks` (Protobuf) + QuestDB.

`infra/caddy/Caddyfile` fronts two vhosts: `app.stratai.live` (the website, no
basic auth — the JWT is the auth boundary) and `app-api.stratai.live` (the data
plane: `/ws/*` open, `/questdb/*` `/deepquant/*` `/kite/*` `/prometheus/*` behind
basic auth, no CORS headers). **No `/tools` or `/sentiment` gateway route exists**
— the frontend container reaches those over the internal `stratai` Docker network
via `QUANT_TOOL_SERVER_URL` / `SENTIMENT_HTTP_URL`.

---

## 11. Data stores

- **QuestDB** — the only database. `live_ticks` (cumulative day volume — use `last(volume)-first(volume)` per bucket), `historical_candles` (daily, PARTITION BY YEAR, DEDUP on ts+symbol), `historical_intraday` (PARTITION BY MONTH, DEDUP on ts+symbol+timeframe), `option_ticks`, `option_chain_snapshots`. Reached over REST (`:9000`, via `/api/questdb/*`) or PG wire (`:8812`, from the Rust services).
- **No SQLite.** The workspace DB and the `instruments` / `nfo_instruments` masters went with the desktop shell. The instrument master is now an in-memory + on-disk JSON cache inside the aggregator (`instruments_cache_{nse,nfo}.json`, per-exchange, 24h TTL, refetched from `https://api.kite.trade/instruments/{NSE,NFO}`).
- **Browser-local state** — `localStorage` holds the per-symbol chart workspace, the radar symbol list (`stratai.*` keys, see `webAdapters.ts`), TradingView layouts (`utils/tvSaveLoadAdapter.ts`), and the terminal selection preferences (`stratai.preferences`, `lib/preferences.ts`). It holds NO session material: the access/refresh tokens are httpOnly cookies on `.stratai.live` and are deliberately unreadable from JS.

---

## 12. Conventions & gotchas

- **CSS tokens (Tailwind v4), not raw colors:** `bg-background/surface/card/elevated/muted`, `text-text-primary/secondary/muted`, `border-border-default/subtle`, `text-emerald-*`/`text-rose-*` for bull/bear. Dark default; `.light` on `<html>` flips (see `globals.css`).
- **Profile-driven UI:** `activeProfile` drives both main layout and sidebar. Split-chart (dual-pane) only in INTRADAY/FNO.
- **Auth gating:** `page.tsx` calls `checkAuth()` (a `/users/me` round trip) and early-returns `AuthGateScreen` until the session is confirmed, then the broker connect card if the broker is not connected. The status is three-state — `unknown` holds, only a confirmed `anonymous` redirects to the auth surface, because bouncing a user out mid-check would eject everyone with a valid cookie.
- **Live data only** — no synthetic/mock generators in UI; components update only on real WS/SSE/HTTP data. `ALPHA_TEST_MODE` (`1`/`true`) only sets `window.__ALPHA_TEST_MODE__` in `app/layout.tsx`, which suppresses the "Connection Lost" overlay in `useConnectionMonitor`; it no longer swaps Kite for mock routes.
- **Charts bypass React state** for hot paths — TV/lightweight-charts `.setData()`/`.update()` called directly.
- **Server-only files:** `app/api/_gateway.ts`, `_proxy.ts` and `_featureSwitches.ts` must never be imported from a Client Component — they read unprefixed env (the gateway credential).
- **Bash on Windows:** shell is bash — use `/dev/null`, forward slashes. Line endings: repo is CRLF; git warns "LF will be replaced by CRLF" (harmless).
- **Verify after changes:** Frontend — `cd frontend && npx tsc --noEmit`, `npx vitest run src/charting` (330 tests, all green), targeted `npx vitest run <file>`, and `npm run build:web` for anything touching a route (a route can typecheck and test green and still fail to register in the production build). Rust — `cargo build` / `cargo test` in the crate you touched (`aggregator/`, `tool-server/`, `ingestion/`, …); there is no `frontend/src-tauri` any more.
- **Pre-existing test failures** — 9 tests across 4 files, failing on a clean tree, unrelated to recent work:
  - `src/components/fno/__tests__/selectors.bounding.property.test.ts` (3)
  - `src/components/chart/__tests__/SplitChartContainer.test.tsx` (3)
  - `src/components/layout/__tests__/TerminalLayout.modeSelector.test.tsx` (1)
  - `src/components/panels/__tests__/LeftPanel.search.test.tsx` (2)

  **`npx tsc --noEmit` is CLEAN** — treat any type error as yours.

---

## 13. Persistent memory

Auto-memory index lives at `C:\Users\yash\.claude\projects\D--Strat-Ai-Ai-trader\memory\MEMORY.md` with detailed notes: `project_overview.md`, `frontend_stack.md`, `frontend_conventions.md`, `feature_gate_infra.md`. **Those notes predate the desktop removal and still describe a Tauri app** — this CLAUDE.md supersedes them wherever they disagree.

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
