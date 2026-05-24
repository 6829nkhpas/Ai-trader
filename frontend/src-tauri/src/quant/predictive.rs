// quant/predictive.rs — Dual Predictive Math Engine
//
// Strat Ai — Phase 1: Dual-Engine Predictive System
//
// Bundles two complementary regression models:
//
//   1. **Linear OLS** — Standard Ordinary Least Squares regression over
//      array indices (not timestamps, to avoid float overflow). Provides
//      a clean baseline trend direction. Anchored to the last close price.
//
//   2. **Curved VWEPR** — Volume-Weighted Exponential Polynomial Regression.
//      Delegates to the existing `vwepr` module. Captures price curvature
//      (acceleration/deceleration) that linear regression misses.
//
// Both produce `ProjectedPoint` arrays with identical anchor semantics
// (first point = last actual close price & time), making them directly
// overlayable on the same Lightweight Charts instance.
//
// The `ProjectionPayload` struct bundles both datasets plus the VWEPR
// acceleration coefficient for downstream AI analysis (e.g., DeepSeek
// conviction scoring can weight its narrative based on whether price
// is accelerating or decelerating).

use super::vwepr::{self, OhlcCandle, ProjectedPoint};

// ── Dual-Engine Output ──────────────────────────────────────────────────────

/// Combined output of both predictive engines, ready for IPC serialization.
///
/// The frontend can render both lines simultaneously:
///   - `linear_points` as a subtle baseline trend indicator
///   - `curved_points` as the primary Ghost Line (VWEPR)
///
/// The `acceleration_coefficient` is the quadratic term `a` from the VWEPR
/// polynomial `y = ax² + bx + c`:
///   - `a > 0` → price is accelerating upward (bullish momentum)
///   - `a < 0` → price is accelerating downward (bearish momentum)
///   - `a ≈ 0` → trend is approximately linear
#[derive(serde::Serialize, serde::Deserialize, Debug, Clone)]
pub struct ProjectionPayload {
    /// OLS linear regression projection points.
    pub linear_points: Vec<ProjectedPoint>,
    /// VWEPR curved polynomial projection points.
    pub curved_points: Vec<ProjectedPoint>,
    /// The quadratic coefficient `a` from the VWEPR fit.
    /// Injected into DeepSeek prompts as a momentum signal.
    pub acceleration_coefficient: f64,
}

// ── Constants ───────────────────────────────────────────────────────────────

/// Maximum lookback window for OLS regression (same as VWEPR for consistency).
const OLS_MAX_WINDOW: usize = 60;

// ── Linear OLS Regression ───────────────────────────────────────────────────

/// Calculate a linear OLS regression over recent candles and project forward.
///
/// Uses **array indices** (0, 1, 2, …) as the x-axis — NOT raw Unix
/// timestamps — to prevent float accumulator overflow that causes
/// NaN/Infinity slopes (the exact bug that plagued the old React OLS).
///
/// # Arguments
/// * `candles`           — Slice of OHLCV candles (chronological order).
/// * `projection_length` — Number of future bars to project.
/// * `interval_sec`      — Duration of one bar in seconds.
///
/// # Returns
/// `Vec<ProjectedPoint>` with `projection_length + 1` entries:
///   - Index 0 = anchor (last candle's actual close + time).
///   - Index 1..=projection_length = future projected points.
///
/// Returns an empty `Vec` when insufficient data prevents a meaningful fit.
pub fn calculate_ols(
    candles: &[OhlcCandle],
    projection_length: usize,
    interval_sec: i64,
) -> Vec<ProjectedPoint> {
    if candles.is_empty() || projection_length == 0 {
        return Vec::new();
    }

    // ── 1. Window selection ─────────────────────────────────────────────────
    let window_size = candles.len().min(OLS_MAX_WINDOW);
    let window = &candles[candles.len() - window_size..];
    let n = window_size as f64;

    // ── 2. Accumulate OLS sums (x = array index) ────────────────────────────
    let mut sum_x: f64 = 0.0;
    let mut sum_y: f64 = 0.0;
    let mut sum_xy: f64 = 0.0;
    let mut sum_xx: f64 = 0.0;

    for (i, candle) in window.iter().enumerate() {
        let x = i as f64;
        let y = candle.close;
        sum_x += x;
        sum_y += y;
        sum_xy += x * y;
        sum_xx += x * x;
    }

    // ── 3. Solve for slope and intercept ─────────────────────────────────────
    let denom = n * sum_xx - sum_x * sum_x;

    if denom.abs() < 1e-30 {
        // Degenerate case (e.g., single candle or all same x).
        // Return only the anchor point.
        let last = &window[window.len() - 1];
        return vec![ProjectedPoint {
            time: last.time,
            value: last.close,
        }];
    }

    let slope = (n * sum_xy - sum_x * sum_y) / denom;

    // Guard: slope must be finite.
    if !slope.is_finite() {
        let last = &window[window.len() - 1];
        return vec![ProjectedPoint {
            time: last.time,
            value: last.close,
        }];
    }

    // ── 4. Anchored Projection ──────────────────────────────────────────────
    //
    // Like VWEPR, we anchor to the last actual close price (NOT the
    // regression's fitted value at the last index). This prevents the
    // visible vertical detachment that occurs when the regression line
    // doesn't pass exactly through the last data point.
    let last_candle = &window[window.len() - 1];
    let last_close = last_candle.close;
    let last_time = last_candle.time;

    let mut result = Vec::with_capacity(projection_length + 1);

    // Anchor point: exact last close
    result.push(ProjectedPoint {
        time: last_time,
        value: last_close,
    });

    // Project forward: each step adds slope * k to the anchor price
    for k in 1..=projection_length {
        let future_time = last_time + (k as i64 * interval_sec);
        let projected_value = last_close + slope * k as f64;

        result.push(ProjectedPoint {
            time: future_time,
            value: projected_value,
        });
    }

    result
}

