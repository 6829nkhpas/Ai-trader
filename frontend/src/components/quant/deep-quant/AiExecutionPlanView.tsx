'use client';

import React from 'react';
import { Shield, Target, Zap, Rocket, CheckCircle2, RotateCcw, Lock } from 'lucide-react';
import { ResearchGate } from '../../common/FeatureGate';

interface AiPlanShape {
  // May be undefined when the committed decision emitted no conviction — the
  // view renders "—" rather than a fabricated number (R1.7).
  conviction_score: number | undefined;
  setup_validation: string;
  execution_plan: string;
}

interface AiExecutionPlanViewProps {
  aiPlan: AiPlanShape;
  // Whether the committed decision is a validated directional trade. When
  // false (HOLD / stand_aside / missing levels) the deploy action is NOT
  // offered — no APPROVE & EXECUTE control renders (R1.3/R1.6).
  actionable: boolean;
  deployed: boolean;
  hasActivePosition: boolean;
  onDeploy: () => Promise<void>;
  onClear: () => void;
}

function convictionColor(score: number) {
  if (score >= 80) return { text: 'text-text-primary', bg: 'bg-text-primary', ring: 'ring-border-default/40', glow: '' };
  if (score >= 60) return { text: 'text-text-secondary', bg: 'bg-text-secondary', ring: 'ring-border-default/20', glow: '' };
  if (score >= 40) return { text: 'text-text-secondary/80', bg: 'bg-text-muted', ring: 'ring-border-default/20', glow: '' };
  return { text: 'text-text-muted', bg: 'bg-text-muted', ring: 'ring-border-default/25', glow: '' };
}

function convictionLabel(score: number) {
  if (score >= 80) return 'HIGH CONVICTION';
  if (score >= 60) return 'MODERATE';
  if (score >= 40) return 'LOW CONVICTION';
  return 'VERY WEAK';
}

function convictionIcon(score: number) {
  if (score >= 60) return <CheckCircle2 size={14} className="text-text-primary" />;
  return <Shield size={14} className="text-text-secondary" />;
}

export default function AiExecutionPlanView({
  aiPlan,
  actionable,
  deployed,
  hasActivePosition,
  onDeploy,
  onClear,
}: AiExecutionPlanViewProps) {
  const [isDeploying, setIsDeploying] = React.useState(false);
  // Conviction may be absent (R1.7). Use 0 for color/label/bar math and render
  // "—" for the numeric readout rather than a fabricated value.
  const score = aiPlan.conviction_score ?? 0;
  const scoreLabel = aiPlan.conviction_score ?? '—';

  const handleDeployClick = async () => {
    setIsDeploying(true);
    try {
      await onDeploy();
    } finally {
      setIsDeploying(false);
    }
  };

  return (
    <div className="flex flex-col gap-0">
      {/* Conviction Score — RESEARCH-gated (compliance blocker P1). The score is
          a directional-quality signal on a recommendation, so it is regulated
          research output rather than neutral analytics. */}
      <ResearchGate
        capability="convictionScore"
        inline
        fallback={
          <div className="px-3 py-3 border-b border-border-default">
            <div className="flex items-center gap-1.5 mb-1">
              <Lock size={11} className="text-text-muted" />
              <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
                AI Conviction
              </h3>
            </div>
            <p className="text-[11px] text-text-secondary">
              Available on the Research plan.
            </p>
          </div>
        }
      >
      <div className="px-3 py-3 border-b border-border-default">
        <div className="flex items-center gap-1.5 mb-2">
          <Shield size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            AI Conviction
          </h3>
        </div>

        <div className="flex items-center gap-3">
          {/* Big score */}
          <div className={`relative flex items-baseline gap-0.5 ${convictionColor(score).text}`}>
            <span className="text-4xl font-black tabular-nums tracking-tighter">
              {scoreLabel}
            </span>
            <span className="text-base font-semibold text-text-muted/50">/100</span>
          </div>

          <div className="flex-1 flex flex-col gap-1.5">
            {/* Label badge */}
            <div className={`inline-flex items-center gap-1 self-start rounded-none px-2 py-0.5 text-[9px] font-bold ${convictionColor(score).text} ${convictionColor(score).bg}/15 ring-1 ${convictionColor(score).ring}`}>
              {convictionIcon(score)}
              {convictionLabel(score)}
            </div>

            {/* Progress bar */}
            <div className="h-1.5 w-full rounded-none bg-elevated overflow-hidden">
              <div
                className={`h-1.5 rounded-none transition-all duration-1000 ease-out ${convictionColor(score).bg}`}
                style={{ width: `${score}%` }}
              />
            </div>
          </div>
        </div>
      </div>
      </ResearchGate>

      {/* Setup Validation */}
      <div className="px-3 py-2.5 border-b border-border-default">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Target size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Setup Validation
          </h3>
        </div>
        <p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">
          {aiPlan.setup_validation}
        </p>
      </div>

      {/* Execution Plan */}
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Zap size={11} className="text-text-primary" />
          <h3 className="text-[10px] font-semibold text-text-primary uppercase tracking-wider">
            Execution Plan
          </h3>
        </div>
        <div className="rounded-none border border-border-default bg-elevated/40 px-3 py-2.5">
          <p className="text-[11px] leading-relaxed text-text-secondary font-medium whitespace-pre-line">
            {aiPlan.execution_plan}
          </p>
        </div>
      </div>

      {/* Clear & Deploy actions */}
      <div className="px-3 py-2 flex flex-col gap-1.5">
        {/* Deploy Strategy Button — offered ONLY for a validated directional
            trade. A HOLD / stand_aside or a plan without structured
            execution_levels is non-actionable, so no deploy control renders
            (R1.3/R1.6). */}
        {actionable && (
          <button
            id="btn-deploy-strategy"
            type="button"
            disabled={deployed || hasActivePosition || isDeploying}
            onClick={handleDeployClick}
            className={`
              group relative w-full flex h-8 items-center justify-center gap-2
              rounded-none px-4 text-[10px] font-bold uppercase tracking-wider
              transition-all duration-300 ease-out border
              ${deployed || hasActivePosition
                ? 'bg-elevated text-text-muted border-border-default cursor-default'
                : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.98]'
              }
            `}
          >
            <span className="relative flex items-center gap-2">
              {deployed || hasActivePosition ? (
                <>
                  <CheckCircle2 size={14} />
                  STRATEGY DEPLOYED
                </>
              ) : (
                <>
                  <Rocket size={14} className={isDeploying ? 'animate-pulse' : 'group-hover:animate-bounce'} />
                  {isDeploying ? 'DEPLOYING...' : 'DEPLOY SIMULATED STRATEGY'}
                </>
              )}
            </span>
          </button>
        )}

        <button
          type="button"
          onClick={onClear}
          className="w-full flex h-8 items-center justify-center gap-1.5 rounded-none px-3 text-[10px] font-bold uppercase tracking-wider text-text-primary bg-elevated border border-border-default hover:bg-zinc-800 transition-colors"
        >
          <RotateCcw size={10} />
          Clear & Reset
        </button>
      </div>
    </div>
  );
}
