# Find Quant Trade — Multi-Session Migration Plan

> Companion to `find-quant-multi-session-design.md`. That document holds the decisions; this one holds
> the executable tasks. **Nothing here has been implemented yet.**
>
> Every task states: objective · files · changes · dependencies · tests · rollback · acceptance.
> Every phase must compile, pass its tests, and leave FIND / VERIFY / QA / RESUME / CANCEL / WATCH
> working before the next phase starts.

## Verification commands (run after every task, not every phase)

```bash
# Frontend
cd frontend && npx tsc --noEmit                      # must be clean; pre-existing failures are filtered in CI
cd frontend && npx vitest run <touched paths>
cd frontend && npm run build:web                     # REQUIRED for any task touching app/api/** or app/**/page

# Python — NOTE: this workstation has NO agent dependencies installed
#   (verified 2026-09-01: `import langgraph` -> ModuleNotFoundError; local Python is 3.11, CI uses 3.13).
#   A one-time setup is required before any Python task can be verified locally:
cd agents/deep-quant-loop && python -m pip install -r requirements.txt && python -m pip install pytest
cd agents/deep-quant-loop && python -m pytest -q     # establish the real baseline count FIRST

# Rust (only T1.5 touches Rust)
cd tool-server && cargo check --locked && cargo test --locked
```

## Measured baseline (2026-09-01, git worktree at HEAD `0913090`, no Phase 1 changes)

Measured, not recalled. The Python figure came from a clean `git worktree` at HEAD so it could be
compared against the working tree rather than asserted.

| Check | Result |
|---|---|
| `npx tsc --noEmit` (CI's filter applied) | **clean** — zero errors |
| `npx vitest run` | **138 files / 1042 tests — all passed, 0 failed** (60.4 s) |
| `python -m pytest -q` | **16 failed / 1570 passed / 5 skipped** (11 m 13 s) |

### One genuinely flaky frontend test — pre-existing, and worth fixing separately

`src/components/panels/__tests__/LeftPanel.stockFnoSelection.property.test.tsx` →
`a valid stock option opens F&O with the stock underlying and reset expiry` fails with
**`Error: Test timed out in 5000ms`** in roughly 3 of 5 full-suite runs, and passes every time in
isolation. It is a Hypothesis-style property test rendering `LeftPanel`, and it is not touched by this
migration (no file it imports is modified).

The cause is the default 5 s `testTimeout` against a render-heavy property test competing with 15 vitest
workers — the full suite reports ~490 s of collect time across ~145 files, so under load this one
legitimately exceeds its budget.

**This matters beyond being noise:** it makes the `frontend` CI job non-deterministic in the same way
the NUL-generating Hypothesis tests make the `python-agent` job non-deterministic (see below). Two of
the three CI jobs can currently go red without a regression.

Suggested fix, outside this migration's scope: give that one test an explicit generous timeout
(`it(..., 20_000)`) or reduce its property run count. Do **not** raise the global `testTimeout`, which
would mask genuinely hanging tests — `/stream`'s unbounded SSE relay already demonstrated what that
looks like.

### The frontend has NO known-failing tests

**`CLAUDE.md` §12 is out of date.** It lists 9 known-failing tests across
`fno/__tests__/selectors.bounding.property.test.ts`, `chart/__tests__/SplitChartContainer.test.tsx`,
`layout/__tests__/TerminalLayout.modeSelector.test.tsx` and
`panels/__tests__/LeftPanel.search.test.tsx`. All pass on this tree. **The frontend baseline is zero
failures — treat any red test as introduced by this work. There is no exemption list.** (`CLAUDE.md`
to be corrected in T11.2.)

### The Python suite is NOT a reliable gate, and the reason is a test defect

The 16 failures are pre-existing — verified by running them on the clean worktree — but the more
important finding is that **the failing SET changes from run to run**, so `python -m pytest -q` is red
or green at random. That means the `python-agent` CI job cannot currently distinguish a regression
from a coin flip, which makes it as weak a safety net as the absent E2E job (P16).

Two independent pre-existing causes, both reproduced at HEAD:

1. **Hypothesis writes NUL bytes into `os.environ`.** The env-driven config-resolution property tests
   (`test_*_config_*_properties.py`, `test_session_config_ordering_properties.py`,
   `test_attribution_*`, `test_options_*`, `test_rs_config_*`, `test_telemetry_config_*`) draw
   arbitrary text and set it as an environment variable. When a draw contains `'\x00'`, CPython raises
   `ValueError: embedded null character` from `<frozen os>:685`. Whether a given run draws a NUL
   depends on the Hypothesis database and seed, which is exactly why the set is unstable. Verified on
   the clean worktree:
   `pytest tests/test_session_config_ordering_properties.py` → `ValueError: embedded null character`.
2. **`test_trade_qa.py` / `test_qa_always_answers_bug.py` fail on the clean worktree in isolation**
   (6 failures, e.g. `assert 'get_candles' in []`). Not order-dependent — genuinely failing at HEAD.

Comparison of the two full runs, which is what rules this work out as the cause:

| | clean worktree @ `0913090` | working tree (Phase 1 in progress) |
|---|---|---|
| failed | 16 | 19 |
| passed | 1570 | 1681 |
| `test_session_config_ordering_properties` | **failed** | passed |
| `test_debate_config_resolution_properties` | passed | **failed** |
| `test_rs_config_properties` | passed | **failed** |
| `test_of_config_pressure_ordering_properties` | passed | **failed** |

Failures appearing and disappearing in *both* directions is not a change signature. The 15 tests
common to both runs are the stable pre-existing set.

**Neither defect is in this migration's scope**, and neither is fixed by it. Both are recorded here
because "the Python suite is green" is not currently a claim anyone can make, so every Python task in
this plan is verified by **running its own test file(s) explicitly** and comparing the full-suite
result against this baseline table — never by reading a bare pass/fail from the whole suite.

Suggested follow-up, outside this migration: constrain those Hypothesis strategies to text valid in an
environment variable (`st.text(alphabet=...)` excluding NUL), which would make the suite deterministic
and the CI job meaningful again.

## Flags — the whole rollout is gated

| Flag | Default | Controls |
|---|---|---|
| `DEEP_QUANT_REQUIRE_IDENTITY` | `0` | reject requests without a verified identity assertion |
| `DEEP_QUANT_SESSIONS_ENABLED` | `0` | mount `/sessions`, `/runs` routes |
| `DEEP_QUANT_REQUIRE_SESSION` | `0` | `POST /run` requires `session_id` (turns off the legacy path) |
| `DEEP_QUANT_PERSIST_STREAM` | `0` | write-through of run events / messages |
| `FQ_REQUIRE_IDENTITY` | `0` | Next tier returns `401` when identity cannot be resolved (vs. mint-if-possible) |
| `NEXT_PUBLIC_FQ_MULTI_SESSION` | `0` | tabs + history + workspace route in the UI |

Rollback for phases 1–5 and 8–10 is "flip the flag", which is why they are ordered this way.

---

# PHASE 1 — Durability + identity foundation

No user-visible behaviour changes in this phase. With every flag off, the system behaves exactly as
today.

## T1.1 — Persistent volume and DB path environment

**Objective.** Make `/data` durable and move all four SQLite files onto it. This also fixes P11: the
compliance hash chain is currently destroyed on every redeploy.

**Files.** `docker-compose.prod.yml`, `docker-compose.yml`, `docker-compose.8gb.yml`,
`agents/deep-quant-loop/Dockerfile`, `.env.example`, `docs/DEPLOYMENT.md`.

**Changes.**
- Add top-level `volumes: deep_quant_data:`.
- `deep-quant` service: `volumes: [deep_quant_data:/data]`; `mem_limit` 512 m → 640 m; add
  `COMPLIANCE_DB_PATH=/data/compliance.db`, `JOURNAL_DB_PATH=/data/trade_journal.db`,
  `TELEMETRY_DB_PATH=/data/telemetry.db`, `SESSIONS_DB_PATH=/data/sessions.db`,
  `LANGGRAPH_CHECKPOINT_DB=/data/checkpoints.db`.
- Dockerfile: `RUN mkdir -p /data`; document `/data` as the state directory. Do **not** `VOLUME /data`
  (compose owns it).
- `.env.example`: document the two internal secrets from T1.3 and `/data`.
- `docs/DEPLOYMENT.md`: add the `.backup`-based backup procedure; state that a file copy of a WAL
  SQLite DB is not a valid backup.

**Dependencies.** none.

**Tests.** `test_db_paths_env.py` — `hashchain.db_path()` honours `COMPLIANCE_DB_PATH` per call (it
already reads it per call, so this is a regression pin); `journal.JOURNAL_DB_PATH` honours the env at
import; `telemetry` config resolution honours `TELEMETRY_DB_PATH`.

**Rollback.** Remove the `volumes:` entries. The service falls back to writing in `/app`, i.e. today's
(broken) behaviour.

**Acceptance.**
- `docker compose -f docker-compose.prod.yml config` resolves with no error.
- Restarting the container preserves `/data/compliance.db` and its row count.
- `python -m pytest -q` green (the conftest `COMPLIANCE_DB_PATH` fixture must still isolate).

## T1.2 — Startup state report

**Objective.** Make a mis-provisioned volume loud instead of silent.

**Files.** `agents/deep-quant-loop/main.py`.

**Changes.** In the existing `_ensure_compliance_stores` startup path, log each resolved DB path, its
`os.path.exists`, and its row count. Log `WARN: <path> is not on a mounted volume — data will be lost
on redeploy` when the resolved path is not under a directory listed in `DEEP_QUANT_STATE_DIRS`
(default `/data`).

**Dependencies.** T1.1.

**Tests.** `test_startup_state_report.py` — the warning fires for `/app/compliance.db` and does not
fire for `/data/compliance.db`; the report never raises when a DB is missing.

**Rollback.** Delete the function call.

**Acceptance.** Production logs list four paths under `/data` with no warning.

## T1.3 — `internal_identity.py` — mint/verify the identity assertion

**Objective.** A verifiable, short-lived, server-minted user identity that deep-quant can trust.

**Files.** NEW `agents/deep-quant-loop/internal_identity.py`; `main.py` (dependency wiring only).

**Changes.**
- `sign_identity(user_id, ttl=60) -> str` and `verify_identity_token(token) -> str` — payload
  `{"sub","iat","exp"}`, `base64url(json).base64url(hmac_sha256(json, secret))`, compared with
  `hmac.compare_digest`. Reject: bad signature, expired, `iat` in the future beyond 30 s skew, missing
  `sub`, oversized token.
- `sign_service(...)` / `verify_service_token(...)` — same primitive, `INTERNAL_SERVICE_SECRET`,
  `{"svc","iat","exp"}`.
- FastAPI dependency `require_user(request) -> str`: read `X-StratAI-Identity`, verify, return `sub`.
  When `DEEP_QUANT_REQUIRE_IDENTITY=0`, fall back to the body `user_id` and log
  `WARN: identity not enforced` **once per process**.
- FastAPI dependency `require_service(request)` for `/resume`.
- Refuse to start (raise at import of `main`) when `DEEP_QUANT_REQUIRE_IDENTITY=1` and
  `INTERNAL_IDENTITY_SECRET` is unset or shorter than 32 bytes.

**Dependencies.** none (stdlib `hmac`/`hashlib`/`base64`/`json` only — no new pip dependency).

**Tests.** `test_internal_identity.py` — round trip; tampered payload rejected; tampered signature
rejected; expired rejected; future `iat` rejected; wrong secret rejected; empty/garbage rejected;
`require_user` returns the body `user_id` when enforcement is off and raises 401 when on with no
header; a **Hypothesis** property that no random string verifies.

**Rollback.** Delete the module; drop the dependency from the routes.

**Acceptance.** Tests green; nothing else changes behaviour while the flag is `0`.

## T1.4 — Next tier: resolve identity and mint the assertion

**Objective.** Move the authentication boundary into the Next route handler without weakening the
existing cookie-stripping property.

**Files.** NEW `frontend/src/app/api/_identity.ts`; `frontend/src/app/api/deepquant/[...path]/route.ts`.
`_gateway.ts` is **not** relaxed — `cookie` and `authorization` stay stripped.

**Changes.**
- `_identity.ts` (server-only): `resolveUserId(req)` reads the `access_token` cookie, calls
  `${API_BASE_URL}${API_V1_PREFIX}/users/me` with `Cookie: access_token=…`, returns `data.id`.
  30 s in-memory cache keyed by `sha256(token)` — never by the token itself. Returns `null` on any
  failure (no cookie, non-2xx, malformed, unreachable). `mintIdentityHeader(userId)` implements the
  same HMAC as T1.3 using `INTERNAL_IDENTITY_SECRET` (unprefixed env, server-only).
- Route handler: for `isAgentPath(segments)`, resolve the identity and, when resolved, pass
  `{'X-StratAI-Identity': token}` through a new `extraHeaders` option on `proxyRequest`.
- **Two-stage rollout — do not start blocking in this task.** A new unprefixed switch
  `FQ_REQUIRE_IDENTITY` (default `0`) controls whether an unresolved identity is a `401`:
  - `0` (Phase 1 → 10): mint the header when the identity resolves, forward without it when it does
    not. Behaviour is byte-identical to today, and a transient `/users/me` outage cannot take the
    whole agent surface down — which it would if this task started returning `401` immediately.
  - `1` (flipped in T11.1, alongside `DEEP_QUANT_REQUIRE_IDENTITY=1`): an unresolved identity returns
    `401 {error: 'authentication required'}` **without contacting upstream**.
  The switch reads via a literal `process.env.FQ_REQUIRE_IDENTITY` member expression, matching the
  convention in `_featureSwitches.ts` (`readSwitchEnv`) — deliberately not a computed lookup.
- `_proxy.ts`: add `extraHeaders?: Record<string,string>` to `ProxyOptions`, applied after
  `forwardHeaders` so it cannot be overwritten by a client-supplied header of the same name. Strip any
  client-supplied `x-stratai-identity` / `x-stratai-service` in `_gateway.ts`'s
  `STRIPPED_REQUEST_HEADERS` — a browser must never be able to inject one.

**Dependencies.** T1.3 (same HMAC format).

**Tests.**
- `frontend/src/app/api/__tests__/identity.test.ts` — cache hit/miss; `null` on no cookie, on 401, on
  network error; the header format matches the Python verifier's expectation (fixture vector shared
  with the Python test).
