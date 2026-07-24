# DigitalOcean provider. The API token is a secret supplied via terraform.tfvars
# locally (gitignored) or TF_VAR_do_token / DIGITALOCEAN_TOKEN in CI.
provider "digitalocean" {
  token = var.do_token
}
