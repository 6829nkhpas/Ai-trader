// ── Service account ──────────────────────────────────────────────────────────
//
// A dedicated account with only logging and monitoring write, instead of the
// project default service account with the default (broad) scopes. Nothing in the
// stack calls a Google API — it talks to Kite, QuestDB and Redpanda — so the VM has
// no business holding a credential that can reach GCS or Compute.

resource "google_service_account" "vm" {
  account_id   = "${var.name}-vm"
  display_name = "StratAI VM"
  description  = "Runs the StratAI compose stack. Logging/monitoring write only."
}

resource "google_project_iam_member" "logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_member" "metrics" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

// ── The instance ─────────────────────────────────────────────────────────────

resource "google_compute_instance" "app" {
  name         = "${var.name}-vm"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["${var.name}-vm"]
  labels       = var.labels

  boot_disk {
    initialize_params {
      image  = var.boot_image
      size   = var.boot_disk_size_gb
      type   = var.boot_disk_type
      labels = var.labels
    }
    # Keep the disk if the instance is replaced by mistake. The droplet's data was
    # unrecoverable precisely because nothing outlived the instance.
    auto_delete = false
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id

    access_config {
      nat_ip = google_compute_address.static.address
    }
  }

  service_account {
    email = google_service_account.vm.email
    # Modern scope model: IAM roles above decide what it can do, not scopes.
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  metadata = {
    # Ubuntu images run cloud-init, so the droplet's bootstrap carries over almost
    # unchanged — see cloud-init.yaml.
    user-data = templatefile("${path.module}/cloud-init.yaml", {
      deploy_user = var.deploy_user
      deploy_path = var.deploy_path
    })

    ssh-keys = "${var.deploy_user}:${trimspace(file(var.ssh_public_key_path))}"

    # OS Login OFF, deliberately. It is the better model in general, but it replaces
    # key-based SSH with an IAM/gcloud flow, and `deploy-server.yml` authenticates
    # with a raw private key via appleboy/ssh-action. Turning this on means
    # reworking CI auth first.
    enable-oslogin = "FALSE"

    # Serial console off: it is an out-of-band root path, and the boot log is
    # readable with `gcloud compute instances get-serial-port-output` anyway.
    serial-port-enable = "FALSE"
  }

  # Live migration, so Google's host maintenance does not take the market feed down
  # mid-session. The alternative (TERMINATE) would drop the Kite WebSocket and every
  # in-flight agent run.
  scheduling {
    on_host_maintenance = "MIGRATE"
    automatic_restart   = true
    preemptible         = false
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # Docker needs to forward between containers and out to the internet.
  can_ip_forward = false

  # `user-data` changes rebuild the box, which would wipe the Docker volumes. Boot
  # config is a one-time bootstrap; day-to-day changes go through redeploy.sh, so an
  # edit to cloud-init.yaml must NOT quietly recreate the instance.
  lifecycle {
    ignore_changes = [metadata["user-data"]]
  }
}

// ── Daily disk snapshots ─────────────────────────────────────────────────────

resource "google_compute_resource_policy" "daily_snapshot" {
  count = var.enable_snapshot_schedule ? 1 : 0

  name   = "${var.name}-daily-snapshot"
  region = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        # 20:00 UTC = 01:30 IST — after the NSE close and well clear of the
        # pre-market token refresh. GCP only accepts whole hours here (HH:00),
        # so this cannot be nudged to 20:30.
        start_time = "20:00"
      }
    }

    retention_policy {
      max_retention_days    = 7
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }

    snapshot_properties {
      storage_locations = [var.region]
      labels            = var.labels
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "boot" {
  count = var.enable_snapshot_schedule ? 1 : 0

  name = google_compute_resource_policy.daily_snapshot[0].name
  disk = google_compute_instance.app.name
  zone = var.zone
}
