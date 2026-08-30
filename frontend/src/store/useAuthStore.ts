// store/useAuthStore.ts — the terminal's view of the shared Strat AI session.
//
// The session lives in an httpOnly cookie pair (`access_token` / `refresh_token`)
// issued by the auth API for `domain=.stratai.live`. Every Strat AI surface —
// auth, dashboard, this terminal — is a subdomain of that, so a login performed
// on any one of them is a login on all of them. The browser attaches the cookie
// to our API calls; JavaScript never sees it.
//
// What this replaced, and why:
//
//   · A desktop handshake. `login()` used to POST `/auth/desktop/session`, open a
//     browser, then race a Tauri `desktop-login-success` deep-link event against
//     a 2-second polling loop with a 5-minute timeout, and exchange the winner
//     for tokens. It existed to carry a session into a Tauri shell that no longer
//     ships; on the web it was an elaborate way to reach a URL.
//
//   · Tokens in `localStorage`. `token`, `strat_refresh_token`, `user` and
//     `strat_authenticated` were all JS-readable, which makes any XSS a full
//     session theft. httpOnly cookies remove that class of exposure entirely.
//
//   · A GUESSED auth state. `isAuthenticated` was
//     `localStorage['strat_authenticated'] === 'true' && !!token`, computed once
//     at store creation with no expiry check — so a token that expired days ago
//     read as signed in, and the app rendered the whole terminal before the first
//     API call failed. `status` below is only ever set from an actual answer from
//     the server.
//
// The `unknown` → `authenticated` | `anonymous` progression is the point of the
// three-state model: "we have not asked yet" is not the same claim as "you are
// not signed in", and only the second one justifies redirecting someone away.

import { create } from 'zustand';
import { API_BASE_URL, API_V1_PREFIX } from '../lib/env';
import { usersApi } from '../lib/api/endpoints';
import { useFeatureStore } from './useFeatureStore';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  username: string;
  role: string;
}

/**
 * Whether the visitor holds a live session.
 *
 * `unknown` until `/users/me` answers. Callers MUST NOT treat it as anonymous —
 * see the comment on the state machine above.
 */
export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous';

interface AuthState {
  status: AuthStatus;
  /** True only for a confirmed session. `unknown` is deliberately not truthy. */
  isAuthenticated: boolean;
  user: AuthUser | null;
  isBrokerConnected: boolean;
  /**
   * Resolve `status` by asking the API who we are. Safe to call repeatedly;
   * concurrent calls share one request.
   */
  checkAuth: () => Promise<AuthStatus>;
  /** Drop the server-side session, then hand off to the auth surface. */
  logout: () => Promise<void>;
  /** Forget the local session without calling the server (401 handling). */
  clearSession: () => void;
  setBrokerConnected: (connected: boolean) => void;
  fetchProfile: () => Promise<void>;
  fetchUserProfile: () => Promise<void>;
  updateName: (name: string) => Promise<void>;
}

/** Shared in-flight `checkAuth`, so a mount storm makes one request. */
let checkPromise: Promise<AuthStatus> | null = null;

export const useAuthStore = create<AuthState>((set, get) => ({
  // No optimistic restore from storage: there is nothing readable to restore
  // from, and guessing is what made the old store claim a dead session was live.
  status: 'unknown',
  isAuthenticated: false,
  user: null,
  isBrokerConnected: true,

  checkAuth: async () => {
    if (checkPromise) return checkPromise;

    checkPromise = (async (): Promise<AuthStatus> => {
      try {
        // `usersApi.getMe` goes through `apiRequest`, which sends
        // `credentials: 'include'` and transparently refreshes on a 401.
        const user = (await usersApi.getMe()) as AuthUser;
        set({ status: 'authenticated', isAuthenticated: true, user });
        return 'authenticated';
      } catch {
        // Any failure to establish identity is treated as anonymous. That
        // includes a network fault: the terminal cannot be entered without a
        // confirmed session, and pretending otherwise would render a logged-out
        // shell full of empty panels.
        set({ status: 'anonymous', isAuthenticated: false, user: null });
        useFeatureStore.getState().reset();
        return 'anonymous';
      }
    })().finally(() => {
      checkPromise = null;
    });

    return checkPromise;
  },

  clearSession: () => {
    // Clear the feature-gate snapshot too, so a previous session's plan flags
    // cannot leak into the next one.
    useFeatureStore.getState().reset();
    set({
      status: 'anonymous',
      isAuthenticated: false,
      user: null,
      isBrokerConnected: false,
    });
  },

  logout: async () => {
    // Ask the server to clear the cookies. Only it can: they are httpOnly, and
    // they are scoped to `.stratai.live`, so signing out here signs the user out
    // of every surface — which is what a user pressing "log out" means.
    try {
      await fetch(`${API_BASE_URL}${API_V1_PREFIX}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (err) {
      // A failed revoke must not trap the user in a session they asked to leave;
      // fall through to the local clear and the redirect. The cookie may outlive
      // this, but it expires on its own and the next surface will re-check.
      console.warn('[Auth] Server logout failed; clearing locally anyway:', err);
    }
    get().clearSession();
  },

  setBrokerConnected: (connected) => set({ isBrokerConnected: connected }),

  fetchProfile: async () => {
    // Unlike the old version there is no token to check first — whether a
    // session exists is the server's answer to give.
    try {
      const user = (await usersApi.getMe()) as AuthUser;
      set({ status: 'authenticated', isAuthenticated: true, user });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch profile';
      if (/401|Unauthorized|Session expired/i.test(message)) {
        get().clearSession();
      } else {
        console.error('[Auth] Fetch user profile failed:', err);
      }
    }
  },

  fetchUserProfile: async () => {
    await get().fetchProfile();
  },

  updateName: async (name: string) => {
    const user = (await usersApi.updateMe({ name })) as AuthUser;
    set({ user });
  },
}));