- `deepquant.route.test.ts` — an agent path with no cookie ⇒ 401 and `fetch` never called;
  `/options/snapshot` with no cookie ⇒ still proxied; a client-supplied `x-stratai-identity` is
  stripped.

**Rollback.** Remove the identity block from `handle()`; the route reverts to today's pass-through.

**Acceptance.** `npx tsc --noEmit` clean; `npm run build:web` succeeds; with
`DEEP_QUANT_REQUIRE_IDENTITY=0` the whole existing flow still works end to end.

## T1.5 — tool-server sends the service credential

**Objective.** Keep the watcher working once `/resume` requires a credential.

**Files.** `tool-server/src/main.rs` (`post_resume`), tool-server env in `docker-compose.prod.yml`.

**Changes.** Read `INTERNAL_SERVICE_SECRET`; when set, attach `X-StratAI-Service: <hmac assertion>`
to the `/resume` POST. When unset, send nothing (local dev, matching
`DEEP_QUANT_REQUIRE_IDENTITY=0`). No change to the body, the 2xx/4xx interpretation, or the
drain-and-discard of the response.

**Dependencies.** T1.3.

**Tests.** Rust unit test for the assertion encoding against a fixture vector shared with
`test_internal_identity.py`; existing tool-server tests must stay green.

**Rollback.** Remove the header.

**Acceptance.** `cargo check --locked && cargo test --locked` green in `tool-server`.

**⚠ `--locked` needs `Cargo.lock` updated in the same commit.** Adding a direct dependency changes the
`tool-server` package's `dependencies` list in the lockfile, and `cargo check --locked` fails on any
Cargo.toml/Cargo.lock disagreement — so CI would go red on a lockfile that was merely not regenerated.
`hmac`, `sha2` and `base64` are already present in `Cargo.lock` as transitive dependencies of the
rustls/sqlx stack (0.12.1 / 0.10.9 / 0.22.1), so promoting them costs no new compilation and no new
supply-chain surface, and the lockfile edit is only the addition of three names to that list.

---

# PHASE 2 — Durable LangGraph checkpointer

## T2.1 — Replace `MemorySaver` with `AsyncSqliteSaver`

**Objective.** Thread state, Q&A grounding and paused runs survive a restart.

**Files.** `agents/deep-quant-loop/requirements.txt`, `graph.py` (only the checkpointer construction
at `:75` / `:6111` / `:6114`), `main.py` (lifespan).

**RESOLVED by measurement (2026-09-01). Three facts, two of which killed the simpler designs.**

1. **Versions.** A fresh install resolves `langgraph 1.2.11` / `langgraph-checkpoint 4.2.0`, so the
   matching checkpointer is the **3.x** line → `langgraph-checkpoint-sqlite==3.1.1` (brings
   `aiosqlite 0.22.1` transitively; nothing imports it directly, so it is not listed separately).
   The `langgraph>=0.2.0` floor was also **raised to `>=1.0,<2`** — an exact 3.x pin beside a 0.2 floor
   let a future resolver pick a never-run combination, and that failure would surface as silently lost
   thread state.
2. **`AsyncSqliteSaver` cannot be constructed at module scope.** Not because
   `from_conn_string` is an async context manager (it is: `-> AsyncIterator[AsyncSqliteSaver]`), but
   because `__init__` itself calls `asyncio.get_running_loop()` — it binds to the loop it will serve.
   Measured: `RuntimeError: no running event loop`. So the lifespan is mandatory, and with it the
   `graph_module.graph` rebinding.
3. **The synchronous `SqliteSaver` fallback does not exist.** It *can* be built at import time and it
   *does* expose `aget_tuple`, so it looks viable — but that method raises
   `NotImplementedError("The SqliteSaver does not support async methods")`, and this graph is driven
   exclusively through `astream`. Measured by running it: `AsyncPregelLoop.__aenter__` → `aget_tuple`
   → `NotImplementedError` on the first request. **The fallback described in an earlier draft of this
   plan was wrong and has been removed.**

Durability was then verified directly rather than assumed: two separate event loops over one file
(which is what a restart is, since the saver binds to a loop), a run parked at `interrupt()`, and the
second "process" observing `next=('waiter',)` and resuming with `Command(resume=...)` to completion.

**Changes.**
- `requirements.txt`: add `langgraph-checkpoint-sqlite` and `aiosqlite` at **exact** pins resolved per
  the note above (the file otherwise uses floors; a checkpointer schema change is a data-migration
  event, so this one is pinned).
- `main.py`: change `from graph import graph, set_run_llm_credentials` to
  `import graph as graph_module` + `from graph import set_run_llm_credentials`, and use
  `graph_module.graph` at the ~6 call sites (`graph.astream`, `graph.get_state`). This is the change
  that makes a lifespan-built graph possible at all — a module-level `from graph import graph` binding
  would go stale on recompile. Mechanical, no behaviour change on its own; land it as its own commit so
  it can be reviewed separately from the checkpointer swap.
- `graph.py`: expose `workflow` and a `compile_with(checkpointer)` helper; keep
  `graph = workflow.compile(checkpointer=MemorySaver())` as the module-scope default so importing
  `graph.py` still yields a working graph (tests and every existing import path are unaffected).
- `main.py` lifespan: when `LANGGRAPH_CHECKPOINT_DB` is set, enter
  `AsyncSqliteSaver.from_conn_string(path)` for the app's lifetime, `PRAGMA journal_mode=WAL`, and
  rebind `graph_module.graph = graph_module.compile_with(saver)`. Exit the context manager on shutdown.
- Set `LANGGRAPH_STRICT_MSGPACK=true` (or pass an explicit `allowed_msgpack_modules` allowlist) in
  `docker-compose.prod.yml`. The reference docs flag this as important: it restricts checkpoint
  deserialisation to known-safe types so a compromised checkpoint DB cannot execute code
  ([reference](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/)). This matters far
  more now than with `MemorySaver`, because the checkpoint becomes a **file on disk**.
  *Content rephrased for compliance with licensing restrictions.*
- Guard the import: a missing package logs a loud `WARN` and leaves the `MemorySaver` default, never a
  hard crash on an existing deployment.

**No fallback exists.** See resolved fact 3 — the synchronous saver is a runtime error on the first
request, not a degraded mode. The only choice is async-in-a-lifespan.

**Dependencies.** T1.1 (the volume).

**Knock-on:** `tests/test_interaction_log.py` patched `main.graph` in four places and errored with
`AttributeError: module 'main' has no attribute 'graph'` after the import change. Repointed to
`main.graph_module.graph` — the object `main` actually calls — with **no assertion changed**. This is
exactly the regression the "run the existing route tests" step exists to catch.

**Tests.** `test_durable_checkpointer.py` — with `LANGGRAPH_CHECKPOINT_DB` at a tmp path and a stub
graph: write a thread, tear the saver down, reopen, `get_state` returns the same values;
`MemorySaver` is used when the env is unset; a missing package falls back with a warning; the
`graph_module.graph` rebinding is visible to `main.py`'s call sites (assert identity after lifespan
startup, which is the exact bug the import change prevents).

**Rollback.** Unset `LANGGRAPH_CHECKPOINT_DB` — the module-scope `MemorySaver` default takes over with
no code change.

**Acceptance.** `python -m pytest -q` green. Manual: start a FIND that parks at
`watch_price_condition`, restart the container, `POST /resume` returns 2xx instead of 400.

## T2.2 — `reconcile_stale_runs` skeleton

**Objective.** Establish the anti-fabrication startup pass before anything depends on it.

**Files.** `main.py` (startup), NEW `session_store.py` stub (real implementation in T3.1).

**Changes.** On startup, for each run the store reports as `running`/`watching`, consult
`graph.get_state(config).next`: pending ⇒ leave `watching`; not pending ⇒ mark `truncated`. In this
phase the store is empty, so the pass is a no-op — it ships now so Phase 5 cannot forget it.

**Dependencies.** T2.1.

**Sequencing.** Land this **after** T3.1. It is listed under Phase 2 because restart honesty is a
Phase-2 correctness guarantee, but it cannot do real work until the store exists — the stub keeps the
two commits independent so Phase 2 can be verified and rolled back on its own.

**Tests.** `test_reconcile_stale_runs.py` — a run with a pending checkpoint stays `watching`; one
without becomes `truncated`; its `streaming` assistant message becomes `truncated`; the pass is
idempotent across two invocations.

**Rollback.** Remove the startup call.

**Acceptance.** Tests green; startup logs `reconciled 0 runs` on a fresh volume.

---

# PHASE 3 — `session_store.py`

## T3.1 — Schema, migrations, repository functions

**Objective.** The persistence layer, fully tested, with no caller.

**Files.** NEW `agents/deep-quant-loop/session_store.py`.

**Changes.** Exactly the DDL in design §4. `connect()` mirrors `hashchain.connect()`
(`row_factory=Row`, `timeout`, WAL) and adds `PRAGMA foreign_keys=ON`. `_migrate(conn)` steps
`schema_version` forward; version 1 is the initial schema. `new_id(prefix)` mints
`prefix_<26-char Crockford base32 ULID>` from `os.urandom` + a millisecond timestamp (time-sortable,
opaque, no `uuid` ordering problems).

Functions (all take an explicit `user_id` where ownership applies; **no function reads a session by id
alone**):

