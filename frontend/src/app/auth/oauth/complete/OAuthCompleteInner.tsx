'use client';

import React, { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Loader2, XCircle } from 'lucide-react';
import { apiClient } from '@/lib/api-client';
import { useAuth } from '@/context/AuthContext';
import type { User } from '@/context/AuthContext';
import { resolvePostAuthDestination } from '@/lib/onboarding';

/**
 * OAuthCompleteInner — Client Component
 *
 * Handles the post-Google-OAuth session hydration.
 * Must be a separate file so the parent page.tsx can wrap it in <Suspense>,
 * satisfying Next.js App Router's static prerendering requirement for
 * useSearchParams().
 */
export default function OAuthCompleteInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { onLoginSuccess } = useAuth();

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const oauthError = searchParams.get('error');
    if (oauthError) {
      setError(
        oauthError === 'state_mismatch'
          ? 'OAuth session mismatch detected. Please try again.'
          : 'Google sign-in failed. Please try again.'
      );
      return;
    }

    async function hydrateAndRedirect() {
      try {
        const res = await apiClient.get<{ user: User; mfa_required?: boolean }>(
          '/api/auth/session'
        );
        const { user, mfa_required = false } = res.data;
        onLoginSuccess(user, mfa_required);
        if (mfa_required) {
          router.replace('/auth/login');
          return;
        }

        const target = await resolvePostAuthDestination();
        router.replace(target);
      } catch {
        setError('Could not verify your session. Please sign in again.');
      }
    }

    hydrateAndRedirect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="flex flex-col items-center gap-4 py-4 text-center">
        <XCircle size={40} className="text-red-400" />
        <p className="text-sm" style={{ color: 'var(--auth-muted)' }}>{error}</p>
        <a href="/auth/login" className="auth-link text-sm font-semibold">
          Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-4 py-8">
      <Loader2 size={32} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
      <p className="text-sm" style={{ color: 'var(--auth-muted)' }}>
        Completing sign-in…
      </p>
    </div>
  );
}
