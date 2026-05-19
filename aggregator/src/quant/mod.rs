// quant/mod.rs — V3 Consensus Engine with Indicator Scoring Matrix.
//
// Aggregates candlestick patterns, institutional strategies, and a full
// mathematical indicator matrix into a single ConsensusReport payload
// for the UI and DeepSeek LLM.
//
// Module structure:
//   quant/
//   ├── mod.rs        ← this file (IndicatorState, ConsensusReport, scoring matrix)
//   ├── patterns.rs   ← Candlestick pattern matcher (Engulfing, Doji, etc.)
//   └── strategies.rs ← Institutional strategy engine (Golden Cross, VWAP, ORB)
//
// STATUS: Fully implemented & unit-tested.
// PENDING: Wiring into the ohlc_server consumer loop once the indicator
//          computation pipeline (SMA50/200, MACD, SAR, RSI, etc.) is connected.
//          At that point, remove this allow and call ConsensusEngine::compile_consensus
//          inside ohlc_server::process_candle() before the WS broadcast.
#![allow(dead_code)]

pub mod patterns;
pub mod strategies;

use patterns::{Candle, PatternEngine};
use strategies::{IndicatorSnapshot, StrategyEngine};

// ── Indicator State ─────────────────────────────────────────────────────────

/// Comprehensive pre-calculated indicator state for the current tick.
///
/// All values are computed upstream (OHLC engine / indicator service) and
/// passed into the consensus compiler. The engine does NOT recalculate
/// indicators — it only evaluates scoring conditions.
#[derive(Debug, Clone)]
pub struct IndicatorState {
    // ── Moving Averages ─────────────────────────────────────────────────
    pub sma_50: f64,
    pub sma_200: f64,
    pub prev_sma_50: f64,
    pub prev_sma_200: f64,

    // ── MACD ────────────────────────────────────────────────────────────
    /// MACD histogram value (MACD line minus signal line).
    pub macd_histogram: f64,

    // ── Parabolic SAR ───────────────────────────────────────────────────
    /// Current Parabolic SAR value. Bullish when below price, bearish above.
    pub parabolic_sar: f64,

    // ── Momentum Oscillators ────────────────────────────────────────────
    /// 14-period Relative Strength Index (0–100).
    pub rsi_14: f64,
    /// Stochastic %K (0–100).
    pub stoch_k: f64,

    // ── Volatility ──────────────────────────────────────────────────────
    /// Bollinger Band upper boundary.
    pub bb_upper: f64,
    /// Bollinger Band lower boundary.
    pub bb_lower: f64,
    /// 20-period moving average of the Average True Range.
    pub atr_20_ma: f64,

    // ── Volume ──────────────────────────────────────────────────────────
    /// On-Balance Volume current value.
    pub obv_current: f64,
    /// On-Balance Volume from the previous bar (for slope detection).
    pub obv_previous: f64,
    /// Chaikin Money Flow (typically 20-period). Range: -1.0 to +1.0.
    pub cmf: f64,

    // ── Session / Strategy fields (passed through to StrategyEngine) ───
    pub vwap: f64,
    pub average_volume: f64,
    pub orb_high: f64,
    pub orb_low: f64,
}

impl IndicatorState {
    /// Project the strategy-specific subset into an `IndicatorSnapshot`
    /// for the Phase-1 `StrategyEngine`.
    pub fn to_snapshot(&self) -> IndicatorSnapshot {
        IndicatorSnapshot {
            sma_50: self.sma_50,
            sma_200: self.sma_200,
            prev_sma_50: self.prev_sma_50,
            prev_sma_200: self.prev_sma_200,
            vwap: self.vwap,
            average_volume: self.average_volume,
            orb_high: self.orb_high,
            orb_low: self.orb_low,
        }
    }
}

// ── Consensus Report ────────────────────────────────────────────────────────

