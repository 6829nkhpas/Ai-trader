terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30.0"
    }
  }

  # Local state by default, as the retired DigitalOcean module had.
  #
  # Worth changing before this is the only copy of anything. The DO state ended up
  # with `.RETIRED` / `.old-account.bak` siblings, which is what local state looks
  # like once more than one person or machine touches it. A GCS backend costs
  # pennies and gives locking:
  #
  #   terraform {
  #     backend "gcs" {
  #       bucket = "stratai-tfstate"
  #       prefix = "infra/gcp"
  #     }
  #   }
  #
  # Create the bucket FIRST (outside Terraform) with versioning on, then
  # `terraform init -migrate-state`.
}
