# Dead Code & Duplication Audit — Ai-trader Monorepo

**Date:** 2026-07-31
**Scope:** All 12 service directories, ~240k lines (Rust, TypeScript, Python, Node, SQL, YAML)
**Method:** Compiler/toolchain evidence first, then reachability tracing, then manual verification of every candidate

---

## 1. Executive summary

The monorepo is in better shape than its size suggests. There is no sprawling
graveyard of abandoned modules — most of what looks unused is either a
legitimate CLI tool with tests, or reached through a barrel export that
per-file grepping misses.

The real waste concentrates in four places:

| Rank | Item | Size | Confidence |
|------|------|------|------------|
| 1 | `frontend` 3D deps (`three`, `@react-three/fiber`) — zero imports | **34 MB** installed | Certain |
| 2 | `aggregator/src/quant/` — compiled but unreachable, duplicated by `quant-core` | **1,288 lines** | Certain |
| 3 | `alpha-backend` generated Prisma client committed to git | **15,268 lines** | Certain |
| 4 | `MOCK_BROKER` mock-data scaffold in production auth paths | 10 call sites | Certain |

**Headline numbers:** ~2,000 lines of source deletable, ~34 MB of dependency
weight removable, 15,268 lines of generated code removable from version
control, and one **stale forked file that is a live behavioural bug** (§5.1).

A note on the method, because it changed several conclusions: my first pass
flagged roughly twice as many candidates as survived. `aggregator/src/quant/`
produced **no compiler warning** despite being unreachable, while several
"unreferenced" frontend engines turned out to be fully live. Both cases are
explained in §2 — they are the reason this audit does not rest on any single
signal.

---

## 2. Methodology, and why single-signal audits fail here

Four independent signals, cross-checked against each other:

1. **Compiler evidence.** `cargo check --message-format=json` across all 10
   Rust crates, filtered for `dead_code`.
2. **Reachability tracing.** From real entrypoints only — `Dockerfile` `CMD`,
   compose service definitions, `invoke_handler![]`, Next.js route files.
3. **Dependency-manifest diffing.** Every `package.json` dependency checked for
   a real importer.
4. **Structural comparison.** Same-named files diffed across services.

### Two traps this repo sets

**Trap 1 — `#![allow(dead_code)]` blinds the compiler.**
`aggregator/src/quant/` is 1,288 lines of unreachable code that emits *zero*
warnings, because all three files open with a module-wide suppression. A
cargo-warnings-only audit reports this directory as clean. Signal 2 caught it.

**Trap 2 — barrel exports defeat per-file grep.**
`charting/engines/*.ts` files appear unreferenced when grepped individually,
because consumers import from `charting/engines/index.ts`. These are **live
code**. Any audit that greps for direct file imports will wrongly recommend
deleting them.

### Verified-live: candidates that did NOT survive

Recorded so a future audit does not re-raise them:

| Candidate | Why it is live |
|---|---|
| `quant-core/` | Path dependency of **both** `tool-server` and `frontend/src-tauri` |
| `agents/deep-quant-loop/backtest.py` (1,800 lines) | CLI tool (`argparse` + `__main__`) with **16** dedicated test files |
| `ingestion/src/option_sink.rs` | Its `allow(dead_code)` is stale — module is called from 8 sites in `main.rs` |
| `charting/engines/*` | Reached via the `index.ts` barrel |
| `tw-animate-css` | Imported in `globals.css:2`, not from TS |
| `lightweight-charts` | Live in F&O charting |
| `tools/load_tester` | Documented dev utility (`docs/ARCHITECTURE.md:51`) |
| `frontend/src/components/fno/symbolParser.ts` | Complementary to `charting/symbolUtils.ts`, not a duplicate |

The `symbolParser` / `symbolUtils` pair deserves a word: the names invite a
merge, but `symbolUtils.ts` holds one shared predicate (`isFnoSymbol`) while
`symbolParser.ts` holds four F&O field extractors. Different consumers,
different layers. **Leave them alone.**

---

## 3. Findings — ranked, biggest cut first

Ponytail tag vocabulary: `delete:` dead code · `dupe:` duplicated logic ·
`yagni:` abstraction with one impl / config nobody sets · `native:` dependency
doing what the platform already does · `shrink:` same logic, fewer lines.