/// The unified V3 Quant Engine output payload.
///
/// Serialized as JSON and sent to:
///   1. Frontend UI (WebSocket broadcast — badges, gauges, bias labels)
///   2. DeepSeek/LLM context window (structured quant evidence)
///
/// # Example JSON
/// ```json
/// {
///   "symbol": "RELIANCE",
///   "trend_score": 75,
///   "momentum_state": "OVERBOUGHT",
///   "volatility_state": "EXPANDING",
///   "volume_flow_state": "ACCUMULATION",
///   "active_patterns": ["Bullish Engulfing"],
///   "active_strategies": ["Golden Cross", "VWAP Bounce (Bullish)"]
/// }
/// ```
#[derive(Debug, Clone, serde::Serialize)]
pub struct ConsensusReport {
    /// Trading symbol (e.g., "RELIANCE", "NIFTY50").
    pub symbol: String,

    /// Composite trend score bounded to [-100, +100].
    /// Derived from 4 independent trend signals, each weighted ±25.
    pub trend_score: i32,

    /// Momentum regime: "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL".
    pub momentum_state: String,

    /// Volatility regime: "SQUEEZING" | "EXPANDING" | "NORMAL".
    pub volatility_state: String,

    /// Volume flow regime: "ACCUMULATION" | "DISTRIBUTION" | "NEUTRAL".
    pub volume_flow_state: String,

    /// Candlestick patterns detected on the most recent candle.
    pub active_patterns: Vec<String>,

    /// Institutional strategies currently active.
    pub active_strategies: Vec<String>,
}

// ── Trend Score Constants ───────────────────────────────────────────────────

/// Weight per trend component. 4 components × 25 = ±100 max.
const TREND_COMPONENT_WEIGHT: i32 = 25;

// ── Momentum Thresholds ─────────────────────────────────────────────────────

const RSI_OVERBOUGHT: f64 = 70.0;
const RSI_OVERSOLD: f64 = 30.0;
const STOCH_OVERBOUGHT: f64 = 80.0;
const STOCH_OVERSOLD: f64 = 20.0;

// ── Volume Flow Thresholds ──────────────────────────────────────────────────

const CMF_ACCUMULATION: f64 = 0.05;
const CMF_DISTRIBUTION: f64 = -0.05;

// ── Consensus Engine ────────────────────────────────────────────────────────

/// Orchestrator: runs pattern detection, strategy evaluation, and the
/// indicator scoring matrix, then fuses everything into a ConsensusReport.
pub struct ConsensusEngine;

impl ConsensusEngine {
    /// Full V3 quant analysis pipeline.
    ///
    /// # Arguments
    /// * `symbol`     — Trading symbol.
    /// * `candles`    — OHLCV history (most recent last).
    /// * `indicators` — Pre-calculated indicator state for the current tick.
    pub fn compile_consensus(
        symbol: &str,
        candles: &[Candle],
        indicators: &IndicatorState,
    ) -> ConsensusReport {
        // ── Phase 1: Candlestick Pattern Detection ──────────────────────
        let active_patterns = PatternEngine::analyze(candles);

        // ── Phase 2: Institutional Strategy Evaluation ──────────────────
        let snapshot = indicators.to_snapshot();
        let active_strategies = StrategyEngine::evaluate(candles, &snapshot);

        // ── Phase 3: Mathematical Scoring Matrix ────────────────────────
        let current_close = candles.last().map(|c| c.close).unwrap_or(0.0);

        let trend_score = Self::compute_trend_score(current_close, indicators);
        let momentum_state = Self::compute_momentum_state(indicators);
        let volatility_state = Self::compute_volatility_state(candles, indicators);
        let volume_flow_state = Self::compute_volume_flow_state(indicators);

        ConsensusReport {
            symbol: symbol.to_string(),
            trend_score,
            momentum_state,
            volatility_state,
            volume_flow_state,
            active_patterns,
            active_strategies,
        }
    }

