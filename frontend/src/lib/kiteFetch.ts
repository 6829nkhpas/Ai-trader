// lib/kiteFetch.ts — the Kite REST prefix helper.
//
// Was `tauriFetch.ts`, which existed because the packaged desktop webview served
// from `tauri.localhost` — an origin absent from the api-web.stratai.live CORS
// allowlist — so backend calls had to be proxied through Rust's reqwest to escape
// CORS entirely. With the desktop shell retired the app is served from a real
// origin that IS on the allowlist, so `tauriFetch(url, init)` collapsed to exactly
// `fetch(url, init)` and its call sites now use `fetch` directly.
//
// `kiteFetch` survives because it is not a transport shim: it is the one place
// that knows Kite REST lives under the `/kite` prefix.

/**
 * Fetch a Kite REST endpoint (historical candles, quotes, instrument search).
 *
 * `next.config.ts` rewrites `/kite/*` to the same-origin `/api/kite/*` route
 * handler, which attaches the gateway credentials server-side — so the credential
 * never enters the JS bundle. That indirection is the whole reason this is a
 * relative URL and not the gateway host.
 *
 * @param path the part after `/kite` — e.g. `/quote?i=NSE:TCS`.
 */
export async function kiteFetch(path: string): Promise<Response> {
  const rel = path.startsWith('/') ? path : `/${path}`;
  return fetch(`/kite${rel}`);
}