```
create_session · list_sessions(cursor,limit,status,q) · get_session_for_user · update_session
archive_session · delete_session(hard)
create_run · get_run_for_user · get_run_by_thread_for_user · list_runs · finalize_run
create_message · append_message_delta · finalize_message · list_messages(after_seq,limit)
append_run_events(batch) · list_run_events(after_seq,limit)
reconcile_stale_runs · prune_run_events
```

**Dependencies.** T1.1.

**Tests.** `test_session_store.py` (unit) + `test_session_store_properties.py` (Hypothesis):

- create → get round trip; unknown id ⇒ `None`; **another user's id ⇒ `None`, not a row**;
- `list_sessions` ordering by `updated_at DESC`, cursor pagination is stable and never skips or
  repeats a row under interleaved writes;
- `PATCH` of `symbol`/`profile` is rejected; `timeframe`/`title` accepted;
- `messages.seq` is dense and gap-free under 100 interleaved appends across two sessions;
- `client_msg_id` re-insert returns the existing row rather than a duplicate;
- `run_events` `(run_id, seq)` re-insert is a no-op;
- `finalize_run` is set-once — a second call with a different status does not change
  `terminal_status`;
- `archive_session` then `list_sessions(status='active')` excludes it; `status='archived'` includes it;
- `delete_session(hard=True)` removes messages and run_events and **leaves `compliance.db`
  byte-identical** (assert the file hash before/after);
- property: for any interleaving of `append_run_events` batches, replaying stored events in `seq` order
  reproduces the input order.

**Rollback.** Delete the module.

**Acceptance.** `python -m pytest -q` green; `session_store` has no import from `main` (it is a leaf).

### Implemented — three bugs the tests found, all of them real

1. **Lossy pagination cursor.** `_encode_cursor` used `f"{updated_at:.6f}"`, which *rounds*.
   A timestamp rounded UP compares as strictly greater than the row it came from, so the keyset
   predicate `updated_at < cursor` matched that row again and it was served on two consecutive pages —
   23 sessions returned 27 rows. Fixed with `repr(float)`, which Python guarantees round-trips exactly
   (`float(repr(x)) == x`). Lossy is not an option for a value used as an exact boundary.
2. **Python and SQLite disagreed about "empty".** `'\x00'.strip()` is truthy in Python (NUL is not
   whitespace), so a NUL `user_id` passed the Python guard — but SQLite's `length('\x00')` is **0**
   (it stops at the C-string terminator), so `CHECK (length(user_id) > 0)` fired and a clean 422
   became an `sqlite3.IntegrityError`/500. Same shape as the non-ASCII crash in T1.3's verifier, found
   the same way. `_text` now strips NULs, so identifier fields reduce to empty and raise `ValueError`
   properly while message content merely loses a character that was never meaningful.
3. **Single-element `IN` clause.** The vocabulary tuples were interpolated into the DDL directly, and
   Python renders a one-element tuple as `('x',)` — a SQLite syntax error. Every tuple happens to have
   ≥2 members today, so this worked right up until someone narrowed one. Replaced with `_sql_in()`,
   which also doubles quotes.

Also added, not in the original task text but required by the schema: `state_paths.ensure_parent_dir`,
shared with the checkpointer. A path under a volume directory that does not exist yet fails with a bare
`sqlite3.OperationalError: unable to open database file`, which reads like a permissions fault and
sends the reader to entirely the wrong place. Measured while wiring this up.

---

# PHASE 4 — Session/run/message API

## T4.1 — Pydantic models and routes

**Objective.** The eight routes in design §5.1, behind `DEEP_QUANT_SESSIONS_ENABLED`.

**Files.** `main.py`, NEW `session_api.py` (an `APIRouter` so `main.py` does not grow another 400
lines).

**Changes.** Router mounted only when the flag is on. Every route depends on `require_user`. `404`
(never `403`) for a non-owned session. `PATCH` returns `409` on an attempt to change `symbol` or
`profile`. `POST /sessions` validates `symbol`/`profile`/`timeframe` against the same allowlists the
run path uses.

**Dependencies.** T1.3, T3.1.

**Tests.** `test_session_api.py` with `TestClient(main.app)`:

- create / get / list / patch / archive / reopen / delete happy paths;
- **cross-user matrix** — user B gets `404` on A's session for GET, PATCH, DELETE, `/messages`,
  `/runs`;
- no identity header + `DEEP_QUANT_REQUIRE_IDENTITY=1` ⇒ `401` on every route;
- routes are absent (`404`) when `DEEP_QUANT_SESSIONS_ENABLED=0`;
- `?cursor=` pagination returns every session exactly once across pages.

**Rollback.** Flip the flag.

**Acceptance.** Tests green; with the flag off, `python -m pytest -q` is unchanged.

## T4.2 — Ownership on the existing agent routes

**Objective.** Close P8 and P9 without breaking the watcher.

**Files.** `main.py`.

**Changes.**
- `POST /run`: accept `session_id`; when present, verify ownership and create the run row. When
  `DEEP_QUANT_REQUIRE_SESSION=1`, a missing `session_id` is `422`. When `0`, the legacy client-supplied
  `thread_id` path is preserved verbatim.
- `POST /qa`: accept `session_id` + `context_run_id`; resolve to `thread_id`; verify
  `context_run_id.session_id == session_id`. Legacy `thread_id`-only path preserved while the flag is
  `0`. **Wrap the generator in `_tee_publish`** (fixes P13).
- `POST /cancel`: accept `run_id`; verify ownership; `404` on a non-owned run. Legacy `thread_id`-only
  is accepted while the flag is `0`.
- `GET /stream/{thread_id}`: verify `run.user_id == sub` via `get_run_by_thread_for_user`; `404`
  otherwise. Unknown thread with sessions disabled ⇒ today's behaviour (a bare subscription), so the
  legacy client keeps working.
- `POST /resume`: `require_service`; owning user read from the run row and used for
  `set_run_user_id`/key resolution, falling back to the body `user_id` exactly as today when there is
  no run row.

**Dependencies.** T4.1.

**Tests.** `test_agent_route_ownership.py`:

- user B cannot `GET /stream/{A's thread}`, cannot `POST /cancel {A's run}`, cannot `POST /qa` against
  A's session;
- `/resume` with a valid service credential succeeds; without one and with enforcement on, `401`;
- **regression:** the full paused → `/resume` → hub-reattach sequence still delivers frames to a
  subscriber (this is the watcher contract and there is currently *no* test for it — write it here);
- `/qa` frames now reach a `/stream` subscriber.

**Rollback.** Flags.

**Acceptance.** Tests green; `test_interaction_log.py` and `test_entitlements_endpoints.py` unchanged
and green.

### Implemented — notes

**`/stream` cannot be tested over HTTP on its allow path.** It is an unbounded SSE relay
(`while True` with a 20 s keepalive, exiting only on `request.is_disconnected()`, which never
becomes true under `TestClient`), so opening it hangs the run — measured as a 15-minute timeout.
The REFUSAL cases go through HTTP, because the ownership guard returns before the subscription; the
ALLOW cases assert `_owned_run_for_thread` directly, which is what those cases are actually about.
**T5.2's replay path has the same constraint** and must be tested the same way.

**One legacy gap is deliberate and documented in code.** A thread with NO run row is still served by
`/stream` and cancellable by `/cancel`. Such threads predate the session store — or came from a
`/run` that carried no `session_id` — so there is no recorded owner to compare against, and refusing
them would break every in-flight price watch across the deploy. It closes when
`DEEP_QUANT_REQUIRE_SESSION` is flipped and every thread has a row. It is strictly narrower than the
status quo, where even *known* threads were unprotected.

**A Phase 1 test was inverted, not deleted.** `test_cancel_is_still_open_in_phase_1` pinned the
unowned-`/cancel` gap and said it would have to change when the gap closed. It now asserts the
opposite, which is the record of the change.

**`/qa` is now teed to the fan-out hub** (P13). `/run` and `/resume` were; `/qa` was not, so a client
attached to `GET /stream/{thread_id}` — every client whose run parked at a price watch — received no
Q&A frames at all. Pinned by `test_qa_frames_reach_a_hub_subscriber`.

**The watcher contract now has tests.** The paused → `/resume` → hub-reattach sequence had **none**
before this file, despite being the path the headless watcher depends on and the one whose failure
mode is a price watch that silently never fires. Covered: service-credential acceptance, frames
reaching a hub subscriber, 400 (not 500) on an unpaused thread so the watcher stops retrying, the
owning user read from the run row rather than the body, the body fallback for legacy threads, refusal
of a user assertion on the service header, and the undeclared `heartbeat_seq` still being silently
dropped.

---

# PHASE 5 — Streaming persistence

## T5.1 — Write-through in `_run_events`

**Objective.** Persist the transcript where it is produced, batched.

**Files.** `main.py` (`_run_events`), NEW `stream_persist.py` (the batcher, so `_run_events` gains ~10
lines, not 100).

**Changes.** `StreamPersister(run_id)` with `add(event, payload)`, `flush()`, `finalize(status,
content)`. Flush on 25 frames, 250 ms, or unconditionally before any terminal frame. Folds
`REASONING`/`DECISION` content into the assistant message. Every store call is wrapped: a persistence
failure logs and degrades, and **never** breaks the live stream or fabricates a terminal state.
Active only when `DEEP_QUANT_PERSIST_STREAM=1`.

**Dependencies.** T3.1, T4.2.

**Tests.** `test_stream_persist.py`:

- a normal run: events stored in order, assistant message `complete`;
- cancellation ⇒ `cancelled`, partial content kept;
- `ERROR` ⇒ `error` with the detail;
- client disconnect mid-run ⇒ `truncated`, partial content kept, **run row not deleted**;
- duplicate `RUN_FINISHED` ⇒ one terminal transition;
- a store that raises on every call ⇒ the SSE byte stream is unchanged (assert frame-for-frame against
  a run with persistence disabled);
- **the honesty property (Hypothesis):** for every terminal path, a message with
  `status='complete'` implies a stored `RUN_FINISHED` event for its run.

**Rollback.** Flag.

**Acceptance.** SSE bytes identical with the flag on and off for a completed run.

### Implemented — notes

**Hooked at the emit sites, not as a tee.** `_run_events` already holds `(name, payload)` at every
yield, so the persister is threaded through exactly like `tracker` — one line per site. A tee would
have had to parse the formatted SSE string back into a payload, which is both wasteful and a second
source of truth about what the frame said.

**A resume appends to the ORIGINAL run.** A watcher-triggered wake continues the run it woke, so its
frames extend that run's transcript rather than starting a new one — otherwise a heartbeat would appear
in the UI as an unrelated event instead of in the glass box the user was already watching. It does
create a new assistant *message* per leg, which is correct: the agent re-analyses with fresh data and
reaches a new conclusion.

**`paused` is not terminal.** It calls `mark_watching()` and deliberately leaves the assistant message
`streaming`, because more of the answer genuinely is still coming. Finalizing there would present a
mid-watch partial as a finished analysis.

**One real data-loss bug, found by a property test.** `append_message_delta` concatenated in SQL via
`substr(content || ?, 1, ?)`, and **SQLite's `substr()` stops at an embedded NUL** — so a delta
containing one silently discarded everything after it. The rest of the assistant's answer, gone, with
no error and nothing in the row to say so. `create_message` sanitised its content through `_text`; this
path bypassed it, which is exactly how the two diverged. Found by
`test_property_streamed_text_is_never_lost_or_reordered` with the chunk `'\x000'`: expected `'0'`,
stored `''`. `finalize_message`'s replacement body had the same hazard. Both now strip NULs.

**A test-strategy trap, twice.** `st.text().filter(lambda s: s.strip())` is subtly wrong for a user id:
`'\x00'.strip()` is truthy in Python, so a NUL-only string passes the filter while the store correctly
rejects it. It bit two separate property tests, so the correct strategy is now defined once as
`VALID_USER_IDS`.

