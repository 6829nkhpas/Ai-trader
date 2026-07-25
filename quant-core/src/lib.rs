// quant-core/src/lib.rs — V3 Consensus Engine (Tauri-free shared crate).
//
// Full indicator scoring matrix + consensus compilation. Extracted verbatim
// from the Tauri desktop crate's `quant` module so BOTH the desktop app and the
// standalone `tool-server` binary share one source of truth for the quant logic.
// This crate has NO Tauri / GUI dependency (the desktop-only `radar` and
// `tool_server` modules remain in the Tauri crate).

pub mod patterns;
pub mod chart_patterns;
pub mod strategies;
pub mod scanner;
pub mod vwepr;
pub mod predictive;

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

// ── Shared Timeframe Validator (R4.5) ────────────────────────────────────────

/// The canonical set of timeframes the deep-quant Tool_Server accepts.
/// Kept in sync with the `interval_sec` mapping used by the Consensus Engine.
pub const SUPPORTED_TIMEFRAMES: &[&str] =
    &["1m", "3m", "5m", "10m", "15m", "30m", "60m", "1h", "4h", "1d"];

/// Error returned when a tool receives a timeframe outside the supported set.
/// Carries the offending value and the supported list so callers can surface a
/// descriptive, actionable message to the agent (R4.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimeframeError {
    /// The unsupported timeframe value that was supplied.
    pub timeframe: String,
    /// The set of timeframes that are accepted.
    pub supported: Vec<String>,
}

impl std::fmt::Display for TimeframeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "unsupported timeframe '{}'; supported timeframes are: {}",
            self.timeframe,
            self.supported.join(", ")
        )
    }
}

impl std::error::Error for TimeframeError {}

/// Validate a timeframe string against the supported set (R4.5).
///
/// On success returns `Ok(())`. On failure returns a [`TimeframeError`] whose
/// message names the offending timeframe and lists the supported values, and
/// logs the validation failure so it is observable in the server logs.
/// Matching is case-insensitive and ignores surrounding whitespace.
pub fn validate_timeframe(tf: &str) -> Result<(), TimeframeError> {
    let normalized = tf.trim();
    if SUPPORTED_TIMEFRAMES
        .iter()
        .any(|s| s.eq_ignore_ascii_case(normalized))
    {
        Ok(())
    } else {
        let err = TimeframeError {
            timeframe: tf.to_string(),
            supported: SUPPORTED_TIMEFRAMES.iter().map(|s| s.to_string()).collect(),
        };
        log::error!("timeframe validation failed: {}", err);
        Err(err)
    }
}

// ── Trade_Validator (pure module — R6.1–R6.5) ────────────────────────────────

/// The directional intent of a declared trade.
///
/// `Buy`/`Sell` are directional and subject to the full set of Trade_Validator
/// checks. `Hold` is an abstention and bypasses all level-based checks (R6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Buy,
    Sell,
    Hold,
}

impl Action {
    /// Parse an action from a free-form string (case-insensitive, whitespace
    /// tolerant). Anything that is not BUY or SELL maps to `Hold` so that an
    /// unrecognized/empty action conservatively abstains rather than trading.
    pub fn from_str_lenient(s: &str) -> Action {
        match s.trim().to_ascii_uppercase().as_str() {
            "BUY" => Action::Buy,
            "SELL" => Action::Sell,
            _ => Action::Hold,
        }
    }
}

/// The execution levels for a proposed/declared trade.
///
/// A complete set of all three prices is required for a BUY/SELL declaration
/// (R6.1). The presence of the struct itself indicates the levels were
/// supplied; finiteness is validated inside [`validate_trade`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ExecutionLevels {
    pub entry: f64,
    pub stop_loss: f64,
    pub take_profit: f64,
}

/// Why a Trade_Validator check failed. Each variant maps to a requirement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidatorReason {
    /// One or more of entry/stop-loss/take-profit was missing or non-finite (R6.1).
    MissingLevels,
    /// `reward / risk` is below the 1:2 minimum (R6.2).
    RiskRewardTooLow,
    /// Stop-loss distance from entry is smaller than `1.5 × ATR` (R6.3).
    StopTooTight,
    /// Level ordering is inconsistent with the trade direction (R6.4 / R6.5).
    DirectionInconsistent,
    // ── Multi-leg Management_Plan checks (trade-management R5.1–R5.4) ──────────
    /// A scale-out leg fraction is not in `(0.0, 1.0]`, or the leg fractions sum
    /// to more than `1.0` (R5.1). Mirrors Python `LEG_FRACTION_OUT_OF_RANGE`.
    LegFractionOutOfRange,
    /// The scale-out targets are inconsistent with the trade direction: for a
    /// BUY every target must be strictly greater than entry and the targets
    /// non-decreasing (mirror-image for SELL) (R5.2). Mirrors Python
    /// `TARGET_ORDERING_INCONSISTENT`.
    TargetOrderingInconsistent,
    /// The breakeven trigger is not strictly between entry and the first
    /// scale-out target on the trade's profit side (R5.3). Mirrors Python
    /// `BREAKEVEN_OUT_OF_RANGE`.
    BreakevenOutOfRange,
    /// The fraction-weighted blended reward-to-risk is below the configured
    /// minimum (R5.4). Mirrors Python `BLENDED_RR_TOO_LOW`.
    BlendedRrTooLow,
}

impl ValidatorReason {
    /// A stable machine-readable tag for the reason, suitable for surfacing to
    /// the agent / serializing in a tool result.
    pub fn as_tag(&self) -> &'static str {
        match self {
            ValidatorReason::MissingLevels => "missing-levels",
            ValidatorReason::RiskRewardTooLow => "risk-reward-too-low",
            ValidatorReason::StopTooTight => "stop-too-tight",
            ValidatorReason::DirectionInconsistent => "direction-inconsistent",
            ValidatorReason::LegFractionOutOfRange => "leg-fraction-out-of-range",
            ValidatorReason::TargetOrderingInconsistent => "target-ordering-inconsistent",
            ValidatorReason::BreakevenOutOfRange => "breakeven-out-of-range",
            ValidatorReason::BlendedRrTooLow => "blended-rr-too-low",
        }
    }
}

impl std::fmt::Display for ValidatorReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let msg = match self {
            ValidatorReason::MissingLevels => {
                "missing execution levels: entry, stop-loss, and take-profit are all required"
            }
            ValidatorReason::RiskRewardTooLow => {
                "risk-reward ratio below the 1:2 minimum"
            }
            ValidatorReason::StopTooTight => {
                "stop-loss is tighter than 1.5x ATR"
            }
            ValidatorReason::DirectionInconsistent => {
                "execution levels are inconsistent with the trade direction"
            }
            ValidatorReason::LegFractionOutOfRange => {
                "a scale-out leg fraction is not in (0.0, 1.0] or the fractions sum to more than 1.0"
            }
            ValidatorReason::TargetOrderingInconsistent => {
                "scale-out targets are inconsistent with the trade direction or not monotonically ordered"
            }
            ValidatorReason::BreakevenOutOfRange => {
                "breakeven trigger is not strictly between entry and the first target on the profit side"
            }
            ValidatorReason::BlendedRrTooLow => {
                "blended reward-to-risk is below the configured minimum"
            }
        };
        write!(f, "{}", msg)
    }
}

/// The outcome of validating a declared trade.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ValidatorOutcome {
    /// All applicable checks passed. Carries the computed Risk_Reward_Ratio.
    /// For a HOLD this is `0.0` (no levels to evaluate).
    Pass { risk_reward: f64 },
    /// A check failed; the trade must not be committed (R6.6).
    Fail { reason: ValidatorReason },
}

impl ValidatorOutcome {
    /// Convenience: did the trade pass all checks?
    pub fn is_pass(&self) -> bool {
        matches!(self, ValidatorOutcome::Pass { .. })
    }
}

/// The minimum acceptable Risk_Reward_Ratio (reward / risk) for the SWING /
/// INVESTOR / F&O profiles (and the safe default for any unknown profile). A
/// value exactly at the boundary (2.0) passes; below 2.0 fails (R6.2).
pub const MIN_RISK_REWARD: f64 = 2.0;

/// The minimum acceptable Risk_Reward_Ratio for the INTRADAY profile. Intraday
/// index/equity ranges are frequently too tight for an 80–100 pt (1:2) target
/// to fit inside the session structure, so a swing-calibrated 1:2 floor makes a
/// defensible intraday bracket mathematically impossible and forces perpetual
/// HOLDs. The intraday floor is relaxed to 1:1.3 — still a positive-expectancy
/// asymmetry given the high win-rate of intraday mean-reversion/continuation
/// scalps — while the stop-distance floor (`MIN_STOP_ATR_MULTIPLE`) and every
/// other hard rule stay UNCHANGED for all profiles. A value exactly at 1.3
/// passes; below fails.
pub const MIN_RISK_REWARD_INTRADAY: f64 = 1.3;

/// The minimum stop-loss distance expressed as a multiple of ATR. A stop
/// distance exactly at `1.5 × ATR` passes; below fails (R6.3).
pub const MIN_STOP_ATR_MULTIPLE: f64 = 1.5;

/// Resolve the minimum Risk_Reward_Ratio for a workspace `profile` (case- and
/// whitespace-insensitive). Returns `MIN_RISK_REWARD_INTRADAY` (1.5) for the
/// INTRADAY profile and `MIN_RISK_REWARD` (2.0) for SWING / INVESTOR / FNO and
/// for any unset / unrecognized profile — the safe default. Total; never panics.
pub fn min_risk_reward_for_profile(profile: Option<&str>) -> f64 {
    match profile {
        Some(p) if p.trim().eq_ignore_ascii_case("INTRADAY") => MIN_RISK_REWARD_INTRADAY,
        _ => MIN_RISK_REWARD,
    }
}

/// Validate a proposed/declared trade against the hard risk rules (R6.1–R6.5)
/// using the default (2.0) Risk_Reward floor. Preserved as the stable public API
/// (and for every existing caller/test); delegates to
/// [`validate_trade_with_min_rr`] with [`MIN_RISK_REWARD`].
pub fn validate_trade(
    action: Action,
    levels: Option<ExecutionLevels>,
    atr_14: Option<f64>,
) -> ValidatorOutcome {
    validate_trade_with_min_rr(action, levels, atr_14, MIN_RISK_REWARD)
}

