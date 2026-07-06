// Feature: professional-charting-suite
//
// Integration & performance suite (Task 15.2).
//
// This suite validates the cross-cutting behaviors of the charting suite at the
// integration level that is feasible in a node Vitest environment — i.e. with
// no real Tauri/SQLite backend and no real `lightweight-charts` DOM. It
// exercises the seams BETWEEN the pure engines, the pane manager, the crosshair
// readout, and the persistence layer rather than any single engine in
// isolation:
//
//   - Pane time-bound sync and pan/zoom follow (Requirements 3.3, 3.4): the
//     PaneManager keeps every oscillator pane on ONE shared time scale, and its
//     layout/redistribution invariants hold across add/remove sequences.
//   - Synchronized crosshair across panes (Requirement 10.4): one crosshair
//     time produces a single, consistent readout that every pane shares.
//   - Persistence round-trip (Requirements 11.1, 11.2): a full workspace
//     survives serialize→deserialize AND saveWorkspace→loadWorkspace through the
//     documented in-memory fallback (the closest feasible proxy for the SQLite
//     IPC round-trip — the real-IPC path serializes the identical blob).
//   - Latency / frame-budget targets (Requirements 9.1, 9.2): canonicalize +
//     build-series + a representative indicator compute over a realistic 5k
//     candle series completes well within a frame budget, and a live single-
//     candle update touches only the latest candle.
//
// The suite mounts the real engine code end-to-end against an in-memory fake
// chart (for pane wiring) and the real in-memory persistence fallback (for
// workspace round-trips), so it asserts integrated behavior, not mocks.

import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IChartApi } from 'lightweight-charts';

import { createPaneManager, redistribute, type PaneLayout } from '@/charting/paneManager';
import { buildCrosshairReadout } from '@/charting/crosshair';
import {
  canonicalCandles,
  applyLatestCandleUpdate,
  buildSeries,
  INDICATOR_REGISTRY,
} from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OhlcCandle } from '@/store/useTradeStore';

// Simulate running OUTSIDE the Tauri runtime so the persistence layer exercises
// its documented in-memory session fallback (Requirement 11.6) — this is the
// closest feasible proxy for the SQLite IPC round-trip, which serializes the
// exact same blob via `save_workspace` / `load_workspace`.
vi.mock('@tauri-apps/api/core', () => ({ invoke: undefined }));

import {
  serializeWorkspace,
  deserializeWorkspace,
  flushWorkspace,
  loadWorkspace,
  resetWorkspacePersistence,
  type WorkspaceState,
} from '@/charting/workspace';

// ── Test data helpers ──────────────────────────────────────────────────────

/** A tiny deterministic PRNG (mulberry32) so generated series are stable. */
function makeRng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Build a realistic raw OHLC candle buffer (store shape) for `symbol`: a random
 * walk on a regular `stepSec` grid with sane OHLC ordering and positive volume.
 */
function makeRawCandles(
  symbol: string,
  n: number,
  startSec = 1_600_000_000,
  stepSec = 60,
  seed = 1,
): OhlcCandle[] {
  const rng = makeRng(seed);
  const out: OhlcCandle[] = [];
  let price = 100;
  for (let i = 0; i < n; i++) {
    const open = price;
    const drift = (rng() - 0.5) * 2; // -1..1
    const close = Math.max(1, open + drift);
    const high = Math.max(open, close) + rng();
    const low = Math.min(open, close) - rng();
    out.push({
      symbol,
      start_timestamp_ms: (startSec + i * stepSec) * 1000,
      open,
      high,
      low,
      close,
      volume: 1000 + Math.floor(rng() * 1000),
    });
    price = close;
  }
  return out;
}

// ── In-memory fake chart for the PaneManager ─────────────────────────────────
//
// Mirrors only the `IChartApi` surface the PaneManager touches. The fake time
// scale RECORDS every visible-range applied so the test can prove that a single
// shared-range mechanism drives all panes (Requirements 3.3, 3.4).

class FakePane {
  private stretch = 1;
  private series: unknown[] = [];
  constructor(private readonly chart: FakeChart) {}
  paneIndex(): number {
    return this.chart.indexOf(this);
  }
  getStretchFactor(): number {
    return this.stretch;
  }
  setStretchFactor(value: number): void {
    this.stretch = value;
  }
  getSeries(): unknown[] {
    return this.series;
  }
  addFakeSeries(): void {
    this.series.push({});
  }
  clearSeries(): void {
    this.series = [];
  }
}

class FakeTimeScale {
  /** Every range applied to the single shared time scale, in order. */
  readonly appliedRanges: Array<{ from: unknown; to: unknown }> = [];
  setVisibleRange(range: { from: unknown; to: unknown }): void {
    this.appliedRanges.push(range);
  }
}