**`event_generator` gained `run_id`/`session_id` kwargs**, which broke six stub signatures in my own
tests. The stubs now take `**_kwargs` so they tolerate signature growth rather than pinning an exact
shape — the churn I had just caused.

**Verified end to end against a real failure path.** With a placeholder LLM key, `POST /run` emitted
`RUN_STARTED, ERROR`; the run recorded `error`, the user turn `complete`, and the assistant turn
`error` with empty content — *not* an empty answer presented as complete. Stored frames matched the
emitted frames exactly and `?after_seq=1` replayed one frame. That is the honesty rule working on a
genuine fault rather than a synthetic one.

**Retention runs at startup, not on a timer.** `run_events` grows at the pace of user analyses, not
market ticks, so a sweep per deploy is ample and it avoids adding a background task to a service whose
event loop must not be blocked. Noted in code as the point to revisit if the service ever runs for
months without a deploy.

## T5.2 — `GET /stream/{thread_id}?after_seq=N` replay

**Objective.** Close P12 — frames published while nobody is attached are currently lost forever.

**Files.** `main.py` (`stream_thread`).

**Changes.** Before subscribing to the live hub, replay `list_run_events(run_id, after_seq)`; then
attach. A frame that arrives live during the replay is de-duplicated by `seq`. Omitting `after_seq`
preserves today's live-only behaviour byte for byte.

**Dependencies.** T5.1.

**Tests.** `test_stream_replay.py` — frames emitted with no subscriber are delivered on a later
`?after_seq=0` attach; no duplicates across the replay/live boundary; `after_seq` beyond `last_seq`
yields only live frames; no `after_seq` ⇒ no replay.

**Rollback.** Ignore the parameter.

**Acceptance.** Tests green; the reattach regression test from T4.2 still passes.

## T5.3 — Server-minted `thread_id` + `run_id`

**Objective.** Retire `thread_${symbol}_${Date.now()}`.

**Files.** `main.py`, `session_store.py`.

**Changes.** `create_run` mints both. `RUN_STARTED` gains `session_id` and `run_id` (additive —
existing consumers ignore unknown keys). The client stops sending `thread_id` in T6.3.

**Dependencies.** T5.1.

**Tests.** `test_run_identity.py` — `POST /run {session_id}` with no `thread_id` succeeds; the minted
thread is unique across 1000 runs; `RUN_STARTED` carries all three ids; a legacy body with
`thread_id` and no `session_id` still works while `DEEP_QUANT_REQUIRE_SESSION=0`.

**Rollback.** Flag.

**Acceptance.** Tests green; `reco_store`'s `UNIQUE(thread_id)` is never violated across two FIND runs
in one session (assert explicitly — this is the constraint that forced the run-per-FIND design).

## T5.4 — Retention pruner

**Objective.** Bound `run_events` growth without touching compliance data.

**Files.** `session_store.py`, `main.py` (a startup task on a daily interval).

**Changes.** `prune_run_events(retention_days)` from `RUN_EVENTS_RETENTION_DAYS` (default 90). Opens
`SESSIONS_DB_PATH` only.

**Dependencies.** T3.1.

**Tests.** `test_prune_run_events.py` — old archived-session events removed, recent kept, live runs
never pruned, `compliance.db` file hash unchanged.

**Rollback.** Set retention to 0 (disabled).

**Acceptance.** Tests green.

---

# PHASE 6 — Frontend session store + the routing fix

**This is the phase that makes multi-session safe. No tab UI before it lands.**

## T6.1 — `useSessionStore`

**Objective.** One store for `activeSessionId`, per-session stream buffers, and per-session UI state.

**Files.** NEW `frontend/src/store/useSessionStore.ts`; NEW `frontend/src/store/sessionSelectors.ts`.

**Changes.**
```ts
sessions:   Record<SessionId, QuantSession>   // reuse the EXISTING QuantSession shape verbatim
streams:    Record<SessionId, { threadId: string | null; runId: string | null }>
ui:         Record<SessionId, { mode:'FIND'|'VERIFY'; draft:string; verification: VerificationDraft }>
threadToSession: Record<string, SessionId>
activeSessionId: SessionId | null
```
Actions: `setActiveSession`, `upsertSession`, `bindThread(threadId, sessionId)`, `applyFrame(frame)`,
`setUi(sessionId, patch)`, `dropSession`. `applyFrame` calls the **existing exported**
`applyStreamEvent` — `useQuantStore` exports it in this task, unchanged.

**Dependencies.** none.

**Tests.** `useSessionStore.routing.test.ts` + `.property.test.ts`:

- **the hard invariant:** a frame carrying A's `thread_id` never mutates B's session, for any
  interleaving of A/B frames (Hypothesis);
- a frame with an unknown/absent `thread_id` mutates **nothing** and increments an unroutable counter;
- switching `activeSessionId` mid-stream: A keeps accumulating, B is untouched, switching back shows
  A's accumulated state;
- per-session `ui` survives a switch away and back;
- two concurrent Q&A streams (A and B) both progress — the current process-wide lock is gone.

**Rollback.** Delete the module; nothing imports it yet.

**Acceptance.** `npx tsc --noEmit` clean; new tests green.

## T6.2 — Delete the active-session fallback

**Objective.** Remove the routing hazard (P5) at its source.

**Files.** `frontend/src/store/useQuantStore.ts`, `frontend/src/components/quant/DeepQuantPanel.tsx`.

**Changes.** `handleStreamEvent` delegates to `useSessionStore.applyFrame`. The
`st._streamingKey || st.activeViewKey` branches are **deleted**, not softened. `sessionsByKey`,
`activeViewKey`, `_streamingKey`, `_threadToKey` and the flat mirror stay in place for one phase as a
read-only shim that projects from `useSessionStore`, so no component changes yet.

**Dependencies.** T6.1.

**Tests.** All four existing `useQuantStore.*.property.test.ts` suites must pass **unchanged** — they
drive the reducer through `handleStreamEvent`, so they are the regression net for this refactor. Add
`useQuantStore.noFallback.test.ts`: a frame with an unknown `thread_id` changes no session.

**Rollback.** `NEXT_PUBLIC_FQ_MULTI_SESSION=false`.

**Acceptance.** `npx vitest run src/store src/components/quant` green.

### Implemented — the sequencing correction that matters

The plan said to delete the fallback outright and keep `sessionsByKey` as a read-only shim. **That
order does not work**, and shipping it would have blanked the panel. Nothing binds a server session id
until the run path is migrated and a session exists to bind to, so an unconditional deletion makes
*every* frame unroutable — the store would be correct and the workspace empty.

So the switchover is gated on `NEXT_PUBLIC_FQ_MULTI_SESSION`. With it off, `handleStreamEvent` runs the
legacy path verbatim; with it on, it delegates to `useSessionStore.applyFrame` and returns. The
fallback is deleted **from the new path**, which is what the invariant needs, without an intermediate
state that cannot be shipped. Rollback is the flag rather than a revert.

`NEXT_PUBLIC_` is acceptable here specifically because this is a rollout switch, not a gate: the
binding checks are all server-side, so the worst a user can do by flipping it in devtools is give
themselves a UI whose backend refuses them.

### The property test is the deliverable

`useSessionStore.isolation.property.test.ts` asserts the strong form: **for any interleaving of frames
across three sessions, each session ends in exactly the state its own frames alone would produce**,
compared against single-session control runs with wall-clock ids and timestamps normalised away.

A weaker "A's content is not in B" test passes even when interleaving perturbs A's own state — a
dropped chunk, a coalescing boundary landing differently, a status arriving out of turn. A second
property switches the active session between every single frame and asserts nothing changes, which is
the direct refutation of the old `|| activeViewKey` behaviour. 600 runs across four properties.

### Other notes

**The reducer is imported, not reimplemented.** `applyStreamEvent` is now exported from
`useQuantStore`. Four property suites pin behaviour on it that was expensive to get right — DECISION
first-write-wins, no `?? 75` conviction default, `RUN_FINISHED` enriching without downgrading, the
`watching → RUN_STARTED` branch dropping the stale leg's plan. A second reducer would have drifted from
all four and re-earned those bugs.

**`startAgentRun` returns a session id, not a thread id.** It cannot return a thread: the server mints
it inside `POST /run` and reports it on `RUN_STARTED`, which is what `applyFrame` self-binds from.
Dispatch between the session and legacy paths is on the presence of `session_id` in the args rather
than on the build flag, so the two cannot disagree with the caller about which ran.

**Cancel before `RUN_STARTED` posts nothing.** There is no server-side identifier yet, so the local
`AbortController` is the whole stop. Stated in code rather than papered over by inventing an id.

**Unroutable frames are counted.** `unroutableFrames` being non-zero means a run is streaming whose
thread this client never bound — a real bug. Under the old fallback it was invisible, because those
frames were written into the visible session and looked like data.

## T6.3 — Session-aware run / QA / cancel calls

**Objective.** The client stops minting ids and stops using the flat mirror to decide what to ask
about.

**Files.** `frontend/src/lib/bridge/webAdapters.ts`, `useQuantStore.ts`
(`fetchDeepAnalysis`, `askQuestion`, `cancelAnalysis`).

**Changes.**
- `startAgentRun`: send `session_id`, no `thread_id`; learn the ids from `RUN_STARTED` and call
  `bindThread`. Keep the paused→hub reattach loop, now with `?after_seq=<last seen seq>`.
- `ask_trade_question`: send `session_id` + `context_run_id`; relay frames onto the same bridge event.
  The per-question closure and the global `qaStatus` lock are removed — routing is by `thread_id`.
- A run reads `symbol`/`timeframe`/`profile` from the **session**, not from `useTradeStore` at call
  time (design §9.4).
- `cancel_deep_quant_agent`: send `run_id`.

**Dependencies.** T6.1, T6.2, T5.3, T4.2.

**Tests.** `webAdapters.session.test.ts` — the run POST body carries `session_id` and no `thread_id`;
the reattach URL carries `after_seq`; QA carries `context_run_id`. `lib/bridge/__tests__/coverage.test.ts`
must stay green (every `bridgeInvoke` literal resolves).
`useQuantStore.runContext.test.ts` — with `useTradeStore.activeTimeframe = '5m'` and the active
session's timeframe `'10m'`, the run is issued with `10m`. This is the "Session A run executed with
Session B timeframe" test.

**Rollback.** `git revert`; the backend legacy path still accepts a client `thread_id`.

**Acceptance.** Manual end-to-end FIND still streams; `npm run build:web` succeeds.

---

# PHASE 7 — Server becomes the source of truth

## T7.1 — TanStack Query, scoped

**Objective.** One disciplined server-state cache for the session resources.

**Files.** `frontend/package.json`, `frontend/src/app/layout.tsx` (or a client provider component),
NEW `frontend/src/lib/fq/queries.ts`, NEW `frontend/src/lib/fq/api.ts`.

**Changes.** Add `@tanstack/react-query` at an exact pin. Provider mounted in a client component.
Query keys only under `['fq', …]`. Hooks: `useSessions`, `useSession`, `useMessages` (infinite,
`after_seq` cursor), `useRuns`; mutations `useCreateSession`, `useRenameSession` (optimistic),
`useArchiveSession` (optimistic + rollback). `api.ts` is a thin typed `fetch` over
`/api/deepquant/sessions*`. Existing `useApi` hooks are **not** migrated.

**Dependencies.** T4.1.

**Tests.** `fq/__tests__/queries.test.tsx` — list/detail fetch and cache; optimistic rename rolls back
on error; archive invalidates the list; messages paginate with `after_seq` without duplicates.

**Rollback.** Remove the provider and the module; the dependency is inert.

**Acceptance.** `npx tsc --noEmit` clean; `npm run build:web` succeeds; bundle delta recorded in the PR.

## T7.2 — Rehydration

**Objective.** Reopening a session reproduces a live transcript exactly.

