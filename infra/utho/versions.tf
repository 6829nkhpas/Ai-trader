terraform {
  required_version = ">= 1.6.0"

  required_providers {
    utho = {
      # FULLY QUALIFIED ON PURPOSE. The provider is published to HashiCorp's
      # registry but is NOT mirrored by the OpenTofu registry, which is where
      # `tofu` looks by default. A bare "uthoplatforms/utho" source fails at
      # init with:
      #
      #   provider registry registry.opentofu.org does not have a provider
      #   named registry.opentofu.org/uthoplatforms/utho
      #
      # Naming the host explicitly makes both `tofu` and `terraform` resolve it.
      # (The DigitalOcean provider in ../ needs no such prefix — it is on both.)
      source = "registry.terraform.io/uthoplatforms/utho"

      # PINNED DELIBERATELY. v0.6.4 (2025-05-25) is the newest of six published
      # releases and the provider is not actively developed (1 star, 6 forks, no
      # commits since). A floating constraint on an unmaintained provider buys
      # nothing and risks a surprise break, so we hold a known-tested version.
      version = "~> 0.6.4"
    }
  }

  # Local state by default, same as the DigitalOcean config in ../.
  #
  # WORTH REVISITING: local state on one laptop is a single point of failure for
  # infrastructure you are billed for — lose it and the instance keeps running
  # with no way to manage or destroy it via Terraform. Utho offers S3-compatible
  # object storage (see the `utho_object_storage_plan` data source), which would
  # work as an `s3` backend and double as an off-host QuestDB backup target.
  # backend "s3" {}
}
