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
#
# `frontend` is built LAST and deliberately so. It is the heaviest build in the
# stack by a wide margin: `next build --turbopack` over this tree needs
# significantly more memory than any Rust service, and on the 8 GB droplet the
# running stack already commits ~6.8 GB (see docker-compose.8gb.yml). Building it
# while everything else is up is the most likely thing here to OOM — and the
# process the kernel picks is not necessarily this one, so a frontend build can
# take QuestDB down with it.
#
# If that happens, the options in order of preference are:
#   1. Build the image in CI and pull it here (removes the droplet build entirely);
#   2. `docker compose stop deep-quant quant-rag` for the duration of the build;
#   3. add swap;
#   4. move to a 16 GB instance.
# Do NOT "fix" it by parallelising this loop.
for svc in ingestion alpha-terminal technical aggregator predictive quant-rag sentiment tool-server deep-quant frontend; do
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

# ── 4. Pick up gateway config changes ─────────────────────────────────────────
#
# `up -d` recreates a container only when its SERVICE DEFINITION changed. The
# Caddyfile is a read-only bind mount, so editing it changes no compose field and
# the gateway keeps serving its previously-loaded config indefinitely — a routing
# change (e.g. adding the app.stratai.live vhost) would appear to deploy
# successfully and simply not take effect.
#
# `caddy reload` is NOT usable here: it drives the admin API, and the Caddyfile
# sets `admin off`, so it would fail on every run. A restart is the remaining
# option — but a restart on a MALFORMED config leaves nothing listening on 443,
# which takes down the WSS feeds and the app together. So validate first and only
# restart when the config actually parses. `caddy validate` is offline (no admin
# API) and runs inside the container so it sees the real QUESTDB_* values.
#
# Non-fatal by design: a gateway problem must not fail a deploy whose service
# builds all succeeded, but it is logged loudly because routing is then stale.
if $COMPOSE ps --status running --services 2>/dev/null | grep -qx questdb-gateway; then
  log "Validating Caddy config"
  if $COMPOSE exec -T questdb-gateway \
       caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    log "Caddy config valid — restarting gateway to apply it"
    $COMPOSE restart questdb-gateway
  else
    log "WARNING: Caddyfile is INVALID — gateway NOT restarted, so it keeps serving"
    log "         its previous config. Routing changes are NOT live. Diagnose with:"
    log "         $COMPOSE exec questdb-gateway caddy validate --config /etc/caddy/Caddyfile"
  fi
fi

log "DEPLOY DONE"
$COMPOSE ps
