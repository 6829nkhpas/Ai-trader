// useQuantStore.ts — V3 Quant Dashboard Zustand Store.
//
// Manages consensus data, AI execution plan state, simulated positions,
// and the Deep Quant Analysis pipeline trigger.

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
  sentiment?: {
    score: number;           // -100 to +100
    label: string;           // "Bullish", "Bearish", "Neutral"
    top_headline: string;
    impact: 'positive' | 'negative' | 'neutral';
  };
}

export interface AiExecutionPlan {
  conviction_score: number;   // 1–100
  setup_validation: string;
  execution_plan: string;
}

// ── Decoupled Sentiment Payload (independent of Kafka/WS ticks) ─────────

export interface SentimentPayload {
  symbol: string;
  score: number;           // -100 to +100
  label: string;           // "Bullish", "Bearish", "Neutral"
  top_headline: string;
  impact: 'positive' | 'negative' | 'neutral';
  headlines: string[];     // All fetched headlines for individual display
}

// ── Paper Trading Position ──────────────────────────────────────────────

export interface Position {
  id: string;
  symbol: string;
  entry_price: number;
  size: number;
  type: 'LONG' | 'SHORT';
  stop_loss: number;
  take_profit: number;
  timestamp: number;
}

export interface CompletedTrade {
  id: string;
  symbol: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  type: 'LONG' | 'SHORT';
  size: number;
  timestamp: number;
  closed_at: number;
}

// ── Store Shape ─────────────────────────────────────────────────────────

interface QuantStore {
  consensusData: ConsensusReport | null;
  /** Per-symbol consensus cache — retains results from previous Deep Quant runs */
  consensusCache: Record<string, ConsensusReport>;
  aiPlan: AiExecutionPlan | null;
  isAnalyzing: boolean;
  analysisError: string | null;
  activePositions: Position[];
  completedTrades: CompletedTrade[];

  // ── Decoupled Sentiment (independent of tick data) ──────────────────
  activeSentiment: SentimentPayload | null;
  isFetchingSentiment: boolean;
  sentimentError: string | null;
  /** Cache entry: payload + timestamp fetched + optional rate-limit cooldown */
  sentimentCache: Record<string, { payload: SentimentPayload; fetchedAt: number; rateLimitedUntil?: number }>;

  setConsensusData: (data: ConsensusReport) => void;
  clearConsensusData: () => void;
  loadConsensusForSymbol: (symbol: string) => void;
  fetchDeepAnalysis: (symbol: string) => Promise<void>;
  loadSentimentForSymbol: (symbol: string) => Promise<void>;
  refreshSentimentForSymbol: (symbol: string) => Promise<void>;
  clearAiPlan: () => void;
  openPosition: (symbol: string, plan: AiExecutionPlan) => void;
  closePosition: (id: string, exitPrice: number) => void;
}

// ── Module-level in-flight deduplication set ─────────────────────────────
// If two components simultaneously request sentiment for the same symbol,
// only the first call makes a network request. Others wait or skip.
const sentimentInFlight = new Set<string>();

const SENTIMENT_TTL_MS   = 10 * 60 * 1000;  // 10 minutes
const SENTIMENT_429_COOL = 5  * 60 * 1000;  // 5 minutes cooldown after 429

// ── Tauri invoke helper ─────────────────────────────────────────────────

async function tauriInvoke<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  // Dynamic import to avoid SSR issues with Tauri APIs
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(cmd, args);
}

// ── Execution Plan Parser ───────────────────────────────────────────────

/** Extract numeric values from AI execution plan text.
 *  Handles patterns like "Entry: 24150", "SL: 24100", "TP: 24300"
 *  as well as "Entry at 24150", "Stop Loss 24100", "Target 24300".
 */
