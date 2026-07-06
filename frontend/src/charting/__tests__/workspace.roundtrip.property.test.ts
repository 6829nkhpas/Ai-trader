// Feature: professional-charting-suite, Property 33
//
// Property-based test for Property 33: "Workspace serialization round-trips"
// (Validates Requirements 1.3, 4.9, 4.10, 5.11, 11.1, 11.2).
//
// For any valid workspace state — a chart type drawn from CHART_TYPES with
// finite-number chart-type params, a list of active indicators (each with
// params, style, visibility and pane assignment), a list of drawings (including
// locked ones), and an oscillator pane layout — deserializing its serialized
// form reproduces an equivalent workspace state:
//
//     deserializeWorkspace(serializeWorkspace(state)) deep-equals state
//
// The arbitrary only generates JSON-serializable values (finite numbers, no
// `undefined`) so the serialize→deserialize pair is exercised on the input
// space the pure functions promise to round-trip.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  serializeWorkspace,
  deserializeWorkspace,
  type WorkspaceState,
} from '@/charting/workspace';
import { CHART_TYPES, type ChartType } from '@/charting/engines';
import type { PaneLayout } from '@/charting/paneManager';
import type { ActiveIndicator, Drawing, Point } from '@/store/useChartUIStore';

const RUNS = 100;

/**
 * A finite real value. `-0` is normalized to `0` because `JSON.stringify(-0)`
 * emits `"0"`, so a generated `-0` would not survive the round-trip verbatim.
 */
const finite = (min: number, max: number): fc.Arbitrary<number> =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true }).map((n) => (Object.is(n, -0) ? 0 : n));

/** A chart type drawn from the canonical list (Requirement 1.3). */
const chartType: fc.Arbitrary<ChartType> = fc.constantFrom(...CHART_TYPES);

/**
 * Chart-type params: a subset of the known numeric keys, each a finite number.
 * `deserializeWorkspace` keeps only finite-number entries, and these all
 * qualify, so the bag round-trips unchanged (Requirement 1.3).
 */
const chartTypeParams = (): fc.Arbitrary<Record<string, number>> =>
  fc.record(
    {
      renkoBoxSize: finite(1, 999_999),
      pfBoxSize: finite(1, 999_999),
      pfReversal: finite(1, 999_999),
      kagiReversal: finite(1, 999_999),
      lineBreakCount: finite(1, 999_999),
    },
    { requiredKeys: [] },
  );

/** A finite numeric parameter bag for an indicator instance. */
const indicatorParams = (): fc.Arbitrary<Record<string, number>> =>
  fc.dictionary(fc.string({ minLength: 1, maxLength: 6 }), finite(-1e6, 1e6), { maxKeys: 4 });

/** A visual line style (Requirements 4.9, 4.10). */
const lineStyle = (): fc.Arbitrary<ActiveIndicator['style']> =>
  fc.record({
    color: fc.string({ minLength: 1, maxLength: 8 }),
    lineWidth: finite(0.5, 8),
    lineStyle: fc.constantFrom('solid', 'dashed', 'dotted'),
  });

/** One active indicator instance (Requirements 4.9, 4.10). */
const activeIndicator = (): fc.Arbitrary<ActiveIndicator> =>
  fc.record({
    instanceId: fc.string({ minLength: 1, maxLength: 12 }),
    indicatorId: fc.string({ minLength: 1, maxLength: 12 }) as fc.Arbitrary<ActiveIndicator['indicatorId']>,
    params: indicatorParams(),
    style: lineStyle(),
    visible: fc.boolean(),
    paneId: fc.option(fc.string({ minLength: 1, maxLength: 10 }), { nil: null }),
  });

/** A drawing anchor point. */
const point = (): fc.Arbitrary<Point> =>
  fc.record({ time: finite(0, 5_000_000), price: finite(-1e6, 1e6) });

/**
 * One drawing, including its optional fields (locked drawings survive — Req
 * 5.11). Optional keys are sometimes present with concrete, JSON-safe values
 * so the round-trip is exercised both with and without them.
 */
const drawing = (): fc.Arbitrary<Drawing> =>
  fc.record(
    {
      id: fc.string({ minLength: 1, maxLength: 12 }),
      tool: fc.string({ minLength: 1, maxLength: 12 }),
      points: fc.array(point(), { maxLength: 5 }),
      color: fc.string({ minLength: 1, maxLength: 8 }),
      text: fc.string({ maxLength: 16 }),
      locked: fc.boolean(),
      symbol: fc.string({ minLength: 1, maxLength: 8 }),
    },
    { requiredKeys: ['id', 'tool', 'points'] },
  );

/** One oscillator pane layout entry. */
const paneLayout = (): fc.Arbitrary<PaneLayout> =>
  fc.record({
    paneId: fc.string({ minLength: 1, maxLength: 12 }),
    heightFraction: finite(0, 1),
    order: fc.nat({ max: 20 }),
  });

/** A complete, valid workspace state (version literal 1). */
const workspaceState = (): fc.Arbitrary<WorkspaceState> =>
  fc.record({
    version: fc.constant(1 as const),
    chartType,
    chartTypeParams: chartTypeParams(),
    activeIndicators: fc.array(activeIndicator(), { maxLength: 6 }),
    drawings: fc.array(drawing(), { maxLength: 6 }),
    paneLayout: fc.array(paneLayout(), { maxLength: 6 }),
  });

describe('Property 33: Workspace serialization round-trips', () => {
  it('deserializeWorkspace(serializeWorkspace(state)) reproduces state', () => {
    fc.assert(
      fc.property(workspaceState(), (state) => {
        const restored = deserializeWorkspace(serializeWorkspace(state));
        expect(restored).toEqual(state);
      }),
      { numRuns: RUNS },
    );
  });
});
