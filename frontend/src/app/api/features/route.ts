// app/api/features/route.ts — the deployment's feature configuration.
//
// The browser asks the server what is enabled instead of reading a value baked
// into its own bundle. See `app/api/_featureSwitches.ts` for why, and for the
// limits of what this achieves.
//
// The response is per-deployment, not per-user: it carries only the operator's
// kill switches. The per-user plan entitlement continues to arrive separately as
// `accessFlags` from the `/credit` API, and the client ANDs the two in
// `computeFeatureAccess`. Keeping them separate is deliberate — this route needs
// no authentication and therefore cannot leak anything about a user.

import { resolveFeatureConfig } from '../_featureSwitches';

export const runtime = 'nodejs';
// Must never be prerendered or cached: the whole point of moving these values
// server-side is that a container restart changes them without a rebuild. A
// static render would freeze them at build time and reintroduce exactly the
// problem this route exists to solve.
export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  return Response.json(resolveFeatureConfig(), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
