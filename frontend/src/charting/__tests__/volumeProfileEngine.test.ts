// Feature: professional-charting-suite
//
// Unit tests for the pure volume-profile binning engine (task 9.1). These
// cover specific examples and edge cases for row binning, volume conservation,
// POC selection, value-area expansion, zero-volume null markers, and invalid
// fixed-range rejection. The universal properties (23, 24, 25) are exercised
// separately by the property tests (tasks 9.3-9.5).

import { describe, it, expect } from 'vitest';

import {
  buildProfile,
  valueArea,
  DEFAULT_PROFILE_ROWS,
  type VolumeProfile,
} from '@/charting/engines';
import type { ChartCandle, VolumeBar } from '@/charting/types';

const candle = (time: number, low: number, high: number): ChartCandle => ({
  time,
  open: low,
  high,
  low,
  close: high,
});

const vol = (time: number, value: number): VolumeBar => ({
  time,
  value,
  color: '#000',
});

describe('buildProfile - row binning', () => {
  it('defaults to 24 rows when no row count is supplied', () => {
    const candles = [candle(1, 100, 110), candle(2, 100, 110)];
    const volumes = [vol(1, 50), vol(2, 50)];
    const profile = buildProfile(candles, volumes, {});
    expect(profile.rows).toHaveLength(DEFAULT_PROFILE_ROWS);
  });

  it('produces exactly the configured number of rows', () => {
    const candles = [candle(1, 100, 110)];
    const volumes = [vol(1, 100)];
    for (const rows of [1, 5, 24, 100, 1000]) {
      expect(buildProfile(candles, volumes, { rows }).rows).toHaveLength(rows);
    }
  });

  it('clamps the row count into the accepted 1-1000 range', () => {
    const candles = [candle(1, 100, 110)];
    const volumes = [vol(1, 100)];
    expect(buildProfile(candles, volumes, { rows: 0 }).rows).toHaveLength(1);
    expect(buildProfile(candles, volumes, { rows: -5 }).rows).toHaveLength(1);
    expect(buildProfile(candles, volumes, { rows: 5000 }).rows).toHaveLength(1000);
  });

  it('conserves the total traded volume across the rows', () => {
    const candles = [candle(1, 100, 110), candle(2, 105, 115), candle(3, 100, 120)];
    const volumes = [vol(1, 30), vol(2, 50), vol(3, 20)];
    const profile = buildProfile(candles, volumes, { rows: 12 });
    const summed = profile.rows.reduce((s, r) => s + r.volume, 0);
    expect(summed).toBeCloseTo(100, 9);
    expect(profile.totalVolume).toBeCloseTo(100, 9);
  });

  it('treats candles with no matching volume bar as zero volume', () => {
    const candles = [candle(1, 100, 110), candle(2, 100, 110)];
    const volumes = [vol(1, 40)]; // candle 2 has no volume bar
    const profile = buildProfile(candles, volumes, { rows: 10 });
    expect(profile.totalVolume).toBeCloseTo(40, 9);
  });
});

describe('buildProfile - POC and value area', () => {
  it('marks the greatest-volume row as the POC', () => {
    // Three candles each pinned to a distinct narrow band so each lands in its
    // own row; the middle band carries the most volume.
    const candles = [candle(1, 100, 101), candle(2, 110, 111), candle(3, 120, 121)];
    const volumes = [vol(1, 10), vol(2, 100), vol(3, 20)];
    const profile = buildProfile(candles, volumes, { rows: 30 });

    const pocRow = profile.rows.reduce((a, b) => (b.volume > a.volume ? b : a));
    expect(profile.poc).not.toBeNull();
    expect(profile.poc).toBeGreaterThanOrEqual(pocRow.priceLow);
    expect(profile.poc).toBeLessThanOrEqual(pocRow.priceHigh);
  });

  it('produces a contiguous value area with VAL <= POC <= VAH', () => {
    const candles = [candle(1, 100, 101), candle(2, 110, 111), candle(3, 120, 121)];
    const volumes = [vol(1, 10), vol(2, 100), vol(3, 20)];
    const profile = buildProfile(candles, volumes, { rows: 30, valuePercent: 70 });

    expect(profile.val).not.toBeNull();
    expect(profile.vah).not.toBeNull();
    expect(profile.poc).not.toBeNull();
    expect(profile.val!).toBeLessThanOrEqual(profile.poc!);
    expect(profile.vah!).toBeGreaterThanOrEqual(profile.poc!);

    // The in-value-area rows are exactly a contiguous block.
    const flags = profile.rows.map((r) => r.inValueArea);
    const first = flags.indexOf(true);
    const last = flags.lastIndexOf(true);
    for (let i = first; i <= last; i += 1) expect(flags[i]).toBe(true);
  });

  it('includes every row when the value percent is 100', () => {
    const candles = [candle(1, 100, 101), candle(2, 110, 111)];
    const volumes = [vol(1, 60), vol(2, 40)];
    const profile = buildProfile(candles, volumes, { rows: 20, valuePercent: 100 });
    const vaVolume = profile.rows
      .filter((r) => r.inValueArea)
      .reduce((s, r) => s + r.volume, 0);
    expect(vaVolume).toBeCloseTo(profile.totalVolume, 9);
  });
});