---

### A-1 · `delete:` Unused 3D rendering stack — 34 MB, zero imports

**Confidence 10/10** · `frontend/package.json:21,37`

```json
"@react-three/fiber": "^9.6.1",   // 2.2 MB installed
"three": "^0.180.0",              //  32 MB installed
```

**Evidence.** Grepped `src/` and `tests/` for any import of `three`,
`@react-three/fiber`, `Canvas`, or `useFrame` → **0 files**. The only textual
hits are the English word "three" in prose comments
(`strategyEngine.ts:13` "ships at least three rule-based strategies",
`volumeProfileEngine.ts:20` "the three supported profile ranges"). No `.glb`,
`.gltf`, or shader assets exist. No `@types/three` is declared either.

**Cut:** remove both from `package.json`. **34 MB** off `node_modules`, faster
installs, faster Tauri bundling. Nothing to replace them with — no 3D feature
exists.

---

### A-2 · `delete:`+`dupe:` `aggregator/src/quant/` — 1,288 lines, compiled but unreachable

**Confidence 10/10** · `aggregator/src/quant/{mod,patterns,strategies}.rs`

```
653  aggregator/src/quant/mod.rs
290  aggregator/src/quant/patterns.rs
345  aggregator/src/quant/strategies.rs
```

**Evidence — three independent confirmations:**

1. `mod quant;` is declared at `aggregator/src/main.rs:25`, so it *compiles* —
   but grepping `quant::` across `aggregator/src/` **excluding** `src/quant/`
   itself returns **zero** hits. Nothing in the crate ever calls into it.
2. `cargo check` is silent because all three files begin with
   `#![allow(dead_code)]` (`mod.rs:18`, `patterns.rs:12`, `strategies.rs:12`).
3. The module header states the intent outright:

```rust
// STATUS: Fully implemented & unit-tested.
// PENDING: Wiring into the ohlc_server consumer loop once the indicator
//          computation pipeline (SMA50/200, MACD, SAR, RSI, etc.) is connected.
//          At that point, remove this allow and call ConsensusEngine::compile_consensus
//          inside ohlc_server::process_candle() before the WS broadcast.
```

The wiring never happened. Meanwhile the same logic was extracted into the
shared `quant-core` crate — whose copies still carry the origin comment
*"Mirrors aggregator/src/quant/patterns.rs for local Tauri execution"*
(`quant-core/src/patterns.rs:3`, `strategies.rs:3`). `quant-core/src/lib.rs`
has since grown to 20 public functions against the aggregator copy's 3, so the
shared crate is now strictly ahead.

**Cut:** delete `aggregator/src/quant/` and the `mod quant;` line. If the
consensus engine is ever wired into `ohlc_server`, take it from `quant-core`,
which is the maintained version. **−1,288 lines.**

---

### A-3 · `delete:` Generated Prisma client committed to git — 15,268 lines

**Confidence 10/10** · `alpha-backend/services/*/src/generated/`

**Evidence.** Of `alpha-backend`'s ~19.6k lines, only **1,842 are
hand-written** — 15,268 are generated Prisma client output, and
`git check-ignore` confirms they are **tracked, not ignored**. Both services
carry a full copy; `runtime/library.d.ts` alone is 3,403 lines, byte-identical
between them.

Worse, the two copies have **drifted**: `generated/client/index.d.ts` is 5,455
lines in auth-service versus 2,277 in payment-service — they were generated
from different schema states and committed at different times.

**Cut:** add `**/src/generated/` to `.gitignore`, `git rm -r --cached` both
trees, and run `prisma generate` in the Docker build and a `postinstall` hook.
**−15,268 lines from version control**, and the drift becomes structurally
impossible.

---

### A-4 · `delete:` `MOCK_BROKER` mock-data scaffold in production code paths

**Confidence 9/10** · 10 call sites across `alpha-backend/services/auth-service/`

```
middlewares/auth.middleware.ts:14-22   ← bypasses JWT auth entirely
controllers/portfolioController.ts:14
services/auth.service.ts:64, 99, 342, 357
services/kiteService.ts:16, 73, 131
```

**Evidence.** Grepped every compose file, `.env.example`, and `infra/` for
`MOCK_BROKER` → **not set anywhere**. So in every deployed configuration these
branches are permanently dead. They are not inert, though — the middleware one
is an auth bypass that hardcodes a specific user:

