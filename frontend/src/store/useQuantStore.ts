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
  // Raw indicator values (new — from enriched ConsensusReport)
  current_price?: number | null;
  rsi_14?: number | null;
  stoch_k?: number | null;
  ema_9?: number | null;
  ema_21?: number | null;
  sma_50?: number | null;
  sma_200?: number | null;
  macd_line?: number | null;
  macd_signal?: number | null;
  macd_histogram?: number | null;
  bb_upper?: number | null;
  bb_mid?: number | null;
  bb_lower?: number | null;
  atr_14?: number | null;
  vwap?: number | null;
  obv?: number | null;
  cmf?: number | null;
  parabolic_sar?: number | null;
  // Projection curves
  vwepr_value?: number | null;
  vwepr_slope?: number | null;
  ols_value?: number | null;
  ols_slope?: number | null;
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

export interface ChartPattern {
  pattern_type: string;
  sentiment: string;
  confidence: number;
  start_idx: number;
  end_idx: number;
  description: string;
  time?: number;
  start_time?: number;
  high?: number;
  low?: number;
  // Phase 9.2 fields
  structural_bias: string;
  geometric_strictness: number;
  volume_validation: string;
  breakout_status: string;
  // Phase 10: Forming pattern fields
  is_forming?: boolean;
  formation_progress?: number;
}

export interface MultiTfChartPatterns {
  timeframe: string;
  patterns: ChartPattern[];
}


// ── Deep Quant SSE stream event payload ─────────────────────────────────
// Shape emitted by the Rust `deep-quant-stream` Tauri event bridge.
export interface StreamEventPayload {
  event: string;
  data?: {
    content?: string;
    tool?: string;
    args?: Record<string, unknown>;
    status?: string;
    error?: string;
    thread_id?: string;
    [key: string]: unknown;
  };
}

// ── Trade Q&A chat message ──────────────────────────────────────────────
// One turn of the post-analysis Trade_QA_Mode chat. `role` mirrors the
// LLM convention; `content` is the (streamed) text; `activity` surfaces
// lightweight tool-call lines while the assistant turn streams; `streaming`
// is true while REASONING is still accumulating; `error` marks an ERROR turn.
export interface QaChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  activity?: string[];
  streaming?: boolean;
  error?: boolean;
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

  // ── Terminal / Stream States ──────────────────────────────────────────
  sessionStatus: 'idle' | 'running' | 'watching' | 'complete' | 'error';
  reasoningSteps: Array<{
    id: string;
    type: string;
    content: string;
    timestamp: number;
    toolName?: string;
    args?: Record<string, unknown>;
  }>;
  finalTrade: AiExecutionPlan | null;
  /** Number of tool calls that have started but not yet completed */
  _pendingToolCalls: number;
  /** Guard: true once RUN_FINISHED has been processed for this session */
  _runFinishedProcessed: boolean;

  // ── Trade Q&A (post-analysis follow-up chat) ──────────────────────────
  /** Thread id of the most recent analysis run — reused for Q&A turns so the
   *  Python service answers from the persisted Session_Analysis_Context. */
  currentThreadId: string | null;
  /** Ordered list of Q&A chat turns (user + assistant). */
  qaMessages: QaChatMessage[];
  /** 'streaming' while a Q&A turn is in flight, otherwise 'idle'. */
  qaStatus: 'idle' | 'streaming';
  /** Guard: true once the current Q&A turn's RUN_FINISHED has been handled. */
  _qaRunFinishedProcessed: boolean;

  setConsensusData: (data: ConsensusReport) => void;
  clearConsensusData: () => void;
  loadConsensusForSymbol: (symbol: string) => void;
  fetchDeepAnalysis: (
    symbol: string,
    mode?: 'FIND' | 'VERIFY',
    manualTrade?: {
      side: string;
      entry: number;
      stopLoss: number;
      takeProfit: number;
      userAnalysis: string;
    }
  ) => Promise<void>;
  loadSentimentForSymbol: (symbol: string) => Promise<void>;
  refreshSentimentForSymbol: (symbol: string) => Promise<void>;
  clearAiPlan: () => void;
  openPosition: (symbol: string, plan: AiExecutionPlan) => void;
  closePosition: (id: string, exitPrice: number) => void;
  handleStreamEvent: (payload: StreamEventPayload) => void;
  resetTerminal: () => void;

  // ── Trade Q&A actions ───────────────────────────────────────────────
  /** Ask a follow-up question about the completed analysis. Streams the
   *  answer over the dedicated `deep-quant-qa-stream` Tauri event. */
  askQuestion: (question: string) => Promise<void>;
  /** Clear the Q&A chat transcript (keeps the captured thread id). */
  clearQa: () => void;

  // ── Multi-Timeframe Chart Patterns ──────────────────────────────────
  multiTfPatterns: MultiTfChartPatterns[] | null;
  isFetchingPatterns: boolean;
  fetchMultiTfPatterns: (symbol: string) => Promise<void>;
}

