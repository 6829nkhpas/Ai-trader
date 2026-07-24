# ──────────────────────────────────────────────────────────────────────────────
# OCI provider — API-key auth.
# Credentials come from variables (TF_VAR_* in CI, terraform.tfvars locally).
# The private key is referenced by PATH and never committed (keys/ is gitignored).
# ──────────────────────────────────────────────────────────────────────────────
provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}
