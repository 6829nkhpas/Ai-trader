# Ai-trader ("Strat Ai") — repo notes for agents

## Working rules (owner's constraints)

- **Do not run tests, linters or typecheckers** — not `cargo test`, `pytest`, `npm test`, `cargo clippy`, `tsc`. The commands below are reference for when you are explicitly asked to run one, not a verification step to perform on your own. Verify by reading the code.
- **Do not run git without asking.** Read-only inspection (`status`, `log`, `diff`, `show`) is fine; anything that writes — `add`, `commit`, `push`, `reset`, `checkout`, `stash` — needs permission first. Remember a push to `main` is a production deploy (below).
- **KISS.** Smallest change that solves the stated problem. No refactors, abstractions, extra files or defensive scaffolding nobody asked for.

## What is and isn't in this repo

Rust + Python + Node microservices for an Indian-market (NSE/NFO via Zerodha Kite) trading terminal. **Backend / data plane only.**

- **The frontend is not here.** `.gitignore:13` ignores `frontend/` and the directory does not exist. `frontend-src/` is an untracked **separate clone** of `github.com/yash-rana0101/strat-app-frontend` (deployed on Vercel) with its own `.git` — never commit it into this repo, and `cd frontend-src` before running anything meant for it.
- **Auth, payments and credits are a separate deployment** (`api-web.stratai.live`, prefix `/api/v1`); the sign-in surface is `auth.stratai.live` (another repo). No code, nothing to start locally — failures there are debugged from its logs.
- References to a **Tauri desktop app** (`azure-pipelines.yml`, `README.md`, `docs/DEPLOYMENT.md`, the root `package.json` scripts) describe deleted code. Trust config and scripts over prose.

## Commands

No root build system and **no Cargo workspace** — each crate has its own `Cargo.lock`, so `cd` into it first.

| Task | Command |
|---|---|
| One Rust crate | `cd <crate> && cargo check --locked && cargo test --locked` |
| Rust without CMake (e.g. Windows) | add `--no-default-features` — drops the `kafka` feature, whose `rdkafka` builds librdkafka via CMake |
| Python agent, full suite | `cd agents/deep-quant-loop && python -m pytest -q` (~15-20 min, 477 test files) |
| Python agent, one file | same dir: `python -m pytest tests/<file> -q` |
| Sentiment agent | `cd agents/sentiment && npm install && npm test` (node:test) |
| Whole stack | `bash deploy.sh` = `docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml build … && up -d` |
| Local infra only | `docker compose up -d` (base `docker-compose.yml`: Redpanda, QuestDB, Redis + a few services) |

- Root `package.json` scripts (`test:rust`, `test:e2e`) point into the deleted `frontend/` — they cannot run.
- CI builds these crates: `aggregator`, `ingestion`, `alpha-terminal`, `tool-server`, `quant-core`, `service-metrics`, `agents/{predictive,technical,quant-rag}`. `status-api` is not in CI; `tools/load_tester` does not compile.
- `protoc` is **not** required: `protoc-bin-vendored` supplies it and each `build.rs` compiles `shared_protos/*.proto` into `$OUT_DIR`. Editing a proto means rebuilding every crate that includes it.
- Run pytest **from inside `agents/deep-quant-loop`**: there is no pytest config, and `tests/conftest.py` is what puts the service dir on `sys.path`.

## pytest is red on a clean tree — it is not your change

If you are ever asked to run the Python suite, take a baseline first. Both CI and a local run fail out of the box (19 failures in CI, 26 locally on Windows/py3.11 — the sets do not match):

- config-resolution property tests die on `ValueError: embedded null character` — Hypothesis writes `\x00` into `os.environ`.
- `tests/test_trade_qa.py` and `tests/test_qa_always_answers_bug.py` make **real LLM calls** → 401 against the placeholder key `conftest.py` sets.
- some options / interaction-log assertions genuinely fail.

## Branching and deploy — every push to `main` is a production deploy