// ── Dual-Engine Unified API ─────────────────────────────────────────────────

/// Run both predictive engines and return the bundled `ProjectionPayload`.
///
/// This is the primary entry point for the IPC command layer. It runs
/// OLS and VWEPR in sequence (both are pure CPU, sub-microsecond),
/// bundles the results, and returns them in a single IPC response.
///
/// # Arguments
/// * `candles`           — Slice of OHLCV candles (chronological order).
/// * `projection_length` — Number of future bars to project.
/// * `interval_sec`      — Duration of one bar in seconds.
pub fn calculate_dual_projection(
    candles: &[OhlcCandle],
    projection_length: usize,
    interval_sec: i64,
) -> ProjectionPayload {
    let linear_points = calculate_ols(candles, projection_length, interval_sec);
    let (curved_points, acceleration_coefficient) =
        vwepr::calculate_vwepr_with_accel(candles, projection_length, interval_sec);

    ProjectionPayload {
        linear_points,
        curved_points,
        acceleration_coefficient,
    }
}

// ── Unit Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper to create a candle with minimal fields.
    fn candle(time: i64, close: f64, volume: f64) -> OhlcCandle {
        OhlcCandle {
            time,
            open: close,
            high: close,
            low: close,
            close,
            volume,
        }
    }

    // ── OLS Tests ───────────────────────────────────────────────────────────

    #[test]
    fn ols_empty_returns_empty() {
        let result = calculate_ols(&[], 5, 60);
        assert!(result.is_empty());
    }

    #[test]
    fn ols_zero_projection_returns_empty() {
        let candles = vec![candle(1000, 100.0, 500.0)];
        let result = calculate_ols(&candles, 0, 60);
        assert!(result.is_empty());
    }

    #[test]
    fn ols_anchor_matches_last_close() {
        let candles: Vec<OhlcCandle> = (0..25)
            .map(|i| candle(1000 + i * 60, 100.0 + i as f64 * 0.5, 1000.0))
            .collect();

        let result = calculate_ols(&candles, 5, 60);
        assert!(!result.is_empty());
        assert_eq!(result[0].time, candles.last().unwrap().time);
        assert!(
            (result[0].value - candles.last().unwrap().close).abs() < 1e-10,
            "OLS anchor must equal last close"
        );
    }

    #[test]
    fn ols_linear_data_extrapolates_perfectly() {
        // Perfectly linear data: close = 100 + 2*i
        let candles: Vec<OhlcCandle> = (0..30)
            .map(|i| candle(1000 + i * 60, 100.0 + 2.0 * i as f64, 500.0))
            .collect();

        let result = calculate_ols(&candles, 5, 60);
        assert_eq!(result.len(), 6); // 1 anchor + 5 projected

        let last_close = candles.last().unwrap().close; // 100 + 2*29 = 158.0
        for k in 1..=5 {
            let expected = last_close + 2.0 * k as f64;
            assert!(
                (result[k].value - expected).abs() < 1e-8,
                "OLS on linear data should extrapolate perfectly: step {}, got {}, expected {}",
                k, result[k].value, expected
            );
        }
    }

    #[test]
    fn ols_flat_projects_flat() {
        let candles: Vec<OhlcCandle> = (0..20)
            .map(|i| candle(1000 + i * 60, 50.0, 1000.0))
            .collect();

        let result = calculate_ols(&candles, 5, 60);
        for point in &result {
            assert!(
                (point.value - 50.0).abs() < 1e-8,
                "Flat data should project flat: got {}",
                point.value
            );
        }
    }

    #[test]
    fn ols_timestamps_are_sequential() {
        let candles: Vec<OhlcCandle> = (0..20)
            .map(|i| candle(5000 + i * 300, 200.0 + i as f64, 500.0))
            .collect();

        let interval = 300_i64;
        let proj_len = 8;
        let result = calculate_ols(&candles, proj_len, interval);

        assert_eq!(result.len(), proj_len + 1);
        let last_time = candles.last().unwrap().time;
        for (idx, point) in result.iter().enumerate() {
            let expected = last_time + (idx as i64 * interval);
            assert_eq!(point.time, expected, "OLS point {} wrong timestamp", idx);
        }
    }

    #[test]
    fn ols_single_candle_returns_anchor() {
        let candles = vec![candle(3000, 75.0, 100.0)];
        let result = calculate_ols(&candles, 5, 60);
        assert!(!result.is_empty());
        assert_eq!(result[0].time, 3000);
        assert!((result[0].value - 75.0).abs() < 1e-10);
    }

    // ── Dual-Engine Tests ───────────────────────────────────────────────────

    #[test]
    fn dual_produces_both_outputs() {
        let candles: Vec<OhlcCandle> = (0..30)
            .map(|i| candle(1000 + i * 60, 100.0 + i as f64 * 0.3, 500.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 6, 60);

        // Both engines should produce output
        assert!(!payload.linear_points.is_empty());
        assert!(!payload.curved_points.is_empty());

        // Both should have 7 points (1 anchor + 6 projected)
        assert_eq!(payload.linear_points.len(), 7);
        assert_eq!(payload.curved_points.len(), 7);

        // Acceleration coefficient should be finite
        assert!(
            payload.acceleration_coefficient.is_finite(),
            "Acceleration coefficient must be finite"
        );
    }

    #[test]
    fn dual_anchors_match() {
        let candles: Vec<OhlcCandle> = (0..25)
            .map(|i| candle(1000 + i * 60, 150.0 + i as f64, 800.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 5, 60);
        let last_close = candles.last().unwrap().close;
        let last_time = candles.last().unwrap().time;

        // Both engines must anchor to the same point
        assert_eq!(payload.linear_points[0].time, last_time);
        assert_eq!(payload.curved_points[0].time, last_time);
        assert!(
            (payload.linear_points[0].value - last_close).abs() < 1e-10,
            "OLS anchor mismatch"
        );
        assert!(
            (payload.curved_points[0].value - last_close).abs() < 1e-10,
            "VWEPR anchor mismatch"
        );
    }

    #[test]
    fn dual_linear_data_produces_near_zero_acceleration() {
        // Perfectly linear data: VWEPR's quadratic term `a` should be ≈ 0.
        let candles: Vec<OhlcCandle> = (0..30)
            .map(|i| candle(1000 + i * 60, 100.0 + i as f64, 500.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 5, 60);

        assert!(
            payload.acceleration_coefficient.abs() < 1e-6,
            "Linear data should produce near-zero acceleration: got {}",
            payload.acceleration_coefficient
        );
    }

    #[test]
    fn dual_quadratic_data_produces_positive_acceleration() {
        // Upward-curving data: close = 100 + 0.1*i²
        let candles: Vec<OhlcCandle> = (0..30)
            .map(|i| candle(1000 + i * 60, 100.0 + 0.1 * (i as f64).powi(2), 500.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 5, 60);

        assert!(
            payload.acceleration_coefficient > 0.0,
            "Upward-curving data should produce positive acceleration: got {}",
            payload.acceleration_coefficient
        );
    }

    #[test]
    fn dual_curves_diverge_on_nonlinear_data() {
        // Quadratic data: the linear OLS and curved VWEPR should diverge.
        let candles: Vec<OhlcCandle> = (0..30)
            .map(|i| candle(1000 + i * 60, 100.0 + 0.05 * (i as f64).powi(2), 500.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 10, 60);

        // At projection step 10, linear extrapolation should differ
        // from the quadratic curve.
        let linear_terminal = payload.linear_points.last().unwrap().value;
        let curved_terminal = payload.curved_points.last().unwrap().value;
        let divergence = (curved_terminal - linear_terminal).abs();

        assert!(
            divergence > 0.01,
            "OLS and VWEPR should diverge on quadratic data (divergence={})",
            divergence
        );
    }

    #[test]
    fn dual_empty_input() {
        let payload = calculate_dual_projection(&[], 5, 60);
        assert!(payload.linear_points.is_empty());
        assert!(payload.curved_points.is_empty());
        assert!((payload.acceleration_coefficient - 0.0).abs() < 1e-15);
    }

    #[test]
    fn payload_serializes_to_json() {
        let candles: Vec<OhlcCandle> = (0..20)
            .map(|i| candle(1000 + i * 60, 100.0 + i as f64, 500.0))
            .collect();

        let payload = calculate_dual_projection(&candles, 3, 60);
        let json = serde_json::to_string(&payload);
        assert!(json.is_ok(), "ProjectionPayload must serialize to JSON");

        let json_str = json.unwrap();
        assert!(json_str.contains("linear_points"));
        assert!(json_str.contains("curved_points"));
        assert!(json_str.contains("acceleration_coefficient"));
    }
}
