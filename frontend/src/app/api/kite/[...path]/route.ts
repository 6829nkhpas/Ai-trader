// app/api/kite/[...path]/route.ts — same-origin proxy to the Kite REST proxy.
//
// Browser analogue of `commands/security.rs::kite_fetch`. `lib/tauriFetch.ts`
// already fetches `/kite/*` when it is not running under Tauri; `next.config.ts`
// rewrites that to here, and this handler attaches the gateway credential
// server-side so it never enters the JS bundle.
//
// Upstream is `{gateway}/kite/*` (Caddy rewrites to `/api/kite{uri}`) or, in
// local dev, `http://{host}:8087/api/kite/*` directly.

import { proxyError } from '../../_gateway';
import { proxyRequest, resolveCatchAll } from '../../_proxy';

/** Node runtime: the gateway credential must never reach an edge bundle. */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type Ctx = { params: Promise<{ path?: string[] }> };

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const resolved = resolveCatchAll(path, req);
  if (!resolved) return proxyError(400, 'kite: a path segment is required');
  return proxyRequest(req, 'kite', { path: resolved });
}

export const GET = handle;
export const POST = handle;
