// src/commands/quant.rs — Dual-Engine Ghost Curve IPC Bridge
//
// Strat Ai — Phase 2: Stateless Tauri command that accepts a lightweight
// candle payload from React and returns the combined ProjectionPayload
// containing both OLS linear and VWEPR curved projections.
//
// ── Design rationale ───────────────────────────────────────────────────────
//   • `MinimalCandle` is purposely slim (time + close + volume): the UI
//     already has OHLCV data in JS memory, but only these three fields are
//     needed for the weighted regression. Sending less data across the IPC
//     bridge reduces serialization overhead.
//
//   • The command is synchronous (`pub fn`, not `pub async fn`) because
//     both math engines are pure CPU work with zero I/O. Tauri dispatches
//     sync commands on a thread-pool, so the main thread stays unblocked.
//
//   • The 20-candle minimum guard prevents degenerate matrix fits that would
//     produce nonsensical projections.

use crate::quant::vwepr::OhlcCandle;
use crate::quant::predictive::{self, ProjectionPayload};

// ── Minimal IPC Input Struct ────────────────────────────────────────────────

/// Lightweight candle representation for the IPC boundary.
///
/// The React frontend sends only the three fields the regression engines need,
/// keeping serialization payload minimal. OHLC high/low are not required for
/// either OLS or VWEPR — they are back-filled as the close price during
/// the conversion to `OhlcCandle`.
#[derive(serde::Deserialize, Debug)]
pub struct MinimalCandle {
    /// UNIX timestamp in seconds (candle open time).
    pub time: i64,
    /// Closing price.
    pub close: f64,
    /// Traded volume for this bar.
    pub volume: f64,
}

// ── Constants ───────────────────────────────────────────────────────────────

/// Minimum candle count required for a meaningful polynomial fit.
/// Below this threshold the command returns an empty payload instead of
/// attempting a degenerate regression.
const MIN_CANDLES_FOR_FIT: usize = 20;

/// Default number of future bars to project when the caller does not specify.
const DEFAULT_PROJECTION_LENGTH: usize = 6;

// ── Tauri Command ───────────────────────────────────────────────────────────

/// Compute the Dual-Engine Ghost Curve from a lightweight candle payload.
///
/// Runs both the OLS linear regression and the VWEPR curved polynomial
/// regression natively in Rust, returning the combined `ProjectionPayload`.
///
/// # Frontend invocation
/// ```ts
/// const payload = await invoke<ProjectionPayload>("compute_ghost_curve", {
///   candles: minimalCandles, // Array<{ time: number, close: number, volume: number }>
///   intervalSec: 300,       // bar duration in seconds (e.g., 300 for 5m)
///   projectionLength: 6,    // optional, defaults to 6
/// });
///
/// // payload.linear_points  — OLS baseline (straight line)
/// // payload.curved_points  — VWEPR curve (polynomial)
/// // payload.acceleration_coefficient — quadratic 'a' for AI analysis
/// ```
///
/// # Returns
/// `Ok(ProjectionPayload)` — contains both projection arrays and the
/// acceleration coefficient. When there are fewer than 20 candles, all
/// arrays are empty and the coefficient is 0.0.
///
/// # Errors
/// Returns `Err(String)` on invalid input (e.g., non-positive interval).
#[tauri::command]
pub fn compute_ghost_curve(
    candles: Vec<MinimalCandle>,
    interval_sec: i64,
    projection_length: Option<usize>,
) -> Result<ProjectionPayload, String> {
    // ── Guard: not enough data ──────────────────────────────────────────
    if candles.len() < MIN_CANDLES_FOR_FIT {
        return Ok(ProjectionPayload {
            linear_points: vec![],
            volume_points: vec![],
            curved_points: vec![],
            acceleration_coefficient: 0.0,
        });
    }

    // ── Guard: invalid interval ─────────────────────────────────────────
    if interval_sec <= 0 {
        return Err("interval_sec must be a positive integer".into());
    }

    let proj_len = projection_length.unwrap_or(DEFAULT_PROJECTION_LENGTH);

    // ── Map MinimalCandle → OhlcCandle ──────────────────────────────────
    //
    // Both math engines expect OhlcCandle (with OHLCV). Since the
    // regressions only use `close` and `volume`, we fill open/high/low
    // with the close price. This is semantically correct for the math
    // and avoids forcing the frontend to send unused fields.
    let ohlc_candles: Vec<OhlcCandle> = candles
        .into_iter()
        .map(|mc| OhlcCandle {
            time: mc.time,
            open: mc.close,
            high: mc.close,
            low: mc.close,
            close: mc.close,
            volume: mc.volume,
        })
        .collect();

    // ── Delegate to the Dual-Engine predictive module ────────────────────
    let payload = predictive::calculate_dual_projection(
        &ohlc_candles,
        proj_len,
        interval_sec,
    );

    Ok(payload)
}

