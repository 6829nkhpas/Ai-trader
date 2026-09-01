# Find Quant Trade — Multi-Session Workspace: Architecture & Design

> **Status: PHASES 0–10.1 IMPLEMENTED, BEHIND FLAGS. T10.2 (CI e2e) and Phase 11 not started.** This document is the
> decision record that the migration plan (`find-quant-multi-session-migration-plan.md`) executes
> against; the plan's per-task entries record what was actually built, including where the plan was
> wrong and was corrected.
>
> Every flag defaults **off**, so the shipped behaviour is still the single-session path:
> `DEEP_QUANT_REQUIRE_IDENTITY`, `DEEP_QUANT_SESSIONS_ENABLED`, `DEEP_QUANT_REQUIRE_SESSION`,
> `DEEP_QUANT_PERSIST_STREAM`, `FQ_REQUIRE_IDENTITY`, `NEXT_PUBLIC_FQ_MULTI_SESSION`.
>
> **Not yet verified against reality.** No part of this has been exercised against a live LLM, live
> market data, or a real price trigger, and the `tool-server` Rust changes have never been compiled
> (no toolchain on the development machine — CI is the first real check). Durability, multi-session
> isolation and auth are covered by unit/property tests only; the claims in §Verification are the
> tests that exist, not a production observation.
>
> **Date:** 2026-09-01 (design), amended through Phase 9
> **Scope:** `frontend/` (state + UI + proxy tier), `agents/deep-quant-loop/` (persistence + API +
> streaming), `docker-compose.prod.yml`, CI. The quant engine (`quant-core`, `tool-server`,
> `aggregator`), market-data ingestion, and QuestDB are **out of scope** and are not modified.

---

## 0. Method — what was actually verified

Every claim in §1 was read out of the working tree, not recalled. Where this document contradicts
the prior architecture audit, the code won.

Files read directly for this design:

| Area | Files |
|---|---|
| Frontend state | `frontend/src/store/useQuantStore.ts` (1905 L), `useTradeStore.ts`, `useAuthStore.ts` |
| Frontend UI | `components/quant/{DeepQuantPanel,AgentTerminal,TradeQaPanel}.tsx`, `components/quant/deep-quant/*` |
| Frontend transport | `lib/bridge/{webAdapters,events}.ts`, `hooks/useApi.ts`, `lib/api/client.ts` |
| Proxy tier | `app/api/{_gateway,_proxy,_featureSwitches}.ts`, `app/api/deepquant/[...path]/route.ts` |
| Backend | `agents/deep-quant-loop/{main,stream_events,interaction_log,reco_store,entitlements,session,hashchain}.py`, `graph.py` (checkpointer + QA grounding), `requirements.txt` |
| Watcher | `tool-server/src/main.rs` (`post_resume`) |
| Deployment | `docker-compose.prod.yml`, `agents/deep-quant-loop/Dockerfile`, `.github/workflows/ci.yml` |

---

## 1. Current architecture — verified facts

### 1.1 The run lifecycle today

```
DeepQuantPanel (FIND/VERIFY press)
  → useQuantStore.fetchDeepAnalysis(symbol)
      reads symbol/timeframe/profile from useTradeStore AT CALL TIME
      runKey = `${SYMBOL}::${PROFILE}`
  → bridgeInvoke('run_deep_quant_agent')
  → webAdapters.startAgentRun()
      threadId = `thread_${symbol}_${Date.now()}`          ← CLIENT-MINTED
      POST /api/deepquant/run  { thread_id, message, mode, symbol, timeframe, profile, user_id }
  → Next route handler (deepquant/[...path]) → proxyRequest(stream: true)
  → FastAPI POST /run → event_generator → _run_events → graph.astream(...)
  → SSE frames ← _tee_publish ← every frame also published to _SUBSCRIBERS[thread_id]
  → relaySse → emitBridgeEvent('deep-quant-stream')
  → useQuantStore.handleStreamEvent → applyStreamEvent(session) → sessionsByKey[runKey] + flat mirror
```

On `RUN_FINISHED{status:"paused"}` the client re-attaches to `GET /api/deepquant/stream/{threadId}`
and keeps that connection for the whole watching lifecycle, because the headless watcher in
`tool-server` POSTs `/resume` and *discards* the response stream (`tool-server/src/main.rs`
`post_resume`). The fan-out hub is the only path by which a watcher resume reaches the browser.

### 1.2 Verified problems

| # | Problem | Evidence |
|---|---|---|
| P1 | **Zero durable chat persistence.** `useQuantStore` is a plain `create<QuantStore>()` with no `persist` middleware. Everything dies on reload. | `useQuantStore.ts:980` |
| P2 | **Session identity collides.** `_sessionKey(symbol, profile)` → `RELIANCE::INTRADAY`. A second FIND on the same symbol+profile *overwrites* the first session (`fetchDeepAnalysis` writes a `blankSession()` at that key). Two timeframes cannot coexist. | `useQuantStore.ts:750-755`, `1310-1327` |
| P3 | **Q&A is not session-safe.** `askQuestion` reads `get().currentThreadId` (the *flat mirror*, i.e. whatever is on screen) and routes streamed chunks by React-closure `assistantMsgId` into the *flat* `qaMessages`. It never writes `sessionsByKey`. Switching sessions mid-answer projects a different array over the flat field and every subsequent chunk is silently dropped. | `useQuantStore.ts:1680`, `1757-1826` |
| P4 | **Global Q&A single-flight.** `qaStatus === 'streaming'` is a flat field, so a Q&A on session A blocks a Q&A on session B. | `useQuantStore.ts:1687-1690` |
| P5 | **Stream routing falls back to "the active session".** Both the `RUN_STARTED` branch and the `else` branch resolve `runKey = st._streamingKey \|\| st.activeViewKey`. A frame whose `thread_id` is unknown can land in the wrong session. | `useQuantStore.ts:1540-1552` |
| P6 | **The flat mirror is a second mutable source of truth.** `projectSession()` copies 12 of 14 `QuantSession` fields to the top level; `clearAiPlan`, `clearQa` and `askQuestion` write **only** the flat copy, so `sessionsByKey` silently diverges. | `useQuantStore.ts:725-740`, `1414-1426`, `1673-1677`, `1679-1848` |
| P7 | **`user_id` is self-asserted.** The browser puts it in the request body; `_gateway.ts` deliberately *strips* `cookie` and `authorization` before forwarding. Nothing server-side verifies it. | `_gateway.ts` `STRIPPED_REQUEST_HEADERS`, `main.py` `RunRequest.user_id` |
| P8 | **`GET /stream/{thread_id}` is unauthenticated and unowned.** Any caller who knows/guesses a thread id receives that thread's full research stream. `thread_${symbol}_${Date.now()}` is guessable. | `main.py:795-833` |
| P9 | **`POST /cancel` has no `user_id` at all.** Any caller can cancel any thread. | `main.py:894-919`, `CancelRequest` |
| P10 | **LangGraph state is in-memory.** `memory = MemorySaver()`; `graph = workflow.compile(checkpointer=memory)`. A container restart destroys every thread → `/qa` answers ungrounded, `/resume` 400s. | `graph.py:6111`, `6114` |
| P11 | **All SQLite files are ephemeral.** The `deep-quant` service in `docker-compose.prod.yml` declares **no `volumes:`** and the Dockerfile writes DBs beside the module in `/app`. `compliance.db`, `trade_journal.db`, `telemetry.db` are destroyed on every redeploy. | `docker-compose.prod.yml` `deep-quant:` block, `hashchain.py:56` |
| P12 | **Frames published with no subscriber attached are lost forever.** `_publish_frame` returns early when `_SUBSCRIBERS[thread_id]` is empty; a full 256-slot queue drops silently. There is no backlog or replay. | `main.py:351-364` |
| P13 | **`/qa` is not teed to the hub.** `/run` and `/resume` are wrapped in `_tee_publish`; `/qa` returns the bare generator. A re-attached client never sees Q&A frames. | `main.py:889-892` |
| P14 | **Single-replica-only globals.** `_CANCELLED`, `_SUBSCRIBERS`, `MemorySaver`, and `entitlements._cache` are process-local. | `main.py:314`, `336` |
| P15 | **Per-session UI state lives outside the session.** `activeMode` is React state in `DeepQuantPanel.tsx:106`; the composer draft is `useState` in `TradeQaPanel.tsx:28`; the whole verification form is `useVerificationForm.ts`, which resets on *symbol* change only. | as cited |
| P16 | **Playwright is not a CI safety net, and the existing setup is stale.** `.github/workflows/ci.yml` has three jobs (`frontend`, `rust-services`, `python-agent`) and **no e2e job**. There is no `test:e2e` npm script, so the suite is only reachable via a manual `npx playwright test`. `frontend/playwright.config.ts` still targets **port 1420** (the retired Tauri dev port) and starts `next dev`, and `frontend/tests/e2e.spec.ts` drives `invoke('run_deep_quant_analysis')` "through a stubbed Tauri IPC bridge" and cites `src-tauri/src/quant/mod.rs` — a directory that no longer exists. The E2E layer is not merely unrun; it tests an architecture that was deleted. | `ci.yml`, `frontend/package.json` scripts, `playwright.config.ts:30,45`, `tests/e2e.spec.ts:1-30` |
| P17 | **No server-state cache library.** `hooks/useApi.ts` is a per-hook `useState` + `useEffect` fetch with no shared cache, no invalidation, no dedup. TanStack Query is absent. | `useApi.ts` |

