// tauriFetch — a fetch() shim that routes backend HTTP through the Rust
// `api_fetch` command when running inside the packaged Tauri app.
//
// WHY: the packaged webview origin is `tauri.localhost`, which is NOT on the
// backend CORS allowlist for api-web.stratai.live. A browser-context fetch()
// therefore fails with "Failed to fetch" (blocked by CORS). reqwest in Rust
// runs server-to-server and is not subject to CORS, so we proxy through it.
//
// In a plain browser (`npm run dev`) there is no Tauri runtime, so we fall
// back to the native window.fetch untouched.

type ApiFetchResponse = { status: number; ok: boolean; body: string };

function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

// Minimal Response-like object exposing the parts the API client uses:
// .ok, .status, .json(), .text().
function makeResponse(res: ApiFetchResponse): Response {
  const body = res.body ?? '';
  return {
    ok: res.ok,
    status: res.status,
    json: async () => JSON.parse(body),
    text: async () => body,
  } as Response;
}

/**
 * Drop-in replacement for fetch() for backend (*.stratai.live) calls. Uses the
 * Rust proxy under Tauri, otherwise the native fetch.
 */
export async function tauriFetch(url: string, init: RequestInit = {}): Promise<Response> {
  if (!isTauri()) {
    return fetch(url, init);
  }

  const { invoke } = await import('@tauri-apps/api/core');
  const method = (init.method ?? 'GET').toUpperCase();

  // Normalize headers (Headers | record | array) into a plain string map.
  const headers: Record<string, string> = {};
  const h = init.headers;
  if (h instanceof Headers) {
    h.forEach((v, k) => {
      headers[k] = v;
    });
  } else if (Array.isArray(h)) {
    for (const [k, v] of h) headers[k] = v;
  } else if (h && typeof h === 'object') {
    Object.assign(headers, h as Record<string, string>);
  }

  const body = typeof init.body === 'string' ? init.body : undefined;

  const res = await invoke<ApiFetchResponse>('api_fetch', {
    method,
    url,
    headers: Object.keys(headers).length ? headers : undefined,
    body,
  });

  return makeResponse(res);
}
