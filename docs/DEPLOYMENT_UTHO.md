# Migration: DigitalOcean → Utho (16 GB)

Runbook for moving the Strat Ai data plane off the DigitalOcean `s-4vcpu-8gb`
droplet onto a 16 GB Utho cloud instance, motivated by ~₹10,000 of Utho credits.

Target state: one Utho instance, 16 GB RAM, Mumbai zone, running the same Docker
Compose stack **without** the `docker-compose.8gb.yml` memory override.

---

## 0. What is verified, and what you must confirm first

Planning honestly requires separating the two.

**Verified (from the provider source and repo):**

| Fact | Value |
|---|---|
| Terraform provider | [`uthoplatforms/utho`](https://registry.terraform.io/providers/uthoplatforms/utho/latest), **v0.6.4**, last commit **2025-05-25** |
| Provider **installs and validates** | ✅ Verified — `tofu init` + `tofu validate` pass against `infra/utho/` |
| Provider registry | ⚠️ **HashiCorp registry only — not on OpenTofu's.** Source must be fully qualified |
| Provider resources | `cloud_instance`, `firewall`, `vpc`, `domain`, `dns_record`, `loadbalancer`, `target_group`, `auto_scaling` |
| Provider data sources | `account`, `images`, `object_storage_plan` |
| Mumbai zone exists | `dcslug` examples in provider docs: `inmumbaizone2`, `innoida` |
| Desktop clients target a **domain** | `wss://app.stratai.live/ws/*`, `https://app.stratai.live/*` — set in `desktop-release.yml` and `azure-pipelines.yml` |
| CI deploy target is a **variable** | `vars.DEPLOY_HOST` in `deploy-server.yml` — repointable without code change |
| Persistent volumes | `questdb_data`, `redis_data`, `caddy_data`, `caddy_config` |

**Not verified — confirm before committing to this plan:**

1. **16 GB plan price and your credit runway.** `utho.com/pricing` returns HTTP 403
   to automated fetches, so I could not read the rate card. The one comparison
   datapoint I found puts Utho at **$40/mo for 4 vCPU / 8 GB** against DO's $48 —
   roughly 15–20% cheaper. If a 16 GB plan lands near ₹4,000–6,000/mo, **₹10,000
   is about 2 months of runway, not a year.** Check this in the console first: it
   determines whether this migration buys you time or just moves the bill.
2. **The exact `planid`.** Utho plan IDs are opaque numeric strings
   (`"10045"` in the provider example). Get the real one for 16 GB from
   `GET /cloud/getplans` or the console — do not guess.
3. **The exact `dcslug`** for your chosen Mumbai zone (`inmumbaizone2` vs others)
   via `GET /cloud/availabledczones`.
4. **The image slug** for Ubuntu 22.04 via the `utho_images` data source.
5. **Provider staleness is a real risk.** v0.6.4 is ~14 months old with 1 star and
   6 forks. `tofu init` and `tofu validate` both pass (verified), so the provider
   installs and the config is schema-correct — but that only proves the *plugin*
   works, not that its API calls still match Utho's current backend. Run a
   throwaway `tofu apply` on the cheapest plan **before** planning the real
   migration. Budget for the possibility that it fails.

### Registry gotcha (already handled in `infra/utho/versions.tf`)

The provider is published to **HashiCorp's** registry but is **not mirrored by the
OpenTofu registry**, which is where `tofu` looks by default. A bare
`source = "uthoplatforms/utho"` fails at init:

```
provider registry registry.opentofu.org does not have a provider named
registry.opentofu.org/uthoplatforms/utho
```

The fix is a fully-qualified source, `registry.terraform.io/uthoplatforms/utho`,
which is what the committed config uses. Six versions are published
(0.4.0 → 0.6.4); the lock file pins 0.6.4 with signature-verified hashes for
`linux_amd64` and `windows_amd64`, and is committed deliberately.

> **Latency note, in your favour:** NSE's matching engines are in Mumbai. You are
> currently in DO `blr1` (Bangalore). A Mumbai zone should *reduce* market-data
> latency, not just preserve it.

---

## 1. The provider gap — your IaC does not port 1:1

This is the single most important finding. `infra/droplet.tf` is 68 lines of
DigitalOcean config that does four things; the Utho provider can only do two of
them declaratively.

| Capability | DigitalOcean (today) | Utho provider | Consequence |
|---|---|---|---|
| Create host | `digitalocean_droplet` | `utho_cloud_instance` | ✅ Port directly |
| Attach firewall | `digitalocean_firewall` | `utho_firewall` (name only) | ⚠️ Can create + attach, but **schema has no rule blocks** |
| **Define firewall rules** | `inbound_rule` / `outbound_rule` blocks | **Not supported** — no `utho_firewall_rule` resource | ❌ **Manage rules via console/API, outside Terraform** |
| Upload SSH key | `digitalocean_ssh_key` | **No resource** — instance takes `sshkeys` as pre-existing *IDs* | ❌ **Create the key in the console first**, pass its ID |
| Cloud-init bootstrap | `user_data = file("cloud-init.yaml")` | **No `user_data` field** | ❌ **`infra/cloud-init.yaml` cannot be used.** Bootstrap moves to SSH |
| Root credential | Key-only | **`root_password` is Required + Sensitive** | ⚠️ Password auth exists on the box, and the password **lands in tfstate** |

Three follow-on consequences worth internalising:

- **Your firewall stops being code.** The rules in `droplet.tf:26-68` become a
  console/API artifact. Document them in this repo (§5) or they will drift
  silently — and that firewall is what keeps QuestDB's PG wire off the internet.
- **`cloud-init.yaml` becomes `bootstrap.sh`.** Same commands, different delivery.
- **`root_password` in state.** Utho requires it. Generate a long random value,
  and **disable SSH password auth immediately after** the key works (§4). Treat
  `infra/utho/terraform.tfstate` as a secret file from day one.

---

## 2. Pre-flight (do before touching infra)

### 2a. Rotate the two committed secrets — independent of this migration

From the earlier audit, two credentials are tracked in git despite matching
`.gitignore`, and are present in history:

- `scripts/powershell/auth/keys/private.pem` — a real private key
- `bedrock-api-key.txt` — a Bedrock API credential

A fresh host is the natural moment to fix this, because you are re-creating `.env`
anyway. **Rotation matters more than history rewrite** — anyone with a clone
already has the old values.

```bash
git rm --cached scripts/powershell/auth/keys/private.pem bedrock-api-key.txt
# .gitignore already covers *.pem and bedrock-api-key.txt
git commit -m "chore(security): untrack committed key material"
```

Then rotate both credentials at their source and put the new values only in the
new host's `.env`. Do **not** carry the old ones to Utho.

### 2b. Lower DNS TTL — 24–48 h before cutover

The single highest-value preparatory step. Drop the `app.stratai.live` A record
TTL to **60 s** now. At cutover the switch propagates in a minute, and rollback is
equally fast. Skip this and you are married to your cutover for hours.

### 2c. Inventory what you are actually moving

```bash
# On the DO droplet
docker volume ls                                    # confirm project-prefixed names
docker system df -v | grep -A10 'Local Volumes'     # sizes → transfer time
df -h /                                             # disk headroom for the tarball
```

QuestDB is the only volume with irreplaceable data. Note the size — it drives
your cutover window length.

### 2d. Capture the current `.env`

`.env` is gitignored (correctly) and `redeploy.sh` preserves it via `git reset
--hard`. It has **33 keys** (`.env.example`). It exists *only* on the droplet.
Copy it to a password manager or encrypted store now — losing it means
re-collecting every Kite, LLM, and QuestDB credential by hand.

```bash
scp root@<do-ip>:/root/Ai-trader/.env ./env.do.backup   # store encrypted, delete after
```

### 2e. Pick the window: a weekend

NSE trades 09:15–15:30 IST, Mon–Fri. **Cut over Saturday morning.** The market is
closed, so a cold QuestDB copy loses zero ticks and you are not racing a session.
This also means the `MarketSession` logic in `service-metrics` will correctly
report the new stack as idle-not-broken while you verify.

---

## 3. Provision — new Terraform alongside the old

**This is already written and verified: see `infra/utho/`.** The DigitalOcean
config in `infra/` is **untouched**, so two independent state files mean rollback
is just "point DNS back."

| File | Purpose |
|---|---|
| `infra/utho/versions.tf` | Provider pin + the fully-qualified registry source |
| `infra/utho/providers.tf` | Token wiring |
| `infra/utho/variables.tf` | All inputs, with lookup commands in the descriptions |
| `infra/utho/instance.tf` | `utho_firewall` + `utho_cloud_instance` (`prevent_destroy`) |
| `infra/utho/outputs.tf` | IP, specs, cost, firewall ID, next-steps checklist |
| `infra/utho/bootstrap.sh` | Docker install + SSH hardening (replaces cloud-init) |
| `infra/utho/FIREWALL.md` | **Source of truth for the rules Terraform can't express** |
| `infra/utho/README.md` | Directory-level runbook |
| `infra/utho/terraform.tfvars.example` | Copy to `terraform.tfvars` (gitignored) |

Verified: `tofu init` installs v0.6.4 signature-verified, `tofu validate` passes,
`tofu fmt -check` clean, and all three shell scripts pass `bash -n`.

Then:
```bash
cd infra/utho
cp terraform.tfvars.example terraform.tfvars   # fill in: token, password, ssh id, planid
tofu init
tofu plan       # review — confirm plan/zone/image resolved as intended
tofu apply
tofu output specs          # CONFIRM ~16 GB before going further
tofu output monthly_cost   # real runway against your ₹10,000
```

The sections below explain the design decisions behind those files; the files
themselves are the authority.

## 4. Bootstrap — replacing cloud-init

The Utho provider exposes no `user_data` field, so `infra/cloud-init.yaml` (which
bootstraps Docker on the DigitalOcean droplet at first boot) cannot be used. Its
steps are ported to **`infra/utho/bootstrap.sh`** — same official-repo Docker
install, idempotent, delivered over SSH after apply.

It also does one thing cloud-init did not: **disables SSH password auth**, because
Utho forces a `root_password` that is persisted in tfstate. The script refuses to
do so when `authorized_keys` is empty, so a bad key cannot lock you out.

Run it, **verifying key auth works before disabling passwords**:

```bash
IP=$(cd infra/utho && tofu output -raw instance_ip)
ssh -i keys/stratai_deploy -o StrictHostKeyChecking=accept-new root@"$IP" 'echo key-auth-ok'
scp -i keys/stratai_deploy infra/utho/bootstrap.sh root@"$IP":/root/
ssh -i keys/stratai_deploy root@"$IP" 'bash /root/bootstrap.sh'
```

Reuse the existing `keys/stratai_deploy` keypair — it is already the
`DEPLOY_SSH_KEY` GitHub secret, so CI keeps working with no secret rotation.

---

## 5. Firewall rules — the part Terraform can't express

Configure these in the Utho console (or via API) and **keep this table in the repo
as the source of truth**, since the provider cannot enforce it.

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22 | TCP | **your IP `/32`** | SSH. See note below |
| 80 | TCP | `0.0.0.0/0` | Let's Encrypt ACME challenge |
| 443 | TCP | `0.0.0.0/0` | Caddy TLS gateway — all WSS feeds + `/questdb` + `/deepquant` |
| 8085 | TCP | **known client IPs** | Ingestion control (subscription diffs) |
| 8087 | TCP | `0.0.0.0/0` | Kite OAuth callback |
| 8812 | TCP | **your IP `/32`** | QuestDB PG wire |
| ICMP | — | `0.0.0.0/0` | ping / path MTU |

**Tighten what DigitalOcean left open.** Your current `infra/variables.tf` defaults
`ssh_ingress_cidrs` *and* `app_ingress_cidrs` to `0.0.0.0/0`, and
`terraform.tfvars` does not override them — so today SSH, 8812, and the 8085
control port are exposed to the entire internet. Do not reproduce that. GitHub
Actions runners have dynamic IPs, so if CI must SSH in, either accept `0.0.0.0/0`
on 22 with key-only auth (passwords now disabled) or front deploys with a
self-hosted runner / tunnel. Everything else should be narrow.

Egress: allow all (matches current DO config — services call Kite and LLM APIs).

---

## 6. Migrate the data

### 6a. Quiesce the DO stack (market closed)

```bash
# On DO
cd /root/Ai-trader
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml"
$COMPOSE stop ingestion alpha-terminal technical aggregator predictive \
              quant-rag sentiment tool-server deep-quant
$COMPOSE stop questdb      # cold copy = consistent snapshot
```

### 6b. Snapshot the volumes

Confirm the project-prefixed volume names from `docker volume ls` first
(typically `ai-trader_questdb_data`).

```bash
VOL_PREFIX=ai-trader   # verify!

docker run --rm -v ${VOL_PREFIX}_questdb_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/questdb_data.tar.gz -C /data .

# Copy caddy_data too — it holds the VALID app.stratai.live certificate.
# Preserving it means TLS works instantly at cutover with no ACME round-trip,
# and no risk of hitting Let's Encrypt's 5-duplicate-certs-per-week limit
# if you end up cutting over more than once.
docker run --rm -v ${VOL_PREFIX}_caddy_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/caddy_data.tar.gz -C /data .
```

Skip `redis_data` — it is a cache and cold-starts fine.

### 6c. Transfer and restore

```bash
# DO → Utho, direct (faster than routing via your laptop)
scp questdb_data.tar.gz caddy_data.tar.gz root@<utho-ip>:/root/

# On Utho, BEFORE the first `up`
cd /root
git clone <repo> Ai-trader && cd Ai-trader
git checkout main

docker volume create ai-trader_questdb_data
docker volume create ai-trader_caddy_data
docker run --rm -v ai-trader_questdb_data:/data -v /root:/backup alpine \
  tar xzf /backup/questdb_data.tar.gz -C /data
docker run --rm -v ai-trader_caddy_data:/data -v /root:/backup alpine \
  tar xzf /backup/caddy_data.tar.gz -C /data
```

> **Why cold-copy rather than re-backfill:** `historical_candles` and
> `historical_intraday` *could* be rebuilt from Kite via `history_loader.rs`, but
> `option_chain_snapshots` is point-in-time data that **cannot be reconstructed**.
> Copy the volume.

### 6d. Recreate `.env`

```bash
# On Utho
cp .env.example .env
# Fill all 33 keys from your 2d backup, EXCEPT the two rotated in 2a.
```

Then regenerate QuestDB auth if you want fresh credentials (the script is
idempotent and skips if already set):

```bash
bash infra/provision-questdb-auth.sh
```

---

## 7. Deploy — 16 GB means dropping the override

The whole point of the bigger box. `docker-compose.8gb.yml` exists only to squeeze
a ~9.5 GB stack into 8 GB; on 16 GB you run the base file's real limits.

```bash
# On Utho — note: NO -f docker-compose.8gb.yml
export DOCKER_BUILDKIT=1
COMPOSE="docker compose -f docker-compose.prod.yml"

for svc in ingestion alpha-terminal technical aggregator predictive \
           quant-rag sentiment tool-server deep-quant; do
  echo "=== building $svc ==="
  $COMPOSE build "$svc"
done

$COMPOSE up -d
$COMPOSE ps
```

Keep builds **sequential** — that guard exists so concurrent Rust compiles don't
OOM, and it costs little.

**`deploy.sh` and `redeploy.sh` both hardcode the 8gb override** (`COMPOSE=
"docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml"`). Make the
override list an env var so the same script serves both hosts during transition:

```bash
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.prod.yml -f docker-compose.8gb.yml}"
COMPOSE="docker compose $COMPOSE_FILES"
```

Then on Utho set `COMPOSE_FILES="-f docker-compose.prod.yml"`. Without this,
`redeploy.sh` will silently re-apply 8 GB limits on your 16 GB box and you will
have paid for headroom you never use.

---

## 8. Verify before cutover — while DNS still points at DO

The new stack is fully testable before a single user is affected. This is where
the `service-metrics` work you just landed earns its keep.

```bash
IP=<utho-ip>

# 1. Every service's health + readiness (ports :9101-:9107)
for p in 9101 9102 9103 9104 9105 9106 9107; do
  echo "--- :$p"
  ssh root@$IP "curl -sf localhost:$p/health && curl -sf localhost:$p/ready"
done

# 2. QuestDB data actually restored — row counts should match DO
ssh root@$IP "curl -s 'localhost:9000/exec?query=SELECT%20count()%20FROM%20historical_candles'"
ssh root@$IP "curl -s 'localhost:9000/exec?query=SELECT%20count()%20FROM%20option_chain_snapshots'"

# 3. Kafka topics created
ssh root@$IP "docker compose -f docker-compose.prod.yml logs kafka-init --tail=30"

# 4. Memory headroom — confirm the 16 GB is real and unconstrained
ssh root@$IP "free -h && docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}'"

# 5. TLS works via Host header, before DNS moves
curl -sk https://$IP/questdb/exec?query=SELECT+1 -H 'Host: app.stratai.live' -u '<user>:<pass>'
```

Compare the two row counts against the same queries on DO. **Do not cut over
until they match.**

---

## 9. Cutover

1. **Confirm §8 is green.** DO stack still stopped, Utho stack verified.
2. **Change the A record** for `app.stratai.live` → Utho IP. With the 60 s TTL
   from §2b this propagates in about a minute.
   *(Optional: the provider does offer `utho_domain` + `utho_dns_record` if you
   want DNS in Terraform — but moving nameservers mid-migration adds a failure
   mode. Change the single A record at your existing provider instead.)*
3. **Watch TLS.** Because you copied `caddy_data`, Caddy should serve the existing
   valid certificate immediately. Confirm:
   ```bash
   curl -sI https://app.stratai.live | head -3
   echo | openssl s_client -connect app.stratai.live:443 2>/dev/null \
     | openssl x509 -noout -dates
   ```
4. **Repoint CI** — repo → Settings → Secrets and variables → Actions:
   set `DEPLOY_HOST` = Utho IP. `DEPLOY_SSH_KEY` and `DEPLOY_PATH` are unchanged
   (same keypair, same `/root/Ai-trader`).
5. **Smoke-test a real client.** Launch the desktop app. Because it targets
   `app.stratai.live` and not an IP, **no rebuild or re-release is required** —
   this is the reason the migration is a DNS change rather than a client rollout.
6. **Watch a live session.** Keep the DO droplet powered off but *not destroyed*
   through the first full trading day.

---

## 10. Rollback

Cheap and fast, if you did §2b:

| Symptom | Action |
|---|---|
| Anything wrong within minutes | A record → DO IP; `docker compose ... up -d` on DO |
| Data gap found post-cutover | Market is closed on a weekend — re-run 6b/6c with a fresh snapshot |
| Provider/Utho instance unusable | `cd infra/utho && tofu destroy`; DO config in `infra/` is untouched |

Because both Terraform states are separate and DO is only stopped, rollback never
requires re-provisioning. **Do not run `tofu destroy` in `infra/` until Utho has
served a full trading week.**

---

## 11. Decommission DigitalOcean

Only after a clean week:

```bash
ssh root@<do-ip> 'cd /root/Ai-trader && docker compose -f docker-compose.prod.yml \
  -f docker-compose.8gb.yml down'
# Take a final local copy of questdb_data.tar.gz first — keep it offline.
cd infra && tofu destroy    # stops DO billing
```

Then update the docs. `infra/README.md` is already stale (it references a
nonexistent `infra/digitalocean/` directory, the wrong key filename
`thestratai_ssh.pub`, ports `8080–8085` that no longer match
`variables.tf`, and a `terraform.tfvars.example` that does not exist). Rather than
patch it, **rewrite it for Utho** and delete the DO instructions — a wrong runbook
is worse than none.

---

## 12. What 16 GB unlocks (do these after the migration settles)

The old box was the binding constraint. Three things become possible:

1. **Prometheus + Grafana — finish the monitoring work.** Seven services now
   export `/metrics` on `:9101–:9107`, but there is **no scraper anywhere in the
   repo** and **no `910*` port mapping in any compose file**, so all of that
   instrumentation is currently inert. Add a `prometheus` + `grafana` service to
   `docker-compose.prod.yml` with a scrape config over the seven targets. Cost:
   ~500 MB. On 8 GB there was no room; on 16 GB there is.
2. **Compose healthchecks.** Only `redpanda`, `questdb`, and `redis` have
   `healthcheck:` blocks; all ten application services have none — despite now
   serving exactly the `/health` endpoint Compose needs. Add healthchecks and
   convert `depends_on` to `condition: service_healthy`, so services stop starting
   against a not-yet-ready Kafka and converging via restart loops.
3. **The `platform` profile.** `docker-compose.8gb.yml` warns "Do NOT enable the
   `platform` profile (auth/payment) on 8 GB — no headroom." At 16 GB you can
   evaluate it.

Also revisit: QuestDB's `mem_limit` was cut to 2560m and
`QDB_SHARED_WORKER_COUNT` to 2 for the small box. On 16 GB, raise both — QuestDB
is your history store and it was the service most starved by the 8 GB squeeze.

---

## Open questions

1. **What is the real 16 GB monthly price, and how long does ₹10,000 last?** If
   it is ~2 months, this migration is a short reprieve — worth knowing before you
   invest a weekend in it.
2. **Does the v0.6.4 provider still work?** Test on a throwaway cheap instance
   first. If it is broken, the fallback is console provisioning plus a documented
   `bootstrap.sh` — which loses IaC for the host but keeps everything below the OS
   line reproducible. Given the provider already cannot express firewall rules,
   SSH keys, or cloud-init, you are giving up less than it appears.
3. **Does Utho object storage make sense for remote tfstate?** The provider has an
   `object_storage_plan` data source, and S3-compatible storage would also give you
   an off-host QuestDB backup target. Local state on your laptop is a single point
   of failure for infrastructure you are billed for.
