'use client';

import React, { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import SignupForm from '@/components/auth/SignupForm';
import GoogleAuthButton from '@/components/auth/GoogleAuthButton';
import MfaChallenge from '@/components/auth/MfaChallenge';
import { useAuth } from '@/context/AuthContext';

export default function SignupPage() {
  const { authState } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (authState === 'authenticated') {
      router.replace('/dashboard');
    }
  }, [authState, router]);

  if (authState === 'mfa') {
    return <MfaChallenge />;
  }

  if (authState === 'loading' || authState === 'idle') {
    return (
      <div className="flex flex-col gap-5 animate-pulse">
        <div className="h-4 w-40 rounded bg-white/10" />
        <div className="h-10 rounded-xl bg-white/10" />
        <div className="h-10 rounded-xl bg-white/10" />
        <div className="h-10 rounded-xl bg-white/10" />
        <div className="h-10 rounded-xl bg-white/10" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-1">
        <h1 className="auth-heading">Create your account</h1>
        <p className="auth-subheading">
          Join Trivx — AI-powered trading, secured by design.
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
