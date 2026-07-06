/**
 * ghostLineInterpolation.ts — Catmull-Rom spline interpolation for the
 * predictive ghost line overlay.
 *
 * Takes a small set of control points (typically 6-7 from the OLS/VWEPR
 * engines) and generates many more intermediate points so that the connected
 * trend_line segments appear as a smooth curve on TradingView Advanced Charts.
 */

interface GhostPoint {
  time: number;
  price: number;
}

/**
 * Catmull-Rom spline interpolation between control points.
 *
 * @param controlPoints - The original projection points (6-7 points)
 * @param segmentsPerSpan - How many sub-segments to generate between each
 *   pair of control points. Higher = smoother curve.
 * @returns Interpolated points forming a smooth curve.
 */
export function interpolateCatmullRom(
  controlPoints: GhostPoint[],
  segmentsPerSpan: number = 5,
): GhostPoint[] {
  if (controlPoints.length < 2) return [...controlPoints];
  if (controlPoints.length === 2) return [...controlPoints];

  const result: GhostPoint[] = [];
  const n = controlPoints.length;

  for (let i = 0; i < n - 1; i++) {
    // Catmull-Rom needs 4 control points: P0, P1, P2, P3
    // For boundary points, we duplicate the first/last point
    const p0 = controlPoints[Math.max(0, i - 1)];
    const p1 = controlPoints[i];
    const p2 = controlPoints[Math.min(n - 1, i + 1)];
    const p3 = controlPoints[Math.min(n - 1, i + 2)];

    // Add the start point of this span
    if (i === 0) {
      result.push({ time: p1.time, price: p1.price });
    }

    // Generate intermediate points
    for (let j = 1; j <= segmentsPerSpan; j++) {
      const t = j / segmentsPerSpan;
      const t2 = t * t;
      const t3 = t2 * t;

      // Catmull-Rom basis matrix (tension = 0.5)
      const time = 0.5 * (
        (2 * p1.time) +
        (-p0.time + p2.time) * t +
        (2 * p0.time - 5 * p1.time + 4 * p2.time - p3.time) * t2 +
        (-p0.time + 3 * p1.time - 3 * p2.time + p3.time) * t3
      );

      const price = 0.5 * (
        (2 * p1.price) +
        (-p0.price + p2.price) * t +
        (2 * p0.price - 5 * p1.price + 4 * p2.price - p3.price) * t2 +
        (-p0.price + 3 * p1.price - 3 * p2.price + p3.price) * t3
      );

      result.push({
        time: Math.round(time),
        price: +price.toFixed(2),
      });
    }
  }

  // Ensure the last point matches exactly
  const last = controlPoints[n - 1];
  result[result.length - 1] = { time: last.time, price: last.price };

  return result;
}