function parseExecutionPlan(text: string): { entry: number; sl: number; tp: number } {
  const normalize = text.replace(/,/g, ''); // strip thousands separators

  // Match entry price
  const entryMatch = normalize.match(
    /(?:entry|buy|sell|enter)\s*(?:at|price|:)?\s*[:=]?\s*(\d+(?:\.\d+)?)/i
  );
  // Match stop loss
  const slMatch = normalize.match(
    /(?:sl|stop[\s_-]?loss|invalidation|risk)\s*(?:at|price|:)?\s*[:=]?\s*(\d+(?:\.\d+)?)/i
  );
  // Match take profit / target
  const tpMatch = normalize.match(
    /(?:tp|take[\s_-]?profit|target|objective)\s*(?:at|price|:)?\s*[:=]?\s*(\d+(?:\.\d+)?)/i
  );

  const entry = entryMatch ? parseFloat(entryMatch[1]) : 0;
  const sl = slMatch ? parseFloat(slMatch[1]) : 0;
  const tp = tpMatch ? parseFloat(tpMatch[1]) : 0;

  return { entry, sl, tp };
}

// ── Store ───────────────────────────────────────────────────────────────

export const useQuantStore = create<QuantStore>((set, get) => ({
  consensusData: null,
  consensusCache: {},
  aiPlan: null,
  isAnalyzing: false,
  analysisError: null,
  activePositions: [],
  completedTrades: [],

  // ── Decoupled Sentiment State ────────────────────────────────────
  activeSentiment: null,
  isFetchingSentiment: false,
  sentimentError: null,
  sentimentCache: {},

  setConsensusData: (data: ConsensusReport) => {
    const sym = data.symbol?.toUpperCase();
    console.log(`[QuantStore] ✔ Consensus SET symbol=${sym} trend=${data.trend_score} momentum=${data.momentum_state}`);
    set((state) => ({
      consensusData: data,
      consensusCache: sym
        ? { ...state.consensusCache, [sym]: data }
        : state.consensusCache,
    }));
  },

  clearConsensusData: () => set({ consensusData: null }),

  loadConsensusForSymbol: (symbol: string) => {
    const sym = symbol.toUpperCase();
    const cached = get().consensusCache[sym];
    if (cached) {
      console.log(`[QuantStore] ✔ Consensus CACHE HIT symbol=${sym} trend=${cached.trend_score}`);
      set({ consensusData: cached });
    } else {
      console.log(`[QuantStore] ⏳ Consensus CACHE MISS symbol=${sym} — clearing stale data`);
      set({ consensusData: null });
    }
  },

  // Cache-aware with TTL: serves cached data on symbol click.
  // Skips network call if:
  //   • Data is fresh (< 10 minutes old)
  //   • Same symbol is already being fetched (deduplication)
  //   • HF returned 429 recently (5-minute cooldown per symbol)
  loadSentimentForSymbol: async (symbol: string) => {
    const entry = get().sentimentCache[symbol];
    const now = Date.now();

    // Serve fresh cache hit
    if (entry && (now - entry.fetchedAt) < SENTIMENT_TTL_MS) {
      console.log(`[QuantStore] ✔ Sentiment CACHE HIT symbol=${symbol} score=${entry.payload.score} age=${Math.round((now - entry.fetchedAt) / 1000)}s`);
      set({ activeSentiment: entry.payload, isFetchingSentiment: false, sentimentError: null });
      return;
    }

    // Rate-limit cooldown active?
    if (entry?.rateLimitedUntil && now < entry.rateLimitedUntil) {
      const secs = Math.round((entry.rateLimitedUntil - now) / 1000);
      console.warn(`[QuantStore] ⚠ Sentiment 429 cooldown active for ${symbol} — ${secs}s remaining`);
      if (entry.payload) set({ activeSentiment: entry.payload });
      return;
    }

    // In-flight deduplication
    if (sentimentInFlight.has(symbol)) {
      console.log(`[QuantStore] ⏳ Sentiment already in-flight for ${symbol} — skipping duplicate`);
      return;
    }

    console.log(`[QuantStore] ▶ Sentiment fetch symbol=${symbol}`);
    sentimentInFlight.add(symbol);
    set({ isFetchingSentiment: true, sentimentError: null });

    try {
      const payload = await tauriInvoke<SentimentPayload>('fetch_symbol_sentiment', { symbol });
      console.log(`[QuantStore] ✔ Sentiment OK symbol=${symbol} score=${payload.score} label=${payload.label}`);
      set((state) => ({
        activeSentiment: payload,
        isFetchingSentiment: false,
        sentimentCache: {
          ...state.sentimentCache,
          [symbol]: { payload, fetchedAt: Date.now() },
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const is429 = message.includes('429') || message.toLowerCase().includes('too many');
      console.error(`[QuantStore] ✘ Sentiment FAIL symbol=${symbol}: ${message}`);
      set((state) => ({
        isFetchingSentiment: false,
        sentimentError: message,
        // On 429: set cooldown so we don't hammer again for 5 minutes
        sentimentCache: is429 ? {
          ...state.sentimentCache,
          [symbol]: {
            payload: state.sentimentCache[symbol]?.payload ?? (state.activeSentiment?.symbol === symbol ? state.activeSentiment : null as unknown as SentimentPayload),
            fetchedAt: state.sentimentCache[symbol]?.fetchedAt ?? 0,
            rateLimitedUntil: Date.now() + SENTIMENT_429_COOL,
          },
        } : state.sentimentCache,
      }));
    } finally {
      sentimentInFlight.delete(symbol);
    }
  },

  // Force-refresh: bypasses TTL cache (but still respects 429 cooldown).
  // Called from AI Quant Analysis button.
  refreshSentimentForSymbol: async (symbol: string) => {
    const entry = get().sentimentCache[symbol];
    const now = Date.now();

    // Respect 429 cooldown even on force-refresh
    if (entry?.rateLimitedUntil && now < entry.rateLimitedUntil) {
      const secs = Math.round((entry.rateLimitedUntil - now) / 1000);
      console.warn(`[QuantStore] ⚠ Sentiment 429 cooldown — skipping refresh for ${symbol} (${secs}s remaining)`);
      return;
    }

    if (sentimentInFlight.has(symbol)) {
      console.log(`[QuantStore] ⏳ Sentiment already in-flight for ${symbol} — skipping refresh`);
      return;
    }

    console.log(`[QuantStore] ▶ Sentiment REFRESH (force) symbol=${symbol}`);
    sentimentInFlight.add(symbol);
    set({ isFetchingSentiment: true, sentimentError: null });

    try {
      const payload = await tauriInvoke<SentimentPayload>('fetch_symbol_sentiment', { symbol });
      console.log(`[QuantStore] ✔ Sentiment REFRESHED symbol=${symbol} score=${payload.score}`);
      set((state) => ({
        activeSentiment: payload,
        isFetchingSentiment: false,
        sentimentCache: {
          ...state.sentimentCache,
          [symbol]: { payload, fetchedAt: Date.now() },
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const is429 = message.includes('429') || message.toLowerCase().includes('too many');
      console.error(`[QuantStore] ✘ Sentiment refresh FAIL symbol=${symbol}: ${message}`);
      set((state) => ({
        isFetchingSentiment: false,
        sentimentError: message,
        sentimentCache: is429 ? {
          ...state.sentimentCache,
          [symbol]: {
            payload: state.sentimentCache[symbol]?.payload ?? (state.activeSentiment?.symbol === symbol ? state.activeSentiment : null as unknown as SentimentPayload),
            fetchedAt: state.sentimentCache[symbol]?.fetchedAt ?? 0,
            rateLimitedUntil: Date.now() + SENTIMENT_429_COOL,
          },
        } : state.sentimentCache,
      }));
    } finally {
      sentimentInFlight.delete(symbol);
    }
  },

  fetchDeepAnalysis: async (symbol: string) => {
    const t0 = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    console.log(`[QuantStore] ▶ Deep analysis START symbol=${symbol} ts=${new Date().toISOString()}`);
    set({ isAnalyzing: true, analysisError: null, aiPlan: null });

    // Force-refresh sentiment with latest news before LLM analysis
    try {
      await get().refreshSentimentForSymbol(symbol);
    } catch {
      console.warn('[QuantStore] Sentiment refresh failed, continuing with analysis...');
    }

    try {
      console.log(`[QuantStore] → invoking 'run_deep_quant_analysis' (Tauri IPC)…`);
      const tInvoke = (typeof performance !== 'undefined' ? performance.now() : Date.now());

      const plan = await tauriInvoke<AiExecutionPlan>(
        'run_deep_quant_analysis',
        { symbol }
      );

      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      console.log(
        `[QuantStore] ✔ Deep analysis OK symbol=${symbol} ` +
        `ipc_ms=${Math.round(tDone - tInvoke)} total_ms=${Math.round(tDone - t0)} ` +
        `conviction=${plan.conviction_score}`
      );
      console.debug('[QuantStore] plan payload:', plan);

      set({ aiPlan: plan, isAnalyzing: false });
    } catch (err) {
      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const message = err instanceof Error ? err.message : String(err);
      console.error(
        `[QuantStore] ✘ Deep analysis FAIL symbol=${symbol} ` +
        `total_ms=${Math.round(tDone - t0)} message=${message}`
      );
      set({ isAnalyzing: false, analysisError: message });
    }
  },

  clearAiPlan: () => set({ aiPlan: null, analysisError: null }),

  openPosition: (symbol: string, plan: AiExecutionPlan) => {
    const { entry, sl, tp } = parseExecutionPlan(plan.execution_plan);

    if (entry <= 0) {
      console.warn('[QuantStore] Could not parse entry price from plan — skipping.');
      return;
    }

    // Determine LONG vs SHORT from the AI plan text
    const planLower = plan.execution_plan.toLowerCase() + ' ' + plan.setup_validation.toLowerCase();
    const isShort = planLower.includes('short') || planLower.includes('sell') || planLower.includes('bearish');
    const posType: 'LONG' | 'SHORT' = isShort ? 'SHORT' : 'LONG';

    // Default position size: 1 lot (for F&O simulation) — user can override later
    const size = 1;

    // Compute safe SL/TP defaults if parser couldn't find them
    const entryPrice = entry;
    const defaultRisk = entryPrice * 0.02; // 2% of entry
    const stopLoss = sl > 0 ? sl : (posType === 'LONG' ? entryPrice - defaultRisk : entryPrice + defaultRisk);
    const takeProfit = tp > 0 ? tp : (posType === 'LONG' ? entryPrice + defaultRisk * 2 : entryPrice - defaultRisk * 2);

    const position: Position = {
      id: `pos-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      symbol,
      entry_price: entryPrice,
      size,
      type: posType,
      stop_loss: stopLoss,
      take_profit: takeProfit,
      timestamp: Date.now(),
    };

    set((state) => ({
      activePositions: [...state.activePositions, position],
    }));

    console.log(`[QuantStore] Position opened: ${posType} ${symbol} @ ${entryPrice} | SL: ${stopLoss} | TP: ${takeProfit}`);
  },

  closePosition: (id: string, exitPrice: number) => {
    const state = get();
    const position = state.activePositions.find((p) => p.id === id);
    if (!position) return;

    // PNL = (exit - entry) * size for LONG; (entry - exit) * size for SHORT
    const rawPnl = position.type === 'LONG'
      ? (exitPrice - position.entry_price) * position.size
      : (position.entry_price - exitPrice) * position.size;

    const trade: CompletedTrade = {
      id: position.id,
      symbol: position.symbol,
      entry_price: position.entry_price,
      exit_price: exitPrice,
      pnl: Math.round(rawPnl * 100) / 100,
      type: position.type,
      size: position.size,
      timestamp: position.timestamp,
      closed_at: Date.now(),
    };

    set((prevState) => ({
      activePositions: prevState.activePositions.filter((p) => p.id !== id),
      completedTrades: [trade, ...prevState.completedTrades].slice(0, 100),
    }));

    // Persist to SQLite asynchronously
    tauriInvoke('log_completed_trade', {
      id: trade.id,
      symbol: trade.symbol,
      entryPrice: trade.entry_price,
      exitPrice: trade.exit_price,
      pnl: trade.pnl,
      posType: trade.type,
      size: trade.size,
      timestamp: trade.timestamp,
    }).catch((err) => {
      console.warn('[QuantStore] Trade persistence failed (non-fatal):', err);
    });

    console.log(`[QuantStore] Position closed: ${position.type} ${position.symbol} | PNL: ${trade.pnl}`);
  },
}));
