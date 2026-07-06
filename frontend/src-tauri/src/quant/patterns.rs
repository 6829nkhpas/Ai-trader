// quant/patterns.rs — Candlestick Pattern Matcher (Tauri-local copy).
//
// Mirrors aggregator/src/quant/patterns.rs for local Tauri execution.
// Detects: Bullish Engulfing, Bearish Engulfing, Doji, Hammer, Shooting Star.

/// A single OHLCV candle.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Candle {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

impl Candle {
    #[inline]
    pub fn body(&self) -> f64 { (self.close - self.open).abs() }
    #[inline]
    pub fn range(&self) -> f64 { self.high - self.low }
    #[inline]
    pub fn is_bullish(&self) -> bool { self.close > self.open }
    #[inline]
    pub fn is_bearish(&self) -> bool { self.open > self.close }
    #[inline]
    pub fn body_top(&self) -> f64 { self.open.max(self.close) }
    #[inline]
    pub fn body_bottom(&self) -> f64 { self.open.min(self.close) }
    #[inline]
    pub fn upper_shadow(&self) -> f64 { self.high - self.body_top() }
    #[inline]
    pub fn lower_shadow(&self) -> f64 { self.body_bottom() - self.low }
}

pub struct PatternEngine;

const MIN_RANGE: f64 = 1e-9;
const DOJI_BODY_RATIO: f64 = 0.10;
const SHADOW_BODY_MULTIPLIER: f64 = 2.0;
const BODY_POSITION_RATIO: f64 = 0.33;

impl PatternEngine {
    pub fn analyze(history: &[Candle]) -> Vec<String> {
        let mut signals: Vec<String> = Vec::new();
        if history.is_empty() { return signals; }

        let current = &history[history.len() - 1];
        let range = current.range();

        // Doji
        if range > MIN_RANGE && (current.body() / range) < DOJI_BODY_RATIO {
            signals.push("Doji".to_string());
        }

        // Hammer
        if range > MIN_RANGE && current.body() > MIN_RANGE {
            let body = current.body();
            if current.lower_shadow() >= body * SHADOW_BODY_MULTIPLIER
                && current.upper_shadow() <= range * BODY_POSITION_RATIO
            {
                signals.push("Hammer".to_string());
            }
        }

        // Shooting Star
        if range > MIN_RANGE && current.body() > MIN_RANGE {
            let body = current.body();
            if current.upper_shadow() >= body * SHADOW_BODY_MULTIPLIER
                && current.lower_shadow() <= range * BODY_POSITION_RATIO
            {
                signals.push("Shooting Star".to_string());
            }
        }

        // Two-candle patterns
        if history.len() >= 2 {
            let prev = &history[history.len() - 2];

            // Bullish Engulfing
            if prev.is_bearish() && current.is_bullish()
                && prev.body() > MIN_RANGE && current.body() > MIN_RANGE
                && current.body_bottom() <= prev.body_bottom()
                && current.body_top() >= prev.body_top()
            {
                signals.push("Bullish Engulfing".to_string());
            }

            // Bearish Engulfing
            if prev.is_bullish() && current.is_bearish()
                && prev.body() > MIN_RANGE && current.body() > MIN_RANGE
                && current.body_top() >= prev.body_top()
                && current.body_bottom() <= prev.body_bottom()
            {
                signals.push("Bearish Engulfing".to_string());
            }
        }

        signals
    }
}
