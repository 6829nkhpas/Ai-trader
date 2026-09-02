# strat-app-frontend — Vercel deployment mirror

This repository is a **mirror** of the `frontend/` directory of the private
monorepo `thestratai/Ai-trader`. It exists so Vercel can auto-deploy the Next.js
terminal on its own, while the backend services stay on the GCP VM behind
`app-api.stratai.live`.

**The monorepo is the source of truth.** Do not develop here — changes made in
this repo will be overwritten the next time the mirror is refreshed. Full history
is preserved (`git subtree split --prefix=frontend`), so `git log` is real.

## Two deliberate differences from the monorepo

**1. The TradingView library is vendored, not a submodule.**
In the monorepo, `public/static/charting_library` is a git submodule pointing at
`github.com/tradingview/charting_library.git` — a **private third-party** repo.
Vercel's build clone cannot authenticate to it, so the directory would arrive
empty and every chart would 404 on
`/static/charting_library/charting_library/charting_library.standalone.js`.

The 31 MB of library files are therefore committed directly here. This repo
**must stay private** — those files are licensed by TradingView and this is a
redistribution of them.

**2. `vercel.json` sets `buildCommand: npm run build:web`.**
This is not a speed preference. Vercel's default `next build` (webpack) dies
partway through with:

```
FATAL ERROR: Committing semi space failed. Allocation failed -
JavaScript heap out of memory
```

The exhaustion is in *external* memory, not the JS heap, so
`--max-old-space-size` does not help (tried at 8 GB). `build:web` passes
`--turbopack`, which compiles the same tree without the spike. See the comment
block in `next.config.ts`. If you ever change this, validate a **cold** build —
a warm `.next` cache masks the failure.

`vercel.json` also restores the security headers that Caddy used to add in front
of the container (`frame-ancestors 'none'` is the load-bearing one: a trading
terminal framed by a third party is a clickjacking target where the clicks place
orders).

## Required environment variables

`NEXT_PUBLIC_*` are **build-time** — Next inlines them into the bundle, so
changing one needs a redeploy, not a restart. The rest are read per request.

### Build time (all three of the first group throw at import if missing)

| Var | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://api-web.stratai.live` |
| `NEXT_PUBLIC_DASHBOARD_URL` | `https://dashboard.stratai.live` |
| `NEXT_PUBLIC_AUTH_URL` | `https://auth.stratai.live` |
| `NEXT_PUBLIC_AUTH_SERVICE_URL` | `https://api-web.stratai.live` |
| `NEXT_PUBLIC_AGGREGATOR_WS_URL` | `wss://app-api.stratai.live/ws/aggregator` |
| `NEXT_PUBLIC_ALPHA_WS_URL` | `wss://app-api.stratai.live/ws/alpha` |
| `NEXT_PUBLIC_PREDICTIVE_WS_URL` | `wss://app-api.stratai.live/ws/predictive` |
| `NEXT_PUBLIC_INSIGHT_WS_URL` | `wss://app-api.stratai.live/ws/insight` |
| `NEXT_PUBLIC_WS_URL` | `wss://app-api.stratai.live/ws/aggregator` |
| `NEXT_PUBLIC_PROD` | `true` |
| `NEXT_PUBLIC_LLM_GATEWAY` | `omniroute` |
| `NEXT_PUBLIC_RESEARCH_BETA_OPEN` | `true` only for the closed beta — see below |

All WS URLs must be `wss://`; an https page cannot open `ws://`.

### Runtime (server-side, never inlined)

| Var | Value |
|---|---|
| `STRATAI_HTTP_BASE_URL` | `https://app-api.stratai.live` |
| `QUESTDB_USER` / `QUESTDB_PASSWORD` | the gateway basic-auth credential |
| `INTERNAL_IDENTITY_SECRET` | ≥32 chars, **identical** to deep-quant's |
| `FQ_REQUIRE_IDENTITY` | `0` during rollout |
| `FEATURE_ENFORCEMENT` | `true` normally; `false` opens all premium features |
| `ENABLE_DEEPSEEK_GLM` … `ENABLE_ADVANCE_CHART` | `true` |

**Leave `KITE_API_URL`, `QUESTDB_HTTP_URL`, `DEEP_QUANT_URL`,
`QUANT_TOOL_SERVER_URL` and `SENTIMENT_HTTP_URL` UNSET.** `src/app/api/_gateway.ts`
gives those explicit overrides priority over `STRATAI_HTTP_BASE_URL`, and their
monorepo values are Docker-network hostnames (`http://aggregator:8087/...`) that
do not resolve from Vercel.

## Known gaps before this can serve app.stratai.live

These are **not** fixed by deploying this repo:

1. **`/api/deepquant/stream/*` is an unbounded SSE stream.** The Python side
   holds it open for the whole trade-watching lifecycle with a 20 s keepalive.
   Vercel caps function duration, so the reattach stream will be severed.
   `maxDuration = 800` is already at the Fluid/Pro ceiling.
2. **`/tools` and `/sentiment` have no route on `app-api.stratai.live`.** The
   Caddyfile deliberately omitted them because the frontend used to reach those
   services over the Docker network. Both 404 until gateway routes are added.
3. **`/api/sentiment` waits up to 35 s** and exports no `maxDuration`, so it
   exceeds Vercel's default cap.
4. `identityCache` in `src/app/api/_identity.ts` is per-process, so it is far
   less effective on serverless — expect more `/users/me` calls to api-web.

## Refreshing the mirror

From the monorepo root:

```bash
git subtree split --prefix=frontend -b vercel-export
git clone -b vercel-export --single-branch . /tmp/fe-export
cd /tmp/fe-export
git rm --cached public/static/charting_library
rm -rf public/static/charting_library
tar --exclude=.git -cf - -C ../Ai-trader/frontend/public/static charting_library \
  | tar -xf - -C public/static
# re-add vercel.json and this file, then push
```
