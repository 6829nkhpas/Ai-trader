# Infrastructure — Utho (16 GB)

Provisions the Strat Ai data-plane host on [Utho](https://console.utho.com).
Replaces the DigitalOcean config in `../` (kept until Utho has served a clean
week — see the decommission step in `docs/DEPLOYMENT_UTHO.md`).

Full migration runbook: **`docs/DEPLOYMENT_UTHO.md`**. This file covers only the
Terraform in this directory.

| Resource | Notes |
|---|---|
| Instance | 16 GB, Ubuntu 22.04 x86_64, Mumbai (`inmumbaizone2`) |
| Firewall | Created + attached here; **rules are manual** — see `FIREWALL.md` |
| Bootstrap | `bootstrap.sh` over SSH — the provider has no cloud-init support |

**Why Mumbai:** NSE's matching engines are there. The current DigitalOcean host is
in `blr1` (Bangalore), so this should *lower* market-data latency.

**Why 16 GB:** the base `docker-compose.prod.yml` limits sum to ~9.5 GB. The 8 GB
droplet needed `docker-compose.8gb.yml` to trim that to ~6.3 GB, which starved
QuestDB (2560m, 2 worker threads). At 16 GB the override is unnecessary.

---

## Read this before you apply

This provider is **weaker than DigitalOcean's**, and it changes how much of your
infrastructure is code. Provider: [`uthoplatforms/utho`](https://registry.terraform.io/providers/uthoplatforms/utho/latest)
**v0.6.4**, last commit **2025-05-25** (~14 months old, 1 star, 6 forks).

| Capability | DigitalOcean (`../droplet.tf`) | Here |
|---|---|---|
| Create host | `digitalocean_droplet` | ✅ `utho_cloud_instance` |
| Firewall object | `digitalocean_firewall` | ✅ `utho_firewall` |
| **Firewall rules** | `inbound_rule` blocks, in code | ❌ **Console/API only** → `FIREWALL.md` |
| SSH key upload | `digitalocean_ssh_key` | ❌ Create in console, pass numeric ID |
| Cloud-init | `user_data = file(...)` | ❌ No field → `bootstrap.sh` over SSH |
| Auth | Key-only | ⚠️ `root_password` **required**, lands in tfstate |

Three consequences:

1. **Your firewall is no longer code.** `FIREWALL.md` is the source of truth and
   nothing enforces it. Drift is silent, and that firewall is what keeps the
   QuestDB PG wire off the internet.
2. **`terraform.tfstate` is a secret file.** It holds `root_password` in
   plaintext. Gitignored here; keep it that way.
3. **Test the provider before trusting it.** It is unmaintained. Run one
   throwaway apply on the cheapest plan to confirm it still works against
   Utho's current API, then destroy it.

---

## Look these up first — do not guess

Three values have no safe default. `planid` has no default at all, so an
accidental `apply` errors instead of billing you for the wrong machine.

```bash
export UTHO_TOKEN=<your-token>
AUTH="Authorization: Bearer $UTHO_TOKEN"

curl -H "$AUTH" https://api.utho.com/v2/cloud/getplans           # -> planid (16 GB)
curl -H "$AUTH" https://api.utho.com/v2/cloud/availabledczones   # -> dcslug (Mumbai)
curl -H "$AUTH" https://api.utho.com/v2/cloud/images             # -> image slug
```

**Also check the price.** I could not read Utho's rate card (`utho.com/pricing`
returns 403 to automated fetches). The one comparison datapoint available puts
Utho at ~$40/mo for 4vCPU/8GB against DigitalOcean's $48 — roughly 15–20%
cheaper. If the 16 GB plan runs ₹4,000–6,000/mo, **₹10,000 in credits is about
two months of runway, not a year.** `tofu output monthly_cost` reports the
provider's figure after apply; confirm it before you rely on the credits.

---

## Deploy

**1. Create the SSH key in the console** (the provider cannot upload it).
Console → Settings → SSH Keys → paste `../../keys/stratai_deploy.pub`. Note the
numeric ID.

> Reuse the existing `keys/stratai_deploy` keypair — it is already the
> `DEPLOY_SSH_KEY` GitHub secret, so CI keeps working with no rotation.

**2. Fill in variables.**
```bash
cd infra/utho
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars      # gitignored
```

**3. Apply.**
```bash
tofu init
tofu plan       # review: confirm the plan/zone/image resolved as intended
tofu apply
tofu output
```

**4. Confirm you got the machine you paid for.**
```bash
tofu output specs          # expect ~16 GB RAM
tofu output monthly_cost
```

**5. Add the firewall rules** — Terraform cannot. Apply the table in
`FIREWALL.md` to the firewall ID from `tofu output firewall_id`.

**6. Bootstrap Docker.**
```bash
IP=$(tofu output -raw instance_ip)
cd ../..
ssh -i keys/stratai_deploy -o StrictHostKeyChecking=accept-new root@"$IP" 'echo key-auth-ok'
scp -i keys/stratai_deploy infra/utho/bootstrap.sh root@"$IP":/root/
ssh -i keys/stratai_deploy root@"$IP" 'bash /root/bootstrap.sh'
```

Verify key auth works *before* `bootstrap.sh` disables password auth. The script
guards this — it refuses to disable passwords when `authorized_keys` is empty —
but check anyway.

**7. Migrate data and deploy:** `docs/DEPLOYMENT_UTHO.md` §6–§7.

---

## Running the stack on 16 GB

`deploy.sh` and `redeploy.sh` default to including `docker-compose.8gb.yml`.
**On this host, drop it** — otherwise you pay for 16 GB and run 8 GB limits:

```bash
# On the Utho host, make it persistent:
echo 'COMPOSE_FILES="-f docker-compose.prod.yml"' >> /etc/environment
```

CI runs `redeploy.sh` over SSH, and `appleboy/ssh-action` starts a
non-interactive shell that may not read `/etc/environment`. Confirm after your
first CI deploy that the log line `Compose files: ...` shows the base file only.
If it still shows the override, set it explicitly in `deploy-server.yml`:

```yaml
script: |
  set -e
  cd "${{ vars.DEPLOY_PATH || '/root/Ai-trader' }}"
  COMPOSE_FILES="-f docker-compose.prod.yml" bash redeploy.sh
```

---

## Teardown

```bash
tofu destroy
```

`utho_cloud_instance.app` has `prevent_destroy = true` because this host holds the
QuestDB volume — and `option_chain_snapshots` is point-in-time data that cannot
be rebuilt from Kite. Remove the `lifecycle` block deliberately, after taking a
final volume snapshot (`docs/DEPLOYMENT_UTHO.md` §6b).

## Notes

- `terraform.tfvars`, `*.tfstate`, and `.terraform/` are gitignored. State holds
  the root password in plaintext.
- Datastore ports are not exposed publicly — reach them over an SSH tunnel.
- Consider remote state on Utho object storage (S3-compatible; there is a
  `utho_object_storage_plan` data source). Local state is a single point of
  failure for billed infrastructure, and the same bucket would serve as an
  off-host QuestDB backup target.
