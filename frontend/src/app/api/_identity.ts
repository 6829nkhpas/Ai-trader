// app/api/_identity.ts — SERVER-ONLY. Resolve the caller's identity and mint the
// internal assertion the deep-quant service verifies.
//
// ⚠ Never import this from a Client Component. It reads unprefixed env (the shared
//   HMAC secret) and forwards a session cookie.
//
// Why this file exists
// -------------------
// The browser currently tells the backend who it is: `user_id` travels in the
// request body (`useQuantStore` reads it from `useAuthStore` and `webAdapters`
// forwards it) and nothing verifies it. That is survivable while the agent only
// streams analysis — a forged id buys someone else's LLM quota at worst. It stops
// being survivable the moment sessions, messages and transcripts are stored per
// user, because `GET /sessions/{id}` would then be readable by anyone willing to
// change one JSON field.
//
// This route tier is the right place for the boundary. It is the only server-side
// code on the request path that can see the httpOnly `access_token` cookie
// (`domain=.stratai.live` covers `app.stratai.live`), and it already holds
// server-only secrets, so it can vouch for the caller to an internal service.
//
// Why /users/me instead of verifying the JWT here
// ----------------------------------------------
// The signing key lives in the separate `thestratai/auth` deployment and is not
// available in this repository. Verifying locally would mean provisioning that
// secret into this container — a key-distribution scheme invented to save a round
// trip. `GET /api/v1/users/me` already exists, is already this app's own
// authentication check (`useAuthStore.checkAuth`), and returns the authoritative
// user. One cached call per token per 30s is the honest price.
//
// The recorded upgrade path (BLOCKED — EXTERNAL DEPENDENCY) is a JWKS endpoint or a
// documented rotatable verification key from that deployment; see
// docs/architecture/find-quant-multi-session-design.md §7.1.
//
// Why the cookie is NOT simply forwarded upstream
// ----------------------------------------------
// `_gateway.ts` deliberately strips `cookie` and `authorization` before proxying,
// so a compromised upstream cannot read the user's session. That property is kept.
// The cookie goes to exactly one place — the auth API that issued it — and what
// travels onward is a minimal, short-lived, single-purpose assertion instead.

import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

import { API_BASE_URL, API_V1_PREFIX } from '../../lib/env';

/** Header carrying the minted user assertion. Must match `internal_identity.py`. */
export const IDENTITY_HEADER = 'X-StratAI-Identity';

/** Assertion lifetime. One hop on a private network; only has to cover clock skew. */
const TTL_SECONDS = 60;

/**
 * How long a resolved identity is cached, keyed by a hash of the token.
 *
 * 30s is chosen against the failure it prevents rather than for round-number
 * tidiness: a single FIND press fans out to `/run` plus a reattach `GET /stream`,
 * and a Q&A turn adds another call, so an uncached resolver would put three
 * cross-service round trips on one user action. 30s collapses them to one while
 * keeping a revoked session's window short.
 */
const CACHE_TTL_MS = 30_000;

/** Bound on the cache so a token-rotation storm cannot grow it without limit. */
const CACHE_MAX_ENTRIES = 500;

type CacheEntry = { userId: string | null; expiresAt: number };

/**
 * Module-scope cache. Per-process, which is correct here: the identity is derived
 * from a cookie the caller presents on every request, so a cold process simply
 * resolves again. Nothing depends on this surviving.
 */
const identityCache = new Map<string, CacheEntry>();

/**
 * Cache key. The raw token is NEVER used as a key.
 *
 * A `Map` keyed by session tokens is a credential store: it would surface in a heap
 * dump and in any future debug logging of the cache. A SHA-256 digest is just as
 * unique for lookup purposes and is not a credential.
 */