/// Validate a proposed/declared trade against the hard risk rules (R6.1–R6.5)
/// with a caller-supplied minimum Risk_Reward floor (resolved per workspace
/// profile via [`min_risk_reward_for_profile`]).
///
/// `HOLD` bypasses all level checks and always passes with a `risk_reward` of
/// `0.0`. For `BUY`/`SELL` the checks are applied in this order:
///
/// 1. **MissingLevels (R6.1)** — `levels` must be `Some` and every price must
///    be finite.
/// 2. **DirectionInconsistent (R6.4/R6.5)** — BUY requires
///    `stop_loss < entry < take_profit`; SELL requires
///    `take_profit < entry < stop_loss`.
/// 3. **StopTooTight (R6.3)** — when `atr_14` is available and finite, the stop
///    distance `|entry − stop_loss|` must be at least `1.5 × atr_14` (this
///    volatility floor is profile-INDEPENDENT and never relaxed).
/// 4. **RiskRewardTooLow (R6.2)** — `|take_profit − entry| / |entry − stop_loss|`
///    must be at least `min_risk_reward`.
///
/// The function is pure: identical inputs always yield an identical outcome.
pub fn validate_trade_with_min_rr(
    action: Action,
    levels: Option<ExecutionLevels>,
    atr_14: Option<f64>,
    min_risk_reward: f64,
) -> ValidatorOutcome {
    // HOLD abstains — no execution levels to check (R6).
    if action == Action::Hold {
        return ValidatorOutcome::Pass { risk_reward: 0.0 };
    }

    // R6.1 — all three levels must be present and finite.
    let levels = match levels {
        Some(l) if l.entry.is_finite() && l.stop_loss.is_finite() && l.take_profit.is_finite() => l,
        _ => return ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels },
    };

    let ExecutionLevels { entry, stop_loss, take_profit } = levels;

    // R6.4 / R6.5 — level ordering must match the trade direction.
    let direction_ok = match action {
        Action::Buy => stop_loss < entry && entry < take_profit,
        Action::Sell => take_profit < entry && entry < stop_loss,
        Action::Hold => unreachable!("HOLD handled above"),
    };
    if !direction_ok {
        return ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent };
    }

    let risk = (entry - stop_loss).abs();
    let reward = (take_profit - entry).abs();

    // Direction consistency guarantees a non-zero risk, but guard anyway so a
    // degenerate stop == entry can never divide by zero.
    if risk <= 0.0 {
        return ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent };
    }

    // R6.3 — stop must not be tighter than 1.5x ATR (only when ATR is known).
    // This volatility floor is profile-independent and is never relaxed.
    if let Some(atr) = atr_14 {
        if atr.is_finite() && atr > 0.0 && risk < MIN_STOP_ATR_MULTIPLE * atr {
            return ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight };
        }
    }

    // R6.2 — risk-reward must meet the profile-resolved minimum (boundary passes).
    let risk_reward = reward / risk;
    if risk_reward < min_risk_reward {
        return ValidatorOutcome::Fail { reason: ValidatorReason::RiskRewardTooLow };
    }

    ValidatorOutcome::Pass { risk_reward }
}

// ── Multi-leg Management_Plan validation (trade-management R5.1–R5.6) ─────────

/// A single scale-out leg: a partial-exit target price and the size fraction of
/// the position closed at that target. Mirrors the Python `ScaleOutLeg`.
#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ScaleOutLeg {
    /// The target price for this partial exit.
    pub target: f64,
    /// The size fraction closed at this target — must be in `(0.0, 1.0]` (R5.1).
    pub fraction: f64,
}

/// The breakeven trigger of a Management_Plan, expressed as **either** a price
/// **or** an R-multiple of progress from entry toward the first target (R1.4).
/// When both are present the explicit `price` takes precedence.
#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct BreakevenTrigger {
    /// An explicit breakeven price.
    pub price: Option<f64>,
    /// Or an R-multiple of progress from entry toward the first target.
    pub r_multiple: Option<f64>,
}

/// An optional trailing-stop rule. It carries no level-ordering constraint, so
/// it is **not** validated here; it is part of the plan only so the structure
/// round-trips. Mirrors the Python `TrailingStop`.
#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TrailingStop {
    /// Trail by N × ATR.
    pub atr_multiple: Option<f64>,
    /// Or trail by a fixed R increment.
    pub r_increment: Option<f64>,
}

/// A multi-leg Management_Plan: an entry, an initial stop, an ordered list of
/// one or more scale-out legs, an optional breakeven trigger, and an optional
/// trailing-stop rule. This is the validation-facing mirror of the Python
/// `ManagementPlan` (the `action` and `atr_14` are passed to
/// [`validate_management_plan`] alongside, mirroring [`validate_trade`]).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ManagementPlan {
    /// The entry price.
    pub entry: f64,
    /// The initial stop-loss price.
    pub initial_stop: f64,
    /// One or more scale-out legs, in declared order (R1.1).
    pub legs: Vec<ScaleOutLeg>,
    /// Optional breakeven trigger (R1.4).
    pub breakeven: Option<BreakevenTrigger>,
    /// Optional trailing-stop rule (not validated — see [`TrailingStop`]).
    pub trailing: Option<TrailingStop>,
}

/// The minimum acceptable blended (fraction-weighted) reward-to-risk for a
/// Management_Plan. Mirrors the documented default for
/// `TM_MIN_BLENDED_REWARD_TO_RISK` (R5.4); a value exactly at the boundary
/// passes, below it fails. Callers that resolve the parameter from the
/// environment pass their resolved value to [`validate_management_plan`].
pub const MIN_BLENDED_REWARD_TO_RISK: f64 = 2.0;

/// Validate a multi-leg Management_Plan against the hard risk rules **plus** the
/// multi-leg consistency checks (trade-management R5.1–R5.6), mirroring the
/// Python `validator.py` so the two implementations agree on identical inputs.
///
/// The plan is an **optional** layer on top of [`validate_trade`]: callers that
/// do not supply a plan keep using `validate_trade` unchanged. `HOLD` bypasses
/// every plan check exactly as it bypasses the level checks (R5.5), and the
/// multi-leg checks only **add** to the existing hard rules — they never relax
/// them (R14.2).
///
/// For a `BUY`/`SELL` the checks are applied in this order:
///
/// 1. **MissingLevels (R6.1)** — `entry`/`initial_stop` must be finite, there
///    must be at least one leg, and every leg target/fraction must be finite.
/// 2. **LegFractionOutOfRange (R5.1)** — every leg fraction must lie in
///    `(0.0, 1.0]` and the fractions must sum to at most `1.0`.
/// 3. **TargetOrderingInconsistent (R5.2)** — BUY requires
///    `initial_stop < entry`, every target strictly greater than entry, and the
///    targets non-decreasing; SELL is the mirror image (`initial_stop > entry`,
///    every target strictly less than entry, non-increasing).
/// 4. **StopTooTight (R6.3)** — when `atr_14` is known/finite/positive the stop
///    distance `|entry − initial_stop|` must be at least `1.5 × atr_14`.
/// 5. **BreakevenOutOfRange (R5.3)** — when a breakeven trigger is present it
///    must resolve to a price strictly between entry and the first leg's target
///    on the profit side.
/// 6. **BlendedRrTooLow (R5.4)** — the fraction-weighted target distance from
///    entry over the initial stop distance must be at least
///    `min_blended_reward_to_risk`.
///
/// On success the returned `risk_reward` is the blended (fraction-weighted)
/// reward-to-risk. The function is pure: identical inputs always yield an
/// identical outcome.
pub fn validate_management_plan(
    action: Action,
    plan: &ManagementPlan,
    atr_14: Option<f64>,
    min_blended_reward_to_risk: f64,
) -> ValidatorOutcome {
    // HOLD abstains — no plan checks at all (R5.5).
    if action == Action::Hold {
        return ValidatorOutcome::Pass { risk_reward: 0.0 };
    }

    let entry = plan.entry;
    let initial_stop = plan.initial_stop;

    // R6.1 — base levels present and finite, at least one finite leg.
    if !entry.is_finite() || !initial_stop.is_finite() || plan.legs.is_empty() {
        return ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels };
    }
    if plan
        .legs
        .iter()
        .any(|l| !l.target.is_finite() || !l.fraction.is_finite())
    {
        return ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels };
    }

    // R5.1 — every leg fraction in (0.0, 1.0] and the fractions sum to <= 1.0.
    let mut fraction_sum = 0.0_f64;
    for leg in &plan.legs {
        if !(leg.fraction > 0.0 && leg.fraction <= 1.0) {
            return ValidatorOutcome::Fail { reason: ValidatorReason::LegFractionOutOfRange };
        }
        fraction_sum += leg.fraction;
    }
    // Tolerate float summation drift so an exact 1.0 sum is not spuriously rejected.
    if fraction_sum > 1.0 + 1e-9 {
        return ValidatorOutcome::Fail { reason: ValidatorReason::LegFractionOutOfRange };
    }

    // R5.2 — direction + target ordering. BUY: stop < entry, targets strictly
    // above entry and non-decreasing; SELL is the mirror image.
    let direction_ok = match action {
        Action::Buy => initial_stop < entry,
        Action::Sell => initial_stop > entry,
        Action::Hold => unreachable!("HOLD handled above"),
    };
    if !direction_ok {
        return ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent };
    }
    let mut prev_target: Option<f64> = None;
    for leg in &plan.legs {
        let target_ok = match action {
            Action::Buy => leg.target > entry,
            Action::Sell => leg.target < entry,
            Action::Hold => unreachable!("HOLD handled above"),
        };
        if !target_ok {
            return ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent };
        }
        if let Some(prev) = prev_target {
            let monotone_ok = match action {
                Action::Buy => leg.target >= prev,   // non-decreasing
                Action::Sell => leg.target <= prev,  // non-increasing
                Action::Hold => unreachable!("HOLD handled above"),
            };
            if !monotone_ok {
                return ValidatorOutcome::Fail {
                    reason: ValidatorReason::TargetOrderingInconsistent,
                };
            }
        }
        prev_target = Some(leg.target);
    }

    let risk = (entry - initial_stop).abs();
    // Direction consistency guarantees a non-zero risk, but guard anyway so a
    // degenerate stop == entry can never divide by zero.
    if risk <= 0.0 {
        return ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent };
    }

    // R6.3 — stop must not be tighter than 1.5x ATR (only when ATR is known).
    if let Some(atr) = atr_14 {
        if atr.is_finite() && atr > 0.0 && risk < MIN_STOP_ATR_MULTIPLE * atr {
            return ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight };
        }
    }

    // R5.3 — breakeven (when present) strictly between entry and the first
    // target on the profit side. A breakeven expressed as an R-multiple is
    // converted to a price first (entry advanced toward the target by R × risk).
    if let Some(be) = plan.breakeven {
        let first_target = plan.legs[0].target;
        let be_price = match (be.price, be.r_multiple) {
            (Some(p), _) if p.is_finite() => Some(p),
            (_, Some(r)) if r.is_finite() => {
                let dir = match action {
                    Action::Buy => 1.0,
                    Action::Sell => -1.0,
                    Action::Hold => unreachable!("HOLD handled above"),
                };
                Some(entry + dir * r * risk)
            }
            _ => None,
        };
        let be_ok = match be_price {
            Some(p) => match action {
                Action::Buy => entry < p && p < first_target,
                Action::Sell => first_target < p && p < entry,
                Action::Hold => unreachable!("HOLD handled above"),
            },
            None => false,
        };
        if !be_ok {
            return ValidatorOutcome::Fail { reason: ValidatorReason::BreakevenOutOfRange };
        }
    }

    // R5.4 — blended reward-to-risk: the fraction-weighted target distance from
    // entry over the initial stop distance must meet the configured minimum.
    let blended_reward: f64 = plan
        .legs
        .iter()
        .map(|l| l.fraction * (l.target - entry).abs())
        .sum();
    let blended_rr = blended_reward / risk;
    if blended_rr < min_blended_reward_to_risk {
        return ValidatorOutcome::Fail { reason: ValidatorReason::BlendedRrTooLow };
    }

    ValidatorOutcome::Pass { risk_reward: blended_rr }
}

