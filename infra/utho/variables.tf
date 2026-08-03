# ──────────────────────────────────────────────────────────────────────────────
# Credentials (secret) — supplied via terraform.tfvars locally, TF_VAR_* in CI
# ──────────────────────────────────────────────────────────────────────────────
variable "utho_token" {
  description = "Utho API token (console -> API). Read/write."
  type        = string
  sensitive   = true
}

variable "root_password" {
  description = <<-EOT
    Root password for the instance. REQUIRED by the Utho API — unlike
    DigitalOcean, there is no key-only provisioning path.

    Generate a long random value:
      openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32

    SECURITY: this value is persisted in plaintext inside terraform.tfstate.
    bootstrap.sh disables SSH password authentication as its final step, after
    which this is a console-recovery credential only. Treat the state file as a
    secret regardless — it is gitignored here.
  EOT
  type        = string
  sensitive   = true
}

# ──────────────────────────────────────────────────────────────────────────────
# SSH
# ──────────────────────────────────────────────────────────────────────────────
variable "ssh_key_ids" {
  description = <<-EOT
    Comma-separated Utho SSH key IDs (e.g. "432" or "432,331").

    NOTE: the provider has NO ssh_key resource — unlike DigitalOcean, the key
    cannot be uploaded from Terraform. Create it in the console first
    (Console -> Settings -> SSH Keys), paste the contents of
    ../../keys/stratai_deploy.pub, then put the resulting numeric ID here.

    Reuse the EXISTING keys/stratai_deploy keypair: it is already the
    DEPLOY_SSH_KEY GitHub secret, so CI keeps working with no rotation.
  EOT
  type        = string
}

# ──────────────────────────────────────────────────────────────────────────────
# Instance
# ──────────────────────────────────────────────────────────────────────────────
variable "instance_name" {
  description = "Instance name / hostname."
  type        = string
  default     = "stratai-prod"
}

variable "dcslug" {
  description = <<-EOT
    Datacenter zone slug. Mumbai is the right choice: NSE's matching engines are
    there, so this LOWERS market-data latency versus the current DigitalOcean
    blr1 (Bangalore) host.

    NOT API-VERIFIABLE — Utho's zone-list endpoints are broken. Every
    unrecognised /v2/cloud/* path falls through to a catch-all returning HTTP 200
    with a 1-byte body and the header `X-Debug-Marker: plans-handler-v2`
    (confirmed by requesting a deliberately nonsense path, which behaves
    identically). /cloud/availabledczones, /cloud/dcslug and /cloud/dczones all
    "succeed" while returning nothing.

    This value is the provider's own documented example. CONFIRM it against the
    zone dropdown in the console before applying: for a latency-sensitive
    market-data pipeline, silently landing in the wrong region defeats the point
    of the move.
  EOT
  type        = string
  default     = "inmumbaizone2"
}

variable "planid" {
  description = <<-EOT
    Plan ID. Default 10316 = "basic-103": 6 vCPU, 16 GB RAM, 320 GB NVMe,
    1000 GB bandwidth, INR 5514/mo. VERIFIED against GET /v2/pricing.

    Chosen over the other two 16 GB plans that include disk because it is the
    only one that is also a CPU *upgrade* on the 4-vCPU DigitalOcean box, and it
    carries the largest disk — which matters because QuestDB history only grows:

      10325  Dedicated-Memory-102     2 vCPU  160 GB  INR 3420/mo  (2.9 mo credit)
      10316  basic-103                6 vCPU  320 GB  INR 5514/mo  <- this (1.8 mo)
      10364  Dedicated-CPU-4-16G-200  4 vCPU  240 GB  INR 7614/mo  (1.3 mo, dedicated)

    2 vCPU was rejected despite the longer runway: redeploy.sh compiles six Rust
    services on the host, and deploy-server.yml allows SSH only 40 minutes before
    timing out.

    Plans reporting disk=0 require a separately billed volume — avoid unless you
    intend to attach one.
  EOT
  type        = string
  default     = "10316"
}

variable "image" {
  description = <<-EOT
    OS image slug. VERIFIED present with cost 0 via GET /v2/cloud/images
    (which also offers ubuntu-20.04/24.04/25.04-x86_64).

    Held at 22.04 to match infra/cloud-init.yaml and the current DigitalOcean
    host, so bootstrap.sh follows the Docker install path already proven in
    production. Moving to 24.04 should be a separate, deliberate change.
  EOT
  type        = string
  default     = "ubuntu-22.04-x86_64"
}

variable "billingcycle" {
  description = "hourly | monthly | 3month | 6month | 12month. Monthly is cheaper than hourly for a long-lived host."
  type        = string
  default     = "monthly"
}

variable "enable_backups" {
  description = <<-EOT
    Weekly backups. Default TRUE here, deliberately diverging from the
    DigitalOcean config (which defaults false to save ~20%).

    This box holds the QuestDB volume — and option_chain_snapshots is
    point-in-time data that CANNOT be reconstructed from Kite. Backups are cheap
    insurance, and the Utho credits are there to spend.
  EOT
  type        = bool
  default     = true
}

variable "firewall_name" {
  description = "Name of the Utho firewall attached to the instance. Rules are NOT managed here — see FIREWALL.md."
  type        = string
  default     = "stratai-prod-fw"
}
