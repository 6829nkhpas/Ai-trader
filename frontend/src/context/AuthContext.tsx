'use client';

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import { apiClient, authApi } from '@/lib/api-client';

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
  onLoginSuccess: (
    user: User,
    requiresMfa: boolean,
    accessToken?: string,
    mfaSetupRequired?: boolean
  ) => void;
  mfaSetupRequired: boolean;
  generateMfaSetup: () => Promise<{ qrCodeDataURL: string; manualSecret: string }>;
  /** Submit the 6-digit TOTP code */
  submitMfa: (token: string) => Promise<void>;
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
  const [pendingAccessToken, setPendingAccessToken] = useState<string | null>(null);
  const [mfaSetupRequired, setMfaSetupRequired] = useState(false);

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
          setPendingAccessToken(null);
          setMfaSetupRequired(false);
        }
      } catch {
        if (!cancelled) {
          setUser(null);
          setAuthState('unauthenticated');
          setPendingAccessToken(null);
          setMfaSetupRequired(false);
        }
      }
    }

    hydrateSession();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Called by LoginForm on credential success ───────────────────────────
  const onLoginSuccess = useCallback(
    (
      incomingUser: User,
      requiresMfa: boolean,
      accessToken?: string,
      setupRequired?: boolean
    ) => {
      setUser(incomingUser);
      if (requiresMfa) {
        setAuthState('mfa');
        setPendingAccessToken(accessToken ?? null);
        setMfaSetupRequired(!!setupRequired);
        return;
      }

      setAuthState('authenticated');
      setPendingAccessToken(null);
      setMfaSetupRequired(false);
    },
    []
  );

  // ── MFA setup (generate QR / secret) ─────────────────────────────────
  const generateMfaSetup = useCallback(async () => {
    if (!pendingAccessToken) {
      throw new Error('No pending MFA session. Please log in again.');
    }

    const res = await authApi.mfaSetup(pendingAccessToken);
    return res.data as { qrCodeDataURL: string; manualSecret: string };
  }, [pendingAccessToken]);

  // ── MFA submission ───────────────────────────────────────────────────────
  const submitMfa = useCallback(async (token: string) => {
    if (!pendingAccessToken) {
      throw new Error('No pending MFA session. Please log in again.');
    }

    await authApi.mfaVerify({ token }, pendingAccessToken);
    setAuthState('authenticated');
    setPendingAccessToken(null);
    setMfaSetupRequired(false);
  }, [pendingAccessToken]);

  // ── Logout ───────────────────────────────────────────────────────────────
  const logout = useCallback(async () => {
    try {
      await apiClient.post('/api/auth/logout');
    } finally {
      setUser(null);
      setAuthState('unauthenticated');
      setPendingAccessToken(null);
      setMfaSetupRequired(false);
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
    mfaSetupRequired,
    generateMfaSetup,
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
