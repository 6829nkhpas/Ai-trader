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
 * By default this is a RELATIVE URL (`/kite/...`), so it hits this app's own
 * same-origin proxy. Set `NEXT_PUBLIC_KITE_PROXY_ORIGIN` to send the request to
 * a DIFFERENT deployment's proxy instead — e.g. in local dev, point it at the
 * live site (`https://app.stratai.live`) so `npm run dev` reuses that frontend's
 * server-side gateway credentials rather than needing the backend locally.
 *
 * ⚠ The target origin must return CORS headers that allow this origin, since a
 * cross-origin browser fetch is otherwise blocked. Same-origin (empty var) needs
 * no CORS. The `/kite` prefix and its `next.config.ts` rewrite are unchanged.
 *
 * @param path the part after `/kite` — e.g. `/quote?i=NSE:TCS`.
 * @param init standard `fetch` options. Exists so callers can pass an
 *   `AbortSignal`: several of these are poll loops, and without a way to bound a
 *   request a hung fetch stalls the loop forever (the order book's "Awaiting
 *   Market Depth Data…" hang). Forwarded verbatim.
 */
const KITE_PROXY_ORIGIN = (process.env.NEXT_PUBLIC_KITE_PROXY_ORIGIN ?? '').replace(/\/+$/, '');

export async function kiteFetch(path: string, init?: RequestInit): Promise<Response> {
  const rel = path.startsWith('/') ? path : `/${path}`;
  return fetch(`${KITE_PROXY_ORIGIN}/kite${rel}`, init);
}
