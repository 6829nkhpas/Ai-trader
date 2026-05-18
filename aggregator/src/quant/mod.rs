// quant/mod.rs — V3 Consensus Engine Module Root.
//
// Aggregates candlestick pattern detection and institutional strategy
// evaluation into a single ConsensusReport that is serialized and passed
// downstream to the UI and DeepSeek LLM for final decision augmentation.
//
// Module structure:
//   quant/
//   ├── mod.rs        ← this file (ConsensusReport + orchestration)
//   ├── patterns.rs   ← Candlestick pattern matcher (Engulfing, Doji, etc.)
//   └── strategies.rs ← Institutional strategy engine (Golden Cross, VWAP, ORB)

pub mod patterns;
pub mod strategies;

use patterns::{Candle, PatternEngine};
use strategies::{IndicatorSnapshot, StrategyEngine};

// ── Consensus Report ────────────────────────────────────────────────────────

/// The final output of the V3 Quant Engine.
///
/// Aggregates all detected candlestick patterns and active institutional
/// strategies into a single serializable struct. This is the payload that
/// gets sent to:
///   1. The frontend UI (via WebSocket JSON broadcast)
///   2. The DeepSeek/LLM context window (as structured quant evidence)
///
/// # Example JSON output
/// ```json
/// {
///   "symbol": "RELIANCE",
///   "active_patterns": ["Bullish Engulfing", "Hammer"],
///   "active_strategies": ["Golden Cross", "VWAP Bounce (Bullish)"],
///   "pattern_count": 2,
///   "strategy_count": 2,
///   "quant_bias": "BULLISH"
/// }
/// ```
#[derive(Debug, Clone, serde::Serialize)]
pub struct ConsensusReport {
    /// The symbol this report pertains to (e.g., "RELIANCE", "NIFTY50").
    pub symbol: String,

    /// Names of all candlestick patterns detected on the most recent candle.
    pub active_patterns: Vec<String>,

    /// Names of all institutional strategies currently active.
    pub active_strategies: Vec<String>,

    /// Total number of detected patterns (convenience field for UI badges).
    pub pattern_count: usize,

    /// Total number of active strategies (convenience field for UI badges).
    pub strategy_count: usize,

    /// Derived directional bias based on pattern + strategy consensus.
    /// Values: "BULLISH", "BEARISH", "NEUTRAL", "MIXED"
    pub quant_bias: String,

    // We will add indicator consensus scores here in the next prompt
}

// ── Consensus Builder ───────────────────────────────────────────────────────

/// Orchestrator that runs both engines and produces a unified ConsensusReport.
pub struct ConsensusEngine;

impl ConsensusEngine {
    /// Run the full quant analysis pipeline for a given symbol.
    ///
    /// # Arguments
    /// * `symbol`     — The trading symbol (e.g., "RELIANCE").
    /// * `history`    — OHLCV candle history (most recent candle last).
    /// * `indicators` — Pre-calculated indicator snapshot for the current tick.
    ///
    /// # Returns
    /// A fully populated `ConsensusReport` ready for serialization.
    pub fn analyze(
        symbol: &str,
        history: &[Candle],
        indicators: &IndicatorSnapshot,
    ) -> ConsensusReport {
        // ── Phase 1: Pattern Detection ──────────────────────────────────
        let active_patterns = PatternEngine::analyze(history);

        // ── Phase 2: Strategy Evaluation ────────────────────────────────
        let active_strategies = StrategyEngine::evaluate(history, indicators);

        // ── Phase 3: Derive Consensus Bias ──────────────────────────────
        let quant_bias = Self::derive_bias(&active_patterns, &active_strategies);

        let pattern_count = active_patterns.len();
        let strategy_count = active_strategies.len();

        ConsensusReport {
            symbol: symbol.to_string(),
            active_patterns,
            active_strategies,
            pattern_count,
            strategy_count,
            quant_bias,
        }
    }

