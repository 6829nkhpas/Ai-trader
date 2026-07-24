# ── Availability domains in the region ───────────────────────────────────────
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# ── Latest Canonical Ubuntu aarch64 image for the A1.Flex shape ───────────────
data "oci_core_images" "ubuntu_arm" {
  compartment_id           = local.compartment_id
  operating_system         = var.os_name
  operating_system_version = var.os_version
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# ── The Always-Free ARM instance ──────────────────────────────────────────────
resource "oci_core_instance" "app" {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  compartment_id      = local.compartment_id
  display_name        = var.instance_name
  shape               = var.instance_shape

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu_arm.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.subnet.id
    assign_public_ip = true
    hostname_label   = "stratai"
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
    user_data           = base64encode(file("${path.module}/cloud-init.yaml"))
  }

  # Boot volume / image swaps shouldn't force needless replacement on re-plan.
  lifecycle {
    ignore_changes = [source_details[0].source_id]
  }
}
