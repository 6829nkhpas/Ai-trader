// patterns.rs — Candlestick Pattern Matcher.
//
// V3 Quant Engine — Institutional Candlestick Pattern Recognition.
//
// Implements strict mathematical detection for classic candlestick formations
// on the most recent candle relative to its predecessor. Each pattern uses
// precise body/wick geometry — no approximations, no placeholders.
//
// STATUS: Fully implemented & unit-tested.
// PENDING: Called by ConsensusEngine::compile_consensus. Will become active once
//          ConsensusEngine is wired into ohlc_server. Remove allow at that point.
#![allow(dead_code)]
//
// Detected patterns:
//   1. Bullish Engulfing  — green candle body fully wraps prior red body
//   2. Bearish Engulfing  — red candle body fully wraps prior green body
//   3. Doji               — body is < 10% of total range (indecision)
//   4. Hammer             — small body at top, long lower shadow (reversal)
//   5. Shooting Star      — small body at bottom, long upper shadow (reversal)

/// A single OHLCV candle.
#[derive(Debug, Clone)]
pub struct Candle {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

impl Candle {
    /// True body size (absolute distance between open and close).
    #[inline]
    pub fn body(&self) -> f64 {
        (self.close - self.open).abs()
    }

    /// Total range from low to high.
    #[inline]
    pub fn range(&self) -> f64 {
        self.high - self.low
    }

    /// True if close > open (green candle).
    #[inline]
    pub fn is_bullish(&self) -> bool {
        self.close > self.open
    }

    /// True if open > close (red candle).
    #[inline]
    pub fn is_bearish(&self) -> bool {
        self.open > self.close
    }

    /// Top of the real body (max of open, close).
    #[inline]
    pub fn body_top(&self) -> f64 {
        self.open.max(self.close)
    }

    /// Bottom of the real body (min of open, close).
    #[inline]
    pub fn body_bottom(&self) -> f64 {
        self.open.min(self.close)
    }

    /// Upper shadow (wick above the body).
    #[inline]
    pub fn upper_shadow(&self) -> f64 {
        self.high - self.body_top()
    }