// ── Module-level in-flight deduplication set ─────────────────────────────
// If two components simultaneously request sentiment for the same symbol,
// only the first call makes a network request. Others wait or skip.
const sentimentInFlight = new Set<string>();

const SENTIMENT_TTL_MS = 10 * 60 * 1000;  // 10 minutes
const SENTIMENT_429_COOL = 5 * 60 * 1000;  // 5 minutes cooldown after 429

// ── Multi-timeframe chart-pattern cache + in-flight dedup ────────────────
// fetchMultiTfPatterns is auto-triggered on EVERY Deep Quant run, and the
// underlying command fans out a DB fetch + forming-pattern detection across
// 7 timeframes. Without a cache, repeatedly analyzing the same symbol re-did
// all of that work each time. A short TTL keeps intraday patterns fresh while
// collapsing back-to-back runs into one fetch; the in-flight set drops
// duplicate concurrent requests for the same symbol.
const multiTfInFlight = new Set<string>();
const multiTfCache = new Map<string, { data: MultiTfChartPatterns[]; fetchedAt: number }>();
const MULTI_TF_TTL_MS = 2 * 60 * 1000;  // 2 minutes

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

/** Extract final JSON trade parameters from text messages using brace matching and validation */
function extractFinalTrade(text: string): AiExecutionPlan | null {
  if (!text) return null;

  // Loosely-typed view of a parsed JSON object — the model may emit any of
  // several key spellings, so every field is optional and read defensively.
  type ParsedPlan = {
    conviction_score?: unknown;
    conviction?: unknown;
    setup_validation?: unknown;
    validation?: unknown;
    setup?: unknown;
    execution_plan?: unknown;
    plan?: unknown;
    [key: string]: unknown;
  };

  interface JsonCandidate {
    parsed: ParsedPlan;
    startIdx: number;
    endIdx: number;
    length: number;
  }

  const candidates: JsonCandidate[] = [];

  // Find all start indexes of '{'
  const startIndexes: number[] = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{') {
      startIndexes.push(i);
    }
  }

  // For each '{', try to find matching closing braces and parse
  for (const startIdx of startIndexes) {
    let braceCount = 0;
    for (let j = startIdx; j < text.length; j++) {
      if (text[j] === '{') {
        braceCount++;
      } else if (text[j] === '}') {
        braceCount--;
        if (braceCount === 0) {
          const candidateStr = text.substring(startIdx, j + 1);
          try {
            const parsed = JSON.parse(candidateStr) as ParsedPlan;
            if (parsed && typeof parsed === 'object') {
              candidates.push({
                parsed,
                startIdx,
                endIdx: j,
                length: candidateStr.length
              });
            }
          } catch {
            // Silence parsing errors for incomplete or invalid blocks
          }
        }
      }
    }
  }

  // Filter for candidates matching the AiExecutionPlan schema
  const validCandidates = candidates.filter(c => {
    const p = c.parsed;
    return p && (
      p.conviction_score !== undefined ||
      p.conviction !== undefined ||
      p.execution_plan !== undefined ||
      p.plan !== undefined ||
      p.setup_validation !== undefined ||
      p.setup !== undefined
    );
  });

  // Coerce an unknown JSON value to a string, defaulting to empty.
  const asString = (v: unknown): string => (typeof v === 'string' ? v : '');
  const asScore = (...vals: unknown[]): number => {
    for (const v of vals) {
      if (typeof v === 'number' && Number.isFinite(v)) return v;
    }
    return 75;
  };

  if (validCandidates.length === 0) {
    // Fallback: search for any valid JSON object at the top level
    const anyValid = candidates.filter(c => c.parsed && typeof c.parsed === 'object');
    if (anyValid.length > 0) {
      const topLevel = anyValid.filter(c => 
        !anyValid.some(other => other !== c && other.startIdx <= c.startIdx && other.endIdx >= c.endIdx)
      );
      if (topLevel.length > 0) {
        topLevel.sort((a, b) => b.startIdx - a.startIdx);
        const parsed = topLevel[0].parsed;
        return {
          conviction_score: asScore(parsed.conviction_score, parsed.conviction),
          setup_validation: asString(parsed.setup_validation) || asString(parsed.validation) || asString(parsed.setup),
          execution_plan: asString(parsed.execution_plan) || asString(parsed.plan)
        };
      }
    }
    return null;
  }

  // Filter out candidates that are nested inside other valid candidates
  const nonNested = validCandidates.filter(c => {
    const isNested = validCandidates.some(other => 
      other !== c && 
      other.startIdx <= c.startIdx && 
      other.endIdx >= c.endIdx
    );
    return !isNested;
  });

  if (nonNested.length === 0) return null;

  // Sort by startIdx descending to pick the last trade plan generated in the text stream
  nonNested.sort((a, b) => b.startIdx - a.startIdx);

  const best = nonNested[0].parsed;
  return {
    conviction_score: asScore(best.conviction_score, best.conviction),
    setup_validation: asString(best.setup_validation) || asString(best.validation) || asString(best.setup),
    execution_plan: asString(best.execution_plan) || asString(best.plan)
  };
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
  sessionStatus: 'idle',
  reasoningSteps: [],
  finalTrade: null,
  _pendingToolCalls: 0,
  _runFinishedProcessed: false,

  // ── Trade Q&A State ──────────────────────────────────────────────
  currentThreadId: null,
  qaMessages: [],
  qaStatus: 'idle',
  _qaRunFinishedProcessed: false,

  // ── Decoupled Sentiment State ────────────────────────────────────
  activeSentiment: null,
  isFetchingSentiment: false,
  sentimentError: null,
  sentimentCache: {},

  // ── Multi-Timeframe Chart Patterns State ──
  multiTfPatterns: null,
  isFetchingPatterns: false,


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

  fetchDeepAnalysis: async (
    symbol: string,
    mode?: 'FIND' | 'VERIFY',
    manualTrade?: {
      side: string;
      entry: number;
      stopLoss: number;
      takeProfit: number;
      userAnalysis: string;
    }
  ) => {
    const t0 = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const activeMode = mode || 'FIND';
    console.log(`[QuantStore] ▶ Deep analysis START symbol=${symbol} mode=${activeMode} ts=${new Date().toISOString()}`);
    
    // Clear terminal state and set running status
    set({
      sessionStatus: 'running',
      reasoningSteps: [],
      finalTrade: null,
      aiPlan: null,
      isAnalyzing: true,
      analysisError: null,
      _pendingToolCalls: 0,
      _runFinishedProcessed: false,
      multiTfPatterns: null,
      isFetchingPatterns: true,
      // A fresh analysis invalidates any prior Q&A transcript / thread id —
      // the new run will re-capture currentThreadId from its RUN_STARTED.
      currentThreadId: null,
      qaMessages: [],
      qaStatus: 'idle',
      _qaRunFinishedProcessed: false,
    });

    // Trigger multi-timeframe chart patterns fetch in parallel
    get().fetchMultiTfPatterns(symbol);

    // Force-refresh sentiment with latest news before LLM analysis
    try {
      await get().refreshSentimentForSymbol(symbol);
    } catch {
      console.warn('[QuantStore] Sentiment refresh failed, continuing with analysis...');
    }

    // Read the active timeframe AND the active workspace profile from the trade
    // store for AI context injection. The profile (INTRADAY / SWING / INVESTOR /
    // FNO) tells the agent which data domain and analysis horizon the user is in,
    // so an F&O run prioritizes options/futures positioning and an intraday run
    // stays on the short-horizon spot microstructure.
    const { useTradeStore } = await import('./useTradeStore');
    const activeTimeframe = useTradeStore.getState().activeTimeframe;
    const activeProfile = useTradeStore.getState().activeProfile;
    // The F&O section's selected expiry (ISO "YYYY-MM-DD", '' => nearest). Only
    // meaningful on an FNO-profile run; the agent ignores it otherwise.
    const fnoExpiry = useTradeStore.getState().fnoExpiry;
    console.log(`[QuantStore] → AI context: timeframe=${activeTimeframe} profile=${activeProfile} fnoExpiry=${fnoExpiry || '(nearest)'}`);

    try {
      console.log(`[QuantStore] → invoking 'run_deep_quant_agent' (Tauri IPC)…`);
      const tInvoke = (typeof performance !== 'undefined' ? performance.now() : Date.now());

      await tauriInvoke<void>(
        'run_deep_quant_agent',
        {
          symbol,
          mode: activeMode,
          timeframe: activeTimeframe,
          profile: activeProfile,
          fnoExpiry,
          manualTrade: manualTrade ? {
            side: manualTrade.side,
            entry: manualTrade.entry,
            stop_loss: manualTrade.stopLoss,
            take_profit: manualTrade.takeProfit,
            user_analysis: manualTrade.userAnalysis
          } : null
        }
      );

      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      console.log(
        `[QuantStore] ✔ Deep analysis triggered symbol=${symbol} ` +
        `ipc_ms=${Math.round(tDone - tInvoke)} total_ms=${Math.round(tDone - t0)}`
      );

      // Bug 2 fix: Safety timeout — if isAnalyzing is still true after 120s,
      // the SSE stream silently failed. Auto-reset to prevent infinite spinner.
      setTimeout(() => {
        const state = get();
        if (state.isAnalyzing && state.sessionStatus === 'running') {
          console.warn('[QuantStore] ⚠ Safety timeout: isAnalyzing stuck for 120s. Auto-resetting.');
          set({
            isAnalyzing: false,
            sessionStatus: 'error',
            analysisError: 'Analysis timed out after 120 seconds. The Python agent server may be unreachable or the LLM request stalled. Please retry.',
          });
        }
      }, 120_000);
    } catch (err) {
      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const message = err instanceof Error ? err.message : String(err);
      console.error(
        `[QuantStore] ✘ Deep analysis FAIL symbol=${symbol} ` +
        `total_ms=${Math.round(tDone - t0)} message=${message}`
      );
      set({ isAnalyzing: false, sessionStatus: 'error', analysisError: message });
    }
  },

  clearAiPlan: () => set({
    aiPlan: null,
    finalTrade: null,
    analysisError: null,
    sessionStatus: 'idle',
    reasoningSteps: [],
    _pendingToolCalls: 0,
    _runFinishedProcessed: false,
    multiTfPatterns: null,
    isFetchingPatterns: false,
  }),

  handleStreamEvent: (payload: StreamEventPayload) => {
    if (!payload || !payload.event) return;

    const event = payload.event;
    const data = payload.data;

    console.log(`[QuantStore] 📥 Stream event: ${event}`, data);

    switch (event) {
      case 'RUN_STARTED': {
        // Capture the analysis thread_id so post-analysis Q&A turns can reuse
        // it (the Python service grounds Q&A in this thread's persisted
        // Session_Analysis_Context).
        const startedThreadId = data?.thread_id;
        if (startedThreadId) {
          set({ currentThreadId: startedThreadId });
        }

        // If we're resuming from a 'watching' state, DON'T clear the existing
        // reasoning steps — the user needs the full analysis context from the
        // original run. Only reset the guard flag so the resumed RUN_FINISHED
        // will be processed.
        const currentStatus = get().sessionStatus;
        if (currentStatus === 'watching') {
          const resumeStep = {
            id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            type: 'message',
            // The run may resume on a target reach, an invalidation, OR a heartbeat
            // pulse — RUN_STARTED does not carry which. Stay neutral rather than
            // asserting the target was met; the following events (invalidation
            // notice / Best_Current_Read / decision) state exactly what happened.
            content: '\n---\n### Resuming Analysis — Fresh Market Data\nThe watcher woke this run. Re-checking the setup with the latest data...\n---\n',
            timestamp: Date.now()
          };
          set((state) => ({
            sessionStatus: 'running',
            isAnalyzing: true,
            analysisError: null,
            _pendingToolCalls: 0,
            _runFinishedProcessed: false,
            reasoningSteps: [...state.reasoningSteps, resumeStep],
          }));
        } else {
          set({
            sessionStatus: 'running',
            reasoningSteps: [],
            finalTrade: null,
            aiPlan: null,
            isAnalyzing: true,
            analysisError: null,
            _pendingToolCalls: 0,
            _runFinishedProcessed: false,
          });
        }
        break;
      }
      // The hardened backend emits the agent's chain-of-thought as REASONING
      // events (TEXT_MESSAGE retained for backward compatibility). Both render
      // as a `message` step so the glass-box transcript is never blank.
      case 'REASONING':
      case 'TEXT_MESSAGE': {
        const content = data?.content || '';
        if (content) {
          const step = {
            id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            type: 'message',
            content,
            timestamp: Date.now()
          };
          set((state) => ({
            reasoningSteps: [...state.reasoningSteps, step]
          }));
        }
        break;
      }
      // Interim Best_Current_Read (Adaptive Opportunity Engine) — a non-committal
      // read surfaced on a stand-aside HOLD and, when the Heartbeat_Monitor is
      // enabled, on each mid-wait heartbeat pulse. Rendered inline so a hunt that
      // takes no trade (or is still waiting) still shows the current bias, the key
      // levels, and why it is standing aside — instead of the box appearing frozen.
      case 'BEST_CURRENT_READ': {
        const bias = (data?.bias as string) || 'neutral';
        const why = (data?.why_standing_aside as string) || '';
        const levelsRaw = (data?.levels && typeof data.levels === 'object')
          ? (data.levels as Record<string, unknown>)
          : {};
        const levelStr = Object.entries(levelsRaw)
          .filter(([, v]) => typeof v === 'number' && Number.isFinite(v as number))
          .map(([k, v]) => `${k}: ${v}`)
          .join(' · ');
        const lines = [
          `**📍 Best Current Read — bias: ${bias}**`,
          levelStr ? `Key levels: ${levelStr}` : '',
          why ? `Read: ${why}` : '',
        ].filter(Boolean);
        const step = {
          id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          // Rendered via the 'message' branch (like VERIFICATION_STEP / DECISION);
          // the bold "📍 Best Current Read" header keeps it visually distinct.
          type: 'message',
          content: lines.join('\n'),
          timestamp: Date.now(),
        };
        set((state) => ({
          reasoningSteps: [...state.reasoningSteps, step],
        }));
        break;
      }
      // Glass-box verification events — each surfaces one self-check the agent
      // ran (e.g. risk/reward validation, data-sufficiency gate) and its
      // outcome. Rendered inline so the user can audit the decision trail.
      case 'VERIFICATION_STEP': {
        const check = (data?.check as string) || (data?.tool as string) || 'check';
        const outcome = (data?.outcome as string) || (data?.status as string) || '';
        const detail = (data?.content as string) || (data?.detail as string) || '';
        const body = [outcome, detail].filter(Boolean).join(' — ');
        const step = {
          id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          type: 'message',
          content: `**Verification — ${check}${body ? `: ${body}` : ''}**`,
          timestamp: Date.now()
        };
        set((state) => ({
          reasoningSteps: [...state.reasoningSteps, step]
        }));
        break;
      }
      // The terminal DECISION event carries the structured verdict. We render a
      // human-readable summary into the transcript AND seed finalTrade/aiPlan
      // from it as a fallback, so the plan panel populates even if the model
      // never emitted a parseable JSON block in its free-text reasoning.
      case 'DECISION': {
        const action = (data?.action as string) || (data?.decision as string) || '';
        const convictionRaw = data?.conviction_score ?? data?.conviction;
        const conviction = typeof convictionRaw === 'number' && Number.isFinite(convictionRaw)
          ? convictionRaw
          : undefined;
        const rationale = (data?.rationale as string)
          || (data?.setup_validation as string)
          || (data?.thesis as string)
          || '';
        const executionPlan = (data?.execution_plan as string) || '';

        const summaryLines = [
          `**Decision${action ? `: ${action}` : ''}**`,
          conviction !== undefined ? `Conviction: ${conviction}/100` : '',
          rationale ? `Rationale: ${rationale}` : '',
          executionPlan ? `Plan: ${executionPlan}` : '',
        ].filter(Boolean);

        const step = {
          id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          type: 'message',
          content: summaryLines.join('\n'),
          timestamp: Date.now()
        };

        // Build a fallback plan from the structured decision payload.
        const decisionPlan: AiExecutionPlan | null =
          (conviction !== undefined || rationale || executionPlan)
            ? {
                conviction_score: conviction ?? 75,
                setup_validation: rationale,
                execution_plan: executionPlan,
              }
            : null;

        set((state) => ({
          reasoningSteps: [...state.reasoningSteps, step],
          // Only seed when not already populated — never clobber a richer
          // plan already parsed from the model's JSON output.
          finalTrade: state.finalTrade ?? decisionPlan,
          aiPlan: state.aiPlan ?? decisionPlan,
        }));
        break;
      }
      case 'TOOL_CALL_START': {
        const toolName = data?.tool || '';
        if (toolName) {
          const step = {
            id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            type: 'tool_start',
            toolName,
            args: data?.args,
            content: `> Executing tool: ${toolName}...`,
            timestamp: Date.now()
          };
          const isWatching = toolName === 'watch_price_condition';
          set((state) => ({
            reasoningSteps: [...state.reasoningSteps, step],
            sessionStatus: isWatching ? 'watching' : state.sessionStatus,
            _pendingToolCalls: state._pendingToolCalls + 1,
          }));
        }
        break;
      }
      case 'TOOL_CALL_END': {
        const toolName = data?.tool || '';
        if (toolName) {
          const step = {
            id: `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            type: 'tool_end',
            toolName,
            content: `✔ Tool ${toolName} completed successfully.`,
            timestamp: Date.now()
          };
          set((state) => ({
            reasoningSteps: [...state.reasoningSteps, step],
            _pendingToolCalls: Math.max(0, state._pendingToolCalls - 1),
          }));
        }
        break;
      }
      case 'RUN_FINISHED': {
        // Guard: ignore duplicate RUN_FINISHED events
        if (get()._runFinishedProcessed) {
          console.log('[QuantStore] ⚠ Duplicate RUN_FINISHED ignored.');
          break;
        }

        // Bug 3 fix: If there are pending tool calls but the server says
        // the run is finished, those tool calls are done — the TOOL_CALL_END
        // events were lost or never emitted. Force-reset and process normally.
        if (get()._pendingToolCalls > 0) {
          console.warn(`[QuantStore] ⚠ RUN_FINISHED received with ${get()._pendingToolCalls} pending tool(s) — force-resetting (server confirmed completion).`);
          set({ _pendingToolCalls: 0 });
        }

        // Concatenate all text messages to parse final plan
        const accumulatedText = get().reasoningSteps
          .filter(s => s.type === 'message')
          .map(s => s.content)
          .join('');

        // Only accept a structured plan that the model actually emitted.
        // We do NOT synthesize a plan (no fabricated conviction score) when the
        // model failed to return one — the terminal relies exclusively on real
        // model output. If nothing parseable was produced, finalTrade stays null
        // and the reasoning transcript is what the user sees.
        const tradePlan = extractFinalTrade(accumulatedText);

        // Handle paused (watching) status from Python agent
        const runStatus = data?.status;
        if (runStatus === 'paused') {
          set({
            sessionStatus: 'watching',
            isAnalyzing: false,
            _runFinishedProcessed: true,
          });
          break;
        }

        set((state) => ({
          // Never downgrade an error to 'complete' — if an ERROR already
          // finalized this run, keep it (defense in depth alongside the
          // _runFinishedProcessed guard set in the ERROR case).
          sessionStatus: state.sessionStatus === 'error' ? 'error' : 'complete',
          // Prefer a freshly-parsed JSON plan, but fall back to whatever was
          // already set (e.g. seeded by a DECISION event) so the plan panel is
          // never blanked out at the end of a run.
          finalTrade: tradePlan ?? state.finalTrade,
          aiPlan: tradePlan ?? state.aiPlan, // Synchronize with existing UI
          isAnalyzing: false,
          _runFinishedProcessed: true,
        }));
        break;
      }
      case 'ERROR': {
        const errorMsg = data?.error || 'Unknown streaming error';
        // Mark the run finalized so a trailing *synthetic* RUN_FINISHED emitted
        // by the Rust bridge (which fires whenever the Python stream ends without
        // its own RUN_FINISHED — including the error path) is treated as a
        // duplicate and ignored, preserving the error state instead of flipping
        // it to 'complete'.
        set({
          sessionStatus: 'error',
          isAnalyzing: false,
          analysisError: errorMsg,
          _runFinishedProcessed: true,
        });
        break;
      }
      default:
        break;
    }
  },

  resetTerminal: () => set({
    sessionStatus: 'idle',
    reasoningSteps: [],
    finalTrade: null,
    aiPlan: null,
    analysisError: null,
    _pendingToolCalls: 0,
    _runFinishedProcessed: false,
    multiTfPatterns: null,
    isFetchingPatterns: false,
  }),

  clearQa: () => set({
    qaMessages: [],
    qaStatus: 'idle',
    _qaRunFinishedProcessed: false,
  }),

  askQuestion: async (question: string) => {
    const threadId = get().currentThreadId;
    const trimmed = question.trim();

    if (!trimmed) return;
    if (!threadId) {
      console.warn('[QuantStore] askQuestion ignored — no analysis thread_id captured yet.');
      return;
    }
    if (get().qaStatus === 'streaming') {
      console.warn('[QuantStore] askQuestion ignored — a Q&A turn is already streaming.');
      return;
    }

    console.log(`[QuantStore] ▶ Trade Q&A ask thread=${threadId} q="${trimmed}"`);

    const stamp = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const userMsgId = `qa-user-${stamp}`;
    const assistantMsgId = `qa-asst-${stamp}`;

    // Push the user turn + an in-progress assistant turn, and enter streaming.
    set((state) => ({
      qaStatus: 'streaming',
      _qaRunFinishedProcessed: false,
      qaMessages: [
        ...state.qaMessages,
        { id: userMsgId, role: 'user', content: trimmed },
        { id: assistantMsgId, role: 'assistant', content: '', activity: [], streaming: true },
      ],
    }));

    let unlisten: (() => void) | undefined;

    // Finalize the assistant turn exactly once and tear down the listener.
    // Guards against duplicate RUN_FINISHED like the analysis run handler.
    const finalize = () => {
      if (get()._qaRunFinishedProcessed) {
        console.log('[QuantStore] ⚠ Duplicate Q&A RUN_FINISHED ignored.');
        return;
      }
      set((state) => ({
        qaStatus: 'idle',
        _qaRunFinishedProcessed: true,
        qaMessages: state.qaMessages.map((m) =>
          m.id === assistantMsgId ? { ...m, streaming: false } : m
        ),
      }));
      unlisten?.();
      unlisten = undefined;
    };

    try {
      // Mirror the deep-quant-stream listener registration (dynamic import of
      // the same `@tauri-apps/api/event` `listen`), but on the dedicated
      // `deep-quant-qa-stream` channel emitted by `ask_trade_question`.
      const { listen } = await import('@tauri-apps/api/event');
      unlisten = await listen<StreamEventPayload>('deep-quant-qa-stream', (event) => {
        const payload = event.payload;
        if (!payload || !payload.event) return;

        const ev = payload.event;
        const data = payload.data;

        switch (ev) {
          case 'RUN_STARTED':
            // Q&A reuses the original thread; nothing to capture here.
            break;
          // A Q&A turn streams its answer as REASONING content (TEXT_MESSAGE
          // tolerated for parity with the analysis run conventions).
          case 'REASONING':
          case 'TEXT_MESSAGE': {
            const content = data?.content || '';
            if (content) {
              set((state) => ({
                qaMessages: state.qaMessages.map((m) =>
                  m.id === assistantMsgId ? { ...m, content: m.content + content } : m
                ),
              }));
            }
            break;
          }
          case 'TOOL_CALL_START': {
            const tool = data?.tool || '';
            if (tool) {
              set((state) => ({
                qaMessages: state.qaMessages.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, activity: [...(m.activity || []), `> ${tool}…`] }
                    : m
                ),
              }));
            }
            break;
          }
          case 'TOOL_CALL_END': {
            const tool = data?.tool || '';
            if (tool) {
              set((state) => ({
                qaMessages: state.qaMessages.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, activity: [...(m.activity || []), `✔ ${tool}`] }
                    : m
                ),
              }));
            }
            break;
          }
          case 'RUN_FINISHED':
            finalize();
            break;
          case 'ERROR': {
            const errorMsg = data?.error || 'Unknown Q&A streaming error';
            console.error(`[QuantStore] ✘ Trade Q&A ERROR: ${errorMsg}`);
            set((state) => ({
              qaMessages: state.qaMessages.map((m) =>
                m.id === assistantMsgId
                  ? { ...m, content: m.content || `⚠ ${errorMsg}`, error: true }
                  : m
              ),
            }));
            finalize();
            break;
          }
          default:
            break;
        }
      });

      // Invoke the proxy command (camelCase args → snake_case Rust params).
      await tauriInvoke<void>('ask_trade_question', { threadId, question: trimmed });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[QuantStore] ✘ askQuestion FAIL: ${message}`);
      set((state) => ({
        qaStatus: 'idle',
        _qaRunFinishedProcessed: true,
        qaMessages: state.qaMessages.map((m) =>
          m.id === assistantMsgId
            ? { ...m, content: m.content || `⚠ ${message}`, error: true, streaming: false }
            : m
        ),
      }));
      unlisten?.();
      unlisten = undefined;
    }
  },

  fetchMultiTfPatterns: async (symbol: string) => {
    const sym = symbol.toUpperCase();
    const now = Date.now();

    // Serve a fresh cache hit instantly — no DB fan-out, no re-detection.
    const cached = multiTfCache.get(sym);
    if (cached && now - cached.fetchedAt < MULTI_TF_TTL_MS) {
      console.log(`[QuantStore] ✔ MultiTF CACHE HIT symbol=${sym} age=${Math.round((now - cached.fetchedAt) / 1000)}s`);
      set({ multiTfPatterns: cached.data, isFetchingPatterns: false });
      return;
    }

    // Collapse duplicate concurrent requests for the same symbol.
    if (multiTfInFlight.has(sym)) {
      console.log(`[QuantStore] ⏳ MultiTF already in-flight for ${sym} — skipping duplicate`);
      return;
    }

    console.log(`[QuantStore] ▶ fetchMultiTfPatterns starting for symbol=${sym}`);
    multiTfInFlight.add(sym);
    // Keep any stale cached patterns visible instead of flashing empty while we refetch.
    set((state) => ({ isFetchingPatterns: true, multiTfPatterns: cached?.data ?? state.multiTfPatterns ?? null }));
    try {
      const data = await tauriInvoke<MultiTfChartPatterns[]>('get_multi_timeframe_chart_patterns', { symbol });
      multiTfCache.set(sym, { data, fetchedAt: Date.now() });
      console.log(`[QuantStore] ✔ fetchMultiTfPatterns completed symbol=${sym} (${data.length} timeframes)`);
      set({ multiTfPatterns: data, isFetchingPatterns: false });
    } catch (err) {
      console.error(`[QuantStore] ✘ fetchMultiTfPatterns failed for ${sym}:`, err);
      set({ isFetchingPatterns: false, multiTfPatterns: multiTfCache.get(sym)?.data ?? [] });
    } finally {
      multiTfInFlight.delete(sym);
    }
  },

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
