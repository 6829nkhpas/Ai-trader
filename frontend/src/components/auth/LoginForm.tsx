'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Eye, EyeOff, Loader2, ShieldCheck } from 'lucide-react';
import { authApi } from '@/lib/api-client';
import { useAuth } from '@/context/AuthContext';
import type { User } from '@/context/AuthContext';
import { resolveAuthRedirect } from '@/lib/auth-redirect';

// ─────────────────────────────────────────────────────────────────────────────
// Validation
// ─────────────────────────────────────────────────────────────────────────────

function validateEmail(email: string): string | null {
  if (!email) return 'Email is required.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return 'Enter a valid email address.';
  return null;
}

function validatePassword(password: string): string | null {
  if (!password) return 'Password is required.';
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = resolveAuthRedirect(searchParams);
  const { onLoginSuccess } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);

  const [emailErr, setEmailErr] = useState<string | null>(null);
  const [passwordErr, setPassErr] = useState<string | null>(null);
  const [serverErr, setServerErr] = useState<string | null>(null);

  const [isLoading, setIsLoading] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  // ── Field blur validation ─────────────────────────────────────────────────
  const handleEmailBlur = () => setEmailErr(validateEmail(email));
  const handlePassBlur = () => setPassErr(validatePassword(password));

  // ── Submit ────────────────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setServerErr(null);

      const eErr = validateEmail(email);
      const pErr = validatePassword(password);
      setEmailErr(eErr);
      setPassErr(pErr);
      if (eErr || pErr) return;

      setIsLoading(true);
      try {
        const res = await authApi.login({ email, password });
        const { user, mfa_required } = res.data as {
          user: User;
          mfa_required: boolean;
        };

        onLoginSuccess(user, mfa_required);

        if (!mfa_required) {
          router.replace(redirectTo);
        }
        // If MFA is required, the AuthContext will set authState = 'mfa'
        // and the parent page will swap to <MfaChallenge />
      } catch (err: unknown) {
        const axErr = err as { response?: { data?: { message?: string; error?: string } } };
        setServerErr(
          axErr?.response?.data?.error ??
          axErr?.response?.data?.message ??
          'Login failed. Check your credentials.'
        );
      } finally {
        setIsLoading(false);
      }
    },
    [email, password, onLoginSuccess, router, redirectTo]
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <form
      id="login-form"
      onSubmit={handleSubmit}
      noValidate
      className="flex flex-col gap-5"
    >
      {/* ── Server error banner ─────────────────────────────────────────── */}
      {serverErr && (
        <div
          role="alert"
          className="auth-error-banner"
          aria-live="assertive"
        >
          {serverErr}
        </div>
      )}

      {/* ── Email ────────────────────────────────────────────────────────── */}
      <div className="auth-field-group">
        <label htmlFor="login-email" className="auth-label">
          Email address
        </label>
        <input
          ref={emailRef}
          id="login-email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => { setEmail(e.target.value); setEmailErr(null); }}
          onBlur={handleEmailBlur}
          placeholder="you@example.com"
          aria-invalid={!!emailErr}
          aria-describedby={emailErr ? 'login-email-error' : undefined}
          className={`auth-input${emailErr ? ' auth-input--error' : ''}`}
        />
        {emailErr && (
          <p id="login-email-error" role="alert" className="auth-field-error">
            {emailErr}
          </p>
        )}
      </div>

      {/* ── Password ─────────────────────────────────────────────────────── */}
      <div className="auth-field-group">
        <div className="flex items-center justify-between">
          <label htmlFor="login-password" className="auth-label">
            Password
          </label>
          <Link href="/auth/forgot-password" className="auth-link text-xs">
            Forgot password?
          </Link>
        </div>
        <div className="relative">
          <input
            id="login-password"
            type={showPass ? 'text' : 'password'}
            autoComplete="current-password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); setPassErr(null); }}
            onBlur={handlePassBlur}
            placeholder="••••••••••••"
            aria-invalid={!!passwordErr}
            aria-describedby={passwordErr ? 'login-password-error' : undefined}
            className={`auth-input pr-10${passwordErr ? ' auth-input--error' : ''}`}
          />
          <button
            type="button"
            id="login-toggle-password"
            aria-label={showPass ? 'Hide password' : 'Show password'}
            onClick={() => setShowPass((v) => !v)}
            className="auth-eye-btn"
            tabIndex={-1}
          >
            {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        {passwordErr && (
          <p id="login-password-error" role="alert" className="auth-field-error">
            {passwordErr}
          </p>
        )}
      </div>

      {/* ── Submit ───────────────────────────────────────────────────────── */}
      <button
        type="submit"
        id="login-submit-btn"
        disabled={isLoading}
        className="auth-btn-primary"
      >
        {isLoading ? (
          <>
            <Loader2 size={16} className="animate-spin" />
            <span>Authenticating…</span>
          </>
        ) : (
          <>
            <ShieldCheck size={16} />
            <span>Sign in</span>
          </>
        )}
      </button>

      {/* ── Sign up link ─────────────────────────────────────────────────── */}
      <p className="text-center text-sm text-auth-muted">
        No account?{' '}
        <Link href="/auth/signup" className="auth-link font-semibold">
          Create one free
        </Link>
      </p>
    </form>
  );
}