// ── SR_Engine (pure module — R9.1–R9.4) ──────────────────────────────────────

/// Authoritative support/resistance levels computed from the shared candle
/// source (R9.1).
///
/// Classic floor-trader pivot formulas derive the central `pivot` plus the
/// three support bands (`s1`–`s3`) and three resistance bands (`r1`–`r3`) from
/// the most recent completed period. Under well-formed candle data these
/// satisfy `s3 ≤ s2 ≤ s1 ≤ pivot ≤ r1 ≤ r2 ≤ r3`; when the data forces a
/// violation the computed levels are still returned and the breach is reported
/// via [`SrLevels::ordering_exception`] (R9.2). Intraday timeframes
/// additionally carry the opening-range high/low and a daily macro pivot
/// (R9.3).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SrLevels {
    /// The central pivot point: `(high + low + close) / 3` of the prior period.
    pub pivot: f64,
    /// First support level.
    pub s1: f64,
    /// Second support level.
    pub s2: f64,
    /// Third support level.
    pub s3: f64,
    /// First resistance level.
    pub r1: f64,
    /// Second resistance level.
    pub r2: f64,
    /// Third resistance level.
    pub r3: f64,
    /// Aggregate high over the supplied candle window (informational context;
    /// not a pivot input).
    pub recent_high: f64,
    /// Aggregate low over the supplied candle window (informational context;
    /// not a pivot input).
    pub recent_low: f64,
    /// Opening-range high — populated for intraday timeframes only (R9.3).
    pub opening_range_high: Option<f64>,
    /// Opening-range low — populated for intraday timeframes only (R9.3).
    pub opening_range_low: Option<f64>,
    /// Daily macro pivot — populated for intraday timeframes only (R9.3).
    pub daily_pivot: Option<f64>,
    /// Set when the computed levels cannot satisfy the canonical ordering
    /// `s3 ≤ s2 ≤ s1 ≤ pivot ≤ r1 ≤ r2 ≤ r3` (R9.2). `None` when the ordering
    /// holds.
    pub ordering_exception: Option<String>,
}

/// Number of leading candles that define the intraday opening range.
///
/// Mirrors the opening-range-breakout window used elsewhere in the engine
/// (`IndicatorState::compute_orb`). Bounded by the available candle count.
pub const OPENING_RANGE_CANDLES: usize = 15;

/// Returns true for any supported timeframe shorter than one day (R9.3).
///
/// Matching is case-insensitive and whitespace tolerant, mirroring
/// [`validate_timeframe`]. Only `"1d"` is treated as non-intraday (daily).
pub fn is_intraday(timeframe: &str) -> bool {
    !timeframe.trim().eq_ignore_ascii_case("1d")
}

/// Compute authoritative support/resistance levels from a candle window (R9).
///
/// The function is **pure**: identical `candles` and `timeframe` always yield an
/// identical [`SrLevels`] (R9.4). It performs no I/O, uses no clock/RNG, and
/// reads no ambient state — callers resolve the candle window from the shared
/// `load_candles_from_db` source so SR levels stay consistent with the other
/// indicators (R9.1).
///
/// Computation:
/// 1. The classic pivot is derived from the most recent completed period (the
///    last candle's high/low/close):
///    `pivot = (H + L + C) / 3`, `r1 = 2·pivot − L`, `s1 = 2·pivot − H`,
///    `r2 = pivot + (H − L)`, `s2 = pivot − (H − L)`,
///    `r3 = H + 2·(pivot − L)`, `s3 = L − 2·(H − pivot)`.
/// 2. `recent_high`/`recent_low` are the aggregate extremes over the window.
/// 3. For intraday timeframes, the opening range (first
///    [`OPENING_RANGE_CANDLES`] candles) and a daily macro pivot
///    (`(recent_high + recent_low + last_close) / 3`) are attached (R9.3).
/// 4. The resulting `s3..=r3` sequence is checked for the canonical ordering;
///    any violation (including non-finite values) sets `ordering_exception`
///    while the computed levels are still returned (R9.2).
pub fn compute_sr(candles: &[Candle], timeframe: &str) -> SrLevels {
    let intraday = is_intraday(timeframe);

    // No data → return a defined, deterministic zeroed result with the ordering
    // exception flagged so callers never see fabricated levels.
    if candles.is_empty() {
        return SrLevels {
            pivot: 0.0,
            s1: 0.0,
            s2: 0.0,
            s3: 0.0,
            r1: 0.0,
            r2: 0.0,
            r3: 0.0,
            recent_high: 0.0,
            recent_low: 0.0,
            opening_range_high: None,
            opening_range_low: None,
            daily_pivot: None,
            ordering_exception: Some(
                "insufficient candle data: no candles supplied".to_string(),
            ),
        };
    }

    // Aggregate window extremes (informational + daily macro pivot input).
    let mut recent_high = f64::MIN;
    let mut recent_low = f64::MAX;
    for c in candles {
        if c.high > recent_high {
            recent_high = c.high;
        }
        if c.low < recent_low {
            recent_low = c.low;
        }
    }

    // Classic pivots are derived from the most recent completed period
    // (the last candle's high/low/close).
    let last = candles.last().unwrap();
    let (ph, pl, pc) = (last.high, last.low, last.close);
    let pivot = (ph + pl + pc) / 3.0;
    let r1 = 2.0 * pivot - pl;
    let s1 = 2.0 * pivot - ph;
    let r2 = pivot + (ph - pl);
    let s2 = pivot - (ph - pl);
    let r3 = ph + 2.0 * (pivot - pl);
    let s3 = pl - 2.0 * (ph - pivot);

    // Intraday extras: opening range over the first candles + daily macro pivot
    // computed from the aggregate window extremes (R9.3).
    let (opening_range_high, opening_range_low, daily_pivot) = if intraday {
        let n = OPENING_RANGE_CANDLES.min(candles.len());
        let mut or_high = f64::MIN;
        let mut or_low = f64::MAX;
        for c in &candles[..n] {
            if c.high > or_high {
                or_high = c.high;
            }
            if c.low < or_low {
                or_low = c.low;
            }
        }
        let daily = (recent_high + recent_low + pc) / 3.0;
        (Some(or_high), Some(or_low), Some(daily))
    } else {
        (None, None, None)
    };

    let ordering = [s3, s2, s1, pivot, r1, r2, r3];
    let ordering_exception = detect_ordering_exception(&ordering);

    SrLevels {
        pivot,
        s1,
        s2,
        s3,
        r1,
        r2,
        r3,
        recent_high,
        recent_low,
        opening_range_high,
        opening_range_low,
        daily_pivot,
        ordering_exception,
    }
}

/// Inspect the `[s3, s2, s1, pivot, r1, r2, r3]` sequence and return an
/// ordering-exception message when the canonical non-decreasing ordering cannot
/// be satisfied — either because a level is non-finite or because the data
/// forced an out-of-order level (R9.2). Returns `None` when the ordering holds.
fn detect_ordering_exception(levels: &[f64; 7]) -> Option<String> {
    // Any non-finite level means the data could not produce consistent levels.
    if levels.iter().any(|v| !v.is_finite()) {
        return Some(
            "non-finite levels: candle data forced an undefined ordering".to_string(),
        );
    }
    // Canonical ordering must be non-decreasing across S3..=R3.
    for w in levels.windows(2) {
        if w[0] > w[1] {
            return Some(
                "level ordering violation: s3 <= s2 <= s1 <= pivot <= r1 <= r2 <= r3 \
                 could not be satisfied"
                    .to_string(),
            );
        }
    }
    None
}

// ── Data-Sufficiency Classifier (pure module — R5.2) ─────────────────────────

/// The result of classifying whether enough candle data exists to compute the
/// requested indicators (R5.2).
///
/// The classifier compares the number of `available` candles against the
/// `required` minimum, allowing a configured `tolerance` for a minimal
/// shortfall. The three branches map directly to the requirement:
///
/// * [`SufficiencyOutcome::Error`] — the shortfall exceeds the tolerance; the
///   affected tool must return a data-insufficiency error.
/// * [`SufficiencyOutcome::ProceedWithWarning`] — the shortfall is within the
///   tolerance; the tool proceeds and attaches the carried data-shortfall
///   warning to its result.
/// * [`SufficiencyOutcome::Ok`] — enough data is available; no warning.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum SufficiencyOutcome {
    /// Too few candles even after allowing the tolerance — return an error.
    Error,
    /// A minimal shortfall within tolerance — proceed but attach this warning.
    ProceedWithWarning { warning: String },
    /// Enough candles are available — proceed without a warning.
    Ok,
}

impl SufficiencyOutcome {
    /// True when analysis may proceed (either [`Ok`](SufficiencyOutcome::Ok) or
    /// [`ProceedWithWarning`](SufficiencyOutcome::ProceedWithWarning)).
    pub fn may_proceed(&self) -> bool {
        !matches!(self, SufficiencyOutcome::Error)
    }

    /// The data-shortfall warning, if one is attached.
    pub fn warning(&self) -> Option<&str> {
        match self {
            SufficiencyOutcome::ProceedWithWarning { warning } => Some(warning.as_str()),
            _ => None,
        }
    }
}

/// Classify data sufficiency using the three-branch rule (R5.2).
///
/// Given the number of candles `available`, the `required` minimum to compute
/// the requested indicators, and the configured minimal-shortfall `tolerance`,
/// the classification is:
///
/// * `available < required - tolerance` → [`SufficiencyOutcome::Error`]
/// * `required - tolerance <= available < required` →
///   [`SufficiencyOutcome::ProceedWithWarning`] (carries a data-shortfall warning)
/// * `available >= required` → [`SufficiencyOutcome::Ok`]
///
/// The lower error bound is computed with saturating subtraction, so when the
/// `tolerance` meets or exceeds the `required` count there is no error band:
/// any shortfall is then treated as within tolerance.
///
/// The function is **pure**: identical inputs always yield an identical
/// outcome.
pub fn classify_sufficiency(
    available: usize,
    required: usize,
    tolerance: usize,
) -> SufficiencyOutcome {
    // Enough data — no shortfall (boundary `available == required` is Ok).
    if available >= required {
        return SufficiencyOutcome::Ok;
    }

    // Lower bound of the proceed-with-warning band. Saturating subtraction keeps
    // the bound at 0 when tolerance >= required (no error band in that case).
    let error_threshold = required.saturating_sub(tolerance);

    if available < error_threshold {
        SufficiencyOutcome::Error
    } else {
        // Within the minimal-shortfall tolerance — proceed with a warning that
        // states the actual shortfall so the agent can surface the limitation.
        let shortfall = required - available;
        SufficiencyOutcome::ProceedWithWarning {
            warning: format!(
                "data-shortfall: {} of {} required candles available ({} short, \
                 within tolerance of {})",
                available, required, shortfall, tolerance
            ),
        }
    }
}

