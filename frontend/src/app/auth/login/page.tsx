'use client';

import React, { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import LoginForm from '@/components/auth/LoginForm';
import GoogleAuthButton from '@/components/auth/GoogleAuthButton';
import MfaChallenge from '@/components/auth/MfaChallenge';
import { useAuth } from '@/context/AuthContext';
import { resolvePostAuthDestination } from '@/lib/onboarding';

export default function LoginPage() {
  const { authState } = useAuth();
  const router = useRouter();

  // If already authenticated, go to onboarding or dashboard
  useEffect(() => {
    if (authState === 'authenticated') {
      let cancelled = false;

      async function route() {
        try {
          const target = await resolvePostAuthDestination();
          if (!cancelled) {
            router.replace(target);
          }
        } catch {
          if (!cancelled) {
            router.replace('/auth/login?reason=session_expired');
          }
        }
      }

      route();
      return () => {
        cancelled = true;
      };
    }
  }, [authState, router]);

  // ── MFA gate ───────────────────────────────────────────────────────────
  if (authState === 'mfa') {
    return <MfaChallenge />;
  }

  // ── Loading skeleton ───────────────────────────────────────────────────
  if (authState === 'loading' || authState === 'idle') {
    return (
      <div className="flex flex-col gap-5 animate-pulse">
        <div className="h-4 w-32 rounded bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
      </div>
    );
  }

  // ── Login form ─────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-1">
        <h1 className="auth-heading">Welcome back</h1>
        <p className="auth-subheading">Sign in to your trading dashboard.</p>
      </div>

      {/* Google OAuth */}
      <GoogleAuthButton mode="login" />

      {/* Divider */}
      <div className="auth-divider">
        <span className="auth-divider-label">or continue with email</span>
      </div>

      {/* Credential form */}
      <LoginForm />
    </div>
  );
}
