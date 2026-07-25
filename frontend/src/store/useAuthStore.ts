import { create } from 'zustand';
import { API_BASE_URL, API_V1_PREFIX } from '../lib/env';
import { usersApi } from '../lib/api/endpoints';
import { REFRESH_TOKEN_KEY } from '../lib/api/client';
import { useFeatureStore } from './useFeatureStore';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  username: string;
  role: string;
}

interface AuthState {
  isAuthenticated: boolean;
  token: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  isBrokerConnected: boolean;
  login: () => Promise<void>;
  logout: () => void;
  setBrokerConnected: (connected: boolean) => void;
  fetchProfile: () => Promise<void>;
  fetchUserProfile: () => Promise<void>;
  updateName: (name: string) => Promise<void>;
}

const ACCESS_TOKEN_KEY = 'token';
const AUTH_FLAG_KEY = 'strat_authenticated';
const USER_KEY = 'user';

function readLocalStorage(key: string): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(key);
}

function writeLocalStorage(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(key, value);
}

function removeLocalStorage(key: string): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(key);
}

export const useAuthStore = create<AuthState>((set, get) => {
  const storedAuth = readLocalStorage(AUTH_FLAG_KEY) === 'true';
  const storedToken = readLocalStorage(ACCESS_TOKEN_KEY);
  const storedRefresh = readLocalStorage(REFRESH_TOKEN_KEY);
  const storedUser = (() => {
    const raw = readLocalStorage(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as AuthUser;
    } catch {
      return null;
    }
  })();

  return {
    isAuthenticated: storedAuth && !!storedToken,
    token: storedToken,
    refreshToken: storedRefresh,
    user: storedUser,
    isBrokerConnected: true,

    login: async () => {
      const response = await fetch(`${API_BASE_URL}${API_V1_PREFIX}/auth/desktop/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to initiate desktop login session');
      }

      const resJson = await response.json();
      const { sessionId, loginUrl } = resJson.data;

      try {
        const { invoke } = await import('@tauri-apps/api/core');
        await invoke('open_browser', { url: loginUrl });
      } catch {
        if (typeof window !== 'undefined') {
          window.open(loginUrl, '_blank', 'noopener,noreferrer');
        }
      }

      const exchangeToken = async (loginToken: string) => {
        const exchangeRes = await fetch(`${API_BASE_URL}${API_V1_PREFIX}/auth/desktop/exchange`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: loginToken }),
        });
        if (!exchangeRes.ok) {
          throw new Error('Token exchange failed');
        }
        const dataJson = await exchangeRes.json();
        const { accessToken, refreshToken, user } = dataJson.data;

        writeLocalStorage(AUTH_FLAG_KEY, 'true');
        writeLocalStorage(ACCESS_TOKEN_KEY, accessToken);
        writeLocalStorage(REFRESH_TOKEN_KEY, refreshToken);
        writeLocalStorage(USER_KEY, JSON.stringify(user));

        set({
          isAuthenticated: true,
          token: accessToken,
          refreshToken,
          user,
          isBrokerConnected: true,
        });
      };

      let unlistenSuccess: (() => void) | undefined;
      const tauriPromise = new Promise<string>((resolve) => {
        import('@tauri-apps/api/event').then(async ({ listen }) => {
          try {
            const unlisten = await listen<{ token: string }>('desktop-login-success', (event) => {
              resolve(event.payload.token);
            });
            unlistenSuccess = unlisten;
          } catch {
            // Not in Tauri environment or listener failed — polling will handle it.
          }
        });
      });

      let isCompleted = false;
      const pollPromise = new Promise<string>((resolve, reject) => {
        const interval = setInterval(async () => {
          if (isCompleted) {
            clearInterval(interval);
            return;
          }
          try {
            const statusRes = await fetch(`${API_BASE_URL}${API_V1_PREFIX}/auth/desktop/session/${sessionId}`);
            if (!statusRes.ok) return;
            const statusData = await statusRes.json();
            const { status, token } = statusData.data;

            if (status === 'authenticated' && token) {
              isCompleted = true;
              clearInterval(interval);
              resolve(token);
            } else if (status === 'expired') {
              isCompleted = true;
              clearInterval(interval);
              reject(new Error('Login session expired. Please try again.'));
            }
          } catch (err) {
            console.error('[Auth Store] Polling session status failed:', err);
          }
        }, 2000);

        setTimeout(() => {
          if (!isCompleted) {
            isCompleted = true;
            clearInterval(interval);
            reject(new Error('Login timed out.'));
          }
        }, 5 * 60 * 1000);
      });

      try {
        const finalToken = await Promise.race([tauriPromise, pollPromise]);
        isCompleted = true;
        if (unlistenSuccess) unlistenSuccess();
        await exchangeToken(finalToken);
      } catch (err) {
        if (unlistenSuccess) unlistenSuccess();
        throw err;
      }
    },

    logout: () => {
      removeLocalStorage(AUTH_FLAG_KEY);
      removeLocalStorage(USER_KEY);
      removeLocalStorage(ACCESS_TOKEN_KEY);
      removeLocalStorage(REFRESH_TOKEN_KEY);
      // Clear the feature-gate snapshot so a stale access map from the
      // previous session can't leak into a fresh login.
      useFeatureStore.getState().reset();
      set({
        isAuthenticated: false,
        token: null,
        refreshToken: null,
        user: null,
        isBrokerConnected: false,
      });
    },

    setBrokerConnected: (connected) => set({ isBrokerConnected: connected }),

    fetchProfile: async () => {
      const token = get().token;
      if (!token) return;

      try {
        const user = await usersApi.getMe();
        writeLocalStorage(USER_KEY, JSON.stringify(user));
        set({ user });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch profile';
        if (/401|Unauthorized|Token refresh failed|No refresh token/i.test(message)) {
          get().logout();
        } else {
          console.error('[Auth Store] Fetch user profile failed:', err);
        }
      }
    },

    fetchUserProfile: async () => {
      await get().fetchProfile();
    },

    updateName: async (name: string) => {
      const user = await usersApi.updateMe({ name });
      writeLocalStorage(USER_KEY, JSON.stringify(user));
      set({ user });
    },
  };
});
