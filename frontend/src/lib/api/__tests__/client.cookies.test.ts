// @vitest-environment jsdom
//
// The transport carries the session as a cookie, not a bearer token.
//
// This is the assertion that would have caught the whole class of bug this change
// fixes: the API is cross-origin (api-web.stratai.live), so without
// `credentials: 'include'` the browser sends NO cookie, the request looks
// unauthenticated, and the app reads a perfectly good session as logged out.
// `same-origin` — fetch's default — fails exactly that way and silently.
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'https://api.test.invalid';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

import { apiRequest } from '../client';
import { ApiError } from '../types';
import { API_BASE_URL, API_V1_PREFIX } from '../../env';
import { useAuthStore } from '../../../store/useAuthStore';

/** An API envelope response. */
function ok(data: unknown, status = 200) {
  return new Response(JSON.stringify({ success: true, message: 'ok', data }), { status });
}
function unauthorized() {
  return new Response(JSON.stringify({ success: false, message: 'Unauthorized' }), { status: 401 });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ status: 'authenticated', isAuthenticated: true, user: null });
  fetchMock = vi.fn();
  global.fetch = fetchMock as unknown as typeof fetch;
});

describe('apiRequest', () => {
  it("sends credentials so the .stratai.live cookie rides along", async () => {
    fetchMock.mockResolvedValue(ok({ id: 'u_1' }));

    await apiRequest('/users/me');

    const [url, init] = fetchMock.mock.calls[0];
    // Base URL comes from `vitest.config.ts`; what matters here is the path and
    // that credentials are sent.
    expect(String(url)).toBe(`${API_BASE_URL}${API_V1_PREFIX}/users/me`);
    expect(init.credentials).toBe('include');
  });

  it('attaches no Authorization header', async () => {
    fetchMock.mockResolvedValue(ok({}));

    await apiRequest('/users/me');

    // There is nothing to attach: the token is httpOnly and unreadable from JS.
    // That is the point — the previous version kept it in localStorage, so any
    // XSS on this origin was a full session theft.
    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain('authorization');
  });

  it('omits credentials for an explicitly unauthenticated call', async () => {
    fetchMock.mockResolvedValue(ok({}));
    await apiRequest('/public/thing', { auth: false });
    expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin');
  });

  it('refreshes once on a 401 and retries the original request', async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized()) // original
      .mockResolvedValueOnce(ok({})) //         refresh
      .mockResolvedValueOnce(ok({ id: 'u_1' })); // retry

    await expect(apiRequest('/users/me')).resolves.toEqual({ id: 'u_1' });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [refreshUrl, refreshInit] = fetchMock.mock.calls[1];
    expect(String(refreshUrl)).toContain('/auth/refresh-token');
    // The refresh token is a cookie too — nothing is passed in the body.
    expect(refreshInit).toMatchObject({ method: 'POST', credentials: 'include' });
    expect(useAuthStore.getState().status).toBe('authenticated');
  });

  it('does not loop when the retry is also unauthorized', async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(ok({}))
      .mockResolvedValueOnce(unauthorized());

    await expect(apiRequest('/users/me')).rejects.toThrow(ApiError);
    // Exactly one refresh attempt: original, refresh, retry. A second refresh
    // here would spin against a genuinely dead session.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('clears the session when the refresh itself fails', async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(new Response('{}', { status: 401 }));

    await expect(apiRequest('/users/me')).rejects.toThrow(/Session expired/);
    // Cleared, but NOT redirected: choosing where an unauthenticated user goes
    // belongs to the app shell, not to whichever background request noticed.
    expect(useAuthStore.getState().status).toBe('anonymous');
  });

  it('shares one refresh across concurrent 401s', async () => {
    // With refresh-token rotation, parallel refreshes invalidate each other and
    // log the user out mid-session, so the single-flight guard is load-bearing.
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/auth/refresh-token')) return ok({});
      return fetchMock.mock.calls.filter(([u]) => !String(u).includes('refresh')).length <= 3
        ? unauthorized()
        : ok({ id: 'u_1' });
    });

    await Promise.allSettled([
      apiRequest('/users/me'),
      apiRequest('/credit/'),
      apiRequest('/billing/history'),
    ]);

    const refreshCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/auth/refresh-token'),
    );
    expect(refreshCalls).toHaveLength(1);
  });
});
