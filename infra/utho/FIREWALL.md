# Firewall rules — source of truth

**These rules are not in Terraform, and cannot be.**

The `utho_firewall` resource (provider v0.6.4) accepts one argument: `name`.
There is no `inbound_rule` block and no `utho_firewall_rule` resource. Compare
`../droplet.tf:26-68`, where every DigitalOcean rule is declared in code.

So: Terraform creates and attaches the firewall, you add the rules by hand in the
Utho console (or via API), and **this file is the only record of what they should
be**. Nothing detects drift. Re-check it whenever the stack's ports change.

Apply to the firewall whose ID is `tofu output firewall_id`.

---

## Inbound — PRODUCTION posture

This host serves real users, not beta testers. Each row below is either "must be
public to function" or "narrowed on purpose".

| Port | Proto | Source | Purpose |
|---|---|---|---|
| 22 | TCP | `0.0.0.0/0` **key-only** | SSH. Decided: keep CI auto-deploy. See below |
| 80 | TCP | `0.0.0.0/0` | Let's Encrypt ACME challenge (Caddy) — required for TLS renewal |
| 443 | TCP | `0.0.0.0/0` | Caddy TLS gateway — all WSS feeds, `/questdb`, `/deepquant` |
| 8085 | TCP | **known client IPs** | Ingestion control — desktop subscription diffs |
| 8087 | TCP | `0.0.0.0/0` | Kite OAuth callback — Zerodha redirects users here |
| 8812 | TCP | **your IP `/32`** | QuestDB PG wire. Do NOT open to the world |
| — | ICMP | `0.0.0.0/0` | ping / path-MTU discovery |

**Verify every rule in the console UI after you add it.** Do not trust an API
`200` here: Utho's API returns HTTP 200 with an empty body for unrecognised
`/v2/cloud/*` paths, so a mistyped firewall API call can look like it succeeded
while applying nothing. On a production host that failure mode means believing
8812 is closed when it is open. Read the rules back off the console before you
consider the firewall done.

### Port 22 — decision recorded

`0.0.0.0/0`, key-only. Chosen so `deploy-server.yml` keeps working: GitHub-hosted
runners have dynamic IPs that cannot be allowlisted, and losing CI deploys means
every production release becomes a manual SSH session.

This is safe *only because* `bootstrap.sh` sets `PasswordAuthentication no`.
Confirm that took effect before considering the host production-ready:

```bash
sshd -T | grep -i passwordauthentication   # must print: passwordauthentication no
```

If that returns `yes`, port 22 is open to the internet with password auth live —
fix it before anything else. Stronger alternatives, if you later want port 22
invisible: a self-hosted runner on a fixed IP, or Tailscale/WireGuard with only
the tunnel allowed.

### Production hardening beyond the firewall

The firewall is one layer. For a host now serving real users, also:

- **`fail2ban`** — blunts SSH brute-force noise, which arrives within hours of a
  public port 22.
- **Unattended security upgrades** — `unattended-upgrades` for kernel/OpenSSL
  patches. `cloud-init.yaml` sets `package_upgrade: false`, so nothing patches
  itself today.
- **Backups on.** `enable_backups` defaults `true` in this config (the DO config
  defaults `false`). `option_chain_snapshots` is point-in-time data that cannot
  be rebuilt from Kite.
- **Rotate the QuestDB basic-auth password** if the beta value was ever shared —
  `infra/provision-questdb-auth.sh` generates a fresh one.

## Outbound

Allow all TCP/UDP/ICMP to `0.0.0.0/0` and `::/0`. Services call out to Kite
(`api.kite.trade`, `wss://ws.kite.trade`), the LLM provider, and news APIs.

---

## What must NOT be exposed

These are published to `127.0.0.1` only in `docker-compose.prod.yml`, or not
published at all. They are reachable inside the Docker network by service name,
and from your laptop over an SSH tunnel. Never open them:

- `9000` QuestDB HTTP — served publicly through Caddy at `/questdb` **with basic auth**
- `9009` QuestDB ILP
- `19092` Kafka/Redpanda external API
- `6379` Redis
- `8080`–`8083` WS feeds — served publicly through Caddy at `/ws/*`
- `8086` deep-quant — served publicly through Caddy at `/deepquant` **with basic auth**
- `9101`–`9107` Prometheus metrics endpoints

That last line matters: the `/metrics` endpoints expose internal operational
detail and have no authentication. Scrape them from a Prometheus container
**inside** the Docker network, never across the public internet.

---

## Tighten what DigitalOcean left open

The current DO host is more exposed than it should be. `../variables.tf` defaults
**both** `ssh_ingress_cidrs` and `app_ingress_cidrs` to `0.0.0.0/0`, and
`../terraform.tfvars` does not override either — so today, SSH (22), the QuestDB
PG wire (8812), and the ingestion control port (8085) are all open to the entire
internet. The variable descriptions say to narrow them; nobody did.

Do not reproduce that here. Port 8085 in particular accepts subscription diffs
over raw TCP — "control port" plus "open to the world" is a combination worth
avoiding.

The one exception carried forward deliberately is port 22, for the CI reason
recorded above. Everything else is narrowed.
