// app/api/_gateway.ts — SERVER-ONLY backend resolution for the hosted website.
//
// This is the browser analogue of `src-tauri/src/server.rs`. The desktop build
// reaches the backend through Rust IPC commands that hold the gateway basic-auth
// credential in the binary; a browser cannot do that, and it cannot call the
// gateway directly either:
//
//   `infra/caddy/Caddyfile` puts /questdb/*, /deepquant/*, /kite/*, /tools/* and
//   /sentiment* behind basic auth AND emits no CORS headers on any of them (only
//   /status/* does). So a cross-origin browser fetch fails on preflight no matter
//   what credential it carries.
//
// The web path is therefore a SAME-ORIGIN Next.js route handler that holds the
// credential server-side and proxies onward. Every value below is read from
// env WITHOUT a `NEXT_PUBLIC_` prefix, which is what keeps the credential out of
// the JS bundle — the same property `commands/security.rs::kite_fetch` protects.
//
// ⚠ Never import this file from a Client Component. It is only reachable from
//   `route.ts` handlers, which always run on the server.

/// Resolve a value with priority: env → default. Mirrors `server.rs::resolve_env`
/// minus the compile-time bake (Node has no `option_env!`; the container's env
/// is the deployment-time equivalent).
function resolveEnv(value: string | undefined, fallback: string): string {
  const v = (value ?? '').trim();
  return v.length > 0 ? v : fallback;
}

/** Backend server host (no scheme, no port). Mirrors `server.rs::host`. */
export function host(): string {
  return resolveEnv(process.env.STRATAI_SERVER_HOST, '127.0.0.1');
}

/**
 * Public HTTPS gateway base (e.g. `https://app-api.stratai.live`).
 * Empty in local dev, where each service is reached on its own port directly.
 * Mirrors `server.rs::http_base`.
 */
export function httpBase(): string {
  return resolveEnv(process.env.STRATAI_HTTP_BASE_URL, '').replace(/\/+$/, '');
}

/**
 * `base` with `suffix` appended exactly once, trailing slashes normalized.
 *
 * Idempotent so an operator who configures `http://tool-server:8084/tools` and one
 * who configures `http://tool-server:8084` both end up at the same upstream —
 * doubling the segment would 404 just as surely as omitting it.
 */
function withSuffix(base: string, suffix: string): string {
  const trimmed = base.replace(/\/+$/, '');
  return trimmed.endsWith(suffix) ? trimmed : `${trimmed}${suffix}`;
}

/** Shared gateway basic-auth username. Mirrors `server.rs::questdb_user`. */
export function gatewayUser(): string {
  return resolveEnv(process.env.QUESTDB_USER, 'admin');
}

/** Shared gateway basic-auth password. Mirrors `server.rs::questdb_password`. */
export function gatewayPassword(): string {
  return resolveEnv(process.env.QUESTDB_PASSWORD, 'quest');
}

/** The local-dev default. Mirrors `server.rs::DEV_QUESTDB_PASSWORD`. */
export const DEV_GATEWAY_PASSWORD = 'quest';

/**
 * True when this deployment targets the public gateway but carries no real
 * credential — every authenticated route will 401. Mirrors
 * `server.rs::gateway_credentials_missing` so the web tier reports the same
 * condition as a credential fault instead of a silently empty panel.
 */
export function gatewayCredentialsMissing(): boolean {
  if (httpBase() === '') return false;
  const p = gatewayPassword().trim();
  return p === '' || p === DEV_GATEWAY_PASSWORD;
}

/** One of the five upstream services this tier proxies to. */
export type Upstream = 'kite' | 'questdb' | 'deepquant' | 'tools' | 'sentiment';

/**
 * Base URL for an upstream service.
 *
 * Priority per service: explicit override env → `{httpBase}/{path}` when the
 * gateway is configured → direct `http://{host}:{port}`. This is exactly the
 * ladder in `server.rs` (`kite_url`, `questdb_http_url`, `deep_quant_url`),
 * extended to the two services the website additionally needs.
 */
export function upstreamBase(target: Upstream): string {
  const base = httpBase();

  switch (target) {
    case 'kite': {
      const override = resolveEnv(process.env.KITE_API_URL, '');
      if (override) return override.replace(/\/+$/, '');
      if (base) return `${base}/kite`;
      return `http://${host()}:8087/api/kite`;
    }
    case 'questdb': {
      const override = resolveEnv(process.env.QUESTDB_HTTP_URL, '');
      if (override) return override.replace(/\/+$/, '');
      if (base) return `${base}/questdb`;
      return `http://${host()}:9000`;
    }
    case 'deepquant': {
      const override = resolveEnv(process.env.DEEP_QUANT_URL, '');
      if (override) return override.replace(/\/+$/, '');
      if (base) return `${base}/deepquant`;
      return `http://${host()}:8086`;
    }
    case 'tools': {
      // tool-server mounts every route under `/tools` (see tool-server/src/main.rs
      // `build_router`), and the `[...path]` segment arrives with that prefix
      // already stripped — so it has to be restored here. Appending it to the
      // OVERRIDE too is the whole point: without that, a deployment setting
      // QUANT_TOOL_SERVER_URL=http://tool-server:8084 forwarded
      // `/api/tools/get_candles` to `http://tool-server:8084/get_candles` and got
      // a 404 from the upstream, which the proxy passed through verbatim — a
      // route that looked missing while both sides were healthy. Measured on the
      // droplet: without the prefix 404, with it 405 (route present).
      //
      // A trailing `/tools` supplied by the operator is tolerated rather than
      // doubled, since documenting the base either way is a coin flip.
      const override = resolveEnv(process.env.QUANT_TOOL_SERVER_URL, '');
      if (override) return withSuffix(override, '/tools');
      if (base) return `${base}/tools`;
      return `http://${host()}:8084/tools`;
    }
    case 'sentiment': {
      const override = resolveEnv(process.env.SENTIMENT_HTTP_URL, '');
      if (override) return override.replace(/\/+$/, '');
      if (base) return `${base}/sentiment`;
      return `http://${host()}:8090`;
    }
  }
}

