import { create } from 'zustand';

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

export interface ExecutedTrade {
  decision: AggregatedDecision;
  quantity: number;
  executedAt: number;
}

interface TradeStore {
  liveDecisions: AggregatedDecision[];
  activeDecision: AggregatedDecision | null;
  portfolioBalance: number;
  positions: Record<string, number>;
  executedTrades: ExecutedTrade[];
  latencyMs: number;
  connectionStatus: 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED';
  wsStatus: 'disconnected' | 'connecting' | 'connected' | 'error';
  connectWebSocket: () => void;
  executeTrade: (decision: AggregatedDecision, quantity: number) => void;
  rejectTrade: (decision: AggregatedDecision) => void;
  resetSession: () => void;
}

export const useTradeStore = create<TradeStore>((set) => {
  let ws: WebSocket | null = null;

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
    connectionStatus: 'DISCONNECTED',
    wsStatus: 'disconnected',

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

      set({ wsStatus: 'connecting', connectionStatus: 'CONNECTING' });

      const wsUrl =
        process.env.NEXT_PUBLIC_AGGREGATOR_WS_URL ||
        process.env.NEXT_PUBLIC_WS_URL ||
        'ws://127.0.0.1:8080';

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log('WebSocket connected to Aggregator', wsUrl);
          set({ wsStatus: 'connected', connectionStatus: 'CONNECTED' });
        };

        ws.onmessage = (event) => {
          console.log('📨 WebSocket message received:', event.data);
          try {
            const rawData: BackendDecisionPayload = JSON.parse(event.data);
            console.log('✅ Parsed payload:', rawData);
            const data = normalizeDecision(rawData);
            console.log('✅ Normalized decision:', data);
            const currentLatency = Date.now() - data.timestamp_ms;

            set((state) => {
              // Append new decision and cap at 100 to prevent memory leaks
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
            console.error('Failed to parse WebSocket message', err);
          }
        };

        ws.onclose = () => {
          console.log('WebSocket disconnected');
          set({ wsStatus: 'disconnected', connectionStatus: 'DISCONNECTED' });
          ws = null;
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          set({ wsStatus: 'error', connectionStatus: 'DISCONNECTED' });
        };
      } catch (error) {
        console.error('Failed to initialize WebSocket', error);
        set({ wsStatus: 'error', connectionStatus: 'DISCONNECTED' });
      }
    },
  };
});
