// @vitest-environment jsdom
//
// The cookie session model.
//
// The property that matters most is the three-state one: `unknown` must never be
// mistaken for `anonymous`. The old store derived `isAuthenticated` from
// `localStorage` at construction with no expiry check, so a token that had
// expired days ago read as signed in and the terminal rendered before the first
// API call failed. Now the status only ever comes from an answer, and the app
// shell only redirects on a CONFIRMED `anonymous` — a returning user must not be
// bounced out to the auth page while their own session is still being verified.
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

const { getMe, updateMe } = vi.hoisted(() => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}));
vi.mock('../../lib/api/endpoints', () => ({
  usersApi: { getMe, updateMe },
  creditApi: { get: vi.fn() },
  billingApi: { history: vi.fn(), invoice: vi.fn() },
}));

import { useAuthStore } from '../useAuthStore';
import { useFeatureStore } from '../useFeatureStore';

const USER = {
  id: 'u_1',
  email: 'a@b.co',
  name: 'A',
  username: 'a',
  role: 'user',
};

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({
    status: 'unknown',
    isAuthenticated: false,
    user: null,
    isBrokerConnected: true,
  });
  global.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as unknown as typeof fetch;
});

describe('initial state', () => {
  it('starts unknown, not anonymous, and not authenticated', () => {
    const s = useAuthStore.getState();
    // "We have not asked yet" is a third thing. Reporting it as anonymous would
    // redirect every returning user away before their cookie was checked.
    expect(s.status).toBe('unknown');
    expect(s.isAuthenticated).toBe(false);
    expect(s.user).toBeNull();
  });

  it('does not read a session out of localStorage', async () => {
    // The session is an httpOnly cookie; nothing about it is JS-readable. A
    // leftover flag from the old token-based store must not resurrect a session.
    localStorage.setItem('strat_authenticated', 'true');
    localStorage.setItem('token', 'stale-jwt');
    vi.resetModules();
    const { useAuthStore: fresh } = await import('../useAuthStore');
    expect(fresh.getState().status).toBe('unknown');
    expect(fresh.getState().isAuthenticated).toBe(false);
    localStorage.clear();
  });
});

describe('checkAuth', () => {
  it('confirms a session from /users/me and loads the user', async () => {
    getMe.mockResolvedValue(USER);

    await expect(useAuthStore.getState().checkAuth()).resolves.toBe('authenticated');

    const s = useAuthStore.getState();
    expect(s.status).toBe('authenticated');
    expect(s.isAuthenticated).toBe(true);
    expect(s.user).toEqual(USER);
  });

  it('reports anonymous when there is no session', async () => {
    getMe.mockRejectedValue(new Error('Session expired'));

    await expect(useAuthStore.getState().checkAuth()).resolves.toBe('anonymous');
    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it('treats an unreachable API as anonymous rather than assuming a session', async () => {
    // Failing open would render a logged-out terminal full of empty panels and
    // let gated features be attempted; failing closed sends the user to sign in.
    getMe.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(useAuthStore.getState().checkAuth()).resolves.toBe('anonymous');
  });

  it('clears the feature-gate snapshot when the session turns out to be gone', async () => {
    useFeatureStore.setState({ accessFlags: { canAccessResearch: true } as never });
    getMe.mockRejectedValue(new Error('401 Unauthorized'));

    await useAuthStore.getState().checkAuth();

    // A previous session's plan flags must not survive into a logged-out state.
    expect(useFeatureStore.getState().accessFlags).toBeNull();
  });

  it('coalesces concurrent calls into one request', async () => {
    getMe.mockResolvedValue(USER);

    // Several components mount at once and each checks the session.
    const results = await Promise.all([
      useAuthStore.getState().checkAuth(),
      useAuthStore.getState().checkAuth(),
      useAuthStore.getState().checkAuth(),
    ]);

    expect(results).toEqual(['authenticated', 'authenticated', 'authenticated']);
    expect(getMe).toHaveBeenCalledTimes(1);
  });

  it('can be re-run after settling', async () => {
    getMe.mockResolvedValue(USER);
    await useAuthStore.getState().checkAuth();
    await useAuthStore.getState().checkAuth();
    expect(getMe).toHaveBeenCalledTimes(2);
  });
});

describe('logout', () => {
  it('revokes the cookie server-side, then reports anonymous', async () => {
    getMe.mockResolvedValue(USER);
    await useAuthStore.getState().checkAuth();

    await useAuthStore.getState().logout();

    // Only the server can clear an httpOnly cookie, and because it is scoped to
    // `.stratai.live` this signs the user out of every surface — which is what
    // pressing "log out" means.
    const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain('/auth/logout');
    expect(init).toMatchObject({ method: 'POST', credentials: 'include' });

    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isBrokerConnected).toBe(false);
  });

  it('still clears locally when the revoke call fails', async () => {
    global.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    // A failed revoke must not trap someone in a session they asked to leave.
    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined();
    expect(useAuthStore.getState().status).toBe('anonymous');
  });
});

describe('clearSession', () => {
  it('drops the session without calling the server', () => {
    // The 401 path: the session is already gone, so there is nothing to revoke.
    useAuthStore.setState({ status: 'authenticated', isAuthenticated: true, user: USER });

    useAuthStore.getState().clearSession();

    expect(global.fetch).not.toHaveBeenCalled();
    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(useAuthStore.getState().user).toBeNull();
  });
});
