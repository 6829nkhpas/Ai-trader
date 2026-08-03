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

    CONFIRM the exact slug before apply — zone names change:
      curl -H "Authorization: Bearer $UTHO_TOKEN" \
        https://api.utho.com/v2/cloud/availabledczones
  EOT
  type        = string
  default     = "inmumbaizone2"
}

variable "planid" {
  description = <<-EOT
    Plan ID for the 16 GB instance. Utho plan IDs are opaque numeric strings
    (the provider's own example uses "10045"), so this MUST be looked up — a
    guessed ID either fails or silently provisions the wrong size.

      curl -H "Authorization: Bearer $UTHO_TOKEN" \
        https://api.utho.com/v2/cloud/getplans

    No default on purpose: an accidental apply should error, not bill you for
    whatever plan happened to be first in the list.
  EOT
  type        = string
}

variable "image" {
  description = <<-EOT
    OS image slug. Matches the Ubuntu 22.04 x86_64 base the DigitalOcean droplet
    uses, so every Dockerfile builds unchanged.

    Confirm available images with the utho_images data source (see images.tf) or:
      curl -H "Authorization: Bearer $UTHO_TOKEN" https://api.utho.com/v2/cloud/images
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
