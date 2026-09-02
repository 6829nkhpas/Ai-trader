# StratAI on Google Cloud — Compute Engine

Terraform for the single VM that runs the whole `docker-compose.prod.yml` stack.
Replaces the retired DigitalOcean droplet, which was deleted along with its data.

**Nothing here has been applied.** Provisioning waits on explicit confirmation.

---

## What this builds

| Resource | Value | Why |
|---|---|---|
| Instance | `e2-custom-4-8192` — 4 vCPU / 8 GB | Exact match for the droplet's `s-4vcpu-8gb`, which `docker-compose.8gb.yml` was written against |
| Region / zone | `asia-south1` (Mumbai) / `-c` | Closest region to the NSE, replacing `blr1`. Kite latency matters: ingestion holds a live WebSocket |
| Boot disk | 160 GB `pd-balanced`, `auto_delete = false` | Matches the droplet's included SSD. Survives instance replacement |
| OS | Ubuntu 22.04 LTS x86_64 | Same as the droplet; cloud-init and the Docker apt pin assume it |
| Public IP | Reserved static | DNS and `DEPLOY_HOST` both hardcode it; an ephemeral IP is released on STOP |
| Network | Dedicated VPC + subnet | New projects may have no `default` network, and `default` ships permissive rules |
| Firewall | 22, 80/443, ICMP. Control ports **closed** | See the security note below |
| Snapshots | Daily 02:00 IST, 7-day retention | The droplet had backups off, and its data was unrecoverable |
| Service account | Dedicated, logging + monitoring write only | Nothing in the stack calls a Google API |

GCP has no 4 vCPU / 8 GB standard shape (`e2-standard-4` is 4/16), so a custom type
is used to avoid either over-provisioning RAM or halving the CPU. For the headroom
the compose comments recommend, set `machine_type = "e2-standard-4"` and drop
`-f docker-compose.8gb.yml` from `COMPOSE_FILES` on the box.

## Read this before you apply

**The droplet's data is gone.** It was deleted with its volumes, and nothing here
can bring it back. What that cost:

- `questdb_data` — all historical `live_ticks`, `option_ticks` and
  `option_chain_snapshots`. Candles re-fetch from Kite on demand and the chain
  re-ingests within a minute, so the app works immediately; the *history* does not
  come back. Per-strike OI buildup needs two consecutive snapshots, so it reports
  `neutral` until the second one lands.
- `grafana_data` — users and preferences. Dashboards are in git.
- `prometheus_data` — 7 days of metrics, which had a 7d/2GB cap anyway.
- `compliance.db` — the **append-only recommendation record**, which lived in the
  repo directory on the box. This is the one that matters: it exists so decisions
  cannot be removed after the fact. If you have no snapshot of it, say so
  explicitly somewhere durable rather than letting the gap sit silently in a
  compliance artefact.

**Generate a new SSH key.** The old `keys/stratai_deploy` private key was printed
into a terminal log during a debugging session. The host it opened is gone, so
nothing is exposed now — but do not carry it forward:

```bash
ssh-keygen -t ed25519 -f keys/stratai_gcp -C stratai-gcp -N ""
```

**The control ports are closed by default and that is a change.** On the droplet,
8085, 8087 and 8812 were open to `0.0.0.0/0`. Port 8085 is the ingestion control
port: it accepts newline-delimited `subscribe:` / `option_chain_set:` commands with
**no authentication**, so anything that could reach it could repoint the market data
feed. 8812 is the QuestDB PostgreSQL wire port. Nothing in the web app needs either
— the browser reaches QuestDB and deep-quant through Caddy on 443. Set
`control_ingress_cidrs` only for the addresses that genuinely need them.

## Apply

```bash
gcloud auth application-default login
gcloud config set project <project-id>

# APIs, once per project
gcloud services enable compute.googleapis.com

cd infra/gcp
cp terraform.tfvars.example terraform.tfvars   # fill in project_id
terraform init
terraform plan -out=gcp.tfplan                 # read it
terraform apply gcp.tfplan
```

`terraform validate` has not been run against this module — Terraform is not
installed on the machine it was written on. Treat the first `plan` as the real
syntax check.

## Bootstrap after apply

`terraform output next_steps` prints this list with your IP substituted in.

