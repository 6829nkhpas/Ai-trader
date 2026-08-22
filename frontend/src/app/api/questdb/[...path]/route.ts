// app/api/questdb/[...path]/route.ts — same-origin proxy to QuestDB's REST API.
//
// Browser analogue of `services/questdb_http.rs`. Callers use the same paths the
// Rust side does — chiefly `/exec?query=…` — and this handler attaches the
// gateway basic-auth credential server-side.
//
// Upstream is `{gateway}/questdb/*` or, in local dev, `http://{host}:9000/*`.

import { proxyError } from '../../_gateway';
import { proxyRequest, resolveCatchAll } from '../../_proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type Ctx = { params: Promise<{ path?: string[] }> };

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const resolved = resolveCatchAll(path, req);
  if (!resolved) return proxyError(400, 'questdb: a path segment is required');
  return proxyRequest(req, 'questdb', { path: resolved });
}

export const GET = handle;
export const POST = handle;
