// app/api/__tests__/_identity.test.ts
//
// Two jobs:
//
//  1. **Pin the wire format** against the Python verifier. The MAC is implemented
//     twice — here in TypeScript and in `agents/deep-quant-loop/internal_identity.py`
//     — so a divergence has to be a failing unit test rather than a 401 in
//     production. The expected values below are the output of
//     `internal_identity.reference_vector()`, captured by running it.
//
//  2. **Pin the staged-rollout behaviour.** `identityHeaders` must forward unminted
//     while `FQ_REQUIRE_IDENTITY` is off and refuse only when it is on. Getting that
//     backwards would either take the agent surface down on a transient auth-API
//     outage, or leave the boundary permanently open.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __resetIdentityCache,
  IDENTITY_HEADER,
  identityHeaders,
  mintIdentityHeader,
  readCookie,
  requireIdentity,
  resolveUserId,
  safeEqual,
  unauthenticated,
  VECTOR_IAT,
  VECTOR_SECRET,
  VECTOR_SUB,
  VECTOR_TTL,
} from '../_identity';

// Captured from `python -c "import internal_identity as i; print(i.reference_vector())"`.
// If a change here is intentional, the Python side must change in the SAME commit.
const EXPECTED_PAYLOAD =
  'eyJleHAiOjE3MDAwMDAwNjAsImlhdCI6MTcwMDAwMDAwMCwic3ViIjoidXNlcl9hYmMxMjMifQ';
const EXPECTED_MAC = 'QQsTxFHmFuwoVoDv3WASxUiWwPyixHmt958Leote0tE';

const SECRET = 'f'.repeat(64);

function reqWith(headers: Record<string, string> = {}): Request {
  return new Request('https://app.stratai.live/api/deepquant/run', { headers });
}

/** An api-web `/users/me` success envelope. */
function meOk(id: unknown) {
  return {
    ok: true,
    json: async () => ({ success: true, data: { id } }),
  } as unknown as Response;
}

beforeEach(() => {
  __resetIdentityCache();
  delete process.env.FQ_REQUIRE_IDENTITY;
  process.env.INTERNAL_IDENTITY_SECRET = SECRET;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  __resetIdentityCache();
});

// ── Cross-language vector ────────────────────────────────────────────────────

describe('wire format', () => {
  it('reproduces the Python reference vector exactly', () => {
    const token = mintIdentityHeader(VECTOR_SUB, {
      now: VECTOR_IAT,
      ttl: VECTOR_TTL,
      secret: VECTOR_SECRET,
    });
    expect(token).toBe(`${EXPECTED_PAYLOAD}.${EXPECTED_MAC}`);
  });

  it('emits unpadded base64url so the token is header-safe', () => {
    const token = mintIdentityHeader('user_x', { secret: SECRET })!;
    expect(token).not.toContain('=');
    expect(token).not.toContain('+');
    expect(token).not.toContain('/');
    expect(token.split('.')).toHaveLength(2);
  });

  it('emits a 43-character MAC (unpadded base64url of a SHA-256 digest)', () => {
    const [, mac] = mintIdentityHeader('user_x', { secret: SECRET })!.split('.');
    expect(mac).toHaveLength(43);
  });

  it('carries sub/iat/exp and nothing else', () => {
    const [payload] = mintIdentityHeader('user_x', { now: 1000, ttl: 60, secret: SECRET })!.split('.');
    const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
    expect(claims).toEqual({ exp: 1060, iat: 1000, sub: 'user_x' });
  });

  it('changes the MAC when the subject changes', () => {
    const a = mintIdentityHeader('user_a', { now: 1000, secret: SECRET })!;
    const b = mintIdentityHeader('user_b', { now: 1000, secret: SECRET })!;
    expect(a).not.toBe(b);
  });
});

