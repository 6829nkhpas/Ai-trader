import { create } from 'zustand';
import type { DataRange } from '../utils/chartTypes';

export type TradeProfile = 'INTRADAY' | 'SWING' | 'INVESTOR';

/**
 * Chart timeframe options. The backend predictive ML engine operates
 * exclusively on 10-minute candles (market.ohlc.10m), making '10m' the
 * primary timeframe for all AI overlays (Ghost Line, confidence scores).
 */
export type ChartTimeframe =
  | '1m' | '2m' | '3m' | '4m' | '5m'
  | '10m' | '15m' | '30m' | '75m' | '125m'
  | '1h' | '1H' | '2h' | '3h' | '4h'
  | '1D' | '1W' | '1M';

type BackendAction = 'BUY' | 'SELL' | 'HOLD';

export interface AggregatedDecision {
  timestamp_ms: number;
  symbol: string;
  action_type: BackendAction;
  final_conviction_score: number;
  reasoning?: string;
  technical_weight_used: number;
  sentiment_weight_used: number;
  price?: number;
}

interface BackendDecisionPayload {
  timestamp_ms?: number | string;
  symbol?: string;
  action_type?: BackendAction | number;
  action?: BackendAction | string | number;
  final_conviction_score?: number | string;
  technical_weight_used?: number | string;
  sentiment_weight_used?: number | string;
  reasoning?: string;
  reasoning_snippet?: string;
  price?: number | string;
}

