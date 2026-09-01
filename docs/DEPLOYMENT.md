# Deployment Guide — Oracle Cloud ARM (Ampere A1) Beta

This guide deploys the **shared backend data plane** for a ~10-user beta on an
Oracle Cloud **Always Free Ampere A1** instance (ARM64). Beta users run the
packaged Tauri desktop app locally, pointed at this server's public IP
(**Topology A**). The reasoning plane (Python `deep-quant-loop` + the Rust tool
server embedded in the Tauri binary) runs client-side in the desktop app and is
**not** hosted here — see "Open decision" at the bottom.

---

## 0. Security: rotate a leaked secret first (REQUIRED)

`bedrock-api-key.txt` is **committed to git history**. `.gitignore` does not
untrack an already-committed file. Before deploying anywhere:

1. **Rotate/revoke** that AWS Bedrock key in your AWS console — assume it is compromised.
2. Remove it from tracking (kept on disk, now ignored by `.gitignore`/`.dockerignore`):
   ```bash
   git rm --cached bedrock-api-key.txt
   git commit -m "chore: stop tracking bedrock key file"
   ```
3. To purge it from history entirely, use `git filter-repo` or BFG (separate, deliberate operation).

`.env` is correctly untracked. Never commit real secrets; the new `.dockerignore`
also keeps `.env` and key files out of image build contexts.

---

## 1. Provision the instance

- **Shape:** VM.Standard.A1.Flex — allocate up to **4 OCPU / 24 GB RAM** (all of it, one instance).
- **Image:** Ubuntu 22.04 (ARM64) or Oracle Linux 9 (ARM64).
- **Boot volume:** default 50 GB is fine; bump to ~100 GB if you archive lots of QuestDB history.
- **Capacity note:** free A1 shapes are frequently "out of capacity" in busy regions. Retry, or pick a different availability domain/region.

## 2. Install Docker + compose plugin

Ubuntu:
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker
```

## 3. Open the firewall — BOTH layers (Oracle gotcha)

Oracle has two independent firewalls; you must open both or connections silently hang.

**A. Cloud layer** — VCN → Security List (or an NSG) → add Ingress rules (Source `0.0.0.0/0`, TCP):
| Port | Purpose |
|---|---|
| 8080 | Aggregator decision WebSocket |
| 8081 | Alpha-terminal OHLC WebSocket |
| 8082 | Predictive (Ghost Line) WebSocket |
| 8083 | Quant-RAG insight WebSocket |
| 8085 | Ingestion TCP control (subscription diffs from desktop) |
| 8087 | Kite OAuth token exchange (only if clients use it; else omit) |
| 3001/3002 | auth/payment — only if you run `--profile platform` |

Do **NOT** open 9000/8812/9009 (QuestDB), 19092 (Kafka), 5432/6379 — they are bound to localhost in the compose and reached via SSH tunnel only.

**B. Instance layer** — Oracle images ship restrictive iptables. On Ubuntu:
```bash
sudo iptables -I INPUT -p tcp -m multiport --dports 8080:8085,8087 -j ACCEPT
sudo netfilter-persistent save
```
(Oracle Linux: use `firewall-cmd --add-port=8080-8085/tcp --permanent` then `--reload`.)

## 4. Configure environment

```bash
git clone <your-repo> && cd Ai-trader
cp .env.example .env
nano .env   # fill in the checklist below
```

### .env checklist (deployment)
Required for the data plane to function:
- `KITE_API_KEY`, `KITE_API_SECRET`, and `KITE_ACCESS_TOKEN` (or `KITE_REQUEST_TOKEN`) — single shared Zerodha session feeds all beta users. Access token resets ~midnight IST; plan a daily refresh.
- `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL` — OpenAI-compatible endpoint (used by quant-rag + sentiment).
- `NEWSDATA_API_KEY` — sentiment agent news source (`FINNHUB_API_KEY` optional).

Set for a hosted deployment (defaults in `.env.example` point at localhost and are overridden by the compose for internal networking, but set these for clarity):
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — used by postgres + auth/payment `DATABASE_URL`.

Only if running `--profile platform` (auth/payment):
- `JWT_SECRET`, `INTERNAL_API_KEY`, and (payment) `PHONEPE_MERCHANT_ID`, `PHONEPE_SALT_KEY`, `PHONEPE_SALT_INDEX`.

> The compose overrides host-specific vars (`KAFKA_BROKERS=redpanda:29092`,
> `QUESTDB_POSTGRES_URL=...@questdb:8812/qdb`, `REDIS_URL=redis://redis:6379`)
> for the internal network — you do not set those to `localhost` for the server.

## 5. Build & launch

First build compiles Rust from source (rdkafka via CMake) on ARM — expect
**15–40 min** on the first run; subsequent builds are cached.