describe('mintIdentityHeader guards', () => {
  it('returns null without a secret, rather than an unsigned token', () => {
    expect(mintIdentityHeader('user_x', { secret: null })).toBeNull();
  });

  it('returns null for a secret shorter than 32 chars', () => {
    process.env.INTERNAL_IDENTITY_SECRET = 'tooshort';
    expect(mintIdentityHeader('user_x')).toBeNull();
  });

  it.each(['', '   '])('returns null for a blank user id (%p)', (uid) => {
    expect(mintIdentityHeader(uid, { secret: SECRET })).toBeNull();
  });
});

describe('safeEqual', () => {
  it('is true for equal strings and false otherwise, including unequal lengths', () => {
    expect(safeEqual('abc', 'abc')).toBe(true);
    expect(safeEqual('abc', 'abd')).toBe(false);
    // timingSafeEqual throws on a length mismatch; this must return false, not throw.
    expect(safeEqual('abc', 'abcd')).toBe(false);
  });
});

// ── Cookie parsing ───────────────────────────────────────────────────────────

describe('readCookie', () => {
  it('reads a value from a multi-cookie header', () => {
    expect(readCookie('a=1; access_token=tok123; b=2', 'access_token')).toBe('tok123');
  });

  it('returns null when absent, empty, or the header is missing', () => {
    expect(readCookie(null, 'access_token')).toBeNull();
    expect(readCookie('a=1', 'access_token')).toBeNull();
    expect(readCookie('access_token=', 'access_token')).toBeNull();
    expect(readCookie('access_token=   ', 'access_token')).toBeNull();
  });

  it('does not match a name that merely ends with the target', () => {
    expect(readCookie('not_access_token=nope', 'access_token')).toBeNull();
  });

  it('takes the first occurrence deterministically', () => {
    // A stale host-scoped cookie alongside the domain-scoped one really happens
    // after a cookie-scope change; resolution must not depend on header order.
    expect(readCookie('access_token=first; access_token=second', 'access_token')).toBe('first');
  });

  it('tolerates a malformed segment without throwing', () => {
    expect(readCookie('garbage; access_token=tok', 'access_token')).toBe('tok');
  });
});

// ── resolveUserId ────────────────────────────────────────────────────────────

describe('resolveUserId', () => {
  it('returns the id from a successful /users/me', async () => {
    const fetchMock = vi.fn().mockResolvedValue(meOk('user_42'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(resolveUserId(reqWith({ cookie: 'access_token=tok' }))).resolves.toBe('user_42');
  });

  it('sends ONLY the access_token cookie upstream', async () => {
    // The whole point of minting an assertion instead of forwarding credentials is
    // that no other .stratai.live cookie leaves this tier.
    const fetchMock = vi.fn().mockResolvedValue(meOk('user_42'));
    vi.stubGlobal('fetch', fetchMock);
    await resolveUserId(reqWith({ cookie: 'other=secret; access_token=tok; third=x' }));
    const sent = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(sent.Cookie).toBe('access_token=tok');
    expect(sent.Cookie).not.toContain('secret');
  });

  it('does not call the auth API at all when there is no cookie', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    await expect(resolveUserId(reqWith())).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['a non-2xx response', { ok: false, json: async () => ({}) }],
    ['success:false', { ok: true, json: async () => ({ success: false, data: { id: 'x' } }) }],
    ['a missing id', { ok: true, json: async () => ({ success: true, data: {} }) }],
    ['a non-string id', { ok: true, json: async () => ({ success: true, data: { id: 42 } }) }],
    ['a blank id', { ok: true, json: async () => ({ success: true, data: { id: '  ' } }) }],
    ['an unparseable body', { ok: true, json: async () => { throw new Error('nope'); } }],
  ])('returns null for %s', async (_label, response) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response as unknown as Response));
    await expect(resolveUserId(reqWith({ cookie: 'access_token=tok' }))).resolves.toBeNull();
  });

  it('returns null instead of throwing when the auth API is unreachable', async () => {
    // A resolver that throws turns an auth miss into a 500.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    await expect(resolveUserId(reqWith({ cookie: 'access_token=tok' }))).resolves.toBeNull();
  });

  it('caches a hit so one user action is not three round trips', async () => {
    const fetchMock = vi.fn().mockResolvedValue(meOk('user_42'));
    vi.stubGlobal('fetch', fetchMock);
    const req = reqWith({ cookie: 'access_token=tok' });
    await resolveUserId(req);
    await resolveUserId(req);
    await resolveUserId(req);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('caches a MISS too, so an expired cookie does not hammer the auth API', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);
    const req = reqWith({ cookie: 'access_token=stale' });
    await resolveUserId(req);
    await resolveUserId(req);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not share a resolution between different tokens', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(meOk('user_a'))
      .mockResolvedValueOnce(meOk('user_b'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(resolveUserId(reqWith({ cookie: 'access_token=a' }))).resolves.toBe('user_a');
    await expect(resolveUserId(reqWith({ cookie: 'access_token=b' }))).resolves.toBe('user_b');
  });
});

