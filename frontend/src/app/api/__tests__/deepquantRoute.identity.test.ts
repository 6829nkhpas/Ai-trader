// app/api/__tests__/deepquantRoute.identity.test.ts
//
// The handler-level half of the authentication boundary. `_identity.test.ts` proves
// the primitives; this proves they are actually wired into the request path, which is
// a different claim and the one that can silently regress.
//
// The load-bearing assertion throughout is `expect(fetchMock).not.toHaveBeenCalled()`:
// a refusal that still contacted the upstream is not a refusal.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetIdentityCache, IDENTITY_HEADER } from '../_identity';
import { GET, POST } from '../deepquant/[...path]/route';

const SECRET = 'a'.repeat(64);

/** `ctx.params` is a Promise in this Next version. */
function ctx(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

function req(url: string, headers: Record<string, string> = {}, method = 'POST'): Request {
  return new Request(url, {
    method,
    headers,
    ...(method === 'POST' ? { body: '{}' } : {}),
  });
}

/** The upstream response the proxy hands back on success. */
function upstreamOk() {
  return new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json' } });
}

/** An api-web `/users/me` success envelope. */
function meOk(id: string) {
  return { ok: true, json: async () => ({ success: true, data: { id } }) } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  __resetIdentityCache();
  process.env.INTERNAL_IDENTITY_SECRET = SECRET;
  delete process.env.FQ_REQUIRE_IDENTITY;
  // Non-enforcing deployment, so the feature kill switch is open and cannot be the
  // reason a request is refused in these tests.
  delete process.env.FEATURE_ENFORCEMENT;
  // Point the proxy at an explicit upstream so no gateway credential path is involved.
  process.env.DEEP_QUANT_URL = 'http://deep-quant:8086';

  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  __resetIdentityCache();
});

/** Route `fetch` by URL: the auth API vs the deep-quant upstream. */
function routeFetch(opts: { userId?: string | null; authFails?: boolean } = {}) {
  fetchMock.mockImplementation(async (input: string | URL | Request) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url.includes('/users/me')) {
      if (opts.authFails) throw new Error('auth api unreachable');
      if (opts.userId == null) return { ok: false, json: async () => ({}) } as unknown as Response;
      return meOk(opts.userId);
    }
    return upstreamOk();
  });
}

/** The upstream call, if one was made. */
function upstreamCall() {
  return fetchMock.mock.calls.find(([input]) => {
    const url = typeof input === 'string' ? input : (input as Request).url ?? String(input);
    return !url.includes('/users/me');
  });
}

function upstreamHeaders(): Headers | undefined {
  const call = upstreamCall();
  if (!call) return undefined;
  return new Headers((call[1] as RequestInit).headers as HeadersInit);
}

describe('deepquant route — identity minting', () => {
  it('mints the assertion for an authenticated agent request', async () => {
    routeFetch({ userId: 'user_42' });
    const res = await POST(
      req('https://app.stratai.live/api/deepquant/run', { cookie: 'access_token=tok' }),
      ctx(['run']),
    );
    expect(res.status).toBe(200);
    expect(upstreamHeaders()!.get(IDENTITY_HEADER)).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/);
  });

  it('never relays a CLIENT-supplied identity header', async () => {
    // Two independent guards should make this impossible: _gateway strips the
    // client's copy on the way in, and extraHeaders is applied after forwardHeaders
    // so ours overwrites it. This asserts the composed result.
    routeFetch({ userId: 'user_42' });
    await POST(
      req('https://app.stratai.live/api/deepquant/run', {
        cookie: 'access_token=tok',
        [IDENTITY_HEADER]: 'forged.payload',
      }),
      ctx(['run']),
    );
    expect(upstreamHeaders()!.get(IDENTITY_HEADER)).not.toBe('forged.payload');
  });

  it('strips a client-supplied identity header even when nothing is minted', async () => {
    // The dangerous case: identity does not resolve, enforcement is off, so the
    // request proceeds unminted. The client's forged header must NOT survive into
    // that gap — deep-quant would otherwise verify a header nobody vouched for.
    delete process.env.INTERNAL_IDENTITY_SECRET;
    routeFetch({ userId: null });
    await POST(
      req('https://app.stratai.live/api/deepquant/run', {
        cookie: 'access_token=tok',
        [IDENTITY_HEADER]: 'forged.payload',
        'x-stratai-service': 'forged.service',
      }),
      ctx(['run']),
    );
    const sent = upstreamHeaders()!;
    expect(sent.get(IDENTITY_HEADER)).toBeNull();
    expect(sent.get('x-stratai-service')).toBeNull();
  });

  it('still strips the browser cookie from the upstream request', async () => {
    // The pre-existing property this work must not weaken: a compromised upstream
    // cannot read the user's session.
    routeFetch({ userId: 'user_42' });
    await POST(
      req('https://app.stratai.live/api/deepquant/run', { cookie: 'access_token=tok' }),
      ctx(['run']),
    );
    expect(upstreamHeaders()!.get('cookie')).toBeNull();
  });
});

