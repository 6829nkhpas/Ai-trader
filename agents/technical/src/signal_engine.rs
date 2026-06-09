// signal_engine.rs — Quantitative conviction score generator.
//
// Implements a weighted multi-indicator confluence model that combines four
// indicator families drawn from the Consensus_Report into a single
// `technical_conviction_score` in the range 0..=100:
//
//   * Momentum   — RSI(14), MACD histogram
//   * Trend      — EMA(9)/EMA(21) cross, price vs SMA(50)
//   * Volatility — price position within the Bollinger Bands
//   * Volume     — OBV slope, CMF, price vs VWAP
//
// Each family casts a signed directional vote in [-1, +1] computed only from
// the inputs that are present. The families' votes are combined with equal
// base weights renormalized over the present families, mapped onto [0, 100],
// and amplified when every present family agrees on direction so that aligned
// confluence always scores more extremely than any conflicting mix.
//
// Score semantics:
//   90–100  Strong bullish confluence (all families aligned bullish)
//   60–89   Bullish bias
//   41–59   Neutral / mixed
//   11–40   Bearish bias
//   0–10    Strong bearish confluence (all families aligned bearish)
//
// The conviction computation is a PURE function of `ConvictionInputs`:
// no wall-clock, no RNG, no ambient state. Identical inputs always yield an
// identical `ConvictionResult` (Requirement 8.5).

use crate::proto::technical_data::TechSignal;

// ─────────────────────────────────────────────────────────────────────────────
// Tuning constants
// ─────────────────────────────────────────────────────────────────────────────

/// RSI midpoint — above is bullish momentum, below is bearish momentum.
const RSI_MIDPOINT: f64 = 50.0;

/// Numbers whose absolute value is below this are treated as zero (neutral),
/// guarding against floating-point dust producing a spurious directional sign.
const NEUTRAL_EPSILON: f64 = 1e-9;

/// Minimum aggregate magnitude assigned when every present family agrees on
/// direction. With four equally-weighted families the most extreme aggregate a
/// *conflicting* mix can reach is strictly below 3/4 = 0.75 (one dissenting
/// family approaching a zero vote). A floor of 0.80 therefore guarantees that a
/// fully-aligned result is always more extreme than any conflicting one
/// (Requirement 8.3).
const AGREEMENT_FLOOR: f64 = 0.80;

// ─────────────────────────────────────────────────────────────────────────────
// Public data model
// ─────────────────────────────────────────────────────────────────────────────

/// The full indicator picture consumed by the conviction model. Every field is
/// optional so the engine can score from whatever subset the Consensus_Report
/// was able to compute (Requirement 8.4).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ConvictionInputs {
    // Momentum family
    pub rsi_14: Option<f64>,
    pub macd_histogram: Option<f64>,

    // Trend family
    pub ema_9: Option<f64>,
    pub ema_21: Option<f64>,
    pub sma_50: Option<f64>,

    // Shared reference price (used by trend, volatility and volume families)
    pub current_price: Option<f64>,

    // Volatility family
    pub atr_14: Option<f64>,
    pub bb_upper: Option<f64>,
    pub bb_lower: Option<f64>,

    // Volume family
    pub obv_slope: Option<f64>,
    pub cmf: Option<f64>,
    pub vwap: Option<f64>,
}

/// The result of the confluence model: an integer conviction score in
/// `0..=100` plus the names of every indicator input that was unavailable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConvictionResult {
    pub score: i32,
    pub missing_indicators: Vec<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Signed direction of a value with an explicit zero band: +1.0, -1.0, or 0.0.
fn direction(value: f64) -> f64 {
    if value > NEUTRAL_EPSILON {
        1.0
    } else if value < -NEUTRAL_EPSILON {
        -1.0
    } else {
        0.0
    }
}

/// Mean of the collected sub-votes, or `None` when no sub-vote was available
/// (meaning the family is absent and contributes no weight).
fn family_vote(sub_votes: &[f64]) -> Option<f64> {
    if sub_votes.is_empty() {
        return None;
    }
    let sum: f64 = sub_votes.iter().sum();
    Some(sum / sub_votes.len() as f64)
}