/**
 * The `Authorization: Basic …` header for the gateway.
 *
 * Attached unconditionally, matching `services/questdb_http.rs:195` and the
 * deep-quant call sites: a stock local QuestDB and the local aggregator both
 * ignore an unexpected Authorization header, so there is no direct-mode branch
 * to get wrong.
 */
export function gatewayAuthHeader(): string {
  const raw = `${gatewayUser()}:${gatewayPassword()}`;
  return `Basic ${Buffer.from(raw, 'utf8').toString('base64')}`;
}

/**
 * Hop-by-hop and identity headers that must NOT be forwarded upstream.
 * `host` in particular would make the gateway route to the wrong vhost.
 */
const STRIPPED_REQUEST_HEADERS = new Set([
  'host',
  'connection',
  'keep-alive',
  'transfer-encoding',
  'upgrade',
  'proxy-authorization',
  'proxy-authenticate',
  'te',
  'trailer',
  // The browser's own cookies/credentials have no meaning to the gateway, and
  // forwarding them would widen what a compromised upstream could read.
  'cookie',
  'authorization',
  'content-length',
  // Identity assertions are MINTED BY THIS TIER, never relayed from the browser.
  // `_identity.ts` signs one only after verifying the session cookie against the
  // auth API, and deep-quant trusts the MAC — so a client-supplied copy reaching
  // upstream would be an attempt to assert an identity nobody verified. Dropped
  // here on the way in; `ProxyOptions.extraHeaders` is applied after
  // `forwardHeaders` so ours also wins on the way out. Either check alone would
  // close this, and both are kept so a later edit to one cannot reopen it.
  'x-stratai-identity',
  'x-stratai-service',
]);

/** Build the upstream request headers from an incoming browser request. */
export function forwardHeaders(req: Request, extra?: Record<string, string>): Headers {
  const out = new Headers();
  req.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) out.set(key, value);
  });
  out.set('Authorization', gatewayAuthHeader());
  // Caddy's /kite route rewrites by URI; an explicit Accept keeps the aggregator
  // and QuestDB on their JSON paths.
  if (!out.has('Accept')) out.set('Accept', 'application/json');
  for (const [k, v] of Object.entries(extra ?? {})) out.set(k, v);
  return out;
}

/**
 * Response headers that must not be copied back to the browser: hop-by-hop
 * fields, and `content-encoding`/`content-length`, which describe the upstream
 * body framing that `fetch` has already undone.
 */
const STRIPPED_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'upgrade',
  'content-encoding',
  'content-length',
  'www-authenticate',
]);

/** Copy an upstream response's headers, minus the ones that break re-framing. */
export function passthroughHeaders(upstream: Response): Headers {
  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIPPED_RESPONSE_HEADERS.has(key.toLowerCase())) out.set(key, value);
  });
  return out;
}

/** Request timeout for ordinary (non-streaming) proxied calls. */
export const PROXY_TIMEOUT_MS = Number(process.env.STRATAI_PROXY_TIMEOUT_MS ?? 30_000);

/**
 * A proxy error body. Deliberately shaped like the errors the Rust commands
 * return (a plain `error` string) so the frontend's existing honest-empty and
 * error-state rendering works unchanged, and deliberately free of any detail
 * that could echo the credential back to the client.
 */
export function proxyError(status: number, message: string): Response {
  return Response.json({ error: message }, { status });
}

/**
 * Map a `fetch` rejection to an honest status + message.
 *
 * A 401/403 from the gateway is reported as a *credential fault* with an
 * actionable message rather than as empty data — the same distinction
 * `commands/security.rs::kite_fetch` makes, and the reason a misconfigured
 * deployment surfaces as a config error instead of a blank chart.
 */
export function describeUpstreamFailure(err: unknown, target: Upstream): string {
  const detail = err instanceof Error ? err.message : String(err);
  if (detail.includes('aborted') || detail.includes('timeout')) {
    return `${target} upstream timed out after ${PROXY_TIMEOUT_MS}ms`;
  }
  return `${target} upstream unreachable: ${detail}`;
}

/** True when the upstream rejected our credential rather than the request. */
export function isCredentialFault(status: number): boolean {
  return status === 401 || status === 403;
}

/** The actionable message for a credential fault, mirroring `kite_fetch`. */
export function credentialFaultMessage(target: Upstream): string {
  return gatewayCredentialsMissing()
    ? `${target} gateway rejected the request (HTTP 401/403): this deployment has no QUESTDB_PASSWORD set, so it is using the local-dev default. Set QUESTDB_USER/QUESTDB_PASSWORD on the frontend service.`
    : `${target} gateway rejected the request (HTTP 401/403): the configured QUESTDB_USER/QUESTDB_PASSWORD is not accepted by ${httpBase() || host()}.`;
}
