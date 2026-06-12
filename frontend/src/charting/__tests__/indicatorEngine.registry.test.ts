// Feature: professional-charting-suite
//
// Unit tests for indicator registry completeness (Task 3.7).
//
// These tests assert that the full overlay catalogue (Requirement 2.1) and the
// full oscillator catalogue (Requirement 3.1) are registered, correctly typed,
// and discoverable through every public lookup surface the registry exposes:
//   - getIndicator(id)        — direct lookup by id
//   - listIndicators()        — full enumeration
//   - searchIndicators(name)  — name-based search (Requirement 4.2)
//
// Unlike the property-based completeness test (which exercises plot structure),
// this suite pins the *catalogue* itself: exactly which indicators exist, their
// kind, and that none are missing or mis-categorised.

import { describe, it, expect } from 'vitest';

import {
  INDICATOR_REGISTRY,
  getIndicator,
  listIndicators,
  searchIndicators,
} from '@/charting/engines';
import type { OverlayId, OscillatorId } from '@/charting/engines';

// Overlay indicators required by Requirement 2.1.
const OVERLAY_IDS: OverlayId[] = [
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
];

// Oscillator indicators required by Requirement 3.1.
const OSCILLATOR_IDS: OscillatorId[] = [
  'rsi',
  'macd',
  'stochastic',
  'adx',
  'atr',
  'obv',
  'cci',
  'mfi',
  'williams-r',
];

describe('indicator registry completeness', () => {
  describe('overlays (Requirement 2.1)', () => {
    it('registers all 10 overlay indicators', () => {
      expect(OVERLAY_IDS).toHaveLength(10);
      for (const id of OVERLAY_IDS) {
        expect(INDICATOR_REGISTRY[id], `overlay "${id}" not in registry`).toBeDefined();
      }
    });

    it.each(OVERLAY_IDS)('overlay "%s" is registered with kind "overlay"', (id) => {
      const def = getIndicator(id);
      expect(def, `overlay "${id}" missing from getIndicator`).toBeDefined();
      expect(def!.id).toBe(id);
      expect(def!.kind).toBe('overlay');
    });

    it.each(OVERLAY_IDS)('overlay "%s" appears in listIndicators()', (id) => {
      const ids = listIndicators().map((d) => d.id);
      expect(ids).toContain(id);
    });

    it.each(OVERLAY_IDS)('overlay "%s" is discoverable by name via searchIndicators', (id) => {
      const def = getIndicator(id)!;
      const results = searchIndicators(def.name);
      expect(
        results.map((d) => d.id),
        `searching "${def.name}" should surface "${id}"`,
      ).toContain(id);
    });
  });

  describe('oscillators (Requirement 3.1)', () => {
    it('registers all 9 oscillator indicators', () => {
      expect(OSCILLATOR_IDS).toHaveLength(9);
      for (const id of OSCILLATOR_IDS) {
        expect(INDICATOR_REGISTRY[id], `oscillator "${id}" not in registry`).toBeDefined();
      }
    });

    it.each(OSCILLATOR_IDS)('oscillator "%s" is registered with kind "oscillator"', (id) => {
      const def = getIndicator(id);
      expect(def, `oscillator "${id}" missing from getIndicator`).toBeDefined();
      expect(def!.id).toBe(id);
      expect(def!.kind).toBe('oscillator');
    });

    it.each(OSCILLATOR_IDS)('oscillator "%s" appears in listIndicators()', (id) => {
      const ids = listIndicators().map((d) => d.id);
      expect(ids).toContain(id);
    });

    it.each(OSCILLATOR_IDS)('oscillator "%s" is discoverable by name via searchIndicators', (id) => {
      const def = getIndicator(id)!;
      const results = searchIndicators(def.name);
      expect(
        results.map((d) => d.id),
        `searching "${def.name}" should surface "${id}"`,
      ).toContain(id);
    });
  });

  describe('registry catalogue', () => {
    it('contains exactly the 19 documented indicators and nothing else', () => {
      const all = listIndicators().map((d) => d.id).sort();
      const expected = [...OVERLAY_IDS, ...OSCILLATOR_IDS].sort();
      expect(all).toEqual(expected);
    });

    it('every registered indicator has a non-empty display name', () => {
      for (const def of listIndicators()) {
        expect(typeof def.name, `"${def.id}" name should be a string`).toBe('string');
        expect(def.name.trim().length, `"${def.id}" name should be non-empty`).toBeGreaterThan(0);
      }
    });

    it('searchIndicators with an empty query returns the full catalogue', () => {
      expect(searchIndicators('').length).toBe(listIndicators().length);
      expect(searchIndicators('   ').length).toBe(listIndicators().length);
    });
  });
});
