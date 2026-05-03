'use client';

import React, { useCallback, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { authApi } from '@/lib/api-client';
import { useAuth } from '@/context/AuthContext';
import type { User } from '@/context/AuthContext';

// Google SVG icon (official brand color)
function GoogleIcon() {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M43.611 20.083H42V20H24v8h11.303C33.654 32.657 29.332 36 24 36c-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"
        fill="#FFC107"
      />
      <path
        d="M6.306 14.691l6.571 4.819C14.655 15.108 19.001 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"
        fill="#FF3D00"
      />
      <path
        d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.313 0-9.822-3.417-11.41-8.112l-6.515 5.019C9.505 39.556 16.227 44 24 44z"
        fill="#4CAF50"
      />
      <path
        d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 01-4.087 5.571l6.19 5.238C39.712 35.463 44 30.138 44 24c0-1.341-.138-2.65-.389-3.917z"
        fill="#1976D2"
      />
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// GoogleAuthButton
// ─────────────────────────────────────────────────────────────────────────────

interface GoogleAuthButtonProps {
  mode?: 'login' | 'signup';
}

export default function GoogleAuthButton({ mode = 'login' }: GoogleAuthButtonProps) {
  const { onLoginSuccess } = useAuth();
  const [isLoading, setIsLoading]   = useState(false);
  const [error, setError]           = useState<string | null>(null);

  /**
   * Initiates the Google OAuth 2.0 PKCE handshake via the backend.
   *
   * Flow:
   *  1. Frontend calls backend  POST /api/auth/oauth/google/init
   *  2. Backend returns a redirect URL with state + nonce params
   *  3. User completes Google consent
   *  4. Google redirects to /api/auth/oauth/google/callback (backend)
   *  5. Backend verifies state/nonce, provisions user, sets HttpOnly cookie
   *  6. Backend redirects browser to /auth/oauth/complete (frontend)
   *  7. Frontend /auth/oauth/complete calls GET /api/auth/session → user hydrated
   *
   * This approach ensures:
   *  ✅ No ID token is ever touched on the frontend
   *  ✅ State/Nonce verified server-side (PKCE + CSRF protection)
   *  ✅ No JWT in localStorage
   */
  const handleGoogleAuth = useCallback(async () => {
    setError(null);
    setIsLoading(true);
    try {
      const res = await authApi.googleOAuth({ idToken: '' });
      const { redirect_url } = res.data as { redirect_url: string };

      // Hard redirect — backend drives the OAuth dance
      if (redirect_url) {
        window.location.href = redirect_url;
        return;
      }

      // If backend completed inline (e.g. ID token exchange), handle user directly
      const { user, mfa_required } = res.data as {
        user: User;
        mfa_required: boolean;
      };
      onLoginSuccess(user, mfa_required);
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { message?: string } } };
      setError(
        axErr?.response?.data?.message ?? 'Google sign-in failed. Try again.'
      );
      setIsLoading(false);
    }
  }, [onLoginSuccess]);

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        id="google-auth-btn"
        onClick={handleGoogleAuth}
        disabled={isLoading}
        aria-label={`${mode === 'signup' ? 'Sign up' : 'Sign in'} with Google`}
        className="google-auth-btn"
      >
        {isLoading ? (
          <Loader2 size={18} className="animate-spin text-white/60" />
        ) : (
          <GoogleIcon />
        )}
        <span>
          {mode === 'signup' ? 'Sign up' : 'Continue'} with Google
        </span>
      </button>

      {error && (
        <p role="alert" className="auth-field-error text-center" aria-live="assertive">
          {error}
        </p>
      )}
    </div>
  );
}
