'use client';

import React from 'react';
import { useFeature } from '../../store/useFeatureStore';
import type { FeatureId } from '../../lib/featureFlags';
import { FEATURE_LABELS } from '../../lib/featureFlags';
import { IS_PROD } from '../../lib/env';
import { dashboardUrl, openExternalUrl } from '../../lib/redirect';
import { Lock, ArrowRight } from 'lucide-react';

interface FeatureGateProps {
  feature: FeatureId;
  /**
   * Rendered when the feature is accessible (or when running in dev mode,
   * where all features are unlocked).
   */
  children: React.ReactNode;
  /**
   * Optional fallback shown when the feature is locked. If omitted, a
   * default "no subscription" placeholder with an upgrade CTA is rendered.
   */
  fallback?: React.ReactNode;
  /**
   * When true the gate renders without a surrounding wrapper (just the
   * children or fallback). Useful for inline toolbar toggles where the
   * parent already supplies its own container. Default false.
   */
  inline?: boolean;
}

function NoSubscriptionCard({ feature }: { feature: FeatureId }) {
  const label = FEATURE_LABELS[feature];
  return (
    <div className="flex flex-col items-center justify-center gap-3 p-6 text-center rounded-none border border-border-default bg-elevated/60">
      <div className="flex h-9 w-9 items-center justify-center rounded-full border border-border-default bg-surface text-text-muted">
        <Lock size={16} />
      </div>
      <div>
        <p className="text-xs font-bold uppercase tracking-wider text-text-primary">
          {label} requires a subscription
        </p>
        <p className="text-[11px] text-text-secondary mt-1 max-w-[260px]">
          You don&apos;t have an active subscription that includes this feature. Subscribe from the dashboard to unlock it.
        </p>
      </div>
      <button
        onClick={() => openExternalUrl(dashboardUrl())}
        className="flex items-center gap-1.5 rounded-none border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-bold text-emerald-400 hover:bg-emerald-500/20 transition-all active:scale-[0.98]"
      >
        <span>VIEW PLANS</span>
        <ArrowRight size={11} />
      </button>
    </div>
  );
}

export default function FeatureGate({ feature, children, fallback, inline }: FeatureGateProps) {
  const enabled = useFeature(feature);

  if (enabled) return <>{children}</>;

  const content = fallback ?? <NoSubscriptionCard feature={feature} />;
  if (inline) return <>{content}</>;
  return (
    <div className="flex h-full w-full items-center justify-center">
      {content}
    </div>
  );
}

export function isFeatureStrictMode(): boolean {
  return IS_PROD;
}
