# frontend/Dockerfile — the hosted website (app.stratai.live)
#
# Build context is ./frontend (NOT the monorepo root, unlike agents/sentiment
# which needs shared_protos/). See frontend/.dockerignore — the root
# .dockerignore does not apply to this context.
#
#   docker-compose.prod.yml sets:
#     build:
#       context: ./frontend
#       dockerfile: Dockerfile
#
# Two things here are load-bearing rather than preference:
#
#   1. `next build --turbopack`. The default webpack production build dies with
#      `FATAL ERROR: Committing semi space failed` partway through compiling this
#      tree. The exhaustion is in EXTERNAL memory, not the JS heap, so raising
#      --max-old-space-size does not help (tried at 8 GB). See next.config.ts.
#
#   2. The NEXT_PUBLIC_* values are build ARGs, not runtime env. Next inlines them
#      into the JS bundle textually at build time, so setting them in `environment:`
#      has no effect whatsoever — the container would serve a bundle compiled
#      against whatever was present when the image was built. The feature kill
#      switches deliberately do NOT appear here: they are unprefixed and read at
#      request time (see app/api/_featureSwitches.ts), which is exactly what keeps
#      them out of the bundle and makes them restart-configurable.
#
# Node 22, not the repo's usual node:20-alpine (agents/sentiment/Dockerfile).
# Node 20 reached end-of-life in April 2026; this is the only container in the
# stack terminating public HTTP, so it should not run an unsupported runtime.

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — dependencies
# ──────────────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS deps

# The SWC/Turbopack native binaries are glibc-linked and need the compat shim on
# Alpine. Without it the build fails at "Failed to load SWC binary".
RUN apk add --no-cache libc6-compat

WORKDIR /app

# Full install (NOT --omit=dev): the build needs typescript, tailwindcss and
# @tailwindcss/postcss, all of which are devDependencies.
COPY package.json package-lock.json ./
RUN npm ci

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — build
# ──────────────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# ── Client-inlined configuration ─────────────────────────────────────────────
# `lib/env.ts` THROWS at import time when API_BASE_URL or DASHBOARD_URL is unset,
# which fails the build rather than producing a broken bundle. The defaults below
# are the production values so a bare `docker build` still yields a working image.
ARG NEXT_PUBLIC_API_BASE_URL=https://api-web.stratai.live
ARG NEXT_PUBLIC_DASHBOARD_URL=https://dashboard.stratai.live
ARG NEXT_PUBLIC_AUTH_SERVICE_URL=https://api-web.stratai.live

# Live feeds. `/ws/*` is the one gateway prefix with no basic auth
# (infra/caddy/Caddyfile), so the browser connects to these directly. They MUST
# be wss:// — an https:// page cannot open a ws:// socket, the browser blocks it
# as mixed content (see wsUrlIsUsable in store/useTradeStore.ts).
ARG NEXT_PUBLIC_AGGREGATOR_WS_URL=wss://app-api.stratai.live/ws/aggregator
ARG NEXT_PUBLIC_ALPHA_WS_URL=wss://app-api.stratai.live/ws/alpha
ARG NEXT_PUBLIC_PREDICTIVE_WS_URL=wss://app-api.stratai.live/ws/predictive
ARG NEXT_PUBLIC_INSIGHT_WS_URL=wss://app-api.stratai.live/ws/insight
ARG NEXT_PUBLIC_WS_URL=wss://app-api.stratai.live/ws/aggregator
# No service in this stack publishes the order-flow feed yet; left unset so the
# mixed-content guard refuses it quietly instead of a socket looping on ECONNREFUSED.
ARG NEXT_PUBLIC_ORDER_FLOW_WS_URL=

# `NEXT_PUBLIC_PROD` gates the regulated RESEARCH surface (lib/sku.ts) and IS_PROD
# UI behaviour. Distinct from FEATURE_ENFORCEMENT, which is runtime and server-side.
ARG NEXT_PUBLIC_PROD=true
ARG NEXT_PUBLIC_SKU_ENFORCE=
# Closed-beta escape hatch for the RESEARCH SKU gate (lib/sku.ts). Empty by
# default: this publishes directional recommendations, which SEBI treats as a
# regulated activity, so it must be switched on deliberately and removed before
# public launch. See the comment on skuEnforcementEnabled().
ARG NEXT_PUBLIC_RESEARCH_BETA_OPEN=
ARG NEXT_PUBLIC_LLM_GATEWAY=omniroute

ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_DASHBOARD_URL=$NEXT_PUBLIC_DASHBOARD_URL \
    NEXT_PUBLIC_AUTH_SERVICE_URL=$NEXT_PUBLIC_AUTH_SERVICE_URL \
    NEXT_PUBLIC_AGGREGATOR_WS_URL=$NEXT_PUBLIC_AGGREGATOR_WS_URL \
    NEXT_PUBLIC_ALPHA_WS_URL=$NEXT_PUBLIC_ALPHA_WS_URL \
    NEXT_PUBLIC_PREDICTIVE_WS_URL=$NEXT_PUBLIC_PREDICTIVE_WS_URL \
    NEXT_PUBLIC_INSIGHT_WS_URL=$NEXT_PUBLIC_INSIGHT_WS_URL \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_PUBLIC_ORDER_FLOW_WS_URL=$NEXT_PUBLIC_ORDER_FLOW_WS_URL \
    NEXT_PUBLIC_PROD=$NEXT_PUBLIC_PROD \
    NEXT_PUBLIC_SKU_ENFORCE=$NEXT_PUBLIC_SKU_ENFORCE \
    NEXT_PUBLIC_RESEARCH_BETA_OPEN=$NEXT_PUBLIC_RESEARCH_BETA_OPEN \
    NEXT_PUBLIC_LLM_GATEWAY=$NEXT_PUBLIC_LLM_GATEWAY \
    NEXT_TELEMETRY_DISABLED=1

# NEXT_OUTPUT_EXPORT must stay unset here: it would switch next.config.ts to
# `output: 'export'`, which drops every `route.web.ts` proxy handler (they are
# admitted via `pageExtensions` only outside export mode) and emits no server.
RUN npm run build:web

# ──────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime
# ──────────────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `output: 'standalone'` traces the modules the server actually needs and emits
# them under .next/standalone, so node_modules is not copied wholesale.
# `public/` and `.next/static` are NOT traced and must be copied separately —
# public/ carries the TradingView Advanced Charts library (~31 MB).
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

USER node

EXPOSE 3000

# `/api/features` is the cheapest honest liveness signal: it is force-dynamic, so
# it proves the Node server is executing route handlers, and it touches no
# upstream — a QuestDB or aggregator outage must not mark the web tier unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/api/features || exit 1

CMD ["node", "server.js"]
