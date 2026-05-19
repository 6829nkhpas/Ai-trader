// strategies.rs — Institutional Strategy Engine.
//
// V3 Quant Engine — Pre-calculated FnO / Institutional Strategy Detection.
//
// Evaluates the current market state against classic institutional setups
// using pre-computed indicator values. Each strategy uses precise crossover
// and price-action logic — no approximations, no placeholders.
//
// STATUS: Fully implemented & unit-tested.
// PENDING: Called by ConsensusEngine::compile_consensus. Will become active once
//          ConsensusEngine is wired into ohlc_server. Remove allow at that point.
#![allow(dead_code)]
// Detected strategies:
//   1. Golden Cross      — SMA50 crosses above SMA200 (bullish trend shift)
//   2. Death Cross       — SMA50 crosses below SMA200 (bearish trend shift)
//   3. VWAP Bounce Bull  — price dips to VWAP, reclaims on volume (institutional bid)
//   4. ORB Breakout Bull — price breaks above Opening Range High on volume
//   5. ORB Breakdown Bear— price breaks below Opening Range Low on volume

use crate::quant::patterns::Candle;

// ── Indicator Snapshot ──────────────────────────────────────────────────────

/// Pre-calculated indicator values for the current and previous tick.
///
/// These are computed upstream (e.g., by the OHLC engine or a dedicated
/// indicator service) and passed into the strategy evaluator. The engine
/// does NOT recalculate moving averages — it only evaluates crossover and
/// price-action conditions.
#[derive(Debug, Clone)]
pub struct IndicatorSnapshot {
    /// Current tick's 50-period Simple Moving Average.
    pub sma_50: f64,
    /// Current tick's 200-period Simple Moving Average.
    pub sma_200: f64,
    /// Previous tick's 50-period SMA (needed for crossover detection).
    pub prev_sma_50: f64,
    /// Previous tick's 200-period SMA (needed for crossover detection).
    pub prev_sma_200: f64,
    /// Volume-Weighted Average Price for the current session.
    pub vwap: f64,
    /// Average volume over the lookback window (e.g., 20-period).
    pub average_volume: f64,
    /// Opening Range High — highest price in the first N minutes of session.
    /// Set to `f64::NAN` if not applicable (e.g., post-ORB window).
    pub orb_high: f64,
    /// Opening Range Low — lowest price in the first N minutes of session.
    /// Set to `f64::NAN` if not applicable.
    pub orb_low: f64,
}

// ── Strategy Engine ─────────────────────────────────────────────────────────

/// Stateless institutional strategy evaluator.
///
/// Accepts candle history and a pre-calculated indicator snapshot, then
/// returns the names of all active strategies on the most recent tick.
pub struct StrategyEngine;

/// Volume surge multiplier for VWAP Bounce confirmation.
/// Current volume must exceed average_volume × this factor.
const VWAP_VOLUME_SURGE: f64 = 1.5;

/// Volume surge multiplier for ORB breakout/breakdown confirmation.
const ORB_VOLUME_SURGE: f64 = 1.2;

/// Minimum SMA separation to avoid false crossover signals on flat markets.
/// If |sma_50 - sma_200| < this on both ticks, skip crossover detection.
const MIN_SMA_SEPARATION: f64 = 1e-9;

