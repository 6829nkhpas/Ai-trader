// quant/mod.rs — V3 Consensus Engine (Tauri-local).
//
// Full indicator scoring matrix + consensus compilation.
// Mirrors aggregator/src/quant/mod.rs for in-process Tauri execution.

pub mod patterns;
pub mod strategies;
pub mod radar;

use patterns::{Candle, PatternEngine};
use strategies::{IndicatorSnapshot, StrategyEngine};

// ── Indicator State ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct IndicatorState {
    pub sma_50: f64,
    pub sma_200: f64,
    pub prev_sma_50: f64,
    pub prev_sma_200: f64,
    pub macd_histogram: f64,
    pub parabolic_sar: f64,
    pub rsi_14: f64,
    pub stoch_k: f64,
    pub bb_upper: f64,
    pub bb_lower: f64,
    pub atr_20_ma: f64,
    pub obv_current: f64,
    pub obv_previous: f64,
    pub cmf: f64,
    pub vwap: f64,
    pub average_volume: f64,
    pub orb_high: f64,
    pub orb_low: f64,
}

impl IndicatorState {
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

    /// Build a default state with NaN for all optional indicators.
    /// Used when we only have candle data and basic SMAs.
    pub fn from_candles_basic(candles: &[Candle]) -> Self {
        let (sma_50, sma_200) = Self::compute_smas(candles);
        let avg_vol = Self::compute_avg_volume(candles, 20);

        IndicatorState {
            sma_50,
            sma_200,
            prev_sma_50: f64::NAN,
            prev_sma_200: f64::NAN,
            macd_histogram: f64::NAN,
            parabolic_sar: f64::NAN,
            rsi_14: Self::compute_rsi(candles, 14),
            stoch_k: f64::NAN,
            bb_upper: f64::NAN,
            bb_lower: f64::NAN,
            atr_20_ma: f64::NAN,
            obv_current: f64::NAN,
            obv_previous: f64::NAN,
            cmf: f64::NAN,
            vwap: f64::NAN,
            average_volume: avg_vol,
            orb_high: f64::NAN,
            orb_low: f64::NAN,
        }
    }

    fn compute_smas(candles: &[Candle]) -> (f64, f64) {
        let sma = |n: usize| -> f64 {
            if candles.len() < n { return f64::NAN; }
            let slice = &candles[candles.len() - n..];
            slice.iter().map(|c| c.close).sum::<f64>() / n as f64
        };
        (sma(50), sma(200))
    }

    fn compute_avg_volume(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period { return 0.0; }
        let slice = &candles[candles.len() - period..];
        slice.iter().map(|c| c.volume).sum::<f64>() / period as f64
    }

    fn compute_rsi(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period + 1 { return f64::NAN; }
        let slice = &candles[candles.len() - period - 1..];
        let mut gains = 0.0_f64;
        let mut losses = 0.0_f64;
        for i in 1..slice.len() {
            let delta = slice[i].close - slice[i - 1].close;
            if delta > 0.0 { gains += delta; } else { losses -= delta; }
        }
        let avg_gain = gains / period as f64;
        let avg_loss = losses / period as f64;
        if avg_loss < 1e-12 { return 100.0; }
        let rs = avg_gain / avg_loss;
        100.0 - (100.0 / (1.0 + rs))
    }
}

// ── Consensus Report ────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ConsensusReport {
    pub symbol: String,
    pub trend_score: i32,
    pub momentum_state: String,
    pub volatility_state: String,
    pub volume_flow_state: String,
    pub active_patterns: Vec<String>,
    pub active_strategies: Vec<String>,
}

// ── AI Execution Plan ───────────────────────────────────────────────────────

/// The final structured payload returned by DeepSeek and sent to the React UI.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct AiExecutionPlan {
    /// Conviction score (1–100) indicating the LLM's confidence in the setup.
    pub conviction_score: i32,
    /// Narrative explaining why the setup is valid or a trap.
    pub setup_validation: String,
    /// Actionable trade plan: entry, stop-loss, and target levels.
    pub execution_plan: String,
}

