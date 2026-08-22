// app/api/tools/[...path]/route.ts — same-origin proxy to the standalone quant
// tool server (`tool-server/`, :8084).
//
// Browser analogue of the Tauri-embedded `quant/tool_server.rs`. The deployed
// crate is Tauri-free and shares `quant-core` with the desktop build, so the
// analytics the website gets here are computed by the SAME code as on desktop —
// chart patterns, support/resistance, prediction, consensus, candles — rather
// than a reimplementation that could drift.

import { proxyError } from '../../_gateway';
import { proxyRequest, resolveCatchAll } from '../../_proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type Ctx = { params: Promise<{ path?: string[] }> };

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const resolved = resolveCatchAll(path, req);
  if (!resolved) return proxyError(400, 'tools: a path segment is required');
  return proxyRequest(req, 'tools', { path: resolved });
}

export const GET = handle;
export const POST = handle;