// ── Consensus Report ────────────────────────────────────────────────────────

/// Helper: convert f64 to Option, turning NaN/Inf into None for clean JSON.
///
/// Rounds finite values to 2 decimal places (the convention used for the
/// Consensus_Report's display fields). Non-finite values (NaN/±Inf) become
/// `None`, which serializes to an explicit JSON `null` rather than a
/// fabricated number (R4.2, R4.3).
pub fn finite_opt(v: f64) -> Option<f64> {
    if v.is_finite() { Some((v * 100.0).round() / 100.0) } else { None }
}

/// Like [`finite_opt`] but preserves full precision (no rounding).
///
/// Use for fields where exact values matter (e.g. levels feeding the
/// Trade_Validator). Non-finite values become `None` → JSON `null`.
pub fn finite_opt_raw(v: f64) -> Option<f64> {
    if v.is_finite() { Some(v) } else { None }
}

/// Serde `serialize_with` helper for a numeric-or-null indicator field.
///
/// Serializes a finite `f64` as a JSON number and any non-finite value
/// (NaN/±Inf) as JSON `null`, guaranteeing the contract that a numeric field
/// is "a finite number or explicit null; never NaN/Inf" (R4.2, R4.3).
pub fn serialize_finite_or_null<S>(v: &f64, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    match finite_opt_raw(*v) {
        Some(n) => serializer.serialize_f64(n),
        None => serializer.serialize_none(),
    }
}

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
    // ── Raw indicator values (exposed so the LLM can reason precisely) ────
    pub current_price: Option<f64>,
    pub rsi_14: Option<f64>,
    pub stoch_k: Option<f64>,
    pub ema_9: Option<f64>,
    pub ema_21: Option<f64>,
    pub sma_50: Option<f64>,
    pub sma_200: Option<f64>,
    pub macd_line: Option<f64>,
    pub macd_signal: Option<f64>,
    pub macd_histogram: Option<f64>,
    pub bb_upper: Option<f64>,
    pub bb_mid: Option<f64>,
    pub bb_lower: Option<f64>,
    pub atr_14: Option<f64>,
    pub vwap: Option<f64>,
    pub obv: Option<f64>,
    pub cmf: Option<f64>,
    pub parabolic_sar: Option<f64>,
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
            // Projection fields come from the OLS/predictive layer and can be
            // non-finite (e.g. a singular fit); funnel them through the finite
            // helper so a NaN/Inf never reaches the wire — only a number or
            // explicit null (R4.2, R4.3).
            vwepr_value: vwepr_value.and_then(finite_opt_raw),
            vwepr_slope: vwepr_slope.and_then(finite_opt_raw),
            ols_value: ols_value.and_then(finite_opt_raw),
            ols_slope: ols_slope.and_then(finite_opt_raw),
            // Raw indicator values for LLM reasoning. `finite_opt` enforces the
            // numeric-or-null contract and rounds; the `close > 0.0` guard keeps
            // the "positive price" semantics while rejecting NaN (NaN > 0 is
            // false) and +Inf (caught by finite_opt) alike.
            current_price: if close > 0.0 { finite_opt(close) } else { None },
            rsi_14: finite_opt(indicators.rsi_14),
            stoch_k: finite_opt(indicators.stoch_k),
            ema_9: finite_opt(indicators.ema_9),
            ema_21: finite_opt(indicators.ema_21),
            sma_50: finite_opt(indicators.sma_50),
            sma_200: finite_opt(indicators.sma_200),
            macd_line: finite_opt(indicators.macd_line),
            macd_signal: finite_opt(indicators.macd_signal),
            macd_histogram: finite_opt(indicators.macd_histogram),
            bb_upper: finite_opt(indicators.bb_upper),
            bb_mid: finite_opt(indicators.bb_mid),
            bb_lower: finite_opt(indicators.bb_lower),
            atr_14: finite_opt(indicators.atr_14),
            vwap: finite_opt(indicators.vwap),
            obv: finite_opt(indicators.obv_current),
            cmf: finite_opt(indicators.cmf),
            parabolic_sar: finite_opt(indicators.parabolic_sar),
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

// ── Unit tests: timeframe validator + serialization helpers ──────────────────

#[cfg(test)]
mod validator_helper_tests {
    use super::*;

    #[test]
    fn supported_timeframes_pass() {
        for tf in SUPPORTED_TIMEFRAMES {
            assert!(validate_timeframe(tf).is_ok(), "expected '{}' to be valid", tf);
        }
    }

    #[test]
    fn supported_timeframes_are_case_and_whitespace_insensitive() {
        assert!(validate_timeframe(" 1H ").is_ok());
        assert!(validate_timeframe("1D").is_ok());
    }

    #[test]
    fn unsupported_timeframe_names_the_offender() {
        let err = validate_timeframe("2w").expect_err("expected '2w' to be rejected");
        assert_eq!(err.timeframe, "2w");
        // The descriptive message must name the offending timeframe.
        assert!(err.to_string().contains("2w"));
    }

    #[test]
    fn finite_opt_maps_non_finite_to_none() {
        assert_eq!(finite_opt(f64::NAN), None);
        assert_eq!(finite_opt(f64::INFINITY), None);
        assert_eq!(finite_opt(f64::NEG_INFINITY), None);
        assert_eq!(finite_opt(12.345), Some(12.35));
    }

    #[test]
    fn finite_opt_raw_preserves_precision() {
        assert_eq!(finite_opt_raw(12.3456789), Some(12.3456789));
        assert_eq!(finite_opt_raw(f64::NAN), None);
    }

    #[test]
    fn serialize_finite_or_null_emits_null_for_non_finite() {
        #[derive(serde::Serialize)]
        struct Wrap {
            #[serde(serialize_with = "serialize_finite_or_null")]
            value: f64,
        }
        let nan = serde_json::to_value(Wrap { value: f64::NAN }).unwrap();
        assert!(nan["value"].is_null());
        let num = serde_json::to_value(Wrap { value: 42.5 }).unwrap();
        assert_eq!(num["value"].as_f64(), Some(42.5));
    }
}

// ── Unit tests: Trade_Validator (R6.1–R6.5) ──────────────────────────────────

#[cfg(test)]
mod trade_validator_tests {
    use super::*;

    fn buy(entry: f64, sl: f64, tp: f64) -> Option<ExecutionLevels> {
        Some(ExecutionLevels { entry, stop_loss: sl, take_profit: tp })
    }

    #[test]
    fn hold_bypasses_level_checks() {
        // HOLD passes even with no levels and no ATR.
        assert!(matches!(
            validate_trade(Action::Hold, None, None),
            ValidatorOutcome::Pass { risk_reward } if risk_reward == 0.0
        ));
        // HOLD passes even with "inconsistent" levels — checks are bypassed.
        assert!(validate_trade(Action::Hold, buy(100.0, 200.0, 50.0), Some(10.0)).is_pass());
    }

    #[test]
    fn missing_levels_rejected() {
        // No levels at all.
        assert_eq!(
            validate_trade(Action::Buy, None, None),
            ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
        );
        // Non-finite price counts as missing.
        assert_eq!(
            validate_trade(Action::Sell, buy(100.0, f64::NAN, 90.0), None),
            ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
        );
    }

