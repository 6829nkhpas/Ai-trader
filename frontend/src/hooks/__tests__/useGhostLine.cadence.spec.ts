/**
 * @vitest-environment jsdom
 */
/**
 * useGhostLine.spec.ts — Stability test for the ghost line redraw cadence.
 *
 * The ghost line's main redraw effect must NOT re-fire on every `predictive-tick`
 * event. Each such event appends a `PredictiveSignal` to the store and changes
 * `predicted_close_price`; if the effect subscribed to a `predictiveKey`
 * derived from that signal, it would re-fire immediately and bypass the realtime
 * `pulse` throttle (4s), producing the redraw storm users reported.
 *
 * After Unit 4, `predictiveKey` is removed entirely: the effect reads signals
 * via `useTradeStore.getState()` inside the effect (a non-reactive read) and
 * only re-fires on the throttled cadence deps — `lastBarTime`, `pulse`,
 * `zoomPulse` — plus the mode/symbol/timeframe identities.
 *
 * This spec asserts the observable consequence: driving only
 * `predictiveSignals` changes (via setState) does NOT increase the number of
 * draws issued to the chart widget. We count `createMultipointShape` and
 * `removeEntity` calls as the draw side-effect.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useGhostLine } from '../useGhostLine';
import { useTradeStore, type PredictiveSignal, type OhlcCandle } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';

// Stub the pure computation module so the effect does not depend on real
// market data — it just returns deterministic points so the draw path runs.
vi.mock('../ghostLineComputation', () => ({
  computeGhostPoints: vi.fn(async () => [
    { time: 1000, price: 10 },
    { time: 2000, price: 11 },
  ]),
}));

/**
 * Build a mock TradingView widget whose chart records every draw side-effect.
 * `onChartReady` invokes its callback synchronously so the effect runs to
 * completion within `act`.
 */
function makeCountingWidget() {
  const drawCalls = { createMultipointShape: 0, removeEntity: 0 };
  const chart = {
    getVisibleRange: () => ({ from: 0, to: 1000 }),
    onVisibleRangeChanged: () => ({
      subscribe: () => {},
      unsubscribeAll: () => {},
    }),
    createMultipointShape: vi.fn(async () => {
      drawCalls.createMultipointShape++;
      return `id-${drawCalls.createMultipointShape}`;
    }),
    removeEntity: vi.fn(() => {
      drawCalls.removeEntity++;
    }),
  };
  const widget = {
    onChartReady: (cb: () => void) => {
      // Run synchronously so the effect completes inside act().
      cb();
    },
    activeChart: () => chart,
  };
  return { widget, drawCalls };
}

function resetStores() {
  useTradeStore.setState({
    ohlcCandles: [] as OhlcCandle[],
    predictiveSignals: [] as PredictiveSignal[],
  } as any);
  useChartUIStore.setState({ ghostLineMode: 'curved' } as any);
}

describe('useGhostLine redraw cadence', () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('does NOT redraw when only predictiveSignals change', async () => {
    const { widget, drawCalls } = makeCountingWidget();

    const { rerender } = renderHook(
      ({ w, sym, tf }) => useGhostLine(w, sym, tf),
      {
        initialProps: { w: widget, sym: 'NIFTY', tf: '10m' },
      },
    );

    // Let the initial draw settle.
    await act(async () => {
      // onChartReady ran synchronously, but the async draw awaits a microtask.
      await Promise.resolve();
      await Promise.resolve();
    });

    const drawsAfterMount = drawCalls.createMultipointShape;
    expect(drawsAfterMount).toBeGreaterThan(0);

    // Now drive ONLY a predictive-tick: a fresh predictive signal whose
    // predicted_close_price differs from anything before. Before Unit 4 this
    // changed `predictiveKey` and re-fired the effect immediately; after
    // Unit 4 the effect does not subscribe to signals, so it must NOT redraw.
    const signal1: PredictiveSignal = {
      symbol: 'NIFTY',
      timestamp_ms: 1_000,
      target_timestamp_ms: 2_000,
      predicted_close_price: 100.5,
      confidence_score: 0.8,
    };

    await act(async () => {
      useTradeStore.setState({ predictiveSignals: [signal1] });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(drawCalls.createMultipointShape).toBe(drawsAfterMount);

    // A second predictive tick with a different predicted_close_price must
    // likewise not trigger a redraw on its own.
    const signal2: PredictiveSignal = {
      symbol: 'NIFTY',
      timestamp_ms: 1_500,
      target_timestamp_ms: 2_500,
      predicted_close_price: 101.25,
      confidence_score: 0.81,
    };

    await act(async () => {
      useTradeStore.setState({ predictiveSignals: [signal1, signal2] });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(drawCalls.createMultipointShape).toBe(drawsAfterMount);

    // Sanity: a redraw trigger that IS in the dep array (a new bar forming,
    // i.e. lastBarTime advancing) MUST still redraw. This guards against the
    // test passing for the wrong reason (e.g. the widget mock silently
    // dropping all draws).
    const candle: OhlcCandle = {
      symbol: 'NIFTY',
      start_timestamp_ms: 99_999,
      open: 1,
      high: 1,
      low: 1,
      close: 1,
      volume: 1,
    };

    await act(async () => {
      useTradeStore.setState({ ohlcCandles: [candle] });
      await Promise.resolve();
      await Promise.resolve();
    });

    // Force a re-render so the new lastBarTime selector value propagates.
    rerender({ w: widget, sym: 'NIFTY', tf: '10m' });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(drawCalls.createMultipointShape).toBeGreaterThan(drawsAfterMount);
  });
});
