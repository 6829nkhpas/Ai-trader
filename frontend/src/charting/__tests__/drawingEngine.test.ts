// Feature: professional-charting-suite
//
// Unit tests for the pure drawing geometry/validation helpers (task 6.1).
// These cover specific examples and edge cases for the tool registry, anchor
// completeness, Fibonacci levels, magnet snapping, coordinate round-trip, and
// clearing unlocked drawings. The universal properties (12-17) are exercised
// separately by the property tests (tasks 6.3-6.8).

import { describe, it, expect } from 'vitest';

import {
  TOOL_REGISTRY,
  isComplete,
  fibLevels,
  FIB_RATIOS,
  magnetSnap,
  pointToPixel,
  pixelToPoint,
  clearUnlocked,
} from '@/charting/engines';
import type { CoordinateViewport } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { Drawing, Point } from '@/store/useChartUIStore';

describe('TOOL_REGISTRY', () => {
  it('covers every drawing category from Requirement 5.1', () => {
    const categories = new Set(Object.values(TOOL_REGISTRY).map((s) => s.category));
    expect(categories).toEqual(
      new Set(['trend-line', 'channel', 'fibonacci', 'shape', 'text', 'projection']),
    );
  });

  it('assigns single-point tools an anchor count of 1', () => {
    expect(TOOL_REGISTRY['horizontal-line'].anchorCount).toBe(1);
    expect(TOOL_REGISTRY['vertical-line'].anchorCount).toBe(1);
    expect(TOOL_REGISTRY['text'].anchorCount).toBe(1);
  });

  it('assigns two-point tools an anchor count of 2', () => {
    expect(TOOL_REGISTRY['trendline'].anchorCount).toBe(2);
    expect(TOOL_REGISTRY['rectangle'].anchorCount).toBe(2);
    expect(TOOL_REGISTRY['fib-retracement'].anchorCount).toBe(2);
  });

  it('marks open-ended tools as multi', () => {
    expect(TOOL_REGISTRY['path'].anchorCount).toBe('multi');
    expect(TOOL_REGISTRY['polyline'].anchorCount).toBe('multi');
  });
});

describe('isComplete', () => {
  const pt = (time: number, price: number): Point => ({ time, price });

  it('is false before the required anchors are placed (cancellation)', () => {
    expect(isComplete('trendline', [])).toBe(false);
    expect(isComplete('trendline', [pt(1, 10)])).toBe(false);
  });

  it('is true once the exact anchor count is met', () => {
    expect(isComplete('horizontal-line', [pt(1, 10)])).toBe(true);
    expect(isComplete('trendline', [pt(1, 10), pt(2, 20)])).toBe(true);
  });

  it('requires at least 3 anchors for multi tools', () => {
    expect(isComplete('polyline', [pt(1, 1), pt(2, 2)])).toBe(false);
    expect(isComplete('polyline', [pt(1, 1), pt(2, 2), pt(3, 3)])).toBe(true);
  });
});

describe('fibLevels', () => {
  it('returns the canonical ratios anchored to the price range', () => {
    const levels = fibLevels(100, 200);
    expect(levels.map((l) => l.ratio)).toEqual([...FIB_RATIOS]);
    expect(levels[0]).toEqual({ ratio: 0, price: 100 });
    expect(levels[levels.length - 1]).toEqual({ ratio: 1.0, price: 200 });
    const half = levels.find((l) => l.ratio === 0.5);
    expect(half?.price).toBeCloseTo(150, 10);
  });

  it('interpolates correctly when anchors are inverted', () => {
    const levels = fibLevels(200, 100);
    expect(levels[0].price).toBe(200);
    expect(levels[levels.length - 1].price).toBe(100);
    expect(levels.find((l) => l.ratio === 0.236)?.price).toBeCloseTo(200 - 100 * 0.236, 10);
  });
});

describe('magnetSnap', () => {
  const candle: ChartCandle = { time: 1000, open: 10, high: 14, low: 9, close: 12 };

  it('snaps to the nearest OHLC when within the pixel threshold', () => {
    // pointer price 11.8 is 0.2 from close (12); at 5 px/price => 1 px <= 10
    const snapped = magnetSnap({ time: 1000, price: 11.8 }, candle, 5);
    expect(snapped.price).toBe(12);
    expect(snapped.time).toBe(1000);
  });

  it('places the anchor at the pointer when no OHLC is within threshold', () => {
    // pointer price 11.0 — nearest is close (12) at distance 1.0 * 20 = 20 px > 10
    const pointer = { time: 1000, price: 11.0 };
    const snapped = magnetSnap(pointer, candle, 20);
    expect(snapped).toEqual(pointer);
  });

  it('honors a custom threshold', () => {
    const pointer = { time: 1000, price: 11.0 };
    // nearest OHLC is open (10) at distance 1.0 * 20 = 20 px, within a 25 px threshold
    expect(magnetSnap(pointer, candle, 20, 25).price).toBe(10);
  });
});

describe('pointToPixel / pixelToPoint', () => {
  const vp: CoordinateViewport = {
    timeFrom: 1000,
    timeTo: 2000,
    priceMin: 50,
    priceMax: 150,
    width: 800,
    height: 400,
  };

  it('maps the corners of the viewport', () => {
    expect(pointToPixel({ time: 1000, price: 150 }, vp)).toEqual({ x: 0, y: 0 });
    expect(pointToPixel({ time: 2000, price: 50 }, vp)).toEqual({ x: 800, y: 400 });
  });

  it('round-trips an anchor back to its original coordinates', () => {
    const anchor: Point = { time: 1500, price: 100 };
    const px = pointToPixel(anchor, vp);
    const back = pixelToPoint(px, vp);
    expect(back.time).toBeCloseTo(anchor.time, 6);
    expect(back.price).toBeCloseTo(anchor.price, 6);
  });
});

describe('clearUnlocked', () => {
  it('keeps only locked drawings', () => {
    const drawings: Drawing[] = [
      { id: 'a', tool: 'trendline', points: [], locked: true },
      { id: 'b', tool: 'rectangle', points: [], locked: false },
      { id: 'c', tool: 'text', points: [] },
      { id: 'd', tool: 'ray', points: [], locked: true },
    ];
    const remaining = clearUnlocked(drawings);
    expect(remaining.map((d) => d.id)).toEqual(['a', 'd']);
  });

  it('returns an empty array when nothing is locked', () => {
    const drawings: Drawing[] = [
      { id: 'a', tool: 'trendline', points: [] },
      { id: 'b', tool: 'rectangle', points: [], locked: false },
    ];
    expect(clearUnlocked(drawings)).toEqual([]);
  });
});
