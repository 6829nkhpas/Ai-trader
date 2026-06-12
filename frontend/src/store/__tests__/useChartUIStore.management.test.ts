// Feature: professional-charting-suite
//
// Unit tests for the Indicator Manager *management paths* (task 5.6). These
// focus on the rejection/feedback and mutation behaviours that the trader
// drives from the Indicator_Manager panel:
//   - duplicate-add rejection surfaces a {ok:false, error:'duplicate'} result
//     with a human-readable message and leaves the list unchanged (Req 4.4)
//   - at-capacity rejection (list already holds 50 entries) surfaces
//     {ok:false, error:'at-capacity'} with a message and leaves the list
//     unchanged (Req 4.5)
//   - the rejection messages are non-empty so the UI can surface them (Req 4.6)
//   - setIndicatorStyle merges a partial style update (restyle → redraw) without
//     clobbering untouched fields (Req 4.6)
//   - removeIndicator drops the targeted instance (Req 4.8)
//
// These complement the broader slice tests in
// `useChartUIStore.indicators.test.ts` (task 5.1) — this file intentionally
// drills into the manager-facing add/restyle/remove flows.

import { describe, it, expect, beforeEach } from 'vitest';

import {
  useChartUIStore,
  MAX_INDICATORS_PER_SYMBOL,
  DEFAULT_INDICATOR_STYLE,
  type ActiveIndicator,
} from '@/store/useChartUIStore';

const SYMBOL = 'TSLA';

function store() {
  return useChartUIStore.getState();
}

/** Build `count` distinct active SMA instances (varying params dodges the
 *  duplicate guard) so we can seed the list to a chosen size. */
function seedIndicators(count: number): ActiveIndicator[] {
  return Array.from({ length: count }, (_, i) => ({
    instanceId: `seed-${i}`,
    indicatorId: 'sma' as const,
    params: { period: i + 1 },
    style: { ...DEFAULT_INDICATOR_STYLE },
    visible: true,
    paneId: null,
  }));
}

beforeEach(() => {
  // Reset the per-symbol active-indicator map between tests.
  useChartUIStore.setState({ activeIndicators: {} });
});

