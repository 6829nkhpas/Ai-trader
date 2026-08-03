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

## Inbound

| Port | Proto | Source | Purpose |
|---|---|---|---|
| 22 | TCP | **your IP `/32`** | SSH. See "CI access" below |
| 80 | TCP | `0.0.0.0/0` | Let's Encrypt ACME challenge (Caddy) |
| 443 | TCP | `0.0.0.0/0` | Caddy TLS gateway — all WSS feeds, `/questdb`, `/deepquant` |
| 8085 | TCP | **known client IPs** | Ingestion control — desktop subscription diffs |
| 8087 | TCP | `0.0.0.0/0` | Kite OAuth callback |
| 8812 | TCP | **your IP `/32`** | QuestDB PG wire |
| — | ICMP | `0.0.0.0/0` | ping / path-MTU discovery |

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

## CI access on port 22

`deploy-server.yml` SSHes in from a GitHub-hosted runner, and those have dynamic
IPs — so a `/32` on port 22 will break CI deploys. Options, best first:

1. **Self-hosted runner** on a fixed IP; allow only that `/32`.
2. **Tailscale / WireGuard**; allow only the tunnel.
3. **Accept `0.0.0.0/0` on 22** — tolerable *only* because `bootstrap.sh`
   disables password auth, so key-only. Weakest option; if you take it, know
   that's a deliberate trade.
4. Allow [GitHub's published runner ranges](https://api.github.com/meta) — large,
   changes often, low value.

Whichever you pick, record it here so the next person knows it was a decision.
