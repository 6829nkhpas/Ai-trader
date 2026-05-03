'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  XCircle,
} from 'lucide-react';
import { authApi } from '@/lib/api-client';
import { useAuth } from '@/context/AuthContext';
import type { User } from '@/context/AuthContext';

// ─────────────────────────────────────────────────────────────────────────────
// Password-complexity rules (mirrors backend Argon2id config)
// ─────────────────────────────────────────────────────────────────────────────

interface PasswordRule {
  id: string;
  label: string;
  test: (pw: string) => boolean;
}

const PASSWORD_RULES: PasswordRule[] = [
  { id: 'len',     label: 'At least 12 characters',        test: (p) => p.length >= 12       },
  { id: 'upper',   label: 'One uppercase letter (A–Z)',     test: (p) => /[A-Z]/.test(p)      },
  { id: 'lower',   label: 'One lowercase letter (a–z)',     test: (p) => /[a-z]/.test(p)      },
  { id: 'digit',   label: 'One digit (0–9)',                test: (p) => /\d/.test(p)         },
  { id: 'special', label: 'One special character (!@#…)',   test: (p) => /[^A-Za-z0-9]/.test(p) },
];

function validateEmail(email: string): string | null {
  if (!email) return 'Email is required.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    return 'Enter a valid email address.';
  return null;
}

function validatePassword(password: string): string | null {
  const failing = PASSWORD_RULES.filter((r) => !r.test(password));
  if (failing.length > 0) return 'Password does not meet all requirements.';
  if (password.length > 128) return 'Password must be 128 characters or fewer.';
  return null;
}

