# ── Upload the SSH public key ─────────────────────────────────────────────────
resource "digitalocean_ssh_key" "key" {
  name       = var.ssh_key_name
  public_key = file(var.ssh_public_key_path)
}

# ── The droplet ───────────────────────────────────────────────────────────────
resource "digitalocean_droplet" "app" {
  name       = var.droplet_name
  region     = var.region
  size       = var.droplet_size
  image      = var.droplet_image
  ssh_keys   = [digitalocean_ssh_key.key.fingerprint]
  backups    = var.enable_backups
  monitoring = true
  ipv6       = true
  user_data  = file("${path.module}/cloud-init.yaml")

  tags = ["stratai", "beta"]
}

# ── Cloud Firewall ────────────────────────────────────────────────────────────
# Public ingress ONLY for SSH + data-plane app ports. Datastore ports
# (QuestDB/Kafka/Postgres/Redis) are bound to localhost in the compose file and
# never exposed. Egress fully open.
resource "digitalocean_firewall" "fw" {
  name        = "${var.droplet_name}-fw"
  droplet_ids = [digitalocean_droplet.app.id]

  # SSH
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_ingress_cidrs
  }

  # Data-plane app ports
  dynamic "inbound_rule" {
    for_each = toset(var.app_ports)
    content {
      protocol         = "tcp"
      port_range       = inbound_rule.value
      source_addresses = var.app_ingress_cidrs
    }
  }

  # ICMP (ping / path MTU)
  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # Egress: allow all
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}