describe('buildProfile - zero volume (Requirement 7.9)', () => {
  it('returns null POC/VAH/VAL markers when total volume is zero', () => {
    const candles = [candle(1, 100, 110), candle(2, 105, 115)];
    const volumes = [vol(1, 0), vol(2, 0)];
    const profile = buildProfile(candles, volumes, { rows: 24 });
    expect(profile.totalVolume).toBe(0);
    expect(profile.poc).toBeNull();
    expect(profile.vah).toBeNull();
    expect(profile.val).toBeNull();
    // Still produces the configured number of rows.
    expect(profile.rows).toHaveLength(24);
    expect(profile.rows.every((r) => !r.inValueArea)).toBe(true);
  });

  it('returns an empty profile with null markers when the range has no candles', () => {
    const profile = buildProfile([], [], { rows: 8 });
    expect(profile.rows).toHaveLength(8);
    expect(profile.poc).toBeNull();
    expect(profile.totalVolume).toBe(0);
  });
});

describe('buildProfile - fixed range (Requirements 7.1, 7.6, 7.10)', () => {
  const candles = [
    candle(10, 100, 110),
    candle(20, 105, 115),
    candle(30, 110, 120),
    candle(40, 115, 125),
  ];
  const volumes = [vol(10, 10), vol(20, 20), vol(30, 30), vol(40, 40)];

  it('profiles only the inclusive span between the anchors', () => {
    const profile = buildProfile(candles, volumes, {
      rows: 12,
      range: { kind: 'fixed', start: 20, end: 30 },
    });
    // Only candles at time 20 and 30 contribute: 20 + 30 = 50.
    expect(profile.totalVolume).toBeCloseTo(50, 9);
  });

  it('rejects an invalid fixed range and retains the prior profile', () => {
    const prior: VolumeProfile = buildProfile(candles, volumes, {
      rows: 12,
      range: { kind: 'fixed', start: 10, end: 40 },
    });

    const rejected = buildProfile(candles, volumes, {
      rows: 12,
      range: { kind: 'fixed', start: 30, end: 20 }, // end <= start
      previousProfile: prior,
    });
    expect(rejected).toBe(prior);

    const equalAnchors = buildProfile(candles, volumes, {
      rows: 12,
      range: { kind: 'fixed', start: 25, end: 25 },
      previousProfile: prior,
    });
    expect(equalAnchors).toBe(prior);
  });
});

describe('valueArea', () => {
  it('collapses to the POC index when total volume is zero', () => {
    expect(valueArea([0, 0, 0], 1, 70)).toEqual({ loIndex: 1, hiIndex: 1 });
  });

  it('expands outward absorbing the larger adjacent row', () => {
    // POC at index 2; right side is heavier, so it should be absorbed first.
    const rowVolumes = [1, 2, 10, 5, 1];
    const { loIndex, hiIndex } = valueArea(rowVolumes, 2, 70);
    // total = 19, target = 13.3 → POC(10) + above(5) = 15 >= 13.3.
    expect(loIndex).toBe(2);
    expect(hiIndex).toBe(3);
  });
});
