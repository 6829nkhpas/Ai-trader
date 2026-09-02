output "instance_ip" {
  description = "Reserved public IPv4. Point every DNS A record at this, and set it as the DEPLOY_HOST repo variable."
  value       = google_compute_address.static.address
}

output "instance_name" {
  description = "GCE instance name."
  value       = google_compute_instance.app.name
}

output "ssh_command" {
  description = "SSH into the VM with the deploy key."
  value       = "ssh -i ../../keys/stratai_gcp ${var.deploy_user}@${google_compute_address.static.address}"
}

output "dns_records_required" {
  description = <<-EOT
    A records that must point at THIS instance before TLS can be issued. Caddy uses
    ACME HTTP-01, so each name has to resolve here first or issuance fails and
    Let's Encrypt starts rate-limiting.

    ONLY these two. `infra/caddy/Caddyfile` defines exactly two vhosts —
    `app.stratai.live` and `app-api.stratai.live` — and they are the only names this
    box can serve.

    `dashboard`, `auth` and `api-web` are SEPARATE deployments (currently on
    216.198.79.65 and 75.2.43.161, and live). An earlier version of this output
    listed them too; repointing them here would take three working services down and
    hand them to a Caddy that has no vhost for them.
  EOT
  value = {
    "app.stratai.live"     = google_compute_address.static.address
    "app-api.stratai.live" = google_compute_address.static.address
  }
}

output "ci_repo_variables" {
  description = "The GitHub Actions repo variables deploy-server.yml reads. DEPLOY_USER and DEPLOY_PATH are NOT optional here — the droplet's root/-/root/Ai-trader defaults do not apply on GCP."
  value = {
    DEPLOY_HOST = google_compute_address.static.address
    DEPLOY_USER = var.deploy_user
    DEPLOY_PATH = var.deploy_path
  }
}

output "next_steps" {
  description = "Ordered bootstrap checklist. See README.md for the detail behind each step."
  value = join("\n", [
    "1. Point the DNS A records above at ${google_compute_address.static.address} and wait for propagation.",
    "2. Seed the charting library:  cd frontend/public/static && tar --exclude=.git -czf /tmp/cl.tgz charting_library",
    "                              scp -i keys/stratai_gcp /tmp/cl.tgz ${var.deploy_user}@${google_compute_address.static.address}:/tmp/",
    "                              ssh ... 'tar xzf /tmp/cl.tgz -C /srv/vendor'",
    "3. Clone the repo to ${var.deploy_path} as ${var.deploy_user}.",
    "4. Copy .env to ${var.deploy_path}/.env  (NOT in git — includes KITE_API_KEY/SECRET/ACCESS_TOKEN, QUESTDB_PASSWORD, LLM keys).",
    "5. Set the DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH repo variables and the DEPLOY_SSH_KEY secret to the NEW key.",
    "6. Run redeploy.sh once by hand, then verify /api/features returns 200 over HTTPS.",
  ])
}
