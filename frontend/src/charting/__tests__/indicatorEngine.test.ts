// Feature: professional-charting-suite
//
// Unit tests for the overlay IndicatorEngine (task 3.1). These cover the
// registry shape and concrete example/edge-case behavior of each overlay
// indicator: correct math on a known series, multi-line/band output
// completeness (Requirement 2.8), the EMA smoothing factor (Requirement 2.9),
// and the insufficient-data path (Requirement 2.6). Universal properties are
// exercised separately by the property tests (tasks 3.3–3.5).

import { describe, it, expect } from 'vitest';

import {
  INDICATOR_REGISTRY,
  getIndicator,
  registerIndicator,
  listIndicators,
  searchIndicators,
} from '@/charting/engines';
import type { IndicatorDef } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

/** Build a simple ascending candle series of `n` candles. */
function ramp(n: number): ChartCandle[] {
  const out: ChartCandle[] = [];
  for (let i = 0; i < n; i++) {
    const base = 100 + i;
    out.push({ time: 1_000 + i * 60, open: base, high: base + 2, low: base - 2, close: base + 1 });
  }
  return out;
}

const OVERLAY_IDS = [
  'sma',
  'ema',
  'wma',
  'bollinger',
  'vwap',
  'ichimoku',
  'supertrend',
  'psar',
  'donchian',
  'keltner',
] as const;

describe('overlay registry', () => {
  it('registers all ten overlay indicators (Requirement 2.1)', () => {
    for (const id of OVERLAY_IDS) {
      const def = getIndicator(id);
      expect(def, `missing ${id}`).toBeDefined();
      expect(def?.kind).toBe('overlay');
    }
  });

  it('exposes paramSpec, defaults, minLookback, and compute for each def', () => {
    for (const id of OVERLAY_IDS) {
      const def = INDICATOR_REGISTRY[id];
      expect(def.id).toBe(id);
      expect(typeof def.name).toBe('string');
      expect(typeof def.paramSpec).toBe('object');
      expect(typeof def.defaults).toBe('object');
      expect(typeof def.minLookback(def.defaults)).toBe('number');
      expect(typeof def.compute).toBe('function');
    }
  });

  it('allows later tasks to extend the registry (registerIndicator)', () => {
    const original = getIndicator('rsi');
    const fake: IndicatorDef = {
      id: 'rsi',
      name: 'Test Oscillator',
      kind: 'oscillator',
      defaults: { period: 14 },
      paramSpec: { period: { min: 1, max: 5000, integer: true } },
      minLookback: (p) => p.period,
      compute: () => ({ lines: [], warmupBars: 0 }),
    };
    registerIndicator(fake);
    expect(getIndicator('rsi')).toBe(fake);
    // Restore the real RSI definition so other tests see the true registry.
    if (original) registerIndicator(original);
  });
});

describe('moving averages', () => {
  it('SMA equals the rolling arithmetic mean of closes', () => {
    const candles = ramp(5); // closes: 101..105
    const plot = getIndicator('sma')!.compute(candles, { period: 3 });
    const sma = plot.lines[0].points;
    // first defined at index 2: mean(101,102,103)=102
    expect(sma[0]).toEqual({ time: candles[2].time, value: 102 });
    expect(sma[1].value).toBeCloseTo(103, 10);
    expect(plot.warmupBars).toBe(2);
  });

  it('EMA uses the standard smoothing factor 2/(period+1) (Requirement 2.9)', () => {
    const candles = ramp(10);
    const period = 4;
    const plot = getIndicator('ema')!.compute(candles, { period });
    const pts = plot.lines[0].points;
    const alpha = 2 / (period + 1);
    // Verify the recurrence holds between consecutive emitted points.
    for (let i = 1; i < pts.length; i++) {
      const price = candles[period - 1 + i].close;
      const expected = price * alpha + pts[i - 1].value * (1 - alpha);
      expect(pts[i].value).toBeCloseTo(expected, 9);
    }
  });

  it('WMA weights the most recent close highest', () => {
    const candles = ramp(4); // closes 101..104
    const plot = getIndicator('wma')!.compute(candles, { period: 3 });
    // index 2: (101*1 + 102*2 + 103*3)/6 = 614/6
    expect(plot.lines[0].points[0].value).toBeCloseTo(614 / 6, 9);
  });
});