### 1.3 Verified assets worth preserving

- **`applyStreamEvent`** (`useQuantStore.ts:760-976`) — a pure `(session, frame) → session` reducer with
  four property-test suites pinning hard-won behaviour: DECISION is first-write-wins, conviction is
  never defaulted to 75, `RUN_FINISHED` enriches but never downgrades a committed decision, and the
  `watching → RUN_STARTED` resume branch drops the stale leg's plan. **Reuse verbatim.**
- **`QuantSession`** (`:687-703`) and `extractFinalTrade` / `mergeFinalPlan` — keep.
- **The thread-id routing map** `_threadToKey` — the right idea; only the fallback is wrong.
- **The SSE contract** in `stream_events.py` — every payload is already stamped with `thread_id`
  (`main.py:550-551`), so session-safe routing needs **no new backend event fields**.
- **`interaction_log` / `reco_store`** — hash-chained, trigger-enforced append-only compliance stores.
  Not to be touched. `reco_store` carries `UNIQUE(thread_id) WHERE thread_id IS NOT NULL`.

### 1.4 Audit validation

| Audit claim | Verdict | Note |
|---|---|---|
| Chat has zero durable persistence | **Confirmed** | P1 |
| No user-data backend in this repo | **Confirmed** | Auth/credits are `api-web.stratai.live`; `lib/api/client.ts` |
| `user_id` is self-asserted | **Confirmed** | P7 |
| `GET /stream/{thread_id}` unauthenticated | **Confirmed** | P8 |
| deep-quant SQLite is ephemeral | **Confirmed** | P11 — no volume at all, not merely a wrong path |
| Q&A streaming has global routing problems | **Confirmed, worse than stated** | P3 + P4: also a process-wide single-flight lock |
| `${SYMBOL}::${PROFILE}` is not a valid identity | **Confirmed** | P2 |
| `QuantSession` + `applyStreamEvent` are worth reusing | **Confirmed** | §1.3 |
| MemorySaver insufficient | **Confirmed** | P10 |
| `session.py` is a user-session module | **REFUTED** | `session.py` is the *market* session/expiry classifier — pure date math, no DB, no users. The name is a false friend; do not extend it. |
| `telemetry.py` has a `sessions` table that could host this | **REFUTED as a target** | It folds a run + its resumes by `thread_id` for measurement, holds no messages, and is explicitly best-effort/swallowing. Using it would make chat loss silent by design. |
| Only `/stream` and `/cancel` need ownership | **INCOMPLETE** | `/qa` and `/resume` also need it, and `/resume` must additionally keep working for the *unauthenticated headless watcher* (§7.4). |
| Playwright is not a reliable CI net | **Confirmed, stronger** | It is not wired into CI at all. |

---

## 2. Target architecture

```
                                USER  (verified identity)
                                  │
                                  └── SESSION            sess_01J...   opaque, server-minted
                                        ├── metadata     title · symbol(immutable) · profile(immutable)
                                        │                timeframe(mutable default) · status · active_run_id
                                        │
                                        ├── RUN 1  run_01J...  ⇄  thread_id (LangGraph checkpoint)
                                        │     ├── run_events[]   ordered durable SSE transcript (glass box)
                                        │     └── messages[]     analysis_request / analysis_answer / qa_*
                                        │
                                        ├── RUN 2  run_01J...  ⇄  thread_id
                                        │
                                        └── messages[]     session-ordered chat stream (seq)
```

### Source-of-truth split (binding)

| Store | Owns | Never holds |
|---|---|---|
| **`sessions.db`** (new, deep-quant, durable volume) | user-visible sessions, runs, messages, run_events | LLM execution state; audit records |
| **LangGraph checkpoint** (`AsyncSqliteSaver`, same volume, separate file) | model context, tool state, interrupt/resume state, QA grounding | user-visible history (it is an implementation detail of execution) |
| **`compliance.db`** (`interaction_log`, `reco_store`) | immutable hash-chained audit | mutable chat |
| **`trade_journal.db` / `telemetry.db`** | outcome scoring / measurement | chat |
| **QuestDB** | market time-series | anything session-shaped |
| **Frontend** | server cache + active-session id + ephemeral UI + in-flight stream buffer | the permanent record |

Synchronisation boundary: `runs.thread_id` is the **only** join between the application store and the
LangGraph checkpoint, and it is written once at run creation inside the same transaction that creates
the run row. Nothing else crosses. The checkpoint is never read to answer a "what did I say" question,
and `sessions.db` is never read to reconstruct model context.

