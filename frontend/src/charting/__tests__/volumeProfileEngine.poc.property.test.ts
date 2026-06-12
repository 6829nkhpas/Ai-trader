// Feature: professional-charting-suite, Property 24
//
// Property-based test for Property 24: "Volume profile POC and value area are
// correct" (Validates Requirements 7.3, 7.4, 7.7, 7.8, 7.9).
//
// For any profile with positive total volume:
//   - the POC is the single greatest-volume row (its reported price is the
//     center of that row),
//   - the Value_Area is a CONTIGUOUS set of rows AROUND the POC,
//   - the in-value-area flag is set exactly for those rows,
//   - the cumulative volume of the value-area rows reaches at least the
//     configured percentage of total volume (or spans every row when the
//     percentage is 100 / cannot otherwise be met),
//   - VAL <= POC <= VAH where VAH/VAL are the upper/lower price edges of the
//     value-area set.
// When total volume is zero, POC, VAH, and VAL are all null.
//
// The assertions use INDEPENDENT oracles: the max-volume row is recomputed with
// a fresh reduction, the value-area rows are re-derived from the inValueArea
// flags, and the cumulative-percentage check sums those rows directly.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildProfile, valueArea } from '@/charting/engines';
import type { VolumeProfile } from '@/charting/engines';
import type { ChartCandle, VolumeBar } from '@/charting/types';

const RUNS = 100;

/** Finite, positive price value generator (bounded to avoid heap blowups). */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** Finite, non-negative volume value generator. */
const volumeVal = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a matched candle series + per-candle volume bars. Candles have
 * strictly ascending unique timestamps; high/low bracket open/close so each
 * candle is valid; each volume bar shares its candle's timestamp so the engine
 * can look it up by time.
 */
const candlesAndVolumes = (): fc.Arbitrary<{
  candles: ChartCandle[];
  volumes: VolumeBar[];
}> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 30 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(
        ...sorted.map((t) =>
          fc
            .record({ a: price(), b: price(), c: price(), d: price(), v: volumeVal() })
            .map(({ a, b, c, d, v }) => ({
              candle: {
                time: t,
                open: a,
                close: b,
                high: Math.max(a, b, c, d),
                low: Math.min(a, b, c, d),
              } as ChartCandle,
              volume: { time: t, value: v, color: '#000' } as VolumeBar,
            })),
        ),
      );
    })
    .map((arr) => ({
      candles: arr.map((x) => x.candle),
      volumes: arr.map((x) => x.volume),
    }));

/** Row count generator covering the accepted 1..1000 range (kept small-ish). */
const rowCount = () => fc.integer({ min: 1, max: 60 });

/** Value-area percentage generator covering the accepted 1..100 range. */
const valuePercentArb = () =>
  fc.double({ min: 1, max: 100, noNaN: true, noDefaultInfinity: true });

/** Indices of the rows flagged as part of the value area, in ascending order. */
function valueAreaIndices(profile: VolumeProfile): number[] {
  const idx: number[] = [];
  profile.rows.forEach((r, i) => {
    if (r.inValueArea) idx.push(i);
  });
  return idx;
}

