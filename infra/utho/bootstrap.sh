#!/usr/bin/env bash
# bootstrap.sh — host bootstrap for the Utho instance.
#
# WHY THIS FILE EXISTS: the Utho Terraform provider (v0.6.4) exposes no
# `user_data` field on utho_cloud_instance, so ../cloud-init.yaml — which
# bootstraps Docker on the DigitalOcean droplet at first boot — cannot be used.
# These are the same steps, delivered over SSH after `tofu apply` instead.
#
# Idempotent: safe to re-run.
#
# Usage from the repo root:
#   IP=$(cd infra/utho && tofu output -raw instance_ip)
#   scp -i keys/stratai_deploy infra/utho/bootstrap.sh root@"$IP":/root/
#   ssh -i keys/stratai_deploy root@"$IP" 'bash /root/bootstrap.sh'
set -euo pipefail

log() { echo "=== [$(date +'%Y-%m-%d %H:%M:%S')] $* ==="; }

# ── 1. Base packages ─────────────────────────────────────────────────────────
log "Installing base packages"
apt-get update
apt-get install -y ca-certificates curl git gnupg

# ── 2. Docker Engine + compose plugin ────────────────────────────────────────
# Same official-repo install as cloud-init.yaml.
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  log "Docker already present — skipping install"
fi

systemctl enable --now docker

# ── 3. Harden SSH ────────────────────────────────────────────────────────────
# The Utho API REQUIRES a root password and it is persisted in terraform.tfstate.
# Key auth is what we actually use, so close the password door.
#
# GUARD: refuse to disable passwords unless an authorized key is present —
# otherwise a misconfigured key would lock us out of our own host.
log "Hardening SSH"
if [ -s /root/.ssh/authorized_keys ]; then
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  grep -q '^PasswordAuthentication no' /etc/ssh/sshd_config \
    || echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config
  systemctl reload ssh
  log "SSH password auth DISABLED (key auth only)"
else
  log "WARNING: /root/.ssh/authorized_keys is empty or missing."
  log "WARNING: leaving password auth ENABLED to avoid locking you out."
  log "WARNING: fix the SSH key, then re-run this script."
fi

# ── 4. Report ────────────────────────────────────────────────────────────────
log "Bootstrap complete"
docker --version
docker compose version
echo
echo "CPU cores : $(nproc)"
echo "Memory    : $(free -h | awk '/^Mem:/{print $2}')"
echo "Disk      : $(df -h / | awk 'NR==2{print $2" total, "$4" free"}')"
echo
echo "Expect ~16 GB memory. If this reports 8 GB, the planid resolved to the"
echo "wrong plan — stop here and fix it before migrating data."