---

## 3. Decisions

### A. Session store location → **Option C: deep-quant owns it, with a verified identity forwarded from the Next tier**

| Criterion | A: api-web owns | B: new session service | **C: deep-quant owns** |
|---|---|---|---|
| Authentication | already has it | needs its own | needs a forwarded identity — designed in §7 |
| Data ownership | user data with user data | clean but arbitrary boundary | co-located with the *producer* of every message |
| Streaming write path | +1 cross-service hop **per frame batch** | +1 hop per frame batch | **in-process** |
| Durability on client disconnect | requires deep-quant→api-web write anyway | same | **guaranteed** — the writer is the streamer |
| Deployment | outside this repo — cannot be built here | new container, CI job, DB, RAM on an 8 GB box already 4 GB committed to QuestDB | volume + env only |
| Latency | worst | worse | best |
| Failure modes | api-web down ⇒ analysis cannot record | new SPOF | deep-quant down ⇒ no analysis anyway (already true) |
| Horizontal scaling | best | good | **pinned to 1 replica** — already true today (P14) |
| Complexity | needs an external repo change | highest | lowest of the three that actually work |

**Decisive argument:** the requirement "an incomplete streamed response must NEVER silently appear as
successful" and "client disconnect must not destroy server-side execution" can only be honoured by
writing the transcript **where it is produced**. Options A and B both put a network boundary between
the SSE generator and the store, which reintroduces exactly the loss window P12 already documents.
Option C also keeps `session → run → thread_id → checkpoint` inside one transactional boundary.

Accepted costs, stated plainly:
1. deep-quant becomes a stateful data owner and therefore **must be single-replica** until
   `_CANCELLED` / `_SUBSCRIBERS` move to shared infrastructure (§10).
2. It needs a persistent volume. This is required regardless — P11 means the *compliance* chain is
   currently being destroyed on every deploy, which is a separate and more serious bug this migration
   fixes as a prerequisite.

**Not chosen but recorded:** if deep-quant ever needs >1 replica, the store moves to Postgres and
`_SUBSCRIBERS` moves to Redis pub/sub (Redis is already in the fleet with a volume). The repository
abstraction in §5.5 exists so that is a driver swap, not a rewrite.

### B. Session vs Run vs Thread vs Message → four distinct entities

- **Session** = the conversation. Opaque id. Owns metadata and message ordering.
- **Run** = one analysis execution. **1 run ⇄ 1 `thread_id` ⇄ 1 LangGraph thread.**
- **Message** = one user-visible chat turn, optionally attributed to a run.
- **Run event** = one durable SSE frame of a run's glass box.

A session may contain many runs. A second FIND press in the same session creates a **new** run with a
**new** thread_id — it must not reuse the thread, because `reco_store` enforces
`UNIQUE(thread_id) WHERE thread_id IS NOT NULL`: a second decision on the same thread would collide
with the compliance record. That constraint, found in the code, is what pins this decision.

**Q&A grounding — chosen: explicit `context_run_id`, defaulting to `sessions.active_run_id`.**

`POST /qa` takes `session_id` plus an optional `context_run_id`. The server resolves
`context_run_id → runs.thread_id` and streams on that thread, which is byte-identical to today's
grounding mechanism (`graph.build_qa_context` reads the checkpointed `decision` + defensibility record
for that thread). The resolved `run_id` is then stored on both the question and answer message rows,
so the transcript states its own grounding after the fact and a reopened session can render
"asked about the 10:31 run" without inference.

