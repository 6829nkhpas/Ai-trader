'use client';

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import { apiClient } from '@/lib/api-client';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
  mfaEnabled: boolean;
  kycStatus: 'PENDING' | 'VERIFIED' | 'REJECTED';
  subscriptionStatus: 'ACTIVE' | 'INACTIVE' | null;
  createdAt: string;
}

export type AuthState =
  | 'idle'       // initial, before first session check
  | 'loading'    // fetching /api/auth/session
  | 'mfa'        // authenticated but awaiting MFA challenge
  | 'authenticated'
  | 'unauthenticated';

interface AuthContextValue {
  user: User | null;
  authState: AuthState;
  isAuthenticated: boolean;
  isLoading: boolean;
  /** Call after successful credential login — backend sets HttpOnly cookie */
  onLoginSuccess: (user: User, requiresMfa: boolean) => void;
  /** Submit the 6-digit TOTP code */
  submitMfa: (code: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Force a session refresh (called by api-client interceptor on 401) */
  refreshSession: () => Promise<boolean>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Context
// ─────────────────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

// ─────────────────────────────────────────────────────────────────────────────
// SessionProvider
// ─────────────────────────────────────────────────────────────────────────────

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [authState, setAuthState] = useState<AuthState>('idle');

  // ── Initial session hydration ────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    async function hydrateSession() {
      setAuthState('loading');
      try {
        const res = await apiClient.get<{ user: User }>('/api/auth/session');
        if (!cancelled) {
          setUser(res.data.user);
          setAuthState('authenticated');
        }
      } catch {
        if (!cancelled) {
          setUser(null);
          setAuthState('unauthenticated');
        }
      }
    }

    hydrateSession();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Called by LoginForm on credential success ───────────────────────────
  const onLoginSuccess = useCallback((incomingUser: User, requiresMfa: boolean) => {
    setUser(incomingUser);
    setAuthState(requiresMfa ? 'mfa' : 'authenticated');
  }, []);

  // ── MFA submission ───────────────────────────────────────────────────────
  const submitMfa = useCallback(async (code: string) => {
    const res = await apiClient.post<{ user: User }>('/api/auth/mfa/verify', { code });
    setUser(res.data.user);
    setAuthState('authenticated');
  }, []);

  // ── Logout ───────────────────────────────────────────────────────────────
  const logout = useCallback(async () => {
    try {
      await apiClient.post('/api/auth/logout');
    } finally {
      setUser(null);
      setAuthState('unauthenticated');
    }
  }, []);

  // ── Token refresh (called by interceptor on 401) ─────────────────────────
  const refreshSession = useCallback(async (): Promise<boolean> => {
    try {
      const res = await apiClient.post<{ user: User }>('/api/auth/refresh');
      setUser(res.data.user);
      setAuthState('authenticated');
      return true;
    } catch {
      setUser(null);
      setAuthState('unauthenticated');
      return false;
    }
  }, []);

  const value: AuthContextValue = {
    user,
    authState,
    isAuthenticated: authState === 'authenticated',
    isLoading: authState === 'idle' || authState === 'loading',
    onLoginSuccess,
    submitMfa,
    logout,
    refreshSession,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ─────────────────────────────────────────────────────────────────────────────
// useAuth hook
// ─────────────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within a <SessionProvider>');
  }
  return ctx;
}