function cacheKey(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

function cacheGet(key: string): CacheEntry | undefined {
  const hit = identityCache.get(key);
  if (!hit) return undefined;
  if (hit.expiresAt <= Date.now()) {
    identityCache.delete(key);
    return undefined;
  }
  return hit;
}

function cacheSet(key: string, userId: string | null): void {
  if (identityCache.size >= CACHE_MAX_ENTRIES) {
    // Evict the oldest insertion. Map preserves insertion order, so this is O(1)
    // and needs no LRU bookkeeping for a cache this small and this short-lived.
    const oldest = identityCache.keys().next();
    if (!oldest.done) identityCache.delete(oldest.value);
  }
  identityCache.set(key, { userId, expiresAt: Date.now() + CACHE_TTL_MS });
}

/** Test-only: drop the cache so a case cannot inherit another's resolution. */
export function __resetIdentityCache(): void {
  identityCache.clear();
}

// ── Cookie parsing ───────────────────────────────────────────────────────────

/**
 * Read one cookie from a raw `Cookie` header.
 *
 * Hand-parsed rather than reached for via `next/headers`, because this runs inside
 * a plain route handler that already has the `Request` — and a `Request` is what the
 * unit tests can construct without a Next request context.
 *
 * Only the FIRST occurrence of a name is honoured. A request carrying two
 * `access_token` cookies (a stale host-scoped one plus the domain-scoped one, which
 * really happens after a cookie-scope change) must resolve deterministically rather
 * than depending on header order.
 */
export function readCookie(header: string | null, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    const value = part.slice(eq + 1).trim();
    return value.length > 0 ? value : null;
  }
  return null;
}

// ── Assertion minting ────────────────────────────────────────────────────────

function b64url(raw: Buffer): string {
  return raw.toString('base64url');
}

/**
 * The shared HMAC secret. Unprefixed, so Next never inlines it into a client chunk.
 *
 * Returns `null` rather than throwing when absent or too short: during the staged
 * rollout a deployment without the secret must keep working (unminted), and only
 * `FQ_REQUIRE_IDENTITY=1` turns a missing identity into a refusal.
 */
function identitySecret(): string | null {
  const raw = (process.env.INTERNAL_IDENTITY_SECRET ?? '').trim();
  if (raw.length < 32) return null;
  return raw;
}

/**
 * Mint `<payload>.<mac>` for `userId`, or `null` when no secret is configured.
 *
 * Format is fixed and matches `agents/deep-quant-loop/internal_identity.py`:
 * unpadded base64url of a compact JSON object with sorted keys, then unpadded
 * base64url of HMAC-SHA256 over the *payload segment text*.
 *
 * The MAC covers the encoded segment, not a re-serialisation, which is what frees
 * the two implementations from having to agree on JSON key order or escaping. The
 * `sort_keys`/sorted-literal ordering here is only so the shared test vector is
 * reproducible — it is not a wire requirement.
 */
export function mintIdentityHeader(
  userId: string,
  opts: { now?: number; ttl?: number; secret?: string | null } = {},
): string | null {
  const uid = (userId ?? '').trim();
  if (!uid) return null;
  const secret = opts.secret === undefined ? identitySecret() : opts.secret;
  if (!secret) return null;

  const iat = opts.now ?? Date.now() / 1000;
  const exp = iat + (opts.ttl ?? TTL_SECONDS);
  // Keys in sorted order (exp, iat, sub) to match Python's `sort_keys=True`.
  const json = JSON.stringify({ exp, iat, sub: uid });
  const payload = b64url(Buffer.from(json, 'utf8'));
  const mac = b64url(createHmac('sha256', secret).update(payload, 'ascii').digest());
  return `${payload}.${mac}`;
}

/**
 * Constant-time equality, for the tests that verify our own minting.
 *
 * `timingSafeEqual` throws on a length mismatch, which would leak length via the
 * exception rather than the comparison — hence the explicit length check first.
 */
export function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, 'utf8');
  const right = Buffer.from(b, 'utf8');
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

// ── Identity resolution ──────────────────────────────────────────────────────

/**
 * Resolve the caller's user id from their session cookie, or `null`.
 *
 * `null` covers every failure — no cookie, non-2xx, malformed body, unreachable
 * auth API — deliberately without distinguishing them to the caller. The route
 * handler's only decision is "do I have a verified identity", and a resolver that
 * returned reasons would invite treating some of them as good enough.
 *
 * A negative result is cached too. Otherwise an expired cookie on a page that
 * polls would hammer the auth API once per request.
 *
 * Never throws: an identity resolver that can throw turns an auth miss into a 500.
 */