**Files.** NEW `frontend/src/lib/fq/rehydrate.ts`, `useSessionStore`.

**Changes.** `rehydrateSession(sessionId)`: fetch session + runs + messages + the active run's events,
then feed the stored events through `applyStreamEvent` in `seq` order to rebuild `reasoningSteps`,
`finalTrade` and `sessionStatus`. If a run is `running`/`watching`, attach to
`/stream/{thread_id}?after_seq=<last stored seq>`. A message with `status='streaming'` and no live
producer renders as `truncated`, never as an answer.

**Dependencies.** T7.1, T5.2.

**Tests.** `rehydrate.test.ts` — a captured frame sequence produces a session byte-identical to the
one built by streaming the same frames live (this is the anti-drift test); a `truncated` message
renders its truncation; a live run reattaches with the right `after_seq`.

**Rollback.** `git revert`.

**Acceptance.** Tests green.

### Implemented — the step the task description did not name

Replaying stored frames through `applyStreamEvent` is necessary but **not sufficient**, and this is the
part that is easy to miss. A run whose process died mid-stream has no terminal frame, so the reducer
leaves the session `running` with `isAnalyzing: true` — a dead run rendering as a live one, with a
spinner that never stops. `reconcileWithRun` is therefore a required second step, taking the server's
verdict (which `reconcile_stale_runs` resolved against the durable checkpoint) as authoritative.

There is a test asserting the naked defect — that replay alone leaves it looking `running` — so the
reconciliation cannot be removed without something failing.

`truncated` maps onto the existing `error` status rather than widening `sessionStatus` to six values.
A sixth state would ripple through every component and the four reducer property suites for no gain;
what the user needs is to be told the analysis did not finish, and the error path already says things
clearly. **A truncated run also drops its plan** while keeping its reasoning: a run may have emitted a
DECISION before dying, and rendering an executable trade card for an analysis that never completed its
own verification is the worst outcome available here.

The anti-drift guarantee is asserted by building the same session **both ways** — replayed and
live-streamed — and comparing, rather than by checking a few fields. That is what makes it a guarantee.

### TanStack Query — what was actually decided

`@tanstack/react-query@5.102.8`, exact-pinned, scoped to `['fq', …]` keys. The existing `useApi` hooks
are **not** migrated: rewriting working code to adopt a new library is churn that would put unrelated
screens in this migration's blast radius.

Two non-obvious choices, both about what a user sees rather than about caching:

* **Rename is optimistic; archive is not.** A rename is direct manipulation of a label on screen, so a
  round trip reads as lag. Archiving *removes a tab*, and a removal that has to be undone looks like
  the app losing track of the user's work — worse than a brief wait.
* **Delete REMOVES cached queries rather than invalidating them.** An invalidated detail query
  refetches and 404s, surfacing an error for something the user asked to be gone.

Rollback on a failed rename restores from the snapshot rather than refetching, because a refetch during
a streaming run could return a newer `updated_at` and reorder the tab bar as a side effect of a
*failed* rename.

`QueryClient` is created in a `useState` initialiser, not at module scope. On this deployment
(`output: 'standalone'`, a long-lived Node server) a module-scope client is shared across requests, so
one user's cached session list could be served to another. With per-user conversation data in the
cache that is a correctness requirement, not a convention.

Test-harness notes: the suite defaults to `environment: 'node'`, so any file that renders — even a hook
via `renderHook` — needs the `// @vitest-environment jsdom` docblock and an explicit `import React`.
And a transport failure is deliberately retried (unlike a 404), so its assertion needs a timeout longer
than the backoff; the retry policy itself is asserted separately.

## T7.3 — Remove the flat mirror

**Objective.** Delete the second mutable source of truth (P6).

**Files.** NEW `frontend/src/components/quant/useFqSession.ts`; `DeepQuantPanel.tsx`,
`AgentTerminal.tsx`, `TradeQaPanel.tsx`, `QaMessages.tsx` call sites.

**Changes — as planned.** `activeMode`, the composer draft and the verification form move into
`ui[sessionId]` (P15). Components stop reading `useQuantStore` for per-session state.

**Changes — CORRECTED.** The plan said to delete `projectSession`, `sessionsByKey`, `activeViewKey`,
`_streamingKey`, `_threadToKey`, `activateSymbolSession`, `clearQa`, `askQuestion` and the 12 flat
fields **in this task**. That is wrong for the same reason the T6.2 fallback deletion was wrong: the
legacy path is what ships until `NEXT_PUBLIC_FQ_MULTI_SESSION` is turned on, and it reads those
fields. Deleting them here blanks the panel for every user.

Instead the reads are routed through ONE flag-aware layer, `useFqSession.ts`, with per-field hooks
(`useFqReasoningSteps`, `useFqQaStatus`, `useFqDraft`, `useFqAskQuestion`, …). Per-field rather than one
hook returning an object, because an object literal is a new reference every render and would
re-render the panel on every frame of every session.

The flat-mirror **deletion** moves to T11.1, where it becomes: delete one branch in `useFqSession.ts`
and the now-dead store fields. The six component call sites do not change again.

**Also fixed in this task** (all found by writing the tests, all pre-existing):

- `sessionSelectors` returned a FRESH empty object/array when no session was active, so
  `useSyncExternalStore` would reject the uncached snapshot — a render loop on the first paint of every
  migrated component. Empty values are now frozen module singletons;
  `selectStreamingSessionIds` is memoized on the `sessions` reference.
- `_run_events` stamped `thread_id` only inside the node-update loop, so `ERROR` and `RUN_FINISHED`
  went out with no routing key. A failed run could not reach its own session. Now stamped at every
  frame's construction site via one `_stamp` helper.
- Q&A answers were indistinguishable from analysis reasoning on the wire, so they would have been
  appended to the glass box live while rehydration showed them as chat. Frames now carry
  `turn: "run" | "qa"`; the client routes on it (`applyQaFrame`).
- `webAdapters.ask_trade_question` put the **session id in the `thread_id` field** of its synthetic
  terminal frame, which the store can never route — the composer stayed locked forever. It now learns
  the real thread id from the stream and names the session directly.
- `applyFrame` self-bound only on `RUN_STARTED`, leaving locally synthesized terminals unroutable. Now
  any frame naming its session binds, and retargeting an already-bound thread is still impossible.
- `QaMessages.AssistantMessageRow` subscribed to `askQuestion` and `qaMessages` and read neither, so
  every assistant row re-rendered on every frame of a streaming answer.

**Dependencies.** T6.3, T7.2.

**Tests.** NEW `useFqSession.multiSession.test.tsx` (flag on) and `useFqSession.legacy.test.tsx` (flag
off) — both paths, because the flag decides which ships. NEW `sessionSelectors.stability.test.ts`,
`useSessionStore.qaRouting.test.ts`, `test_turn_kind_stamp.py`. Existing `AgentTerminal.*` and
`useQuantStore.skuGate.property.test.ts` pass **unchanged** — the hook layer preserved the legacy path,
so not even store-setup lines needed editing.

**Rollback.** `git revert`.

**Acceptance.** `npx tsc --noEmit` clean; `npm run build:web` succeeds; `npx vitest run` 149 files /
1286 tests with the one known pre-existing `LeftPanel.stockFnoSelection` load-timeout flake;
`python -m pytest -q` 23 failed / 2000 passed — the same 23 pre-existing names as the recorded
baseline.

---

# PHASE 8 — Session tabs

## T8.1 — `SessionTabBar`

**Files.** NEW `frontend/src/components/quant/session/SessionTabBar.tsx`, `SessionTab.tsx`,
`NewSessionButton.tsx`.

**Changes.** `SYMBOL · TF · h:mm a` primary; title on hover / `title` attribute / mobile detail row.
Active vs inactive state; per-tab close (archive) with a running-run confirm; overflow → horizontal
scroll with `scroll-snap` and edge fades, plus an overflow menu past 8 tabs. Streaming indicator per
tab. Loading / creating / error states. Existing Tailwind v4 tokens only
(`bg-surface/elevated`, `text-text-primary/secondary/muted`, `border-border-default`) — no new colours.

Accessibility: `role="tablist"` / `role="tab"` / `aria-selected`, arrow-key + Home/End navigation,
`Ctrl/⌘+1..9` to switch, visible focus ring, close button reachable by keyboard with an accessible
name.

**Dependencies.** T7.1, T7.3.

**Built.** `SessionTabBar.tsx`, `SessionTab.tsx`, `NewSessionButton.tsx`, plus
`sessionLabel.ts` — the naming rules pulled out as pure functions so they are testable without
rendering. Mounted in `DeepQuantPanel` behind `FQ_MULTI_SESSION`, rendering **nothing** when off.

**Decisions taken during implementation:**

- **The list is server-sourced** (`GET /sessions`), not derived from `useSessionStore`. A tab therefore
  survives a reload, and a session that exists only in one browser tab is unrepresentable.
- **Activation is injected** (`onActivate` prop), because activating a session the client has not
  loaded must also rehydrate it — which belongs to the workspace that renders the transcript
  (T9.1/T10.1). The default keeps the bar usable and testable alone.
- **A user rename wins over the derived label.** `title` is nullable so the client can tell "never
  named" from "named deliberately"; the derived `SYMBOL · TF · h:mm a` and the profile move to the
  tooltip. A blank/whitespace title falls back to the derived label rather than rendering an empty tab.
- **Times are formatted in `Asia/Kolkata`, not the browser timezone.** A session label is a trading
  timestamp; one that disagreed with every chart in the app is worse than none.
- **Confirm ONLY when a run would be interrupted.** Confirming every close trains the user to dismiss
  without reading. An idle session is recoverable from history.
- **Archive is not optimistic** and the client drops its copy only after the server accepts — a tab
  that reappears looks exactly like lost work.
- **Roving tabindex**, so the bar is one tab stop rather than eight. Arrow keys move focus **without**
  activating; committing is a click. `Ctrl/⌘+1..9` switches, with 9 meaning *last* as in browsers.
- **`FqQueryProvider` is mounted here**, not at the app root — it exists for the session/message/run
  resources only. ⚠️ **T10.1 must HOIST it to a shared layout, not add a second one**, or the panel and
  the standalone route would keep separate caches and a tab archived in one would still be listed in
  the other.

**Fixed while testing** (both were mine, both found by the tests):

- `doArchive` had `try/finally` with no `catch`, so a failed archive became an **unhandled promise
  rejection** and the user saw nothing at all — the close button read as broken. Now caught and
  surfaced with the server's reason.
- The close button was `opacity-0` on inactive tabs, which is **invisible but still clickable**. On
  touch, where no hover reveals it, a 20px invisible target sat on every tab edge and a tap meant to
  switch sessions would archive one. Now `pointer-events-none` while hidden; the touch path is
  activate-then-close.

**Tests.** `sessionLabel.test.ts` (16) — market-timezone formatting, the seconds-vs-milliseconds
conversion, rename precedence, and two sessions on the same symbol+timeframe being distinguishable
(the case `${symbol}::${profile}` could not represent). `SessionTabBar.test.tsx` (27) — server-sourced
list, one selected tab, streaming indicator on a **background** session, injected activation, roving
tabindex, arrow/Home/End, `Ctrl+N`, confirm-only-when-running, archive failure leaves the tab and says
why, active-tab close moves to a survivor, last-tab close clears the selection, overflow menu.

`fireEvent` is used rather than `@testing-library/user-event` (not installed; not worth a dependency to
press a button). Consequence stated in the test: jsdom does not translate Enter on a `<button>` into a
click, so that behaviour is asserted structurally — the tab is a real `<button type="button">`, which
is what earns it — rather than by faking a keypress.

**Rollback.** `NEXT_PUBLIC_FQ_MULTI_SESSION=0`.