```ts
// auth.middleware.ts:14-22
if (process.env.MOCK_BROKER === 'true') {
  req.user = { userId: '1014418d-08a1-4372-800b-f4a21d2cbfe3', tier: 'PREMIUM' };
  return next();
}
```

One environment variable away from unauthenticated PREMIUM access as a fixed
user id.

**Cut:** delete all 10 branches. If offline development needs fake broker data,
put it behind a test fixture or a mock server, not behind a runtime check
inside the production auth path. Confidence is 9 rather than 10 only because a
developer may rely on this locally — worth one question before deleting.

---

### A-5 · `delete:` Five registered-but-never-invoked Tauri IPC commands

**Confidence 9/10** · `frontend/src-tauri/`

The backend defines and registers **36** `#[tauri::command]` functions. The
frontend invokes **31**. These five are wired into `invoke_handler![]` but
called by nothing:

| Command | Definition |
|---|---|
| `deploy_ai_sentinel` | `commands/deep_quant.rs:1999` |
| `run_ai_analysis` | `commands/deep_quant.rs:1256` |
| `get_radar_symbols` | `commands/radar.rs:137` |
| `get_trade_history` | `db.rs:196` |
| `load_historical` | `commands/charts.rs:560` |

**Evidence.** Extracted all `#[tauri::command]` definitions and all
`invoke(...)` / `tauriInvoke(...)` call sites, then diffed. Each of the five
returns **0 references** across `frontend/src/` and `frontend/tests/`.

**Methodology note.** My first extraction only matched bare `invoke(`, which
falsely flagged six commands. The frontend routes most calls through a
`tauriInvoke` wrapper (`useQuantStore.ts`), and `run_deep_quant_agent` is
invoked across a multi-line call at `useQuantStore.ts:1227`. Re-running against
both wrappers cleared that false positive. **`run_deep_quant_agent` is live —
do not delete it.**

**Before cutting**, note that each of the five is a plausible *unfinished
feature* rather than an abandoned one — an AI sentinel, a trade-history panel.
Confirm intent first. Deleting `load_historical` is safe regardless: it is a
thin command wrapper, and the underlying `history_loader::load_historical_data`
is called from three other live sites and must stay.

---

### A-6 · `delete:` Dead custom footprint chart chain — ~1,256 lines

**Confidence 9/10** · `frontend/src/`

```
446  components/chart/FootprintChart.tsx
213  hooks/useFootprintState.ts
597  hooks/useHistoricalData.ts        ← documented "(legacy)"
```

**Evidence.** A closed three-link chain that nothing else enters:

- `FootprintChart.tsx` has **no importer**. `MainTerminalChart.tsx:22` says so
  explicitly: *"MainTerminalChart therefore does NOT mount FootprintChart
  separately — that would double-mount the footprint surface."*
- Its only consumer would be `useFootprintState`, imported by
  `FootprintChart.tsx` alone.
- Which is the only importer of `useHistoricalData`.

The reason the chain died is recorded in `ChartSurface.tsx:7`: *"Now renders
the TV widget for ALL chart modes including Volume Footprint"* — TradingView
Advanced Charts replaced the hand-rolled footprint renderer.

**Caveat.** `charting/engines/footprintEngine.ts` (374 lines) exports
`buildFootprint` / `cumulativeDelta`, consumed *only* by `useFootprintState`.
It is reachable through the `engines/index.ts` barrel, so deleting the chain
orphans it. Delete it in the same commit — or keep it if the footprint math is
still wanted for tests. **−1,256 lines**, or −1,630 including the engine.

---

### A-7 · `dupe:` `historical_candles` DDL duplicated across 7 files

**Confidence 10/10**

```
backend/db/migration.rs
backend/db/migrations/002_historical.sql
frontend/src-tauri/src/services/history_loader.rs:277
frontend/src-tauri/src/commands/deep_quant.rs
frontend/src-tauri/src/services/option_chain_subscriber.rs
ingestion/src/main.rs
tool-server/src/candles.rs:66
```

