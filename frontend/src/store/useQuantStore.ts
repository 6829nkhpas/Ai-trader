// useQuantStore.ts — V3 Quant Dashboard Zustand Store.
//
// Manages consensus data, AI execution plan state, and the
// Deep Quant Analysis pipeline trigger.

import { create } from 'zustand';

// ── TypeScript interfaces matching Rust backend structs ─────────────────

export interface ConsensusReport {
  symbol: string;
  trend_score: number;      // -100 to +100
  momentum_state: string;   // "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL"
  volatility_state: string; // "SQUEEZING" | "EXPANDING" | "NORMAL"
  volume_flow_state: string; // "ACCUMULATION" | "DISTRIBUTION" | "NEUTRAL"
  active_patterns: string[];
  active_strategies: string[];
}

export interface AiExecutionPlan {
  conviction_score: number;   // 1–100
  setup_validation: string;
  execution_plan: string;
}

// ── Store Shape ─────────────────────────────────────────────────────────

interface QuantStore {
  consensusData: ConsensusReport | null;
  aiPlan: AiExecutionPlan | null;
  isAnalyzing: boolean;
  analysisError: string | null;

  setConsensusData: (data: ConsensusReport) => void;
  fetchDeepAnalysis: (symbol: string) => Promise<void>;
  clearAiPlan: () => void;
}

// ── Tauri invoke helper ─────────────────────────────────────────────────

async function tauriInvoke<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  // Dynamic import to avoid SSR issues with Tauri APIs
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

// ── Store ───────────────────────────────────────────────────────────────

export const useQuantStore = create<QuantStore>((set) => ({
  consensusData: null,
  aiPlan: null,
  isAnalyzing: false,
  analysisError: null,

  setConsensusData: (data: ConsensusReport) => set({ consensusData: data }),

  fetchDeepAnalysis: async (symbol: string) => {
    set({ isAnalyzing: true, analysisError: null, aiPlan: null });

    try {
      const plan = await tauriInvoke<AiExecutionPlan>(
        'run_deep_quant_analysis',
        { symbol }
      );

      set({ aiPlan: plan, isAnalyzing: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error('[QuantStore] Deep analysis failed:', message);
      set({ isAnalyzing: false, analysisError: message });
    }
  },

  clearAiPlan: () => set({ aiPlan: null, analysisError: null }),
}));
