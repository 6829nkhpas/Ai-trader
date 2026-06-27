'use client';

import React from 'react';
import { Shield, Target, Zap, Rocket, CheckCircle2, RotateCcw } from 'lucide-react';

interface AiPlanShape {
  conviction_score: number;
  setup_validation: string;
  execution_plan: string;
}

interface AiExecutionPlanViewProps {
  aiPlan: AiPlanShape;
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
  deployed,
  hasActivePosition,
  onDeploy,
  onClear,
}: AiExecutionPlanViewProps) {
  const [isDeploying, setIsDeploying] = React.useState(false);

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
      {/* Conviction Score */}
      <div className="px-3 py-3 border-b border-border-default">
        <div className="flex items-center gap-1.5 mb-2">
          <Shield size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            AI Conviction
          </h3>
        </div>

        <div className="flex items-center gap-3">
          {/* Big score */}
          <div className={`relative flex items-baseline gap-0.5 ${convictionColor(aiPlan.conviction_score).text}`}>
            <span className="text-4xl font-black tabular-nums tracking-tighter">
              {aiPlan.conviction_score}
            </span>
            <span className="text-base font-semibold text-text-muted/50">/100</span>
          </div>

          <div className="flex-1 flex flex-col gap-1.5">
            {/* Label badge */}
            <div className={`inline-flex items-center gap-1 self-start rounded-none px-2 py-0.5 text-[9px] font-bold ${convictionColor(aiPlan.conviction_score).text} ${convictionColor(aiPlan.conviction_score).bg}/15 ring-1 ${convictionColor(aiPlan.conviction_score).ring}`}>
              {convictionIcon(aiPlan.conviction_score)}
              {convictionLabel(aiPlan.conviction_score)}
            </div>

            {/* Progress bar */}
            <div className="h-1.5 w-full rounded-none bg-elevated overflow-hidden">
              <div
                className={`h-1.5 rounded-none transition-all duration-1000 ease-out ${convictionColor(aiPlan.conviction_score).bg}`}
                style={{ width: `${aiPlan.conviction_score}%` }}
              />
            </div>
          </div>
        </div>
      </div>

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
        {/* Deploy Strategy Button */}
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