```bash
# Core data plane (recommended for the beta):
docker compose -f docker-compose.prod.yml up -d --build

# With auth + payments:
docker compose -f docker-compose.prod.yml --profile platform up -d --build
```

## 6. Verify

```bash
docker compose -f docker-compose.prod.yml ps           # all 'running'/'healthy', kafka-init 'exited (0)'
docker compose -f docker-compose.prod.yml logs -f ingestion   # Kite connect + tick publish
# Kafka topics (via localhost tunnel):
docker exec stratai-redpanda rpk topic list --brokers redpanda:29092
# QuestDB console over SSH tunnel from your laptop:
#   ssh -L 9000:127.0.0.1:9000 ubuntu@<server-ip>   then open http://localhost:9000
```

Point a desktop client's server config at `ws://<server-ip>:8080` / `:8081` / `:8082` / `:8083`
and control port `<server-ip>:8085`.

## 6.5 The web app — app.stratai.live

The Next.js terminal runs as the `frontend` service and is fronted by the same
Caddy container as the data plane (`infra/caddy/Caddyfile`, second vhost). It holds
**no gateway credential**: its `environment:` block points each upstream at an
internal service name, so its same-origin `/api/*` route handlers reach
aggregator / QuestDB / deep-quant / tool-server / sentiment over the `stratai`
network and never traverse the public gateway.

**Prerequisites, both outside this repo and both able to break the launch:**

1. **DNS** — an A record for `app.stratai.live` → the droplet. Caddy provisions the
   TLS certificate on first request; until the record resolves, ACME cannot
   validate and the site will not serve HTTPS.
2. **CORS on the auth deployment** — `api-web.stratai.live` must add
   `https://app.stratai.live` to its allowlist. Every login call is a cross-origin
   `fetch` from the browser, so without this the preflight fails and **nobody can
   log in**, while the rest of the app looks fine. This is the single most likely
   launch blocker and it cannot be fixed from this tree.

```bash
# Build + start just the web tier (the Caddy container needs no rebuild — it
# re-reads the Caddyfile bind mount on restart):
docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml build frontend
docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml up -d frontend
docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml restart questdb-gateway
```

Verify — the last check is the important one:

```bash
curl -sI https://app.stratai.live/ | head -1                    # 200
curl -s  https://app.stratai.live/api/features                  # {"enforced":…}
curl -s 'https://app.stratai.live/api/kite/quote?i=NSE:TCS'     # real quote, same-origin

# The credential MUST still be required when the gateway is hit directly. If this
# returns data without -u, the web tier is leaking the shared password.
curl -sI https://app-api.stratai.live/kite/quote?i=NSE:TCS | head -1   # expect 401
```

> **Memory.** The container is capped at 192m in `docker-compose.8gb.yml` — measured,
> not guessed (idles at 38 MiB; 41 MiB under 60 concurrent page loads). The tight
> resource is the **build**, not the runtime: `redeploy.sh` builds on the droplet and
> `next build --turbopack` needs far more than 192 MB. With ~1 GB free and no swap,
> that build is the likeliest OOM in the stack, and the kernel may kill QuestDB
> instead of the build. Building the image in CI and pulling it here removes the
> problem; see the note above the build loop in `redeploy.sh`.

## 7. Operations

- Logs: `docker compose -f docker-compose.prod.yml logs -f <service>`
- Restart one service: `docker compose -f docker-compose.prod.yml restart <service>`
- Update: `git pull && docker compose -f docker-compose.prod.yml up -d --build`
- Stop: `docker compose -f docker-compose.prod.yml down` (add `-v` to wipe QuestDB/PG/Redis volumes)
- Memory: total `mem_limit` budget ≈ 9.5 GB of 24 GB. QuestDB (4 GB) is the largest; watch it under history backfills.

### 7.1 deep-quant durable state — `/data` (the `deep_quant_data` volume)

`deep-quant` owns **four SQLite databases**. They used to be written beside the module inside the
image, i.e. into a container layer, so **every redeploy destroyed them** — including
`compliance.db`, whose append-only hash chain silently restarted from genesis each time. They are
now on a named volume at `/data`.

| File | Contents | Retention |
|---|---|---|
| `/data/compliance.db` | `interaction_log` (P5) + `recommendations` (P2) — hash-chained, append-only, UPDATE/DELETE blocked by triggers | **5 years (SEBI). Never prune. Never repair in place.** |
| `/data/sessions.db` | Find Quant Trade sessions / runs / messages / run_events | app data; `run_events` pruned per `RUN_EVENTS_RETENTION_DAYS` |
| `/data/checkpoints.db` | durable LangGraph checkpoints (Q&A grounding, paused watch runs) | transient-but-durable; safe to lose at the cost of ungrounded Q&A on old threads |

**Confirm the checkpointer is durable, not in-memory.** The service starts either way, so
the log is the only signal:

```bash
docker compose -f docker-compose.prod.yml logs deep-quant | grep '\[checkpointer\]'
# want: "ok durable LangGraph checkpoints at /data/checkpoints.db"
# bad:  "!! IN-MEMORY checkpoints (...)"  -> /qa answers ungrounded and /resume 400s
#                                            after every redeploy
```

**Checkpoint deserialisation hardening.** The checkpoint used to be process memory; it is
a file now, so `langgraph-checkpoint` will deserialise whatever types it finds in it.
`LANGGRAPH_STRICT_MSGPACK=true` restricts that to an allowlist. It is deliberately **not
on yet**, because strict mode *blocks* unlisted types and enabling it blind could break
Q&A grounding on real graph state. Do it observation-first:

```bash
# 1. Run normally for a few real FIND + Q&A cycles, then look for advisories:
docker compose -f docker-compose.prod.yml logs deep-quant \
  | grep -E 'Blocked deserialization|LANGGRAPH_STRICT_MSGPACK'
# 2. If clean, set LANGGRAPH_STRICT_MSGPACK=true in the deep-quant environment.
# 3. If a legitimate type is named, add it to an explicit allowlist instead.
```
| `/data/trade_journal.db`, `/data/telemetry.db` | outcome scoring, measurement | app data |

**Verify the mount after any deploy.** A missing mount is silent data loss, so check the startup
report rather than assuming:

```bash
docker compose -f docker-compose.prod.yml logs deep-quant | grep -i 'state\|WARN'
# Expect four paths under /data and NO "not on a mounted volume" warning.
docker exec stratai-deep-quant ls -la /data
docker volume inspect stratai_deep_quant_data
```

**Backup.** These run in WAL mode, so **`cp` / `tar` of a live file is NOT a valid backup** — it can
capture a torn page set with an unapplied WAL. Use SQLite's own online backup:

```bash
docker exec stratai-deep-quant sh -c '
  mkdir -p /data/backup
  for db in compliance sessions trade_journal telemetry; do
    sqlite3 /data/$db.db ".backup /data/backup/$db-$(date +%F).db"
  done'
docker cp stratai-deep-quant:/data/backup ./dq-backup-$(date +%F)
```

`checkpoints.db` is deliberately excluded — it is execution state, not a record.

**Restore.** Stop the service first (a restore into a live WAL database corrupts it):

```bash
docker compose -f docker-compose.prod.yml stop deep-quant
docker cp ./dq-backup-<date>/compliance.db stratai-deep-quant:/data/compliance.db
docker compose -f docker-compose.prod.yml start deep-quant
# Then verify the chain is intact — a restored compliance.db that fails this is
# evidence of tampering or a torn backup, and must not be quietly accepted:
docker exec stratai-deep-quant python -c "import hashchain,interaction_log,reco_store; \
  print('interactions', hashchain.verify_chain(hashchain.connect(), interaction_log.TABLE)); \
  print('recommendations', hashchain.verify_chain(hashchain.connect(), reco_store.TABLE))"
```

**`docker compose down -v` now destroys the compliance record too.** Take a backup first.

### 7.2 deep-quant is single-replica by design

`_CANCELLED`, `_SUBSCRIBERS` (the watcher re-attach hub), the SQLite writer, and the SQLite
checkpointer are all process-local. Two replicas would mean `/cancel` on replica B never stops a run
on replica A, and a watcher `/resume` on A never reaches a browser attached to B. Do **not** add
`deploy.replicas` or a second container. The service refuses to start with
`DEEP_QUANT_ALLOW_MULTI_REPLICA` unset unless it is genuinely single-replica; scaling out requires
moving the store to Postgres and the hub to Redis first.

## 8. Expected load for 10 users

- **Market data is shared**, not per-user: one Kite session → one tick stream. User count does not multiply ingestion load.
- **CPU:** heavy LLM reasoning is offloaded to your external LLM API. The box mostly orchestrates + serves WebSockets. 4 OCPU is comfortable for 10 users.
- **Real pressure points:** QuestDB memory during historical backfills, and concurrent QuestDB reads. This sizing handles a 10-user beta with headroom.

---

## Open decision — reasoning plane (deep-quant + tool server)

The Rust tool server (port 8084) the Python agent calls is **compiled into the
Tauri desktop binary** (`frontend/src-tauri/src/quant/tool_server.rs`), and the
live-data UI path uses Tauri IPC. So:

- **Topology A (this guide):** deep-quant + tool server run client-side in each
  user's desktop app. Nothing extra to host. Recommended for the beta.
- **Topology B (full web):** to serve a browser with no desktop app, the tool
  server must be extracted into a standalone service (new Dockerfile), the
  Python `deep-quant-loop` must be hosted (new Dockerfile), and the frontend
  decoupled from Tauri IPC. This is a real engineering effort, not packaging.

Tell me which topology you want and I'll finish the reasoning-plane packaging
(task 6) accordingly.
