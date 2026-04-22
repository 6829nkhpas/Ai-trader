// signal_engine.rs — Quantitative conviction score generator.
//
// Implements a simple confluence algorithm combining RSI and VWAP into a
// `technical_conviction_score` field on a `TechSignal` Protobuf.
//
// Score semantics:
//   85–100  Strong bullish confluence (oversold RSI + price above VWAP)
//   60–84   Moderate bullish bias
//   41–59   Neutral / indeterminate
//   16–40   Moderate bearish bias
//   1–15    Strong bearish confluence (overbought RSI + price below VWAP)
//
// All thresholds are conservative and intentionally simple for Phase 1.3.
// Subsequent phases will layer in EMA cross-overs, Bollinger Band position,
// and volume-weighted momentum to refine the score further.

use crate::proto::technical_data::TechSignal;

// ─────────────────────────────────────────────────────────────────────────────
// Threshold constants
// ─────────────────────────────────────────────────────────────────────────────

/// RSI below this level → asset is considered oversold (bullish signal).
const RSI_OVERSOLD: f64 = 30.0;

/// RSI above this level → asset is considered overbought (bearish signal).
const RSI_OVERBOUGHT: f64 = 70.0;

/// Mild RSI oversold zone (for moderate bullish classification).
const RSI_MILD_OVERSOLD: f64 = 45.0;

/// Mild RSI overbought zone (for moderate bearish classification).
const RSI_MILD_OVERBOUGHT: f64 = 55.0;

// ─────────────────────────────────────────────────────────────────────────────
// evaluate_signal
// ─────────────────────────────────────────────────────────────────────────────

/// Applies the confluence algorithm and returns a populated [`TechSignal`].
///
/// ## Conviction Score Logic
///
/// | Condition                                    | Score |
/// |----------------------------------------------|-------|
/// | RSI < 30 **and** price > VWAP                | 85    |
/// | RSI < 30 **and** price ≤ VWAP                | 65    |
/// | RSI < 45 **and** price > VWAP                | 62    |
/// | RSI > 70 **and** price < VWAP                | 15    |
/// | RSI > 70 **and** price ≥ VWAP                | 35    |
/// | RSI > 55 **and** price < VWAP                | 38    |
/// | Everything else (neutral zone)               | 50    |
///
/// ## VWAP Distance
/// Reported as a signed percentage: positive = price above VWAP (bullish),
/// negative = price below VWAP (bearish).
///
/// ```text
/// vwap_distance = ((current_price - vwap) / vwap) * 100.0
/// ```
///
/// # Arguments
/// - `symbol`        — NSE ticker symbol string.
/// - `rsi`           — current RSI value in `[0.0, 100.0]`.
/// - `vwap`          — current intraday VWAP.
/// - `current_price` — the latest `last_traded_price` from the Tick.
/// - `timestamp_ms`  — Unix epoch milliseconds from the originating Tick.
pub fn evaluate_signal(
    symbol: &str,
    rsi: f64,
    vwap: f64,
    current_price: f64,
    timestamp_ms: i64,
) -> TechSignal {
    let price_above_vwap = current_price > vwap;

    // ── Compute the conviction score via confluence rules ─────────────────────
    let technical_conviction_score: i32 = if rsi < RSI_OVERSOLD && price_above_vwap {
        // Strongest bullish case: deeply oversold + trading above VWAP
        85
    } else if rsi < RSI_OVERSOLD && !price_above_vwap {
        // Oversold but price below VWAP — bullish RSI, bearish momentum
        65
    } else if rsi < RSI_MILD_OVERSOLD && price_above_vwap {
        // Moderately oversold with bullish price action
        62
    } else if rsi > RSI_OVERBOUGHT && !price_above_vwap {
        // Strongest bearish case: deeply overbought + trading below VWAP
        15
    } else if rsi > RSI_OVERBOUGHT && price_above_vwap {
        // Overbought but price above VWAP — bearish RSI, bullish momentum
        35
    } else if rsi > RSI_MILD_OVERBOUGHT && !price_above_vwap {
        // Moderately overbought with bearish price action
        38
    } else {
        // Neutral — no strong confluence in either direction
        50
    };

    // ── Compute VWAP distance as a signed percentage ──────────────────────────
    let vwap_distance = if vwap != 0.0 {
        ((current_price - vwap) / vwap) * 100.0
    } else {
        0.0
    };

    log::debug!(
        "[signal_engine] symbol={} rsi={:.2} vwap={:.2} price={:.2} \
         above_vwap={} score={} vwap_dist={:.3}%",
        symbol,
        rsi,
        vwap,
        current_price,
        price_above_vwap,
        technical_conviction_score,
        vwap_distance
    );

    // ── Assemble and return the TechSignal Protobuf struct ────────────────────
    TechSignal {
        symbol: symbol.to_string(),
        timestamp_ms,
        rsi_value: rsi,
        vwap_distance,
        technical_conviction_score,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const TS: i64 = 1_700_000_000_000;

    /// Strong bullish: RSI < 30 and price above VWAP → score 85.
    #[test]
    fn strong_bullish_signal() {
        let sig = evaluate_signal("RELIANCE", 25.0, 2_400.0, 2_450.0, TS);
        assert_eq!(sig.technical_conviction_score, 85);
        assert!(sig.vwap_distance > 0.0, "Price is above VWAP, distance must be positive");
        assert_eq!(sig.rsi_value, 25.0);
    }

    /// Strong bearish: RSI > 70 and price below VWAP → score 15.
    #[test]
    fn strong_bearish_signal() {
        let sig = evaluate_signal("INFY", 75.0, 1_500.0, 1_450.0, TS);
        assert_eq!(sig.technical_conviction_score, 15);
        assert!(sig.vwap_distance < 0.0, "Price is below VWAP, distance must be negative");
    }

    /// Neutral zone → score 50.
    #[test]
    fn neutral_signal() {
        let sig = evaluate_signal("TCS", 50.0, 3_000.0, 3_010.0, TS);
        assert_eq!(sig.technical_conviction_score, 50);
    }

    /// Overbought RSI but price still above VWAP → moderate bearish (35).
    #[test]
    fn overbought_above_vwap() {
        let sig = evaluate_signal("HDFCBANK", 72.0, 1_600.0, 1_620.0, TS);
        assert_eq!(sig.technical_conviction_score, 35);
    }

    /// Oversold RSI but price below VWAP → moderate bullish (65).
    #[test]
    fn oversold_below_vwap() {
        let sig = evaluate_signal("WIPRO", 28.0, 500.0, 490.0, TS);
        assert_eq!(sig.technical_conviction_score, 65);
    }

    /// Symbol and timestamp are faithfully propagated to the TechSignal.
    #[test]
    fn fields_propagated_correctly() {
        let sig = evaluate_signal("SBIN", 50.0, 600.0, 605.0, TS);
        assert_eq!(sig.symbol, "SBIN");
        assert_eq!(sig.timestamp_ms, TS);
    }

    /// VWAP distance is approximately correct.
    #[test]
    fn vwap_distance_calculation() {
        // price = 110, vwap = 100 → distance = +10%
        let sig = evaluate_signal("TEST", 50.0, 100.0, 110.0, TS);
        let expected = 10.0_f64;
        assert!(
            (sig.vwap_distance - expected).abs() < 1e-9,
            "Expected +10.0%, got {}",
            sig.vwap_distance
        );
    }
}