// ── The staged rollout ───────────────────────────────────────────────────────

describe('requireIdentity switch', () => {
  it.each([
    ['1', true], ['true', true], ['TRUE', true], ['yes', true], ['on', true],
    ['0', false], ['false', false], ['', false], ['nonsense', false],
  ])('%p -> %p', (value, expected) => {
    process.env.FQ_REQUIRE_IDENTITY = value;
    expect(requireIdentity()).toBe(expected);
  });

  it('defaults to off when unset', () => {
    delete process.env.FQ_REQUIRE_IDENTITY;
    expect(requireIdentity()).toBe(false);
  });
});

describe('identityHeaders', () => {
  it('mints the assertion header when the identity resolves', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(meOk('user_42')));
    const headers = await identityHeaders(reqWith({ cookie: 'access_token=tok' }));
    expect(headers).not.toBeNull();
    expect(headers![IDENTITY_HEADER]).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/);
  });

  it('forwards UNMINTED when unresolved and enforcement is off', async () => {
    // A transient /users/me outage must not take the agent surface down. `{}` means
    // proceed; `null` means refuse.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    await expect(identityHeaders(reqWith({ cookie: 'access_token=tok' }))).resolves.toEqual({});
  });

  it('refuses when unresolved and enforcement is on', async () => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    await expect(identityHeaders(reqWith({ cookie: 'access_token=tok' }))).resolves.toBeNull();
  });

  it('refuses when there is no cookie at all and enforcement is on', async () => {
    process.env.FQ_REQUIRE_IDENTITY = '1';
    vi.stubGlobal('fetch', vi.fn());
    await expect(identityHeaders(reqWith())).resolves.toBeNull();
  });

  it('refuses, loudly, when the identity resolves but the secret is missing', async () => {
    // Resolved-but-unassertable is a configuration fault, not a user's auth failure,
    // so the operator has to be able to tell them apart from the log.
    process.env.FQ_REQUIRE_IDENTITY = '1';
    delete process.env.INTERNAL_IDENTITY_SECRET;
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(meOk('user_42')));

    await expect(identityHeaders(reqWith({ cookie: 'access_token=tok' }))).resolves.toBeNull();
    expect(err).toHaveBeenCalledWith(expect.stringContaining('INTERNAL_IDENTITY_SECRET'));
  });

  it('proceeds unminted when the secret is missing and enforcement is off', async () => {
    delete process.env.INTERNAL_IDENTITY_SECRET;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(meOk('user_42')));
    await expect(identityHeaders(reqWith({ cookie: 'access_token=tok' }))).resolves.toEqual({});
  });
});

describe('unauthenticated', () => {
  it('is a 401 that names no failed check', async () => {
    const res = unauthenticated();
    expect(res.status).toBe(401);
    // Telling an unauthenticated caller WHICH check failed helps only them.
    expect(await res.json()).toEqual({ error: 'authentication required' });
  });
});