    #[test]
    fn buy_direction_consistency_enforced() {
        // Valid BUY: stop below entry, target above.
        assert!(validate_trade(Action::Buy, buy(100.0, 90.0, 130.0), None).is_pass());
        // Stop above entry — inconsistent.
        assert_eq!(
            validate_trade(Action::Buy, buy(100.0, 110.0, 130.0), None),
            ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent }
        );
        // Target below entry — inconsistent.
        assert_eq!(
            validate_trade(Action::Buy, buy(100.0, 90.0, 95.0), None),
            ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent }
        );
    }

    #[test]
    fn sell_direction_consistency_enforced() {
        // Valid SELL: stop above entry, target below. risk=10, reward=30 → 3.0
        assert!(validate_trade(Action::Sell, buy(100.0, 110.0, 70.0), None).is_pass());
        // Stop below entry — inconsistent.
        assert_eq!(
            validate_trade(Action::Sell, buy(100.0, 90.0, 70.0), None),
            ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent }
        );
    }

    #[test]
    fn risk_reward_boundary_passes_at_exactly_two() {
        // risk = 10, reward = 20 → RR = 2.0 exactly → passes (R6.2).
        assert!(matches!(
            validate_trade(Action::Buy, buy(100.0, 90.0, 120.0), None),
            ValidatorOutcome::Pass { risk_reward } if (risk_reward - 2.0).abs() < 1e-9
        ));
        // risk = 10, reward = 19.9 → RR < 2.0 → fails.
        assert_eq!(
            validate_trade(Action::Buy, buy(100.0, 90.0, 119.9), None),
            ValidatorOutcome::Fail { reason: ValidatorReason::RiskRewardTooLow }
        );
    }

    #[test]
    fn stop_too_tight_boundary() {
        // ATR = 10 → min stop distance = 15. risk = 20 (>=15) passes the ATR
        // check; reward = 60 → RR 3.0 → overall pass.
        assert!(validate_trade(Action::Buy, buy(100.0, 80.0, 160.0), Some(10.0)).is_pass());
        // Stop distance exactly 1.5*ATR = 15 → passes the ATR check.
        assert!(validate_trade(Action::Buy, buy(100.0, 85.0, 145.0), Some(10.0)).is_pass());
        // Stop distance 14 < 15 → StopTooTight (R6.3).
        assert_eq!(
            validate_trade(Action::Buy, buy(100.0, 86.0, 200.0), Some(10.0)),
            ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight }
        );
    }

    #[test]
    fn stop_too_tight_skipped_when_atr_unavailable() {
        // No ATR → ATR check skipped; RR = 3.0 → pass.
        assert!(validate_trade(Action::Buy, buy(100.0, 99.0, 103.0), None).is_pass());
        // Non-finite ATR → treated as unavailable, ATR check skipped.
        assert!(validate_trade(Action::Buy, buy(100.0, 99.0, 103.0), Some(f64::NAN)).is_pass());
    }

    #[test]
    fn validator_is_deterministic() {
        let a = validate_trade(Action::Sell, buy(250.0, 270.0, 200.0), Some(8.0));
        let b = validate_trade(Action::Sell, buy(250.0, 270.0, 200.0), Some(8.0));
        assert_eq!(a, b);
    }

    // ── Multi-leg Management_Plan validation (trade-management R5.1–R5.6) ──────

    fn leg(target: f64, fraction: f64) -> ScaleOutLeg {
        ScaleOutLeg { target, fraction }
    }

    fn plan(
        entry: f64,
        initial_stop: f64,
        legs: Vec<ScaleOutLeg>,
        breakeven: Option<BreakevenTrigger>,
    ) -> ManagementPlan {
        ManagementPlan { entry, initial_stop, legs, breakeven, trailing: None }
    }

    #[test]
    fn plan_hold_bypasses_all_checks() {
        // A wildly inconsistent plan still passes under HOLD (R5.5).
        let p = plan(100.0, 200.0, vec![leg(50.0, 2.0)], None);
        assert!(matches!(
            validate_management_plan(Action::Hold, &p, Some(10.0), MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Pass { risk_reward } if risk_reward == 0.0
        ));
    }

    #[test]
    fn plan_valid_buy_passes_with_blended_rr() {
        // entry 100, stop 90 (risk 10). Two legs: 0.5 @ 120 (+20), 0.5 @ 140 (+40).
        // blended reward = 0.5*20 + 0.5*40 = 30 → blended RR = 3.0.
        let p = plan(
            100.0,
            90.0,
            vec![leg(120.0, 0.5), leg(140.0, 0.5)],
            Some(BreakevenTrigger { price: Some(110.0), r_multiple: None }),
        );
        assert!(matches!(
            validate_management_plan(Action::Buy, &p, Some(5.0), MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Pass { risk_reward } if (risk_reward - 3.0).abs() < 1e-9
        ));
    }

    #[test]
    fn plan_valid_sell_passes_mirror_image() {
        // entry 100, stop 110 (risk 10). Targets below entry, non-increasing.
        // legs: 0.5 @ 80 (+20), 0.5 @ 60 (+40) → blended RR = 3.0.
        let p = plan(
            100.0,
            110.0,
            vec![leg(80.0, 0.5), leg(60.0, 0.5)],
            Some(BreakevenTrigger { price: None, r_multiple: Some(1.0) }),
        );
        assert!(validate_management_plan(
            Action::Sell,
            &p,
            None,
            MIN_BLENDED_REWARD_TO_RISK
        )
        .is_pass());
    }

    #[test]
    fn plan_leg_fraction_out_of_range() {
        // Fraction > 1.0.
        let p = plan(100.0, 90.0, vec![leg(120.0, 1.5)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &p, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::LegFractionOutOfRange }
        );
        // Fraction == 0.0 is excluded by the open interval.
        let p0 = plan(100.0, 90.0, vec![leg(120.0, 0.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &p0, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::LegFractionOutOfRange }
        );
        // Fractions summing to > 1.0.
        let psum = plan(100.0, 90.0, vec![leg(120.0, 0.6), leg(140.0, 0.6)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &psum, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::LegFractionOutOfRange }
        );
        // A single full-size leg at fraction 1.0 is in range.
        let pfull = plan(100.0, 90.0, vec![leg(130.0, 1.0)], None);
        assert!(validate_management_plan(
            Action::Buy,
            &pfull,
            None,
            MIN_BLENDED_REWARD_TO_RISK
        )
        .is_pass());
    }

    #[test]
    fn plan_target_ordering_inconsistent() {
        // BUY with a target below entry.
        let below = plan(100.0, 90.0, vec![leg(95.0, 1.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &below, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent }
        );
        // BUY with stop above entry.
        let stop_above = plan(100.0, 110.0, vec![leg(130.0, 1.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &stop_above, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent }
        );
        // BUY with decreasing targets (not non-decreasing).
        let decreasing = plan(100.0, 90.0, vec![leg(140.0, 0.5), leg(120.0, 0.5)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &decreasing, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::TargetOrderingInconsistent }
        );
    }

    #[test]
    fn plan_breakeven_out_of_range() {
        // BUY breakeven below entry — not on the profit side.
        let below = plan(
            100.0,
            90.0,
            vec![leg(120.0, 0.5), leg(140.0, 0.5)],
            Some(BreakevenTrigger { price: Some(95.0), r_multiple: None }),
        );
        assert_eq!(
            validate_management_plan(Action::Buy, &below, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::BreakevenOutOfRange }
        );
        // BUY breakeven at/above the first target — not strictly between.
        let at_target = plan(
            100.0,
            90.0,
            vec![leg(120.0, 0.5), leg(140.0, 0.5)],
            Some(BreakevenTrigger { price: Some(120.0), r_multiple: None }),
        );
        assert_eq!(
            validate_management_plan(Action::Buy, &at_target, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::BreakevenOutOfRange }
        );
    }

    #[test]
    fn plan_blended_rr_too_low() {
        // entry 100, stop 90 (risk 10). Single leg 1.0 @ 115 → blended RR 1.5 < 2.0.
        let p = plan(100.0, 90.0, vec![leg(115.0, 1.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &p, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::BlendedRrTooLow }
        );
    }

    #[test]
    fn plan_stop_too_tight_preserved() {
        // ATR 10 → min stop distance 15, but risk here is 10 → StopTooTight (R6.3),
        // proving the base rule is enforced on top of the multi-leg checks.
        let p = plan(100.0, 90.0, vec![leg(140.0, 1.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &p, Some(10.0), MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight }
        );
    }

    #[test]
    fn plan_missing_levels() {
        // No legs at all.
        let none = plan(100.0, 90.0, vec![], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &none, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
        );
        // Non-finite entry.
        let nan_entry = plan(f64::NAN, 90.0, vec![leg(120.0, 1.0)], None);
        assert_eq!(
            validate_management_plan(Action::Buy, &nan_entry, None, MIN_BLENDED_REWARD_TO_RISK),
            ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
        );
    }

    #[test]
    fn plan_validation_is_deterministic() {
        let p = plan(
            100.0,
            90.0,
            vec![leg(120.0, 0.5), leg(140.0, 0.5)],
            Some(BreakevenTrigger { price: Some(110.0), r_multiple: None }),
        );
        let a = validate_management_plan(Action::Buy, &p, Some(5.0), MIN_BLENDED_REWARD_TO_RISK);
        let b = validate_management_plan(Action::Buy, &p, Some(5.0), MIN_BLENDED_REWARD_TO_RISK);
        assert_eq!(a, b);
    }

    #[test]
    fn plan_new_reason_tags_are_stable() {
        assert_eq!(ValidatorReason::LegFractionOutOfRange.as_tag(), "leg-fraction-out-of-range");
        assert_eq!(
            ValidatorReason::TargetOrderingInconsistent.as_tag(),
            "target-ordering-inconsistent"
        );
        assert_eq!(ValidatorReason::BreakevenOutOfRange.as_tag(), "breakeven-out-of-range");
        assert_eq!(ValidatorReason::BlendedRrTooLow.as_tag(), "blended-rr-too-low");
    }

    #[test]
    fn action_parsing_is_lenient() {
        assert_eq!(Action::from_str_lenient(" buy "), Action::Buy);
        assert_eq!(Action::from_str_lenient("SELL"), Action::Sell);
        assert_eq!(Action::from_str_lenient("hold"), Action::Hold);
        assert_eq!(Action::from_str_lenient("whatever"), Action::Hold);
    }

    // ── Python <-> Rust parity fixtures (trade-management task 6.9, R5.6) ──────
    //
    // This is the authoritative-side half of the shared fixture table pinned by
    // the Python `tests/test_tm_validator_parity.py::PARITY_FIXTURES`. The rows
    // here are re-encoded VERBATIM from that table — a representative valid plan
    // (BUY and the SELL mirror) plus exactly one fixture per rejection class and
    // the HOLD bypass — and each is asserted to produce the SAME stable reason
    // tag (or a pass) as the Python validator. Because every fixture isolates a
    // single deciding condition, the differing internal check-ordering of the
    // two implementations cannot change which tag is produced. The blended
    // reward-to-risk floor is pinned to `MIN_BLENDED_REWARD_TO_RISK` (2.0) on
    // both sides so the examples are deterministic.
    #[test]
    fn plan_parity_fixtures() {
        // (name, action, entry, initial_stop, legs, breakeven, atr_14, expected_tag)
        // expected_tag = None means "passes"; Some(tag) is the reason both sides
        // must return.
        struct Fixture {
            name: &'static str,
            action: Action,
            entry: f64,
            initial_stop: f64,
            legs: Vec<ScaleOutLeg>,
            breakeven: Option<BreakevenTrigger>,
            atr_14: Option<f64>,
            expected_tag: Option<&'static str>,
        }

        let fixtures = vec![
            Fixture {
                name: "valid_buy",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(120.0, 0.5), leg(140.0, 0.5)],
                breakeven: Some(BreakevenTrigger { price: Some(110.0), r_multiple: None }),
                atr_14: None,
                expected_tag: None,
            },
            Fixture {
                name: "valid_sell",
                action: Action::Sell,
                entry: 100.0,
                initial_stop: 110.0,
                legs: vec![leg(80.0, 0.5), leg(60.0, 0.5)],
                breakeven: Some(BreakevenTrigger { price: None, r_multiple: Some(1.0) }),
                atr_14: None,
                expected_tag: None,
            },
            Fixture {
                name: "leg_fraction",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(120.0, 1.5)],
                breakeven: None,
                atr_14: None,
                expected_tag: Some("leg-fraction-out-of-range"),
            },
            Fixture {
                name: "target_ordering",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(140.0, 0.5), leg(120.0, 0.5)],
                breakeven: None,
                atr_14: None,
                expected_tag: Some("target-ordering-inconsistent"),
            },
            Fixture {
                name: "breakeven_range",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(120.0, 0.5), leg(140.0, 0.5)],
                breakeven: Some(BreakevenTrigger { price: Some(95.0), r_multiple: None }),
                atr_14: None,
                expected_tag: Some("breakeven-out-of-range"),
            },
            Fixture {
                name: "blended_rr",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(115.0, 1.0)],
                breakeven: None,
                atr_14: None,
                expected_tag: Some("blended-rr-too-low"),
            },
            Fixture {
                name: "stop_too_tight",
                action: Action::Buy,
                entry: 100.0,
                initial_stop: 90.0,
                legs: vec![leg(140.0, 1.0)],
                breakeven: None,
                atr_14: Some(10.0),
                expected_tag: Some("stop-too-tight"),
            },
            Fixture {
                name: "hold_bypass",
                action: Action::Hold,
                entry: 100.0,
                initial_stop: 200.0,
                legs: vec![leg(50.0, 2.0)],
                breakeven: None,
                atr_14: Some(10.0),
                expected_tag: None,
            },
        ];

        for f in &fixtures {
            let p = plan(f.entry, f.initial_stop, f.legs.clone(), f.breakeven);
            let outcome =
                validate_management_plan(f.action, &p, f.atr_14, MIN_BLENDED_REWARD_TO_RISK);
            match f.expected_tag {
                None => assert!(
                    outcome.is_pass(),
                    "{}: expected pass, got {:?}",
                    f.name,
                    outcome
                ),
                Some(tag) => match outcome {
                    ValidatorOutcome::Fail { reason } => assert_eq!(
                        reason.as_tag(),
                        tag,
                        "{}: expected tag {}, got {}",
                        f.name,
                        tag,
                        reason.as_tag()
                    ),
                    ValidatorOutcome::Pass { .. } => {
                        panic!("{}: expected reject {}, got pass", f.name, tag)
                    }
                },
            }
        }
    }
}

// ── Property tests: timeframe validator ──────────────────────────────────────
//
// Feature: deep-quant-analysis-hardening, Property 16: Unsupported timeframes
// are rejected with a descriptive error
#[cfg(test)]
mod timeframe_validator_proptests {
    use super::*;
    use proptest::prelude::*;

    /// True when `tf` would be accepted by the validator (after the same
    /// trim + case-insensitive normalization the validator applies). Used to
    /// keep the "unsupported" generator strictly outside the supported set.
    fn is_supported(tf: &str) -> bool {
        let normalized = tf.trim();
        SUPPORTED_TIMEFRAMES
            .iter()
            .any(|s| s.eq_ignore_ascii_case(normalized))
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        /// Feature: deep-quant-analysis-hardening, Property 16: Unsupported
        /// timeframes are rejected with a descriptive error.
        ///
        /// For any timeframe string outside the supported set, the validator
        /// returns an Err whose Display message names the offending timeframe.
        #[test]
        fn unsupported_timeframes_rejected_with_descriptive_error(
            tf in any::<String>().prop_filter(
                "must not be a supported timeframe",
                |s| !is_supported(s),
            )
        ) {
            let result = validate_timeframe(&tf);
            prop_assert!(
                result.is_err(),
                "expected '{}' to be rejected as unsupported",
                tf
            );
            let err = result.unwrap_err();
            // The error carries the offending timeframe verbatim.
            prop_assert_eq!(&err.timeframe, &tf);
            // The descriptive Display message names the offending timeframe.
            prop_assert!(
                err.to_string().contains(&tf),
                "error message '{}' did not name offending timeframe '{}'",
                err.to_string(),
                tf
            );
        }

        /// Complement of Property 16: every supported timeframe validates Ok.
        #[test]
        fn supported_timeframes_validate_ok(idx in 0usize..SUPPORTED_TIMEFRAMES.len()) {
            let tf = SUPPORTED_TIMEFRAMES[idx];
            prop_assert!(
                validate_timeframe(tf).is_ok(),
                "expected supported timeframe '{}' to validate Ok",
                tf
            );
        }
    }
}

// ── Unit tests: SR_Engine (R9.1–R9.4) ───────────────────────────────────────

#[cfg(test)]
mod sr_engine_tests {
    use super::*;

    fn candle(high: f64, low: f64, close: f64) -> Candle {
        Candle { open: (high + low) / 2.0, high, low, close, volume: 1_000.0 }
    }

    #[test]
    fn is_intraday_only_excludes_daily() {
        assert!(is_intraday("1m"));
        assert!(is_intraday("15m"));
        assert!(is_intraday("1h"));
        assert!(is_intraday("4h"));
        // Daily is the only non-intraday timeframe (case/whitespace tolerant).
        assert!(!is_intraday("1d"));
        assert!(!is_intraday(" 1D "));
    }

    #[test]
    fn classic_pivot_formulas_match_expected() {
        // Single prior period H=110, L=90, C=105.
        let candles = vec![candle(110.0, 90.0, 105.0)];
        let sr = compute_sr(&candles, "1d");
        let pivot = (110.0 + 90.0 + 105.0) / 3.0; // 101.6667
        assert!((sr.pivot - pivot).abs() < 1e-9);
        assert!((sr.r1 - (2.0 * pivot - 90.0)).abs() < 1e-9);
        assert!((sr.s1 - (2.0 * pivot - 110.0)).abs() < 1e-9);
        assert!((sr.r2 - (pivot + 20.0)).abs() < 1e-9);
        assert!((sr.s2 - (pivot - 20.0)).abs() < 1e-9);
        assert!((sr.r3 - (110.0 + 2.0 * (pivot - 90.0))).abs() < 1e-9);
        assert!((sr.s3 - (90.0 - 2.0 * (110.0 - pivot))).abs() < 1e-9);
    }

    #[test]
    fn well_formed_data_orders_levels_without_exception() {
        let candles = vec![
            candle(105.0, 95.0, 100.0),
            candle(112.0, 98.0, 108.0),
            candle(110.0, 100.0, 104.0),
        ];
        let sr = compute_sr(&candles, "1d");
        assert!(sr.ordering_exception.is_none());
        assert!(sr.s3 <= sr.s2);
        assert!(sr.s2 <= sr.s1);
        assert!(sr.s1 <= sr.pivot);
        assert!(sr.pivot <= sr.r1);
        assert!(sr.r1 <= sr.r2);
        assert!(sr.r2 <= sr.r3);
    }

    #[test]
    fn daily_timeframe_omits_intraday_extras() {
        let candles = vec![candle(110.0, 90.0, 105.0)];
        let sr = compute_sr(&candles, "1d");
        assert!(sr.opening_range_high.is_none());
        assert!(sr.opening_range_low.is_none());
        assert!(sr.daily_pivot.is_none());
    }

    #[test]
    fn intraday_timeframe_adds_opening_range_and_daily_pivot() {
        let candles = vec![
            candle(105.0, 95.0, 100.0),
            candle(112.0, 98.0, 108.0),
            candle(110.0, 100.0, 104.0),
        ];
        let sr = compute_sr(&candles, "15m");
        // Opening range over the first OPENING_RANGE_CANDLES (here all 3).
        assert_eq!(sr.opening_range_high, Some(112.0));
        assert_eq!(sr.opening_range_low, Some(95.0));
        // Daily macro pivot uses aggregate extremes + last close.
        let expected_daily = (112.0 + 95.0 + 104.0) / 3.0;
        assert!((sr.daily_pivot.unwrap() - expected_daily).abs() < 1e-9);
    }

    #[test]
    fn empty_candles_flag_ordering_exception() {
        let sr = compute_sr(&[], "1h");
        assert!(sr.ordering_exception.is_some());
        assert_eq!(sr.pivot, 0.0);
        assert!(sr.opening_range_high.is_none());
    }

    #[test]
    fn non_finite_levels_flag_ordering_exception() {
        // A non-finite price propagates to the levels and must be flagged.
        let candles = vec![candle(f64::INFINITY, 90.0, 100.0)];
        let sr = compute_sr(&candles, "1d");
        assert!(sr.ordering_exception.is_some());
    }

    #[test]
    fn computation_is_deterministic() {
        let candles = vec![
            candle(105.0, 95.0, 100.0),
            candle(112.0, 98.0, 108.0),
        ];
        let a = compute_sr(&candles, "15m");
        let b = compute_sr(&candles, "15m");
        assert_eq!(a, b);
    }
}

// ── Unit tests: Data-Sufficiency Classifier (R5.2) ──────────────────────────

#[cfg(test)]
mod sufficiency_tests {
    use super::*;

    #[test]
    fn enough_data_is_ok() {
        // available == required → Ok (boundary).
        assert_eq!(classify_sufficiency(100, 100, 5), SufficiencyOutcome::Ok);
        // available > required → Ok.
        assert_eq!(classify_sufficiency(150, 100, 5), SufficiencyOutcome::Ok);
    }

    #[test]
    fn minimal_shortfall_proceeds_with_warning() {
        // required - tolerance = 95; available 96..=99 → proceed-with-warning.
        let outcome = classify_sufficiency(96, 100, 5);
        assert!(matches!(outcome, SufficiencyOutcome::ProceedWithWarning { .. }));
        assert!(outcome.may_proceed());
        // The attached warning states the shortfall.
        let warning = outcome.warning().expect("warning attached");
        assert!(warning.contains("4 short"), "warning was: {}", warning);
    }

    #[test]
    fn warning_band_lower_boundary_is_inclusive() {
        // available == required - tolerance (95) → proceed-with-warning.
        assert!(matches!(
            classify_sufficiency(95, 100, 5),
            SufficiencyOutcome::ProceedWithWarning { .. }
        ));
    }

    #[test]
    fn shortfall_beyond_tolerance_is_error() {
        // available 94 < required - tolerance (95) → Error.
        assert_eq!(classify_sufficiency(94, 100, 5), SufficiencyOutcome::Error);
        assert!(!classify_sufficiency(94, 100, 5).may_proceed());
    }

    #[test]
    fn zero_tolerance_has_no_warning_band() {
        // With no tolerance any shortfall is an error.
        assert_eq!(classify_sufficiency(99, 100, 0), SufficiencyOutcome::Error);
        // Exactly required still Ok.
        assert_eq!(classify_sufficiency(100, 100, 0), SufficiencyOutcome::Ok);
    }

    #[test]
    fn tolerance_at_least_required_removes_error_band() {
        // tolerance >= required → saturating_sub keeps threshold at 0, so even
        // zero available candles proceed with a warning rather than error.
        assert!(matches!(
            classify_sufficiency(0, 50, 50),
            SufficiencyOutcome::ProceedWithWarning { .. }
        ));
        assert!(matches!(
            classify_sufficiency(0, 50, 100),
            SufficiencyOutcome::ProceedWithWarning { .. }
        ));
    }

    #[test]
    fn classifier_is_deterministic() {
        let a = classify_sufficiency(96, 100, 5);
        let b = classify_sufficiency(96, 100, 5);
        assert_eq!(a, b);
    }
}

// ── Property tests: Trade_Validator (R6.1–R6.5) ──────────────────────────────
//
// Property-based coverage for the pure `validate_trade` risk rules. Generators
// carefully construct direction-consistent levels and derive take-profit from a
// target risk-reward so each property isolates the single check it exercises.
#[cfg(test)]
mod trade_validator_proptests {
    use super::*;
    use proptest::prelude::*;

    /// A finite price within a sane, non-degenerate band.
    fn finite_price() -> impl Strategy<Value = f64> {
        -1.0e6..1.0e6
    }

    /// BUY or SELL only — HOLD bypasses every level check by design.
    fn buy_or_sell() -> impl Strategy<Value = Action> {
        prop_oneof![Just(Action::Buy), Just(Action::Sell)]
    }

    /// A non-finite f64 — the values R6.1 must reject alongside missing levels.
    fn non_finite() -> impl Strategy<Value = f64> {
        prop_oneof![
            Just(f64::INFINITY),
            Just(f64::NEG_INFINITY),
            Just(f64::NAN),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 20: Missing-level
        // trades are rejected
        // Validates: Requirements 6.1
        #[test]
        fn prop20_missing_levels_rejected(
            action in buy_or_sell(),
            use_none in any::<bool>(),
            bad_slot in 0usize..3,
            bad in non_finite(),
            a in finite_price(),
            b in finite_price(),
            c in finite_price(),
            atr in proptest::option::of(0.1f64..1_000.0),
        ) {
            if use_none {
                // No levels supplied at all (R6.1).
                let outcome = validate_trade(action, None, atr);
                prop_assert_eq!(
                    outcome,
                    ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
                );
            } else {
                // Levels present but at least one price is non-finite (R6.1).
                let mut prices = [a, b, c];
                prices[bad_slot] = bad;
                let levels = ExecutionLevels {
                    entry: prices[0],
                    stop_loss: prices[1],
                    take_profit: prices[2],
                };
                let outcome = validate_trade(action, Some(levels), atr);
                prop_assert_eq!(
                    outcome,
                    ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
                );
            }
        }

        // Feature: deep-quant-analysis-hardening, Property 21: Risk-reward below
        // 1:2 is rejected at the boundary
        // Validates: Requirements 6.2
        #[test]
        fn prop21_risk_reward_boundary(
            action in buy_or_sell(),
            entry in 100.0f64..10_000.0,
            risk in 1.0f64..500.0,
            // Include the exact RR = 2.0 boundary alongside a continuous range.
            rr in prop_oneof![Just(2.0f64), 0.1f64..5.0f64],
        ) {
            // Direction-consistent levels with a target RR. atr = None so the
            // stop-distance check is skipped and the RR check is isolated.
            let levels = match action {
                Action::Buy => ExecutionLevels {
                    entry,
                    stop_loss: entry - risk,
                    take_profit: entry + risk * rr,
                },
                Action::Sell => ExecutionLevels {
                    entry,
                    stop_loss: entry + risk,
                    take_profit: entry - risk * rr,
                },
                Action::Hold => unreachable!("generator yields BUY/SELL only"),
            };
            let outcome = validate_trade(action, Some(levels), None);

            // Recompute RR exactly as the validator does to stay float-robust at
            // the 2.0 boundary.
            let actual_risk = (levels.entry - levels.stop_loss).abs();
            let actual_reward = (levels.take_profit - levels.entry).abs();
            let actual_rr = actual_reward / actual_risk;

            if actual_rr < MIN_RISK_REWARD {
                prop_assert_eq!(
                    outcome,
                    ValidatorOutcome::Fail { reason: ValidatorReason::RiskRewardTooLow }
                );
            } else {
                // RR exactly 2.0 (and above) passes the RR check.
                prop_assert!(
                    outcome.is_pass(),
                    "expected Pass for rr={}, got {:?}",
                    actual_rr,
                    outcome
                );
            }
        }

        // Feature: deep-quant-analysis-hardening, Property 22: Stops tighter than
        // 1.5×ATR are rejected at the boundary
        // Validates: Requirements 6.3
        #[test]
        fn prop22_stop_atr_boundary(
            action in buy_or_sell(),
            entry in 100.0f64..10_000.0,
            atr in 0.5f64..200.0,
            // Include the exact 1.5×ATR boundary alongside a continuous range.
            mult in prop_oneof![Just(1.5f64), 0.5f64..3.0f64],
        ) {
            let risk = mult * atr;
            // RR fixed at 3.0 (>= 2.0) so the RR check always passes and the
            // stop-distance check is the deciding factor.
            let levels = match action {
                Action::Buy => ExecutionLevels {
                    entry,
                    stop_loss: entry - risk,
                    take_profit: entry + risk * 3.0,
                },
                Action::Sell => ExecutionLevels {
                    entry,
                    stop_loss: entry + risk,
                    take_profit: entry - risk * 3.0,
                },
                Action::Hold => unreachable!("generator yields BUY/SELL only"),
            };
            let outcome = validate_trade(action, Some(levels), Some(atr));

            // Recompute the stop distance exactly as the validator does.
            let actual_risk = (levels.entry - levels.stop_loss).abs();
            if actual_risk < MIN_STOP_ATR_MULTIPLE * atr {
                prop_assert_eq!(
                    outcome,
                    ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight }
                );
            } else {
                // Stop distance exactly 1.5×ATR (and wider) passes.
                prop_assert!(
                    outcome.is_pass(),
                    "expected Pass for risk={}, atr={}, got {:?}",
                    actual_risk,
                    atr,
                    outcome
                );
            }
        }

        // Feature: deep-quant-analysis-hardening, Property 23: Direction
        // consistency is enforced per side
        // Validates: Requirements 6.4, 6.5
        #[test]
        fn prop23_direction_consistency(
            action in buy_or_sell(),
            entry in finite_price(),
            stop in finite_price(),
            tp in finite_price(),
        ) {
            let levels = ExecutionLevels { entry, stop_loss: stop, take_profit: tp };
            // atr = None to isolate ordering from the stop-distance check; all
            // prices are finite so MissingLevels never pre-empts.
            let outcome = validate_trade(action, Some(levels), None);

            // BUY requires stop < entry < tp; SELL requires tp < entry < stop.
            let direction_ok = match action {
                Action::Buy => stop < entry && entry < tp,
                Action::Sell => tp < entry && entry < stop,
                Action::Hold => unreachable!("generator yields BUY/SELL only"),
            };

            if !direction_ok {
                prop_assert_eq!(
                    outcome,
                    ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent }
                );
            } else {
                // Consistent ordering must never fail for a direction reason
                // (it may still fail the RR check, but never DirectionInconsistent).
                prop_assert!(
                    outcome != ValidatorOutcome::Fail {
                        reason: ValidatorReason::DirectionInconsistent
                    },
                    "consistent levels reported DirectionInconsistent: {:?}",
                    outcome
                );
            }
        }
    }
}

