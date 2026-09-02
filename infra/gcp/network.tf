// Network. A dedicated VPC rather than `default`, for two reasons: new projects can
// be created without a default network at all, and the default network ships
// permissive pre-made rules (including `default-allow-internal` across the whole
// 10/8) that are awkward to reason about for a box holding a market-data database.

resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  description             = "StratAI trading stack — single-VM VPC."
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.name}-subnet"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.10.0.0/24"

  # Flow logs off: this is one VM with no internal traffic worth sampling, and they
  # bill per GB.
  private_ip_google_access = true
}

// ── Static external IP ───────────────────────────────────────────────────────
//
// Reserved, NOT ephemeral. The droplet's IP was hardcoded into DNS
// (app.stratai.live, app-api.stratai.live, dashboard, auth) and into the
// `DEPLOY_HOST` repo variable. An ephemeral IP is released on instance STOP, so a
// resize — the one thing the compose comments explicitly anticipate — would silently
// break DNS and CI together.
resource "google_compute_address" "static" {
  name         = "${var.name}-ip"
  region       = var.region
  address_type = "EXTERNAL"
  description  = "Stable public IP for DNS A records and the CI DEPLOY_HOST."
  labels       = var.labels
}

// ── Firewall ─────────────────────────────────────────────────────────────────
//
// Mirrors the retired DO Cloud Firewall, with the control ports closed by default
// (see `control_ingress_cidrs`). Rules attach by network tag, so the instance opts
// in explicitly.

resource "google_compute_firewall" "ssh" {
  name        = "${var.name}-allow-ssh"
  network     = google_compute_network.vpc.name
  description = "SSH for CI deploys and operators."
  direction   = "INGRESS"
  priority    = 1000

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_ingress_cidrs
  target_tags   = ["${var.name}-vm"]
}

resource "google_compute_firewall" "http" {
  name        = "${var.name}-allow-http"
  network     = google_compute_network.vpc.name
  description = "Caddy gateway: TLS for the web app, the /questdb /deepquant /kite /tools proxies and the WSS feeds. 80 is required for ACME HTTP-01."
  direction   = "INGRESS"
  priority    = 1000

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = var.http_ingress_cidrs
  target_tags   = ["${var.name}-vm"]
}

// Created ONLY when someone opts in by setting `control_ingress_cidrs`. `count`
// rather than an empty `source_ranges`, because GCP rejects a rule with no source
// and a half-written rule is worse than no rule.
resource "google_compute_firewall" "control" {
  count = length(var.control_ingress_cidrs) > 0 ? 1 : 0

  name        = "${var.name}-allow-control"
  network     = google_compute_network.vpc.name
  description = "Raw TCP data-plane ports (ingestion control, Kite OAuth, QuestDB PG wire). Unauthenticated — keep the source list tight."
  direction   = "INGRESS"
  priority    = 1000

  allow {
    protocol = "tcp"
    ports    = var.control_ports
  }

  source_ranges = var.control_ingress_cidrs
  target_tags   = ["${var.name}-vm"]
}

// ICMP, as the DO firewall allowed. Ping and path-MTU discovery; dropping ICMP
// breaks MTU negotiation in ways that look like random stalls.
resource "google_compute_firewall" "icmp" {
  name        = "${var.name}-allow-icmp"
  network     = google_compute_network.vpc.name
  description = "Ping and path-MTU discovery."
  direction   = "INGRESS"
  priority    = 1000

  allow {
    protocol = "icmp"
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["${var.name}-vm"]
}

// Egress is unrestricted, matching the droplet. The stack needs Kite
// (api.kite.trade + the tick WebSocket), Docker Hub, the Debian/Ubuntu mirrors,
// GitHub, Let's Encrypt and the news/sentiment upstreams — an allowlist here would
// be a permanent maintenance tax for little gain on a single-tenant box.
resource "google_compute_firewall" "egress" {
  name        = "${var.name}-allow-egress"
  network     = google_compute_network.vpc.name
  description = "Allow all egress (Kite, registries, ACME, package mirrors)."
  direction   = "EGRESS"
  priority    = 1000

  allow {
    protocol = "all"
  }

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = ["${var.name}-vm"]
}
