# Utho provider. The API token is a secret supplied via terraform.tfvars locally
# (gitignored) or TF_VAR_utho_token in CI.
#
# Get one: Utho console (https://console.utho.com) -> API -> generate token.
provider "utho" {
  token = var.utho_token
}
