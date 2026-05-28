import { create } from 'zustand';

interface AuthState {
  isAuthenticated: boolean;
  token: string | null;
  user: any | null;
  isBrokerConnected: boolean;
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  signup: (email: string, password: string, name?: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => void;
  setBrokerConnected: (connected: boolean) => void;
  fetchProfile: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => {
  // Safe initial values retrieval from local storage (safe for browser env check)
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const user = typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('user') || 'null') : null;

  return {
    isAuthenticated: !!token,
    token,
    user,
    isBrokerConnected: !!user?.brokerConnection,

    login: async (email, password) => {
      try {
        console.log('[Auth Store] Attempting login to http://localhost:3001...');
        const response = await fetch('http://localhost:3001/api/auth/login', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ email, password }),
        });

        const data = await response.json();

        if (!response.ok) {
          return { success: false, error: data.error || 'Failed to login' };
        }

        if (typeof window !== 'undefined') {
          localStorage.setItem('token', data.token);
          localStorage.setItem('user', JSON.stringify(data.user));
        }

        set({
          isAuthenticated: true,
          token: data.token,
          user: data.user,
          isBrokerConnected: !!data.user?.brokerConnection
        });

        console.log('[Auth Store] Login successful, credentials updated.');
        return { success: true };
      } catch (err: any) {
        console.error('[Auth Store] Login process failed:', err);
        return { success: false, error: err.message || 'Auth service is currently unreachable' };
      }
    },

    signup: async (email, password, name) => {
      try {
        console.log('[Auth Store] Attempting signup to http://localhost:3001...');
        const response = await fetch('http://localhost:3001/api/auth/signup', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ email, password, name, tier: 'FREE' }),
        });

        const data = await response.json();

        if (!response.ok) {
          return { success: false, error: data.error || 'Failed to sign up' };
        }

        if (typeof window !== 'undefined') {
          localStorage.setItem('token', data.token);
          localStorage.setItem('user', JSON.stringify(data.user));
        }

        set({
          isAuthenticated: true,
          token: data.token,
          user: data.user,
          isBrokerConnected: !!data.user?.brokerConnection
        });

        console.log('[Auth Store] Signup successful, credentials updated.');
        return { success: true };
      } catch (err: any) {
        console.error('[Auth Store] Signup process failed:', err);
        return { success: false, error: err.message || 'Auth service is currently unreachable' };
      }
    },

    logout: () => {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
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
      console.log(`[Auth Store] Broker connection status updated: ${connected}`);
    },

    fetchProfile: async () => {
      const token = useAuthStore.getState().token;
      if (!token) return;
      try {
        const response = await fetch('http://localhost:3001/api/auth/me', {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${token}`
          }
        });
        if (response.ok) {
          const data = await response.json();
          set({ 
            user: data.profile,
            isBrokerConnected: !!data.profile?.brokerConnection
          });
          if (typeof window !== 'undefined') {
            localStorage.setItem('user', JSON.stringify(data.profile));
          }
        }
      } catch (err) {
        console.error('[Auth Store] Failed to fetch profile:', err);
      }
    },
  };
});
