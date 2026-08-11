#!/usr/bin/env bash
# redeploy.sh — one-shot full redeploy on the droplet.
#
# Syncs the repo to origin/$DEPLOY_BRANCH (default: main), rebuilds every
# service, and (re)starts the whole stack so any pushed change takes effect.
# Safe to run repeatedly.
#
#   • Untracked files (notably the gitignored .env) are PRESERVED by the hard
#     reset, so server secrets/config survive.
#   • Rust/Python builds reuse Docker layer cache, so unchanged services rebuild
#     near-instantly; only what actually changed is recompiled.
#   • Old containers keep serving until each new image is built, then `up -d`
#     swaps them — minimal downtime.
#
# Usage (on the droplet):
#   GITHUB_TOKEN=$(gh auth token) bash /root/Ai-trader/redeploy.sh
#   DEPLOY_BRANCH=develop GITHUB_TOKEN=... bash redeploy.sh   # deploy a branch
#   bash /root/Ai-trader/redeploy.sh --force    # also force-recreate ALL containers
#   FORCE_RECREATE=1 bash redeploy.sh           # same as --force
#
# GITHUB_TOKEN is REQUIRED (private repo). In CI it is injected automatically.
set -euo pipefail

cd "$(dirname "$0")"
export DOCKER_BUILDKIT=1

# Which compose files to use. The default keeps the 8 GB memory override, which
# is correct for the 4vCPU/8GB DigitalOcean droplet.
#
# On a 16 GB host, set this to the base file alone so the stack gets the real
# limits instead of the squeezed ones:
#
#   echo 'COMPOSE_FILES="-f docker-compose.prod.yml"' >> /etc/environment
#   # or, per invocation:
#   COMPOSE_FILES="-f docker-compose.prod.yml" bash redeploy.sh
#
# This is an env var rather than host detection on purpose: CI invokes this
# script over SSH, and a silent wrong guess would cap a 16 GB box at 8 GB limits
# with no visible symptom beyond unexplained memory pressure.
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.prod.yml -f docker-compose.8gb.yml}"
COMPOSE="docker compose $COMPOSE_FILES"

FORCE_RECREATE="${FORCE_RECREATE:-0}"
[ "${1:-}" = "--force" ] && FORCE_RECREATE=1

log() { echo "=== [$(date +'%Y-%m-%d %H:%M:%S')] $* ==="; }

log "Redeploy start"
log "Compose files: $COMPOSE_FILES"

# ── 1. Sync to origin/main (preserves untracked .env) ────────────────────────
#
# This is a PRIVATE repo, so the fetch needs credentials. The droplet stores
# none: CI passes a short-lived token in GITHUB_TOKEN and it is used for this
# one fetch only, via `-c http.extraheader`. That keeps it out of the remote URL
# (which git would otherwise persist into .git/config), out of `git remote -v`,
# and out of the process list in a form that survives the run.
#
# Org policy has deploy keys disabled, so a server-side SSH key is not an
# option here; a per-run token is also the better posture — nothing to rotate
# or leak on the box, and it expires when the job ends.
#
# Running by hand on the droplet: export GITHUB_TOKEN=<a PAT with repo:read>
# first, or the fetch will fail asking for a username.
# Which branch to deploy. CI passes the branch that triggered the run
# (`github.ref_name`), so a push to develop deploys develop and a push to main
# deploys main — the droplet is no longer pinned to whatever branch it happened
# to be checked out on. Defaults to main for a bare manual invocation.
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"

log "Syncing to origin/$DEPLOY_BRANCH"

# Never let git block on an interactive credential prompt. Without this, missing
# or unusable auth surfaces as:
#   fatal: could not read Username for 'https://github.com': No such device or address
# which reads like a broken remote when the real cause is absent credentials.
# With it, the failure names itself and the run stops immediately.
export GIT_TERMINAL_PROMPT=0

if [ -n "${GITHUB_TOKEN:-}" ]; then
  AUTH_HEADER="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)"
  git -c http.extraheader="$AUTH_HEADER" fetch origin --prune
else
  log "ERROR: GITHUB_TOKEN is unset. This is a PRIVATE repo, so the fetch cannot"
  log "       succeed without it. CI supplies it automatically; when running by"
  log "       hand use:  GITHUB_TOKEN=\$(gh auth token) bash redeploy.sh"
  exit 1
fi

git reset --hard "origin/$DEPLOY_BRANCH"
git log --oneline -1

# ── 2. Build every service (sequential — keeps concurrent Rust compiles from
#       exhausting memory on a small box; cached layers make unchanged services
#       fast, so the serial cost is low even on a 16 GB host) ─────────────────
for svc in ingestion alpha-terminal technical aggregator predictive quant-rag sentiment tool-server deep-quant; do
  log "Building $svc"
  $COMPOSE build "$svc"
done

# ── 3. (Re)start the stack ───────────────────────────────────────────────────
if [ "$FORCE_RECREATE" = "1" ]; then
  log "Starting stack (force-recreate ALL)"
  $COMPOSE up -d --force-recreate
else
  log "Starting stack (recreate changed)"
  $COMPOSE up -d
fi

log "DEPLOY DONE"
$COMPOSE ps