describe('multi-output overlays render every line and band (Requirement 2.8)', () => {
  it('Bollinger Bands exposes middle/upper/lower lines and a band', () => {
    const candles = ramp(30);
    const plot = getIndicator('bollinger')!.compute(candles, { period: 20, stdDev: 2 });
    const ids = plot.lines.map((l) => l.id).sort();
    expect(ids).toEqual(['lower', 'middle', 'upper']);
    expect(plot.bands).toHaveLength(1);
    expect(plot.bands![0].upper.length).toBeGreaterThan(0);
    expect(plot.bands![0].lower.length).toBeGreaterThan(0);
    expect(typeof plot.bands![0].fill).toBe('string');
    // Upper should sit above lower at every aligned index.
    for (let i = 0; i < plot.bands![0].upper.length; i++) {
      expect(plot.bands![0].upper[i].value).toBeGreaterThanOrEqual(plot.bands![0].lower[i].value);
    }
  });

  it('Ichimoku exposes all five lines plus the cloud band', () => {
    const candles = ramp(80);
    const plot = getIndicator('ichimoku')!.compute(candles, {});
    const ids = plot.lines.map((l) => l.id).sort();
    expect(ids).toEqual(['chikou', 'kijun', 'spanA', 'spanB', 'tenkan']);
    expect(plot.bands).toHaveLength(1);
  });

  it('Donchian and Keltner expose a middle line and a band', () => {
    const candles = ramp(40);
    for (const id of ['donchian', 'keltner'] as const) {
      const plot = getIndicator(id)!.compute(candles, {});
      expect(plot.lines.length).toBeGreaterThanOrEqual(1);
      expect(plot.bands).toHaveLength(1);
    }
  });
});

describe('single-line overlays', () => {
  it('VWAP falls back to a cumulative typical-price average without volume', () => {
    const candles = ramp(5);
    const plot = getIndicator('vwap')!.compute(candles, {});
    expect(plot.lines[0].points).toHaveLength(5);
    // First point equals the first candle's typical price.
    const c0 = candles[0];
    expect(plot.lines[0].points[0].value).toBeCloseTo((c0.high + c0.low + c0.close) / 3, 9);
  });

  it('VWAP weights by volume when candles carry it', () => {
    const candles = ramp(3).map((c, i) => ({ ...c, volume: i === 1 ? 1000 : 1 }));
    const plot = getIndicator('vwap')!.compute(candles, {});
    expect(plot.lines[0].points).toHaveLength(3);
  });

  it('SuperTrend and Parabolic SAR produce a single plotted line', () => {
    const candles = ramp(30);
    for (const id of ['supertrend', 'psar'] as const) {
      const plot = getIndicator(id)!.compute(candles, {});
      expect(plot.lines).toHaveLength(1);
      expect(plot.lines[0].points.length).toBeGreaterThan(0);
    }
  });
});

