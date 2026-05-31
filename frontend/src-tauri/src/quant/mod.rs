// quant/mod.rs — V3 Consensus Engine (Tauri-local).
//
// Full indicator scoring matrix + consensus compilation.
// Mirrors aggregator/src/quant/mod.rs for in-process Tauri execution.

pub mod patterns;
pub mod strategies;
pub mod radar;
pub mod scanner;
pub mod vwepr;
pub mod predictive;
pub mod tool_server;

use patterns::{Candle, PatternEngine};
use strategies::{IndicatorSnapshot, StrategyEngine};
use vwepr::OhlcCandle;

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
    // ── RAG Context Injection fields (V3 Phase 4) ──────────────────────
    pub ema_9: f64,
    pub ema_21: f64,
    pub macd_line: f64,
    pub macd_signal: f64,
    // ── Institutional RAG Expansion (V3 Phase 5) ─────────────────────
    pub atr_14: f64,
    pub bb_mid: f64,
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
        let (prev_sma_50, prev_sma_200) = if candles.len() > 1 {
            Self::compute_smas(&candles[..candles.len() - 1])
        } else {
            (f64::NAN, f64::NAN)
        };
        let avg_vol = Self::compute_avg_volume(candles, 20);

        let ema_9 = Self::compute_ema(candles, 9);
        let ema_21 = Self::compute_ema(candles, 21);
        let (macd_line, macd_signal) = Self::compute_macd(candles);
        let atr_14 = Self::compute_atr(candles, 14);
        let (bb_upper, bb_mid, bb_lower) = Self::compute_bollinger_bands(candles, 20, 2.0);
        let vwap = Self::compute_vwap(candles);
        let (orb_high, orb_low) = Self::compute_orb(candles, 15);
        let parabolic_sar = Self::compute_parabolic_sar(candles);
        let stoch_k = Self::compute_stoch_k(candles, 14);
        let (obv_current, obv_previous) = Self::compute_obv(candles);
        let cmf = Self::compute_cmf(candles, 20);

        IndicatorState {
            sma_50,
            sma_200,
            prev_sma_50,
            prev_sma_200,
            macd_histogram: if macd_line.is_finite() && macd_signal.is_finite() {
                macd_line - macd_signal
            } else {
                f64::NAN
            },
            parabolic_sar,
            rsi_14: Self::compute_rsi(candles, 14),
            stoch_k,
            bb_upper,
            bb_lower,
            atr_20_ma: atr_14,  // reuse for volatility scoring
            obv_current,
            obv_previous,
            cmf,
            vwap,
            average_volume: avg_vol,
            orb_high,
            orb_low,
            ema_9,
            ema_21,
            macd_line,
            macd_signal,
            atr_14,
            bb_mid,
        }
    }

    fn compute_orb(candles: &[Candle], period: usize) -> (f64, f64) {
        if candles.is_empty() { return (f64::NAN, f64::NAN); }
        let limit = period.min(candles.len());
        let slice = &candles[..limit];
        let mut high = f64::MIN;
        let mut low = f64::MAX;
        for c in slice {
            if c.high > high { high = c.high; }
            if c.low < low { low = c.low; }
        }
        (high, low)
    }

    fn compute_parabolic_sar(candles: &[Candle]) -> f64 {
        if candles.len() < 3 {
            return f64::NAN;
        }
        let mut uptrend = candles[1].close > candles[0].close;
        let mut sar = if uptrend { candles[0].low } else { candles[0].high };
        let mut ep = if uptrend { candles[1].high } else { candles[1].low };
        let mut af = 0.02;
        for i in 2..candles.len() {
            let next_sar = sar + af * (ep - sar);
            if uptrend {
                let cap = candles[i - 1].low.min(candles[i - 2].low);
                sar = next_sar.min(cap);
                if candles[i].low < sar {
                    uptrend = false;
                    sar = ep;
                    ep = candles[i].low;
                    af = 0.02;
                } else {
                    if candles[i].high > ep {
                        ep = candles[i].high;
                        af = (af + 0.02).min(0.20);
                    }
                }
            } else {
                let cap = candles[i - 1].high.max(candles[i - 2].high);
                sar = next_sar.max(cap);
                if candles[i].high > sar {
                    uptrend = true;
                    sar = ep;
                    ep = candles[i].high;
                    af = 0.02;
                } else {
                    if candles[i].low < ep {
                        ep = candles[i].low;
                        af = (af + 0.02).min(0.20);
                    }
                }
            }
        }
        sar
    }

    fn compute_stoch_k(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period {
            return f64::NAN;
        }
        let slice = &candles[candles.len() - period..];
        let mut highest = f64::MIN;
        let mut lowest = f64::MAX;
        for c in slice {
            if c.high > highest { highest = c.high; }
            if c.low < lowest { lowest = c.low; }
        }
        let range = highest - lowest;
        let current_close = candles.last().map(|c| c.close).unwrap_or(0.0);
        if range > 1e-9 {
            ((current_close - lowest) / range) * 100.0
        } else {
            50.0
        }
    }

    fn compute_obv(candles: &[Candle]) -> (f64, f64) {
        if candles.is_empty() {
            return (0.0, 0.0);
        }
        let mut obv = 0.0;
        let mut obv_history = Vec::with_capacity(candles.len());
        obv_history.push(0.0);
        for i in 1..candles.len() {
            let change = candles[i].close - candles[i - 1].close;
            if change > 0.0 {
                obv += candles[i].volume;
            } else if change < 0.0 {
                obv -= candles[i].volume;
            }
            obv_history.push(obv);
        }
        let obv_current = *obv_history.last().unwrap_or(&0.0);
        let obv_previous = if obv_history.len() > 1 {
            obv_history[obv_history.len() - 2]
        } else {
            0.0
        };
        (obv_current, obv_previous)
    }

    fn compute_cmf(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period {
            return f64::NAN;
        }
        let slice = &candles[candles.len() - period..];
        let mut sum_mfv = 0.0;
        let mut sum_vol = 0.0;
        for c in slice {
            let range = c.high - c.low;
            let mfm = if range > 1e-9 {
                ((c.close - c.low) - (c.high - c.close)) / range
            } else {
                0.0
            };
            sum_mfv += mfm * c.volume;
            sum_vol += c.volume;
        }
        if sum_vol > 1e-9 {
            sum_mfv / sum_vol
        } else {
            0.0
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

    /// Compute Exponential Moving Average for the given period.
    /// Returns f64::NAN if there aren't enough candles.
    pub fn compute_ema(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period {
            return f64::NAN;
        }
        let multiplier = 2.0 / (period as f64 + 1.0);
        // Seed EMA with SMA of first `period` candles
        let sma: f64 = candles[..period].iter().map(|c| c.close).sum::<f64>() / period as f64;
        let mut ema = sma;
        for candle in &candles[period..] {
            ema = (candle.close - ema) * multiplier + ema;
        }
        ema
    }

    /// Compute MACD line (EMA-12 − EMA-26) and signal line (EMA-9 of MACD).
    /// Returns (macd_line, signal_line) — both NAN if insufficient data.
    fn compute_macd(candles: &[Candle]) -> (f64, f64) {
        if candles.len() < 35 {
            // Need at least 26 + 9 periods for a meaningful signal
            return (f64::NAN, f64::NAN);
        }
        let mult_12 = 2.0 / 13.0;
        let mult_26 = 2.0 / 27.0;
        let mult_signal = 2.0 / 10.0;

        // Seed EMAs
        let sma_12: f64 = candles[..12].iter().map(|c| c.close).sum::<f64>() / 12.0;
        let sma_26: f64 = candles[..26].iter().map(|c| c.close).sum::<f64>() / 26.0;

        let mut ema_12 = sma_12;
        let mut ema_26 = sma_26;

        // Build MACD history from candle index 26 onward
        let mut macd_history: Vec<f64> = Vec::with_capacity(candles.len() - 26);

        for (i, candle) in candles.iter().enumerate() {
            if i >= 12 {
                ema_12 = (candle.close - ema_12) * mult_12 + ema_12;
            }
            if i >= 26 {
                ema_26 = (candle.close - ema_26) * mult_26 + ema_26;
                macd_history.push(ema_12 - ema_26);
            }
        }

        if macd_history.len() < 9 {
            return (*macd_history.last().unwrap_or(&f64::NAN), f64::NAN);
        }

        // Signal line = EMA-9 of MACD series
        let signal_sma: f64 = macd_history[..9].iter().sum::<f64>() / 9.0;
        let mut signal = signal_sma;
        for val in &macd_history[9..] {
            signal = (val - signal) * mult_signal + signal;
        }

        let macd_line = *macd_history.last().unwrap();
        (macd_line, signal)
    }

    /// Compute Average True Range over `period` candles.
    /// ATR = SMA of True Range, where TR = max(H-L, |H-prevC|, |L-prevC|).
    fn compute_atr(candles: &[Candle], period: usize) -> f64 {
        if candles.len() < period + 1 {
            return f64::NAN;
        }
        let start = candles.len() - period - 1;
        let slice = &candles[start..];
        let mut tr_sum = 0.0;
        for i in 1..slice.len() {
            let high = slice[i].high;
            let low = slice[i].low;
            let prev_close = slice[i - 1].close;
            let tr = (high - low)
                .max((high - prev_close).abs())
                .max((low - prev_close).abs());
            tr_sum += tr;
        }
        tr_sum / period as f64
    }

    /// Compute Bollinger Bands: (upper, middle, lower).
    /// Middle = SMA(period), Upper/Lower = Middle ± (num_std_dev × σ).
    fn compute_bollinger_bands(candles: &[Candle], period: usize, num_std_dev: f64) -> (f64, f64, f64) {
        if candles.len() < period {
            return (f64::NAN, f64::NAN, f64::NAN);
        }
        let slice = &candles[candles.len() - period..];
        let mean: f64 = slice.iter().map(|c| c.close).sum::<f64>() / period as f64;
        let variance: f64 = slice.iter()
            .map(|c| (c.close - mean).powi(2))
            .sum::<f64>() / period as f64;
        let std_dev = variance.sqrt();
        (mean + num_std_dev * std_dev, mean, mean - num_std_dev * std_dev)
    }

    /// Compute Volume-Weighted Average Price across all candles.
    /// VWAP = Σ(Typical Price × Volume) / Σ(Volume).
    fn compute_vwap(candles: &[Candle]) -> f64 {
        if candles.is_empty() {
            return f64::NAN;
        }
        let mut cumulative_tpv = 0.0;
        let mut cumulative_vol = 0.0;
        for c in candles {
            let typical_price = (c.high + c.low + c.close) / 3.0;
            cumulative_tpv += typical_price * c.volume;
            cumulative_vol += c.volume;
        }
        if cumulative_vol < 1e-12 {
            return f64::NAN;
        }
        cumulative_tpv / cumulative_vol
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
    pub vwepr_value: Option<f64>,
    pub vwepr_slope: Option<f64>,
    pub ols_value: Option<f64>,
    pub ols_slope: Option<f64>,
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
        timeframe: &str,
    ) -> ConsensusReport {
        // Scan the final 15 candles for patterns, deduplicated
        let active_patterns: Vec<String> = {
            use std::collections::HashSet;
            let mut seen = HashSet::new();
            let mut found = Vec::new();
            const PATTERN_SCAN_WINDOW: usize = 15;
            let scan_start = if candles.len() > PATTERN_SCAN_WINDOW {
                candles.len() - PATTERN_SCAN_WINDOW
            } else {
                0
            };
            for end in (scan_start + 1)..=candles.len() {
                let window = &candles[..end];
                for p in PatternEngine::analyze(window) {
                    if seen.insert(p.clone()) {
                        found.push(p);
                    }
                }
            }
            found
        };

        // Scan the final 15 candles for strategies, deduplicated
        let active_strategies: Vec<String> = {
            use std::collections::HashSet;
            let mut seen = HashSet::new();
            let mut found = Vec::new();
            const STRATEGY_SCAN_WINDOW: usize = 15;
            let scan_start = if candles.len() > STRATEGY_SCAN_WINDOW {
                candles.len() - STRATEGY_SCAN_WINDOW
            } else {
                0
            };
            for i in scan_start..candles.len() {
                let window = &candles[..=i];
                let snapshot = if i == candles.len() - 1 {
                    indicators.to_snapshot()
                } else {
                    IndicatorState::from_candles_basic(window).to_snapshot()
                };
                for s in StrategyEngine::evaluate(window, &snapshot) {
                    if seen.insert(s.clone()) {
                        found.push(s);
                    }
                }
            }
            found
        };

        let close = candles.last().map(|c| c.close).unwrap_or(0.0);

        // VWEPR & OLS Calculations
        let interval_sec: i64 = match timeframe {
            "1m"  => 60,
            "3m"  => 180,
            "5m"  => 300,
            "10m" => 600,
            "15m" => 900,
            "30m" => 1_800,
            "60m" | "1h" => 3_600,
            "4h"  => 14_400,
            "1d"  => 86_400,
            _     => 600,
        };

        let ohlc_candles: Vec<OhlcCandle> = candles
            .iter()
            .enumerate()
            .map(|(i, c)| OhlcCandle {
                time:   i as i64 * interval_sec,
                open:   c.open,
                high:   c.high,
                low:    c.low,
                close:  c.close,
                volume: c.volume,
            })
            .collect();

        let (ols_value, ols_slope, vwepr_value, vwepr_slope) = if !ohlc_candles.is_empty() {
            let proj = predictive::calculate_dual_projection(&ohlc_candles, 1, interval_sec);
            let o_val = proj.linear_points.get(1).map(|p| p.value);
            let o_slope = if proj.linear_points.len() >= 2 {
                Some(proj.linear_points[1].value - proj.linear_points[0].value)
            } else {
                None
            };
            let v_val = proj.curved_points.get(1).map(|p| p.value);
            let v_slope = if proj.curved_points.len() >= 2 {
                Some(proj.curved_points[1].value - proj.curved_points[0].value)
            } else {
                None
            };
            (o_val, o_slope, v_val, v_slope)
        } else {
            (None, None, None, None)
        };

        ConsensusReport {
            symbol: symbol.to_string(),
            trend_score: Self::trend_score(close, indicators),
            momentum_state: Self::momentum(indicators),
            volatility_state: Self::volatility(candles, indicators),
            volume_flow_state: Self::volume_flow(indicators),
            active_patterns,
            active_strategies,
            vwepr_value,
            vwepr_slope,
            ols_value,
            ols_slope,
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