// ── Property tests: SR_Engine (R9.1–R9.2) ────────────────────────────────────
//
// Property-based coverage for the pure `compute_sr` engine. Candles are drawn
// from arbitrary finite OHLC values so both branches of the ordering check are
// exercised (well-formed candles order cleanly; high < low forces a flagged
// exception).
#[cfg(test)]
mod sr_engine_proptests {
    use super::*;
    use proptest::prelude::*;

    /// A finite OHLC component within a bounded band (keeps pivot arithmetic
    /// finite and overflow-free).
    fn finite_ohlc() -> impl Strategy<Value = f64> {
        -1.0e6..1.0e6
    }

    /// An arbitrary finite candle — OHLC components are independent so the
    /// generator covers both ordered and disordered (high < low) windows.
    fn candle_strat() -> impl Strategy<Value = Candle> {
        (finite_ohlc(), finite_ohlc(), finite_ohlc(), finite_ohlc(), 0.0f64..1.0e6)
            .prop_map(|(open, high, low, close, volume)| Candle {
                open,
                high,
                low,
                close,
                volume,
            })
    }

    /// One of the supported timeframes (intraday + daily).
    fn timeframe_strat() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("1m".to_string()),
            Just("5m".to_string()),
            Just("15m".to_string()),
            Just("1h".to_string()),
            Just("4h".to_string()),
            Just("1d".to_string()),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 33: SR levels are
        // derived by formula from the candle source
        // Validates: Requirements 9.1
        #[test]
        fn prop33_levels_match_pivot_formula(
            candles in proptest::collection::vec(candle_strat(), 1..30),
            tf in timeframe_strat(),
        ) {
            let sr = compute_sr(&candles, &tf);

            // Classic floor-trader pivots recomputed from the last (most recent
            // completed) candle's high/low/close.
            let last = candles.last().unwrap();
            let (h, l, c) = (last.high, last.low, last.close);
            let pivot = (h + l + c) / 3.0;
            let r1 = 2.0 * pivot - l;
            let s1 = 2.0 * pivot - h;
            let r2 = pivot + (h - l);
            let s2 = pivot - (h - l);
            let r3 = h + 2.0 * (pivot - l);
            let s3 = l - 2.0 * (h - pivot);

            let close_enough = |a: f64, b: f64| (a - b).abs() <= 1e-6 * (1.0 + b.abs());
            prop_assert!(close_enough(sr.pivot, pivot), "pivot {} != {}", sr.pivot, pivot);
            prop_assert!(close_enough(sr.r1, r1), "r1 {} != {}", sr.r1, r1);
            prop_assert!(close_enough(sr.s1, s1), "s1 {} != {}", sr.s1, s1);
            prop_assert!(close_enough(sr.r2, r2), "r2 {} != {}", sr.r2, r2);
            prop_assert!(close_enough(sr.s2, s2), "s2 {} != {}", sr.s2, s2);
            prop_assert!(close_enough(sr.r3, r3), "r3 {} != {}", sr.r3, r3);
            prop_assert!(close_enough(sr.s3, s3), "s3 {} != {}", sr.s3, s3);
        }

