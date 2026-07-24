# Infrastructure — DigitalOcean (OpenTofu)

Provisions the beta host for the Ai-trader data plane on DigitalOcean.

| Resource | Notes |
|---|---|
| Droplet | `s-4vcpu-8gb` (4 vCPU / 8 GB), Ubuntu 22.04 **x86_64**, region `blr1` (Bangalore) |
| SSH key | Uploads `keys/thestratai_ssh.pub` (already generated) |
| Cloud Firewall | Opens SSH (22) + app ports 8080–8085, 8087. Datastore ports stay private. |
| cloud-init | Installs Docker Engine + compose plugin on first boot |

**Why DigitalOcean is simpler than the OCI attempt:** droplets are x86_64, so every
Dockerfile builds with no ARM cross-compile concerns and `docker-compose.prod.yml`
runs as-is. No capacity lottery either.

**Cost:** `s-4vcpu-8gb` is ~**$48/month** (backups off). This is a paid resource —
there is no free tier on DigitalOcean.

---

## WHERE TO ADD YOUR DIGITALOCEAN CREDENTIALS  ← (what you asked for)

You need exactly **one** credential: a DigitalOcean API token.

1. **Create the token:** DigitalOcean Console → **API** (left sidebar) → **Tokens/Keys**
   → **Generate New Token**. Name it `stratai`, scope **Read + Write**, no expiry (or your choice).
   Copy the `dop_v1_...` value (shown once).

2. **Add it here:** in this folder (`infra/digitalocean/`), create a file named
   **`terraform.tfvars`** (it is gitignored — never committed) and paste:

   ```hcl
   do_token            = "dop_v1_<your-token-here>"
   ssh_public_key_path = "../../keys/thestratai_ssh.pub"
   region              = "blr1"
   droplet_size        = "s-4vcpu-8gb"
   ```

   (You can copy `terraform.tfvars.example` as a starting point.)

That's the only credential. The SSH key already exists in the repo, so once the
token is in place you're ready to deploy.

> For GitHub Actions instead of local: add the token as repo secret
> **`DIGITALOCEAN_TOKEN`** (or `TF_VAR_do_token`) and the SSH public key as
> **`OCI_SSH_PUBLIC_KEY`** — the workflows pass them through as `TF_VAR_*`.

---

## Deploy

```bash
cd infra/digitalocean
tofu init
tofu plan       # review — creates droplet + firewall + ssh key
tofu apply       # provisions (this incurs cost)
tofu output      # prints droplet IP + service endpoints
```

Then bring the app stack up on the droplet (8 GB-tuned):

```bash
ssh root@<droplet_ip>
git clone <your-repo> && cd Ai-trader
cp .env.example .env && nano .env        # fill in the DEPLOYMENT.md checklist
docker compose -f docker-compose.prod.yml -f docker-compose.8gb.yml up -d --build
```

`docker-compose.8gb.yml` trims memory limits so the whole stack fits in 8 GB
(~6.3 GB used, OS gets the rest). **Do not** enable the `platform` profile
(auth/payment) on 8 GB — bump to `s-8vcpu-16gb` first if you need those.

## Teardown

```bash
tofu destroy     # removes the droplet + firewall + ssh key, stops billing
```

## Notes

- `keys/`, `terraform.tfvars`, and state files are gitignored — never commit them.
- Datastore ports (QuestDB/Kafka/Postgres/Redis) are not exposed; reach them via SSH tunnel.
- Narrow `ssh_ingress_cidrs` / `app_ingress_cidrs` to known IPs once beta users are fixed.
- 8 GB is the practical minimum for the full data plane. For comfortable headroom
  (and to run auth/payment), use `s-8vcpu-16gb` (~$96/mo).
