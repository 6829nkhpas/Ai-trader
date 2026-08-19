// useQuantStore.ts — V3 Quant Dashboard Zustand Store.
//
// Manages consensus data, AI execution plan state, simulated positions,
// and the Deep Quant Analysis pipeline trigger.

import { create } from 'zustand';
import { useAuthStore } from './useAuthStore';
import { canRunAgentMode } from './useFeatureStore';
import { RESEARCH_LOCKED_MESSAGE } from '../lib/sku';

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

// Validated directional Execution_Levels — present ONLY for a committed
// BUY/SELL trade whose Declare_Trade_Args carried finite entry/stop/target.
export interface ExecutionLevels {
  entry: number;
  stop_loss: number;
  take_profit: number;
}

export interface AiExecutionPlan {
  // `undefined` when the committed decision emitted no conviction — the UI
  // renders "—" rather than fabricating a default (R1.7). Never defaulted to 75.
  conviction_score: number | undefined;   // 1–100, or undefined when absent
  setup_validation: string;
  execution_plan: string;
  action?: 'BUY' | 'SELL' | 'HOLD';   // from the committed decision
  opportunity_tier?: string;          // e.g. 'a_plus' | 'stand_aside'
  execution_levels?: ExecutionLevels; // present ONLY for a validated directional trade
}