/// Momentum vote in [-1, 1] from RSI and the MACD histogram.
fn momentum_vote(inputs: &ConvictionInputs) -> Option<f64> {
    let mut votes = Vec::new();
    if let Some(rsi) = inputs.rsi_14 {
        // Map RSI onto [-1, 1] around the 50 midpoint, clamped.
        votes.push(((rsi - RSI_MIDPOINT) / RSI_MIDPOINT).clamp(-1.0, 1.0));
    }
    if let Some(hist) = inputs.macd_histogram {
        votes.push(direction(hist));
    }
    family_vote(&votes)
}

/// Trend vote in [-1, 1] from the EMA cross and price vs the SMA(50).
fn trend_vote(inputs: &ConvictionInputs) -> Option<f64> {
    let mut votes = Vec::new();
    if let (Some(ema_9), Some(ema_21)) = (inputs.ema_9, inputs.ema_21) {
        votes.push(direction(ema_9 - ema_21));
    }
    if let (Some(price), Some(sma_50)) = (inputs.current_price, inputs.sma_50) {
        votes.push(direction(price - sma_50));
    }
    family_vote(&votes)
}

/// Volatility-position vote in [-1, 1] from where price sits inside the
/// Bollinger Band channel: at/above the upper band → +1, at/below the lower
/// band → -1, mid-band → 0. A degenerate (zero-width) band yields a neutral 0.
fn volatility_vote(inputs: &ConvictionInputs) -> Option<f64> {
    if let (Some(price), Some(upper), Some(lower)) =
        (inputs.current_price, inputs.bb_upper, inputs.bb_lower)
    {
        let mid = (upper + lower) / 2.0;
        let half_width = (upper - lower) / 2.0;
        if half_width.abs() <= NEUTRAL_EPSILON {
            return Some(0.0);
        }
        return Some(((price - mid) / half_width).clamp(-1.0, 1.0));
    }
    None
}

/// Volume-flow vote in [-1, 1] from the OBV slope, CMF, and price vs VWAP.
fn volume_vote(inputs: &ConvictionInputs) -> Option<f64> {
    let mut votes = Vec::new();
    if let Some(obv_slope) = inputs.obv_slope {
        votes.push(direction(obv_slope));
    }
    if let Some(cmf) = inputs.cmf {
        votes.push(direction(cmf));
    }
    if let (Some(price), Some(vwap)) = (inputs.current_price, inputs.vwap) {
        votes.push(direction(price - vwap));
    }
    family_vote(&votes)
}

/// Collects the names of every unavailable indicator input (Requirement 8.4).
fn collect_missing(inputs: &ConvictionInputs) -> Vec<String> {
    let mut missing = Vec::new();
    let checks: [(&str, Option<f64>); 12] = [
        ("rsi_14", inputs.rsi_14),
        ("macd_histogram", inputs.macd_histogram),
        ("ema_9", inputs.ema_9),
        ("ema_21", inputs.ema_21),
        ("sma_50", inputs.sma_50),
        ("current_price", inputs.current_price),
        ("atr_14", inputs.atr_14),
        ("bb_upper", inputs.bb_upper),
        ("bb_lower", inputs.bb_lower),
        ("obv_slope", inputs.obv_slope),
        ("cmf", inputs.cmf),
        ("vwap", inputs.vwap),
    ];
    for (name, value) in checks {
        if value.is_none() {
            missing.push(name.to_string());
        }
    }
    missing
}

// ─────────────────────────────────────────────────────────────────────────────
// compute_conviction — the weighted confluence model (PURE)
// ─────────────────────────────────────────────────────────────────────────────