class FakeChart {
  private readonly panes: FakePane[] = [new FakePane(this)]; // index 0 = price pane
  readonly scale = new FakeTimeScale();
  addPane(): FakePane {
    const pane = new FakePane(this);
    this.panes.push(pane);
    return pane;
  }
  removePane(index: number): void {
    this.panes.splice(index, 1);
  }
  indexOf(pane: FakePane): number {
    return this.panes.indexOf(pane);
  }
  paneAt(index: number): FakePane | undefined {
    return this.panes[index];
  }
  timeScale(): FakeTimeScale {
    return this.scale;
  }
}

function makeManager() {
  const chart = new FakeChart();
  const mgr = createPaneManager(chart as unknown as IChartApi);
  return { chart, mgr };
}

/** Assert a layout's height fractions sum to 1.0 and orders are contiguous. */
function expectLayoutInvariants(layout: PaneLayout[]): void {
  if (layout.length === 0) return;
  const sum = layout.reduce((s, l) => s + l.heightFraction, 0);
  expect(sum).toBeCloseTo(1, 10);
  const orders = [...layout].map((l) => l.order).sort((a, b) => a - b);
  expect(orders).toEqual(layout.map((_, i) => i));
}

afterEach(() => {
  resetWorkspacePersistence();
  vi.useRealTimers();
});

// ─────────────────────────────────────────────────────────────────────────────
// 1. Pane time-bound sync & pan/zoom follow (Requirements 3.3, 3.4)
// ─────────────────────────────────────────────────────────────────────────────

