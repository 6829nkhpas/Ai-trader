// ─────────────────────────────────────────────────────────────────────────────
// Project / location
// ─────────────────────────────────────────────────────────────────────────────

variable "project_id" {
  description = "GCP project ID the stack lives in."
  type        = string
}

variable "region" {
  description = <<-EOT
    GCP region. `asia-south1` is Mumbai — the closest region to the NSE, and the
    replacement for the retired droplet's `blr1` (Bangalore). Latency to Kite's
    endpoints is the reason this is not us-central1: the ingestion service holds a
    live WebSocket and the aggregator makes per-cycle REST calls.
  EOT
  type        = string
  default     = "asia-south1"
}

variable "zone" {
  description = "Zone for the instance. Single zone by design — this is one VM, exactly as the droplet was."
  type        = string
  default     = "asia-south1-c"
}

variable "name" {
  description = "Base name for every resource (instance, VPC, firewall rules, IP)."
  type        = string
  default     = "stratai"
}

// ─────────────────────────────────────────────────────────────────────────────
// Instance — sized to match the retired droplet exactly
// ─────────────────────────────────────────────────────────────────────────────

variable "machine_type" {
  description = <<-EOT
    Machine type. `e2-custom-4-8192` is 4 vCPU / 8 GB — the exact equivalent of the
    droplet's `s-4vcpu-8gb`, which is what `docker-compose.8gb.yml` was written
    against.

    A custom type is used deliberately: GCP's standard families have no 4 vCPU /
    8 GB shape (`e2-standard-4` is 4 vCPU / 16 GB), so the alternatives were to
    over-provision RAM or to halve the CPU. If you want the headroom the compose
    comments recommend, `e2-standard-4` is the one-line change — and then drop
    `-f docker-compose.8gb.yml` from `COMPOSE_FILES` so the stack gets its real
    limits instead of the squeezed ones.
  EOT
  type        = string
  default     = "e2-custom-4-8192"
}

variable "boot_disk_size_gb" {
  description = <<-EOT
    Boot disk size. 160 GB matches what `s-4vcpu-8gb` included.

    This is not idle sizing: QuestDB's `option_chain_snapshots` grows by roughly one
    row per subscribed token per minute (~1300 tokens today) and `live_ticks` grows
    with the tick feed, so the disk is consumed continuously.
  EOT
  type        = number
  default     = 160
}

variable "boot_disk_type" {
  description = "pd-balanced is the closest analogue to the droplet's SSD. pd-ssd costs more and is worth it only if QuestDB shows write pressure."
  type        = string
  default     = "pd-balanced"
}

variable "boot_image" {
  description = "Ubuntu 22.04 LTS x86_64, matching the droplet's `ubuntu-22-04-x64`. The compose stack, cloud-init and the Docker apt repo pin all assume this."
  type        = string
  default     = "ubuntu-os-cloud/ubuntu-2204-lts"
}

variable "deploy_user" {
  description = <<-EOT
    Unix user CI deploys as, created by cloud-init and added to the `docker` group.

    NOT root, unlike the droplet. GCP's Ubuntu images ship
    `PermitRootLogin without-password` and hand SSH key management to the guest
    agent, so deploying as root would mean fighting the platform. This user is why
    `DEPLOY_USER` and `DEPLOY_PATH` must both be set on the repo — see README.
  EOT
  type        = string
  default     = "stratai"
}

variable "deploy_path" {
  description = "Where the repo is cloned on the VM. Owned by `deploy_user`, so it cannot be under /root."
  type        = string
  default     = "/opt/stratai/Ai-trader"
}

variable "ssh_public_key_path" {
  description = <<-EOT
    Public half of the CI deploy key, injected as instance metadata.

    Generate a FRESH pair for this migration. The old `keys/stratai_deploy` was
    printed into a terminal log, so it must not be carried over even though the
    droplet it opened is gone.
  EOT
  type        = string
  default     = "../../keys/stratai_gcp.pub"
}

// ─────────────────────────────────────────────────────────────────────────────
// Firewall
// ─────────────────────────────────────────────────────────────────────────────

variable "ssh_ingress_cidrs" {
  description = <<-EOT
    Who may reach SSH (22). GitHub-hosted runners have no stable egress range, so
    `0.0.0.0/0` is the honest default for a repo whose CI deploys over SSH.

    Narrow it to your own /32 and switch CI to a self-hosted runner, IAP tunnelling
    or a WireGuard hop if you want this closed.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "http_ingress_cidrs" {
  description = "Who may reach 80/443. Public — this is the Caddy gateway that terminates TLS for app.stratai.live and the WSS feeds."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "control_ports" {
  description = <<-EOT
    Raw TCP data-plane ports that cannot sit behind the HTTPS gateway:

      8085 — ingestion control port (`subscribe:` / `option_chain_set:` commands)
      8087 — Kite OAuth redirect target
      8812 — QuestDB PostgreSQL wire protocol

    These were open to `0.0.0.0/0` on the droplet. That is NOT reproduced here, and
    the change is deliberate: 8085 takes newline-delimited commands with NO
    authentication of any kind — anything that can reach it can repoint the market
    data feed — and 8812 is a database port. `control_ingress_cidrs` starts closed.
  EOT
  type        = list(string)
  default     = ["8085", "8087", "8812"]
}

variable "control_ingress_cidrs" {
  description = <<-EOT
    Who may reach `control_ports`. EMPTY by default, which creates no rule at all.

    Set it to the addresses that genuinely need them — your office /32 for the
    QuestDB wire port, Zerodha's redirect for 8087 if you move the OAuth flow off
    the gateway. Leaving it empty is safe: nothing in the web app uses these, since
    the browser reaches QuestDB and deep-quant through Caddy on 443.
  EOT
  type        = list(string)
  default     = []
}

// ─────────────────────────────────────────────────────────────────────────────
// Optional
// ─────────────────────────────────────────────────────────────────────────────

variable "enable_snapshot_schedule" {
  description = <<-EOT
    Daily boot-disk snapshots, retained 7 days.

    ON by default, unlike the droplet's `enable_backups = false`. The droplet was
    deleted with its QuestDB volume, its Grafana database and its append-only
    compliance record inside it, and none of that was recoverable. A few rupees a
    day is the cheapest insurance in this entire configuration.
  EOT
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to every resource that accepts them."
  type        = map(string)
  default = {
    app        = "stratai"
    managed-by = "terraform"
  }
}
