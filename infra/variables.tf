# ──────────────────────────────────────────────────────────────────────────────
# Authentication (secrets — supplied via terraform.tfvars locally or TF_VAR_* in CI)
# ──────────────────────────────────────────────────────────────────────────────
variable "tenancy_ocid" {
  description = "OCID of the tenancy."
  type        = string
}

variable "user_ocid" {
  description = "OCID of the API user."
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the API signing key."
  type        = string
}

variable "private_key_path" {
  description = "Local filesystem path to the API private key (PEM). Never commit this file."
  type        = string
}

variable "region" {
  description = "OCI region identifier."
  type        = string
  default     = "ap-mumbai-1"
}

variable "compartment_ocid" {
  description = "Compartment OCID to create resources in. Defaults to the tenancy (root) compartment."
  type        = string
  default     = ""
}

# ──────────────────────────────────────────────────────────────────────────────
# SSH access
# ──────────────────────────────────────────────────────────────────────────────
variable "ssh_public_key_path" {
  description = "Path to the SSH PUBLIC key (OpenSSH format, e.g. 'ssh-rsa AAAA...') injected into the instance. NOTE: OCI rejects PEM-format public keys — use OpenSSH format."
  type        = string
  default     = "../keys/thestratai_ssh.pub"
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to reach SSH (22). Narrow this to your IP/32 for better security."
  type        = string
  default     = "0.0.0.0/0"
}

# ──────────────────────────────────────────────────────────────────────────────
# Instance sizing — Always Free Ampere A1 budget is 4 OCPU / 24 GB total.
# ──────────────────────────────────────────────────────────────────────────────
variable "instance_name" {
  description = "Display name / hostname label for the instance."
  type        = string
  default     = "stratai-beta"
}

variable "instance_shape" {
  description = "Compute shape. A1.Flex is the Always Free ARM shape."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "OCPUs (Always Free ceiling across all A1 instances is 4)."
  type        = number
  default     = 4
}

variable "instance_memory_gbs" {
  description = "Memory in GB (Always Free ceiling across all A1 instances is 24)."
  type        = number
  default     = 24
}

variable "boot_volume_gbs" {
  description = "Boot volume size in GB. Always Free block storage total is 200 GB."
  type        = number
  default     = 100
}

variable "availability_domain_index" {
  description = "Index into the region's availability domains. Try 1 or 2 if A1 capacity is exhausted in AD-0."
  type        = number
  default     = 0
}

# ──────────────────────────────────────────────────────────────────────────────
# OS image selection (Canonical Ubuntu, ARM aarch64)
# ──────────────────────────────────────────────────────────────────────────────
variable "os_name" {
  description = "Operating system for the image lookup."
  type        = string
  default     = "Canonical Ubuntu"
}

variable "os_version" {
  description = "Operating system version for the image lookup."
  type        = string
  default     = "22.04"
}

# ──────────────────────────────────────────────────────────────────────────────
# Networking
# ──────────────────────────────────────────────────────────────────────────────
variable "vcn_cidr" {
  description = "CIDR block for the VCN."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet."
  type        = string
  default     = "10.0.1.0/24"
}

variable "app_ports" {
  description = "Public TCP ports for the data-plane services (desktop clients connect here). Datastore ports are intentionally excluded and stay private."
  type        = list(number)
  default     = [8080, 8081, 8082, 8083, 8085, 8087]
}

variable "app_ingress_cidr" {
  description = "CIDR allowed to reach the app ports. Narrow to your beta users' networks if known."
  type        = string
  default     = "0.0.0.0/0"
}
