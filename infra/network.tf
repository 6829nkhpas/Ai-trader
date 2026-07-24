locals {
  # Fall back to the tenancy (root) compartment when none is supplied.
  compartment_id = var.compartment_ocid != "" ? var.compartment_ocid : var.tenancy_ocid
}

# ── Virtual Cloud Network ─────────────────────────────────────────────────────
resource "oci_core_vcn" "vcn" {
  compartment_id = local.compartment_id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.instance_name}-vcn"
  dns_label      = "stratvcn"
}

# ── Internet Gateway (free; no NAT gateway to keep it Always-Free) ────────────
resource "oci_core_internet_gateway" "igw" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${var.instance_name}-igw"
  enabled        = true
}

# ── Route table: default route to the internet ───────────────────────────────
resource "oci_core_route_table" "rt" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${var.instance_name}-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

# ── Security list ─────────────────────────────────────────────────────────────
# Public ingress ONLY for SSH + the data-plane service ports. Datastore ports
# (QuestDB 9000/8812/9009, Kafka 19092, Postgres 5432, Redis 6379) are NOT opened
# here — they are bound to localhost in docker-compose.prod.yml and reached via
# SSH tunnel. Egress is fully open.
resource "oci_core_security_list" "sl" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.vcn.id
  display_name   = "${var.instance_name}-sl"

  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
  }

  # SSH
  ingress_security_rules {
    protocol    = "6" # TCP
    source      = var.ssh_ingress_cidr
    source_type = "CIDR_BLOCK"
    description = "SSH"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # Data-plane app ports (one rule per port)
  dynamic "ingress_security_rules" {
    for_each = toset(var.app_ports)
    content {
      protocol    = "6" # TCP
      source      = var.app_ingress_cidr
      source_type = "CIDR_BLOCK"
      description = "app-port-${ingress_security_rules.value}"
      tcp_options {
        min = ingress_security_rules.value
        max = ingress_security_rules.value
      }
    }
  }

  # Allow inbound ICMP path-MTU / unreachable (recommended by OCI)
  ingress_security_rules {
    protocol    = "1" # ICMP
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    description = "ICMP type 3 code 4 (path MTU)"
    icmp_options {
      type = 3
      code = 4
    }
  }
}

# ── Public subnet ─────────────────────────────────────────────────────────────
resource "oci_core_subnet" "subnet" {
  compartment_id             = local.compartment_id
  vcn_id                     = oci_core_vcn.vcn.id
  cidr_block                 = var.subnet_cidr
  display_name               = "${var.instance_name}-subnet"
  dns_label                  = "stratsub"
  route_table_id             = oci_core_route_table.rt.id
  security_list_ids          = [oci_core_security_list.sl.id]
  prohibit_public_ip_on_vnic = false
}