// ── Unit Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_candles(count: usize) -> Vec<MinimalCandle> {
        (0..count)
            .map(|i| MinimalCandle {
                time: 1000 + i as i64 * 60,
                close: 100.0 + i as f64 * 0.5,
                volume: 1000.0,
            })
            .collect()
    }

    #[test]
    fn returns_empty_below_minimum() {
        let candles = make_candles(10); // < 20
        let result = compute_ghost_curve(candles, 60, None).unwrap();
        assert!(result.linear_points.is_empty());
        assert!(result.curved_points.is_empty());
        assert!((result.acceleration_coefficient - 0.0).abs() < 1e-15);
    }

    #[test]
    fn returns_payload_at_minimum() {
        let candles = make_candles(20); // exactly 20
        let result = compute_ghost_curve(candles, 60, None).unwrap();
        // Both engines: 1 anchor + 6 default projection points
        assert_eq!(result.linear_points.len(), 7);
        assert_eq!(result.curved_points.len(), 7);
        assert!(result.acceleration_coefficient.is_finite());
    }

    #[test]
    fn custom_projection_length() {
        let candles = make_candles(30);
        let result = compute_ghost_curve(candles, 300, Some(12)).unwrap();
        // 1 anchor + 12 projected for each engine
        assert_eq!(result.linear_points.len(), 13);
        assert_eq!(result.curved_points.len(), 13);
    }

    #[test]
    fn both_anchors_match_last_close() {
        let candles = make_candles(25);
        let last_close = candles.last().unwrap().close;
        let last_time = candles.last().unwrap().time;

        let result = compute_ghost_curve(candles, 60, None).unwrap();

        // OLS anchor
        assert_eq!(result.linear_points[0].time, last_time);
        assert!(
            (result.linear_points[0].value - last_close).abs() < 1e-10,
            "OLS anchor must match last close"
        );

        // VWEPR anchor
        assert_eq!(result.curved_points[0].time, last_time);
        assert!(
            (result.curved_points[0].value - last_close).abs() < 1e-10,
            "VWEPR anchor must match last close"
        );
    }

    #[test]
    fn rejects_invalid_interval() {
        let candles = make_candles(25);
        let result = compute_ghost_curve(candles, 0, None);
        assert!(result.is_err());
    }

    #[test]
    fn negative_interval_is_rejected() {
        let candles = make_candles(25);
        let result = compute_ghost_curve(candles, -60, None);
        assert!(result.is_err());
    }

    #[test]
    fn acceleration_coefficient_is_exposed() {
        // Quadratic data should produce a non-zero acceleration
        let candles: Vec<MinimalCandle> = (0..30)
            .map(|i| MinimalCandle {
                time: 1000 + i as i64 * 60,
                close: 100.0 + 0.1 * (i as f64).powi(2),
                volume: 500.0,
            })
            .collect();

        let result = compute_ghost_curve(candles, 60, Some(5)).unwrap();
        assert!(
            result.acceleration_coefficient > 0.0,
            "Quadratic data should produce positive acceleration: got {}",
            result.acceleration_coefficient
        );
    }

    #[test]
    fn payload_serializes_correctly() {
        let candles = make_candles(25);
        let result = compute_ghost_curve(candles, 60, None).unwrap();

        let json = serde_json::to_string(&result);
        assert!(json.is_ok(), "Payload must serialize to JSON for IPC");

        let json_str = json.unwrap();
        assert!(json_str.contains("linear_points"));
        assert!(json_str.contains("curved_points"));
        assert!(json_str.contains("acceleration_coefficient"));
    }
}