- `main` is the only branch. Nothing is enforced server-side (private repo on a free plan, protection + ruleset APIs both 403). Discipline is the whole mechanism; details in `CONTRIBUTING.md`.
- `deploy-server.yml` SSHes into the VM and runs `redeploy.sh` on every push touching server paths. `ci.yml` fires on the **same event, concurrently — it does not gate**. A red CI means the bad commit is already live, so verify before pushing.
- Real deploy target, from the repo's Actions variables (the comments in `deploy-server.yml` and `docs/DEPLOYMENT.md` are stale — they name a DigitalOcean droplet and Oracle Cloud): `DEPLOY_HOST=8.234.73.219`, `DEPLOY_USER=stratai`, `DEPLOY_PATH=/opt/stratai/Ai-trader` — the GCP Mumbai VM described in `infra/gcp/`.
- `redeploy.sh` needs `GITHUB_TOKEN` (private repo), does `git reset --hard origin/$DEPLOY_BRANCH` on the box (untracked `.env` survives), rebuilds 9 services sequentially, `up -d`, then `caddy validate`s before restarting the gateway — `caddy reload` is impossible because the Caddyfile sets `admin off`.
- **Deploy blind spots:** `service-metrics/` and `status-api/` are absent from `deploy-server.yml`'s path filter, and `status-api` is absent from the build loop in `redeploy.sh`/`deploy.sh`. Changing either looks deployed and is not.
- CI's `frontend` and `e2e` jobs still reference `frontend/`. Touching `agents/deep-quant-loop/**` triggers `e2e`, which dies immediately at `setup-node` ("Some specified paths were not resolved" — `frontend/package-lock.json`). `ci-ok` accepts only success/skipped, so CI goes red for reasons unrelated to your change.
- The `frontend` compose service was removed, but the `app.stratai.live` vhost in `infra/caddy/Caddyfile` still `reverse_proxy frontend:3000` as an on-box fallback that cannot resolve. Live traffic is Vercel.

## Services and ports

| Path | Lang | Role | Ports |
|---|---|---|---|
| `ingestion/` | Rust | Kite WS ticks → Kafka + QuestDB; `option_sink.rs` writes `option_ticks` / `option_chain_snapshots`; control port takes `option_chain_set:{json}` | 8085 (control) |
| `aggregator/` | Rust | decision fusion, decisions WS, Kite REST proxy (`kite_api.rs`), `option_chain_selector.rs` pushes the chain band to ingestion every 60s | 8080 WS, 8087 REST |
| `alpha-terminal/` | Rust | OHLC aggregation, predictive V2 | 8081 |
| `agents/predictive/`, `agents/quant-rag/`, `agents/technical/` | Rust | signals WS, insights WS, indicator math | 8082, 8083, — |
| `agents/sentiment/` | Node ESM | news → LLM score → Kafka | 8090 |
| `agents/deep-quant-loop/` | Python FastAPI + LangGraph | the LLM agent, F&O analytics, sessions; entry `main.py` | 8086 |
| `tool-server/` | Rust | quant tools over `quant-core`; **every route is under `/tools`** | 8084 |
| `quant-core/` | Rust lib | shared indicator/pattern math (pure, proptested) | — |
| `service-metrics/` | Rust lib | shared `/metrics` `/health` `/ready` + work heartbeat; path dep of every service | 9101-9110 |
| `status-api/` | Rust | fleet status for the admin panel; API and metrics share one port | 9110 |
| `infra/caddy/Caddyfile` | Caddy | the `app-api.stratai.live` gateway | 443 |
| `shared_protos/` | proto | cross-service contracts | — |
| `backend/` | — | two leftover QuestDB migration files, not a service | — |

QuestDB is the only database (REST 9000, PG wire 8812). Kafka is Redpanda (19092 external, 29092 internal).

Gateway shape: `/ws/*` is the one open prefix. `/questdb` `/deepquant` `/kite` `/tools` `/sentiment` `/prometheus` `/grafana` `/status` sit behind basic auth and emit **no CORS headers**, so a browser cannot call them cross-origin — the frontend's own same-origin `/api/*` handlers hold the credential. `/tools` and `/grafana` use `handle`, not `handle_path`: the prefix is kept deliberately, and stripping it 404s.