    /// Derive a directional bias from the combined pattern + strategy signals.
    ///
    /// Scoring system:
    ///   +1 for each bullish signal, -1 for each bearish signal, 0 for neutral.
    ///   Final score: > 0 → BULLISH, < 0 → BEARISH, 0 with signals → MIXED,
    ///   0 without signals → NEUTRAL.
    fn derive_bias(patterns: &[String], strategies: &[String]) -> String {
        let mut score: i32 = 0;
        let mut signal_count: usize = 0;

        // ── Score patterns ──────────────────────────────────────────────
        for p in patterns {
            match p.as_str() {
                "Bullish Engulfing" | "Hammer" => {
                    score += 1;
                    signal_count += 1;
                }
                "Bearish Engulfing" | "Shooting Star" => {
                    score -= 1;
                    signal_count += 1;
                }
                "Doji" => {
                    // Doji is neutral / indecision — counts as a signal but no bias
                    signal_count += 1;
                }
                _ => {}
            }
        }

        // ── Score strategies ────────────────────────────────────────────
        for s in strategies {
            match s.as_str() {
                "Golden Cross" | "VWAP Bounce (Bullish)" | "ORB Breakout (Bullish)" => {
                    score += 1;
                    signal_count += 1;
                }
                "Death Cross" | "ORB Breakdown (Bearish)" => {
                    score -= 1;
                    signal_count += 1;
                }
                _ => {}
            }
        }

        // ── Map score to bias label ─────────────────────────────────────
        if signal_count == 0 {
            "NEUTRAL".to_string()
        } else if score > 0 {
            "BULLISH".to_string()
        } else if score < 0 {
            "BEARISH".to_string()
        } else {
            // Signals exist but cancel out → conflicting signals
            "MIXED".to_string()
        }
    }
}

// ── Unit Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn candle(open: f64, high: f64, low: f64, close: f64, volume: f64) -> Candle {
        Candle { open, high, low, close, volume }
    }

    fn neutral_indicators() -> IndicatorSnapshot {
        IndicatorSnapshot {
            sma_50: 100.0,
            sma_200: 100.0,
            prev_sma_50: 100.0,
            prev_sma_200: 100.0,
            vwap: 100.0,
            average_volume: 100_000.0,
            orb_high: f64::NAN,
            orb_low: f64::NAN,
        }
    }

    #[test]
    fn consensus_report_serializes_to_json() {
        let report = ConsensusReport {
            symbol: "RELIANCE".to_string(),
            active_patterns: vec!["Bullish Engulfing".to_string()],
            active_strategies: vec!["Golden Cross".to_string()],
            pattern_count: 1,
            strategy_count: 1,
            quant_bias: "BULLISH".to_string(),
        };

        let json = serde_json::to_string(&report).expect("Failed to serialize ConsensusReport");
        assert!(json.contains("\"symbol\":\"RELIANCE\""));
        assert!(json.contains("\"quant_bias\":\"BULLISH\""));
    }

    #[test]
    fn full_pipeline_bullish_engulfing_with_golden_cross() {
        // Candle pair that triggers Bullish Engulfing
        let prev = candle(104.0, 105.0, 99.0, 100.0, 90_000.0);  // red
        let curr = candle(99.0, 106.0, 98.0, 105.0, 150_000.0);   // green, engulfs prev

        let mut ind = neutral_indicators();
        // Golden Cross: prev SMA50 below SMA200, now above
        ind.prev_sma_50 = 99.0;
        ind.prev_sma_200 = 100.0;
        ind.sma_50 = 101.0;
        ind.sma_200 = 100.0;

        let report = ConsensusEngine::analyze("RELIANCE", &[prev, curr], &ind);

        assert_eq!(report.symbol, "RELIANCE");
        assert!(report.active_patterns.contains(&"Bullish Engulfing".to_string()));
        assert!(report.active_strategies.contains(&"Golden Cross".to_string()));
        assert_eq!(report.quant_bias, "BULLISH");
        assert!(report.pattern_count >= 1);
        assert!(report.strategy_count >= 1);
    }

    #[test]
    fn derive_bias_neutral_on_no_signals() {
        let bias = ConsensusEngine::derive_bias(&[], &[]);
        assert_eq!(bias, "NEUTRAL");
    }

    #[test]
    fn derive_bias_mixed_on_conflicting_signals() {
        let patterns = vec!["Bullish Engulfing".to_string()];
        let strategies = vec!["Death Cross".to_string()];
        let bias = ConsensusEngine::derive_bias(&patterns, &strategies);
        assert_eq!(bias, "MIXED");
    }

    #[test]
    fn derive_bias_bearish() {
        let patterns = vec!["Bearish Engulfing".to_string(), "Shooting Star".to_string()];
        let strategies = vec!["Death Cross".to_string()];
        let bias = ConsensusEngine::derive_bias(&patterns, &strategies);
        assert_eq!(bias, "BEARISH");
    }
}
