import React, { Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import OAuthCompleteInner from './OAuthCompleteInner';

/**
 * /auth/oauth/complete — Page shell (Server Component)
 *
 * Wraps the client-side OAuth hydration logic in <Suspense> so Next.js can
 * statically prerender this shell, then stream in the client component.
 * Required because OAuthCompleteInner calls useSearchParams().
 */
export default function OAuthCompletePage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col items-center gap-4 py-8">
          <Loader2 size={32} className="animate-spin" style={{ color: 'var(--color-primary)' }} />
          <p className="text-sm" style={{ color: 'var(--auth-muted)' }}>
            Completing sign-in…
          </p>
        </div>
      }
    >
      <OAuthCompleteInner />
    </Suspense>
  );
}