export interface OhlcCandle {
  symbol: string;
  start_timestamp_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PredictiveSignal {
  symbol: string;
  timestamp_ms: number;
  target_timestamp_ms: number;
  predicted_close_price: number;
  confidence_score: number;
}

export interface MarketInsight {
  symbol: string;
  timestamp_ms: number;
  headline: string;
  analysis_text: string;
  sentiment_score: number;
  anomaly_pct: number;
}

export interface ExecutedTrade {
  decision: AggregatedDecision;
  quantity: number;
  executedAt: number;
}

export interface SystemLog {
  timestamp: number;
  level: 'INFO' | 'WARN' | 'ERROR';
  message: string;
}

interface TradeStore {
  liveDecisions: AggregatedDecision[];
  activeDecision: AggregatedDecision | null;
  portfolioBalance: number;
  positions: Record<string, number>;
  executedTrades: ExecutedTrade[];
  latencyMs: number;
  ohlcCandles: OhlcCandle[];
  predictiveSignals: PredictiveSignal[];
  latestInsight: MarketInsight | null;
  connectionStatus: 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED';
  wsStatus: 'disconnected' | 'connecting' | 'connected' | 'error';
  activeProfile: TradeProfile;
  activeTimeframe: ChartTimeframe;
  /** Data range — how many years of historical data to fetch. */
  activeRange: DataRange;
  systemLogs: SystemLog[];
  /** Explicitly selected symbol from the watchlist. Takes priority over the
   *  AI decision symbol when set. Defaults to 'RELIANCE'. */
  selectedSymbol: string;
  /** In-memory cache of historical candles keyed by symbol.
   *  Prevents redundant backend fetches when switching between symbols. */
  historicalCache: Record<string, OhlcCandle[]>;
  setActiveProfile: (profile: TradeProfile) => void;
  setActiveTimeframe: (tf: ChartTimeframe) => void;
  setActiveRange: (range: DataRange) => void;
  setLatestInsight: (insight: MarketInsight) => void;
  addSystemLog: (level: SystemLog['level'], message: string) => void;
  /** Set the active chart symbol from the watchlist or search. */
  setSelectedSymbol: (symbol: string) => void;
  /** Clear all live OHLC candles (used when switching symbols). */
  clearLiveBuffer: () => void;
  /** Cache historical candles for a symbol. */
  setHistoricalCache: (symbol: string, candles: OhlcCandle[]) => void;
  /** Retrieve cached historical candles (returns undefined if not cached). */
  getHistoricalCache: (symbol: string) => OhlcCandle[] | undefined;
  /** Invalidate one or all cached symbol entries. */
  clearHistoricalCache: (symbol?: string) => void;
  connectWebSocket: () => void;
  connectAlphaWebSocket: (url: string) => void;
  connectPredictiveWebSocket: (url: string) => void;
  connectInsightWebSocket: (url: string) => void;
  executeTrade: (decision: AggregatedDecision, quantity: number) => void;
  rejectTrade: (decision: AggregatedDecision) => void;
  resetSession: () => void;
}

export const useTradeStore = create<TradeStore>((set) => {
  let ws: WebSocket | null = null;

  // Helper: append a system log entry
  const syslog = (level: SystemLog['level'], message: string) => {
    set((state) => ({
      systemLogs: [...state.systemLogs, { timestamp: Date.now(), level, message }].slice(-500),
    }));
  };

  const resolveActionType = (value: BackendDecisionPayload['action_type'] | BackendDecisionPayload['action']): BackendAction => {
    if (typeof value === 'string') {
      const normalized = value.toUpperCase();
      if (normalized === 'BUY' || normalized === 'SELL' || normalized === 'HOLD') {
        return normalized;
      }
    }

    if (typeof value === 'number') {
      if (value === 0) return 'BUY';
      if (value === 1) return 'SELL';
      if (value === 2) return 'HOLD';
    }

    return 'HOLD';
  };

  const normalizeDecision = (payload: BackendDecisionPayload): AggregatedDecision => {
    const timestampMs = Number(payload.timestamp_ms ?? Date.now());
    const score = Number(payload.final_conviction_score ?? 50);
    const technicalWeight = Number(payload.technical_weight_used ?? 1);
    const sentimentWeight = Number(payload.sentiment_weight_used ?? 0);
    const price = payload.price === undefined ? undefined : Number(payload.price);
    const action_type = resolveActionType(payload.action_type ?? payload.action);

    return {
      timestamp_ms: Number.isFinite(timestampMs) ? timestampMs : Date.now(),
      symbol: payload.symbol ?? 'UNKNOWN',
      action_type,
      final_conviction_score: Number.isFinite(score) ? score : 50,
      reasoning: payload.reasoning ?? payload.reasoning_snippet,
      technical_weight_used: Number.isFinite(technicalWeight) ? technicalWeight : 0,
      sentiment_weight_used: Number.isFinite(sentimentWeight) ? sentimentWeight : 0,
      price: Number.isFinite(price ?? Number.NaN) ? price : undefined,
    };
  };

  return {
    liveDecisions: [],
    activeDecision: null,
    portfolioBalance: 100000,
    positions: {},
    executedTrades: [],
    latencyMs: 0,
    ohlcCandles: [],
    predictiveSignals: [],
    latestInsight: null,
    connectionStatus: 'DISCONNECTED',
    wsStatus: 'disconnected',
    activeProfile: 'INTRADAY',
    activeTimeframe: '10m',
    activeRange: '1Y' as DataRange,
    systemLogs: [],
    selectedSymbol: 'RELIANCE',
    historicalCache: {},

    setActiveProfile: (profile: TradeProfile) => {
      set({ activeProfile: profile });
    },

    setActiveTimeframe: (tf: ChartTimeframe) => {
      set({ activeTimeframe: tf });
    },

    setActiveRange: (range: DataRange) => {
      set({ activeRange: range });
    },

    addSystemLog: (level: SystemLog['level'], message: string) => {
      set((state) => ({
        systemLogs: [...state.systemLogs, { timestamp: Date.now(), level, message }].slice(-500),
      }));
    },

    setLatestInsight: (insight: MarketInsight) => {
      set({ latestInsight: insight });
    },

    setSelectedSymbol: (symbol: string) => {
      set({ selectedSymbol: symbol.toUpperCase() });
    },

    clearLiveBuffer: () => {
      set({ ohlcCandles: [], predictiveSignals: [] });
    },

    setHistoricalCache: (symbol: string, candles: OhlcCandle[]) => {
      set((state) => ({
        historicalCache: { ...state.historicalCache, [symbol.toUpperCase()]: candles },
      }));
    },

    getHistoricalCache: (symbol: string): OhlcCandle[] | undefined => {
      return useTradeStore.getState().historicalCache[symbol.toUpperCase()];
    },

    clearHistoricalCache: (symbol?: string) => {
      if (symbol) {
        set((state) => {
          const copy = { ...state.historicalCache };
          delete copy[symbol.toUpperCase()];
          return { historicalCache: copy };
        });
      } else {
        set({ historicalCache: {} });
      }
    },

    connectAlphaWebSocket: (url: string) => {
      const destroyed = false;

      const connect = () => {
        if (destroyed) return;
        const alphaWs = new WebSocket(url);
        syslog('INFO', `Alpha OHLC WS connecting → ${url}`);

        alphaWs.onopen = () => {
          syslog('INFO', 'Alpha OHLC WS connected. Streaming candle data.');
        };

        alphaWs.onmessage = (event) => {
          try {
            const candle: OhlcCandle = JSON.parse(event.data);

            // Validate required fields before storing — malformed messages
            // (e.g. missing symbol or NaN prices) corrupt the chart silently.
            if (
              !candle.symbol ||
              typeof candle.start_timestamp_ms !== 'number' ||
              typeof candle.open !== 'number' ||
              typeof candle.close !== 'number'
            ) {
              syslog('WARN', `Malformed OHLC candle received: ${event.data.slice(0, 100)}`);
              return;
            }

            set((state) => {
              // Upsert: if a candle with the same symbol + timestamp already
              // exists, replace it in-place so the chart reflects live price
              // movement within the current bucket. Otherwise append.
              const idx = state.ohlcCandles.findIndex(
                (c) =>
                  c.symbol === candle.symbol &&
                  c.start_timestamp_ms === candle.start_timestamp_ms
              );

              let newCandles: OhlcCandle[];
              if (idx !== -1) {
                // Replace existing candle with updated OHLC values
                newCandles = [...state.ohlcCandles];
                newCandles[idx] = candle;
              } else {
                newCandles = [...state.ohlcCandles, candle];
                // Log the first few candles arriving to confirm data flow
                if (newCandles.length <= 5) {
                  console.log(`[OHLC WS] Candle #${newCandles.length}:`, candle);
                }
              }

              if (newCandles.length > 3000) {
                return { ohlcCandles: newCandles.slice(-3000) };
              }
              return { ohlcCandles: newCandles };
            });
          } catch (e) {
            syslog('ERROR', `Alpha OHLC parse error: ${e}`);
          }
        };

        alphaWs.onclose = () => {
          syslog('WARN', 'Alpha OHLC WS disconnected. Reconnecting in 3s...');
          if (!destroyed) setTimeout(connect, 3000);
        };

        alphaWs.onerror = () => {
          syslog('ERROR', `Alpha OHLC WS connection error → ${url}`);
        };
      };

      connect();
    },

    connectPredictiveWebSocket: (url: string) => {
      const destroyed = false;

      const connect = () => {
        if (destroyed) return;
        const predictiveWs = new WebSocket(url);
        syslog('INFO', `Predictive WS connecting → ${url}`);

        predictiveWs.onopen = () => {
          syslog('INFO', 'Predictive WS connected. Ghost line projections active.');
        };

        predictiveWs.onmessage = (event) => {
          try {
            const signal: PredictiveSignal = JSON.parse(event.data);
            set((state) => ({
              predictiveSignals: [...state.predictiveSignals, signal].slice(-100),
            }));
          } catch (e) {
            syslog('ERROR', `Predictive signal parse error: ${e}`);
          }
        };

        predictiveWs.onclose = () => {
          syslog('WARN', 'Predictive WS disconnected. Reconnecting in 3s...');
          if (!destroyed) setTimeout(connect, 3000);
        };

        predictiveWs.onerror = () => {
          syslog('ERROR', `Predictive WS connection error → ${url}`);
        };
      };

      connect();
    },

    connectInsightWebSocket: (url: string) => {
      const destroyed = false;

      const connect = () => {
        if (destroyed) return;
        const insightWs = new WebSocket(url);
        syslog('INFO', `Insight (DeepSeek) WS connecting → ${url}`);

        insightWs.onopen = () => {
          syslog('INFO', 'Insight WS connected. DeepSeek anomaly detection active.');
        };

        insightWs.onmessage = (event) => {
          try {
            const insight: MarketInsight = JSON.parse(event.data);
            set({ latestInsight: insight });
            if (insight.headline === 'LLM API Failure') {
              syslog('ERROR', `DeepSeek API failure: ${insight.analysis_text}`);
            } else {
              syslog('INFO', `Market insight received: ${insight.headline} (${insight.symbol})`);
            }
          } catch (e) {
            syslog('ERROR', `Insight parse error: ${e}`);
          }
        };

        insightWs.onclose = () => {
          syslog('WARN', 'Insight WS disconnected. Reconnecting in 3s...');
          if (!destroyed) setTimeout(connect, 3000);
        };

        insightWs.onerror = () => {
          syslog('ERROR', `Insight WS connection error → ${url}`);
        };
      };

      connect();
    },

    executeTrade: (decision: AggregatedDecision, quantity: number) => {
      set((state) => {
        const symbol = decision.symbol;
        const price = decision.price || 0;
        let newBalance = state.portfolioBalance;
        const newPositions = { ...state.positions };
        const currentQty = newPositions[symbol] || 0;

        if (decision.action_type === 'BUY') {
          newBalance -= price * quantity;
          newPositions[symbol] = currentQty + quantity;
        } else if (decision.action_type === 'SELL') {
          newBalance += price * quantity;
          newPositions[symbol] = currentQty - quantity;
        }

        return {
          portfolioBalance: newBalance,
          positions: newPositions,
          executedTrades: [...state.executedTrades, { decision, quantity, executedAt: Date.now() }],
          activeDecision: null,
        };
      });
    },

    rejectTrade: (decision: AggregatedDecision) => {
      void decision;
      set({ activeDecision: null });
    },

    resetSession: () => {
      set({
        portfolioBalance: 100000,
        positions: {},
        executedTrades: [],
        liveDecisions: [],
        activeDecision: null,
      });
    },

    connectWebSocket: () => {
      // Prevent multiple connections
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        return;
      }

      const wsUrl =
        process.env.NEXT_PUBLIC_AGGREGATOR_WS_URL ||
        process.env.NEXT_PUBLIC_WS_URL ||
        'ws://127.0.0.1:8080';

      const connect = () => {
        set({ wsStatus: 'connecting', connectionStatus: 'CONNECTING' });

        try {
          ws = new WebSocket(wsUrl);

          ws.onopen = () => {
            syslog('INFO', `Decision WS connected → ${wsUrl}`);
            set({ wsStatus: 'connected', connectionStatus: 'CONNECTED' });
          };

          ws.onmessage = (event) => {
            try {
              const rawData: BackendDecisionPayload = JSON.parse(event.data);
              const data = normalizeDecision(rawData);
              const currentLatency = Date.now() - data.timestamp_ms;

              set((state) => {
                const updatedDecisions = [...state.liveDecisions, data];
                if (updatedDecisions.length > 100) {
                  updatedDecisions.shift();
                }

                return {
                  liveDecisions: updatedDecisions,
                  activeDecision: state.activeDecision ? state.activeDecision : data,
                  latencyMs: Number.isFinite(currentLatency) ? Math.max(0, currentLatency) : 0,
                };
              });
            } catch (err) {
              syslog('ERROR', `Decision WS parse error: ${err}`);
            }
          };

          ws.onclose = () => {
            set({ wsStatus: 'disconnected', connectionStatus: 'DISCONNECTED' });
            ws = null;
            // Auto-reconnect after 3s (matches other WS connections)
            syslog('WARN', 'Decision WS disconnected. Reconnecting in 3s...');
            setTimeout(connect, 3000);
          };

          ws.onerror = () => {
            // Suppress noisy console.error — the onclose handler will fire
            // immediately after and trigger reconnection. This is expected
            // when the aggregator backend isn't running yet.
            syslog('WARN', `Decision WS connection failed → ${wsUrl}`);
            set({ wsStatus: 'error', connectionStatus: 'DISCONNECTED' });
          };
        } catch (error) {
          syslog('ERROR', `Decision WS init failed: ${error}`);
          set({ wsStatus: 'error', connectionStatus: 'DISCONNECTED' });
          // Retry after 3s
          setTimeout(connect, 3000);
        }
      };

      connect();
    },
  };
});
