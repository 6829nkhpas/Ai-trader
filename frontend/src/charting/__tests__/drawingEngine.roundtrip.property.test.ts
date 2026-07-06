// Feature: professional-charting-suite, Property 13
//
// Property-based test for Property 13: "Drawing anchors survive a coordinate
// round-trip" (Validates Requirement 5.4).
//
// For any drawing anchor expressed in `{time, price}`, converting to pixel
// coordinates under any valid visible range and back to data coordinates
// reproduces the original time and price within a 1-pixel tolerance.
//
// A viewport is valid when `timeTo !== timeFrom`, `priceMax !== priceMin`,
// `width > 0`, and `height > 0`. We generate arbitrary valid viewports and
// arbitrary anchors lying within (and around) the visible range, then assert
// that `pixelToPoint(pointToPixel(p, vp), vp)` reproduces `p`. Closeness is
// measured in pixel space — the natural unit of the 1-pixel tolerance — by
// mapping the round-tripped anchor back to pixels and bounding its distance
// from the original pixel.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { pointToPixel, pixelToPoint } from '@/charting/engines';
import type { CoordinateViewport } from '@/charting/engines';
import type { Point } from '@/store/useChartUIStore';

const RUNS = 100;

/** The round-trip must land within this many pixels of the original anchor. */
const PIXEL_TOLERANCE = 1;

/** A finite real value within a magnitude that keeps float error well below 1 px. */
const finite = (min: number, max: number) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a valid {@link CoordinateViewport}: the two time edges differ, the
 * two price edges differ, and both pixel dimensions are strictly positive.
 */
const viewport = (): fc.Arbitrary<CoordinateViewport> =>
  fc
    .record({
      timeFrom: finite(0, 5_000_000),
      timeSpan: finite(1, 5_000_000),
      timeDir: fc.boolean(),
      priceMin0: finite(0.01, 100_000),
      priceSpan: finite(0.01, 100_000),
      priceDir: fc.boolean(),
      width: finite(1, 4000),
      height: finite(1, 4000),
    })
    .map(({ timeFrom, timeSpan, timeDir, priceMin0, priceSpan, priceDir, width, height }) => {
      // timeDir / priceDir let the edges run in either direction so timeTo can
      // be greater or less than timeFrom (likewise for price), exercising both
      // orientations while guaranteeing the edges never coincide.
      const timeTo = timeDir ? timeFrom + timeSpan : timeFrom - timeSpan;
      const priceMax = priceDir ? priceMin0 + priceSpan : priceMin0;
      const priceMin = priceDir ? priceMin0 : priceMin0 + priceSpan;
      return { timeFrom, timeTo, priceMin, priceMax, width, height };
    });

/**
 * Generate an anchor lying within the viewport's visible range (interpolated by
 * an arbitrary fraction along each axis), plus a little overshoot so the
 * property also covers anchors just outside the visible window.
 */
const pointWithin = (vp: CoordinateViewport): fc.Arbitrary<Point> =>
  fc
    .record({ ft: finite(-0.25, 1.25), fp: finite(-0.25, 1.25) })
    .map(({ ft, fp }) => ({
      time: vp.timeFrom + ft * (vp.timeTo - vp.timeFrom),
      price: vp.priceMin + fp * (vp.priceMax - vp.priceMin),
    }));

describe('Property 13: Drawing anchors survive a coordinate round-trip', () => {
  it('pixelToPoint(pointToPixel(p, vp), vp) reproduces p within 1 pixel', () => {
    fc.assert(
      fc.property(
        viewport().chain((vp) => pointWithin(vp).map((point) => ({ vp, point }))),
        ({ vp, point }) => {
          const original = pointToPixel(point, vp);
          const back = pixelToPoint(original, vp);
          const reprojected = pointToPixel(back, vp);

          // The reproduced anchor, mapped back to pixels, must sit within the
          // 1-pixel tolerance of the original pixel on both axes.
          expect(Math.abs(reprojected.x - original.x)).toBeLessThanOrEqual(PIXEL_TOLERANCE);
          expect(Math.abs(reprojected.y - original.y)).toBeLessThanOrEqual(PIXEL_TOLERANCE);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