describe('Property 24: Volume profile POC and value area are correct', () => {
  it('POC is the max-volume row and the value area is a contiguous block reaching the target percentage', () => {
    fc.assert(
      fc.property(
        candlesAndVolumes(),
        rowCount(),
        valuePercentArb(),
        ({ candles, volumes }, rows, valuePercent) => {
          const profile = buildProfile(candles, volumes, { rows, valuePercent });

          // Row-count invariant always holds (clamped to 1..1000).
          expect(profile.rows.length).toBe(Math.min(Math.max(Math.round(rows), 1), 1000));

          if (profile.totalVolume <= 0) {
            // Requirement 7.9: zero total volume → null markers, no flagged rows.
            expect(profile.poc).toBeNull();
            expect(profile.vah).toBeNull();
            expect(profile.val).toBeNull();
            expect(profile.rows.every((r) => !r.inValueArea)).toBe(true);
            return;
          }

          // ── POC is the single greatest-volume row (Req 7.3) ───────────────
          expect(profile.poc).not.toBeNull();
          const maxVol = profile.rows.reduce((m, r) => Math.max(m, r.volume), -Infinity);
          const pocIndex = profile.rows.findIndex(
            (r) => (r.priceLow + r.priceHigh) / 2 === profile.poc,
          );
          expect(pocIndex).toBeGreaterThanOrEqual(0);
          expect(profile.rows[pocIndex].volume).toBe(maxVol);

          // ── Value area is contiguous and contains the POC (Req 7.7, 7.8) ──
          const vaIdx = valueAreaIndices(profile);
          expect(vaIdx.length).toBeGreaterThan(0);
          const lo = vaIdx[0];
          const hi = vaIdx[vaIdx.length - 1];
          // Contiguous: every index between lo..hi is present.
          expect(vaIdx.length).toBe(hi - lo + 1);
          // Contains the POC.
          expect(lo).toBeLessThanOrEqual(pocIndex);
          expect(hi).toBeGreaterThanOrEqual(pocIndex);

          // ── VAH/VAL are the price edges of the set, VAL <= POC <= VAH ──────
          expect(profile.val).toBe(profile.rows[lo].priceLow);
          expect(profile.vah).toBe(profile.rows[hi].priceHigh);
          expect(profile.val!).toBeLessThanOrEqual(profile.poc!);
          expect(profile.poc!).toBeLessThanOrEqual(profile.vah!);

          // ── Cumulative value-area volume reaches the target % (Req 7.4) ───
          const clampedPct = Math.min(Math.max(valuePercent, 1), 100);
          const target = profile.totalVolume * (clampedPct / 100);
          const cumVA = vaIdx.reduce((s, i) => s + profile.rows[i].volume, 0);
          const spansAllRows = lo === 0 && hi === profile.rows.length - 1;
          const tol = Math.abs(target) * 1e-9 + 1e-9;
          // Either the accumulated volume meets the target, or the value area
          // already spans every row (target unreachable with fewer rows).
          expect(cumVA >= target - tol || spansAllRows).toBe(true);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('zero total volume yields null POC/VAH/VAL markers', () => {
    fc.assert(
      fc.property(candlesAndVolumes(), rowCount(), ({ candles }, rows) => {
        // Force every matched volume to zero → total volume must be zero.
        const zeroVolumes: VolumeBar[] = candles.map((c) => ({
          time: c.time,
          value: 0,
          color: '#000',
        }));
        const profile = buildProfile(candles, zeroVolumes, { rows });

        expect(profile.totalVolume).toBe(0);
        expect(profile.poc).toBeNull();
        expect(profile.vah).toBeNull();
        expect(profile.val).toBeNull();
        expect(profile.rows.every((r) => !r.inValueArea)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('valueArea helper returns a contiguous block around the POC reaching the target percentage', () => {
    fc.assert(
      fc.property(
        fc.array(volumeVal(), { minLength: 1, maxLength: 60 }),
        fc.integer({ min: 0, max: 59 }),
        valuePercentArb(),
        (rowVolumes, rawPoc, valuePercent) => {
          const n = rowVolumes.length;
          const pocIndex = Math.min(rawPoc, n - 1);
          const { loIndex, hiIndex } = valueArea(rowVolumes, pocIndex, valuePercent);

          // Bounds are valid and the POC is enclosed.
          expect(loIndex).toBeGreaterThanOrEqual(0);
          expect(hiIndex).toBeLessThanOrEqual(n - 1);
          expect(loIndex).toBeLessThanOrEqual(pocIndex);
          expect(hiIndex).toBeGreaterThanOrEqual(pocIndex);

          const total = rowVolumes.reduce((s, v) => s + v, 0);
          if (total <= 0) {
            // Zero total volume collapses the area onto the POC row.
            expect(loIndex).toBe(pocIndex);
            expect(hiIndex).toBe(pocIndex);
            return;
          }

          const clampedPct = Math.min(Math.max(valuePercent, 1), 100);
          const target = total * (clampedPct / 100);
          let cum = 0;
          for (let i = loIndex; i <= hiIndex; i += 1) cum += rowVolumes[i];
          const spansAllRows = loIndex === 0 && hiIndex === n - 1;
          const tol = Math.abs(target) * 1e-9 + 1e-9;
          expect(cum >= target - tol || spansAllRows).toBe(true);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
