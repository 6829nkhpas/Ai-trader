'use client';

import React, { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { isOnboardingComplete } from '@/lib/onboarding';

export default function DashboardRedirect() {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;

    async function route() {
      try {
        const complete = await isOnboardingComplete();
        if (!cancelled) {
          router.replace(complete ? '/' : '/auth/onboarding');
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
  }, [router]);

  return (
    <div className="flex min-h-[40vh] items-center justify-center gap-3 text-sm text-text-secondary">
      <Loader2 size={18} className="animate-spin" />
      <span>Preparing your dashboard...</span>
    </div>
  );
}