describe('deepquant route — staged enforcement', () => {
  it('proceeds unminted when identity is unresolved and enforcement is off', async () => {
    routeFetch({ authFails: true });
    const res = await POST(
      req('https://app.stratai.live/api/deepquant/run', { cookie: 'access_token=tok' }),
      ctx(['run']),
    );
    // A transient auth-API outage must not take the agent surface down.
    expect(res.status).toBe(200);
    expect(upstreamHeaders()!.get(IDENTITY_HEADER)).toBeNull();
  });

  it('401s and NEVER contacts the upstream when enforcement is on and there is no cookie', async () => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    routeFetch({ userId: null });
    const res = await POST(req('https://app.stratai.live/api/deepquant/run'), ctx(['run']));

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: 'authentication required' });
    expect(upstreamCall()).toBeUndefined();
  });

  it('401s when enforcement is on and the cookie is not valid', async () => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    routeFetch({ userId: null });
    const res = await POST(
      req('https://app.stratai.live/api/deepquant/run', { cookie: 'access_token=stale' }),
      ctx(['run']),
    );
    expect(res.status).toBe(401);
    expect(upstreamCall()).toBeUndefined();
  });
});

describe('deepquant route — scope of the boundary', () => {
  it.each([
    ['run', ['run']],
    ['qa', ['qa']],
    ['cancel', ['cancel']],
    ['stream/{id}', ['stream', 'thread_x']],
    ['sessions', ['sessions']],
    ['sessions/{id}/messages', ['sessions', 'sess_x', 'messages']],
    ['runs/{id}/events', ['runs', 'run_x', 'events']],
  ])('requires identity for /%s when enforced', async (_label, segments) => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    routeFetch({ userId: null });
    const res = await GET(
      req(`https://app.stratai.live/api/deepquant/${segments.join('/')}`, {}, 'GET'),
      ctx(segments),
    );
    expect(res.status).toBe(401);
    expect(upstreamCall()).toBeUndefined();
  });

  it('leaves /options/snapshot reachable without a cookie, even when enforced', async () => {
    // The F&O workspace is not user data and is not gated in the UI either. Gating it
    // here would break the options panel for everyone the moment enforcement went on.
    process.env.FQ_REQUIRE_IDENTITY = '1';
    routeFetch({ userId: null });
    const res = await GET(
      req('https://app.stratai.live/api/deepquant/options/snapshot?underlying=NIFTY', {}, 'GET'),
      ctx(['options', 'snapshot']),
    );
    expect(res.status).toBe(200);
    expect(upstreamCall()).toBeDefined();
  });

  it('does not resolve an identity for an ungated path', async () => {
    // No wasted /users/me round trip on the F&O polling path.
    routeFetch({ userId: 'user_42' });
    await GET(
      req('https://app.stratai.live/api/deepquant/options/snapshot?underlying=NIFTY', {
        cookie: 'access_token=tok',
      }, 'GET'),
      ctx(['options', 'snapshot']),
    );
    const authCalls = fetchMock.mock.calls.filter(([input]) =>
      String(typeof input === 'string' ? input : (input as Request).url).includes('/users/me'),
    );
    expect(authCalls).toHaveLength(0);
  });

  it('still 400s an empty path before doing any identity work', async () => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    routeFetch({ userId: null });
    const res = await GET(req('https://app.stratai.live/api/deepquant/', {}, 'GET'), ctx([]));
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
