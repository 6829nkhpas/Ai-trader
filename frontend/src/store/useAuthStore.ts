import { create } from 'zustand';

interface AuthState {
  isAuthenticated: boolean;
  token: string | null;
  user: {
    id: string;
    email: string;
    name: string;
    username: string;
    role: string;
  } | null;
  isBrokerConnected: boolean;
  login: () => Promise<void>;
  logout: () => void;
  setBrokerConnected: (connected: boolean) => void;
  fetchProfile: () => Promise<void>;
  fetchUserProfile: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => {
  // Check localStorage for persisted session
  const storedAuth = typeof window !== 'undefined' ? localStorage.getItem('strat_authenticated') : null;
  const storedToken = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const storedUser = typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('user') || 'null') : null;

  return {
    isAuthenticated: storedAuth === 'true',
    token: storedToken,
    user: storedUser,
    isBrokerConnected: true, // No broker gate needed

    login: async () => {
      // 1. Call desktopSession creation endpoint on backend
      const response = await fetch('https://api-web.stratai.live/api/v1/auth/desktop/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!response.ok) {
        throw new Error('Failed to initiate desktop login session');
      }

      const resJson = await response.json();
      const { sessionId, loginUrl } = resJson.data;

      // 2. Open login page in the browser
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        await invoke('open_browser', { url: loginUrl });
      } catch {
        if (typeof window !== 'undefined') {
          window.open(loginUrl, '_blank', 'noopener,noreferrer');
        }
      }

      // Helper function to handle exchange of loginToken for actual tokens
      const exchangeToken = async (loginToken: string) => {
        const exchangeRes = await fetch('https://api-web.stratai.live/api/v1/auth/desktop/exchange', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: loginToken }),
        });
        if (!exchangeRes.ok) {
          throw new Error('Token exchange failed');
        }
        const dataJson = await exchangeRes.json();
        const { accessToken, user } = dataJson.data;

        if (typeof window !== 'undefined') {
          localStorage.setItem('strat_authenticated', 'true');
          localStorage.setItem('token', accessToken);
          localStorage.setItem('user', JSON.stringify(user));
        }

        set({
          isAuthenticated: true,
          token: accessToken,
          user: user,
          isBrokerConnected: true,
        });
        console.log('[Auth Store] Desktop authentication completed successfully.');
      };

      // 3. Setup Tauri event listener for instant deep link callback
      let unlistenSuccess: (() => void) | undefined;
      const tauriPromise = new Promise<string>((resolve) => {
        import('@tauri-apps/api/event').then(async ({ listen }) => {
          try {
            const unlisten = await listen<{ token: string }>('desktop-login-success', (event) => {
              resolve(event.payload.token);
            });
            unlistenSuccess = unlisten;
          } catch {
            // Not in Tauri environment or error
          }
        });
      });

      // 4. Polling backup (if deep link is not clicked/supported)
      let isCompleted = false;
      const pollPromise = new Promise<string>((resolve, reject) => {
        const interval = setInterval(async () => {
          if (isCompleted) {
            clearInterval(interval);
            return;
          }
          try {
            const statusRes = await fetch(`https://api-web.stratai.live/api/v1/auth/desktop/session/${sessionId}`);
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

        // Auto-cleanup after 5 minutes timeout
        setTimeout(() => {
          if (!isCompleted) {
            isCompleted = true;
            clearInterval(interval);
            reject(new Error('Login timed out.'));
          }
        }, 5 * 60 * 1000);
      });

      try {
        // Wait for first one (deep link callback OR polling completion)
        const finalToken = await Promise.race([tauriPromise, pollPromise]);
        isCompleted = true; // stop poll if deep-link succeeded
        if (unlistenSuccess) unlistenSuccess();
        await exchangeToken(finalToken);
      } catch (err) {
        if (unlistenSuccess) unlistenSuccess();
        throw err;
      }
    },

    logout: () => {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('strat_authenticated');
        localStorage.removeItem('user');
        localStorage.removeItem('token');
      }
      set({
        isAuthenticated: false,
        token: null,
        user: null,
        isBrokerConnected: false,
      });
      console.log('[Auth Store] Successfully logged out.');
    },

    setBrokerConnected: (connected) => {
      set({ isBrokerConnected: connected });
    },

    fetchProfile: async () => {
      const token = useAuthStore.getState().token;
      if (!token) return;

      try {
        const res = await fetch('https://api-web.stratai.live/api/v1/users/me', {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });
        if (res.ok) {
          const resJson = await res.json();
          const user = resJson.data;
          if (typeof window !== 'undefined') {
            localStorage.setItem('user', JSON.stringify(user));
          }
          set({ user });
        } else if (res.status === 401) {
          // Token expired, log out
          useAuthStore.getState().logout();
        }
      } catch (err) {
        console.error('[Auth Store] Fetch user profile failed:', err);
      }
    },

    fetchUserProfile: async () => {
      await useAuthStore.getState().fetchProfile();
    },
  };
});