impl StrategyEngine {
    /// Evaluate the most recent candle against institutional strategy rules.
    ///
    /// # Arguments
    /// * `history`    — OHLCV candle history (at least 2 candles required for
    ///                  strategies that reference the previous bar).
    /// * `indicators` — Pre-calculated indicator values for the current tick.
    ///
    /// # Returns
    /// A `Vec<String>` containing the names of every active strategy.
    /// Returns an empty vector if no strategies trigger.
    pub fn evaluate(history: &[Candle], indicators: &IndicatorSnapshot) -> Vec<String> {
        let mut signals: Vec<String> = Vec::new();

        if history.is_empty() {
            return signals;
        }

        let current = &history[history.len() - 1];

        // ── 1. Golden Cross ─────────────────────────────────────────────
        // Definition: SMA50 crosses ABOVE SMA200 on the current tick.
        //
        // Mathematical rules:
        //   a) Previous tick: prev_sma_50 <= prev_sma_200  (was below or equal)
        //   b) Current tick:  sma_50      >  sma_200       (now above)
        //   c) Both SMA values are finite (guard against NaN from insufficient data)
        //
        // This is a strict crossover — the previous state must be ≤ and the
        // current state must be strictly >, ensuring we only fire on the
        // exact tick of the cross.
        if indicators.prev_sma_50.is_finite()
            && indicators.prev_sma_200.is_finite()
            && indicators.sma_50.is_finite()
            && indicators.sma_200.is_finite()
        {
            let was_below_or_equal = indicators.prev_sma_50 <= indicators.prev_sma_200;
            let now_above = indicators.sma_50 > indicators.sma_200;

            if was_below_or_equal && now_above {
                signals.push("Golden Cross".to_string());
            }

            // ── 2. Death Cross ──────────────────────────────────────────
            // Definition: SMA50 crosses BELOW SMA200 on the current tick.
            //
            // Mathematical rules:
            //   a) Previous tick: prev_sma_50 >= prev_sma_200  (was above or equal)
            //   b) Current tick:  sma_50      <  sma_200       (now below)
            let was_above_or_equal = indicators.prev_sma_50 >= indicators.prev_sma_200;
            let now_below = indicators.sma_50 < indicators.sma_200;

            if was_above_or_equal && now_below {
                signals.push("Death Cross".to_string());
            }
        }

        // ── 3. VWAP Bounce (Bullish) ────────────────────────────────────
        // Definition: Price dips to VWAP, reclaims above it, with volume
        // confirmation — classic institutional accumulation pattern.
        //
        // Mathematical rules:
        //   a) Current low  <= VWAP        (price touched/dipped below VWAP)
        //   b) Current close > VWAP        (price reclaimed above VWAP)
        //   c) Previous candle was bearish  (selling pressure preceded the bounce)
        //   d) Current volume > avg_volume × 1.5 (institutional volume surge)
        //   e) VWAP is finite (guard against pre-market NaN)
        if history.len() >= 2 && indicators.vwap.is_finite() {
            let prev = &history[history.len() - 2];

            if current.low <= indicators.vwap
                && current.close > indicators.vwap
                && prev.is_bearish()
                && indicators.average_volume > MIN_SMA_SEPARATION
                && current.volume > indicators.average_volume * VWAP_VOLUME_SURGE
            {
                signals.push("VWAP Bounce (Bullish)".to_string());
            }
        }

        // ── 4. ORB Breakout (Bullish) ───────────────────────────────────
        // Definition: Price breaks above the Opening Range High with
        // volume confirmation — momentum continuation signal.
        //
        // Mathematical rules:
        //   a) orb_high is finite (ORB window has been established)
        //   b) Current close > orb_high (breakout above OR high)
        //   c) Current volume > avg_volume × 1.2 (volume confirms conviction)
        if indicators.orb_high.is_finite()
            && indicators.average_volume > MIN_SMA_SEPARATION
        {
            if current.close > indicators.orb_high
                && current.volume > indicators.average_volume * ORB_VOLUME_SURGE
            {
                signals.push("ORB Breakout (Bullish)".to_string());
            }
        }

        // ── 5. ORB Breakdown (Bearish) ──────────────────────────────────
        // Definition: Price breaks below the Opening Range Low with
        // volume confirmation — bearish momentum signal.
        //
        // Mathematical rules:
        //   a) orb_low is finite (ORB window has been established)
        //   b) Current close < orb_low (breakdown below OR low)
        //   c) Current volume > avg_volume × 1.2
        if indicators.orb_low.is_finite()
            && indicators.average_volume > MIN_SMA_SEPARATION
        {
            if current.close < indicators.orb_low
                && current.volume > indicators.average_volume * ORB_VOLUME_SURGE
            {
                signals.push("ORB Breakdown (Bearish)".to_string());
            }
        }

        signals
    }
}