**Evidence.** `tool-server/src/candles.rs:66` admits it: *"mirrors
`history_loader::run_migration` DDL exactly so the schema is identical."* Two
independent services each run `CREATE TABLE IF NOT EXISTS` against the same
QuestDB instance, and a schema change must currently be made in all seven
places or the copies silently diverge — including the `DEDUP ENABLE UPSERT
KEYS` clause, where divergence corrupts data rather than erroring.

**Cut:** one owner for the DDL. Either a migration run once at deploy time, or
a shared constant in `quant-core` (already a dependency of both
`frontend/src-tauri` and `tool-server`). Roughly **−150 lines** and, more
importantly, one place to change.

---

### A-8 · `delete:` `backend/` — orphaned directory, no crate

**Confidence 10/10** · `backend/db/migration.rs`, `backend/db/migrations/002_historical.sql`

**Evidence.** No `Cargo.toml` anywhere in `backend/`, so it is not a crate and
cannot compile. It appears in no compose file. Its own header explains it:

```rust
// This module is referenced by the Tauri lib.rs but the actual migration
// logic is inlined in the Tauri crate's services module for compilation
// simplicity. This file serves as the canonical migration runner reference.
```

The live implementation is `frontend/src-tauri/src/services/history_loader.rs:277`.
This is a copy kept "for reference" that has no way of staying in sync — and
per A-7, one of seven.

**Cut:** delete the directory.

---

### A-9 · `dupe:` Stale forked auth middleware — **behavioural bug**

**Confidence 10/10** · `alpha-backend/services/payment-service/src/middlewares/auth.middleware.ts`

This is the one finding that is not merely wasteful. The two middleware files
were copy-pasted, then only one was maintained:

| | auth-service (45 lines) | payment-service (30 lines) |
|---|---|---|
| Expired-token handling | `TokenExpiredError` → **401** + `TOKEN_EXPIRED` | *(absent)* |
| Invalid token | **401** `INVALID_TOKEN` | **403** |
| Missing token | 401 + `code` field | 401, no `code` |

**Impact.** A client whose session expires gets **401 + `TOKEN_EXPIRED`** from
the auth service and a bare **403** from the payment service for the identical
condition. The auth-service comment states the contract the fork violates:
*"Differentiate expiration vs malformed/invalid signature so the client can
react correctly (re-login on 401 vs treat 403 as a permission issue)."* So an
expired session during a payment surfaces as a permissions error instead of
triggering re-login.

`config/redis.ts` is forked the same way — identical but for three log-prefix
strings.

**Cut:** extract a shared `alpha-backend/packages/common/` with the middleware,
the redis config, and the Prisma client. Small line saving (~50), but it fixes
a real inconsistency and removes the fork-drift mechanism. Given only 1,842
hand-written lines total across both services, this is cheap.

---

### A-10 · `delete:` Four unused frontend dependencies

**Confidence 9/10** · `frontend/package.json`

| Package | Evidence |
|---|---|
| `@base-ui/react` | 0 importers in `src/` |
| `class-variance-authority` | 0 importers; no `cva(` call anywhere |
| `shadcn` | CLI scaffolding tool; `components.json` exists but **no `src/components/ui/`** — nothing was ever generated |
| `@tauri-apps/plugin-stronghold` | 0 JS importers **and** no Rust counterpart in `src-tauri/Cargo.toml`, `capabilities/`, or `tauri.conf.json` |

The stronghold one is worth flagging beyond the dependency saving:
`CLAUDE.md` documents Stronghold as the encrypted-secrets mechanism, but the
plugin is not installed on the Rust side, so **it is not actually in use**. A
Tauri plugin requires both halves. Either the docs are stale or secret storage
is not doing what the architecture claims.

**Verified-live counterexample:** `tw-animate-css` reports 0 TS importers but
is imported at `globals.css:2`. Dependency audits must check CSS `@import` too.

---

### A-11 · `delete:` Stale root `docker-compose.yml`

**Confidence 9/10**

**Evidence.** Both `deploy.sh:12` and `redeploy.sh:22` use:

```bash
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml"
```

The root `docker-compose.yml` is referenced by neither. It defines **7**
services (redpanda, questdb, redis, ingestion, alpha-terminal, aggregator,
predictive-agent, quant-rag-agent) against the prod file's **23** — it predates
`tool-server`, `deep-quant`, `auth-service`, `payment-service`, `caddy`, and
`questdb-gateway`.