export async function resolveUserId(req: Request): Promise<string | null> {
  const token = readCookie(req.headers.get('cookie'), 'access_token');
  if (!token) return null;

  const key = cacheKey(token);
  const cached = cacheGet(key);
  if (cached) return cached.userId;

  let userId: string | null = null;
  try {
    const res = await fetch(`${API_BASE_URL}${API_V1_PREFIX}/users/me`, {
      method: 'GET',
      // The cookie goes ONLY to the service that issued it. Constructed explicitly
      // rather than forwarding the whole incoming header, so no other cookie on
      // .stratai.live rides along.
      headers: { Cookie: `access_token=${token}`, Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(5_000),
    });
    if (res.ok) {
      // The API wraps payloads as { success, data }. A body that does not say
      // success is not an identity, whatever else it contains.
      const body = (await res.json().catch(() => null)) as
        | { success?: boolean; data?: { id?: unknown } }
        | null;
      const id = body?.success === true ? body?.data?.id : undefined;
      if (typeof id === 'string' && id.trim().length > 0) userId = id.trim();
    }
  } catch {
    // Unreachable / timed out / aborted. Unresolved, not an error to the caller.
    userId = null;
  }

  cacheSet(key, userId);
  return userId;
}

/**
 * Whether an unresolved identity is a refusal.
 *
 * Staged deliberately. During the rollout this is OFF, so the handler mints when it
 * can and forwards without the header when it cannot — a transient `/users/me`
 * outage must not take the whole agent surface down, which is exactly what an
 * immediate 401 would do. It is flipped to ON together with the backend's
 * `DEEP_QUANT_REQUIRE_IDENTITY` (migration plan T11.1); flipping either alone is a
 * misconfiguration, and each half warns about the other in its own logs.
 *
 * Literal `process.env.<NAME>` member expression, matching `_featureSwitches.ts` —
 * a computed lookup is not statically analysable and the two tiers should not drift
 * into different habits.
 */
export function requireIdentity(): boolean {
  const v = (process.env.FQ_REQUIRE_IDENTITY ?? '').trim().toLowerCase();
  return v === 'true' || v === '1' || v === 'yes' || v === 'on';
}

/** The 401 an unauthenticated caller gets. Says nothing about which check failed. */
export function unauthenticated(): Response {
  return Response.json({ error: 'authentication required' }, { status: 401 });
}

/**
 * Resolve, then mint. Returns the headers to add upstream, or `null` to refuse.
 *
 * `{}` means "proceed unminted" — only reachable while `requireIdentity()` is off.
 */
export async function identityHeaders(
  req: Request,
): Promise<Record<string, string> | null> {
  const userId = await resolveUserId(req);

  if (!userId) {
    if (requireIdentity()) return null;
    return {};
  }

  const header = mintIdentityHeader(userId);
  if (!header) {
    if (requireIdentity()) {
      // Identity resolved but cannot be asserted: the secret is missing or too
      // short. Refusing is right when enforcement is on, but the reason has to be
      // in the log or this reads as an authentication failure by the user.
      console.error(
        '[identity] INTERNAL_IDENTITY_SECRET is missing or shorter than 32 chars, so a ' +
          'resolved identity cannot be asserted. FQ_REQUIRE_IDENTITY is on, so the request ' +
          'is refused. Generate one with `openssl rand -hex 32` and set the SAME value on ' +
          'the frontend and deep-quant services.',
      );
      return null;
    }
    return {};
  }

  return { [IDENTITY_HEADER]: header };
}

// ── Cross-language test vector ───────────────────────────────────────────────
// Mirrors `internal_identity.reference_vector()`. Both sides assert this exact
// output, so a divergence in the wire format is a failing unit test rather than a
// 401 in production.

export const VECTOR_SECRET =
  '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
export const VECTOR_SUB = 'user_abc123';
export const VECTOR_IAT = 1_700_000_000;
export const VECTOR_TTL = 60;