// Shared, pure render-guard predicate (R1). A plan is actionable only when the
// committed decision is a directional BUY/SELL carrying three finite positive
// prices. HOLD, `stand_aside`, an unknown/absent action, or missing/malformed
// levels all fail safe to non-actionable. Total over null/partial plans.
export function isActionableTrade(
  plan: AiExecutionPlan | null,
): plan is AiExecutionPlan & { execution_levels: ExecutionLevels } {
  if (!plan) return false;
  const act = (plan.action || '').toUpperCase();
  if (act === 'HOLD') return false;
  if (plan.opportunity_tier === 'stand_aside') return false;
  if (act !== 'BUY' && act !== 'SELL') return false;
  const l = plan.execution_levels;
  return (
    !!l &&
    [l.entry, l.stop_loss, l.take_profit].every(
      (n) => typeof n === 'number' && Number.isFinite(n) && n > 0,
    )
  );
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

// ── Model provider selection ────────────────────────────────────────────
// The Q&A / analysis composer lets the user pick which LLM to run. The `id` is
// the model string sent to the backend (empty string = the deployment default,
// i.e. the server's LLM_MODEL). Whether a given id resolves depends on the
// deployment's LLM gateway (a unified OpenAI-compatible gateway such as
// OpenRouter can serve Claude/GPT/DeepSeek via one endpoint); adjust the ids
// here to match your gateway's catalog.
export interface ModelOption { id: string; label: string; recommended?: boolean; }
export interface ModelProviderGroup { provider: string; models: ModelOption[]; }

// Comprehensive, current (non-deprecated) model catalog grouped by provider.
// NOTE ON IDS: the `id` is passed verbatim to the backend, which forwards it as
// the `model` to the provider gateway. The exact string a gateway expects
// varies — a native provider SDK uses e.g. "gpt-4o" / "claude-3-5-sonnet" /
// "gemini-2.5-pro" / "deepseek-chat", while a unified gateway like OpenRouter
// expects a "vendor/model" form (e.g. "anthropic/claude-3.5-sonnet"). These are
// the native ids; adjust the prefixes to match your deployment's gateway.
// The LLM gateway differs by deployment:
//   • beta       → omniroute (our shared key/model/URL) — omniroute model ids
//   • production → OpenRouter (per-user keys)            — provider/model ids
// The active list is selected by NEXT_PUBLIC_LLM_GATEWAY at build time and must
// match the server's OPENROUTER_BASE_URL / LLM_MODEL for that deployment. All
// listed models support tool calling (the glass-box agent requires it).
// Empty id = the deployment's default model (server LLM_MODEL).

// ── OpenRouter (production) — canonical provider/model ids ───────────────────
const MODEL_PROVIDERS_OPENROUTER: ModelProviderGroup[] = [
  { provider: 'Default', models: [
    { id: '', label: 'Auto' },
  ]},
  { provider: 'Anthropic (Claude)', models: [
    { id: 'anthropic/claude-sonnet-4.5', label: 'Claude Sonnet 4.5', recommended: true },
    { id: 'anthropic/claude-opus-4.5', label: 'Claude Opus 4.5' },
    { id: 'anthropic/claude-sonnet-4', label: 'Claude Sonnet 4' },
    { id: 'anthropic/claude-opus-4.1', label: 'Claude Opus 4.1' },
    { id: 'anthropic/claude-haiku-4.5', label: 'Claude Haiku 4.5' },
    { id: 'anthropic/claude-3-haiku', label: 'Claude 3 Haiku' },
  ]},
  { provider: 'OpenAI', models: [
    { id: 'openai/gpt-4o', label: 'GPT-4o', recommended: true },
    { id: 'openai/gpt-4o-mini', label: 'GPT-4o mini' },
    { id: 'openai/gpt-4.1', label: 'GPT-4.1' },
    { id: 'openai/gpt-4.1-mini', label: 'GPT-4.1 mini' },
    { id: 'openai/gpt-5', label: 'GPT-5' },
    { id: 'openai/gpt-5-mini', label: 'GPT-5 mini' },
    { id: 'openai/o3', label: 'o3' },
    { id: 'openai/o4-mini', label: 'o4-mini' },
  ]},
  { provider: 'Google (Gemini)', models: [
    { id: 'google/gemini-2.5-pro', label: 'Gemini 2.5 Pro', recommended: true },
    { id: 'google/gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
    { id: 'google/gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite' },
  ]},
  { provider: 'DeepSeek', models: [
    { id: 'deepseek/deepseek-r1', label: 'DeepSeek R1', recommended: true },
    { id: 'deepseek/deepseek-chat-v3.1', label: 'DeepSeek V3.1 (Chat)' },
    { id: 'deepseek/deepseek-v3.2', label: 'DeepSeek V3.2' },
  ]},
  { provider: 'xAI (Grok)', models: [
    { id: 'x-ai/grok-4.5', label: 'Grok 4.5' },
    { id: 'x-ai/grok-4.3', label: 'Grok 4.3' },
  ]},
];

// ── omniroute (beta) — omniroute gateway model ids ───────────────────────────
// `auto/*` are smart-routing combos (safest); `aug/*` are specific tuned models.
const MODEL_PROVIDERS_OMNIROUTE: ModelProviderGroup[] = [
  { provider: 'Default', models: [
    { id: '', label: 'Auto' },
  ]},
  { provider: 'Auto (Smart Routing)', models: [
    { id: 'auto/best-reasoning', label: 'Best Reasoning', recommended: true },
    { id: 'auto/smart', label: 'Smart' },
    { id: 'auto/best-fast', label: 'Best Fast' },
    { id: 'auto/best-chat', label: 'Best Chat' },
    { id: 'auto/best-coding', label: 'Best Coding' },
  ]},
  { provider: 'Anthropic (Claude)', models: [
    { id: 'auto/claude-sonnet', label: 'Claude Sonnet (auto)', recommended: true },
    { id: 'auto/claude-opus', label: 'Claude Opus (auto)' },
    { id: 'aug/claude-sonnet-4.6-thinking', label: 'Claude Sonnet 4.6 (thinking)' },
    { id: 'aug/claude-opus-4.6', label: 'Claude Opus 4.6' },
    { id: 'aug/claude-haiku-4.5', label: 'Claude Haiku 4.5' },
  ]},
  { provider: 'OpenAI (GPT)', models: [
    { id: 'aug/gpt-5.5-high', label: 'GPT-5.5 (high)' },
    { id: 'aug/gpt-5.5-medium', label: 'GPT-5.5 (medium)' },
    { id: 'aug/gpt-5.4-high', label: 'GPT-5.4 (high)' },
  ]},
  { provider: 'Google (Gemini)', models: [
    { id: 'auto/gemini', label: 'Gemini (auto)' },
    { id: 'aug/gemini-3.1-pro', label: 'Gemini 3.1 Pro' },
    { id: 'aug/gemini-3.0-flash', label: 'Gemini 3.0 Flash' },
  ]},
  { provider: 'DeepSeek', models: [
    { id: 'tllm/deepseek_v4', label: 'DeepSeek V4' },
  ]},
];

// Which LLM gateway this build targets: 'openrouter' (production, per-user keys)
// or 'omniroute' (beta, shared key). Defaults to omniroute.
export const LLM_GATEWAY: 'openrouter' | 'omniroute' =
  process.env.NEXT_PUBLIC_LLM_GATEWAY === 'openrouter' ? 'openrouter' : 'omniroute';

// Model selection is LOCKED on the beta (omniroute) gateway — beta users run the
// deployment's default model and cannot switch. On OpenRouter (production) every
// model is selectable (billed to the user's own credits).
export const MODEL_SELECTION_LOCKED = LLM_GATEWAY !== 'openrouter';

// Active list for this build. Defaults to omniroute (beta); production builds set
// NEXT_PUBLIC_LLM_GATEWAY=openrouter.
export const MODEL_PROVIDERS: ModelProviderGroup[] =
  LLM_GATEWAY === 'openrouter' ? MODEL_PROVIDERS_OPENROUTER : MODEL_PROVIDERS_OMNIROUTE;

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

  // ── Per-symbol session persistence ────────────────────────────────────
  /** One persisted analysis session per symbol. The top-level fields above are
   *  a live mirror of `activeViewKey`'s session. */
  sessionsByKey: Record<string, QuantSession>;
  /** The session key (symbol::profile) currently displayed in the terminal —
   *  its session mirrors to the flat top-level fields. */
  activeViewKey: string | null;
  /** The session key (symbol::profile) whose analysis run is currently
   *  streaming — stream events are routed to THIS session, not the viewed one,
   *  so a background run keeps accumulating while the user looks elsewhere. */
  _streamingKey: string | null;
  /** Maps a run's thread id to its session key, so EVERY event of that run (and
   *  a later /resume, which reuses the thread id) routes back to the right
   *  session even when multiple symbols/profiles run concurrently. */
  _threadToKey: Record<string, string>;
  /** Load the (symbol, profile) session into the active view (snapshotting the
   *  outgoing one first). Call on every active-symbol OR active-profile change. */
  activateSymbolSession: (symbol: string, profile: string) => void;

  // ── Model provider selection ──────────────────────────────────────────
  /** The model id sent to the backend for analysis and Q&A ('' = deployment
   *  default). Persisted across the session so the choice sticks. */
  selectedModel: string;
  setSelectedModel: (modelId: string) => void;

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
  /** (internal) Arm/re-arm the activity-based stall watchdog for a run key.
   *  Called on run start and on every stream event; only fires after a full
   *  idle window with no events. */
  _armStreamWatchdog: (runKey: string) => void;
  resetTerminal: () => void;
  /** Cancel the active deep-quant run. Aborts the Rust proxy task, signals
   *  the Python agent to stop, and resets the session to idle immediately. */
  cancelAnalysis: () => Promise<void>;

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

// ── Deep-quant stream watchdog (activity-based, not run-total) ────────────
// A long-but-healthy agent run can legitimately exceed 2 minutes end-to-end
// while it streams reasoning/tool events the whole time. A fixed
// 120s-from-start timeout falsely errored those runs even though the backend
// was actively working. Instead we arm a per-run watchdog that is RESET on
// every stream event: it only fires when NO event has arrived for the idle
// window — i.e. the SSE stream is genuinely stalled/dead, not merely slow.
const STREAM_IDLE_TIMEOUT_MS = 120_000;  // fire only after this much silence
const streamWatchdogs = new Map<string, ReturnType<typeof setTimeout>>();

const clearStreamWatchdog = (runKey: string) => {
  const t = streamWatchdogs.get(runKey);
  if (t !== undefined) {
    clearTimeout(t);
    streamWatchdogs.delete(runKey);
  }
};

// ── Cancelled thread guard ────────────────────────────────────────────────
// Thread IDs that have been cancelled. handleStreamEvent ignores events for
// these threads; the id is dropped on RUN_FINISHED/ERROR so the set stays small.
const cancelledThreads = new Set<string>();

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
  // Return the first finite numeric conviction found, or `undefined` when none
  // was emitted. RUN_FINISHED text-extraction must NOT reintroduce a `75`
  // default — an absent conviction stays undefined so the UI renders "—" (R1.7).
  const asScore = (...vals: unknown[]): number | undefined => {
    for (const v of vals) {
      if (typeof v === 'number' && Number.isFinite(v)) return v;
    }
    return undefined;
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

/**
 * Merge a plan scraped from the model's closing monologue (`scraped`) into the
 * plan committed by the `DECISION` event (`committed`), without ever losing the
 * committed decision's directional identity.
 *
 * Why this exists: `graph.py` instructs the model to restate a final JSON
 * conviction block AFTER `declare_trade` succeeds. That block carries only
 * `conviction_score` / `setup_validation` / `execution_plan` — it has no
 * `action` and no `execution_levels`. `RUN_FINISHED` used to assign the scraped
 * plan straight over `finalTrade`/`aiPlan`, which stripped `action: 'BUY'` and
 * the validated levels off an already-committed trade. `isActionableTrade` then
 * failed and the UI rendered "Stand Aside — No Trade" for every run, no matter
 * what the backend validated. (The overwrite predates the commit that added
 * `action` to the DECISION plan and was never updated for it.)
 *
 * Rules: the committed decision wins on every field it actually carries —
 * `action`, `opportunity_tier` and `execution_levels` are taken from it
 * exclusively and are never sourced from scraped text. The scrape may only
 * fill gaps (a missing conviction, empty prose), which is its useful role since
 * `DECISION` often ships an empty `execution_plan`. Pure; total over nulls.
 */
export function mergeFinalPlan(
  committed: AiExecutionPlan | null,
  scraped: AiExecutionPlan | null,
): AiExecutionPlan | null {
  if (!committed) return scraped;
  if (!scraped) return committed;
  return {
    conviction_score: committed.conviction_score ?? scraped.conviction_score,
    setup_validation: committed.setup_validation || scraped.setup_validation,
    execution_plan: committed.execution_plan || scraped.execution_plan,
    // Directional identity is decided by the backend, never by scraped prose.
    action: committed.action,
    opportunity_tier: committed.opportunity_tier,
    execution_levels: committed.execution_levels,
  };
}

// ── Store ───────────────────────────────────────────────────────────────

// ── Per-symbol analysis session ─────────────────────────────────────────
// The Deep Quant terminal state used to be a single slot, so switching the
// active chart symbol (or starting a new run) wiped the reasoning transcript,
// tool calls, decision, and Q&A of the symbol you were on. We now keep one
// QuantSession PER SYMBOL in `sessionsByKey`, route streaming events to the
// symbol whose run they belong to (`_streamingKey`, resolved from the run's
// thread id), and mirror the currently-VIEWED symbol's session (`activeViewKey`)
// into the flat top-level fields the UI already reads. Switching away and back
// therefore restores the full analysis for each symbol, and a background run for
// symbol A keeps accumulating into A's session even while you view symbol B.
export interface ReasoningStep {
  id: string;
  type: string;
  content: string;
  timestamp: number;
  toolName?: string;
  args?: Record<string, unknown>;
}

export interface QuantSession {
  sessionStatus: 'idle' | 'running' | 'watching' | 'complete' | 'error';
  reasoningSteps: ReasoningStep[];
  finalTrade: AiExecutionPlan | null;
  aiPlan: AiExecutionPlan | null;
  analysisError: string | null;
  isAnalyzing: boolean;
  _pendingToolCalls: number;
  _runFinishedProcessed: boolean;
  currentThreadId: string | null;
  qaMessages: QaChatMessage[];
  qaStatus: 'idle' | 'streaming';
  _qaRunFinishedProcessed: boolean;
  /** 'FIND' or 'VERIFY' — the mode the run was launched in. */
  mode: 'FIND' | 'VERIFY';
  updatedAt: number;
}

function blankSession(): QuantSession {
  return {
    sessionStatus: 'idle',
    reasoningSteps: [],
    finalTrade: null,
    aiPlan: null,
    analysisError: null,
    isAnalyzing: false,
    _pendingToolCalls: 0,
    _runFinishedProcessed: false,
    currentThreadId: null,
    qaMessages: [],
    qaStatus: 'idle',
    _qaRunFinishedProcessed: false,
    mode: 'FIND',
    updatedAt: Date.now(),
  };
}

// Project a session into the flat top-level fields the UI components read.
function projectSession(s: QuantSession) {
  return {
    sessionStatus: s.sessionStatus,
    reasoningSteps: s.reasoningSteps,
    finalTrade: s.finalTrade,
    aiPlan: s.aiPlan,
    analysisError: s.analysisError,
    isAnalyzing: s.isAnalyzing,
    _pendingToolCalls: s._pendingToolCalls,
    _runFinishedProcessed: s._runFinishedProcessed,
    currentThreadId: s.currentThreadId,
    qaMessages: s.qaMessages,
    qaStatus: s.qaStatus,
    _qaRunFinishedProcessed: s._qaRunFinishedProcessed,
  };
}

function _newStepId(): string {
  return `step-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

// A session is keyed by BOTH the symbol AND the workspace profile, because the
// same symbol analyzed in INTRADAY vs SWING vs INVESTOR vs F&O is a distinct
// analysis. So `TMPV::INTRADAY` and `TMPV::FNO` persist independently, and
// switching either the symbol or the mode restores the matching session.
function _sessionKey(symbol: string | null | undefined, profile: string | null | undefined): string {
  const sym = (symbol || '').toUpperCase();
  const prof = (profile || 'INTRADAY').toUpperCase();
  return `${sym}::${prof}`;
}

// Pure reducer: apply one SSE stream event to a session and return the new
// session. This is the per-symbol equivalent of the old inline switch — it reads
// and writes ONLY the passed session, so an event can be routed to the correct
// symbol's session regardless of which symbol is currently on screen.
function applyStreamEvent(session: QuantSession, payload: StreamEventPayload): QuantSession {
  const event = payload.event;
  const data = payload.data;

  switch (event) {
    case 'RUN_STARTED': {
      const startedThreadId = data?.thread_id;
      const base: QuantSession = startedThreadId
        ? { ...session, currentThreadId: startedThreadId }
        : { ...session };
      if (session.sessionStatus === 'watching') {
        const resumeStep: ReasoningStep = {
          id: _newStepId(),
          type: 'message',
          content: '\n---\n### Resuming Analysis — Fresh Market Data\nThe watcher woke this run. Re-checking the setup with the latest data...\n---\n',
          timestamp: Date.now(),
        };
        return {
          ...base,
          sessionStatus: 'running',
          isAnalyzing: true,
          analysisError: null,
          _pendingToolCalls: 0,
          _runFinishedProcessed: false,
          // Drop the PREVIOUS leg's committed decision. This resume branch keeps
          // the reasoning transcript (that's its whole point), but the decision
          // itself is now stale: the watcher woke precisely because the market
          // moved. `DECISION` is first-write-wins for within-leg idempotency
          // (reattach can replay frames), so leaving the old plan here made the
          // stale leg-1 stand-aside HOLD swallow the real BUY the resumed leg
          // declares — the "always HOLD" symptom, on the heartbeat path that
          // dominates shipped builds. Clearing here keeps both properties.
          finalTrade: null,
          aiPlan: null,
          reasoningSteps: [...session.reasoningSteps, resumeStep],
          updatedAt: Date.now(),
        };
      }
      return {
        ...base,
        sessionStatus: 'running',
        reasoningSteps: [],
        finalTrade: null,
        aiPlan: null,
        isAnalyzing: true,
        analysisError: null,
        _pendingToolCalls: 0,
        _runFinishedProcessed: false,
        updatedAt: Date.now(),
      };
    }
    case 'REASONING':
    case 'TEXT_MESSAGE': {
      const content = data?.content || '';
      if (!content) return session;

      const steps = [...session.reasoningSteps];
      const lastIdx = steps.length - 1;

      if (lastIdx >= 0 && steps[lastIdx].type === 'message') {
        const lastStep = steps[lastIdx];
        
        // Fast O(1) check to avoid catastrophic regex backtracking on long streams
        const isJsonDecision = lastStep.content.trim().startsWith('{');

        if (!isJsonDecision) {
          steps[lastIdx] = {
            ...lastStep,
            content: lastStep.content + content,
            timestamp: Date.now(),
          };
          return {
            ...session,
            reasoningSteps: steps,
            updatedAt: Date.now(),
          };
        }
      }

      return {
        ...session,
        reasoningSteps: [
          ...session.reasoningSteps,
          { id: _newStepId(), type: 'message', content, timestamp: Date.now() },
        ],
        updatedAt: Date.now(),
      };
    }
    case 'BEST_CURRENT_READ': {
      const bias = (data?.bias as string) || 'neutral';
      const why = (data?.why_standing_aside as string) || '';
      const levelsRaw = (data?.levels && typeof data.levels === 'object') ? (data.levels as Record<string, unknown>) : {};
      const levelStr = Object.entries(levelsRaw)
        .filter(([, v]) => typeof v === 'number' && Number.isFinite(v as number))
        .map(([k, v]) => `${k}: ${v}`)
        .join(' · ');
      const lines = [
        `**📍 Best Current Read — bias: ${bias}**`,
        levelStr ? `Key levels: ${levelStr}` : '',
        why ? `Read: ${why}` : '',
      ].filter(Boolean);
      return {
        ...session,
        reasoningSteps: [...session.reasoningSteps, { id: _newStepId(), type: 'message', content: lines.join('\n'), timestamp: Date.now() }],
        updatedAt: Date.now(),
      };
    }
    case 'VERIFICATION_STEP': {
      const check = (data?.check as string) || (data?.tool as string) || 'check';
      const outcome = (data?.outcome as string) || (data?.status as string) || '';
      const detail = (data?.content as string) || (data?.detail as string) || '';
      const body = [outcome, detail].filter(Boolean).join(' — ');
      return {
        ...session,
        reasoningSteps: [...session.reasoningSteps, { id: _newStepId(), type: 'message', content: `**Verification — ${check}${body ? `: ${body}` : ''}**`, timestamp: Date.now() }],
        updatedAt: Date.now(),
      };
    }
    case 'DECISION': {
      // Normalize case at the single entry point. Python threads the model's raw
      // action string through verbatim (stream_events.py builds the payload with
      // `decision.get("action")`), while it normalizes separately when deciding
      // whether to attach execution_levels. So a lowercase "sell" arrives WITH
      // levels and passes isActionableTrade (which upper-cases) — but downstream
      // consumers compare raw (`aiPlan.action === 'SELL'` in DeepQuantPanel and
      // ActionableTradePlan), so it would fall through to BUY and place a
      // wrong-direction order. Upper-casing here fixes every consumer at once.
      const actionRaw = (data?.action as string) || (data?.decision as string) || '';
      const action = typeof actionRaw === 'string' ? actionRaw.trim().toUpperCase() : '';
      const convictionRaw = data?.conviction_score ?? data?.conviction;
      const conviction = typeof convictionRaw === 'number' && Number.isFinite(convictionRaw) ? convictionRaw : undefined;
      const rationale = (data?.rationale as string) || (data?.setup_validation as string) || (data?.thesis as string) || '';
      const executionPlan = (data?.execution_plan as string) || '';
      // Carry the committed decision's action / tier / validated levels through
      // to the plan so the UI can gate on them (R1.1). `execution_levels` is
      // only ever the structured object the Python payload threads for a
      // directional trade — never synthesized here.
      const tier = (data?.opportunity_tier as string) || undefined;
      const levels = (data?.execution_levels && typeof data.execution_levels === 'object')
        ? (data.execution_levels as ExecutionLevels)
        : undefined;
      const summaryLines = [
        `**Decision${action ? `: ${action}` : ''}**`,
        conviction !== undefined ? `Conviction: ${conviction}/100` : '',
        rationale ? `Rationale: ${rationale}` : '',
        executionPlan ? `Plan: ${executionPlan}` : '',
      ].filter(Boolean);
      // Leave `conviction_score` undefined when the payload omits it — no `?? 75`
      // default (R1.7). Build a plan whenever we have any decision signal.
      const decisionPlan: AiExecutionPlan | null = (conviction !== undefined || rationale || executionPlan || action)
        ? {
            conviction_score: conviction,
            setup_validation: rationale,
            execution_plan: executionPlan,
            action: (action as AiExecutionPlan['action']) || undefined,
            opportunity_tier: tier,
            execution_levels: levels,
          }
        : null;
      return {
        ...session,
        reasoningSteps: [...session.reasoningSteps, { id: _newStepId(), type: 'message', content: summaryLines.join('\n'), timestamp: Date.now() }],
        finalTrade: session.finalTrade ?? decisionPlan,
        aiPlan: session.aiPlan ?? decisionPlan,
        updatedAt: Date.now(),
      };
    }
    case 'TOOL_CALL_START': {
      const toolName = data?.tool || '';
      if (!toolName) return session;
      const isWatching = toolName === 'watch_price_condition';
      return {
        ...session,
        reasoningSteps: [...session.reasoningSteps, { id: _newStepId(), type: 'tool_start', toolName, args: data?.args, content: `> Executing tool: ${toolName}...`, timestamp: Date.now() }],
        sessionStatus: isWatching ? 'watching' : session.sessionStatus,
        _pendingToolCalls: session._pendingToolCalls + 1,
        updatedAt: Date.now(),
      };
    }
    case 'TOOL_CALL_END': {
      const toolName = data?.tool || '';
      if (!toolName) return session;
      return {
        ...session,
        reasoningSteps: [...session.reasoningSteps, { id: _newStepId(), type: 'tool_end', toolName, content: `✔ Tool ${toolName} completed successfully.`, timestamp: Date.now() }],
        _pendingToolCalls: Math.max(0, session._pendingToolCalls - 1),
        updatedAt: Date.now(),
      };
    }
    case 'RUN_FINISHED': {
      if (session._runFinishedProcessed) return session;
      let s = session;
      if (s._pendingToolCalls > 0) s = { ...s, _pendingToolCalls: 0 };
      const accumulatedText = s.reasoningSteps.filter((step) => step.type === 'message').map((step) => step.content).join('');
      const tradePlan = extractFinalTrade(accumulatedText);
      if (data?.status === 'paused') {
        return { ...s, sessionStatus: 'watching', isAnalyzing: false, _runFinishedProcessed: true, updatedAt: Date.now() };
      }
      return {
        ...s,
        sessionStatus: s.sessionStatus === 'error' ? 'error' : 'complete',
        // Enrich, never downgrade: a committed BUY/SELL keeps its action and
        // validated levels; the scraped block only fills fields the decision
        // left empty. See mergeFinalPlan.
        finalTrade: mergeFinalPlan(s.finalTrade, tradePlan),
        aiPlan: mergeFinalPlan(s.aiPlan, tradePlan),
        isAnalyzing: false,
        _runFinishedProcessed: true,
        updatedAt: Date.now(),
      };
    }
    case 'ERROR': {
      const errorMsg = data?.error || 'Unknown streaming error';
      return { ...session, sessionStatus: 'error', isAnalyzing: false, analysisError: errorMsg, _runFinishedProcessed: true, updatedAt: Date.now() };
    }
    default:
      return session;
  }
}

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

  // ── Per-symbol session persistence ───────────────────────────────
  sessionsByKey: {},
  activeViewKey: null,
  _streamingKey: null,
  _threadToKey: {},

  // ── Model provider selection ─────────────────────────────────────
  selectedModel: '',
  setSelectedModel: (modelId: string) => set({ selectedModel: modelId }),

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

    // Read the active timeframe AND workspace profile up front so we can key the
    // session by BOTH symbol and profile (INTRADAY / SWING / INVESTOR / FNO).
    // The same symbol in two profiles is a distinct analysis, so each persists
    // independently. The profile also tells the agent which data domain to lead
    // with; the F&O expiry is only meaningful on an FNO run.
    const { useTradeStore } = await import('./useTradeStore');
    const activeTimeframe = useTradeStore.getState().activeTimeframe;
    const activeProfile = useTradeStore.getState().activeProfile;
    const fnoExpiry = useTradeStore.getState().fnoExpiry;
    const runKey = _sessionKey(symbol, activeProfile);
    console.log(`[QuantStore] ▶ Deep analysis START key=${runKey} mode=${activeMode} tf=${activeTimeframe} ts=${new Date().toISOString()}`);

    // ── RESEARCH SKU gate (compliance blocker P1) ─────────────────────────────
    // FIND produces a directional recommendation, which is regulated research;
    // VERIFY only validates numbers the user supplied, so it stays on TERMINAL.
    // Short-circuit BEFORE the IPC invoke so an unentitled user triggers no
    // analysis. This is defence in depth and a UX affordance — the gate that
    // actually holds is server-side in the agent's `entitlements.py`.
    if (!canRunAgentMode(activeMode)) {
      console.warn(`[QuantStore] ⛔ ${activeMode} blocked: RESEARCH SKU required`);
      set((s) => {
        const sess = s.sessionsByKey[runKey] ?? blankSession();
        const locked: QuantSession = {
          ...sess,
          isAnalyzing: false,
          sessionStatus: 'error',
          analysisError: RESEARCH_LOCKED_MESSAGE,
          updatedAt: Date.now(),
        };
        return {
          activeViewKey: runKey,
          sessionsByKey: { ...s.sessionsByKey, [runKey]: locked },
          ...projectSession(locked),
        };
      });
      return;
    }

    // Initialize a FRESH running session under this (symbol, profile) key. It
    // becomes both the streaming target (so its events route here) and the
    // active view. Every other session — other symbols AND other profiles of
    // this symbol — is left untouched, so no in-flight run is ever wiped. The
    // flat top-level fields mirror this new session.
    const freshSession: QuantSession = {
      ...blankSession(),
      sessionStatus: 'running',
      isAnalyzing: true,
      mode: activeMode === 'VERIFY' ? 'VERIFY' : 'FIND',
      updatedAt: Date.now(),
    };
    set((state) => ({
      _streamingKey: runKey,
      activeViewKey: runKey,
      sessionsByKey: { ...state.sessionsByKey, [runKey]: freshSession },
      ...projectSession(freshSession),
      // multi-TF patterns are cached per-symbol separately; clear the view while
      // the parallel fetch below refreshes them.
      multiTfPatterns: null,
      isFetchingPatterns: true,
    }));

    // Trigger multi-timeframe chart patterns fetch in parallel (non-blocking).
    get().fetchMultiTfPatterns(symbol);

    // Refresh the frontend sentiment panel in parallel — do NOT await it. The
    // agent fetches its own news via get_news_context, so blocking the run start
    // on the frontend sentiment refresh only delayed the first SSE events from
    // appearing after the user hit "Find Quant Trade". Fire-and-forget instead so
    // the agent is invoked immediately and the glass-box transcript streams in
    // with minimal latency.
    get().refreshSentimentForSymbol(symbol).catch(() => {
      console.warn('[QuantStore] Sentiment refresh failed, continuing with analysis...');
    });

    console.log(`[QuantStore] → AI context: timeframe=${activeTimeframe} profile=${activeProfile} fnoExpiry=${fnoExpiry || '(nearest)'}`);

    try {
      console.log(`[QuantStore] → invoking 'run_deep_quant_agent' (Tauri IPC)…`);
      const tInvoke = (typeof performance !== 'undefined' ? performance.now() : Date.now());

      const threadId = await tauriInvoke<string>(
        'run_deep_quant_agent',
        {
          symbol,
          mode: activeMode,
          timeframe: activeTimeframe,
          profile: activeProfile,
          fnoExpiry,
          // Beta (locked) always uses the deployment default model; production
          // sends the user's selection.
          model: MODEL_SELECTION_LOCKED ? null : (get().selectedModel || null),
          manualTrade: manualTrade ? {
            side: manualTrade.side,
            entry: manualTrade.entry,
            stop_loss: manualTrade.stopLoss,
            take_profit: manualTrade.takeProfit,
            user_analysis: manualTrade.userAnalysis
          } : null,
          // Authenticated user id → the droplet resolves this user's OpenRouter
          // key from the backend internal endpoint for the run.
          userId: useAuthStore.getState().user?.id ?? null
        }
      );

      // Write the thread_id onto the session immediately so cancelAnalysis works
      // even before the first RUN_STARTED SSE event arrives.
      set((s) => {
        const sess = s.sessionsByKey[runKey] ?? blankSession();
        const updated = { ...sess, currentThreadId: threadId };
        return {
          sessionsByKey: { ...s.sessionsByKey, [runKey]: updated },
          ...(s.activeViewKey === runKey ? { currentThreadId: threadId } : {}),
        };
      });

      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      console.log(
        `[QuantStore] ✔ Deep analysis triggered symbol=${symbol} ` +
        `ipc_ms=${Math.round(tDone - tInvoke)} total_ms=${Math.round(tDone - t0)}`
      );

      // Bug 2 fix: Activity-based safety watchdog. Rather than a fixed
      // 120s-from-start timeout (which falsely errored long-but-healthy runs
      // that were still streaming), arm a watchdog that is re-armed on every
      // stream event via `_armStreamWatchdog`. It only fires when the stream
      // has been SILENT for the idle window — a genuinely stalled/unreachable
      // agent — and only touches THIS run's session (by key), never whichever
      // session happens to be on screen.
      get()._armStreamWatchdog(runKey);
    } catch (err) {
      const tDone = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const message = err instanceof Error ? err.message : String(err);
      console.error(
        `[QuantStore] ✘ Deep analysis FAIL key=${runKey} ` +
        `total_ms=${Math.round(tDone - t0)} message=${message}`
      );
      // Error ONLY this run's session (by key), mirroring to the view if active.
      set((s) => {
        const sess = s.sessionsByKey[runKey] ?? blankSession();
        const errored: QuantSession = { ...sess, isAnalyzing: false, sessionStatus: 'error', analysisError: message, updatedAt: Date.now() };
        return {
          sessionsByKey: { ...s.sessionsByKey, [runKey]: errored },
          ...(s.activeViewKey === runKey ? projectSession(errored) : {}),
        };
      });
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

  activateSymbolSession: (symbol: string, profile: string) => {
    const st = get();
    const key = _sessionKey(symbol, profile);
    if (st.activeViewKey === key) return;
    const next: Record<string, QuantSession> = { ...st.sessionsByKey };
    // Snapshot the outgoing session's live top-level state so any Q&A or late
    // updates made while it was on screen are preserved.
    if (st.activeViewKey) {
      const prev = next[st.activeViewKey] ?? blankSession();
      next[st.activeViewKey] = {
        ...prev,
        sessionStatus: st.sessionStatus,
        reasoningSteps: st.reasoningSteps,
        finalTrade: st.finalTrade,
        aiPlan: st.aiPlan,
        analysisError: st.analysisError,
        isAnalyzing: st.isAnalyzing,
        _pendingToolCalls: st._pendingToolCalls,
        _runFinishedProcessed: st._runFinishedProcessed,
        currentThreadId: st.currentThreadId,
        qaMessages: st.qaMessages,
        qaStatus: st.qaStatus,
        _qaRunFinishedProcessed: st._qaRunFinishedProcessed,
        updatedAt: Date.now(),
      };
    }
    const target = next[key] ?? blankSession();
    // Restore the symbol's cached multi-TF patterns from the module cache
    // (keyed by symbol; profile-independent) so the patterns panel matches.
    const cachedPatterns = multiTfCache.get(symbol.toUpperCase())?.data ?? null;
    set({
      sessionsByKey: next,
      activeViewKey: key,
      ...projectSession(target),
      multiTfPatterns: cachedPatterns,
      isFetchingPatterns: false,
    });
  },

  _armStreamWatchdog: (runKey: string) => {
    // Reset any pending watchdog for this run, then start a fresh idle timer.
    clearStreamWatchdog(runKey);
    const timer = setTimeout(() => {
      streamWatchdogs.delete(runKey);
      const state = get();
      const sess = state.sessionsByKey[runKey];
      // Only trip if the run is STILL running and no event re-armed us — i.e.
      // the SSE stream has been silent for the whole idle window.
      if (sess && sess.isAnalyzing && sess.sessionStatus === 'running') {
        console.warn(`[QuantStore] ⚠ Stream stalled: no events for ${Math.round(STREAM_IDLE_TIMEOUT_MS / 1000)}s on ${runKey}. Auto-resetting.`);
        const timedOut: QuantSession = {
          ...sess,
          isAnalyzing: false,
          sessionStatus: 'error',
          analysisError: `The agent stream stalled — no activity for ${Math.round(STREAM_IDLE_TIMEOUT_MS / 1000)}s. The Python agent server may be unreachable or the LLM request stalled. Please retry.`,
          updatedAt: Date.now(),
        };
        set((s) => ({
          sessionsByKey: { ...s.sessionsByKey, [runKey]: timedOut },
          ...(s.activeViewKey === runKey ? projectSession(timedOut) : {}),
        }));
      }
    }, STREAM_IDLE_TIMEOUT_MS);
    streamWatchdogs.set(runKey, timer);
  },

  handleStreamEvent: (payload: StreamEventPayload) => {
    if (!payload || !payload.event) return;

    const event = payload.event;
    const data = payload.data;

    // Drop events for cancelled runs; clean up the guard on terminal events.
    const incomingThreadId = data?.thread_id;
    if (incomingThreadId && cancelledThreads.has(incomingThreadId)) {
      if (event === 'RUN_FINISHED' || event === 'ERROR') {
        cancelledThreads.delete(incomingThreadId);
      }
      return;
    }

    console.log(`[QuantStore] 📥 Stream event: ${event}`, data);

    // ── Route this event to the SESSION KEY its run belongs to ─────────────
    // Every event now carries the run's thread_id (backend stamps them all), so
    // we resolve the session key from the thread→key map first. That makes
    // concurrent runs across different symbols/profiles fully independent — each
    // run's events land in its own (symbol::profile) session regardless of what
    // is on screen. Fallbacks: the current streaming key, then the viewed key.
    // Events are only mirrored to the flat top-level fields when the run is the
    // one currently displayed, so a background run keeps filling its session
    // while the user looks at (or analyzes) something else.
    const st = get();
    const threadId = data?.thread_id;
    let runKey: string | null = null;
    if (threadId && st._threadToKey[threadId]) {
      runKey = st._threadToKey[threadId];
    } else if (event === 'RUN_STARTED') {
      // First event of a run: bind its thread to the current streaming/viewed key.
      runKey = st._streamingKey || st.activeViewKey;
    } else {
      runKey = st._streamingKey || st.activeViewKey;
    }
    if (!runKey) return;

    const current = st.sessionsByKey[runKey] ?? blankSession();
    const nextSession = applyStreamEvent(current, payload);
    const threadMapUpdate = (threadId && !st._threadToKey[threadId])
      ? { [threadId]: runKey }
      : {};

    // Watchdog: an event = the stream is alive. Clear it once the run reaches a
    // terminal state; otherwise re-arm the idle timer so a long-but-active run
    // is never falsely timed out.
    if (nextSession.sessionStatus === 'complete' || nextSession.sessionStatus === 'error') {
      clearStreamWatchdog(runKey);
    } else {
      get()._armStreamWatchdog(runKey);
    }

    set((state) => ({
      _streamingKey: runKey,
      sessionsByKey: { ...state.sessionsByKey, [runKey as string]: nextSession },
      _threadToKey: { ...state._threadToKey, ...threadMapUpdate },
      // Mirror to the flat top-level fields only when the run's session is the
      // one currently on screen.
      ...(runKey === state.activeViewKey ? projectSession(nextSession) : {}),
    }));

    // ── Discipline counters (compliance blocker P6) ─────────────────────────
    // Count this run against the discipline statistics that replaced the removed
    // performance metrics. Fires on the null→non-null `finalTrade` transition,
    // which the DECISION reducer makes exactly once per session
    // (`finalTrade: session.finalTrade ?? decisionPlan`), so a duplicate or
    // replayed DECISION frame cannot double-count. Best-effort: a failure here
    // must never break a live run.
    if (current.finalTrade == null && nextSession.finalTrade != null) {
      void (async () => {
        try {
          const { useTradeStore } = await import('./useTradeStore');
          useTradeStore.getState().recordSetupAudit({
            mode: nextSession.mode,
            actionable: isActionableTrade(nextSession.finalTrade),
          });
        } catch (err) {
          console.warn('[QuantStore] discipline stat not recorded:', err);
        }
      })();
    }
    return;

  },

  resetTerminal: () => {
    // Reset ONLY the active view symbol's session (and its top-level mirror);
    // other symbols' persisted sessions are preserved.
    const st = get();
    const fresh = blankSession();
    const sym = st.activeViewKey;
    set((state) => ({
      ...(sym ? { sessionsByKey: { ...state.sessionsByKey, [sym]: fresh } } : {}),
      ...projectSession(fresh),
      multiTfPatterns: null,
      isFetchingPatterns: false,
    }));
  },

  cancelAnalysis: async () => {
    const st = get();
    const runKey = st._streamingKey || st.activeViewKey;
    if (!runKey) return;
    const sess = st.sessionsByKey[runKey];
    const threadId = sess?.currentThreadId;

    clearStreamWatchdog(runKey);
    if (threadId) {
      cancelledThreads.add(threadId);
      try {
        await tauriInvoke('cancel_deep_quant_agent', { threadId });
      } catch {
        // best-effort
      }
    }

    const cancelStep = {
      id: `cancel-${Date.now()}`,
      type: 'message' as const,
      content: '⏹ Analysis cancelled by user.',
      timestamp: Date.now(),
    };
    set((s) => {
      const existing = s.sessionsByKey[runKey] ?? blankSession();
      const cancelled: QuantSession = {
        ...existing,
        isAnalyzing: false,
        sessionStatus: 'idle',
        reasoningSteps: [...existing.reasoningSteps, cancelStep],
        _runFinishedProcessed: true,
        updatedAt: Date.now(),
      };
      return {
        _streamingKey: s._streamingKey === runKey ? null : s._streamingKey,
        sessionsByKey: { ...s.sessionsByKey, [runKey]: cancelled },
        ...(s.activeViewKey === runKey ? projectSession(cancelled) : {}),
      };
    });
  },

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

    // ── RESEARCH SKU gate (compliance blocker P1) ─────────────────────────────
    // Q&A elaborates a committed recommendation, so it is a RESEARCH surface.
    // Refuse before the IPC invoke; render the refusal as an ordinary assistant
    // turn so the transcript stays coherent. Server-side `entitlements.py` is
    // the authoritative check.
    if (!canRunAgentMode('QA')) {
      console.warn('[QuantStore] ⛔ Q&A blocked: RESEARCH SKU required');
      const lockStamp = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      set((state) => ({
        qaMessages: [
          ...state.qaMessages,
          { id: `qa-user-${lockStamp}`, role: 'user', content: trimmed },
          {
            id: `qa-asst-${lockStamp}`,
            role: 'assistant',
            content: RESEARCH_LOCKED_MESSAGE,
            error: true,
          },
        ],
      }));
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
      await tauriInvoke<void>('ask_trade_question', { threadId, question: trimmed, model: MODEL_SELECTION_LOCKED ? null : (get().selectedModel || null), userId: useAuthStore.getState().user?.id ?? null });
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

    // Only push patterns into the on-screen fields when the fetched symbol is
    // the one currently being viewed — so a background run's pattern fetch for
    // symbol A can never overwrite the patterns you see while viewing symbol B.
    // The cache is always populated regardless, so returning to A shows them.
    const isActiveSymbol = () => {
      const key = get().activeViewKey;
      return !!key && key.split('::')[0] === sym;
    };

    // Serve a fresh cache hit instantly — no DB fan-out, no re-detection.
    const cached = multiTfCache.get(sym);
    if (cached && now - cached.fetchedAt < MULTI_TF_TTL_MS) {
      console.log(`[QuantStore] ✔ MultiTF CACHE HIT symbol=${sym} age=${Math.round((now - cached.fetchedAt) / 1000)}s`);
      if (isActiveSymbol()) set({ multiTfPatterns: cached.data, isFetchingPatterns: false });
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
    if (isActiveSymbol()) {
      set((state) => ({ isFetchingPatterns: true, multiTfPatterns: cached?.data ?? state.multiTfPatterns ?? null }));
    }
    try {
      const data = await tauriInvoke<MultiTfChartPatterns[]>('get_multi_timeframe_chart_patterns', { symbol });
      multiTfCache.set(sym, { data, fetchedAt: Date.now() });
      console.log(`[QuantStore] ✔ fetchMultiTfPatterns completed symbol=${sym} (${data.length} timeframes)`);
      if (isActiveSymbol()) set({ multiTfPatterns: data, isFetchingPatterns: false });
    } catch (err) {
      console.error(`[QuantStore] ✘ fetchMultiTfPatterns failed for ${sym}:`, err);
      if (isActiveSymbol()) set({ isFetchingPatterns: false, multiTfPatterns: multiTfCache.get(sym)?.data ?? [] });
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
