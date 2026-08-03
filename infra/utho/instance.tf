# ──────────────────────────────────────────────────────────────────────────────
# Firewall
# ──────────────────────────────────────────────────────────────────────────────
# ⚠️  RULES ARE NOT DEFINED HERE — AND CANNOT BE.
#
# The utho_firewall resource schema accepts exactly one argument: `name`. There
# is no inbound_rule/outbound_rule block and no separate utho_firewall_rule
# resource in the provider (v0.6.4). This is a hard capability gap versus
# digitalocean_firewall in ../droplet.tf, which defines all rules in code.
#
# Consequence: Terraform creates and attaches the firewall, but the actual
# ingress rules are console/API state that Terraform will neither create nor
# detect drift on. The required rule set is documented in FIREWALL.md — that file
# is the source of truth. Keep it updated, because nothing enforces it.
resource "utho_firewall" "fw" {
  name = var.firewall_name
}

# ──────────────────────────────────────────────────────────────────────────────
# The instance
# ──────────────────────────────────────────────────────────────────────────────
# 16 GB replaces the 4vCPU/8GB DigitalOcean droplet. The extra headroom means the
# stack runs on docker-compose.prod.yml's real memory limits — the
# docker-compose.8gb.yml override exists only to squeeze a ~9.5 GB stack into
# 8 GB and should NOT be applied here (see COMPOSE_FILES in ../../redeploy.sh).
resource "utho_cloud_instance" "app" {
  name          = var.instance_name
  dcslug        = var.dcslug
  image         = var.image
  planid        = var.planid
  root_password = var.root_password
  sshkeys       = var.ssh_key_ids
  firewall      = utho_firewall.fw.id

  enable_publicip = "true"
  enablebackup    = var.enable_backups
  billingcycle    = var.billingcycle

  # NO cloud-init. The provider exposes no user_data field, so ../cloud-init.yaml
  # (which bootstraps Docker on the DigitalOcean droplet) cannot be used. Its
  # steps are ported to bootstrap.sh and run over SSH after apply.
  #
  # root_password is required above and is stored in tfstate; bootstrap.sh
  # disables SSH password auth to close that exposure.

  lifecycle {
    # This host holds the QuestDB volume. A replacement would silently discard
    # historical_candles, historical_intraday, and the unreconstructable
    # option_chain_snapshots. Force a deliberate `tofu destroy` instead.
    prevent_destroy = true
  }
}
