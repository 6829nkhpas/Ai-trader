# ──────────────────────────────────────────────────────────────────────────────
# Credentials (secret) — supplied via terraform.tfvars locally, TF_VAR_* in CI
# ──────────────────────────────────────────────────────────────────────────────
variable "do_token" {
  description = "DigitalOcean personal access token (read/write)."
  type        = string
  sensitive   = true
}

# ──────────────────────────────────────────────────────────────────────────────
# SSH
# ──────────────────────────────────────────────────────────────────────────────
variable "ssh_public_key_path" {
  description = "Path to the SSH public key (OpenSSH format) uploaded to DigitalOcean and injected into the droplet."
  type        = string
  default     = "../keys/stratai_deploy.pub"
}

variable "ssh_key_name" {
  description = "Name for the uploaded SSH key in the DO account."
  type        = string
  default     = "stratai-key"
}

# ──────────────────────────────────────────────────────────────────────────────
# Droplet
# ──────────────────────────────────────────────────────────────────────────────
variable "droplet_name" {
  description = "Droplet name / hostname."
  type        = string
  default     = "stratai-beta"
}

variable "region" {
  description = "DO region slug. blr1 = Bangalore (lowest latency to NSE/India)."
  type        = string
  default     = "blr1"
}

variable "droplet_size" {
  description = "Droplet size slug. s-4vcpu-8gb = 4 vCPU / 8 GB (~$48/mo). Bump to s-8vcpu-16gb for comfortable headroom."
  type        = string
  default     = "s-4vcpu-8gb"
}

variable "droplet_image" {
  description = "Base image slug (x86_64)."
  type        = string
  default     = "ubuntu-22-04-x64"
}

variable "enable_backups" {
  description = "Enable DO automated weekly backups (adds ~20% to droplet cost). Off by default to minimize spend."
  type        = bool
  default     = false
}

# ──────────────────────────────────────────────────────────────────────────────
# Firewall
# ──────────────────────────────────────────────────────────────────────────────
variable "ssh_ingress_cidrs" {
  description = "CIDRs allowed to reach SSH (22). Narrow to your IP/32 for better security."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}

variable "app_ports" {
  description = "Public TCP ports for the data-plane services (desktop clients connect here). Datastore ports stay private."
  type        = list(string)
  default     = ["8080", "8081", "8082", "8083", "8085", "8087"]
}

variable "app_ingress_cidrs" {
  description = "CIDRs allowed to reach the app ports."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}
