// app/api/deepquant/[...path]/route.ts — same-origin proxy to the deep-quant
// FastAPI service (`agents/deep-quant-loop`, :8086).
//
// Browser analogue of `commands/deep_quant.rs`. Serves the agent lifecycle
// (`/run`, `/resume`, `/qa`, `/cancel`, `/stream/{thread_id}`) and the F&O
// snapshot endpoint (`/options/snapshot`).
//
// STREAMING IS LOAD-BEARING. `/run`, `/qa` and `/stream/*` are SSE: the Python
// side holds the connection open and pushes glass-box frames as the graph
// executes. `infra/caddy/Caddyfile` sets `flush_interval -1` on /deepquant/* for
// this reason, and this handler must not buffer either — it passes the upstream
// `ReadableStream` through untouched with `no-transform` and no timeout.

import { assertFeatureEnabled } from '../../_featureSwitches';
import { proxyError } from '../../_gateway';
import { identityHeaders, unauthenticated } from '../../_identity';
import { proxyRequest, resolveCatchAll } from '../../_proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
/**
 * SSE runs outlive the default function budget. `maxDuration` is honoured by
 * serverless hosts and harmless on the self-hosted standalone server, where the
 * connection lives as long as the client holds it.
 */
export const maxDuration = 800;

type Ctx = { params: Promise<{ path?: string[] }> };

/** Paths whose responses are Server-Sent Events rather than a single JSON body. */
export function isStreamingPath(segments: string[]): boolean {
  const first = (segments[0] ?? '').toLowerCase();
  return first === 'run' || first === 'qa' || first === 'resume' || first === 'stream';
}

/**
 * The LLM agent lifecycle — the surface `DeepQuantPanel` paywalls behind the
 * `deepseekGlm` feature (`DeepQuantPanel.tsx`, which renders `PremiumPaywall`
 * instead of the panel when the feature is off).
 *
 * Deliberately NOT the whole service. `/options/snapshot` is served by the same
 * FastAPI app but belongs to the F&O workspace, which is not feature-gated
 * anywhere in the UI — gating it here would break the options panel for
 * everyone the moment an operator left `ENABLE_DEEPSEEK_GLM` unset. Enforcement
 * has to match the UI's gate exactly, or the server and the client disagree
 * about what the user bought.
 */
export function isAgentPath(segments: string[]): boolean {
  const first = (segments[0] ?? '').toLowerCase();
  return (
    first === 'run' ||
    first === 'qa' ||
    first === 'resume' ||
    first === 'stream' ||
    first === 'cancel' ||
    // The Find Quant Trade session surface. Part of the same paid capability, and —
    // unlike everything above it — it returns STORED USER DATA, which is why the
    // identity block below matters more here than anywhere else in this tier.
    first === 'sessions' ||
    first === 'runs'
  );
}

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const segments = (path ?? []).filter((s) => s.length > 0);
  const resolved = resolveCatchAll(segments, req);
  if (!resolved) return proxyError(400, 'deepquant: a path segment is required');

  let extraHeaders: Record<string, string> | undefined;

  if (isAgentPath(segments)) {
    const denied = assertFeatureEnabled('deepseekGlm', 'Deep Quant AI analysis');
    if (denied) return denied;

    // THE AUTHENTICATION BOUNDARY for the agent surface.
    //
    // This tier is the only place on the request path that can see the httpOnly
    // `access_token` cookie, so it is the only place that can turn "a request
    // arrived" into "this user sent it". `identityHeaders` verifies the cookie
    // against the auth API and mints a short-lived MAC'd assertion; deep-quant
    // trusts the MAC and ignores the body's `user_id` once enforcement is on.
    //
    // `null` means refuse, and only `FQ_REQUIRE_IDENTITY=1` can produce it — during
    // the staged rollout an unresolved identity forwards unminted instead, so a
    // transient auth-API outage cannot take the agent down. Crucially the refusal
    // happens HERE: an unauthenticated caller never reaches the upstream at all.
    //
    // `/options/snapshot` is deliberately outside `isAgentPath` (it is the ungated
    // F&O workspace, not user data), so it is unaffected.
    const asserted = await identityHeaders(req);
    if (asserted === null) return unauthenticated();
    extraHeaders = asserted;
  }

  return proxyRequest(req, 'deepquant', {
    path: resolved,
    stream: isStreamingPath(segments),
    extraHeaders,
  });
}

export const GET = handle;
export const POST = handle;