**Acceptance.** `npx tsc --noEmit` clean; `npm run build:web` succeeds; 52/52 across the three new
session suites; full frontend run green apart from the pre-existing `LeftPanel` load-timeout flake. The
panel with the flag off renders no tab bar at all, so it is unchanged.

## T8.2 — New Session

**Files.** `NewSessionButton.tsx`, `lib/fq/queries.ts`.

**Changes.** `POST /sessions` with the **current trading context** as the seed
(`selectedSymbol`, `activeTimeframe`, `activeProfile`) — never a fake local session. On success:
activate, push the URL, invalidate the list. Failure surfaces an inline error and creates nothing.

**Dependencies.** T8.1.

**Built.** `NewSessionButton.tsx`. The trading context is read at **click** time via
`useTradeStore.getState()` rather than subscribed — subscribing would re-render the whole tab bar on
every symbol change, and would risk posting a stale symbol.

An empty symbol is rejected client-side. The server would answer `422 Unprocessable Entity`, which is
not a sentence to show a trader; "Pick a symbol first." is.

URL push is **not** implemented here — there is no session route until T10.1. `onCreated` hands the
server id to the bar, which activates it.

**Tests.** `NewSessionButton.test.tsx` (9) — the POST body is exactly the current context; the context
is read at click time, not mount; an empty symbol posts nothing; success returns the **server-minted**
id and invalidates the list; a failed POST leaves zero sessions and a null `activeSessionId`; the
server's reason is shown (out-of-credit and service-down need different actions from the user); a
transport rejection does not become an unhandled rejection; dismiss-and-retry works.

**Rollback.** Flag.

**Acceptance.** No code path creates a session object without a server `session_id` — asserted directly
by "creates nothing at all" on the failure path.

---

# PHASE 9 — Session history

## T9.1 — History panel

**Files.** NEW `frontend/src/components/quant/session/SessionHistory.tsx`, `SessionHistoryRow.tsx`.

**Changes.** Server-paginated (cursor) list: title, symbol, timeframe, relative updated time, status,
last run's action. Reopen / rename (inline) / archive. Loading skeleton, empty state, error state.
Search via `?q=` only if the list exceeds one page. **Never** loads the full history into the browser.

**Dependencies.** T7.1, T8.1.

**Built.** `SessionHistory.tsx`, `SessionHistoryRow.tsx`, plus the two pieces the plan implied but never
named:

- **`lib/fq/useActivateSession.ts`** — the first caller of `rehydrateSession`, which had existed since
  T7.2 with nothing invoking it. This is where "reopen activates and rehydrates" actually lives.
- **`SessionTabBarConnected.tsx`** — wires the bar and history to that hook. Needed because
  `useActivateSession` consumes the query client, and `DeepQuantPanel` *provides* it; a hook cannot
  consume a context its own component supplies.

**`SessionStream.hydratedAt` was added to the store.** Whether a session is loaded cannot be inferred
from `sessions[id]`: `setActiveSession` and `upsertSession` both create a blank entry as a side effect,
so an unopened session would present as a finished conversation with nothing in it. `markHydrated`
takes `lastSeq` as a **floor**, never a reset — a frame can land between the rehydration snapshot and
the marker, and lowering the high-water mark would make the next reattach request a gap it already has,
re-delivering frames and duplicating the transcript.

**Decisions taken during implementation:**

- **Switching is free; opening costs a fetch.** Two operations wear one name in the UI. Conflating them
  gives either a blank panel on first open or a refetch of thousands of frames per tab flip.
- **The activation switches the session BEFORE awaiting.** Awaiting the rehydration first makes every
  first open feel broken — the tab does not respond until the network does.
- **Rehydration goes through `fetchQuery`**, so a double-click, or a tab and a history row racing, is
  one request rather than two full replays.
- **404 and 401 are reported separately.** One offers a new session, the other means the login expired;
  collapsing them makes an expired login look like deleted work.
- **Search appears only past one page, and is sticky once shown.** A box over eight rows invites
  searching when scanning is faster; removing it because the *filtered* result is short would strand
  the user in a filtered view with no way out.
- **`?q=` filters server-side, debounced 250ms.** A client-side filter would search one page while
  looking like it searched everything.
- **`IntersectionObserver` drives infinite scroll, but a Load-more button is always present.** The
  observer is absent in jsdom and in some embedded webviews; without the button, history would be
  permanently truncated there — and the button is also the keyboard path.
- **Reopen also opens.** A row that flips a status badge and nothing else appears to do nothing.
- **Rename does not seed the input with the derived label** — pre-filling turns "rename" into "accept
  this generated name", and the user ends up with a title they never chose. Clearing the field sends
  `title: null`, which is the only way back to the derived label.

**Fixed while testing:**

- The loading skeleton was built from `<ul>/<li>`, making it **indistinguishable from a loaded list by
  role** — assistive technology announced "list, 4 items" for placeholder boxes. Now a `role="status"`
  region.
- The row's open button had no `aria-label`, so its accessible name was the label plus every metadata
  fragment beneath it ("RELIANCE · 10m · 9:15 AM RELIANCE · 10m just now FIND · complete") and said
  nothing about what activating it does.

**Tests.** `SessionHistory.test.tsx` (27) and `useActivateSession.test.tsx` (14): one request per page
with the server's cursor echoed verbatim and no duplicate rows; server-side debounced search; optimistic
rename with rollback from the snapshot; Escape abandons an edit; archive/reopen including both failure
paths; rebuild-from-stored-frames through the same reducer the live stream uses; thread binding for a
still-live run; the run's `last_seq` rather than the page's; no network on a re-switch; a frame arriving
after the snapshot is not discarded; a double-click is one rehydration; gone vs unauthenticated.

⚠️ **Fake timers were tried and removed.** `vi.useFakeTimers()` for the debounce made every test *after*
that block hang: `waitFor`/`findBy` and TanStack Query both schedule on timers, and swapping the clock
under a mounted tree and a live query client leaks pending work into the next test. Real timers plus
`waitFor` assert the same thing; the suite also went from 65s to 6s.

**Rollback.** Flag.

**Acceptance.** `npx tsc --noEmit` clean; `npm run build:web` succeeds; 156/156 across `src/lib/fq` and
`src/components/quant/session`; full frontend run **154 files, 0 failures**.

---

# PHASE 10 — Chat promotion + route

## T10.1 — `/find-trade/session/[sessionId]`

**Files.** NEW `frontend/src/app/find-trade/session/[sessionId]/page.tsx` (+ `loading.tsx`,
`not-found.tsx`), NEW `components/quant/session/SessionWorkspace.tsx`.

**Changes.** Full-width workspace: tab bar → session header (`RELIANCE · 10m`, run state) →
conversation stream → composer. Reuses `AgentTerminal`'s glass-box renderers verbatim — structured
tool activity is **not** flattened to text. Deep-linkable; unknown/not-owned ⇒ `not-found`.
Mobile: horizontal-scroll tabs, history in a sheet that does not unmount the chat, composer pinned
above the keyboard inset, plan and reasoning readable at 360 px.

> **Read `node_modules/next/dist/docs/` for this version's routing / params / metadata conventions
> before writing this route** (`frontend/AGENTS.md`). Do not assume App Router conventions.

**Dependencies.** T8.1, T9.1.

⚠️ **`node_modules/next/dist/docs/` does not exist in this install.** The instruction above could not be
followed as written. Conventions were verified instead against the installed version (**Next 15.5.19**)
and against the repo's own dynamic route, `app/api/deepquant/[...path]/route.ts`, which already types
`params` as a **Promise**. That is the Next 15 change that matters here: read synchronously,
`params.sessionId` is `undefined`, and the page would happily fetch `/sessions/undefined`. A test asserts
the awaiting directly rather than trusting it.

**Built.** `page.tsx`, `loading.tsx`, `not-found.tsx`, `SessionWorkspace.tsx`, plus
`components/quant/useFqStreamListeners.ts`.

**The listeners had to be extracted.** Both bridge subscriptions lived inline in `DeepQuantPanel`. The
standalone route is a different React tree, so a run started from the panel would stream fine while the
same run opened at `/find-trade/session/{id}` received **nothing at all** — frames emitted, nobody
subscribed. One hook, two callers.

**Decisions taken during implementation:**

- **The workspace renders `AgentTerminal` and `TradeQaPanel` verbatim.** The requirement is that
  structured tool activity is not flattened to text; the only durable way to honour that is to render
  the same components rather than grow a second set that drifts.
- **`notFound()` for both "does not exist" and "is not yours".** A 403 would confirm an id is real,
  turning the route into an enumeration oracle. The API already answers 404 for both; the UI must not
  undo it. The not-found page also refuses to speculate about *why*.
- **401 is NOT not-found.** A deep link is exactly when a cookie is most likely to have lapsed, so an
  expired login gets its own message. Collapsing it would tell the user their conversation was deleted.
- **The route 404s entirely when `FQ_MULTI_SESSION` is off** — on that build the page genuinely does not
  exist, which beats rendering a half-built workspace.
- **The id is shape-checked before any fetch.** Ownership is server-enforced, but a malformed id should
  not be interpolated into a request URL, and should not cost a round trip.
- **`generateMetadata` is deliberately generic and `robots: noindex`.** A page title leaks into browser
  history, screenshots and shared tabs; the symbol and the user's own title are private to the session.
- **History overlays the conversation rather than replacing it.** Unmounting the transcript would discard
  the subtree a live run is streaming into, so reopening it would look like the run had restarted.

**`FqQueryProvider` placement — hoisted, measured, reverted.** It was moved to the root layout so the
panel and the route would share one cache. Measured cost: **shared JS 142 kB → 154 kB on every page**,
including `/dashboard`, which has no session UI. Reverted to one provider per entry point (**144 kB**;
the residual +2 kB is the Phase 8/9 components themselves). The hoist bought nothing: the two pages never
mount together, and what must survive a navigation is the session **state**, which lives in the
module-scoped `useSessionStore` — its `hydratedAt` marker persists, so `useActivateSession`
short-circuits instead of replaying a transcript. The earlier warning in T8.1 that two providers would
let a tab archived in one tree still be listed in the other was **wrong**, and is corrected there.

**Tests.** `SessionWorkspace.test.tsx` (12) and `page.test.tsx` (9): rebuild from stored frames with
nothing in memory; tool activity stays structured and does not pollute the message text; header carries
symbol/timeframe/profile; loading state before the transcript; 404 for unknown *and* for another user's
session with no wording difference; 401 says "expired"; retry for a 500; the composer sends
`session_id` + `context_run_id` and **no** `thread_id`; the question appears optimistically; history
overlays. Page: `params` is awaited; malformed ids (traversal, query injection, slash, whitespace,
over-long) 404 without a fetch; legitimate id shapes are accepted; flag-off 404s the route.

**Three test-harness gaps worth recording, because each looked like a product bug:**

- `vi.mock` factories are **hoisted**, so a `const` spy declared above one is uninitialised when it runs
  ("Cannot access 'notFoundMock' before initialization"). `vi.hoisted` is required, not stylistic.
- jsdom has **no layout engine and no `scrollIntoView`**. `AgentTerminal` auto-scrolls on every
  transcript change, so without a stub the first frame throws and the transcript never renders.
- A restored **finished** run renders its reasoning inside a *collapsed* `ThinkingGroupRenderer`
  (`useState(isRunning)`), so the words are legitimately absent from the DOM. The test now expands the
  group — which proves more than the original assertion: that the restored transcript uses the same
  collapsible renderer rather than dumping raw text.

**No 360 px viewport test.** jsdom has no layout, so any claim it made about what is visible at a given
width would be fiction. That assertion belongs to the Playwright job in T10.2 and is deliberately absent
here rather than faked.

**Rollback.** Delete the route directory; the sidebar panel is untouched.

**Acceptance.** `npx tsc --noEmit` clean. `npm run build:web` **registers the route** —
`ƒ /find-trade/session/[sessionId]`, server-rendered on demand (the mandatory check: a route can
typecheck and still fail to register). Full frontend run **156 files / 1400 tests**, the only failures
being the pre-existing `LeftPanel.stockFnoSelection` flake, which passes 3/3 in isolation.

