'use client';

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const DIGIT_COUNT = 6;

// ─────────────────────────────────────────────────────────────────────────────
// MfaChallenge
// ─────────────────────────────────────────────────────────────────────────────

export default function MfaChallenge() {
  const { submitMfa, logout } = useAuth();

  // 6 individual digit slots
  const [digits, setDigits] = useState<string[]>(Array(DIGIT_COUNT).fill(''));
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const inputRefs = useRef<Array<HTMLInputElement | null>>(
    Array(DIGIT_COUNT).fill(null)
  );

  // Focus first input on mount
  useEffect(() => {
    inputRefs.current[0]?.focus();
  }, []);

  // ── Auto-submit when all 6 digits are filled ───────────────────────────
  useEffect(() => {
    if (submitted) return;
    const code = digits.join('');
    if (code.length === DIGIT_COUNT && /^\d{6}$/.test(code)) {
      handleSubmit(code);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits]);

  // ── Submit ────────────────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (code: string) => {
      if (isLoading || submitted) return;
      setError(null);
      setIsLoading(true);
      setSubmitted(true);

      try {
        await submitMfa(code);
        // On success, AuthContext sets authState → 'authenticated'
        // The parent route will redirect to /dashboard
      } catch (err: unknown) {
        const axErr = err as { response?: { data?: { message?: string } } };
        setError(
          axErr?.response?.data?.message ?? 'Invalid code. Please try again.'
        );
        // Reset digits and focus first input on failure
        setDigits(Array(DIGIT_COUNT).fill(''));
        setSubmitted(false);
        setIsLoading(false);
        setTimeout(() => inputRefs.current[0]?.focus(), 50);
      }
    },
    [isLoading, submitted, submitMfa]
  );

  // ── Digit input change ────────────────────────────────────────────────────
  const handleChange = (index: number, value: string) => {
    // Allow pasting full 6-digit code into first cell
    if (value.length > 1) {
      const pasted = value.replace(/\D/g, '').slice(0, DIGIT_COUNT);
      const next = [...digits];
      for (let i = 0; i < pasted.length; i++) {
        next[i] = pasted[i];
      }
      setDigits(next);
      const focusTarget = Math.min(pasted.length, DIGIT_COUNT - 1);
      inputRefs.current[focusTarget]?.focus();
      return;
    }

    const digit = value.replace(/\D/g, '');
    const next = [...digits];
    next[index] = digit;
    setDigits(next);
    setError(null);

    if (digit && index < DIGIT_COUNT - 1) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  // ── Backspace ─────────────────────────────────────────────────────────────
  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace' && !digits[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const isFull = digits.join('').length === DIGIT_COUNT;

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col items-center gap-6" role="region" aria-label="MFA verification">
      {/* Icon */}
      <div className="auth-mfa-icon size-16">
        <ShieldCheck size={32} className="auth-mfa-icon-glyph" />
      </div>

      {/* Heading */}
      <div className="text-center space-y-1">
        <h2 className="text-lg font-bold text-text-primary">Two-Factor Authentication</h2>
        <p className="text-sm text-auth-muted">
          Enter the 6-digit code from your authenticator app.
        </p>
      </div>

      {/* Digit inputs */}
      <div
        className="flex gap-3"
        role="group"
        aria-label="One-time password digits"
      >
        {digits.map((digit, i) => (
          <input
            key={i}
            ref={(el) => { inputRefs.current[i] = el; }}
            id={`mfa-digit-${i + 1}`}
            type="text"
            inputMode="numeric"
            pattern="\d"
            maxLength={6}  /* allow full paste */
            value={digit}
            onChange={(e) => handleChange(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            disabled={isLoading}
            aria-label={`Digit ${i + 1}`}
            className={`mfa-digit-input${error ? ' mfa-digit-input--error' : ''}${digit ? ' mfa-digit-input--filled' : ''
              }`}
          />
        ))}
      </div>

      {/* Error */}
      {error && (
        <p role="alert" className="auth-field-error text-center" aria-live="assertive">
          {error}
        </p>
      )}

      {/* Loading indicator (auto-submit in progress) */}
      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-auth-muted">
          <Loader2 size={14} className="animate-spin" />
          <span>Verifying…</span>
        </div>
      )}

      {/* Manual submit fallback (if auto-submit hasn't fired) */}
      {!isLoading && isFull && !submitted && (
        <button
          type="button"
          id="mfa-submit-btn"
          onClick={() => handleSubmit(digits.join(''))}
          className="auth-btn-primary w-full"
        >
          Verify code
        </button>
      )}

      {/* Cancel / logout */}
      <button
        type="button"
        id="mfa-cancel-btn"
        onClick={logout}
        className="text-xs text-auth-muted hover:text-text-primary transition-colors underline underline-offset-2"
      >
        Cancel and sign out
      </button>
    </div>
  );
}