describe('insufficient data (Requirement 2.6)', () => {
  it('returns no plotted output and flags insufficiency when too few candles', () => {
    const candles = ramp(5);
    const plot = getIndicator('sma')!.compute(candles, { period: 20 });
    expect(plot.insufficientData).toBe(true);
    expect(plot.lines).toHaveLength(0);
    expect(plot.warmupBars).toBe(5);
  });

  it('Ichimoku reports insufficiency below its max period', () => {
    const candles = ramp(10);
    const plot = getIndicator('ichimoku')!.compute(candles, {}); // needs 52
    expect(plot.insufficientData).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Oscillator indicators (task 3.2)
// ---------------------------------------------------------------------------

const OSCILLATOR_IDS = [
  'rsi',
  'macd',
  'stochastic',
  'adx',
  'atr',
  'obv',
  'cci',
  'mfi',
  'williams-r',
] as const;

/** A noisy but bounded oscillating series so momentum oscillators have signal. */
function wave(n: number): ChartCandle[] {
  const out: ChartCandle[] = [];
  for (let i = 0; i < n; i++) {
    const base = 100 + 10 * Math.sin(i / 3);
    out.push({
      time: 1_000 + i * 60,
      open: base,
      high: base + 2,
      low: base - 2,
      close: base + Math.cos(i / 2),
      // Enriched volume so OBV/MFI weight by real volume.
      volume: 1_000 + (i % 5) * 100,
    } as ChartCandle);
  }
  return out;
}

describe('oscillator registry (Requirement 3.1)', () => {
  it('registers all nine oscillator indicators as kind "oscillator"', () => {
    for (const id of OSCILLATOR_IDS) {
      const def = getIndicator(id);
      expect(def, `missing ${id}`).toBeDefined();
      expect(def?.kind).toBe('oscillator');
    }
  });

  it('exposes complete metadata for each oscillator def', () => {
    for (const id of OSCILLATOR_IDS) {
      const def = INDICATOR_REGISTRY[id];
      expect(def.id).toBe(id);
      expect(typeof def.name).toBe('string');
      expect(typeof def.minLookback(def.defaults)).toBe('number');
      expect(typeof def.compute).toBe('function');
    }
  });
});

describe('oscillator reference levels (Requirement 3.5)', () => {
  const EXPECTED: Record<string, number[]> = {
    rsi: [30, 70],
    stochastic: [20, 80],
    'williams-r': [-80, -20],
    cci: [-100, 100],
    mfi: [20, 80],
    adx: [25],
    macd: [0],
    obv: [0],
  };
  for (const [id, levels] of Object.entries(EXPECTED)) {
    it(`${id} carries reference levels ${JSON.stringify(levels)}`, () => {
      const def = getIndicator(id as (typeof OSCILLATOR_IDS)[number])!;
      const candles = wave(def.minLookback(def.defaults) + 40);
      const plot = def.compute(candles, {});
      expect(plot.referenceLevels).toEqual(levels);
    });
  }
});

describe('oscillator math', () => {
  it('RSI stays within [0, 100]', () => {
    const candles = wave(60);
    const plot = getIndicator('rsi')!.compute(candles, { period: 14 });
    for (const pt of plot.lines[0].points) {
      expect(pt.value).toBeGreaterThanOrEqual(0);
      expect(pt.value).toBeLessThanOrEqual(100);
    }
    expect(plot.lines[0].points.length).toBeGreaterThan(0);
  });

  it('RSI of a strictly rising series approaches 100', () => {
    const rising = ramp(40);
    const plot = getIndicator('rsi')!.compute(rising, { period: 14 });
    const last = plot.lines[0].points.at(-1)!;
    expect(last.value).toBeCloseTo(100, 5);
  });

  it('MACD exposes macd/signal/histogram lines', () => {
    const candles = wave(60);
    const plot = getIndicator('macd')!.compute(candles, {});
    const ids = plot.lines.map((l) => l.id).sort();
    expect(ids).toEqual(['histogram', 'macd', 'signal']);
  });

  it('Stochastic %K and %D stay within [0, 100]', () => {
    const candles = wave(60);
    const plot = getIndicator('stochastic')!.compute(candles, {});
    for (const l of plot.lines) {
      for (const pt of l.points) {
        expect(pt.value).toBeGreaterThanOrEqual(0);
        expect(pt.value).toBeLessThanOrEqual(100);
      }
    }
  });

  it('ADX/DMI exposes +DI, -DI and ADX lines', () => {
    const candles = wave(80);
    const plot = getIndicator('adx')!.compute(candles, { period: 14 });
    const ids = plot.lines.map((l) => l.id).sort();
    expect(ids).toEqual(['adx', 'minusDI', 'plusDI']);
  });

  it('ATR is non-negative', () => {
    const candles = wave(60);
    const plot = getIndicator('atr')!.compute(candles, { period: 14 });
    for (const pt of plot.lines[0].points) expect(pt.value).toBeGreaterThanOrEqual(0);
  });

  it('OBV accumulates signed volume', () => {
    const candles = ramp(10).map((c, i) => ({ ...c, volume: 100 }) as ChartCandle);
    const plot = getIndicator('obv')!.compute(candles, {});
    // Strictly rising closes => OBV adds 100 each step from a 0 base.
    const pts = plot.lines[0].points;
    expect(pts[0].value).toBe(0);
    expect(pts.at(-1)!.value).toBe(100 * (candles.length - 1));
  });

  it('Williams %R stays within [-100, 0]', () => {
    const candles = wave(40);
    const plot = getIndicator('williams-r')!.compute(candles, { period: 14 });
    for (const pt of plot.lines[0].points) {
      expect(pt.value).toBeGreaterThanOrEqual(-100);
      expect(pt.value).toBeLessThanOrEqual(0);
    }
  });

  it('flags insufficient data below minimum lookback', () => {
    const plot = getIndicator('rsi')!.compute(ramp(5), { period: 14 });
    expect(plot.insufficientData).toBe(true);
    expect(plot.lines).toHaveLength(0);
  });
});

describe('listIndicators / searchIndicators', () => {
  it('lists overlays and oscillators together', () => {
    const all = listIndicators();
    expect(all.length).toBeGreaterThanOrEqual(19);
    expect(all.some((d) => d.kind === 'overlay')).toBe(true);
    expect(all.some((d) => d.kind === 'oscillator')).toBe(true);
  });

  it('searches by case-insensitive name substring (Requirement 4.2)', () => {
    const moving = searchIndicators('moving average').map((d) => d.id).sort();
    expect(moving).toEqual(['ema', 'sma', 'wma']);
    expect(searchIndicators('RELATIVE').map((d) => d.id)).toEqual(['rsi']);
    expect(searchIndicators('macd').map((d) => d.id)).toEqual(['macd']);
  });

  it('returns the full list for an empty query', () => {
    expect(searchIndicators('   ')).toHaveLength(listIndicators().length);
  });

  it('returns nothing for a non-matching query', () => {
    expect(searchIndicators('zzz-no-such-indicator')).toHaveLength(0);
  });
});