## T10.2 — E2E in CI

**Objective.** An e2e job that actually runs, rather than tests that never execute.

**Files.** `.github/workflows/ci.yml` (new `e2e` job + add it to `ci-ok`'s `needs`),
`frontend/package.json` (new `test:e2e` script — there is none today),
`frontend/playwright.config.ts` (**exists but is stale — repair, do not recreate**),
`frontend/tests/e2e.spec.ts` (**exists and tests a deleted architecture — replace**),
NEW `frontend/tests/fq-multi-session.spec.ts`, NEW `frontend/tests/support/stub-agent.mjs`.

**Pre-work — the existing E2E layer is not merely unrun, it is wrong.** Before adding coverage:
- `playwright.config.ts` targets `baseURL: http://localhost:1420` and runs `npx next dev --port 1420`.
  1420 was the **Tauri** dev port; the desktop shell is gone. Retarget to `3000` and run
  `next start` against a `build:web` output, so CI exercises the production build (a route can
  typecheck and test green and still fail to register — see `CLAUDE.md` §0).
- `tests/e2e.spec.ts` drives `invoke('run_deep_quant_analysis')` through a "stubbed Tauri IPC bridge"
  and cites `src-tauri/src/quant/mod.rs`, which does not exist. Replace it; do not extend it.
- Add `"test:e2e": "playwright test"` to `frontend/package.json`.

**Changes.** The job builds the frontend, starts a real deep-quant with
`LLM_API_KEY=<placeholder>` and a **stubbed graph** (a fixture module that emits a canned frame
sequence — no LLM, no Kite, no QuestDB), stubs `/users/me` behind a local identity fixture, and runs
Playwright. Covered: create session → FIND → streamed result → QA → second session → switch → assert
isolation → reload → restore → open history → reopen an old session.

**Honest limitation, recorded in the workflow file:** the LLM and market-data paths are stubbed. CI
proves the session/streaming/isolation/persistence wiring, not model quality. Full-stack verification
stays manual.

**Dependencies.** T10.1.

## T10.2 — STATUS: INFRASTRUCTURE DONE AND VERIFIED, JOURNEY SPEC NOT YET GREEN

The job is defined and **runs**, but it is deliberately **not** in `ci-ok`'s `needs` yet, because the
browser journey does not pass. Wiring a known-red job as a required gate would block every merge;
deleting it would hide the gap. It must be added to `ci-ok` the moment the journey is green — an e2e job
that cannot fail the build is precisely the "tests that never execute" problem this task set out to fix.

**Pre-work, confirmed exactly as the plan described it.** `playwright.config.ts` targeted
`baseURL: http://localhost:1420` and ran `next next dev --port 1420` — 1420 was the **Tauri** dev port,
so nothing had listened there since the desktop shell was removed. `tests/e2e.spec.ts` drove
`invoke('run_deep_quant_analysis')` through a stubbed Tauri IPC bridge and cited
`src-tauri/src/quant/mod.rs`, which does not exist. Config repaired; spec deleted, not extended.

**Built.**

- `frontend/playwright.config.ts` — retargeted to `next start` against a **`build:web` output** rather
  than `next dev`, because a route can typecheck, unit-test green and still fail to REGISTER, and the
  new workspace is exactly that shape of risk. `retries: 0` on purpose: a retry on a streaming or
  isolation test converts a real intermittent routing bug into a green run.
- `agents/deep-quant-loop/e2e_stub_server.py` — the **real** FastAPI app, session store, SQLite
  persistence, SSE assembler, identity verification and ownership checks, with only the compiled graph
  replaced by a canned frame sequence.
- `frontend/tests/support/stub-identity.mjs` — `/users/me` for two distinct users, because a
  single-user e2e cannot prove ownership isolation.
- `frontend/tests/fq-multi-session.spec.ts` and `…mobile.spec.ts` (the 360 px assertions, which jsdom
  cannot make at all).
- `agents/deep-quant-loop/tests/test_e2e_stub_server.py` (5 tests) — the stub is tested like any other
  code, because a stub that silently fails to install would leave the job hanging or reaching for a real
  LLM.
- `.github/workflows/ci.yml` — the `e2e` job, plus `frontend/tests/**`, `playwright.config.*` and
  `next.config.*` added to the `changes` filter. Without those, editing a spec matched **no** filter,
  skipped every job and reported green — the same defect the `service-metrics` note in that file records.
- `frontend/package.json` — `test:e2e`, `test:e2e:install`.

**Verified by actually running it** (local processes, real HMAC, real SQLite):

| Checked | Result |
|---|---|
| Stub agent boots with `DEEP_QUANT_REQUIRE_IDENTITY=1` | ok, `/openapi.json` 200, "graph stubbed" logged |
| Durable checkpointer + session store come up | ok, `sessions.db` ready, 0 runs reconciled |
| `build:web` registers the route | ok, `/find-trade/session/[sessionId]/page` in the manifest |
| Alice creates a session through the proxy | **201**, server-minted `sess_01M1F03…` |
| No cookie | **401** |
| Alice reads her own session | **200** |
| **Bob reads Alice's session** | **404**, never 403 — no enumeration oracle |
| Bob's session list | `{"items":[]}` |
| Stub server suite | 5/5 |

That table is the strongest evidence produced in this migration so far: ownership isolation and the
full cookie → `/users/me` → HMAC → verification chain proven against running processes rather than mocks.

**Four defects the run found that reading could not have:**

1. `INTERNAL_IDENTITY_SECRET` must be **≥32 chars** — `assert_startup_config` refuses to boot below it.
   The first placeholder was 26 and the service died at import.
2. `INTERNAL_SERVICE_SECRET` is **also** required with enforcement on (the same guard covers the
   watcher's service credential). Omitting it refuses to start.
3. The identity stub needed **CORS**. `useAuthStore.checkAuth` runs in the BROWSER against
   `NEXT_PUBLIC_API_BASE_URL`, so it is a credentialed cross-origin call; without
   `Access-Control-Allow-Origin` (echoed, not `*`) and `…-Credentials: true` the browser blocked it, the
   auth gate failed, and every selector failed against a page snapshot of `auth.stratai.live`.
4. The `fq-desktop` Playwright project had no `testIgnore`, so it ran the **mobile** spec at desktop
   width. It failed, which was lucky — a viewport assertion that happened to hold at the wrong width
   would have passed and proved nothing.

**Where it stands.** The journey now gets much further, and driving it surfaced **a real product bug plus
two harness defects**:

1. **`fetchDeepAnalysis` never passed `session_id`, so the multi-session run path was never actually
   taken.** This is the find that justifies the whole job. `webAdapters.run_deep_quant_agent` dispatches
   on the PRESENCE of `session_id` (T6.2's `startSessionRun`), and the store's run action never supplied
   it — so with `NEXT_PUBLIC_FQ_MULTI_SESSION=on`, every FIND still took the legacy branch: a
   client-minted `thread_id`, **no session row, no `runs` row**, and frames arriving on a thread
   `useSessionStore` had never bound, which `applyFrame` correctly dropped into `unroutableFrames`. The
   transcript stayed empty and nothing said why. Diagnosed from the agent reporting `sessions=1, runs=0`.
   Fixed in `useQuantStore.fetchDeepAnalysis`, which now passes the active `session_id` when the flag is
   on and — critically — does **not** write the returned value into `currentThreadId` on that path,
   because the server returns a *session* id there and the thread is minted server-side.
   `src/store` + `src/lib/bridge`: 282/282 still green, so the legacy path is unchanged.
2. **The panel is behind "Show AI Agent panel", not "Quant Radar".** `DeepQuantPanel` renders only when
   `RightSidebar`'s `sidebarTab === 'deepquant'`; "Quant Radar" is a different panel. The suite failed
   against a snapshot of a fully authenticated terminal, which reads as a broken tab bar.
3. **The suite was order-dependent.** The tab bar lists every active session the user owns, so sessions
   left by an earlier run broke absolute counts like `toHaveCount(1)`. Fixed at both ends: the stub
   server now wipes its state directory on boot (`E2E_KEEP_STATE=1` to opt out), and the spec counts
   relatively. CI never saw this because it gets a clean checkout — running the suite twice locally did.

**Not yet green, and not claimed as such.** Verified state after these fixes: tsc clean, `build:web`
registers the route, stub-server suite 5/5, `src/store`+`src/lib/bridge` 282/282, and the curl-level
table above. The full browser journey has **not** been observed passing. `e2e` therefore stays out of
`ci-ok`'s `needs`.

### How far the journey gets, and the exact next step

**CORRECTION to an earlier note in this section:** it previously recorded "no `/api/deepquant/*` calls
were observed" as a fact. That was **wrong** — an artefact of the first `page.on('response')` recorder,
which only printed when `test.info().status !== expectedStatus` and in practice printed nothing at all.
A diagnostic that can silently produce no output is worse than none, because it invites exactly that
false conclusion. It now attaches its output unconditionally via `testInfo.attach`, and the very next
run showed the truth:

```
200 GET  /api/deepquant/sessions?status=active&limit=25
201 POST /api/deepquant/sessions            <- created, server-minted id
200 GET  /api/deepquant/sessions/sess_01M1F4M9…
200 GET  /api/deepquant/sessions/sess_01M1F4M9…/runs
```

**Confirmed working in the browser, against the real service:** session creation (201), the FIND run
streaming through the proxy (both "Scanning RELIANCE" and "Momentum is intact" render), the composer
unlocking on the terminal frame, the Q&A turn sending and its answer streaming into the chat, and a
second session being created and run. That is the `session_id` fix above proven end to end — before it,
none of this happened at all.

**Two more test defects found on the way, both of which had been passing for the wrong reason:**

- `waitForComplete` looked for the run-state text "Complete"/"Watching", which lives in
  `SessionWorkspace`'s header and therefore **does not exist in the side panel**. It now waits for the
  COMPOSER to enable, which is strictly stronger: the composer unlocks only at `watching`/`complete`
  **and** once a thread id is bound, so it proves the terminal frame was routed to this session rather
  than merely that some text appeared.
- The switch-back step clicked `getByRole('tab').nth(0)`. **Tab order is not stable** — the list is
  ordered by `updated_at DESC`, so running the second session moves it to the front, and the test was
  switching to the wrong session. The isolation assertion still passed, because both sessions stream the
  same canned script; only the Q&A turn (which exists in one of them) exposed it. Now selected by
  `[data-session-id="…"]`, so it is order-independent.

### FINAL MEASURED STATE — the suite is FLAKY, and that is the blocker

Across five clean runs the result oscillated: **4 passed/1 failed, then 2/3, then 3/2**, with *different*
tests failing each time and no code changing between some of them. So the remaining problem is not a
single broken assertion — it is **non-determinism**, and with `retries: 0` (chosen deliberately, so a real
intermittent routing bug cannot be retried into a green build) that means a red gate.

**Consistently passing:** ownership/not-found, the deep-link restore, and both 360 px mobile assertions
have each passed on clean state. **The journey** is the least stable.

**What is now known to be sound** (proven repeatedly, and by unit tests that do not flake):
`POST /sessions` 201, `POST /run` on the session path, frames streaming and rendering, the composer
unlocking, Q&A streaming into the chat, rehydration from stored frames, and per-user isolation.

**The flakiness is in the harness, not obviously in the product.** Three causes were found and fixed while
chasing it, each of which had produced a misleading failure:

- `expandAllThinking` captured `locator.count()` once and clicked by stale index; now re-queries per
  iteration and asserts `aria-expanded` flipped.
- `count()` does not wait, so a freshly opened session reported 0 groups; now `expect.poll`.
- Sessions are per-user and the agent database outlives a run, so tests sharing an identity counted each
  other's tabs; each test now gets its own user (`tokenForTest`), and the identity stub derives a user from
  any `e2e-*` token.

**Remaining suspected cause of the residual flake:** the per-test token derives from the test TITLE, so it
is stable *across runs*. Within a run that isolates tests; across runs the same user accumulates sessions
unless the agent's state directory is cleared. CI gets a fresh checkout so this cannot bite there, but it
makes local repetition unreliable — and it means a rerun on the same machine is not a clean experiment.
**Add a per-run nonce to the token** (e.g. an env var set by the CI step and by the local runner) to make
the isolation total.

⚠️ **`_wipe_state()` does not actually work.** It is called before `import main` under a `__name__` guard,
which is the right place, yet the database still showed 31 sessions from prior runs; deleting
`.e2e-state/` by hand was what produced a clean run. Do not trust it — either fix it or have the CI step
`rm -rf` the directory explicitly before starting the agent.

**Verdict: T10.2 is NOT done.** The infrastructure is real and verified, 3–4 of 5 specs pass, and the job
must stay out of `ci-ok` until it is deterministic.

---

**Earlier reading, kept for the record — `fq-desktop`: 1 passed, 2 failed** (two independent fresh runs,
distinct server-minted session ids each time, so these were real readings and not the stale-terminal
artefact).

**PASSES: `a session belonging to someone else is not found`.** Ownership isolation now proven *in a
browser* end to end: Alice creates a session, Bob deep-links to it by id, and gets the not-found page with
no wording that distinguishes "not yours" from "does not exist". Combined with the curl table above, that
is the security property of this migration verified against a running stack.

**FAILS: both remaining tests, on the SAME assertion** — `getByText(/Momentum is intact/)` after
`expandAllThinking(page)`:

- `the whole journey` — after switching back to session one (its run finished).
- `a deep link restores a session with nothing in memory` — after rehydrating a completed run.

Both paths have in common that the session is **not currently streaming**. While a run is live the same
text renders fine (asserted earlier in the journey and passing). So:

> **The first reasoning message renders, the second does not, on a completed session.** Reproduced via two
> independent code paths (in-memory switch-back and a from-scratch rehydration), which makes a test
> artefact unlikely — a spec bug would not manifest identically through both.

Ruled out already: the collapsed-group theory (every `Thinking` toggle is now expanded, and
`ThinkingGroupRenderer` gained the `aria-expanded` it was missing so the helper can tell open from
closed); and tab ordering (selection is by `data-session-id`).

**Suspect ELIMINATED by inspection — `AgentTerminal`'s grouping.** The loop pushes a trailing
`thinking_group` *after* it ends (`if (currentThinkingGroup.length > 0)` following the `for`), so a
completed run's final group is emitted. The second message also cannot be misclassified as a decision:
`isJsonDecision` requires an empty `cleanContent`, and "Momentum is intact above 2,450." has none. So the
renderer is not dropping it.

**The DATA side is now ELIMINATED too**, by `lib/fq/__tests__/replayCannedRun.test.ts` (6 tests, green).
It replays the **exact** frame sequence the stub emits — dumped from `e2e_stub_server.py` driving the real
`/run`, so it tracks the canned script and the SSE assembler rather than an invented fixture:

```
1 RUN_STARTED   2 REASONING "Scanning RELIANCE…"   3 REASONING "Pulling candles…"
4 TOOL_CALL_START get_ohlc   5 TOOL_CALL_RESULT   6 TOOL_CALL_END
7 REASONING "Momentum is intact above 2,450."   8 DECISION   9 RUN_FINISHED
```

Proven: `replayEvents` keeps the post-tool reasoning, keeps all three messages **in order**, keeps the tool
step unflattened, and `reconcileWithRun` on the `complete` branch preserves everything while setting
`sessionStatus: 'complete'` and `isAnalyzing: false`. So the text the e2e cannot see **is in the store**.

**Both halves are therefore ruled out — it is neither persistence/replay nor the grouping loop.** What
remains is the narrow gap between them: **the expansion of the SECOND `Thinking` group in a live browser.**
The transcript renders two groups (reasoning, tool boundary, reasoning), and `expandAllThinking` is
supposed to open both. Candidates, cheapest first:

1. `toggles.count()` is read once, then clicking re-renders the list — indices can shift under the loop.
   Re-query per iteration, or click by `nth` derived from a fresh locator each time.
2. The second group may be outside the scroll viewport; `click()` auto-scrolls, but `aria-expanded` should
   be asserted **after** each click to prove the toggle actually flipped.
3. Assert the group count first (`expect(toggles).toHaveCount(2)`) — if only one exists in the browser, the
   render differs from the unit-test expectation and that is the real bug.

Doing (3) first distinguishes "two groups, one wouldn't open" from "only one group rendered".

**Then, once green:** add `e2e` to `ci-ok`'s `needs`, and do the plan's teeth check — temporarily
reinstate T6.2's active-session fallback and confirm the isolation assertion fails.

**Fixed in the product while getting here:** `ThinkingGroupRenderer`'s toggle had no `aria-expanded`, so a
screen-reader user heard "Thinking, button" with no indication whether the reasoning was showing — the
chevron conveying it is decorative. Added, with the chevrons marked `aria-hidden`.
`src/components/quant`: 145/145 still green.

⚠️ **A self-inflicted regression, caught by running the full suite — worth recording because the failure
mode is easy to repeat.** The first version of `e2e_stub_server.py` did two things at IMPORT time: wiped
its state directory, and (via the test fixture calling `install_stub()` directly) replaced
`graph_module.compile_with`, `graph_module.graph` and `main.set_run_llm_credentials`. Those are
module-level mutations with no teardown, so once `tests/test_e2e_stub_server.py` was collected, **every
later test in the session got the canned graph** — python went from 23 failed to **56 failed**, and the
new failures looked like breakage in `test_turn_kind_stamp` and `test_interaction_log` rather than
contamination from a fixture.

Two rules came out of it, both now enforced in the code:
- **Import must be inert.** The wipe moved into `_wipe_state()`, called only from `main_entry()`.
- **A fixture must restore what it replaces.** The client fixture uses `monkeypatch.setattr` for all three
  globals; the one test that must call `install_stub()` for real captures and restores them.

Baseline restored and verified: **23 failed / 2005 passed**, the same 16 files as the recorded baseline
(the Hypothesis `'\x00'` config-property class plus the QA tests), with +5 net tests from this work.

⚠️ **Tooling hazard that cost two false readings in this task.** Reusing a shell for a second Playwright
run returns the PREVIOUS run's buffered output, so a stale result reads as a fresh one — twice this was
mistaken for "the fix did nothing". Always run the suite in a NEW shell, and cross-check the session id in
the `agent-calls` attachment: an identical id across two runs means you are reading the old run.

Note also that `next build` reports `Environments: .env.local` — a developer machine has a local env file
that can override the `NEXT_PUBLIC_*` values the fixture depends on. CI has none, so this affects local
reproduction only, but rule it out before trusting a local red result.

**Still outstanding from the plan.** "It must fail when T6.2's fallback is reinstated" — not verified,
because the journey does not pass yet. That check is what proves the job has teeth and must be done
before wiring it into `ci-ok`.

**Rollback.** Remove the job.

**Acceptance.** The job runs and passes on a PR; `ci-ok` includes it in `needs`.

---

# PHASE 11 — Remove legacy

## T11.1 — Retire the legacy paths

**Files.** `main.py`, `webAdapters.ts`.

**Changes.** Set `DEEP_QUANT_REQUIRE_SESSION=1`, `DEEP_QUANT_REQUIRE_IDENTITY=1`,
`DEEP_QUANT_SESSIONS_ENABLED=1`, `DEEP_QUANT_PERSIST_STREAM=1` and `FQ_REQUIRE_IDENTITY=1` in
`docker-compose.prod.yml`. Flip `FQ_REQUIRE_IDENTITY` and `DEEP_QUANT_REQUIRE_IDENTITY` **together** —
`FQ=1` with `DQ=0` leaves the backend accepting body `user_id`, and `FQ=0` with `DQ=1` breaks every
agent call whose identity did not resolve. Delete
the client-`thread_id` branch from `/run`, `/qa`, `/cancel`. `thread_id` remains the wire key for
`/stream/{thread_id}` and `/resume` — **the watcher contract does not change.**

**Dependencies.** all prior phases green in production for one full trading day.

**Tests.** the whole suite; the T4.2 watcher regression test is the gate.

**Rollback.** Flip the four flags back off.

**Acceptance.** FIND / VERIFY / QA / RESUME / CANCEL / WATCH verified manually against production
before the legacy branches are deleted.

## T11.2 — Dead code

**Files.** as listed in design §13, and nothing else.

**Changes.** Remove `agentStatus` + its listener + the unrendered `LoadingState` import;
`parseInlineMarkdown`/`AnswerText` leftovers in `QaMessages.tsx`; the `hydrateLegacyAgentBridge` →
`agentChatLog`/`finalTradePlan` path; `run_deep_quant_analysis`; unused imports.

**Plus the flat mirror, moved here from T7.3.** `projectSession`, `sessionsByKey`, `activeViewKey`,
`_streamingKey`, `_threadToKey`, `activateSymbolSession`, `clearQa`, `askQuestion` and the 12 flat
fields could not be deleted in T7.3 without blanking the panel for everyone still on the legacy path.
With the flag permanently on, deleting them is: remove the `FQ_MULTI_SESSION` branch from each hook in
`useFqSession.ts`, then remove whatever that leaves unreferenced. No component call site changes —
that was the point of routing every read through one layer.

**Dependencies.** T11.1.

**Tests.** `lib/bridge/__tests__/coverage.test.ts` must stay green (removing an adapter requires
removing it from exactly one classification table). Full frontend suite.

**Rollback.** `git revert`.

**Acceptance.** `npx tsc --noEmit` clean; `npx vitest run` shows no new failures beyond the 9 known.

---

# Acceptance-criteria traceability

| Requirement | Proven by |
|---|---|
| Sessions are real backend entities with opaque ids | T3.1, T4.1 |
| Runs distinct from sessions | T3.1, T5.3 (+ the `reco_store` uniqueness assertion) |
| Messages persistent | T3.1, T5.1 |
| Ownership authenticated | T1.3, T1.4, T4.1, T4.2 |
| Compliance separate | T3.1 (file-hash assertion), T5.4 |
| LangGraph state survives restart | T2.1 |
| Persistence actually durable | T1.1, T1.2 |
| Same symbol, multiple sessions / timeframes | T3.1, T6.1 |
| Session A cannot receive B's events | T6.1 (Hypothesis), T6.2, T10.2 |
| Q&A session-safe, concurrent runs isolated | T6.1, T6.3 |
| Tabs / new / switch / archive / rename / history / reopen / URL | T8.1, T8.2, T9.1, T10.1 |
| Streaming state persists & reconciles; truncated identifiable | T5.1, T2.2, T7.2 |
| Q&A context survives reload and restart | T7.2, T2.1 |
| FIND / VERIFY / WATCH / RESUME / CANCEL still work | T4.2 regression test, T11.1 manual gate |
| Event ordering unchanged | existing `test_stream_events*` suites, unmodified |
| Cross-user access tests pass | T4.1 matrix, T4.2 |
| E2E actually runs in CI | T10.2 |
| No known critical data-loss path | T5.1 disconnect/error/cancel cases, T5.2 replay, T2.2 reconciliation |

## What this plan does NOT claim

- It does not claim durability until T1.1 is deployed and a restart has been observed to preserve
  `/data`.
- It does not claim secure authentication until T1.4 + T11.1 are live with
  `DEEP_QUANT_REQUIRE_IDENTITY=1`; before that, identity is still client-supplied.
- It does not claim multi-session isolation until T6.1's concurrent-stream property tests and T10.2's
  E2E both run green.
- It does not claim horizontal scalability. deep-quant remains single-replica by design and by
  startup guard.