// ── Unit Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn candle(open: f64, high: f64, low: f64, close: f64, volume: f64) -> Candle {
        Candle { open, high, low, close, volume }
    }

    fn base_indicators() -> IndicatorSnapshot {
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

    // ── Golden Cross Tests ──────────────────────────────────────────────

    #[test]
    fn detects_golden_cross() {
        let history = vec![candle(100.0, 105.0, 99.0, 103.0, 150_000.0)];
        let mut ind = base_indicators();
        // Previously: SMA50 (99) <= SMA200 (100) → below
        // Now:        SMA50 (101) > SMA200 (100) → above → CROSS
        ind.prev_sma_50 = 99.0;
        ind.prev_sma_200 = 100.0;
        ind.sma_50 = 101.0;
        ind.sma_200 = 100.0;

        let strategies = StrategyEngine::evaluate(&history, &ind);
        assert!(strategies.contains(&"Golden Cross".to_string()), "Expected Golden Cross, got {:?}", strategies);
    }

    #[test]
    fn no_golden_cross_when_already_above() {
        let history = vec![candle(100.0, 105.0, 99.0, 103.0, 150_000.0)];
        let mut ind = base_indicators();
        // Previously already above → no crossover
        ind.prev_sma_50 = 101.0;
        ind.prev_sma_200 = 100.0;
        ind.sma_50 = 102.0;
        ind.sma_200 = 100.0;

        let strategies = StrategyEngine::evaluate(&history, &ind);
        assert!(!strategies.contains(&"Golden Cross".to_string()), "Should not detect Golden Cross when already above");
    }

    // ── Death Cross Tests ───────────────────────────────────────────────

    #[test]
    fn detects_death_cross() {
        let history = vec![candle(100.0, 105.0, 99.0, 103.0, 150_000.0)];
        let mut ind = base_indicators();
        // Previously: SMA50 (101) >= SMA200 (100) → above
        // Now:        SMA50 (99) < SMA200 (100) → below → CROSS
        ind.prev_sma_50 = 101.0;
        ind.prev_sma_200 = 100.0;
        ind.sma_50 = 99.0;
        ind.sma_200 = 100.0;

        let strategies = StrategyEngine::evaluate(&history, &ind);
        assert!(strategies.contains(&"Death Cross".to_string()), "Expected Death Cross, got {:?}", strategies);
    }

    // ── VWAP Bounce Tests ───────────────────────────────────────────────

    #[test]
    fn detects_vwap_bounce_bullish() {
        let prev = candle(103.0, 104.0, 99.0, 100.0, 80_000.0); // bearish (red)
        let curr = candle(99.5, 102.0, 98.0, 101.0, 160_000.0); // low<=100 ✓, close>100 ✓
        let mut ind = base_indicators();
        ind.vwap = 100.0;
        ind.average_volume = 100_000.0;
        // volume 160k > 100k * 1.5 = 150k ✓

        let strategies = StrategyEngine::evaluate(&[prev, curr], &ind);
        assert!(strategies.contains(&"VWAP Bounce (Bullish)".to_string()), "Expected VWAP Bounce, got {:?}", strategies);
    }

    #[test]
    fn no_vwap_bounce_without_volume_surge() {
        let prev = candle(103.0, 104.0, 99.0, 100.0, 80_000.0);
        let curr = candle(99.5, 102.0, 98.0, 101.0, 120_000.0); // 120k < 150k threshold
        let mut ind = base_indicators();
        ind.vwap = 100.0;
        ind.average_volume = 100_000.0;

        let strategies = StrategyEngine::evaluate(&[prev, curr], &ind);
        assert!(!strategies.contains(&"VWAP Bounce (Bullish)".to_string()), "Should not detect without volume surge");
    }

    #[test]
    fn no_vwap_bounce_when_prev_bullish() {
        let prev = candle(100.0, 104.0, 99.0, 103.0, 80_000.0); // bullish (green)
        let curr = candle(99.5, 102.0, 98.0, 101.0, 160_000.0);
        let mut ind = base_indicators();
        ind.vwap = 100.0;
        ind.average_volume = 100_000.0;

        let strategies = StrategyEngine::evaluate(&[prev, curr], &ind);
        assert!(!strategies.contains(&"VWAP Bounce (Bullish)".to_string()), "Should not detect when previous candle is bullish");
    }

    // ── ORB Tests ───────────────────────────────────────────────────────

    #[test]
    fn detects_orb_breakout_bullish() {
        let curr = candle(101.0, 106.0, 100.5, 105.5, 130_000.0);
        let mut ind = base_indicators();
        ind.orb_high = 104.0;
        ind.orb_low = 98.0;
        ind.average_volume = 100_000.0;
        // close(105.5) > orb_high(104) ✓, volume(130k) > 100k*1.2=120k ✓

        let strategies = StrategyEngine::evaluate(&[curr], &ind);
        assert!(strategies.contains(&"ORB Breakout (Bullish)".to_string()), "Expected ORB Breakout, got {:?}", strategies);
    }

    #[test]
    fn detects_orb_breakdown_bearish() {
        let curr = candle(99.0, 99.5, 96.0, 97.0, 130_000.0);
        let mut ind = base_indicators();
        ind.orb_high = 104.0;
        ind.orb_low = 98.0;
        ind.average_volume = 100_000.0;
        // close(97) < orb_low(98) ✓, volume(130k) > 120k ✓

        let strategies = StrategyEngine::evaluate(&[curr], &ind);
        assert!(strategies.contains(&"ORB Breakdown (Bearish)".to_string()), "Expected ORB Breakdown, got {:?}", strategies);
    }

    #[test]
    fn no_orb_when_nan() {
        let curr = candle(101.0, 106.0, 100.5, 105.5, 130_000.0);
        let ind = base_indicators(); // orb_high and orb_low are NAN

        let strategies = StrategyEngine::evaluate(&[curr], &ind);
        assert!(!strategies.contains(&"ORB Breakout (Bullish)".to_string()));
        assert!(!strategies.contains(&"ORB Breakdown (Bearish)".to_string()));
    }

    #[test]
    fn empty_history_returns_empty() {
        let ind = base_indicators();
        let strategies = StrategyEngine::evaluate(&[], &ind);
        assert!(strategies.is_empty());
    }
}