        // Feature: deep-quant-analysis-hardening, Property 34: SR levels are
        // ordered or the exception is flagged
        // Validates: Requirements 9.2
        #[test]
        fn prop34_ordered_or_exception_flagged(
            candles in proptest::collection::vec(candle_strat(), 1..30),
            tf in timeframe_strat(),
        ) {
            let sr = compute_sr(&candles, &tf);
            let ordered = sr.s3 <= sr.s2
                && sr.s2 <= sr.s1
                && sr.s1 <= sr.pivot
                && sr.pivot <= sr.r1
                && sr.r1 <= sr.r2
                && sr.r2 <= sr.r3;
            // Either the canonical ordering holds, or the breach is flagged (R9.2).
            prop_assert!(
                ordered || sr.ordering_exception.is_some(),
                "levels neither ordered nor flagged: {:?}",
                sr
            );
        }
    }
}

// ── Property tests: SR_Engine intraday extras (R9.3) ─────────────────────────
//
// Property-based coverage for the intraday vs daily branch of `compute_sr`.
// Intraday timeframes attach the opening-range high/low and a daily macro
// pivot; the daily ("1d") timeframe omits all three.
#[cfg(test)]
mod intraday_sr_proptests {
    use super::*;
    use proptest::prelude::*;

    /// A finite OHLC component within a bounded band (keeps pivot arithmetic
    /// finite and overflow-free).
    fn finite_ohlc() -> impl Strategy<Value = f64> {
        -1.0e6..1.0e6
    }

    /// An arbitrary finite candle.
    fn candle_strat() -> impl Strategy<Value = Candle> {
        (finite_ohlc(), finite_ohlc(), finite_ohlc(), finite_ohlc(), 0.0f64..1.0e6)
            .prop_map(|(open, high, low, close, volume)| Candle {
                open,
                high,
                low,
                close,
                volume,
            })
    }

