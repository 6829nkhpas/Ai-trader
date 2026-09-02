// tests/support/stub-identity.mjs
//
// Stands in for the auth API during the Playwright run: one endpoint, `GET /api/v1/users/me`.
//
// The identity chain is NOT bypassed in the e2e job, and that is the point. `/sessions` calls
// `resolve_user` with no body fallback, so it 401s without a verified HMAC assertion even when
// `DEEP_QUANT_REQUIRE_IDENTITY` is off. Stubbing identity out would therefore skip the one thing that
// decides whether session data is per-user at all. So the real chain runs end to end —
// cookie -> /users/me -> HMAC assertion -> deep-quant verification — with only the *issuer* of the
// cookie faked.
//
// Response shape matches what `app/api/_identity.ts` requires: `{ success: true, data: { id } }`.
// A body that does not say `success` is not an identity, whatever else it contains.

import { createServer } from 'node:http';

const PORT = Number(process.env.E2E_IDENTITY_PORT ?? 8098);

/**
 * Which user a token maps to.
 *
 * Two distinct users, because a single-user e2e cannot prove ownership isolation: the interesting
 * assertion is that Bob's request for Alice's session is a 404, and that needs two identities.
 */
const USERS = {
  'e2e-alice-token': 'e2e-user-alice',
  'e2e-bob-token': 'e2e-user-bob',
};

/**
 * Any `e2e-*` token resolves to a user of its own name.
 *
 * This is how tests get ISOLATION. Sessions are per-user and the agent's database lives for the whole
 * Playwright run, so tests sharing one identity also share a session list — the tab bar then counts
 * leftovers from earlier tests and absolute assertions fail with "locator resolved to 7 elements". Wiping
 * the database per test would mean restarting the service; giving each test its own user costs nothing and
 * removes the coupling entirely.
 *
 * Deliberately restricted to the `e2e-` prefix so an unrecognised or empty token still 401s, which is what
 * the unauthenticated path is asserted against.
 */
function userFor(token) {
  if (!token) return null;
  if (USERS[token]) return USERS[token];
  return token.startsWith('e2e-') ? token.replace(/-token$/, '') : null;
}

function readCookie(header, name) {
  for (const part of (header ?? '').split(';')) {
    const [k, ...rest] = part.trim().split('=');
    if (k === name) return rest.join('=');
  }
  return null;
}

/**
 * CORS, and it is load-bearing rather than boilerplate.
 *
 * Two different callers hit `/users/me`: the Next server (same process family, no CORS involved) and
 * **the browser itself**, because `useAuthStore.checkAuth` runs client-side against
 * `NEXT_PUBLIC_API_BASE_URL`. That second call is cross-origin — app on :3000, stub on :8098 — and it
 * sends credentials. Without these headers the browser blocks it, the client-side auth check fails, and
 * the app redirects to the real `auth.stratai.live`, which then fails every selector in the suite with
 * a page snapshot of the marketing site. That is exactly how this presented before the headers existed.
 *
 * The origin is ECHOED, not `*`: a wildcard is rejected outright for credentialed requests.
 */
function cors(req, res) {
  const origin = req.headers.origin;
  if (origin) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
  }
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Headers', 'content-type, accept, authorization');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
  cors(req, res);

  if (req.method === 'OPTIONS') {
    // Preflight. A credentialed cross-origin GET with custom headers will not proceed without it.
    res.writeHead(204);
    res.end();
    return;
  }

  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"ok":true}');
    return;
  }

  if (url.pathname === '/api/v1/users/me' && req.method === 'GET') {
    const token = readCookie(req.headers.cookie, 'access_token');
    const id = userFor(token);
    if (!id) {
      // 401 rather than a generic 500: the frontend treats an unresolved identity as "no header",
      // and conflating the two would hide a misconfigured cookie in the job.
      res.writeHead(401, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ success: false, error: 'unauthenticated' }));
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ success: true, data: { id, email: `${id}@example.test` } }));
    return;
  }

  res.writeHead(404, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ success: false, error: 'not found' }));
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[e2e] identity stub on :${PORT} (users: ${Object.values(USERS).join(', ')})`);
});