function validateConfirm(password: string, confirm: string): string | null {
  if (!confirm) return 'Please confirm your password.';
  if (password !== confirm) return 'Passwords do not match.';
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Password strength meter
// ─────────────────────────────────────────────────────────────────────────────

function PasswordStrengthMeter({ password }: { password: string }) {
  if (!password) return null;
  const passed = PASSWORD_RULES.filter((r) => r.test(password)).length;
  const pct    = Math.round((passed / PASSWORD_RULES.length) * 100);
  const color  =
    pct <= 20 ? '#ef4444'
    : pct <= 60 ? '#f97316'
    : pct <= 80 ? '#eab308'
    : '#22c55e';

  return (
    <div className="mt-2 space-y-2">
      {/* Bar */}
      <div className="h-1 w-full rounded-full bg-white/10 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      {/* Rule checklist */}
      <ul className="grid grid-cols-1 gap-0.5" aria-label="Password requirements">
        {PASSWORD_RULES.map((rule) => {
          const ok = rule.test(password);
          return (
            <li key={rule.id} className="flex items-center gap-2 text-xs">
              {ok ? (
                <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />
              ) : (
                <XCircle size={12} className="text-white/25 shrink-0" />
              )}
              <span className={ok ? 'text-emerald-400' : 'text-auth-muted'}>
                {rule.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default function SignupForm() {
  const router = useRouter();
  const { onLoginSuccess } = useAuth();

  const [email, setEmail]         = useState('');
  const [displayName, setName]    = useState('');
  const [password, setPassword]   = useState('');
  const [confirm, setConfirm]     = useState('');
  const [showPass, setShowPass]   = useState(false);
  const [showConf, setShowConf]   = useState(false);

  const [emailErr, setEmailErr]   = useState<string | null>(null);
  const [passErr, setPassErr]     = useState<string | null>(null);
  const [confErr, setConfErr]     = useState<string | null>(null);
  const [serverErr, setServerErr] = useState<string | null>(null);

  const [isLoading, setIsLoading] = useState(false);
  const emailRef                  = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  // ── Field blur validation ─────────────────────────────────────────────────
  const handleEmailBlur   = () => setEmailErr(validateEmail(email));
  const handlePassBlur    = () => setPassErr(validatePassword(password));
  const handleConfirmBlur = () => setConfErr(validateConfirm(password, confirm));

  // ── Submit ────────────────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setServerErr(null);

      const eErr = validateEmail(email);
      const pErr = validatePassword(password);
      const cErr = validateConfirm(password, confirm);
      setEmailErr(eErr);
      setPassErr(pErr);
      setConfErr(cErr);
      if (eErr || pErr || cErr) return;

      setIsLoading(true);
      try {
        const res = await authApi.signup({
          email,
          password,
          displayName: displayName.trim() || undefined,
        });
        const { user, mfa_required } = res.data as {
          user: User;
          mfa_required: boolean;
        };

        onLoginSuccess(user, mfa_required);

        if (!mfa_required) {
          router.push('/dashboard');
        }
      } catch (err: unknown) {
        const axErr = err as { response?: { data?: { message?: string } } };
        setServerErr(
          axErr?.response?.data?.message ??
            'Registration failed. Please try again.'
        );
      } finally {
        setIsLoading(false);
      }
    },
    [email, password, confirm, displayName, onLoginSuccess, router]
  );

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <form
      id="signup-form"
      onSubmit={handleSubmit}
      noValidate
      className="flex flex-col gap-5"
    >
      {serverErr && (
        <div role="alert" className="auth-error-banner" aria-live="assertive">
          {serverErr}
        </div>
      )}

      {/* ── Display name ─────────────────────────────────────────────────── */}
      <div className="auth-field-group">
        <label htmlFor="signup-name" className="auth-label">
          Display name <span className="text-auth-muted">(optional)</span>
        </label>
        <input
          id="signup-name"
          type="text"
          autoComplete="name"
          value={displayName}
          onChange={(e) => setName(e.target.value)}
          placeholder="Yash"
          className="auth-input"
        />
      </div>

      {/* ── Email ────────────────────────────────────────────────────────── */}
      <div className="auth-field-group">
        <label htmlFor="signup-email" className="auth-label">
          Email address
        </label>
        <input
          ref={emailRef}
          id="signup-email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => { setEmail(e.target.value); setEmailErr(null); }}
          onBlur={handleEmailBlur}
          placeholder="you@example.com"
          aria-invalid={!!emailErr}
          aria-describedby={emailErr ? 'signup-email-error' : undefined}
          className={`auth-input${emailErr ? ' auth-input--error' : ''}`}
        />
        {emailErr && (
          <p id="signup-email-error" role="alert" className="auth-field-error">
            {emailErr}
          </p>
        )}
      </div>

      {/* ── Password + real-time meter ───────────────────────────────────── */}
      <div className="auth-field-group">
        <label htmlFor="signup-password" className="auth-label">
          Password
        </label>
        <div className="relative">
          <input
            id="signup-password"
            type={showPass ? 'text' : 'password'}
            autoComplete="new-password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); setPassErr(null); }}
            onBlur={handlePassBlur}
            placeholder="Min. 12 characters"
            aria-invalid={!!passErr}
            aria-describedby="signup-password-rules"
            className={`auth-input pr-10${passErr ? ' auth-input--error' : ''}`}
          />
          <button
            type="button"
            id="signup-toggle-password"
            aria-label={showPass ? 'Hide password' : 'Show password'}
            onClick={() => setShowPass((v) => !v)}
            className="auth-eye-btn"
            tabIndex={-1}
          >
            {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        <div id="signup-password-rules">
          <PasswordStrengthMeter password={password} />
        </div>
        {passErr && (
          <p id="signup-password-error" role="alert" className="auth-field-error">
            {passErr}
          </p>
        )}
      </div>

      {/* ── Confirm password ─────────────────────────────────────────────── */}
      <div className="auth-field-group">
        <label htmlFor="signup-confirm" className="auth-label">
          Confirm password
        </label>
        <div className="relative">
          <input
            id="signup-confirm"
            type={showConf ? 'text' : 'password'}
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => { setConfirm(e.target.value); setConfErr(null); }}
            onBlur={handleConfirmBlur}
            placeholder="Repeat your password"
            aria-invalid={!!confErr}
            aria-describedby={confErr ? 'signup-confirm-error' : undefined}
            className={`auth-input pr-10${confErr ? ' auth-input--error' : ''}`}
          />
          <button
            type="button"
            id="signup-toggle-confirm"
            aria-label={showConf ? 'Hide password' : 'Show password'}
            onClick={() => setShowConf((v) => !v)}
            className="auth-eye-btn"
            tabIndex={-1}
          >
            {showConf ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        {confErr && (
          <p id="signup-confirm-error" role="alert" className="auth-field-error">
            {confErr}
          </p>
        )}
      </div>

      {/* ── Submit ───────────────────────────────────────────────────────── */}
      <button
        type="submit"
        id="signup-submit-btn"
        disabled={isLoading}
        className="auth-btn-primary"
      >
        {isLoading ? (
          <>
            <Loader2 size={16} className="animate-spin" />
            <span>Creating account…</span>
          </>
        ) : (
          <span>Create account</span>
        )}
      </button>

      {/* ── Login link ───────────────────────────────────────────────────── */}
      <p className="text-center text-sm text-auth-muted">
        Already have an account?{' '}
        <Link href="/auth/login" className="auth-link font-semibold">
          Sign in
        </Link>
      </p>
    </form>
  );
}