1. **DNS.** Point **only these two** A records at `terraform output instance_ip`:

   | Name | Why |
   |---|---|
   | `app.stratai.live` | the web terminal vhost in `infra/caddy/Caddyfile` |
   | `app-api.stratai.live` | the gateway vhost — WSS feeds, `/questdb`, `/deepquant`, `/kite`, `/tools` |

   **Do NOT point `dashboard`, `auth` or `api-web` here.** They are separate
   deployments that are already live elsewhere, and the Caddyfile has no vhost for
   them — repointing would take three working services down.

   Wait for propagation *before* the first deploy: Caddy uses ACME HTTP-01, so a
   name that does not resolve to this box yet fails issuance and gets rate-limited.

2. **Charting library.** A private TradingView submodule the box cannot clone.
   `redeploy.sh` hard-fails without it, deliberately — a frontend built without it
   looks healthy and every chart 404s.
   ```bash
   cd frontend/public/static && tar --exclude=.git -czf /tmp/cl.tgz charting_library
   scp -i keys/stratai_gcp /tmp/cl.tgz stratai@<ip>:/tmp/
   ssh -i keys/stratai_gcp stratai@<ip> 'tar xzf /tmp/cl.tgz -C /srv/vendor'
   ```
   The `--exclude=.git` is not optional: packing the submodule's gitlink breaks the
   *next* deploy's `git reset --hard`.

3. **Clone** the repo to `/opt/stratai/Ai-trader` as the `stratai` user.

4. **`.env`.** Not in git. Needs `KITE_API_KEY` / `KITE_API_SECRET` /
   `KITE_ACCESS_TOKEN`, the LLM keys, and the QuestDB credentials —
   `bash infra/provision-questdb-auth.sh` generates the last set if you want fresh
   ones. Note `KITE_ACCESS_TOKEN` expires daily at midnight IST; the stack 403s on
   every Kite call until it is replaced.

5. **CI variables.** `DEPLOY_USER` and `DEPLOY_PATH` are **not optional here** — the
   workflow defaults to `root` and `/root/Ai-trader`, which are the droplet's
   values and wrong on GCP.

   | Setting | Value |
   |---|---|
   | `DEPLOY_HOST` (var) | `terraform output instance_ip` |
   | `DEPLOY_USER` (var) | `stratai` |
   | `DEPLOY_PATH` (var) | `/opt/stratai/Ai-trader` |
   | `DEPLOY_SSH_KEY` (secret) | contents of `keys/stratai_gcp` (private) |

6. **First deploy**, by hand, so you see the output:
   ```bash
   GITHUB_TOKEN=$(gh auth token) DEPLOY_BRANCH=main bash redeploy.sh
   ```
   Then check `https://app.stratai.live/api/features` returns 200 and
   `docker compose ps` shows every service up.

## Notes on the design

**Root login.** GCP's Ubuntu images ship `PermitRootLogin without-password` and let
the guest agent manage keys, so deploying as root means fighting the platform. Hence
the `stratai` user in the `docker` group, and `/opt/stratai` instead of `/root`.

**OS Login is off.** It is the better model, but it replaces key-based SSH with an
IAM/gcloud flow and `deploy-server.yml` authenticates with a raw private key through
`appleboy/ssh-action`. Enabling it means reworking CI auth first.

**`user-data` is in `ignore_changes`.** Editing `cloud-init.yaml` would otherwise
recreate the instance and wipe the Docker volumes. Boot config is a one-time
bootstrap; day-to-day changes go through `redeploy.sh`.

**Live migration is on.** Host maintenance must not drop the Kite WebSocket
mid-session, which `on_host_maintenance = "TERMINATE"` would.

**State is local.** Fine for one operator; move it to a versioned GCS bucket before
it is the only copy of anything. The DO module's state ended up with `.RETIRED` and
`.old-account.bak` siblings, which is what local state looks like once more than one
machine touches it. See the commented backend block in `versions.tf`.

## Not included, on purpose

- **DNS.** Your zone is not in this project, so the records are a manual step.
  Adding `google_dns_record_set` would mean moving the zone to Cloud DNS first.
- **Secret Manager.** `.env` is copied by hand, exactly as on the droplet. Wiring
  Secret Manager in is worthwhile, but it changes how every service reads config and
  is a separate piece of work from the migration.
- **Managed data services.** Cloud SQL and Memorystore would replace QuestDB and
  Redis with something operationally simpler and considerably more expensive, and
  QuestDB has no managed GCP equivalent at all. "Same server configuration" means
  the same single VM running the same compose file.
