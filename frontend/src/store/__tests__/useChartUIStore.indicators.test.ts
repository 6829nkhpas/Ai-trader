// Feature: professional-charting-suite
//
// Unit tests for the Indicator Manager store slice (task 5.1). These verify the
// per-symbol active-indicator invariants required by the spec:
//  - unknown symbols resolve to an empty list (Requirement 4.11)
//  - duplicate adds (same indicator id + identical params) are rejected (4.4)
//  - the per-symbol list is capped at 50 entries (4.5)
//  - setIndicatorParams validates against the indicator's paramSpec and retains
//    the previous params on rejection
//  - toggleIndicatorVisible flips visibility WITHOUT discarding configuration (4.7)
//  - removeIndicator drops an instance (4.8)
//
// The universal versions of these invariants are exercised by the dedicated
// property tests (tasks 5.4/5.5).

import { describe, it, expect, beforeEach } from 'vitest';

import {
  useChartUIStore,
  MAX_INDICATORS_PER_SYMBOL,
} from '@/store/useChartUIStore';

const SYMBOL = 'AAPL';

function store() {
  return useChartUIStore.getState();
}

beforeEach(() => {
  // Reset the per-symbol active-indicator map between tests.
  useChartUIStore.setState({ activeIndicators: {} });
});

describe('indicator store slice', () => {
  it('returns an empty list for an unknown symbol (Req 4.11)', () => {
    expect(store().getActiveIndicators('UNKNOWN')).toEqual([]);
  });

  it('adds an indicator with registry defaults, visible, on the price pane', () => {
    const result = store().addIndicator(SYMBOL, 'sma');
    expect(result.ok).toBe(true);

    const list = store().getActiveIndicators(SYMBOL);
    expect(list).toHaveLength(1);
    expect(list[0].indicatorId).toBe('sma');
    expect(list[0].visible).toBe(true);
    expect(list[0].paneId).toBeNull();
    // Params seeded from the registry defaults (SMA has a period default).
    expect(typeof list[0].params.period).toBe('number');
    // A unique instance id distinct from the indicator id.
    expect(list[0].instanceId).not.toBe('sma');
  });

  it('rejects an unknown indicator id', () => {
    // @ts-expect-error intentionally invalid id
    const result = store().addIndicator(SYMBOL, 'not-a-real-indicator');
    expect(result).toMatchObject({ ok: false, error: 'unknown-indicator' });
    expect(store().getActiveIndicators(SYMBOL)).toHaveLength(0);
  });

  it('rejects a duplicate add (same id + identical params) and leaves the list unchanged (Req 4.4)', () => {
    store().addIndicator(SYMBOL, 'ema');
    const before = store().getActiveIndicators(SYMBOL);

    const dup = store().addIndicator(SYMBOL, 'ema');
    expect(dup).toMatchObject({ ok: false, error: 'duplicate' });
    expect(store().getActiveIndicators(SYMBOL)).toEqual(before);
  });

  it('caps the active list at 50 entries per symbol (Req 4.5)', () => {
    // Fill the list to capacity with distinct instances by mutating params so
    // the duplicate guard does not reject them.
    const seeded = Array.from({ length: MAX_INDICATORS_PER_SYMBOL }, (_, i) => ({
      instanceId: `seed-${i}`,
      indicatorId: 'sma' as const,
      params: { period: i + 1 },
      style: { color: '#fff', lineWidth: 1, lineStyle: 'solid' as const },
      visible: true,
      paneId: null,
    }));
    useChartUIStore.setState({ activeIndicators: { [SYMBOL]: seeded } });

    const result = store().addIndicator(SYMBOL, 'rsi');
    expect(result).toMatchObject({ ok: false, error: 'at-capacity' });
    expect(store().getActiveIndicators(SYMBOL)).toHaveLength(MAX_INDICATORS_PER_SYMBOL);
  });

  it('removes an indicator instance by id (Req 4.8)', () => {
    const added = store().addIndicator(SYMBOL, 'wma');
    expect(added.ok).toBe(true);
    const instanceId = added.ok ? added.instanceId : '';

    store().removeIndicator(SYMBOL, instanceId);
    expect(store().getActiveIndicators(SYMBOL)).toHaveLength(0);
  });

  it('updates params when valid and rejects invalid params retaining the old ones', () => {
    const added = store().addIndicator(SYMBOL, 'sma');
    const instanceId = added.ok ? added.instanceId : '';
    const original = store().getActiveIndicators(SYMBOL)[0].params;

    const ok = store().setIndicatorParams(SYMBOL, instanceId, { period: 21 });
    expect(ok).toEqual({ ok: true });
    expect(store().getActiveIndicators(SYMBOL)[0].params.period).toBe(21);

    // Out-of-range period (period spec is 1..5000 integer) is rejected.
    const bad = store().setIndicatorParams(SYMBOL, instanceId, { period: -5 });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.errorParam).toBe('period');
    // Previous (valid) params retained.
    expect(store().getActiveIndicators(SYMBOL)[0].params.period).toBe(21);
    // The original add value was overwritten by the valid update, not reverted.
    expect(original.period).not.toBe(21);
  });

  it('merges a partial style update', () => {
    const added = store().addIndicator(SYMBOL, 'sma');
    const instanceId = added.ok ? added.instanceId : '';

    store().setIndicatorStyle(SYMBOL, instanceId, { color: '#FF0000' });
    const styled = store().getActiveIndicators(SYMBOL)[0].style;
    expect(styled.color).toBe('#FF0000');
    // Untouched fields preserved.
    expect(styled.lineWidth).toBe(1);
    expect(styled.lineStyle).toBe('solid');
  });

  it('toggles visibility without discarding configuration (Req 4.7)', () => {
    const added = store().addIndicator(SYMBOL, 'bollinger');
    const instanceId = added.ok ? added.instanceId : '';
    store().setIndicatorParams(SYMBOL, instanceId, { period: 30, stdDev: 2.5 });
    store().setIndicatorStyle(SYMBOL, instanceId, { color: '#00FF00' });

    const before = store().getActiveIndicators(SYMBOL)[0];
    expect(before.visible).toBe(true);

    store().toggleIndicatorVisible(SYMBOL, instanceId);
    const off = store().getActiveIndicators(SYMBOL)[0];
    expect(off.visible).toBe(false);
    // Configuration preserved across the toggle.
    expect(off.params).toEqual(before.params);
    expect(off.style).toEqual(before.style);

    store().toggleIndicatorVisible(SYMBOL, instanceId);
    const on = store().getActiveIndicators(SYMBOL)[0];
    expect(on.visible).toBe(true);
    expect(on.params).toEqual(before.params);
    expect(on.style).toEqual(before.style);
  });

  it('initializes independent lists per symbol', () => {
    store().addIndicator('AAA', 'sma');
    expect(store().getActiveIndicators('AAA')).toHaveLength(1);
    expect(store().getActiveIndicators('BBB')).toEqual([]);
  });
});