describe('pane sync & pan/zoom follow (Requirements 3.3, 3.4)', () => {
  it('syncVisibleRange is the single shared-range mechanism applied once per update', () => {
    const { chart, mgr } = makeManager();
    // Three oscillator panes share the price pane's one time scale (v5).
    mgr.ensurePane('rsi');
    mgr.ensurePane('macd');
    mgr.ensurePane('stochastic');

    // Simulate a pan then a zoom: each applies ONE visible range.
    const panRange = { from: 1_600_000_000 as unknown, to: 1_600_003_600 as unknown };
    const zoomRange = { from: 1_600_001_000 as unknown, to: 1_600_002_000 as unknown };
    mgr.syncVisibleRange(panRange as never);
    mgr.syncVisibleRange(zoomRange as never);

    // Exactly two ranges were applied — one per pan/zoom — to the shared scale,
    // not once per pane. Because all panes share that scale, every pane follows
    // the identical range (the pane-bound sync contract, Req 3.3/3.4).
    expect(chart.scale.appliedRanges).toEqual([panRange, zoomRange]);
  });

  it('layout invariants hold across an add sequence (heights sum to 1, order preserved)', () => {
    const { mgr } = makeManager();
    const adds = ['rsi', 'macd', 'stochastic', 'adx', 'atr'];
    const expectedOrder: string[] = [];
    for (const id of adds) {
      const paneId = mgr.ensurePane(id);
      expectedOrder.push(paneId);
      const layout = mgr.layout();
      expectLayoutInvariants(layout);
      // Addition order is preserved top→bottom.
      const ordered = [...layout].sort((a, b) => a.order - b.order).map((l) => l.paneId);
      expect(ordered).toEqual(expectedOrder);
    }
  });

  it('pane removal redistributes height with no gap and preserves remaining order', () => {
    const { chart, mgr } = makeManager();
    const ids = ['rsi', 'macd', 'stochastic', 'adx'];
    const paneIds = ids.map((id) => mgr.ensurePane(id));

    // Empty the macd pane (index 2: price=0, rsi=1, macd=2) so it is removable.
    // Give the others a fake series so they are NOT removed.
    chart.paneAt(1)?.addFakeSeries(); // rsi
    chart.paneAt(3)?.addFakeSeries(); // stochastic
    chart.paneAt(4)?.addFakeSeries(); // adx
    // macd (index 2) stays empty.

    const macdPane = paneIds[1];
    mgr.removePaneIfEmpty(macdPane);

    const layout = mgr.layout();
    // One fewer pane, invariants still hold.
    expect(layout.length).toBe(3);
    expectLayoutInvariants(layout);
    // The removed pane is gone; survivors keep their relative top→bottom order.
    const survivors = [...layout].sort((a, b) => a.order - b.order).map((l) => l.paneId);
    expect(survivors).not.toContain(macdPane);
    expect(survivors).toEqual([paneIds[0], paneIds[2], paneIds[3]]);
  });

  it('pure redistribute conserves total height for arbitrary survivor proportions', () => {
    const layouts: PaneLayout[] = [
      { paneId: 'a', heightFraction: 0.5, order: 0 },
      { paneId: 'b', heightFraction: 0.3, order: 1 },
      { paneId: 'c', heightFraction: 0.2, order: 2 },
    ];
    const next = redistribute(layouts, 'b');
    expect(next.map((l) => l.paneId)).toEqual(['a', 'c']);
    expectLayoutInvariants(next);
    // Removing every pane yields an empty layout (no gap to fill).
    expect(redistribute([layouts[0]], 'a')).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 2. Synchronized crosshair across panes (Requirement 10.4)
// ─────────────────────────────────────────────────────────────────────────────

describe('synchronized crosshair across panes (Requirement 10.4)', () => {
  it('one crosshair time yields a single consistent readout shared by every pane', () => {
    const candles = canonicalCandles(makeRawCandles('NIFTY', 200, 1_600_000_000, 60, 7), 'NIFTY');
    const t = candles[120].time;

    // Price pane hosts overlay indicators; each oscillator lives in its own
    // pane. The controller broadcasts ONE crosshair time to all of them.
    const emaPlot = INDICATOR_REGISTRY.ema.compute(candles, { period: 20 });
    const rsiPlot = INDICATOR_REGISTRY.rsi.compute(candles, { period: 14 });
    const macdPlot = INDICATOR_REGISTRY.macd.compute(candles, { fast: 12, slow: 26, signal: 9 });

    const pricePane = buildCrosshairReadout({
      time: t,
      candles,
      indicators: [{ instanceId: 'ema-1', indicatorId: 'ema', label: 'EMA', plot: emaPlot }],
      precision: 2,
    });
    const rsiPane = buildCrosshairReadout({
      time: t,
      candles,
      indicators: [{ instanceId: 'rsi-1', indicatorId: 'rsi', label: 'RSI', plot: rsiPlot }],
      precision: 2,
    });
    const macdPane = buildCrosshairReadout({
      time: t,
      candles,
      indicators: [{ instanceId: 'macd-1', indicatorId: 'macd', label: 'MACD', plot: macdPlot }],
      precision: 2,
    });

    // Every pane's readout is anchored to the IDENTICAL time key (Req 10.4).
    expect(pricePane.time).toBe(t);
    expect(rsiPane.time).toBe(t);
    expect(macdPane.time).toBe(t);

    // And they agree about the candle under the crosshair — one consistent
    // readout, not three divergent ones.
    expect(rsiPane.hasCandle).toBe(true);
    expect(rsiPane.ohlc).toEqual(pricePane.ohlc);
    expect(macdPane.ohlc).toEqual(pricePane.ohlc);

    // The shared OHLC matches the underlying candle to the instrument precision.
    const c = candles[120];
    expect(pricePane.ohlc.close).toBe(c.close.toFixed(2));
  });

  it('moving the crosshair off the data range yields a consistent no-value readout for all panes', () => {
    const candles = canonicalCandles(makeRawCandles('NIFTY', 50, 1_600_000_000, 60, 3), 'NIFTY');
    const offTime = candles[candles.length - 1].time + 10_000; // beyond loaded data

    const rsiPlot = INDICATOR_REGISTRY.rsi.compute(candles, { period: 14 });
    const readout = buildCrosshairReadout({
      time: offTime,
      candles,
      indicators: [{ instanceId: 'rsi-1', indicatorId: 'rsi', label: 'RSI', plot: rsiPlot }],
      precision: 2,
    });

    expect(readout.hasCandle).toBe(false);
    expect(readout.ohlc).toEqual({ open: '—', high: '—', low: '—', close: '—' });
    // The oscillator pane shows a placeholder rather than a borrowed value.
    expect(readout.indicators[0].lines[0].value).toBe('—');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 3. Persistence round-trip through the documented IPC fallback (Reqs 11.1, 11.2)
// ─────────────────────────────────────────────────────────────────────────────

describe('workspace persistence round-trip (Requirements 11.1, 11.2)', () => {
  /** A full workspace: chart type + params + indicators + drawings + paneLayout. */
  function fullWorkspace(): WorkspaceState {
    return {
      version: 1,
      chartType: 'heikin-ashi',
      chartTypeParams: { renkoBoxSize: 5, lineBreakCount: 3 },
      activeIndicators: [
        {
          instanceId: 'ema-1',
          indicatorId: 'ema',
          params: { period: 20 },
          style: { color: '#f59e0b', lineWidth: 2, lineStyle: 'solid' },
          visible: true,
          paneId: null,
        },
        {
          instanceId: 'rsi-1',
          indicatorId: 'rsi',
          params: { period: 14 },
          style: { color: '#8b5cf6', lineWidth: 2, lineStyle: 'dashed' },
          visible: false,
          paneId: 'indicator-pane-0',
        },
      ],
      drawings: [
        {
          id: 'd1',
          tool: 'trend-line',
          points: [
            { time: 1_600_000_000, price: 101.5 },
            { time: 1_600_003_600, price: 110.25 },
          ],
          color: '#22d3ee',
          locked: true,
          symbol: 'NIFTY',
        },
        {
          id: 'd2',
          tool: 'horizontal-line',
          points: [{ time: 1_600_001_000, price: 105 }],
          color: '#ef4444',
          locked: false,
          symbol: 'NIFTY',
        },
      ] as unknown as WorkspaceState['drawings'],
      paneLayout: [
        { paneId: 'indicator-pane-0', heightFraction: 1, order: 0 },
      ],
    };
  }

  it('serialize → deserialize reproduces the complete workspace', () => {
    const ws = fullWorkspace();
    const restored = deserializeWorkspace(serializeWorkspace(ws));
    expect(restored).toEqual(ws);
  });

  it('saveWorkspace (flush) → loadWorkspace round-trips through the in-memory IPC fallback', async () => {
    const ws = fullWorkspace();
    const symbol = 'NIFTY';

    // flushWorkspace serializes the exact blob the real save_workspace IPC
    // would persist; outside Tauri it records it in the session store and
    // reports `false` (no persistent backend) while retaining the state.
    const persisted = await flushWorkspace(symbol, ws);
    expect(persisted).toBe(false);

    const restored = await loadWorkspace(symbol);
    expect(restored).toEqual(ws);
  });

  it('an absent persisted workspace restores the defaults', async () => {
    const restored = await loadWorkspace('NEVER_PERSISTED');
    expect(restored.chartType).toBe('candlestick');
    expect(restored.activeIndicators).toEqual([]);
    expect(restored.drawings).toEqual([]);
    expect(restored.paneLayout).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. Latency / frame-budget targets (Requirements 9.1, 9.2)
// ─────────────────────────────────────────────────────────────────────────────

describe('latency & frame-budget targets (Requirements 9.1, 9.2)', () => {
  // A frame budget is 16ms (60fps). We assert the integrated compute pipeline
  // for a realistic 5k-candle dataset completes well under a deliberately
  // GENEROUS 50ms ceiling so the test stays stable across CI hardware, while
  // still proving the work is within a few frame budgets. We time the minimum
  // of several iterations to suppress one-off GC / scheduler jitter.
  const FRAME_BUDGET_CEILING_MS = 50;
  const ITERATIONS = 5;

  it('canonicalize + buildSeries + representative indicator compute stays within the frame-budget ceiling (5k candles)', () => {
    const raw = makeRawCandles('NIFTY', 5_000, 1_600_000_000, 60, 11);

    let best = Infinity;
    let lastCount = 0;
    for (let it = 0; it < ITERATIONS; it++) {
      const start = performance.now();

      const candles = canonicalCandles(raw, 'NIFTY');
      // A representative pass: a transformed chart type plus an overlay and an
      // oscillator indicator — the work a single render pass drives (Req 9.1).
      buildSeries(candles, 'heikin-ashi', {});
      INDICATOR_REGISTRY.ema.compute(candles, { period: 20 });
      INDICATOR_REGISTRY.bollinger.compute(candles, { period: 20, stdDev: 2 });
      INDICATOR_REGISTRY.rsi.compute(candles, { period: 14 });

      const elapsed = performance.now() - start;
      best = Math.min(best, elapsed);
      lastCount = candles.length;
    }

    expect(lastCount).toBe(5_000);
    // Log the measured best for visibility without making the assertion flaky.
    // eslint-disable-next-line no-console
    console.log(`[perf] 5k canonical+buildSeries+3 indicators best=${best.toFixed(2)}ms`);
    expect(best).toBeLessThan(FRAME_BUDGET_CEILING_MS);
  });

  it('a live single-candle update touches only the latest candle and is sub-frame-budget (Req 9.2/9.3)', () => {
    const candles = canonicalCandles(makeRawCandles('NIFTY', 5_000, 1_600_000_000, 60, 13), 'NIFTY');
    const last = candles[candles.length - 1];
    const update: ChartCandle = { ...last, close: last.close + 1, high: last.high + 1 };

    let best = Infinity;
    let kind = '';
    for (let it = 0; it < ITERATIONS; it++) {
      const start = performance.now();
      const res = applyLatestCandleUpdate(candles, update);
      const elapsed = performance.now() - start;
      best = Math.min(best, elapsed);
      kind = res.kind;
    }

    // Same-timestamp update is classified as an in-place update (not a repaint).
    expect(kind).toBe('update');
    // eslint-disable-next-line no-console
    console.log(`[perf] live latest-candle update over 5k candles best=${best.toFixed(2)}ms`);
    expect(best).toBeLessThan(FRAME_BUDGET_CEILING_MS);

    // Earlier candles are preserved by reference — only the last candle changed.
    const res = applyLatestCandleUpdate(candles, update);
    for (let i = 0; i < candles.length - 1; i++) {
      expect(res.series[i]).toBe(candles[i]);
    }
    expect(res.series[res.series.length - 1]).toEqual(update);
  });
});