## Environment

- Every Rust service calls `dotenvy::dotenv()` and 12 compose services share `env_file: .env`, so **one variable name is read by everything**. That is why `METRICS_PORT` is set per service in `docker-compose.prod.yml` — a stray value in `.env` collapses the whole fleet onto one port.
- `.env` is gitignored; `.env.example` (330 lines) is the reference. Do not print secret values back.
- Kite access tokens expire 06:00 IST daily and the 2FA login cannot be automated. `python scripts/generate_kite_tokens.py` does the exchange, writes the local `.env`, pushes to the server and restarts the consumers; `--no-deploy` for local only, `--deploy-only` to recover a half-finished run (a `request_token` is single-use).
- Feature switches are **unprefixed and server-side** (`FEATURE_ENFORCEMENT`, `ENABLE_*`); `FEATURE_ENFORCEMENT` unset means "local dev, everything unlocked". The only binding per-user gate is `agents/deep-quant-loop/entitlements.py`.

## Conventions

- Comments here are unusually long on purpose: they record *why* something is the way it is and what was measured. Don't strip them while editing nearby code, and write commit messages the same way.
- `.gitignore` deliberately has **no `*.db` rule** — only named files. A new SQLite store shows up in `git status` ready to be committed by accident. `compliance.db` must never be committed: append-only regulatory record, 5-year SEBI retention.
- Shell is bash on Windows; the tree is CRLF and git's "LF will be replaced by CRLF" warning is harmless.
- `CONTRIBUTING.md` and `CLAUDE.md` are current. `CLAUDE.md` is the deepest reference, but its §3-§9 describe the frontend that now lives in the other repo. `docs/STATE.md`, `docs/MASTER_CONTEXT.md` and `docs/DEPLOYMENT.md` are from the Tauri/DigitalOcean era.
- `.mcp.json` points the code-review-graph server at `D:\Strat Ai\Ai-trader`, which does not exist in this checkout (neither does `.code-review-graph/` or `graphify-out/`). Until it is repointed, the graph tools described below — and the `graphify` rules in `.agent/rules/` — cannot answer anything; use Grep/Glob/Read.

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

<!-- Ponytail Skill -->

# Ponytail, lazy senior dev mode

You are a lazy senior developer. Lazy means efficient, not careless. The best code is the code never written.

Before writing any code, stop at the first rung that holds:

1. Does this need to be built at all? (YAGNI)
2. Does it already exist in this codebase? Reuse the helper, util, or pattern that's already here, don't re-write it.
3. Does the standard library already do this? Use it.
4. Does a native platform feature cover it? Use it.
5. Does an already-installed dependency solve it? Use it.
6. Can this be one line? Make it one line.
7. Only then: write the minimum code that works.

The ladder runs after you understand the problem, not instead of it: read the task and the code it touches, trace the real flow end to end, then climb.

Bug fix = root cause, not symptom: a report names a symptom. Grep every caller of the function you touch and fix the shared function once — one guard there is a smaller diff than one per caller, and patching only the path the ticket names leaves a sibling caller still broken.

Rules:

- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Pick the edge-case-correct option when two stdlib approaches are the same size, lazy means less code, not the flimsier algorithm.
- Mark deliberate simplifications that cut a real corner with a known ceiling (global lock, O(n²) scan, naive heuristic) with a `ponytail:` comment naming the ceiling and upgrade path.

Not lazy about: understanding the problem (read it fully and trace the real flow before picking a rung, a small diff you don't understand is just laziness dressed up as efficiency), input validation at trust boundaries, error handling that prevents data loss, security, accessibility, the calibration real hardware needs (the platform is never the spec ideal, a clock drifts, a sensor reads off), anything explicitly requested. Lazy code without its check is unfinished: non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; no frameworks, no fixtures). Trivial one-liners need no test.

(Yes, this file also applies to agents working on the ponytail repo itself. Especially to them.)