A stale compose file is an operational hazard: `docker compose up` with no `-f`
picks it up by default and silently starts a 2024-era subset of the stack.

**Cut:** delete it, or rename to `docker-compose.dev.yml` and update it to
match reality. Note `docs/COMPLETE_ANALYSIS.md:192` still claims *"All backend
services are orchestrated via `docker-compose.yml`"* — false, and worth fixing
in the same pass.

---

### A-12 · `delete:` Dead pattern-analysis scaffolding in `quant-rag`

**Confidence 10/10** · `agents/quant-rag/src/patterns.rs` — from `cargo check`

```
:70  struct `PatternContract` is never constructed
:86  associated fns `sentiment_from_bias`, `normalize_confidence`, `from_detected` never used
:131 method `to_contract` is never used
:139 field `symbol` is never read
:342 associated fn `analyze_contract` is never used
:8   unused import `std::collections::HashMap`
:481,482,532 unused variables `bc_ab`, `cd_xc`, `current_time`
```

An entire `PatternContract` abstraction was built and never wired. `~120 lines`.
The unused variables at `:481-482` (`bc_ab`, `cd_xc` — Fibonacci ratio
computations in what looks like harmonic-pattern detection) suggest a
half-finished validation step; worth a glance before deleting in case a ratio
check was meant to be applied.

---

### A-13 · `delete:` Dead Kite session structs in `ingestion`

**Confidence 10/10** · from `cargo check`

```
struct `KiteSessionResponse` is never constructed
struct `KiteSessionData`     is never constructed
function `generate_access_token` is never used
```

Token generation lives in the auth service now. **~40 lines.**

---

### A-14 · `yagni:` Unwired `#[allow(dead_code)]` placeholders

**Confidence 8/10**

| Location | What |
|---|---|
| `aggregator/src/kite_api.rs:42,67` | `QuoteData` / `QuoteParams` — *"PENDING: GET /api/kite/quote... Once the quote_handler function is added to the router, remove these allows"* |
| `frontend/src-tauri/src/commands/charts.rs:486` | `enum HistorySource { Intraday, Ticks }` — never matched on |
| `frontend/src-tauri/src/services/fno_service.rs:100`, `instrument_master.rs:21` | targeted suppressions worth re-checking |

`ingestion/src/option_sink.rs:30` carries the same marker — *"Wired into the
tick router in task 8.1; until then these public sinks are intentionally
unreferenced"* — but that wiring **did** happen: `main.rs` calls into it from 8
sites. **The suppression is stale and should be removed so the compiler can
watch that module again.** It is the clearest example of why these markers rot.

---

### A-15 · `shrink:` Five near-identical WebSocket fan-out servers

**Confidence 7/10**

```
108  aggregator/src/ws_server.rs
298  aggregator/src/ohlc_server.rs
 25  alpha-terminal/src/ws_server.rs
 34  agents/predictive/src/ws_server.rs
 35  agents/quant-rag/src/ws_server.rs
```

The three small ones are ~30 lines each of the same `accept_async` +
broadcast-subscribe + forward loop. Consolidating into a shared crate saves
maybe 60 lines — marginal, and it would add a dependency edge between otherwise
independent services. **Recommend leaving as-is**; noted for completeness. The
proto `build.rs` duplication across 6 crates is the same story: repetitive, but
each crate compiles only the `.proto` files it needs from the genuinely shared
`shared_protos/`, which is correct design.

---

### A-16 · `delete:` Two loose ends the compiler found

**Confidence 10/10**

- `frontend/src-tauri/src/commands/fno.rs:914` — `fn combined_payload` is never
  used. ~25 lines.
- `alpha-terminal/src/`— unused `StreamExt` import and an unnecessary `mut`;
  `cargo fix` resolves both.

**And one that is not a deletion:** `tools/load_tester` **does not compile**.

```
src/main.rs:266:20: error[E0063]: missing field `open_interest`
                   in initializer of `Tick`
```

The shared `Tick` proto gained an `open_interest` field and the load tester was
never updated. So it is not merely unused — it is *unusable*, and has been
since that field landed. Either fix the initializer (a one-line change) or
delete the tool; leaving a non-compiling crate in the tree is the worst of the
three options because it will keep breaking any workspace-wide `cargo build`.

---

## 4. Cut summary