    /// Lower shadow (wick below the body).
    #[inline]
    pub fn lower_shadow(&self) -> f64 {
        self.body_bottom() - self.low
    }
}

// ── Pattern Engine ──────────────────────────────────────────────────────────

/// Stateless candlestick pattern recognizer.
///
/// Operates on a slice of historical candles and returns the names of all
/// patterns detected on the **most recent** candle.
pub struct PatternEngine;

/// Minimum candle range to avoid division-by-zero on flat ticks (₹0.01).
const MIN_RANGE: f64 = 1e-9;

/// Doji threshold: body must be < this fraction of total range.
const DOJI_BODY_RATIO: f64 = 0.10;

/// Hammer/Shooting Star: lower/upper shadow must be >= this multiple of body.
const SHADOW_BODY_MULTIPLIER: f64 = 2.0;

/// Hammer/Shooting Star: body must be in upper/lower this fraction of range.
const BODY_POSITION_RATIO: f64 = 0.33;

impl PatternEngine {
    /// Analyze the most recent candle in `history` against its predecessor.
    ///
    /// Returns a `Vec<String>` containing the names of every pattern detected
    /// on the last candle. Returns an empty vector if fewer than 1 candle is
    /// provided, or if no patterns match.
    pub fn analyze(history: &[Candle]) -> Vec<String> {
        let mut signals: Vec<String> = Vec::new();

        if history.is_empty() {
            return signals;
        }

        let current = &history[history.len() - 1];

        // ── Single-candle patterns (only need the current candle) ────────

        // 1. Doji — indecision candle
        //    Mathematical rule: |open - close| < (high - low) * 0.10
        //    Guards against zero-range ticks via MIN_RANGE.
        let range = current.range();
        if range > MIN_RANGE {
            let body_ratio = current.body() / range;
            if body_ratio < DOJI_BODY_RATIO {
                signals.push("Doji".to_string());
            }
        }

        // 2. Hammer — bullish reversal signal at bottom of downtrend
        //    Mathematical rules:
        //      a) Lower shadow >= 2× body size
        //      b) Upper shadow <= 1/3 of total range (body sits near the top)
        //      c) Body is non-trivial (not a doji)
        if range > MIN_RANGE && current.body() > MIN_RANGE {
            let body = current.body();
            let lower = current.lower_shadow();
            let upper = current.upper_shadow();

            if lower >= body * SHADOW_BODY_MULTIPLIER
                && upper <= range * BODY_POSITION_RATIO
            {
                signals.push("Hammer".to_string());
            }
        }

        // 3. Shooting Star — bearish reversal signal at top of uptrend
        //    Mathematical rules:
        //      a) Upper shadow >= 2× body size
        //      b) Lower shadow <= 1/3 of total range (body sits near the bottom)
        //      c) Body is non-trivial (not a doji)
        if range > MIN_RANGE && current.body() > MIN_RANGE {
            let body = current.body();
            let lower = current.lower_shadow();
            let upper = current.upper_shadow();

            if upper >= body * SHADOW_BODY_MULTIPLIER
                && lower <= range * BODY_POSITION_RATIO
            {
                signals.push("Shooting Star".to_string());
            }
        }

        // ── Two-candle patterns (require a predecessor) ─────────────────

        if history.len() >= 2 {
            let prev = &history[history.len() - 2];

            // 4. Bullish Engulfing
            //    Mathematical rules:
            //      a) Previous candle is bearish (red): prev.open > prev.close
            //      b) Current candle is bullish (green): current.close > current.open
            //      c) Current body fully engulfs previous body:
            //         current.body_bottom() <= prev.body_bottom()
            //         AND current.body_top() >= prev.body_top()
            //      d) Both candles have non-trivial bodies (avoid triggering on dojis).
            if prev.is_bearish()
                && current.is_bullish()
                && prev.body() > MIN_RANGE
                && current.body() > MIN_RANGE
                && current.body_bottom() <= prev.body_bottom()
                && current.body_top() >= prev.body_top()
            {
                signals.push("Bullish Engulfing".to_string());
            }

            // 5. Bearish Engulfing
            //    Mathematical rules:
            //      a) Previous candle is bullish (green): prev.close > prev.open
            //      b) Current candle is bearish (red): current.open > current.close
            //      c) Current body fully engulfs previous body:
            //         current.body_top() >= prev.body_top()
            //         AND current.body_bottom() <= prev.body_bottom()
            //      d) Both candles have non-trivial bodies.
            if prev.is_bullish()
                && current.is_bearish()
                && prev.body() > MIN_RANGE
                && current.body() > MIN_RANGE
                && current.body_top() >= prev.body_top()
                && current.body_bottom() <= prev.body_bottom()
            {
                signals.push("Bearish Engulfing".to_string());
            }
        }

        signals
    }
}

// ── Unit Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn candle(open: f64, high: f64, low: f64, close: f64) -> Candle {
        Candle { open, high, low, close, volume: 100_000.0 }
    }

    #[test]
    fn detects_doji() {
        // open ≈ close, large range → body/range ≈ 0.01 < 0.10
        let history = vec![candle(100.0, 105.0, 95.0, 100.1)];
        let patterns = PatternEngine::analyze(&history);
        assert!(patterns.contains(&"Doji".to_string()), "Expected Doji, got {:?}", patterns);
    }

    #[test]
    fn no_doji_on_large_body() {
        // body = 5.0, range = 10.0 → ratio = 0.50 > 0.10
        let history = vec![candle(95.0, 105.0, 95.0, 100.0)];
        let patterns = PatternEngine::analyze(&history);
        assert!(!patterns.contains(&"Doji".to_string()), "Should not detect Doji, got {:?}", patterns);
    }

    #[test]
    fn detects_bullish_engulfing() {
        let prev = candle(104.0, 105.0, 99.0, 100.0);   // red: open=104, close=100
        let curr = candle(99.0, 106.0, 98.0, 105.0);     // green: open=99, close=105
        // curr body_bottom=99 <= prev body_bottom=100 ✓
        // curr body_top=105 >= prev body_top=104 ✓
        let patterns = PatternEngine::analyze(&[prev, curr]);
        assert!(patterns.contains(&"Bullish Engulfing".to_string()), "Expected Bullish Engulfing, got {:?}", patterns);
    }

    #[test]
    fn detects_bearish_engulfing() {
        let prev = candle(100.0, 106.0, 99.0, 105.0);    // green: open=100, close=105
        let curr = candle(106.0, 107.0, 98.0, 99.0);     // red: open=106, close=99
        // curr body_top=106 >= prev body_top=105 ✓
        // curr body_bottom=99 <= prev body_bottom=100 ✓
        let patterns = PatternEngine::analyze(&[prev, curr]);
        assert!(patterns.contains(&"Bearish Engulfing".to_string()), "Expected Bearish Engulfing, got {:?}", patterns);
    }

    #[test]
    fn detects_hammer() {
        // Small body at top, long lower shadow
        // open=100, close=101 → body=1, body_top=101, body_bottom=100
        // high=101.2, low=95.0 → upper_shadow=0.2, lower_shadow=5.0
        // lower_shadow(5) >= body(1)*2 ✓, upper_shadow(0.2) <= range(6.2)*0.33=2.046 ✓
        let history = vec![candle(100.0, 101.2, 95.0, 101.0)];
        let patterns = PatternEngine::analyze(&history);
        assert!(patterns.contains(&"Hammer".to_string()), "Expected Hammer, got {:?}", patterns);
    }

    #[test]
    fn detects_shooting_star() {
        // Small body at bottom, long upper shadow
        // open=101, close=100 → body=1, body_top=101, body_bottom=100
        // high=106.0, low=99.8 → upper_shadow=5.0, lower_shadow=0.2
        // upper_shadow(5) >= body(1)*2 ✓, lower_shadow(0.2) <= range(6.2)*0.33=2.046 ✓
        let history = vec![candle(101.0, 106.0, 99.8, 100.0)];
        let patterns = PatternEngine::analyze(&history);
        assert!(patterns.contains(&"Shooting Star".to_string()), "Expected Shooting Star, got {:?}", patterns);
    }

    #[test]
    fn empty_history_returns_empty() {
        let patterns = PatternEngine::analyze(&[]);
        assert!(patterns.is_empty());
    }

    #[test]
    fn single_candle_no_engulfing() {
        let history = vec![candle(100.0, 105.0, 95.0, 103.0)];
        let patterns = PatternEngine::analyze(&history);
        assert!(!patterns.contains(&"Bullish Engulfing".to_string()));
        assert!(!patterns.contains(&"Bearish Engulfing".to_string()));
    }
}
