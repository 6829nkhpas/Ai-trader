'use client';

import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { useRouter } from 'next/navigation';
import { Loader2, ShieldCheck } from 'lucide-react';
import { kycApi } from '@/lib/api-client';
import { isOnboardingComplete } from '@/lib/onboarding';

type OnboardingFormState = {
  legalName: string;
  panNumber: string;
  residentialAddress: string;
  aadhaarLast4: string;
};

type OnboardingErrors = {
  legalName?: string;
  panNumber?: string;
  residentialAddress?: string;
  aadhaarLast4?: string;
};

function getErrors(form: OnboardingFormState): OnboardingErrors {
  const errors: OnboardingErrors = {};

  if (!form.legalName.trim()) {
    errors.legalName = 'Legal name is required.';
  }

  if (!form.panNumber.trim()) {
    errors.panNumber = 'PAN number is required.';
  }

  if (!form.residentialAddress.trim()) {
    errors.residentialAddress = 'Residential address is required.';
  }

  if (form.aadhaarLast4 && !/^\d{4}$/.test(form.aadhaarLast4)) {
    errors.aadhaarLast4 = 'Enter the last 4 digits.';
  }

  return errors;
}

export default function OnboardingPage() {
  const router = useRouter();
  const [isChecking, setIsChecking] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [serverErr, setServerErr] = useState<string | null>(null);

  const [form, setForm] = useState<OnboardingFormState>({
    legalName: '',
    panNumber: '',
    residentialAddress: '',
    aadhaarLast4: '',
  });

  useEffect(() => {
    let cancelled = false;

    async function checkStatus() {
      try {
        const complete = await isOnboardingComplete();
        if (cancelled) return;

        if (complete) {
          router.replace('/dashboard');
          return;
        }

        setIsChecking(false);
      } catch (err) {
        if (cancelled) return;

        if (axios.isAxiosError(err) && err.response?.status === 401) {
          router.replace('/auth/login?reason=session_expired');
          return;
        }

        setServerErr('Unable to verify onboarding status. Please try again.');
        setIsChecking(false);
      }
    }

    checkStatus();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const errors = submitted ? getErrors(form) : {};
  const hasErrors = Object.values(errors).some(Boolean);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    setServerErr(null);

    const nextErrors = getErrors(form);
    if (Object.values(nextErrors).some(Boolean)) {
      return;
    }

    setIsSaving(true);
    try {
      await kycApi.upsertProfile({
        legalName: form.legalName.trim(),
        panNumber: form.panNumber.trim().toUpperCase(),
        residentialAddress: form.residentialAddress.trim(),
        aadhaarMetadata: form.aadhaarLast4
          ? { last4: form.aadhaarLast4 }
          : null,
      });

      router.replace('/dashboard');
    } catch {
      setServerErr('Failed to save your profile. Please try again.');
    } finally {
      setIsSaving(false);
    }
  }

  if (isChecking) {
    return (
      <div className="flex flex-col items-center gap-4 py-8">
        <Loader2 size={32} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
        <p className="text-sm" style={{ color: 'var(--auth-muted)' }}>
          Preparing onboarding...
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-1">
        <h1 className="auth-heading">Complete your onboarding</h1>
        <p className="auth-subheading">
          Add your profile details to unlock the trading dashboard.
        </p>
      </div>

      {serverErr && (
        <div role="alert" className="auth-error-banner" aria-live="assertive">
          {serverErr}
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
        <div className="auth-field-group">
          <label htmlFor="onboarding-legal-name" className="auth-label">
            Legal name
          </label>
          <input
            id="onboarding-legal-name"
            type="text"
            autoComplete="name"
            value={form.legalName}
            onChange={(event) => {
              setForm((prev) => ({ ...prev, legalName: event.target.value }));
              setServerErr(null);
            }}
            aria-invalid={!!errors.legalName}
            aria-describedby={errors.legalName ? 'onboarding-legal-name-error' : undefined}
            placeholder="Full name as per PAN"
            className={`auth-input${errors.legalName ? ' auth-input--error' : ''}`}
          />
          {errors.legalName && (
            <p id="onboarding-legal-name-error" role="alert" className="auth-field-error">
              {errors.legalName}
            </p>
          )}
        </div>

        <div className="auth-field-group">
          <label htmlFor="onboarding-pan" className="auth-label">
            PAN number
          </label>
          <input
            id="onboarding-pan"
            type="text"
            maxLength={10}
            value={form.panNumber}
            onChange={(event) => {
              setForm((prev) => ({ ...prev, panNumber: event.target.value }));
              setServerErr(null);
            }}
            aria-invalid={!!errors.panNumber}
            aria-describedby={errors.panNumber ? 'onboarding-pan-error' : undefined}
            placeholder="ABCDE1234F"
            className={`auth-input${errors.panNumber ? ' auth-input--error' : ''}`}
          />
          {errors.panNumber && (
            <p id="onboarding-pan-error" role="alert" className="auth-field-error">
              {errors.panNumber}
            </p>
          )}
        </div>

        <div className="auth-field-group">
          <label htmlFor="onboarding-address" className="auth-label">
            Residential address
          </label>
          <textarea
            id="onboarding-address"
            rows={3}
            value={form.residentialAddress}
            onChange={(event) => {
              setForm((prev) => ({ ...prev, residentialAddress: event.target.value }));
              setServerErr(null);
            }}
            aria-invalid={!!errors.residentialAddress}
            aria-describedby={errors.residentialAddress ? 'onboarding-address-error' : undefined}
            placeholder="Street, city, state, postal code"
            className={`auth-input min-h-24 resize-none${errors.residentialAddress ? ' auth-input--error' : ''}`}
          />
          {errors.residentialAddress && (
            <p id="onboarding-address-error" role="alert" className="auth-field-error">
              {errors.residentialAddress}
            </p>
          )}
        </div>

        <div className="auth-field-group">
          <label htmlFor="onboarding-aadhaar" className="auth-label">
            Aadhaar last 4 digits (optional)
          </label>
          <input
            id="onboarding-aadhaar"
            type="text"
            maxLength={4}
            inputMode="numeric"
            value={form.aadhaarLast4}
            onChange={(event) => {
              setForm((prev) => ({ ...prev, aadhaarLast4: event.target.value }));
              setServerErr(null);
            }}
            aria-invalid={!!errors.aadhaarLast4}
            aria-describedby={errors.aadhaarLast4 ? 'onboarding-aadhaar-error' : undefined}
            placeholder="1234"
            className={`auth-input${errors.aadhaarLast4 ? ' auth-input--error' : ''}`}
          />
          {errors.aadhaarLast4 && (
            <p id="onboarding-aadhaar-error" role="alert" className="auth-field-error">
              {errors.aadhaarLast4}
            </p>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
          Your details are encrypted and stored securely. Completing onboarding is required before you can access the Platform.
        </div>

        <button
          type="submit"
          disabled={isSaving || hasErrors}
          className="auth-btn-primary"
        >
          {isSaving ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              <span>Saving...</span>
            </>
          ) : (
            <>
              <ShieldCheck size={16} />
              <span>Finish onboarding</span>
            </>
          )}
        </button>
      </form>
    </div>
  );
}
