import { create } from 'zustand';

export interface AggregatedDecision {
  timestamp_ms: number;
  symbol: string;
  action_type: 'BUY' | 'SELL' | 'HOLD';
  final_conviction_score: number;
  reasoning: string;
  technical_weight_used: number;
  sentiment_weight_used: number;
  price?: number;
}

interface TradeStore {
  liveDecisions: AggregatedDecision[];
  activeDecision: AggregatedDecision | null;
  portfolioBalance: number;
  positions: Record<string, number>;
  executedTrades: any[];
  latencyMs: number;
  connectionStatus: 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED';
  wsStatus: 'disconnected' | 'connecting' | 'connected' | 'error';
  connectWebSocket: () => void;
  executeTrade: (decision: AggregatedDecision, quantity: number) => void;
  rejectTrade: (decision: AggregatedDecision) => void;
  resetSession: () => void;
}

export const useTradeStore = create<TradeStore>((set, get) => {
  let ws: WebSocket | null = null;

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

      // Use env variable or fallback
      const wsUrl = process.env.NEXT_PUBLIC_WS_URL || 'ws://127.0.0.1:8080';

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          console.log('WebSocket connected to Aggregator', wsUrl);
          set({ wsStatus: 'connected', connectionStatus: 'CONNECTED' });
        };

        ws.onmessage = (event) => {
          try {
            const data: AggregatedDecision = JSON.parse(event.data);
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