    // ── Trend Score (-100 to +100) ──────────────────────────────────────
    //
    // Four independent boolean signals, each contributing ±25:
    //   1. Price vs SMA-50:  close > sma_50  → +25, else -25
    //   2. Price vs SMA-200: close > sma_200 → +25, else -25
    //   3. MACD Histogram:   > 0             → +25, else -25
    //   4. Parabolic SAR:    SAR < close      → +25, else -25
    //
    // Sum is inherently bounded: 4 × +25 = +100, 4 × -25 = -100.
    fn compute_trend_score(close: f64, ind: &IndicatorState) -> i32 {
        let mut score: i32 = 0;

        // Component 1: Price vs SMA-50
        if ind.sma_50.is_finite() {
            score += if close > ind.sma_50 {
                TREND_COMPONENT_WEIGHT
            } else {
                -TREND_COMPONENT_WEIGHT
            };
        }

        // Component 2: Price vs SMA-200
        if ind.sma_200.is_finite() {
            score += if close > ind.sma_200 {
                TREND_COMPONENT_WEIGHT
            } else {
                -TREND_COMPONENT_WEIGHT
            };
        }

        // Component 3: MACD Histogram
        if ind.macd_histogram.is_finite() {
            score += if ind.macd_histogram > 0.0 {
                TREND_COMPONENT_WEIGHT
            } else {
                -TREND_COMPONENT_WEIGHT
            };
        }

        // Component 4: Parabolic SAR (below price = bullish)
        if ind.parabolic_sar.is_finite() {
            score += if ind.parabolic_sar < close {
                TREND_COMPONENT_WEIGHT
            } else {
                -TREND_COMPONENT_WEIGHT
            };
        }

        // Clamp as safety net (already bounded by construction)
        score.clamp(-100, 100)
    }

    // ── Momentum State ──────────────────────────────────────────────────
    //
    // Rules (OR logic — either oscillator can trigger):
    //   OVERBOUGHT: rsi_14 > 70  OR  stoch_k > 80
    //   OVERSOLD:   rsi_14 < 30  OR  stoch_k < 20
    //   NEUTRAL:    otherwise
    //
    // If both overbought AND oversold fire (impossible in practice but
    // guarded), OVERBOUGHT takes precedence.
    fn compute_momentum_state(ind: &IndicatorState) -> String {
        let rsi_ob = ind.rsi_14.is_finite() && ind.rsi_14 > RSI_OVERBOUGHT;
        let stoch_ob = ind.stoch_k.is_finite() && ind.stoch_k > STOCH_OVERBOUGHT;
        let rsi_os = ind.rsi_14.is_finite() && ind.rsi_14 < RSI_OVERSOLD;
        let stoch_os = ind.stoch_k.is_finite() && ind.stoch_k < STOCH_OVERSOLD;

        if rsi_ob || stoch_ob {
            "OVERBOUGHT".to_string()
        } else if rsi_os || stoch_os {
            "OVERSOLD".to_string()
        } else {
            "NEUTRAL".to_string()
        }
    }

    // ── Volatility State ────────────────────────────────────────────────
    //
    // Bollinger Band width vs ATR-based historical volatility:
    //
    //   bb_width = bb_upper - bb_lower
    //   SQUEEZING:  bb_width < atr_20_ma  (bands narrower than avg volatility)
    //   EXPANDING:  current candle H/L breaks outside the bands
    //   NORMAL:     otherwise
    fn compute_volatility_state(candles: &[Candle], ind: &IndicatorState) -> String {
        if !ind.bb_upper.is_finite() || !ind.bb_lower.is_finite() || !ind.atr_20_ma.is_finite() {
            return "NORMAL".to_string();
        }

        let bb_width = ind.bb_upper - ind.bb_lower;

        // Check for band breakout on the current candle
        if let Some(current) = candles.last() {
            if current.high > ind.bb_upper || current.low < ind.bb_lower {
                return "EXPANDING".to_string();
            }
        }

        // Compare BB width to the 20-period MA of ATR
        // ATR_20_MA represents a single-bar average range; BB width spans
        // ~4σ of price. We compare directly: if the band is tighter than
        // the average true range, volatility is compressing.
        if bb_width < ind.atr_20_ma {
            "SQUEEZING".to_string()
        } else {
            "NORMAL".to_string()
        }
    }

    // ── Volume Flow State ───────────────────────────────────────────────
    //
    // Combines OBV slope with Chaikin Money Flow:
    //   ACCUMULATION:  cmf > +0.05  AND  OBV is rising (current > previous)
    //   DISTRIBUTION:  cmf < -0.05  AND  OBV is falling (current < previous)
    //   NEUTRAL:       otherwise
    fn compute_volume_flow_state(ind: &IndicatorState) -> String {
        let obv_rising = ind.obv_current.is_finite()
            && ind.obv_previous.is_finite()
            && ind.obv_current > ind.obv_previous;

        let obv_falling = ind.obv_current.is_finite()
            && ind.obv_previous.is_finite()
            && ind.obv_current < ind.obv_previous;

        let cmf_valid = ind.cmf.is_finite();

        if cmf_valid && ind.cmf > CMF_ACCUMULATION && obv_rising {
            "ACCUMULATION".to_string()
        } else if cmf_valid && ind.cmf < CMF_DISTRIBUTION && obv_falling {
            "DISTRIBUTION".to_string()
        } else {
            "NEUTRAL".to_string()
        }
    }

