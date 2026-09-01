// app/api/_proxy.ts — SERVER-ONLY shared proxy body for every /api/* route.
//
// One function does the work for all five upstreams. It never buffers the
// response: `upstream.body` is handed straight back to the browser, which is
// what makes the deep-quant SSE routes work (Caddy sets `flush_interval -1` on
// /deepquant/* for exactly the same reason — buffering an SSE stream would make
// every glass-box panel appear to hang).
//
// ⚠ Server-only. Reachable only from `route.ts` handlers.

import {
  PROXY_TIMEOUT_MS,
  credentialFaultMessage,
  describeUpstreamFailure,
  forwardHeaders,
  isCredentialFault,
  passthroughHeaders,
  proxyError,
  upstreamBase,
  type Upstream,
} from './_gateway';

/** Methods that carry a request body. */
const BODY_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export interface ProxyOptions {
  /**
   * Path appended to the upstream base, e.g. `/instruments`. Must start with
   * `/`. The caller is responsible for including the query string.
   */
  path: string;
  /**
   * Streaming responses (SSE) skip the timeout: an agent run legitimately holds
   * the connection open for minutes, and aborting it at 30s would truncate the
   * transcript mid-reasoning.
   */
  stream?: boolean;
  /**
   * Headers this tier ASSERTS about the request, applied after `forwardHeaders`.
   *
   * The ordering is the security property, not a detail. `forwardHeaders` copies
   * the browser's headers; applying these afterwards means a client that sends
   * its own `X-StratAI-Identity` cannot win, because ours overwrites it. Belt and
   * braces with `_gateway.ts`'s `STRIPPED_REQUEST_HEADERS`, which also drops the
   * client's copy on the way in — either alone would be sufficient, and having
   * both means a future edit to one cannot silently open the hole.
   */
  extraHeaders?: Record<string, string>;
}

/**
 * Proxy `req` to `target` at `path`, returning the upstream response.
 *
 * Failure handling mirrors the Rust commands this replaces:
 *   * a transport failure becomes a 502 with a plain `{ error }` body, which the
 *     frontend already renders as an honest error state;
 *   * a 401/403 becomes a *credential fault* with an actionable message rather
 *     than empty data, so a misconfigured deployment reads as a config error and
 *     not as a blank panel.
 */
export async function proxyRequest(
  req: Request,
  target: Upstream,
  { path, stream = false, extraHeaders }: ProxyOptions,
): Promise<Response> {
  if (!path.startsWith('/')) {
    return proxyError(500, `internal: proxy path must start with "/" (got ${path})`);
  }

  const url = `${upstreamBase(target)}${path}`;
  const method = req.method.toUpperCase();

  const controller = new AbortController();
  const timer = stream
    ? undefined
    : setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method,
      headers: forwardHeaders(req, {
        ...(stream ? { Accept: 'text/event-stream' } : {}),
        ...(extraHeaders ?? {}),
      }),
      // `duplex: 'half'` is required by the Fetch spec when sending a stream
      // body; Node's undici enforces it. Reading the body to an ArrayBuffer
      // first would be simpler but would break large historical POSTs.
      ...(BODY_METHODS.has(method) && req.body
        ? { body: req.body, duplex: 'half' as const }
        : {}),
      signal: controller.signal,
      // These proxies are the live data path — never serve a cached answer.
      cache: 'no-store',
      redirect: 'manual',
    } as RequestInit);
  } catch (err) {
    return proxyError(502, describeUpstreamFailure(err, target));
  } finally {
    if (timer) clearTimeout(timer);
  }

  if (isCredentialFault(upstream.status)) {
    return proxyError(upstream.status, credentialFaultMessage(target));
  }

  const headers = passthroughHeaders(upstream);
  if (stream) {
    // Match Caddy's `flush_interval -1`: no buffering anywhere on the path.
    headers.set('Content-Type', upstream.headers.get('content-type') ?? 'text/event-stream');
    headers.set('Cache-Control', 'no-cache, no-transform');
    headers.set('X-Accel-Buffering', 'no');
    headers.delete('content-length');
  } else if (!headers.has('Cache-Control')) {
    headers.set('Cache-Control', 'no-store');
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}

/**
 * Re-serialize a query string with RFC 3986 percent-encoding, so a space is
 * `%20` and never `+`.
 *
 * This is not cosmetic. Next.js normalizes the request URL it hands to a route
 * handler through a form-encoding serializer, so a browser that sent
 *
 *   ?i=NSE%3ANIFTY%2050
 *
 * arrives at the handler as `req.url === '...?i=NSE%3ANIFTY+50'` — measured, not
 * assumed. Forwarding that verbatim broke every symbol containing a space:
 * `aggregator/src/kite_api.rs::quote_handler` decodes each `i=` value with
 * `urlencoding::decode`, which is RFC 3986 percent-decoding and correctly leaves
 * `+` as a literal plus. Kite was therefore asked for `NSE:NIFTY+50`, did not
 * recognise it, and returned no data — so the five macro indices and the
 * `NIFTY 50` / `NIFTY BANK` watchlist rows rendered "—" with an HTTP 200 and no
 * error anywhere to explain it.
 *
 * `URLSearchParams` decodes `+` and `%20` alike to a space, so the *values* are
 * intact by the time we see them; only the wire form needs correcting.
 * `encodeURIComponent` is the right encoder because it escapes `+` itself as
 * `%2B`, so a value that genuinely contains a plus still round-trips.
 *
 * One loss is inherent and upstream of us: a client that sent a bare `+` meaning
 * a literal plus is indistinguishable from one that sent an encoded space by the
 * time Next has normalized the URL. Callers must percent-encode, which every
 * caller in this tree does via `encodeURIComponent`.
 */
export function canonicalizeSearch(search: string): string {
  const raw = search.startsWith('?') ? search.slice(1) : search;
  if (!raw) return '';
  const parts: string[] = [];
  for (const [key, value] of new URLSearchParams(raw)) {
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

/**
 * The `[...path]` segment as a single `/`-joined path, with the incoming query
 * string appended. Returns `null` when the segment is empty, which callers turn
 * into a 400 rather than silently proxying the upstream root.
 */
export function resolveCatchAll(
  segments: string[] | undefined,
  req: Request,
): string | null {
  const joined = (segments ?? []).filter((s) => s.length > 0).join('/');
  if (!joined) return null;
  return `/${joined}${canonicalizeSearch(new URL(req.url).search)}`;
}