/// Computes the conviction score from the full indicator picture.
///
/// Algorithm (Requirements 8.1–8.5):
/// 1. Each of the four families (momentum, trend, volatility, volume) casts a
///    signed vote in `[-1, 1]` using only its available inputs. A family with
///    no available inputs is dropped (Requirement 8.4).
/// 2. Present families share equal weight, renormalized so the present weights
///    sum to 1. The weighted sum is the base aggregate in `[-1, 1]`.
/// 3. If every present family votes the same non-zero direction, the aggregate
///    magnitude is amplified to at least [`AGREEMENT_FLOOR`], guaranteeing an
///    aligned result is more extreme than any conflicting mix (Requirement 8.3).
/// 4. The aggregate is mapped onto `[0, 100]` and clamped (Requirement 8.2).
///
/// The function is pure: identical inputs always produce an identical result
/// (Requirement 8.5).
pub fn compute_conviction(inputs: &ConvictionInputs) -> ConvictionResult {
    let missing_indicators = collect_missing(inputs);

    // Step 1 — gather the present family votes (drop absent families).
    let present: Vec<f64> = [
        momentum_vote(inputs),
        trend_vote(inputs),
        volatility_vote(inputs),
        volume_vote(inputs),
    ]
    .into_iter()
    .flatten()
    .collect();

    // No directional information at all → neutral 50.
    if present.is_empty() {
        return ConvictionResult {
            score: 50,
            missing_indicators,
        };
    }

    // Step 2 — equal weights renormalized over the present families.
    let aggregate: f64 = present.iter().sum::<f64>() / present.len() as f64;

    // Step 3 — agreement amplification. Aligned ⇔ every present family votes
    // the same strict direction (none neutral, none opposing).
    let first_sign = direction(present[0]);
    let aligned = first_sign != 0.0
        && present
            .iter()
            .all(|&vote| direction(vote) == first_sign);

    let amplified = if aligned {
        // sqrt lifts the magnitude (inputs are in [0, 1]); the floor guarantees
        // it clears the maximum a conflicting mix can reach.
        let magnitude = aggregate.abs().sqrt().max(AGREEMENT_FLOOR);
        first_sign * magnitude
    } else {
        aggregate
    };

    let bounded = amplified.clamp(-1.0, 1.0);

    // Step 4 — map [-1, 1] → [0, 100].
    let score = (((bounded + 1.0) / 2.0) * 100.0).round() as i32;
    let score = score.clamp(0, 100);

    ConvictionResult {
        score,
        missing_indicators,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// evaluate_signal — Kafka tick → TechSignal bridge
// ─────────────────────────────────────────────────────────────────────────────

/// Builds a [`TechSignal`] for the live Kafka pipeline. The streaming agent only
/// has RSI, VWAP and the current price available per tick, so it scores from the
/// momentum and volume families via [`compute_conviction`]; absent families are
/// reported as missing and simply do not contribute (Requirement 8.4).
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
    let inputs = ConvictionInputs {
        rsi_14: Some(rsi),
        vwap: Some(vwap),
        current_price: Some(current_price),
        ..Default::default()
    };

    let conviction = compute_conviction(&inputs);

    // VWAP distance as a signed percentage: positive = price above VWAP.
    let vwap_distance = if vwap != 0.0 {
        ((current_price - vwap) / vwap) * 100.0
    } else {
        0.0
    };

    log::debug!(
        "[signal_engine] symbol={} rsi={:.2} vwap={:.2} price={:.2} \
         score={} missing={:?} vwap_dist={:.3}%",
        symbol,
        rsi,
        vwap,
        current_price,
        conviction.score,
        conviction.missing_indicators,
        vwap_distance
    );

    TechSignal {
        symbol: symbol.to_string(),
        timestamp_ms,
        rsi_value: rsi,
        vwap_distance,
        technical_conviction_score: conviction.score,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const TS: i64 = 1_700_000_000_000;

    /// A fully bullish input set across all four families produces an extreme
    /// high score (>= 90).
    #[test]
    fn all_families_bullish_scores_extreme_high() {
        let inputs = ConvictionInputs {
            rsi_14: Some(65.0),
            macd_histogram: Some(1.2),
            ema_9: Some(105.0),
            ema_21: Some(100.0),
            sma_50: Some(95.0),
            current_price: Some(110.0),
            atr_14: Some(2.0),
            bb_upper: Some(112.0),
            bb_lower: Some(90.0),
            obv_slope: Some(500.0),
            cmf: Some(0.3),
            vwap: Some(102.0),
        };
        let result = compute_conviction(&inputs);
        assert!(result.score >= 90, "expected >= 90, got {}", result.score);
        assert!(result.missing_indicators.is_empty());
    }

    /// A fully bearish input set across all four families produces an extreme
    /// low score (<= 10).
    #[test]
    fn all_families_bearish_scores_extreme_low() {
        let inputs = ConvictionInputs {
            rsi_14: Some(35.0),
            macd_histogram: Some(-1.2),
            ema_9: Some(95.0),
            ema_21: Some(100.0),
            sma_50: Some(105.0),
            current_price: Some(90.0),
            atr_14: Some(2.0),
            bb_upper: Some(110.0),
            bb_lower: Some(88.0),
            obv_slope: Some(-500.0),
            cmf: Some(-0.3),
            vwap: Some(98.0),
        };
        let result = compute_conviction(&inputs);
        assert!(result.score <= 10, "expected <= 10, got {}", result.score);
    }

    /// Aligned families score more extremely than a conflicting mix
    /// (Requirement 8.3).
    #[test]
    fn aligned_more_extreme_than_conflicting() {
        let aligned = ConvictionInputs {
            rsi_14: Some(60.0),
            macd_histogram: Some(0.5),
            ema_9: Some(101.0),
            ema_21: Some(100.0),
            sma_50: Some(99.0),
            current_price: Some(105.0),
            bb_upper: Some(106.0),
            bb_lower: Some(96.0),
            obv_slope: Some(10.0),
            cmf: Some(0.1),
            vwap: Some(100.0),
            ..Default::default()
        };
        // Conflicting: momentum/volume bullish, trend/volatility bearish.
        let conflicting = ConvictionInputs {
            rsi_14: Some(70.0),
            macd_histogram: Some(1.0),
            ema_9: Some(95.0),
            ema_21: Some(100.0),
            sma_50: Some(105.0),
            current_price: Some(94.0),
            bb_upper: Some(110.0),
            bb_lower: Some(96.0),
            obv_slope: Some(50.0),
            cmf: Some(0.4),
            vwap: Some(90.0),
            ..Default::default()
        };
        let aligned_score = compute_conviction(&aligned).score;
        let conflicting_score = compute_conviction(&conflicting).score;
        assert!(
            (aligned_score - 50).abs() > (conflicting_score - 50).abs(),
            "aligned {} should be more extreme than conflicting {}",
            aligned_score,
            conflicting_score
        );
    }

    /// Score is always within [0, 100] inclusive (Requirement 8.2).
    #[test]
    fn score_within_bounds() {
        let extreme = ConvictionInputs {
            rsi_14: Some(100.0),
            macd_histogram: Some(1e12),
            ema_9: Some(1e9),
            ema_21: Some(0.0),
            sma_50: Some(0.0),
            current_price: Some(1e9),
            bb_upper: Some(1e9),
            bb_lower: Some(-1e9),
            obv_slope: Some(1e12),
            cmf: Some(1e9),
            vwap: Some(0.0),
            ..Default::default()
        };
        let result = compute_conviction(&extreme);
        assert!((0..=100).contains(&result.score));
    }

    /// Missing indicators are tolerated and reported (Requirement 8.4).
    #[test]
    fn missing_indicators_reported() {
        let inputs = ConvictionInputs {
            rsi_14: Some(60.0),
            current_price: Some(105.0),
            vwap: Some(100.0),
            ..Default::default()
        };
        let result = compute_conviction(&inputs);
        // Score is still produced from the available momentum/volume inputs.
        assert!((0..=100).contains(&result.score));
        // All nine unset fields are reported as missing.
        assert!(result.missing_indicators.contains(&"macd_histogram".to_string()));
        assert!(result.missing_indicators.contains(&"ema_9".to_string()));
        assert!(result.missing_indicators.contains(&"sma_50".to_string()));
        assert!(result.missing_indicators.contains(&"cmf".to_string()));
        assert_eq!(result.missing_indicators.len(), 9);
    }

    /// No available indicators at all → neutral 50 with everything missing.
    #[test]
    fn no_inputs_is_neutral() {
        let result = compute_conviction(&ConvictionInputs::default());
        assert_eq!(result.score, 50);
        assert_eq!(result.missing_indicators.len(), 12);
    }

    /// Identical inputs always yield identical results (Requirement 8.5).
    #[test]
    fn deterministic_for_identical_inputs() {
        let inputs = ConvictionInputs {
            rsi_14: Some(57.3),
            macd_histogram: Some(0.42),
            ema_9: Some(101.1),
            ema_21: Some(100.7),
            sma_50: Some(99.4),
            current_price: Some(102.6),
            atr_14: Some(1.8),
            bb_upper: Some(104.0),
            bb_lower: Some(98.0),
            obv_slope: Some(12.0),
            cmf: Some(0.05),
            vwap: Some(101.0),
        };
        let a = compute_conviction(&inputs);
        let b = compute_conviction(&inputs);
        assert_eq!(a, b);
    }

    // ── evaluate_signal bridge tests ──────────────────────────────────────────

    /// Bullish RSI + price above VWAP → bullish score (> 50).
    #[test]
    fn evaluate_signal_bullish_bias() {
        let sig = evaluate_signal("RELIANCE", 65.0, 2_400.0, 2_450.0, TS);
        assert!(
            sig.technical_conviction_score > 50,
            "expected bullish > 50, got {}",
            sig.technical_conviction_score
        );
        assert!(sig.vwap_distance > 0.0);
        assert_eq!(sig.rsi_value, 65.0);
    }

    /// Bearish RSI + price below VWAP → bearish score (< 50).
    #[test]
    fn evaluate_signal_bearish_bias() {
        let sig = evaluate_signal("INFY", 35.0, 1_500.0, 1_450.0, TS);
        assert!(
            sig.technical_conviction_score < 50,
            "expected bearish < 50, got {}",
            sig.technical_conviction_score
        );
        assert!(sig.vwap_distance < 0.0);
    }

    /// Symbol and timestamp are faithfully propagated to the TechSignal.
    #[test]
    fn fields_propagated_correctly() {
        let sig = evaluate_signal("SBIN", 50.0, 600.0, 605.0, TS);
        assert_eq!(sig.symbol, "SBIN");
        assert_eq!(sig.timestamp_ms, TS);
    }

    /// VWAP distance is computed as a signed percentage.
    #[test]
    fn vwap_distance_calculation() {
        // price = 110, vwap = 100 → distance = +10%
        let sig = evaluate_signal("TEST", 50.0, 100.0, 110.0, TS);
        assert!(
            (sig.vwap_distance - 10.0).abs() < 1e-9,
            "Expected +10.0%, got {}",
            sig.vwap_distance
        );
    }

    /// A zero VWAP must not produce a non-finite distance.
    #[test]
    fn zero_vwap_safe() {
        let sig = evaluate_signal("ZERO", 50.0, 0.0, 100.0, TS);
        assert_eq!(sig.vwap_distance, 0.0);
        assert!((0..=100).contains(&sig.technical_conviction_score));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Property-based tests — deep-quant-analysis-hardening conviction scoring
//
// Implements design Properties 28–32 over `compute_conviction`. Each property
// runs proptest with `cases = 100` and is tagged with its design property.
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod conviction_proptests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 28: Conviction score
        // depends on all four indicator families.
        //
        // Validates: Requirements 8.1
        //
        // Strategy: build a strongly-bullish baseline across all four families,
        // then flip each family individually from bullish to bearish. Flipping
        // any single family must change the resulting score, proving the score
        // genuinely depends on momentum, trend, volatility, and volume inputs.
        #[test]
        fn prop28_score_depends_on_all_four_families(
            // Magnitudes kept comfortably away from the neutral epsilon so each
            // family casts an unambiguous directional vote.
            rsi_mag in 11.0f64..40.0,
            macd_mag in 0.1f64..100.0,
            ema_gap in 0.1f64..50.0,
            sma_gap in 0.1f64..50.0,
            bb_off in 0.1f64..0.9,      // fraction of half-band away from mid
            half_band in 1.0f64..100.0,
            obv_mag in 0.1f64..1e6,
            cmf_mag in 0.01f64..0.9,
            vwap_gap in 0.1f64..50.0,
        ) {
            let price = 100.0;
            let mid = price; // center the BB channel on price; offset drives the vote

            // Bullish baseline: every family votes +1 direction.
            let bullish = ConvictionInputs {
                rsi_14: Some(RSI_MIDPOINT + rsi_mag),
                macd_histogram: Some(macd_mag),
                ema_9: Some(100.0 + ema_gap),
                ema_21: Some(100.0),
                sma_50: Some(price - sma_gap),
                current_price: Some(price),
                atr_14: Some(2.0),
                bb_upper: Some(mid + half_band),
                bb_lower: Some(mid - half_band),
                obv_slope: Some(obv_mag),
                cmf: Some(cmf_mag),
                vwap: Some(price - vwap_gap),
            };

            // The bullish baseline price must sit above mid for a bullish
            // volatility vote — nudge price up within the band.
            let bull_price = mid + bb_off * half_band;
            let bullish = ConvictionInputs {
                current_price: Some(bull_price),
                // keep trend/volume references consistent with the nudged price
                sma_50: Some(bull_price - sma_gap),
                vwap: Some(bull_price - vwap_gap),
                ..bullish
            };
            let base_score = compute_conviction(&bullish).score;

            // Flip momentum family bearish.
            let flip_momentum = ConvictionInputs {
                rsi_14: Some(RSI_MIDPOINT - rsi_mag),
                macd_histogram: Some(-macd_mag),
                ..bullish
            };
            // Flip trend family bearish.
            let flip_trend = ConvictionInputs {
                ema_9: Some(100.0 - ema_gap),
                ema_21: Some(100.0),
                sma_50: Some(bull_price + sma_gap),
                ..bullish
            };
            // Flip volatility family bearish (price below mid).
            let bear_price = mid - bb_off * half_band;
            let flip_volatility = ConvictionInputs {
                current_price: Some(bear_price),
                // keep trend & volume references bullish relative to bear_price
                sma_50: Some(bear_price - sma_gap),
                vwap: Some(bear_price - vwap_gap),
                ..bullish
            };
            // Flip volume family bearish.
            let flip_volume = ConvictionInputs {
                obv_slope: Some(-obv_mag),
                cmf: Some(-cmf_mag),
                vwap: Some(bull_price + vwap_gap),
                ..bullish
            };

            prop_assert_ne!(
                compute_conviction(&flip_momentum).score, base_score,
                "flipping momentum did not change the score"
            );
            prop_assert_ne!(
                compute_conviction(&flip_trend).score, base_score,
                "flipping trend did not change the score"
            );
            prop_assert_ne!(
                compute_conviction(&flip_volatility).score, base_score,
                "flipping volatility did not change the score"
            );
            prop_assert_ne!(
                compute_conviction(&flip_volume).score, base_score,
                "flipping volume did not change the score"
            );
        }
    }

    /// Generator producing arbitrary finite-or-absent values for every
    /// `ConvictionInputs` field, covering present/missing subsets and a wide
    /// finite numeric range (including extremes and negatives).
    fn arb_opt_finite() -> impl Strategy<Value = Option<f64>> {
        prop_oneof![
            // ~1/5 of the time the field is absent.
            Just(None),
            (-1e9f64..1e9).prop_map(Some),
        ]
    }

    fn arb_inputs() -> impl Strategy<Value = ConvictionInputs> {
        (
            // proptest tuples max out at 12 elements — exactly our field count.
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
            arb_opt_finite(),
        )
            .prop_map(
                |(
                    rsi_14,
                    macd_histogram,
                    ema_9,
                    ema_21,
                    sma_50,
                    current_price,
                    atr_14,
                    bb_upper,
                    bb_lower,
                    obv_slope,
                    cmf,
                    vwap,
                )| ConvictionInputs {
                    rsi_14,
                    macd_histogram,
                    ema_9,
                    ema_21,
                    sma_50,
                    current_price,
                    atr_14,
                    bb_upper,
                    bb_lower,
                    obv_slope,
                    cmf,
                    vwap,
                },
            )
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 29: Conviction score
        // stays within [0, 100].
        //
        // Validates: Requirements 8.2
        #[test]
        fn prop29_score_within_bounds(inputs in arb_inputs()) {
            let result = compute_conviction(&inputs);
            prop_assert!(
                (0..=100).contains(&result.score),
                "score {} out of [0, 100]",
                result.score
            );
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 30: Aligned indicators
        // produce more extreme scores than conflicting ones.
        //
        // Validates: Requirements 8.3
        //
        // Strategy: construct an all-aligned-bullish input and a conflicting
        // input that shares the same momentum/volume bullish votes but flips
        // trend and volatility bearish. The aligned score's distance from the
        // neutral midpoint (50) must strictly exceed the conflicting score's.
        #[test]
        fn prop30_aligned_more_extreme_than_conflicting(
            rsi_mag in 11.0f64..40.0,
            macd_mag in 0.1f64..100.0,
            ema_gap in 0.5f64..50.0,
            sma_gap in 0.5f64..50.0,
            half_band in 1.0f64..100.0,
            bb_off in 0.2f64..0.9,
            obv_mag in 0.1f64..1e6,
            cmf_mag in 0.01f64..0.9,
            vwap_gap in 0.5f64..50.0,
        ) {
            let mid = 100.0;
            let bull_price = mid + bb_off * half_band;
            let bear_price = mid - bb_off * half_band;

            // Fully aligned bullish across all four families.
            let aligned = ConvictionInputs {
                rsi_14: Some(RSI_MIDPOINT + rsi_mag),
                macd_histogram: Some(macd_mag),
                ema_9: Some(100.0 + ema_gap),
                ema_21: Some(100.0),
                sma_50: Some(bull_price - sma_gap),
                current_price: Some(bull_price),
                atr_14: Some(2.0),
                bb_upper: Some(mid + half_band),
                bb_lower: Some(mid - half_band),
                obv_slope: Some(obv_mag),
                cmf: Some(cmf_mag),
                vwap: Some(bull_price - vwap_gap),
            };

            // Conflicting: momentum + volume bullish, trend + volatility bearish.
            let conflicting = ConvictionInputs {
                rsi_14: Some(RSI_MIDPOINT + rsi_mag),
                macd_histogram: Some(macd_mag),
                ema_9: Some(100.0 - ema_gap),
                ema_21: Some(100.0),
                sma_50: Some(bear_price + sma_gap),
                current_price: Some(bear_price),
                atr_14: Some(2.0),
                bb_upper: Some(mid + half_band),
                bb_lower: Some(mid - half_band),
                obv_slope: Some(obv_mag),
                cmf: Some(cmf_mag),
                vwap: Some(bear_price - vwap_gap),
            };

            let aligned_dist = (compute_conviction(&aligned).score - 50).abs();
            let conflicting_dist = (compute_conviction(&conflicting).score - 50).abs();
            prop_assert!(
                aligned_dist > conflicting_dist,
                "aligned distance {} should exceed conflicting distance {}",
                aligned_dist,
                conflicting_dist
            );
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 31: Missing indicators
        // are tolerated and reported.
        //
        // Validates: Requirements 8.4
        //
        // Strategy: arbitrary present/absent subsets must never panic, must
        // still yield an in-range score, and `missing_indicators` must list
        // exactly the absent fields — no more, no less.
        #[test]
        fn prop31_missing_indicators_tolerated_and_reported(inputs in arb_inputs()) {
            let result = compute_conviction(&inputs);

            // No panic + valid score even for arbitrary subsets.
            prop_assert!((0..=100).contains(&result.score));

            // Compute the ground-truth set of absent field names.
            let mut expected: Vec<String> = Vec::new();
            let checks: [(&str, Option<f64>); 12] = [
                ("rsi_14", inputs.rsi_14),
                ("macd_histogram", inputs.macd_histogram),
                ("ema_9", inputs.ema_9),
                ("ema_21", inputs.ema_21),
                ("sma_50", inputs.sma_50),
                ("current_price", inputs.current_price),
                ("atr_14", inputs.atr_14),
                ("bb_upper", inputs.bb_upper),
                ("bb_lower", inputs.bb_lower),
                ("obv_slope", inputs.obv_slope),
                ("cmf", inputs.cmf),
                ("vwap", inputs.vwap),
            ];
            for (name, value) in checks {
                if value.is_none() {
                    expected.push(name.to_string());
                }
            }

            let mut got = result.missing_indicators.clone();
            got.sort();
            expected.sort();
            prop_assert_eq!(
                got,
                expected,
                "missing_indicators must list exactly the absent fields"
            );
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 32: Conviction scoring
        // is deterministic.
        //
        // Validates: Requirements 8.5
        //
        // Strategy: scoring the same inputs twice must yield byte-identical
        // results (score and missing_indicators), confirming purity — no clock,
        // RNG, or ambient state leaks into the computation.
        #[test]
        fn prop32_scoring_is_deterministic(inputs in arb_inputs()) {
            let a = compute_conviction(&inputs);
            let b = compute_conviction(&inputs);
            prop_assert_eq!(a, b, "identical inputs must yield identical results");
        }
    }
}