    /// The intraday timeframes the engine supports (everything except "1d").
    fn intraday_timeframe() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("1m".to_string()),
            Just("5m".to_string()),
            Just("15m".to_string()),
            Just("1h".to_string()),
            Just("4h".to_string()),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 35: Intraday SR adds
        // opening range and daily macro levels
        // Validates: Requirements 9.3
        #[test]
        fn prop35_intraday_adds_opening_range_and_daily_pivot(
            candles in proptest::collection::vec(candle_strat(), 1..30),
            tf in intraday_timeframe(),
        ) {
            // Sanity: the chosen timeframes are all classified intraday.
            prop_assert!(is_intraday(&tf), "timeframe '{}' should be intraday", tf);

            let sr = compute_sr(&candles, &tf);

            // Intraday timeframes attach all three extra levels (R9.3).
            prop_assert!(
                sr.opening_range_high.is_some(),
                "intraday tf '{}' missing opening_range_high: {:?}",
                tf,
                sr
            );
            prop_assert!(
                sr.opening_range_low.is_some(),
                "intraday tf '{}' missing opening_range_low: {:?}",
                tf,
                sr
            );
            prop_assert!(
                sr.daily_pivot.is_some(),
                "intraday tf '{}' missing daily_pivot: {:?}",
                tf,
                sr
            );

            // The same candle window on the daily timeframe omits all three.
            let daily = compute_sr(&candles, "1d");
            prop_assert!(daily.opening_range_high.is_none());
            prop_assert!(daily.opening_range_low.is_none());
            prop_assert!(daily.daily_pivot.is_none());
        }
    }
}

// ── Property tests: SR_Engine determinism (R9.4) ─────────────────────────────
//
// Property-based coverage for the purity of `compute_sr`: for any candle
// dataset and timeframe, two calls over the *identical* inputs must yield the
// identical `SrLevels`. The generator deliberately includes non-finite (NaN,
// ±infinity) and degenerate (flat / zero) OHLC so determinism is exercised on
// the same non-finite paths that set `ordering_exception`.
//
// Note on comparison: `SrLevels` derives `PartialEq`, but IEEE-754 defines
// `NaN != NaN`, so a plain `==` cannot express "identical" for NaN-bearing
// outputs. Determinism means the two calls produce the *same bit pattern* for
// every field, so we compare each `f64` by `to_bits()`. For finite outputs this
// is exactly the equality `PartialEq` would report, so the check is a strict
// superset of `compute_sr(..) == compute_sr(..)`.
#[cfg(test)]
mod sr_determinism_proptests {
    use super::*;
    use proptest::prelude::*;

    /// An OHLC component mixing the common finite band with degenerate and
    /// non-finite values so the determinism guarantee is checked across the
    /// whole input space (including the NaN/inf paths).
    fn ohlc_component() -> impl Strategy<Value = f64> {
        prop_oneof![
            8 => -1.0e6f64..1.0e6,
            1 => prop_oneof![Just(0.0f64), Just(-0.0f64)],
            1 => prop_oneof![
                Just(f64::NAN),
                Just(f64::INFINITY),
                Just(f64::NEG_INFINITY),
            ],
        ]
    }

    /// An arbitrary candle, possibly degenerate or non-finite.
    fn candle_strat() -> impl Strategy<Value = Candle> {
        (
            ohlc_component(),
            ohlc_component(),
            ohlc_component(),
            ohlc_component(),
            0.0f64..1.0e6,
        )
            .prop_map(|(open, high, low, close, volume)| Candle {
                open,
                high,
                low,
                close,
                volume,
            })
    }

    /// One of the supported timeframes (intraday + daily).
    fn timeframe_strat() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("1m".to_string()),
            Just("5m".to_string()),
            Just("15m".to_string()),
            Just("1h".to_string()),
            Just("4h".to_string()),
            Just("1d".to_string()),
        ]
    }

    /// Bit-exact equality for a single `f64` (so `NaN` bit patterns match).
    fn f64_bit_eq(a: f64, b: f64) -> bool {
        a.to_bits() == b.to_bits()
    }

    /// Bit-exact equality for the optional intraday levels.
    fn opt_f64_bit_eq(a: Option<f64>, b: Option<f64>) -> bool {
        match (a, b) {
            (Some(x), Some(y)) => f64_bit_eq(x, y),
            (None, None) => true,
            _ => false,
        }
    }

    /// Two `SrLevels` are "identical" when every field is bit-for-bit equal.
    fn sr_levels_identical(a: &SrLevels, b: &SrLevels) -> bool {
        f64_bit_eq(a.pivot, b.pivot)
            && f64_bit_eq(a.s1, b.s1)
            && f64_bit_eq(a.s2, b.s2)
            && f64_bit_eq(a.s3, b.s3)
            && f64_bit_eq(a.r1, b.r1)
            && f64_bit_eq(a.r2, b.r2)
            && f64_bit_eq(a.r3, b.r3)
            && f64_bit_eq(a.recent_high, b.recent_high)
            && f64_bit_eq(a.recent_low, b.recent_low)
            && opt_f64_bit_eq(a.opening_range_high, b.opening_range_high)
            && opt_f64_bit_eq(a.opening_range_low, b.opening_range_low)
            && opt_f64_bit_eq(a.daily_pivot, b.daily_pivot)
            && a.ordering_exception == b.ordering_exception
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 36: SR computation is
        // deterministic — repeated calls over identical inputs return identical
        // levels.
        // Validates: Requirements 9.4
        #[test]
        fn prop36_repeated_calls_are_identical(
            candles in proptest::collection::vec(candle_strat(), 0..30),
            tf in timeframe_strat(),
        ) {
            let first = compute_sr(&candles, &tf);
            let second = compute_sr(&candles, &tf);
            prop_assert!(
                sr_levels_identical(&first, &second),
                "compute_sr is non-deterministic for tf '{}': {:?} != {:?}",
                tf,
                first,
                second
            );
        }
    }
}

// ── Property tests: Consensus_Report contract (R4.2, R4.3) ───────────────────
//
// Property-based coverage for the numeric-or-null contract on the compiled
// Consensus_Report. The report is built over arbitrary finite candle windows,
// serialized to JSON, and every documented numeric field is asserted to be
// present and to be either a finite JSON number or an explicit null — never
// NaN, infinity, or missing.
#[cfg(test)]
mod consensus_proptests {
    use super::*;
    use proptest::prelude::*;

    /// A finite OHLC component within a bounded, positive-leaning band so the
    /// generated candles resemble real price data while still exercising the
    /// non-finite-projection guard paths.
    fn finite_ohlc() -> impl Strategy<Value = f64> {
        1.0f64..1.0e6
    }

    /// An arbitrary finite candle with a positive volume.
    fn candle_strat() -> impl Strategy<Value = Candle> {
        (finite_ohlc(), finite_ohlc(), finite_ohlc(), finite_ohlc(), 1.0f64..1.0e6)
            .prop_map(|(open, high, low, close, volume)| Candle {
                open,
                high,
                low,
                close,
                volume,
            })
    }

    fn timeframe_strat() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("1m".to_string()),
            Just("5m".to_string()),
            Just("15m".to_string()),
            Just("1h".to_string()),
            Just("1d".to_string()),
        ]
    }

    /// Every documented numeric field of the Consensus_Report. Each must be
    /// present in the serialized JSON and be either a finite number or null.
    const NUMERIC_FIELDS: &[&str] = &[
        "trend_score",
        "vwepr_value",
        "vwepr_slope",
        "ols_value",
        "ols_slope",
        "current_price",
        "rsi_14",
        "stoch_k",
        "ema_9",
        "ema_21",
        "sma_50",
        "sma_200",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "bb_upper",
        "bb_mid",
        "bb_lower",
        "atr_14",
        "vwap",
        "obv",
        "cmf",
        "parabolic_sar",
    ];

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 14: Consensus report
        // fields are present and numeric-or-null
        // Validates: Requirements 4.2, 4.3
        #[test]
        fn prop14_consensus_fields_numeric_or_null(
            candles in proptest::collection::vec(candle_strat(), 1..40),
            tf in timeframe_strat(),
        ) {
            let indicators = IndicatorState::from_candles_basic(&candles);
            let report = ConsensusEngine::compile_consensus("TESTSYM", &candles, &indicators, &tf);

            // Serialize through serde_json — the same path the tool server uses.
            let value = serde_json::to_value(&report)
                .expect("consensus report must serialize");
            let obj = value
                .as_object()
                .expect("consensus report serializes to a JSON object");

            for &field in NUMERIC_FIELDS {
                let entry = obj.get(field);
                // The field must be present (never missing).
                prop_assert!(
                    entry.is_some(),
                    "documented numeric field '{}' is missing from the report",
                    field
                );
                let v = entry.unwrap();
                // Each field is either an explicit null or a finite number —
                // never NaN/Inf (serde_json cannot even represent those as a
                // bare number, and the engine routes non-finite values to null).
                let ok = v.is_null()
                    || v.as_f64().map(|n| n.is_finite()).unwrap_or(false);
                prop_assert!(
                    ok,
                    "field '{}' is neither null nor a finite number: {:?}",
                    field,
                    v
                );
            }
        }
    }
}

// ── Property tests: Data-Sufficiency Classifier (R5.2) ───────────────────────
//
// Property-based coverage for the three-branch sufficiency rule. The lower
// error bound is computed with saturating subtraction so a tolerance that meets
// or exceeds `required` collapses the error band entirely.
#[cfg(test)]
mod sufficiency_proptests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 18: Data-sufficiency
        // classification follows the three-branch rule
        // Validates: Requirements 5.2
        #[test]
        fn prop18_sufficiency_three_branch_rule(
            available in 0usize..10_000,
            required in 0usize..10_000,
            tolerance in 0usize..10_000,
        ) {
            let outcome = classify_sufficiency(available, required, tolerance);

            // Lower bound of the proceed-with-warning band, using the same
            // saturating subtraction the classifier applies.
            let error_threshold = required.saturating_sub(tolerance);

            if available >= required {
                // Enough data → Ok (boundary available == required is Ok).
                prop_assert_eq!(
                    outcome,
                    SufficiencyOutcome::Ok,
                    "available {} >= required {} should be Ok",
                    available,
                    required
                );
            } else if available < error_threshold {
                // Shortfall beyond tolerance → Error.
                prop_assert_eq!(
                    outcome,
                    SufficiencyOutcome::Error,
                    "available {} < required-tolerance {} should be Error",
                    available,
                    error_threshold
                );
            } else {
                // required - tolerance <= available < required → warn + proceed.
                prop_assert!(
                    matches!(outcome, SufficiencyOutcome::ProceedWithWarning { .. }),
                    "available {} in [{}, {}) should ProceedWithWarning, got {:?}",
                    available,
                    error_threshold,
                    required,
                    outcome
                );
                prop_assert!(outcome.may_proceed());
            }
        }
    }
}
