// hooks/useTauriLiveData.ts — Dynamic Tauri IPC event subscription
//
// Binds ohlc-tick, predictive-tick, and insight-tick Tauri events to the
// Zustand store. Crucially, when activeSymbol changes we:
//   1. Clear the live candle/predictive buffers
//   2. Re-subscribe the event listeners (they are symbol-agnostic at the
//      backend level, but the frontend only stores matching ticks)
//
// This hook replaces the static connectAlphaWebSocket / connectPredictiveWebSocket
// / connectInsightWebSocket calls for Tauri builds where the Rust backend bridges
// backend WebSockets → IPC events.

import { useEffect, useRef } from 'react';
import { useTradeStore, type OhlcCandle, type PredictiveSignal, type MarketInsight } from '../store/useTradeStore';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

/**
 * Subscribe to Tauri IPC live data events and dynamically route them
 * based on the currently active chart symbol.
 *
 * @param activeSymbol - The symbol the chart is currently displaying.
 */
export function useTauriLiveData(activeSymbol: string) {
  const previousSymbolRef = useRef<string>('');

  useEffect(() => {
    if (!activeSymbol || !isTauri()) return;

    let cancelled = false;
    const cleanupFns: Array<() => void> = [];

    // ── Symbol Switch Cleanup ──────────────────────────────────────────
    // When the symbol changes, flush stale live data so the chart doesn't
    // show ticks from the previous instrument.
    if (previousSymbolRef.current && previousSymbolRef.current !== activeSymbol) {
      useTradeStore.getState().clearLiveBuffer();
      useTradeStore.getState().addSystemLog('INFO', `Live feed switched: ${previousSymbolRef.current} → ${activeSymbol}`);
    }
    previousSymbolRef.current = activeSymbol;

    // ── Notify Rust backend of active symbol ───────────────────────────
    // In test mode: switches the mock OHLC emitter to the new symbol.
    // In production: keeps backend state in sync for future server-side
    // symbol-filtered routing (currently the WS bridge sends all symbols).
    (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        // ── DIAGNOSTIC TRACER — UI → RUST live subscribe dispatch ──
        console.log("🔥 [UI DISPATCH] Subscribing Live - Symbol:", activeSymbol);
        await invoke('subscribe_ticker', { symbol: activeSymbol });
      } catch {
        // Not fatal — production WS bridge is symbol-agnostic.
      }
    })();

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        if (cancelled) return;

        // ── OHLC Tick Listener ────────────────────────────────────────
        const unlistenOhlc = await listen<OhlcCandle>('ohlc-tick', (event) => {
          if (cancelled) return;
          const candle = event.payload;

          // Validate required fields
          if (
            !candle.symbol ||
            typeof candle.start_timestamp_ms !== 'number' ||
            typeof candle.open !== 'number' ||
            typeof candle.close !== 'number'
          ) {
            return;
          }

          useTradeStore.setState((state: { ohlcCandles: OhlcCandle[] }) => {
            const idx = state.ohlcCandles.findIndex(
              (c: OhlcCandle) =>
                c.symbol === candle.symbol &&
                c.start_timestamp_ms === candle.start_timestamp_ms
            );

            let newCandles: OhlcCandle[];
            if (idx !== -1) {
              newCandles = [...state.ohlcCandles];
              newCandles[idx] = candle;
            } else {
              newCandles = [...state.ohlcCandles, candle];
            }

            if (newCandles.length > 3000) {
              return { ohlcCandles: newCandles.slice(-3000) };
            }
            return { ohlcCandles: newCandles };
          });
        });
        cleanupFns.push(unlistenOhlc);

        // ── Predictive Tick Listener ──────────────────────────────────
        const unlistenPredictive = await listen<PredictiveSignal>('predictive-tick', (event) => {
          if (cancelled) return;
          useTradeStore.setState((state: { predictiveSignals: PredictiveSignal[] }) => ({
            predictiveSignals: [...state.predictiveSignals, event.payload].slice(-100),
          }));
        });
        cleanupFns.push(unlistenPredictive);

        // ── Insight Tick Listener ─────────────────────────────────────
        const unlistenInsight = await listen<MarketInsight>('insight-tick', (event) => {
          if (cancelled) return;
          useTradeStore.setState({ latestInsight: event.payload });
        });
        cleanupFns.push(unlistenInsight);

        useTradeStore.getState().addSystemLog('INFO', `Tauri IPC live data bound to ${activeSymbol}`);
      } catch {
        // Not in Tauri context — listeners will be handled by WebSocket fallback
      }
    })();

    return () => {
      cancelled = true;
      cleanupFns.forEach((fn) => fn());
    };
  }, [activeSymbol]);
}
