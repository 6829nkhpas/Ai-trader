'use client';

import React, { useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import SignupForm from '@/components/auth/SignupForm';
import GoogleAuthButton from '@/components/auth/GoogleAuthButton';
import MfaChallenge from '@/components/auth/MfaChallenge';
import { useAuth } from '@/context/AuthContext';
import { resolveAuthRedirect } from '@/lib/auth-redirect';

export default function SignupPage() {
  const { authState } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = resolveAuthRedirect(searchParams);

  useEffect(() => {
    if (authState === 'authenticated') {
      router.replace(redirectTo);
    }
  }, [authState, router, redirectTo]);

  if (authState === 'mfa') {
    return <MfaChallenge />;
  }

  if (authState === 'loading' || authState === 'idle') {
    return (
      <div className="flex flex-col gap-5 animate-pulse">
        <div className="h-4 w-40 rounded bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
        <div className="h-10 rounded-xl bg-slate-200/70" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-1">
        <h1 className="auth-heading">Create your account</h1>
        <p className="auth-subheading">
          Join AI Trader and unlock real-time market insights.
        </p>
      </div>

      {/* Google OAuth */}
      <GoogleAuthButton mode="signup" />

      {/* Divider */}
      <div className="auth-divider">
        <span className="auth-divider-label">or register with email</span>
      </div>

      {/* Registration form */}
      <SignupForm />
    </div>
  );
}