    // ── Legacy Phase-1 entry point (backwards compatible) ───────────────

    /// Phase-1 analyze (kept for backward compatibility).
    pub fn analyze(
        symbol: &str,
        history: &[Candle],
        indicators: &IndicatorSnapshot,
    ) -> ConsensusReport {
        let active_patterns = PatternEngine::analyze(history);
        let active_strategies = StrategyEngine::evaluate(history, indicators);
        let close = history.last().map(|c| c.close).unwrap_or(0.0);

        // Build a minimal IndicatorState from the snapshot for scoring
        let ind = IndicatorState {
            sma_50: indicators.sma_50,
            sma_200: indicators.sma_200,
            prev_sma_50: indicators.prev_sma_50,
            prev_sma_200: indicators.prev_sma_200,
            macd_histogram: f64::NAN,
            parabolic_sar: f64::NAN,
            rsi_14: f64::NAN,
            stoch_k: f64::NAN,
            bb_upper: f64::NAN,
            bb_lower: f64::NAN,
            atr_20_ma: f64::NAN,
            obv_current: f64::NAN,
            obv_previous: f64::NAN,
            cmf: f64::NAN,
            vwap: indicators.vwap,
            average_volume: indicators.average_volume,
            orb_high: indicators.orb_high,
            orb_low: indicators.orb_low,
        };

        ConsensusReport {
            symbol: symbol.to_string(),
            trend_score: Self::compute_trend_score(close, &ind),
            momentum_state: Self::compute_momentum_state(&ind),
            volatility_state: Self::compute_volatility_state(history, &ind),
            volume_flow_state: Self::compute_volume_flow_state(&ind),
            active_patterns,
            active_strategies,
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

    fn base_state() -> IndicatorState {
        IndicatorState {
            sma_50: 100.0,
            sma_200: 100.0,
            prev_sma_50: 100.0,
            prev_sma_200: 100.0,
            macd_histogram: 0.0,
            parabolic_sar: 100.0,
            rsi_14: 50.0,
            stoch_k: 50.0,
            bb_upper: 110.0,
            bb_lower: 90.0,
            atr_20_ma: 15.0,
            obv_current: 1_000_000.0,
            obv_previous: 1_000_000.0,
            cmf: 0.0,
            vwap: 100.0,
            average_volume: 100_000.0,
            orb_high: f64::NAN,
            orb_low: f64::NAN,
        }
    }

    // ── Trend Score Tests ───────────────────────────────────────────────

    #[test]
    fn trend_score_max_bullish() {
        // close=105 > sma50=100 (+25), > sma200=100 (+25),
        // macd=1.5 > 0 (+25), sar=98 < 105 (+25) → total = +100
        let mut ind = base_state();
        ind.macd_histogram = 1.5;
        ind.parabolic_sar = 98.0;
        let score = ConsensusEngine::compute_trend_score(105.0, &ind);
        assert_eq!(score, 100);
    }

    #[test]
    fn trend_score_max_bearish() {
        // close=95 < sma50=100 (-25), < sma200=100 (-25),
        // macd=-2.0 < 0 (-25), sar=102 > 95 (-25) → total = -100
        let mut ind = base_state();
        ind.macd_histogram = -2.0;
        ind.parabolic_sar = 102.0;
        let score = ConsensusEngine::compute_trend_score(95.0, &ind);
        assert_eq!(score, -100);
    }

    #[test]
    fn trend_score_mixed() {
        // close=105 > sma50 (+25), > sma200 (+25),
        // macd=-1.0 < 0 (-25), sar=98 < 105 (+25) → total = +50
        let mut ind = base_state();
        ind.macd_histogram = -1.0;
        ind.parabolic_sar = 98.0;
        let score = ConsensusEngine::compute_trend_score(105.0, &ind);
        assert_eq!(score, 50);
    }

    #[test]
    fn trend_score_handles_nan_gracefully() {
        let mut ind = base_state();
        ind.sma_50 = f64::NAN;
        ind.sma_200 = f64::NAN;
        ind.macd_histogram = f64::NAN;
        ind.parabolic_sar = f64::NAN;
        let score = ConsensusEngine::compute_trend_score(105.0, &ind);
        assert_eq!(score, 0); // No finite components → 0
    }

    // ── Momentum State Tests ────────────────────────────────────────────

    #[test]
    fn momentum_overbought_rsi() {
        let mut ind = base_state();
        ind.rsi_14 = 75.0; // > 70
        ind.stoch_k = 50.0;
        assert_eq!(ConsensusEngine::compute_momentum_state(&ind), "OVERBOUGHT");
    }

    #[test]
    fn momentum_overbought_stoch() {
        let mut ind = base_state();
        ind.rsi_14 = 50.0;
        ind.stoch_k = 85.0; // > 80
        assert_eq!(ConsensusEngine::compute_momentum_state(&ind), "OVERBOUGHT");
    }

    #[test]
    fn momentum_oversold_rsi() {
        let mut ind = base_state();
        ind.rsi_14 = 25.0; // < 30
        ind.stoch_k = 50.0;
        assert_eq!(ConsensusEngine::compute_momentum_state(&ind), "OVERSOLD");
    }

    #[test]
    fn momentum_oversold_stoch() {
        let mut ind = base_state();
        ind.rsi_14 = 50.0;
        ind.stoch_k = 15.0; // < 20
        assert_eq!(ConsensusEngine::compute_momentum_state(&ind), "OVERSOLD");
    }

    #[test]
    fn momentum_neutral() {
        let ind = base_state(); // rsi=50, stoch=50
        assert_eq!(ConsensusEngine::compute_momentum_state(&ind), "NEUTRAL");
    }

    // ── Volatility State Tests ──────────────────────────────────────────

    #[test]
    fn volatility_squeezing() {
        // bb_width = 110 - 90 = 20, atr_20_ma = 25 → width < atr → SQUEEZING
        let mut ind = base_state();
        ind.atr_20_ma = 25.0;
        let candles = vec![candle(100.0, 105.0, 95.0, 103.0, 100_000.0)];
        assert_eq!(ConsensusEngine::compute_volatility_state(&candles, &ind), "SQUEEZING");
    }

    #[test]
    fn volatility_expanding() {
        // Candle high=112 breaks above bb_upper=110 → EXPANDING
        let ind = base_state();
        let candles = vec![candle(100.0, 112.0, 95.0, 108.0, 100_000.0)];
        assert_eq!(ConsensusEngine::compute_volatility_state(&candles, &ind), "EXPANDING");
    }

    #[test]
    fn volatility_expanding_low_break() {
        // Candle low=88 breaks below bb_lower=90 → EXPANDING
        let ind = base_state();
        let candles = vec![candle(100.0, 105.0, 88.0, 92.0, 100_000.0)];
        assert_eq!(ConsensusEngine::compute_volatility_state(&candles, &ind), "EXPANDING");
    }

    #[test]
    fn volatility_normal() {
        // bb_width=20 >= atr_20_ma=15, no band break → NORMAL
        let ind = base_state();
        let candles = vec![candle(100.0, 105.0, 95.0, 103.0, 100_000.0)];
        assert_eq!(ConsensusEngine::compute_volatility_state(&candles, &ind), "NORMAL");
    }

    // ── Volume Flow State Tests ─────────────────────────────────────────

    #[test]
    fn volume_accumulation() {
        let mut ind = base_state();
        ind.cmf = 0.10;            // > 0.05
        ind.obv_current = 1_100_000.0;
        ind.obv_previous = 1_000_000.0; // rising
        assert_eq!(ConsensusEngine::compute_volume_flow_state(&ind), "ACCUMULATION");
    }

    #[test]
    fn volume_distribution() {
        let mut ind = base_state();
        ind.cmf = -0.12;           // < -0.05
        ind.obv_current = 900_000.0;
        ind.obv_previous = 1_000_000.0; // falling
        assert_eq!(ConsensusEngine::compute_volume_flow_state(&ind), "DISTRIBUTION");
    }

    #[test]
    fn volume_neutral_cmf_positive_obv_flat() {
        let mut ind = base_state();
        ind.cmf = 0.10; // positive but OBV flat
        ind.obv_current = 1_000_000.0;
        ind.obv_previous = 1_000_000.0;
        assert_eq!(ConsensusEngine::compute_volume_flow_state(&ind), "NEUTRAL");
    }

    #[test]
    fn volume_neutral_mixed_signals() {
        let mut ind = base_state();
        ind.cmf = 0.10;  // positive
        ind.obv_current = 900_000.0;
        ind.obv_previous = 1_000_000.0; // but falling → conflict → NEUTRAL
        assert_eq!(ConsensusEngine::compute_volume_flow_state(&ind), "NEUTRAL");
    }

    // ── Full Pipeline Integration Tests ─────────────────────────────────

    #[test]
    fn compile_consensus_full_bullish() {
        let prev = candle(104.0, 105.0, 99.0, 100.0, 90_000.0);  // red
        let curr = candle(99.0, 106.0, 98.0, 105.0, 160_000.0);   // green engulfing

        let mut ind = base_state();
        // Golden Cross setup
        ind.prev_sma_50 = 99.0;
        ind.prev_sma_200 = 100.0;
        ind.sma_50 = 101.0;
        ind.sma_200 = 100.0;
        // Bullish trend indicators
        ind.macd_histogram = 2.0;
        ind.parabolic_sar = 97.0;
        // Overbought momentum
        ind.rsi_14 = 72.0;
        ind.stoch_k = 82.0;
        // Accumulation
        ind.cmf = 0.15;
        ind.obv_current = 1_200_000.0;
        ind.obv_previous = 1_000_000.0;
        // VWAP bounce setup
        ind.vwap = 100.0;
        ind.average_volume = 100_000.0;

        let report = ConsensusEngine::compile_consensus(
            "RELIANCE",
            &[prev, curr],
            &ind,
        );

        assert_eq!(report.symbol, "RELIANCE");
        assert_eq!(report.trend_score, 100);
        assert_eq!(report.momentum_state, "OVERBOUGHT");
        assert_eq!(report.volume_flow_state, "ACCUMULATION");
        assert!(report.active_patterns.contains(&"Bullish Engulfing".to_string()));
        assert!(report.active_strategies.contains(&"Golden Cross".to_string()));
    }

    #[test]
    fn compile_consensus_serializes_to_json() {
        let candles = vec![candle(100.0, 105.0, 95.0, 103.0, 100_000.0)];
        let ind = base_state();

        let report = ConsensusEngine::compile_consensus("NIFTY50", &candles, &ind);
        let json = serde_json::to_string(&report).expect("Serialization failed");

        assert!(json.contains("\"symbol\":\"NIFTY50\""));
        assert!(json.contains("\"trend_score\":"));
        assert!(json.contains("\"momentum_state\":"));
        assert!(json.contains("\"volatility_state\":"));
        assert!(json.contains("\"volume_flow_state\":"));
        assert!(json.contains("\"active_patterns\":"));
        assert!(json.contains("\"active_strategies\":"));
    }

    #[test]
    fn legacy_analyze_still_works() {
        let candles = vec![candle(100.0, 105.0, 95.0, 103.0, 100_000.0)];
        let snapshot = IndicatorSnapshot {
            sma_50: 100.0,
            sma_200: 100.0,
            prev_sma_50: 100.0,
            prev_sma_200: 100.0,
            vwap: 100.0,
            average_volume: 100_000.0,
            orb_high: f64::NAN,
            orb_low: f64::NAN,
        };

        let report = ConsensusEngine::analyze("TCS", &candles, &snapshot);
        assert_eq!(report.symbol, "TCS");
        // With NaN MACD/SAR, only SMA components fire → +25 +25 = 50
        // close=103 > sma50=100 (+25), > sma200=100 (+25) = 50
        assert_eq!(report.trend_score, 50);
        assert_eq!(report.momentum_state, "NEUTRAL");
    }
}
