# ──────────────────────────────────────────────────────────────────────────────
# OpenTofu core + provider requirements
# ──────────────────────────────────────────────────────────────────────────────
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.30.0"
    }
  }

  # ── Remote state (enable for CI / team use) ────────────────────────────────
  # State is kept LOCAL by default so the first manual apply works with zero
  # bootstrap. For GitHub Actions we use OCI Object Storage via its
  # S3-compatible endpoint (Always Free: 20 GB). See infra/backend.hcl.example
  # and infra/README.md §"Remote state". To enable, uncomment and run:
  #   tofu init -backend-config=backend.hcl -reconfigure
  #
  # backend "s3" {
  #   # values supplied via -backend-config=backend.hcl (keeps secrets out of git)
  # }
}