Rejected alternatives: *thread_id from the client* (today's design — unownable, spoofable);
*implicit "latest run"* (silently re-grounds an old question when a new FIND lands mid-typing);
*session-level only* (cannot express "ask about the earlier run", which is the whole point of
multiple runs per session).

### C. Session metadata → symbol/profile immutable, timeframe a mutable default

The tab must render `RELIANCE · 10m · 10:31 AM` from the session row alone, so all three live on the
session. But:

- **`symbol` immutable.** A tab is an instrument. A session whose symbol changed would hold a
  conversation about two instruments, and its message history would be misleading. Switching symbol
  creates a new session.
- **`profile` immutable.** Matches today's semantics — the existing code comment is right that
  `TMPV::INTRADAY` and `TMPV::FNO` are genuinely distinct analyses.
- **`timeframe` mutable.** Re-running the same instrument at 5 m after 10 m within one conversation is
  a normal analyst action.
- **Every run snapshots symbol/timeframe/profile immutably**, so changing a session's timeframe never
  rewrites what an earlier run actually analysed.
- `title` is nullable. `NULL` ⇒ the client renders the derived label; a non-null title is a user rename.

### D. Message model → 3 roles, 5 statuses, no tool role

Roles: `user`, `assistant`, `system`. **No `tool` role** — tool activity is structured run-event data
(Decision E), and flattening it into a message row would destroy the structure the glass box needs.
`system` is for durable notices ("analysis cancelled by user", "the price watch went quiet") that
today are synthesised as reasoning steps and lost on reload.

Statuses: `streaming` · `complete` · `truncated` · `error` · `cancelled`.

The honesty rule is enforced on the **read** path, not hoped for on the write path: a message left
`streaming` by a crashed process is reported as `truncated` (§8.4). A `streaming` row is never
rendered as a completed answer.

`kind` discriminates `analysis_request` / `analysis_answer` / `qa_question` / `qa_answer` / `notice`
so the UI can render a FIND turn differently from a follow-up without parsing content.

### E. Reasoning transcript → a durable, structured `run_events` table

`reasoningSteps` and `qaMessages` stay structurally separate in the database, exactly as the audit
requires, but for a better reason than symmetry: persisting the **raw ordered SSE frames** makes three
things possible that a flattened text blob cannot.

1. **Rehydration replays the reducer.** Reopening a session feeds stored frames through the existing,
   property-tested `applyStreamEvent`, so a restored transcript is byte-identical to a live one and
   there is no second rendering path to drift.
2. **Gap-free reattach.** `GET /stream/{thread_id}?after_seq=N` can replay from the table, which
   closes P12 (frames published to nobody are currently lost forever) — a real correctness fix, not
   just persistence.
3. **Structure survives.** `TOOL_CALL_START.args`, `TOOL_CALL_RESULT.result`, `VERIFICATION_STEP`,
   `BEST_CURRENT_READ.levels` and `DECISION.execution_levels` keep their shape.

The UI still presents one conversation stream. The database does not become one blob.

### F. Database → **SQLite (WAL) on a named volume**, behind a repository abstraction

| Criterion | **SQLite + volume** | Postgres |
|---|---|---|
| Concurrent users | fine — one writer *process*, WAL readers unblocked | better |
| Write volume | batched frame writes ≈ tens/sec at peak; well inside SQLite's envelope | overkill |
| Multi-device access | works (server-side store, not client-side) | works |
| Horizontal scaling | **blocks it** — accepted, already blocked by P14 | enables it |
| Transactional consistency | full ACID, single writer | full |
| Migrations | hand-rolled `schema_version` stepper | needs alembic (new dep) |
| Ops complexity | one file to back up | new container, ~256 MB RAM, credentials, pooling |
| Fit with existing code | **matches three existing stores and `hashchain.connect()` conventions** | first non-sqlite3 DB dependency in the service |
| 8 GB host budget | 0 MB | ~256 MB against a box already at 4 GB QuestDB + 512 MB deep-quant |

**Chosen: SQLite.** Explicitly accepted limitations:
- deep-quant **must** run exactly one replica. Enforced at startup (§10), not left to discipline.
- Long transactions must be avoided; all writes are short and batched.
- `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON` per connection.
- A **persistent volume is mandatory**; the migration does not claim durability until §10 ships.

The DB is reached only through `session_store.py`'s repository functions. No SQL leaks into `main.py`.
The documented trigger to move to Postgres: a second deep-quant replica, or p99 write latency > 50 ms.

### G. Server-side identity → cookie → `/users/me` → HMAC internal assertion

Fully implementable from this repository. See §7.

### H. Lifecycle → soft archive by default; hard delete scrubs app data only

`CREATE · OPEN · SWITCH · RENAME · ARCHIVE · REOPEN · DELETE`. `DELETE` sets
`status='deleted'`, `archived_at`, blanks `title`, and removes `messages` + `run_events` rows for the
session. It **never** touches `interaction_log` or `recommendations` — those are the five-year SEBI
record and are trigger-protected against UPDATE/DELETE anyway. The two concerns are separate stores
for exactly this reason (§11).

---

## 4. Database design

New file: `agents/deep-quant-loop/session_store.py`. New DB: `SESSIONS_DB_PATH`, default
`/data/sessions.db`. Deliberately **not** `compliance.db` — mixing a mutable store into a file whose
tables carry append-only triggers invites the exact confusion Phase 19 forbids.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE sessions (
  session_id    TEXT PRIMARY KEY,                 -- 'sess_' || 26-char Crockford base32 (ULID: time-sortable, opaque)
  user_id       TEXT NOT NULL,
  title         TEXT,                             -- NULL => client renders the derived label
  symbol        TEXT NOT NULL,                    -- IMMUTABLE
  profile       TEXT NOT NULL,                    -- IMMUTABLE
  timeframe     TEXT NOT NULL,                    -- mutable default; runs snapshot their own
  status        TEXT NOT NULL DEFAULT 'active',
  active_run_id TEXT,                             -- default Q&A grounding target
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  archived_at   REAL,
  metadata_json TEXT,                             -- small, genuinely-flexible metadata only
  CHECK (status IN ('active','archived','deleted')),
  CHECK (length(symbol) > 0 AND length(profile) > 0 AND length(timeframe) > 0),
  CHECK ((status = 'active') = (archived_at IS NULL))
);
-- The tab bar and the history list are the same query: owner's sessions, newest activity first.
CREATE INDEX idx_sessions_owner_activity ON sessions(user_id, status, updated_at DESC);
CREATE INDEX idx_sessions_owner_symbol   ON sessions(user_id, symbol, status);

CREATE TABLE runs (
  run_id            TEXT PRIMARY KEY,             -- 'run_' || ULID
  session_id        TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  user_id           TEXT NOT NULL,                -- denormalised: ownership check on /stream and /cancel
                                                  -- is a single indexed read, no join
  thread_id         TEXT NOT NULL,                -- the LangGraph checkpoint key. 1:1 with the run.
  kind              TEXT NOT NULL,                -- find | verify
  symbol            TEXT NOT NULL,                -- immutable snapshot
  timeframe         TEXT NOT NULL,                -- immutable snapshot
  profile           TEXT NOT NULL,                -- immutable snapshot
  model             TEXT,
  manual_trade_json TEXT,                         -- VERIFY inputs, so a reopened VERIFY shows what was verified
  status            TEXT NOT NULL,
  terminal_status   TEXT,                         -- set-once; makes duplicate terminal events idempotent
  started_at        REAL NOT NULL,
  ended_at          REAL,
  last_event_at     REAL NOT NULL,
  last_seq          INTEGER NOT NULL DEFAULT 0,
  CHECK (kind IN ('find','verify')),
  CHECK (status IN ('running','watching','complete','cancelled','error','truncated'))
);
CREATE UNIQUE INDEX idx_runs_thread   ON runs(thread_id);
CREATE INDEX        idx_runs_session  ON runs(session_id, started_at);
CREATE INDEX        idx_runs_owner    ON runs(user_id);
-- Startup reconciliation scans exactly this: runs that claim to be live.
CREATE INDEX        idx_runs_live     ON runs(status, last_event_at) WHERE status IN ('running','watching');

CREATE TABLE messages (
  message_id    TEXT PRIMARY KEY,                 -- 'msg_' || ULID
  session_id    TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  run_id        TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
  seq           INTEGER NOT NULL,                 -- dense per session, allocated in-transaction
  role          TEXT NOT NULL,
  kind          TEXT NOT NULL,
  content       TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL,
  error_detail  TEXT,
  activity_json TEXT,                             -- compact tool-activity lines for a Q&A turn
  client_msg_id TEXT,                             -- client idempotency key: a retried send cannot duplicate
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  CHECK (role IN ('user','assistant','system')),
  CHECK (kind IN ('analysis_request','analysis_answer','qa_question','qa_answer','notice')),
  CHECK (status IN ('streaming','complete','truncated','error','cancelled')),
  CHECK (role <> 'user' OR status = 'complete')    -- a user message is never "streaming"
);
CREATE UNIQUE INDEX idx_messages_seq    ON messages(session_id, seq);
CREATE UNIQUE INDEX idx_messages_client ON messages(session_id, client_msg_id) WHERE client_msg_id IS NOT NULL;
CREATE INDEX        idx_messages_run    ON messages(run_id);

CREATE TABLE run_events (
  run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,                  -- monotonic per run, from runs.last_seq
  event        TEXT NOT NULL,                     -- RUN_STARTED | REASONING | TOOL_CALL_* | ... verbatim
  payload_json TEXT NOT NULL,                     -- the frame's `data` object, unmodified
  created_at   REAL NOT NULL,
  PRIMARY KEY (run_id, seq)                       -- replay is idempotent by construction
);

CREATE TABLE schema_version (version INTEGER NOT NULL);
```

Notes on the constraints that are doing real work:

- `UNIQUE(run_id, seq)` — a replayed or duplicated frame cannot be stored twice, so reconnect cannot
  duplicate the transcript.
- `UNIQUE(session_id, client_msg_id)` — a retried `POST /qa` cannot duplicate the user's question.
- `runs.terminal_status` set-once — duplicate `RUN_FINISHED` frames are idempotent server-side, the
  same guarantee `applyStreamEvent`'s `_runFinishedProcessed` gives client-side.
- `CHECK (role <> 'user' OR status = 'complete')` — makes "a user message can never be half-written"
  a schema fact.
- `REAL` timestamps (epoch float) to match `hashchain.now()` and the three existing stores.

**Retention.** `run_events` is the only table with unbounded growth (~10²–10³ rows per run).
`RUN_EVENTS_RETENTION_DAYS` (default 90) prunes events for runs whose session is `archived`/`deleted`
or whose `ended_at` is older than the window. Pruning is application data only and is explicitly
forbidden from touching `compliance.db` — the pruner opens `SESSIONS_DB_PATH` and nothing else.

---

## 5. API contracts

### 5.1 New session/run/message routes (deep-quant, all identity-required)

| Method | Path | Body / query | Response |
|---|---|---|---|
| `POST` | `/sessions` | `{symbol, profile, timeframe, title?}` | `201 Session` |
| `GET` | `/sessions` | `?status=active&cursor=&limit=25&q=` | `{items: SessionSummary[], next_cursor}` |
| `GET` | `/sessions/{session_id}` | — | `Session` \| `404` |
| `PATCH` | `/sessions/{session_id}` | `{title?, timeframe?, status?}` | `Session` \| `404` \| `409` on immutable field |
| `DELETE` | `/sessions/{session_id}` | `?hard=false` | `{session_id, status}` |
| `GET` | `/sessions/{session_id}/messages` | `?after_seq=0&limit=200` | `{items: Message[], last_seq}` |
| `GET` | `/sessions/{session_id}/runs` | — | `{items: Run[]}` |
| `GET` | `/runs/{run_id}/events` | `?after_seq=0&limit=1000` | `{items: [{seq, event, data}], last_seq}` |

`404` — never `403` — for a session owned by someone else. A 403 confirms the id exists, which is an
enumeration oracle. The ownership predicate is `WHERE session_id = ? AND user_id = ?`; there is no
code path that reads a session by id alone.

`SessionSummary` is what the tab bar and the history list need, and nothing more:
`{session_id, title, symbol, timeframe, profile, status, updated_at, last_run: {run_id, status, action, ended_at} | null, message_count}`.

### 5.2 Evolved existing routes (additive; old shapes keep working)

```
POST /run
  + session_id      required once DEEP_QUANT_REQUIRE_SESSION=1
  + client_msg_id   optional idempotency key for the analysis_request message
  - thread_id        NO LONGER SUPPLIED BY THE CLIENT. Server-generated, returned in RUN_STARTED.
                     Legacy: a body carrying thread_id and no session_id takes the pre-migration
                     path (ephemeral session, current behaviour) until the flag flips.
  Response: unchanged SSE. RUN_STARTED payload gains `session_id` and `run_id` (additive).

POST /qa
  + session_id      required
  + context_run_id  optional; defaults to sessions.active_run_id
  + client_msg_id   optional
  - thread_id       legacy-only
  Response: unchanged SSE, now ALSO teed to the fan-out hub (fixes P13).

POST /cancel
  Accepts {run_id} or legacy {thread_id}. Ownership enforced (fixes P9).

POST /resume
  Unchanged shape. Authenticated by the SERVICE credential, not a user identity (§7.4).
  The owning user_id is read from the run row, not from the body.

GET /stream/{thread_id}?after_seq=N
  Ownership enforced (fixes P8). `after_seq` replays missed frames from run_events (fixes P12).
  Omitting after_seq preserves today's live-only behaviour exactly.
```

**Event vocabulary is unchanged.** `RUN_STARTED`, `REASONING`, `TOOL_CALL_START`,
`TOOL_CALL_RESULT`, `TOOL_CALL_END`, `VERIFICATION_STEP`, `DECISION`, `BEST_CURRENT_READ`, `ERROR`,
`RUN_FINISHED` keep their names, ordering, and payload semantics. The only change is additive fields
(`session_id`, `run_id`) on `RUN_STARTED`. `thread_id` continues to be stamped on every payload.

### 5.3 Proxy tier changes (`frontend/src/app/api/deepquant/[...path]/route.ts`)

- `isAgentPath` extended with `sessions` and `runs`, so the session surface is behind the same
  `deepseekGlm` deployment gate as the rest of the agent. `/options/snapshot` stays ungated.
- `isStreamingPath` unchanged.
- New: every request to an agent path gets a server-minted identity header (§7). A request whose
  identity cannot be resolved gets `401` from the route handler and **never reaches upstream**.

### 5.4 Watcher compatibility (hard requirement)

`tool-server/src/main.rs::post_resume` POSTs `{thread_id, triggered_candle, trigger_kind,
heartbeat_seq?, user_id?}` to `/resume`, treats 2xx as resumable / 4xx as ended, and drains the SSE
body. **None of that changes.** The only addition is that tool-server must send the service
credential header; that is one header on one existing request. `heartbeat_seq` continues to be
silently dropped by Pydantic, as today.

### 5.5 Repository abstraction

`session_store.py` exposes only intent-shaped functions — `create_session`, `list_sessions`,
`get_session_for_user`, `update_session`, `archive_session`, `create_run`, `append_run_events`,
`finalize_run`, `create_message`, `append_message_delta`, `finalize_message`,
`reconcile_stale_runs`, `prune_run_events`. `main.py` never writes SQL. This is what makes the
documented Postgres path a driver swap.

---

## 6. Session lifecycle

```
CREATE   POST /sessions              → server mints session_id; symbol/profile/timeframe from the
                                       caller's current trading context (see §9.4)
OPEN     GET /sessions/{id} + /messages + /runs  → rehydrate; if a run is live, attach to
                                       /stream/{thread_id}?after_seq=<last seen>
SWITCH   activeSessionId := id       → no server call beyond cache reads; other sessions keep streaming
RENAME   PATCH {title}               → optimistic in the client, reconciled on response
ARCHIVE  DELETE (soft)               → status='archived', archived_at set; removed from the tab bar,
                                       still in history
REOPEN   PATCH {status:'active'}
DELETE   DELETE ?hard=true           → app rows scrubbed; compliance chain untouched
```

A run's terminal state never archives its session. Sessions outlive runs.

---

## 7. Authentication & the trust chain

```
Browser
  │   httpOnly cookie pair (access_token / refresh_token), domain=.stratai.live
  │   — JS cannot read it; app.stratai.live receives it on every same-origin request
  ▼
Next route handler  (app/api/deepquant/[...path]/route.ts)   ← THE AUTHENTICATION BOUNDARY
  │   1. read `access_token` from the incoming request's cookies
  │   2. resolve identity: GET {API_BASE_URL}/api/v1/users/me with that cookie forwarded
  │        · server-side only; 30 s in-memory cache keyed by a hash of the token
  │        · no cookie is forwarded anywhere else — _gateway.ts keeps stripping cookie/authorization
  │   3. no identity ⇒ 401 here, upstream never contacted — once FQ_REQUIRE_IDENTITY=1.
  │        Until then (rollout phases 1–10) the request is forwarded without the header, so a
  │        transient /users/me outage cannot take the whole agent surface down. Flipped in T11.1
  │        together with DEEP_QUANT_REQUIRE_IDENTITY.
  │   4. mint X-StratAI-Identity: base64url(payload).base64url(HMAC-SHA256(payload, INTERNAL_IDENTITY_SECRET))
  │        payload = {"sub": <user_id>, "iat": …, "exp": iat + 60}
  ▼
deep-quant  (verify_identity dependency)
  │   verify HMAC (constant-time), check exp, extract sub  → verified_user_id
  │   body `user_id` is IGNORED for every ownership decision
  ▼
session_store   every read/write carries user_id in the WHERE clause
```

### 7.1 Why `/users/me` and not local JWT verification

The signing key lives in the separate `thestratai/auth` deployment and is not available in this
repository. Verifying locally would require that secret to be provisioned into the frontend container.
`GET /api/v1/users/me` already exists, is already the app's own authentication check
(`useAuthStore.checkAuth`), and returns the authoritative user. One cached round trip per 30 s per
session is an acceptable price for not inventing a key distribution scheme.

> **BLOCKED — EXTERNAL DEPENDENCY (optimisation, not a blocker for this migration).**
> To remove the `/users/me` hop, the auth deployment must expose either a JWKS endpoint
> (`GET /.well-known/jwks.json`, RS256) or a documented, rotatable shared verification key. Exact
> contract required: JWT with `sub` = the same user id `/users/me` returns, standard `exp`/`iat`, and
> a stable `kid`. Until that exists, the `/users/me` path is the implementation. **No fake
> verification is written.**

### 7.2 Internal secrets

| Var | Set on | Purpose |
|---|---|---|
| `INTERNAL_IDENTITY_SECRET` | `frontend`, `deep-quant` | HMAC key for the user identity assertion |
| `INTERNAL_SERVICE_SECRET` | `tool-server`, `deep-quant` | HMAC key for the watcher's `/resume` |

Both are required in production. `deep-quant` **refuses to start** when
`DEEP_QUANT_REQUIRE_IDENTITY=1` and `INTERNAL_IDENTITY_SECRET` is unset — a session store guarded by
an absent secret is worse than no session store. In local dev both default to unset and
`DEEP_QUANT_REQUIRE_IDENTITY=0` allows a body `user_id`, which is exactly today's behaviour.

### 7.3 What each route enforces

| Route | Check |
|---|---|
| `/sessions*`, `/runs/*` | verified identity; `WHERE user_id = sub`; `404` on miss |
| `POST /run` | verified identity; `session.user_id == sub` |
| `POST /qa` | verified identity; `session.user_id == sub`; `context_run_id.session_id == session_id` |
| `POST /cancel` | verified identity; `run.user_id == sub` |
| `GET /stream/{thread_id}` | verified identity; `run.user_id == sub` |
| `POST /resume` | **service** credential; owning user resolved from the run row |
| `GET /options/snapshot`, `/events/calendar` | unchanged, no identity (not user data) |

### 7.4 Why `/resume` is different

The watcher is a headless Rust service with no user session. It cannot present a user identity, and
it must be able to resume any thread it was asked to watch. Giving it a *service* credential and
resolving the owning user from `runs.user_id` is the only design that keeps the watcher working while
still attributing the resumed run to the right owner. Treating tool-server as a user would be the fake
authentication this migration is forbidden from writing.

---

## 8. Streaming: durability, idempotency, honesty

### 8.1 Write path

```
POST /run
 ├─ tx: create run(status='running', thread_id=<server-minted>, snapshot symbol/tf/profile)
 │      create message(role=user,      kind=analysis_request, status=complete)
 │      create message(role=assistant, kind=analysis_answer,  status=streaming, run_id=…)
 │      session.active_run_id := run_id ; session.updated_at := now
 └─ stream:  for each frame
        append_run_events(run_id, seq, event, payload)      ← batched: ≤25 frames or ≤250 ms
        REASONING/DECISION content folded into the assistant message's content
        runs.last_event_at, runs.last_seq updated with the batch
    terminal frame:
        finalize_run(terminal_status)  — set-once
        finalize_message(status = complete | cancelled | error)
```

Batching is what makes this affordable: a REASONING stream is many small frames, and one transaction
per token would be the only way to make SQLite the wrong choice. A batch is flushed on size, on time,
and unconditionally before any terminal frame — so the terminal state is never ahead of the transcript.

### 8.2 Idempotency

| Hazard | Mechanism |
|---|---|
| Duplicate frame / replayed reattach | `PRIMARY KEY (run_id, seq)` + `INSERT OR IGNORE` |
| Duplicate `RUN_FINISHED` | `runs.terminal_status` set-once |
| Retried user send | `UNIQUE(session_id, client_msg_id)` |
| Reconnect duplicating messages | client reads `?after_seq=`; server never re-creates a message for an existing `client_msg_id` |

### 8.3 Client disconnect

`event_generator`'s `finally` already guarantees a terminal record for metrics and the interaction log.
It gains one more guarantee: a disconnect finalises the assistant message as `truncated`, with the
partial content preserved. The **run keeps executing** — the generator is closed but the graph's own
work and the fan-out hub continue, exactly as today.

### 8.4 Server restart — the anti-fabrication rule

On startup, `reconcile_stale_runs()` scans `idx_runs_live`:

- a run in `running`/`watching` whose thread has no live producer in this process (there are none, the
  process just started) and whose LangGraph checkpoint reports no pending `next` → `status='truncated'`,
  and its `streaming` assistant message → `status='truncated'`;
- a run whose checkpoint **does** report a pending `next` → left `watching`. It is genuinely resumable,
  because the durable checkpointer (§Phase 2) survived the restart. This is precisely what MemorySaver
  could not do.

A `streaming` row is never rendered as an answer: the read model maps `streaming` with no live
producer to `truncated`. **No restart path can produce a message that claims to be a complete answer.**

### 8.5 SSE is preserved

No transport change. `_proxy.ts` already passes `upstream.body` through with `no-transform` and no
timeout, Caddy already sets `flush_interval -1`, and `relaySse` already handles multi-line frames per
spec. There is no technical reason to replace any of it.

---

## 9. Frontend state model

### 9.1 Ownership

| Concern | Owner |
|---|---|
| Persistent sessions / messages / runs | **server**, cached by TanStack Query |
| Active session id | `useSessionStore.activeSessionId` (+ the URL) |
| In-flight stream buffer, keyed by `session_id` | `useSessionStore.streams[sessionId]` |
| Per-session ephemeral UI (mode, draft, verification form) | `useSessionStore.ui[sessionId]` |
| Reducer + trade-plan extraction | `useQuantStore` (`applyStreamEvent`, `extractFinalTrade`, `mergeFinalPlan`) — unchanged |
| Consensus / sentiment / multi-TF patterns | `useQuantStore` — unchanged |
| Chart symbol / timeframe / profile | `useTradeStore`, as a **projection** of the active session (§9.4) |

`useQuantStore` is **not** rewritten. It loses `sessionsByKey`, the flat mirror, `activeViewKey`,
`_streamingKey`, `_threadToKey`, `askQuestion`, `clearQa`, and `activateSymbolSession`; it keeps the
reducer, the extractors, and the three unrelated slices. That is a subtraction of ~600 lines, not a
rewrite of 1900.

### 9.2 Deriving state — no flat mirror

```ts
const currentSession = useSessionStore(selectCurrentSession);   // sessions[activeSessionId]
const stream        = useSessionStore(selectCurrentStream);      // streams[activeSessionId]
```

There is exactly one mutable copy of a session's live state, keyed by its opaque id. Components read
through selectors. `activeSessionId` is the only "which one" state in the system.

### 9.3 Event routing — the hard invariant

```ts
// thread_id → run_id → session_id, resolved from the map populated at run creation.
// There is NO fallback to the active session. An unroutable frame is dropped and counted.
const sessionId = state.threadToSession[frame.data?.thread_id ?? ''];
if (!sessionId) { recordUnroutableFrame(frame); return; }
```

Deleting the `_streamingKey || activeViewKey` fallback is the single change that makes multi-tab safe.
It is safe to delete because the backend stamps `thread_id` on **every** payload (`main.py:550-551`),
including Q&A frames, which the current client simply ignores. Q&A follows the same path — the
per-question closure and the global `qaStatus` lock both go away, replaced by per-session streaming
state.

### 9.4 Symbol / timeframe ownership

**The active session owns its trading context. The global chart is a projection of it.**

- A run reads `symbol`/`timeframe`/`profile` from the **session row**, never from `useTradeStore` at
  call time. This is what makes "Session A's run executed with Session B's timeframe" structurally
  impossible rather than merely unlikely.
- Switching tab → `setSelectedSymbol(session.symbol)`, `setActiveTimeframe(session.timeframe)`,
  `setActiveProfile(session.profile)`. One-way, session → chart.
- Changing the chart **timeframe** while a session is active → `PATCH /sessions/{id} {timeframe}` for
  **that session only**.
- Changing the chart **symbol** while a session is active does **not** mutate the session (symbol is
  immutable). The workspace offers "New session for TCS". No silent cross-session mutation is
  possible.

### 9.5 Server-state cache → **introduce TanStack Query v5**, scoped

`hooks/useApi.ts` has no shared cache, no invalidation, and no request dedup. Session list, session
detail, paginated messages, runs, optimistic rename, and archive-with-rollback are precisely the
problem TanStack Query solves; hand-rolling them would be *more* code than the dependency and would
be the "another uncontrolled custom caching system" the requirements forbid.

Scope discipline: one exact-pinned dependency, a `QueryClientProvider` in the client shell, and query
keys only under `['fq', …]`. Existing `useApi` hooks (`useCredit`, `useUserProfile`,
`useBillingHistory`) are **not** migrated.

### 9.6 URL

Canonical route: **`/find-trade/session/[sessionId]`** — a real App Router route rendering the
chat-first workspace (Phase 14). Refresh → the route param drives
`GET /sessions/{id}` + `/messages` + `/runs` → transcript restored; a live run reattaches to
`/stream/{thread_id}?after_seq=<last>`. Unknown / deleted / not-owned → `404` from the API → a
not-found state offering "start a new session", never a blank panel.

The existing right-sidebar `DeepQuantPanel` keeps working as a compact view of the same active
session, with an affordance into the route. It is not deleted in this migration.

> Per `frontend/AGENTS.md`: the routing/params/server-component conventions for this Next version must
> be read from `node_modules/next/dist/docs/` before the route is written. Do not assume App Router
> conventions from memory.

---

## 10. Deployment & durability

Without this section the persistence claim is false, so it ships in Phase 1, not at the end.

```yaml
volumes:
  deep_quant_data:            # NEW

  deep-quant:
    volumes:
      - deep_quant_data:/data
    environment:
      SESSIONS_DB_PATH:        /data/sessions.db          # NEW store
      LANGGRAPH_CHECKPOINT_DB: /data/checkpoints.db       # NEW durable checkpointer
      COMPLIANCE_DB_PATH:      /data/compliance.db        # FIXES P11 — the audit chain was ephemeral
      JOURNAL_DB_PATH:         /data/trade_journal.db     # FIXES P11
      TELEMETRY_DB_PATH:       /data/telemetry.db         # FIXES P11
      INTERNAL_IDENTITY_SECRET: ${INTERNAL_IDENTITY_SECRET:?required}
      INTERNAL_SERVICE_SECRET:  ${INTERNAL_SERVICE_SECRET:?required}
      DEEP_QUANT_REQUIRE_IDENTITY: "1"
```

`mem_limit` rises 512 m → **576 m** for the checkpointer's aiosqlite connection and the frame-batch
buffers. The +64 m is precautionary, not measured — WAL SQLite with short-lived connections adds little
RSS — and the compose comment records how to measure it and pull it back. The 8 GB box is already at
~6.75 GB committed, so headroom here is not free.

**Horizontal scaling.** `_CANCELLED`, `_SUBSCRIBERS`, the SQLite writer, and the SQLite checkpointer
are all process-local. deep-quant is therefore **explicitly single-replica**, enforced rather than
assumed: startup aborts unless `DEEP_QUANT_ALLOW_MULTI_REPLICA=1` is set *and* both a Postgres DSN and
a Redis URL for the subscriber hub are configured. A database-backed session system silently deployed
behind two replicas that cannot resume or cancel each other's runs is the failure this guard exists to
prevent.

**Backups.** `/data` is one directory with WAL-mode SQLite files. Backup = `sqlite3 <db> ".backup"`
per file on a schedule; a plain file copy of a WAL database is not a valid backup and must not be used.

---

## 11. Compliance separation

`interaction_log` and `reco_store` remain append-only, hash-chained, trigger-protected audit records
in `compliance.db`. They are **not** the chat store and are not read to render a conversation.

A chat event that must also be audited produces **two independent writes**:

- compliance write raises on failure (by design — `interaction_log`'s module contract);
- session-store write is wrapped so a failure degrades chat but never corrupts the chain;
- a compliance failure never fabricates a successful chat state, and a chat failure never writes a
  compliance row that claims something happened.

The one behavioural improvement: because `COMPLIANCE_DB_PATH` now points at a volume, the chain
actually survives a redeploy. It previously did not.

---

## 12. Migration phases (each compiles, tests, and is independently revertible)

| # | Phase | Ships | Rollback |
|---|---|---|---|
| 0 | ✅ This document + the migration plan | docs only | delete docs |
| 1 | ✅ **Durability + identity foundation** — volume, DB path env, HMAC identity mint/verify across Python/TypeScript/Rust, Next authentication boundary, service credential, startup state report | no behaviour change with flags off | unset flags; remove volume |
| 2 | ✅ **Durable LangGraph checkpointer** — `AsyncSqliteSaver` on `/data` via a FastAPI lifespan, `reconcile_stale_runs` hook | unset `LANGGRAPH_CHECKPOINT_DB` — the module-scope `MemorySaver` default takes over with no code change | env var |
| 3 | ✅ **`session_store.py` + schema + tests** — sessions/runs/messages/run_events, ownership-scoped reads, keyset pagination, set-once terminal transitions, `reconcile_stale_runs`, retention pruner. No route calls it yet | dead code except the startup `ensure_store`; no request path touches it | delete module |
| 4 | ✅ **Session/run/message API + ownership on the agent routes** — eight authenticated CRUD routes; `/stream` and `/cancel` ownership (closing P8/P9); `/qa` grounded by `session_id` + `context_run_id` and teed to the hub (closing P13); `/resume` owner read from the run row; server-minted `thread_id` | routes 404 when off; agent routes keep their legacy body path | flag |
| 5 | ✅ **Streaming persistence + `/stream?after_seq`** — batched write-through from inside the SSE generator, terminal-flush ordering, disconnect→`truncated`, paused→`watching`, replay closing the P12 frame-loss window, retention pruner | flag; SSE bytes asserted identical on and off | flag |
| 6 | ✅ **Frontend session store + routing fix** — `useSessionStore` keyed by opaque id, `applyFrame` with NO active-session fallback, per-session UI state, derived selectors, session-aware run/QA/cancel adapters, `?after_seq=` reattach | `NEXT_PUBLIC_FQ_MULTI_SESSION=false` keeps the legacy path verbatim | flag (rebuild) |
| 7 | 🔶 **Server becomes source of truth** — ✅ rehydration (replay stored frames through the live reducer + reconcile against the run's stored status), ✅ TanStack Query scoped to `['fq', …]`, ⬜ remove the flat mirror / switch components to selectors (T7.3) | revert | git |
| 8 | **Session tabs** | feature-flagged UI | flag |
| 9 | **Session history** | flag | flag |
| 10 | **Chat promotion + `/find-trade/session/[id]` route** | new route; sidebar unchanged | delete route |
| 11 | **Remove legacy** — client thread-id minting, dead code (§13), legacy `/run` path | git | git |

**Phase 8 is not reachable before Phase 6.** The tab UI is safe only once the event-routing fallback
is gone.

---

## 13. Dead code in scope (removed in Phase 11 only)

`agentStatus` state + its `agent_status` listener and the unrendered `LoadingState` import
(`DeepQuantPanel.tsx:103,133-140,15`); `parseInlineMarkdown` + `AnswerText` leftovers in
`QaMessages.tsx:52-92`; `clearQa` (zero call sites); the `hydrateLegacyAgentBridge` path feeding
`agentChatLog`/`finalTradePlan`, which its own comment says nothing renders
(`useTradeStore.ts:479-506`); `run_deep_quant_analysis` (no caller); unused imports
(`useChartUIStore`, `Cpu`, `Plus`, `Mic`). Nothing outside this feature is touched.

---

## 14. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Volume introduction changes DB paths → existing `compliance.db` in a container layer is orphaned | Medium | Phase 1 startup logs both paths and row counts; the old file is ephemeral anyway (P11), so there is nothing durable to lose — but this is stated, not assumed |
| Two sources of truth drift (checkpoint vs message store) | High | §2 boundary: `runs.thread_id` is the only join, written once; the checkpoint is never read for user-visible history |
| **The durable checkpoint becomes a new deserialisation surface.** `MemorySaver` held state in the heap; a file on disk can be tampered with, and checkpoint deserialisation can reach arbitrary types | High | `LANGGRAPH_STRICT_MSGPACK=true` (or an explicit `allowed_msgpack_modules` allowlist) is set in the same commit that introduces the file — see the migration plan T2.1. The volume is not host-mounted and `/data` is reachable only from inside the container |
| The durable checkpointer cannot be built where `MemorySaver` sits, and the obvious workaround is a runtime error | **Resolved** | Measured, not assumed: `AsyncSqliteSaver.__init__` calls `asyncio.get_running_loop()` (it binds to the loop it serves), so module scope raises `RuntimeError: no running event loop`; and the synchronous `SqliteSaver` — which *can* be built at import and *does* expose `aget_tuple` — raises `NotImplementedError` from it, which is fatal because this graph only runs via `astream`. Implemented as a FastAPI lifespan that recompiles and rebinds `graph_module.graph`; `main.py` reaches the graph by attribute so the rebind is visible, pinned by a test |
| SQLite write contention under concurrent runs | Medium | batched writes, WAL, `busy_timeout`; measured in Phase 5 before the flag flips |
| Watcher breakage | High | `/resume` shape unchanged; service credential is one added header; Phase 5 has an explicit resume+reattach regression test |
| `/users/me` latency on every agent call | Medium | 30 s server-side cache keyed by token hash; a cache miss adds one internal round trip |
| Identity secret missing in production | High | deep-quant refuses to start (§7.2) |
| Silent multi-replica deploy | High | startup guard (§10) |
| E2E cannot cover a real LLM run in CI | Accepted | CI e2e runs against a stubbed agent graph; the LLM path is verified manually. Stated, not papered over. |

---

## 15. Blocked / out of scope

- **BLOCKED — EXTERNAL DEPENDENCY:** JWKS or a shared verification key from the auth deployment, to
  remove the `/users/me` hop (§7.1). **Not a blocker for this migration** — the `/users/me` path is
  fully implementable here. Exact contract specified in §7.1.
- **BLOCKED — EXTERNAL DEPENDENCY:** `GET /api/v1/internal/entitlement/{user_id}` still does not
  exist, which is why `SKU_ENFORCE` defaults off (`entitlements.py`). Unchanged by this migration and
  not fixed by it.
- **Out of scope:** the quant engine, market-data architecture, QuestDB schemas, the F&O workspace,
  and every other service.

---

## 16. Before vs After

| | Before | After |
|---|---|---|
| Session identity | `RELIANCE::INTRADAY`, collides | `sess_01J…`, opaque, server-minted |
| Same symbol, two timeframes | impossible — second run overwrites the first | independent sessions |
| Survives reload | no | yes |
| Survives backend restart | no (MemorySaver) | yes (durable checkpointer + message store) |
| Survives redeploy | no (no volume — *including the compliance chain*) | yes (named volume) |
| Chat persistence | none | normalised `messages` + `run_events` |
| Truncated vs complete answer | indistinguishable | distinct status, reconciled on startup |
| Q&A routing | closure into whatever is on screen | `thread_id → run_id → session_id` |
| Concurrent Q&A | one process-wide | per session |
| Stream frames with no listener | lost forever | replayable via `?after_seq=` |
| `thread_id` | `thread_${symbol}_${Date.now()}`, client-minted, guessable | server-minted, ownership-checked |
| `user_id` | body field, unverified | HMAC assertion minted after a `/users/me` verification |
| `GET /stream/{id}` | anyone with the id | owner only |
| `POST /cancel` | anyone | owner only |
| Active-session state | `sessionsByKey` + a 12-field flat mirror | `sessions[activeSessionId]` + selectors |
| Event fallback | "route to whatever is active" | no fallback; unroutable frames dropped and counted |
| UI | right-sidebar utility | tabbed, deep-linkable chat workspace |
| E2E in CI | none | a job that actually runs |
