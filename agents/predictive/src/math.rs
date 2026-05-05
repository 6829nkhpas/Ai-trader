// math.rs — Predictive Math Engine (Linear Regression).
//
// Phase 6.1 — Alpha Suite Quantitative Core.
//
// Maintains a rolling window of the last 14 closing prices from 10-minute
// OHLC candles and uses Ordinary Least-Squares (OLS) linear regression to
// predict the close of the *next* (15th) candle.
//
// Confidence is the R² (coefficient of determination) of the fit, mapped
// from its natural [0, 1] range to a user-facing [1, 100] scale.
//
// All floating-point division is guarded against divide-by-zero.

use std::collections::VecDeque;

/// Maximum number of closing prices retained in the rolling window.
const WINDOW_SIZE: usize = 14;

/// Stores the last [`WINDOW_SIZE`] closing prices for a single symbol
/// and exposes a linear-regression predictor over them.
pub struct PredictionEngine {
    /// Rolling window of the most recent closing prices.
    closes: VecDeque<f64>,
}

impl PredictionEngine {
    /// Creates a new, empty `PredictionEngine`.
    pub fn new() -> Self {
        Self {
            closes: VecDeque::with_capacity(WINDOW_SIZE),
        }
    }

    /// Appends a closing price to the rolling window.
    ///
    /// If the window already contains [`WINDOW_SIZE`] items, the oldest
    /// entry is evicted before the new one is pushed.
    pub fn add_close_price(&mut self, price: f64) {
        if self.closes.len() >= WINDOW_SIZE {
            self.closes.pop_front();
        }
        self.closes.push_back(price);
    }

    /// Predicts the next closing price and returns a confidence score.
    ///
    /// Returns `None` if fewer than [`WINDOW_SIZE`] prices are available.
    ///
    /// # Mathematics
    ///
    /// Given time indices `x = 0, 1, … , 13` and corresponding closes `y`:
    ///
    /// ```text
    ///   m = (N·Σxy − Σx·Σy) / (N·Σx² − (Σx)²)
    ///   b = (Σy − m·Σx) / N
    ///   predicted_close = m × 14 + b
    /// ```
    ///
    /// Confidence is the R² value mapped to `[1, 100]`.
    pub fn predict_next(&self) -> Option<(f64, f64)> {
        if self.closes.len() < WINDOW_SIZE {
            return None;
        }

        let n = WINDOW_SIZE as f64;

        // ── Accumulators ─────────────────────────────────────────────────
        let mut sum_x: f64 = 0.0;
        let mut sum_y: f64 = 0.0;
        let mut sum_xy: f64 = 0.0;
        let mut sum_x2: f64 = 0.0;

        for (i, &y) in self.closes.iter().enumerate() {
            let x = i as f64;
            sum_x += x;
            sum_y += y;
            sum_xy += x * y;
            sum_x2 += x * x;
        }

        // ── Slope (m) and intercept (b) ──────────────────────────────────
        let denominator = n * sum_x2 - sum_x * sum_x;

        // Guard: if all x values are identical (impossible for 0..13, but
        // defensive against future changes), return None to avoid NaN.
        if denominator.abs() < f64::EPSILON {
            return None;
        }

        let m = (n * sum_xy - sum_x * sum_y) / denominator;
        let b = (sum_y - m * sum_x) / n;

        // ── Prediction ───────────────────────────────────────────────────
        let predicted_close = m * (WINDOW_SIZE as f64) + b;

        // ── R² (Coefficient of Determination) ────────────────────────────
        let y_mean = sum_y / n;

        let mut ss_res: f64 = 0.0; // sum of squared residuals
        let mut ss_tot: f64 = 0.0; // total sum of squares

        for (i, &y) in self.closes.iter().enumerate() {
            let y_hat = m * (i as f64) + b;
            ss_res += (y - y_hat).powi(2);
            ss_tot += (y - y_mean).powi(2);
        }

        // Guard: if all prices are identical, ss_tot == 0.  The model
        // perfectly "predicts" a flat line → R² = 1 → max confidence.
        let r_squared = if ss_tot.abs() < f64::EPSILON {
            1.0
        } else {
            1.0 - (ss_res / ss_tot)
        };

        // Map R² [0, 1] → [1, 100] and clamp.
        let confidence_score = (r_squared * 100.0).clamp(1.0, 100.0);

        Some((predicted_close, confidence_score))
    }
}

// ── Unit tests ──────────────────────────────────────────────────────────────
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn returns_none_when_window_incomplete() {
        let mut engine = PredictionEngine::new();
        for i in 0..13 {
            engine.add_close_price(100.0 + i as f64);
            assert!(engine.predict_next().is_none());
        }
    }

    #[test]
    fn returns_prediction_at_full_window() {
        let mut engine = PredictionEngine::new();
        for i in 0..14 {
            engine.add_close_price(100.0 + i as f64);
        }
        let result = engine.predict_next();
        assert!(result.is_some());
        let (predicted, confidence) = result.unwrap();
        // Perfect linear data → predicted should be 114.0, R² ≈ 1.0.
        assert!((predicted - 114.0).abs() < 1e-6);
        assert!((confidence - 100.0).abs() < 1e-6);
    }

    #[test]
    fn window_never_exceeds_capacity() {
        let mut engine = PredictionEngine::new();
        for i in 0..100 {
            engine.add_close_price(i as f64);
        }
        assert_eq!(engine.closes.len(), WINDOW_SIZE);
    }

    #[test]
    fn flat_prices_yield_high_confidence() {
        let mut engine = PredictionEngine::new();
        for _ in 0..14 {
            engine.add_close_price(50.0);
        }
        let (predicted, confidence) = engine.predict_next().unwrap();
        assert!((predicted - 50.0).abs() < 1e-6);
        assert!((confidence - 100.0).abs() < 1e-6);
    }

    #[test]
    fn confidence_is_clamped() {
        let mut engine = PredictionEngine::new();
        // Noisy data should still clamp between 1 and 100.
        let values = [1.0, 50.0, 2.0, 49.0, 3.0, 48.0, 4.0, 47.0, 5.0, 46.0, 6.0, 45.0, 7.0, 44.0];
        for v in values {
            engine.add_close_price(v);
        }
        let (_, confidence) = engine.predict_next().unwrap();
        assert!(confidence >= 1.0 && confidence <= 100.0);
    }
}