// ── Scoring Constants ───────────────────────────────────────────────────────

const TREND_WEIGHT: i32 = 25;
const RSI_OB: f64 = 70.0;
const RSI_OS: f64 = 30.0;
const STOCH_OB: f64 = 80.0;
const STOCH_OS: f64 = 20.0;
const CMF_ACC: f64 = 0.05;
const CMF_DIST: f64 = -0.05;

// ── Consensus Engine ────────────────────────────────────────────────────────

pub struct ConsensusEngine;

impl ConsensusEngine {
    pub fn compile_consensus(
        symbol: &str,
        candles: &[Candle],
        indicators: &IndicatorState,
    ) -> ConsensusReport {
        let active_patterns = PatternEngine::analyze(candles);
        let snapshot = indicators.to_snapshot();
        let active_strategies = StrategyEngine::evaluate(candles, &snapshot);
        let close = candles.last().map(|c| c.close).unwrap_or(0.0);

        ConsensusReport {
            symbol: symbol.to_string(),
            trend_score: Self::trend_score(close, indicators),
            momentum_state: Self::momentum(indicators),
            volatility_state: Self::volatility(candles, indicators),
            volume_flow_state: Self::volume_flow(indicators),
            active_patterns,
            active_strategies,
        }
    }

    fn trend_score(close: f64, ind: &IndicatorState) -> i32 {
        let mut s: i32 = 0;
        if ind.sma_50.is_finite() {
            s += if close > ind.sma_50 { TREND_WEIGHT } else { -TREND_WEIGHT };
        }
        if ind.sma_200.is_finite() {
            s += if close > ind.sma_200 { TREND_WEIGHT } else { -TREND_WEIGHT };
        }
        if ind.macd_histogram.is_finite() {
            s += if ind.macd_histogram > 0.0 { TREND_WEIGHT } else { -TREND_WEIGHT };
        }
        if ind.parabolic_sar.is_finite() {
            s += if ind.parabolic_sar < close { TREND_WEIGHT } else { -TREND_WEIGHT };
        }
        s.clamp(-100, 100)
    }

    fn momentum(ind: &IndicatorState) -> String {
        let ob = (ind.rsi_14.is_finite() && ind.rsi_14 > RSI_OB)
            || (ind.stoch_k.is_finite() && ind.stoch_k > STOCH_OB);
        let os = (ind.rsi_14.is_finite() && ind.rsi_14 < RSI_OS)
            || (ind.stoch_k.is_finite() && ind.stoch_k < STOCH_OS);
        if ob { "OVERBOUGHT".into() } else if os { "OVERSOLD".into() } else { "NEUTRAL".into() }
    }

    fn volatility(candles: &[Candle], ind: &IndicatorState) -> String {
        if !ind.bb_upper.is_finite() || !ind.bb_lower.is_finite() || !ind.atr_20_ma.is_finite() {
            return "NORMAL".into();
        }
        if let Some(c) = candles.last() {
            if c.high > ind.bb_upper || c.low < ind.bb_lower {
                return "EXPANDING".into();
            }
        }
        if (ind.bb_upper - ind.bb_lower) < ind.atr_20_ma { "SQUEEZING".into() } else { "NORMAL".into() }
    }

    fn volume_flow(ind: &IndicatorState) -> String {
        let rising = ind.obv_current.is_finite() && ind.obv_previous.is_finite()
            && ind.obv_current > ind.obv_previous;
        let falling = ind.obv_current.is_finite() && ind.obv_previous.is_finite()
            && ind.obv_current < ind.obv_previous;
        if ind.cmf.is_finite() && ind.cmf > CMF_ACC && rising { "ACCUMULATION".into() }
        else if ind.cmf.is_finite() && ind.cmf < CMF_DIST && falling { "DISTRIBUTION".into() }
        else { "NEUTRAL".into() }
    }
}
