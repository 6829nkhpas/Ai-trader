// radarData.normalizeScan.test.ts — the Radar's payload trust boundary.
//
// Why this exists: the browser transport does `(await res.json()) as RadarScan`,
// a blind cast, so the TypeScript type is a promise the runtime does not keep.
// `QuantRadar` then reads `scan.patterns.length` and `scan.strategies.map(...)`
// DURING RENDER — inside a `useMemo` for the detection badge, and again in each
// symbol card. An undefined there throws a TypeError while rendering, which is
// what produced "Application error: a client-side exception has occurred" after
// pressing Enter in the Radar.
//
// `normalizeScan` is the single choke point both scan entrypoints go through, so
// these cases are the whole guarantee that the render path cannot be handed a
// scan without its two arrays.

import { describe, expect, it } from 'vitest';

import { normalizeScan } from '../radarData';

/** A complete, well-formed scan as the Rust tool-server emits it. */
const fullScan = {
  symbol: 'RELIANCE',
  timeframe: '10m',
  candle_count: 240,
  last_close: 1304.8,
  last_time: 1_700_000_000,
  trend_score: 42,
  momentum_state: 'OVERBOUGHT',
  volatility_state: 'EXPANDING',
  volume_flow_state: 'ACCUMULATION',
  patterns: [{ name: 'Hammer', bias: 'BULLISH', candle_index: 3, time: 1, open: 1, high: 2, low: 0, close: 1.5 }],
  strategies: [{ name: 'Golden Cross', bias: 'BULLISH', candle_index: 5, time: 2, price: 1304, level: null }],
};

describe('normalizeScan', () => {
  it('passes a well-formed scan through with its detections intact', () => {
    const scan = normalizeScan(fullScan)!;
    expect(scan).not.toBeNull();
    expect(scan.symbol).toBe('RELIANCE');
    expect(scan.trend_score).toBe(42);
    expect(scan.patterns).toHaveLength(1);
    expect(scan.strategies).toHaveLength(1);
    expect(scan.momentum_state).toBe('OVERBOUGHT');
  });

  it('rejects the payloads that used to crash the render path', () => {
    // Every one of these previously reached `scan.patterns.length`.
    expect(normalizeScan(null)).toBeNull();
    expect(normalizeScan(undefined)).toBeNull();
    expect(normalizeScan({})).toBeNull();
    expect(normalizeScan('a string')).toBeNull();
    expect(normalizeScan(42)).toBeNull();
    // A gateway/proxy error envelope — a 200 whose body is not a scan.
    expect(normalizeScan({ error: 'pool not ready' })).toBeNull();
    // Present but wrong type.
    expect(normalizeScan({ patterns: 'nope', strategies: 'nope' })).toBeNull();
  });

  it('accepts a scan with one array present and defaults the other to empty', () => {
    // A partial response is still renderable: the guarantee the UI needs is that
    // BOTH fields are arrays, not that both were sent.
    const onlyPatterns = normalizeScan({ ...fullScan, strategies: undefined })!;
    expect(onlyPatterns).not.toBeNull();
    expect(onlyPatterns.patterns).toHaveLength(1);
    expect(Array.isArray(onlyPatterns.strategies)).toBe(true);
    expect(onlyPatterns.strategies).toHaveLength(0);

    const onlyStrategies = normalizeScan({ ...fullScan, patterns: undefined })!;
    expect(Array.isArray(onlyStrategies.patterns)).toBe(true);
    expect(onlyStrategies.patterns).toHaveLength(0);
  });

  it('always yields arrays, so the render-path dereferences are total', () => {
    // The property that matters: anything non-null out of here can be rendered.
    const inputs: unknown[] = [
      fullScan,
      { patterns: [] },
      { strategies: [] },
      { ...fullScan, patterns: [], strategies: [] },
    ];
    for (const input of inputs) {
      const scan = normalizeScan(input);
      if (scan === null) continue;
      expect(Array.isArray(scan.patterns)).toBe(true);
      expect(Array.isArray(scan.strategies)).toBe(true);
      // These are the two expressions QuantRadar evaluates during render.
      expect(() => scan.patterns.length + scan.strategies.length).not.toThrow();
    }
  });

  it('substitutes neutral defaults for missing scalars rather than undefined', () => {
    // `undefined` here would render as "undefined" / NaN in the summary chips.
    const scan = normalizeScan({ patterns: [], strategies: [] })!;
    expect(scan.trend_score).toBe(0);
    expect(scan.candle_count).toBe(0);
    expect(scan.momentum_state).toBe('NEUTRAL');
    expect(scan.volatility_state).toBe('NORMAL');
    expect(scan.volume_flow_state).toBe('NEUTRAL');
    expect(scan.symbol).toBe('');
  });

  it('discards non-finite numbers', () => {
    const scan = normalizeScan({
      ...fullScan,
      trend_score: Number.NaN,
      last_close: Number.POSITIVE_INFINITY,
    })!;
    expect(scan.trend_score).toBe(0);
    expect(scan.last_close).toBe(0);
  });
});
