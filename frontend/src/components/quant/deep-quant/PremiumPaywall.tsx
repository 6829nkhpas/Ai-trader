'use client';

import React from 'react';
import { Loader2, Lock, ArrowRight, Cpu, ShieldCheck } from 'lucide-react';
import { motion } from 'framer-motion';
import { dashboardUrl, openExternalUrl } from '../../../lib/redirect';
import SubscriptionIcon from './SubscriptionIcon';
import { staggerContainerSlow, fadeInUp, hoverScale } from '../../../lib/motionVariants';

interface PremiumPaywallProps {
  onUpgradeClick: () => void;
}

export default function PremiumPaywall({ onUpgradeClick }: PremiumPaywallProps) {
  const [upgrading, setUpgrading] = React.useState(false);

  const handleClick = async () => {
    setUpgrading(true);
    try {
      await onUpgradeClick();
    } finally {
      setUpgrading(false);
    }
  };

  const handleViewPlans = () => openExternalUrl(dashboardUrl());

  return (
    <motion.div
      variants={staggerContainerSlow}
      initial="hidden"
      animate="show"
      className="grow flex flex-col items-center justify-center gap-6 p-6 w-full h-full min-h-112.5 max-w-md mx-auto text-center"
    >
      {/* Large SVG Illustration */}
      <motion.div variants={fadeInUp} className="w-72 h-40 flex items-center justify-center shrink-0">
        <SubscriptionIcon className="w-full h-full object-contain" />
      </motion.div>

      {/* Header Info */}
      <motion.div variants={fadeInUp} className="space-y-1.5 max-w-sm">
        <span className="text-[10px] font-black tracking-widest uppercase text-emerald-500 dark:text-emerald-400">
          Strat Ai
        </span>
        <h2 className="text-xl font-extrabold tracking-tight text-text-primary">
          Subscription Access Required
        </h2>
        <p className="text-xs text-text-secondary leading-relaxed">
          You don&apos;t have an active subscription that includes this feature. Subscribe from the dashboard to unlock it.
        </p>
      </motion.div>

      {/* Feature list */}
      {/* Compliance: this is PROMOTIONAL copy for a paid plan, so every label must be
          literally true of the shipped build — docs/compliance/BRAND_GUIDELINES.md §3.
          Previously: "DeepSeek v4 Autonomous ReAct Agent Loop" (a model that does not
          run this loop — the default is openai/gpt-4o, and the model is configurable,
          so naming one here is wrong in every deployment) and "Virtual Execution &
          Paper Broker Sync" ("Broker Sync" implies orders reach a broker; paper
          trading is entirely local). "Autonomous" is the single worst word to sell a
          product on when its compliance position is that it cannot act — see §1.1
          rule 11.

          Also removed this pass: "Local Paper-Trade Simulator — No Broker Orders".
          The simulated paper portfolio has been deleted from the product, so
          advertising it on a paid plan would be selling something that does not
          exist — the same §3 problem as the two labels above. */}
      <motion.div variants={fadeInUp} className="w-full max-w-sm space-y-2 text-left">
        {[
          { icon: Cpu, label: 'Multi-Step AI Research Loop with Live Tool Calls' },
          { icon: ShieldCheck, label: 'Mathematical Risk Manager & Trade Evaluator' },
        ].map(({ icon: Icon, label }) => (
          <div
            key={label}
            className="flex items-center gap-2.5 rounded-none border border-border-default bg-elevated/40 px-3 py-2"
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-none border border-emerald-500/20 bg-emerald-500/10 text-emerald-500 dark:text-emerald-400">
              <Icon size={12} />
            </span>
            <span className="text-xs font-medium text-text-secondary leading-snug">
              {label}
            </span>
          </div>
        ))}
      </motion.div>

      {/* Footer CTAs */}
      <motion.div variants={fadeInUp} className="w-full max-w-sm flex flex-col gap-2 pt-1">
        <motion.button
          type="button"
          disabled={upgrading}
          onClick={handleClick}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.97 }}
          className="w-full flex items-center justify-center gap-2 rounded-none bg-text-primary text-surface hover:bg-text-secondary px-4 py-2.5 text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50 border border-text-primary"
        >
          {upgrading ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              <span>Initiating Checkout...</span>
            </>
          ) : (
            <>
              <span>Upgrade to PRO</span>
              <ArrowRight size={13} />
            </>
          )}
        </motion.button>
        <motion.button
          type="button"
          onClick={handleViewPlans}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.97 }}
          className="w-full flex items-center justify-center gap-1.5 rounded-none border border-border-default bg-elevated hover:bg-elevated hover:text-text-primary px-4 py-2 text-[11px] font-semibold text-text-secondary transition-all"
        >
          <span>VIEW PLANS ON DASHBOARD</span>
          <ArrowRight size={11} />
        </motion.button>
        <span className="mt-1 text-center text-[9px] text-text-muted">
          Secure checkout powered by PhonePe
        </span>
      </motion.div>
    </motion.div>
  );
}