describe('indicator management paths', () => {
  describe('duplicate-add rejection (Req 4.4)', () => {
    it('rejects a duplicate add with error "duplicate" and a message', () => {
      const first = store().addIndicator(SYMBOL, 'ema');
      expect(first.ok).toBe(true);

      const dup = store().addIndicator(SYMBOL, 'ema');
      expect(dup.ok).toBe(false);
      if (!dup.ok) {
        expect(dup.error).toBe('duplicate');
        // The rejection is surfaced via a non-empty message (Req 4.6).
        expect(typeof dup.message).toBe('string');
        expect(dup.message.length).toBeGreaterThan(0);
      }
    });

    it('leaves the existing active-indicator list unchanged on a duplicate add', () => {
      store().addIndicator(SYMBOL, 'ema');
      const before = store().getActiveIndicators(SYMBOL);

      store().addIndicator(SYMBOL, 'ema');
      const after = store().getActiveIndicators(SYMBOL);

      expect(after).toEqual(before);
      expect(after).toHaveLength(1);
    });
  });

  describe('at-capacity rejection (Req 4.5)', () => {
    it('rejects an add at 50 entries with error "at-capacity" and a message', () => {
      useChartUIStore.setState({
        activeIndicators: { [SYMBOL]: seedIndicators(MAX_INDICATORS_PER_SYMBOL) },
      });

      const result = store().addIndicator(SYMBOL, 'rsi');
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toBe('at-capacity');
        // The limit message is surfaced for the UI (Req 4.6).
        expect(typeof result.message).toBe('string');
        expect(result.message.length).toBeGreaterThan(0);
      }
    });

    it('leaves the list unchanged (still 50) when at capacity', () => {
      useChartUIStore.setState({
        activeIndicators: { [SYMBOL]: seedIndicators(MAX_INDICATORS_PER_SYMBOL) },
      });
      const before = store().getActiveIndicators(SYMBOL);

      store().addIndicator(SYMBOL, 'rsi');
      const after = store().getActiveIndicators(SYMBOL);

      expect(after).toHaveLength(MAX_INDICATORS_PER_SYMBOL);
      expect(after).toEqual(before);
    });

    it('accepts an add at one below capacity (boundary)', () => {
      useChartUIStore.setState({
        activeIndicators: { [SYMBOL]: seedIndicators(MAX_INDICATORS_PER_SYMBOL - 1) },
      });

      const result = store().addIndicator(SYMBOL, 'rsi');
      expect(result.ok).toBe(true);
      expect(store().getActiveIndicators(SYMBOL)).toHaveLength(MAX_INDICATORS_PER_SYMBOL);
    });
  });

  describe('restyle / redraw via setIndicatorStyle (Req 4.6)', () => {
    it('merges a partial style update, preserving untouched fields', () => {
      const added = store().addIndicator(SYMBOL, 'sma');
      const instanceId = added.ok ? added.instanceId : '';

      store().setIndicatorStyle(SYMBOL, instanceId, { color: '#FF0000' });

      const styled = store().getActiveIndicators(SYMBOL)[0].style;
      expect(styled.color).toBe('#FF0000');
      // Fields not included in the partial update are retained from defaults.
      expect(styled.lineWidth).toBe(DEFAULT_INDICATOR_STYLE.lineWidth);
      expect(styled.lineStyle).toBe(DEFAULT_INDICATOR_STYLE.lineStyle);
    });

    it('applies successive partial updates cumulatively', () => {
      const added = store().addIndicator(SYMBOL, 'sma');
      const instanceId = added.ok ? added.instanceId : '';

      store().setIndicatorStyle(SYMBOL, instanceId, { color: '#00FF00' });
      store().setIndicatorStyle(SYMBOL, instanceId, { lineWidth: 4 });

      const styled = store().getActiveIndicators(SYMBOL)[0].style;
      expect(styled.color).toBe('#00FF00');
      expect(styled.lineWidth).toBe(4);
      expect(styled.lineStyle).toBe(DEFAULT_INDICATOR_STYLE.lineStyle);
    });

    it('only restyles the targeted instance, leaving siblings untouched', () => {
      const a = store().addIndicator(SYMBOL, 'sma');
      const b = store().addIndicator(SYMBOL, 'ema');
      const aId = a.ok ? a.instanceId : '';
      const bId = b.ok ? b.instanceId : '';

      store().setIndicatorStyle(SYMBOL, aId, { color: '#123456' });

      const list = store().getActiveIndicators(SYMBOL);
      const styledA = list.find((i) => i.instanceId === aId)!;
      const styledB = list.find((i) => i.instanceId === bId)!;
      expect(styledA.style.color).toBe('#123456');
      expect(styledB.style.color).toBe(DEFAULT_INDICATOR_STYLE.color);
    });
  });

  describe('removeIndicator (Req 4.8)', () => {
    it('drops the targeted instance', () => {
      const added = store().addIndicator(SYMBOL, 'wma');
      const instanceId = added.ok ? added.instanceId : '';
      expect(store().getActiveIndicators(SYMBOL)).toHaveLength(1);

      store().removeIndicator(SYMBOL, instanceId);
      expect(store().getActiveIndicators(SYMBOL)).toHaveLength(0);
    });

    it('removes only the targeted instance, keeping the rest', () => {
      const a = store().addIndicator(SYMBOL, 'sma');
      store().addIndicator(SYMBOL, 'ema');
      const aId = a.ok ? a.instanceId : '';

      store().removeIndicator(SYMBOL, aId);

      const list = store().getActiveIndicators(SYMBOL);
      expect(list).toHaveLength(1);
      expect(list.some((i) => i.instanceId === aId)).toBe(false);
      expect(list[0].indicatorId).toBe('ema');
    });

    it('is a no-op for an unknown instance id', () => {
      store().addIndicator(SYMBOL, 'sma');
      const before = store().getActiveIndicators(SYMBOL);

      store().removeIndicator(SYMBOL, 'does-not-exist');
      expect(store().getActiveIndicators(SYMBOL)).toEqual(before);
    });
  });
});