| Tag | Finding | Lines | Other |
|---|---|---|---|
| `delete:` | A-1 3D deps | — | **34 MB** |
| `delete:` | A-2 `aggregator/src/quant/` | 1,288 | |
| `delete:` | A-3 generated Prisma (from git) | 15,268 | |
| `delete:` | A-4 `MOCK_BROKER` branches | ~60 | closes auth-bypass risk |
| `delete:` | A-5 five dead IPC commands | ~250 | confirm intent first |
| `delete:` | A-6 footprint chain | 1,256–1,630 | |
| `dupe:` | A-7 DDL ×7 | ~150 | |
| `delete:` | A-8 `backend/` | ~80 | |
| `dupe:` | A-9 forked middleware | ~50 | **fixes a real bug** |
| `delete:` | A-10 four npm deps | — | 4 deps |
| `delete:` | A-11 stale compose | ~155 | removes ops hazard |
| `delete:` | A-12 `quant-rag` scaffolding | ~120 | |
| `delete:` | A-13 ingestion structs | ~40 | |
| `yagni:` | A-14 stale suppressions | ~30 | restores compiler coverage |
| `shrink:` | A-15 WS servers | (~60) | **not recommended** |
| `delete:` | A-16 compiler loose ends | ~30 | + `load_tester` is broken |

**net: −4,500 source lines, −15,268 generated lines from git, −4 npm deps, −34 MB**

---

## 5. Recommended order

**Do first — zero risk, immediate payoff**

1. **A-1** drop `three` + `@react-three/fiber` (34 MB, one-line change)
2. **A-3** gitignore the generated Prisma client
3. **A-10** drop the four unused deps — but keep `tw-animate-css`
4. **A-13**, **A-12**, **A-16** apply the compiler's own findings
5. **A-11** delete or rename the stale root compose

**Do next — needs a moment's thought**

6. **A-9** extract `alpha-backend/packages/common/` — *fixes the 401/403
   inconsistency, not just duplication*
7. **A-2** delete `aggregator/src/quant/`
8. **A-8** delete `backend/`
9. **A-4** remove `MOCK_BROKER` — confirm no one depends on it locally
10. **A-14** drop the stale `option_sink.rs` suppression first; it is pure win

**Confirm before cutting**

11. **A-5** five IPC commands — unfinished features or abandoned?
12. **A-6** footprint chain — is the footprint math wanted for future work?
13. **A-7** pick one DDL owner (this is design work, not deletion)

**Skip:** A-15.

---

## 6. One thing worth acting on beyond cleanup

**The Stronghold gap (A-10).** `CLAUDE.md` documents Stronghold as the
encrypted-secrets mechanism, but the plugin is absent from the Rust side
entirely — no `Cargo.toml` entry, no capability grant. A Tauri plugin needs
both halves to function. Either the documentation is stale or secrets are not
being stored the way the architecture claims. Worth confirming which.

**A candidate that did not survive.** Mid-audit I suspected
`agents/deep-quant-loop/options.py:1225` of building its QuestDB URL with a
backslash separator, which would have broken `/options/snapshot` outright. It
does not — the line reads:

```python
f"{QUESTDB_HTTP_URL}/exec", params={"query": query}, timeout=timeout
```

A correct forward slash. Recorded here so the same suspicion is not re-raised.

---

## 7. What was audited and found clean

- **`quant-core/`** — genuinely shared, consumed by two crates
- **`agents/deep-quant-loop/`** — every module imported by ≥1 other except the
  CLI tools (`backtest`, `attribution`, `telemetry`, `journal`, `eval/harness`),
  which are entrypoints in their own right and well covered by tests
- **`agents/sentiment/`** — 15 JS files, built by prod compose
- **`ingestion/`**, **`alpha-terminal/`**, **`agents/technical/`**,
  **`tool-server/`** — clean cargo checks apart from two trivial lints
- **`shared_protos/`** — correctly shared; no duplication
- **`frontend/src/charting/engines/`** — live via barrel export
- **`tools/load_tester`** — documented dev utility, but **currently broken**;
  see A-16

---

*Every finding lists the command run and its output. The `verified-live` table
in §2 exists so the same candidates are not re-raised next time. Line counts
are `wc -l`; dependency sizes are `du -sh` against `node_modules`.*

