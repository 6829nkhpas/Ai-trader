import { create } from 'zustand';

interface AuthState {
  isAuthenticated: boolean;
  token: string | null;
  user: any | null;
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
  const storedUser = typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('user') || 'null') : null;

  return {
    isAuthenticated: storedAuth === 'true',
    token: storedAuth === 'true' ? 'strat-ai-session' : null,
    user: storedUser,
    isBrokerConnected: true, // No broker gate needed

    login: async () => {
      const mockUser = {
        id: 'strat-user-1',
        email: 'trader@stratai.com',
        name: 'Strat AI Trader',
        tier: 'PRO',
        brokerConnection: true,
      };

      if (typeof window !== 'undefined') {
        localStorage.setItem('strat_authenticated', 'true');
        localStorage.setItem('user', JSON.stringify(mockUser));
      }

      set({
        isAuthenticated: true,
        token: 'strat-ai-session',
        user: mockUser,
        isBrokerConnected: true,
      });

      console.log('[Auth Store] Logged in via Strat AI.');
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
      // No-op: profile is set during login
    },

    fetchUserProfile: async () => {
      // No-op: profile is set during login
    },
  };
});
