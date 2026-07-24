#!/usr/bin/env bash
# One-shot: generate a strong QuestDB beta credential and append it to .env.
# Idempotent — does nothing if QUESTDB_PASSWORD is already set.
#
# Writes three keys consumed by docker-compose.prod.yml:
#   QUESTDB_USER          — PG-wire + basic-auth username
#   QUESTDB_PASSWORD      — plaintext (PG-wire password; also baked into the app)
#   QUESTDB_PASSWORD_HASH — bcrypt hash for the Caddy gateway, with every '$'
#                           doubled to '$$' so Compose interpolation preserves it.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

if grep -q '^QUESTDB_PASSWORD=' .env 2>/dev/null; then
  echo "ALREADY_SET — leaving existing QuestDB credentials untouched."
  echo "CURRENT_USER=$(grep '^QUESTDB_USER=' .env | cut -d= -f2-)"
  exit 0
fi

PW="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 28)"
HASH="$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$PW")"
HASH_ESC="${HASH//\$/\$\$}"

{
  printf '\n# ── QuestDB shared beta auth (auto-generated) ──\n'
  printf 'QUESTDB_USER=admin\n'
  printf 'QUESTDB_PASSWORD=%s\n' "$PW"
  printf 'QUESTDB_PASSWORD_HASH=%s\n' "$HASH_ESC"
} >> .env

echo "GENERATED_OK"
echo "QUESTDB_USER=admin"
echo "QUESTDB_PASSWORD=$PW"
echo "QUESTDB_PASSWORD_HASH_RAW=$HASH"
