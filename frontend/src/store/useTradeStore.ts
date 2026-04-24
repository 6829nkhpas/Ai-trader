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
  latencyMs: number;
  connectionStatus: 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED';
  wsStatus: 'disconnected' | 'connecting' | 'connected' | 'error';
  connectWebSocket: () => void;
}

export const useTradeStore = create<TradeStore>((set, get) => {
  let ws: WebSocket | null = null;

  return {
    liveDecisions: [],
    latencyMs: 0,
    connectionStatus: 'DISCONNECTED',
    wsStatus: 'disconnected',

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
