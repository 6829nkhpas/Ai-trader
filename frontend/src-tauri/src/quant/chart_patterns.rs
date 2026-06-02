// quant/chart_patterns.rs — Institutional-Grade Chart Pattern Detection Engine.
//
// Detects 19 structural chart patterns across any timeframe by:
//   1. Identifying swing highs/lows (local extrema) using a rolling window.
//   2. Building a cleaned structural skeleton (alternating Peak → Trough → Peak).
//   3. Fitting support/resistance trendlines via linear regression.
//   4. Evaluating geometric rules against the 19 pattern archetypes.
//
// Pattern Categories:
//   Reversal (8):     Head & Shoulders, Inverse H&S, Double Top/Bottom,
//                     Triple Top/Bottom, Rising Wedge, Falling Wedge
//   Continuation (6): Bullish/Bearish Flag, Bullish/Bearish Pennant,
//                     Cup and Handle, Inverse Cup and Handle
//   Bilateral (4):    Symmetrical Triangle, Ascending Triangle,
//                     Descending Triangle, Rectangle

use super::patterns::Candle;

// ── Chart Pattern Data Structures ──────────────────────────────────────────

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ChartPattern {
    pub pattern_type: String,
    pub sentiment: String, // "Bullish", "Bearish", "Neutral"
    pub confidence: f64,   // 0.0 to 1.0
    pub start_idx: usize,
    pub end_idx: usize,
    pub description: String,
}

/// A swing point in the price series: either a local high (Peak) or low (Trough).
#[derive(Debug, Clone, Copy)]
struct SwingPoint {
    idx: usize,
    price: f64,
    kind: SwingKind,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum SwingKind {
    Peak,
    Trough,
}

// ── Configuration Constants ────────────────────────────────────────────────

/// Rolling window half-size for swing detection. A candle is a swing high if it
/// is the highest high in [i - SWING_WINDOW .. i + SWING_WINDOW].
const SWING_WINDOW: usize = 5;

/// Tolerance for "matching" peak/trough heights (e.g. double top within 1.5%).
const MATCH_TOLERANCE: f64 = 0.015;

/// Shoulder symmetry tolerance for Head & Shoulders patterns (3%).
const SHOULDER_TOLERANCE: f64 = 0.03;

/// Minimum number of candles in the flagpole for flag/pennant detection.
const MIN_FLAGPOLE_CANDLES: usize = 5;

/// Minimum flagpole body-range ratio (flagpole should cover a substantial range).
const FLAGPOLE_MIN_RANGE_RATIO: f64 = 0.02;

/// Rectangle: maximum slope magnitude for "flat" trendlines.
const FLAT_SLOPE_THRESHOLD: f64 = 0.0005;

/// Cup-and-handle: maximum asymmetry between cup sides.
const CUP_ASYMMETRY_TOLERANCE: f64 = 0.05;

// ── Chart Pattern Engine ───────────────────────────────────────────────────

pub struct ChartPatternEngine;

impl ChartPatternEngine {
    /// Analyze a slice of candles and return all detected chart patterns.
    pub fn analyze(candles: &[Candle]) -> Vec<ChartPattern> {
        if candles.len() < 20 {
            return Vec::new();
        }

        // Step 1: Identify raw swing points
        let raw_swings = Self::find_swings(candles);
        if raw_swings.len() < 3 {
            return Vec::new();
        }

        // Step 2: Build alternating skeleton (Peak → Trough → Peak → ...)
        let swings = Self::alternate_swings(&raw_swings);
        if swings.len() < 3 {
            return Vec::new();
        }

        let mut patterns: Vec<ChartPattern> = Vec::new();

        // Step 3: Evaluate all pattern archetypes
        Self::detect_head_and_shoulders(&swings, &mut patterns);
        Self::detect_inverse_head_and_shoulders(&swings, &mut patterns);
        Self::detect_double_top(&swings, &mut patterns);
        Self::detect_double_bottom(&swings, &mut patterns);
        Self::detect_triple_top(&swings, &mut patterns);
        Self::detect_triple_bottom(&swings, &mut patterns);
        Self::detect_rising_wedge(&swings, &mut patterns);
        Self::detect_falling_wedge(&swings, &mut patterns);
        Self::detect_flags_and_pennants(candles, &swings, &mut patterns);
        Self::detect_cup_and_handle(&swings, &mut patterns);
        Self::detect_inverse_cup_and_handle(&swings, &mut patterns);
        Self::detect_triangles(&swings, &mut patterns);
        Self::detect_rectangle(&swings, &mut patterns);

        patterns
    }

    // ── Swing Detection ────────────────────────────────────────────────────

    fn find_swings(candles: &[Candle]) -> Vec<SwingPoint> {
        let mut swings = Vec::new();
        let n = candles.len();
        if n < SWING_WINDOW * 2 + 1 {
            return swings;
        }

        for i in SWING_WINDOW..(n - SWING_WINDOW) {
            let mut is_high = true;
            let mut is_low = true;

            for j in (i.saturating_sub(SWING_WINDOW))..=(i + SWING_WINDOW).min(n - 1) {
                if j == i {
                    continue;
                }
                if candles[j].high >= candles[i].high {
                    is_high = false;
                }
                if candles[j].low <= candles[i].low {
                    is_low = false;
                }
            }

            if is_high {
                swings.push(SwingPoint {
                    idx: i,
                    price: candles[i].high,
                    kind: SwingKind::Peak,
                });
            }
            if is_low {
                swings.push(SwingPoint {
                    idx: i,
                    price: candles[i].low,
                    kind: SwingKind::Trough,
                });
            }
        }

        // Sort by index
        swings.sort_by_key(|s| s.idx);
        swings
    }

    /// Clean swings so they strictly alternate: Peak → Trough → Peak → ...
    /// When two consecutive swings of the same kind appear, keep the more extreme one.
    fn alternate_swings(raw: &[SwingPoint]) -> Vec<SwingPoint> {
        if raw.is_empty() {
            return Vec::new();
        }

        let mut result: Vec<SwingPoint> = vec![raw[0]];

        for sp in &raw[1..] {
            let last = result.last().unwrap();
            if sp.kind == last.kind {
                // Same kind: keep the more extreme
                let better = match sp.kind {
                    SwingKind::Peak => sp.price > last.price,
                    SwingKind::Trough => sp.price < last.price,
                };
                if better {
                    *result.last_mut().unwrap() = *sp;
                }
            } else {
                result.push(*sp);
            }
        }

        result
    }

    // ── Helper: price match within tolerance ───────────────────────────────

    #[inline]
    fn prices_match(a: f64, b: f64, tolerance: f64) -> bool {
        let avg = (a + b) / 2.0;
        if avg.abs() < 1e-9 {
            return (a - b).abs() < 1e-9;
        }
        ((a - b).abs() / avg) <= tolerance
    }

    /// Simple linear regression: returns (slope, intercept) for points (x, y).
    fn linear_regression(points: &[(f64, f64)]) -> (f64, f64) {
        let n = points.len() as f64;
        if n < 2.0 {
            return (0.0, points.first().map(|p| p.1).unwrap_or(0.0));
        }
        let sum_x: f64 = points.iter().map(|p| p.0).sum();
        let sum_y: f64 = points.iter().map(|p| p.1).sum();
        let sum_xy: f64 = points.iter().map(|p| p.0 * p.1).sum();
        let sum_xx: f64 = points.iter().map(|p| p.0 * p.0).sum();

        let denom = n * sum_xx - sum_x * sum_x;
        if denom.abs() < 1e-12 {
            return (0.0, sum_y / n);
        }
        let slope = (n * sum_xy - sum_x * sum_y) / denom;
        let intercept = (sum_y - slope * sum_x) / n;
        (slope, intercept)
    }

    // ── Reversal Patterns ──────────────────────────────────────────────────

    /// Head and Shoulders: 3 peaks where the middle peak is highest,
    /// and the two shoulders are within SHOULDER_TOLERANCE of each other.
    fn detect_head_and_shoulders(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<&SwingPoint> = swings.iter().filter(|s| s.kind == SwingKind::Peak).collect();
        if peaks.len() < 3 {
            return;
        }

        for window in peaks.windows(3) {
            let (left, head, right) = (window[0], window[1], window[2]);

            // Head must be the highest
            if head.price <= left.price || head.price <= right.price {
                continue;
            }

            // Shoulders should be roughly equal
            if !Self::prices_match(left.price, right.price, SHOULDER_TOLERANCE) {
                continue;
            }

            // Confidence based on how much higher the head is
            let shoulder_avg = (left.price + right.price) / 2.0;
            let head_prominence = (head.price - shoulder_avg) / shoulder_avg;
            let shoulder_symmetry =
                1.0 - ((left.price - right.price).abs() / shoulder_avg);
            let confidence = (0.5 + head_prominence.min(0.3) + shoulder_symmetry * 0.2).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Head and Shoulders".to_string(),
                sentiment: "Bearish".to_string(),
                confidence,
                start_idx: left.idx,
                end_idx: right.idx,
                description: format!(
                    "H&S: left shoulder {:.2}, head {:.2}, right shoulder {:.2}. \
                     Neckline break confirms bearish reversal.",
                    left.price, head.price, right.price
                ),
            });
        }
    }

    /// Inverse Head and Shoulders: 3 troughs where the middle is the lowest.
    fn detect_inverse_head_and_shoulders(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let troughs: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Trough).collect();
        if troughs.len() < 3 {
            return;
        }

        for window in troughs.windows(3) {
            let (left, head, right) = (window[0], window[1], window[2]);

            if head.price >= left.price || head.price >= right.price {
                continue;
            }

            if !Self::prices_match(left.price, right.price, SHOULDER_TOLERANCE) {
                continue;
            }

            let shoulder_avg = (left.price + right.price) / 2.0;
            let head_depth = (shoulder_avg - head.price) / shoulder_avg;
            let shoulder_symmetry =
                1.0 - ((left.price - right.price).abs() / shoulder_avg);
            let confidence = (0.5 + head_depth.min(0.3) + shoulder_symmetry * 0.2).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Inverse Head and Shoulders".to_string(),
                sentiment: "Bullish".to_string(),
                confidence,
                start_idx: left.idx,
                end_idx: right.idx,
                description: format!(
                    "IH&S: left shoulder {:.2}, head {:.2}, right shoulder {:.2}. \
                     Neckline break confirms bullish reversal.",
                    left.price, head.price, right.price
                ),
            });
        }
    }

    /// Double Top: 2 peaks at similar heights.
    fn detect_double_top(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<&SwingPoint> = swings.iter().filter(|s| s.kind == SwingKind::Peak).collect();
        if peaks.len() < 2 {
            return;
        }

        for window in peaks.windows(2) {
            let (p1, p2) = (window[0], window[1]);

            if !Self::prices_match(p1.price, p2.price, MATCH_TOLERANCE) {
                continue;
            }

            // Ensure there's a meaningful trough between them
            let trough_between: Option<&SwingPoint> = swings
                .iter()
                .find(|s| s.kind == SwingKind::Trough && s.idx > p1.idx && s.idx < p2.idx);

            if trough_between.is_none() {
                continue;
            }
            let trough = trough_between.unwrap();
            let avg_peak = (p1.price + p2.price) / 2.0;
            let depth = (avg_peak - trough.price) / avg_peak;
            if depth < 0.005 {
                continue; // Trough too shallow
            }

            let price_symmetry = 1.0 - ((p1.price - p2.price).abs() / avg_peak);
            let confidence = (0.55 + price_symmetry * 0.25 + depth.min(0.2)).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Double Top".to_string(),
                sentiment: "Bearish".to_string(),
                confidence,
                start_idx: p1.idx,
                end_idx: p2.idx,
                description: format!(
                    "Double Top at {:.2} and {:.2}. Trough between at {:.2}. \
                     Break below trough confirms reversal.",
                    p1.price, p2.price, trough.price
                ),
            });
        }
    }

    /// Double Bottom: 2 troughs at similar lows.
    fn detect_double_bottom(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let troughs: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Trough).collect();
        if troughs.len() < 2 {
            return;
        }

        for window in troughs.windows(2) {
            let (t1, t2) = (window[0], window[1]);

            if !Self::prices_match(t1.price, t2.price, MATCH_TOLERANCE) {
                continue;
            }

            let peak_between: Option<&SwingPoint> = swings
                .iter()
                .find(|s| s.kind == SwingKind::Peak && s.idx > t1.idx && s.idx < t2.idx);

            if peak_between.is_none() {
                continue;
            }
            let peak = peak_between.unwrap();
            let avg_trough = (t1.price + t2.price) / 2.0;
            let height = (peak.price - avg_trough) / avg_trough;
            if height < 0.005 {
                continue;
            }

            let price_symmetry = 1.0 - ((t1.price - t2.price).abs() / avg_trough);
            let confidence = (0.55 + price_symmetry * 0.25 + height.min(0.2)).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Double Bottom".to_string(),
                sentiment: "Bullish".to_string(),
                confidence,
                start_idx: t1.idx,
                end_idx: t2.idx,
                description: format!(
                    "Double Bottom at {:.2} and {:.2}. Peak between at {:.2}. \
                     Break above peak confirms reversal.",
                    t1.price, t2.price, peak.price
                ),
            });
        }
    }

    /// Triple Top: 3 peaks at similar heights.
    fn detect_triple_top(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<&SwingPoint> = swings.iter().filter(|s| s.kind == SwingKind::Peak).collect();
        if peaks.len() < 3 {
            return;
        }

        for window in peaks.windows(3) {
            let (p1, p2, p3) = (window[0], window[1], window[2]);
            let avg = (p1.price + p2.price + p3.price) / 3.0;

            if !Self::prices_match(p1.price, avg, MATCH_TOLERANCE)
                || !Self::prices_match(p2.price, avg, MATCH_TOLERANCE)
                || !Self::prices_match(p3.price, avg, MATCH_TOLERANCE)
            {
                continue;
            }

            let max_dev = [(p1.price - avg).abs(), (p2.price - avg).abs(), (p3.price - avg).abs()]
                .iter()
                .cloned()
                .fold(0.0_f64, f64::max);
            let confidence = (0.6 + 0.3 * (1.0 - max_dev / avg)).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Triple Top".to_string(),
                sentiment: "Bearish".to_string(),
                confidence,
                start_idx: p1.idx,
                end_idx: p3.idx,
                description: format!(
                    "Triple Top at {:.2}, {:.2}, {:.2}. Strong resistance zone. \
                     Break below support confirms reversal.",
                    p1.price, p2.price, p3.price
                ),
            });
        }
    }

    /// Triple Bottom: 3 troughs at similar lows.
    fn detect_triple_bottom(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let troughs: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Trough).collect();
        if troughs.len() < 3 {
            return;
        }

        for window in troughs.windows(3) {
            let (t1, t2, t3) = (window[0], window[1], window[2]);
            let avg = (t1.price + t2.price + t3.price) / 3.0;

            if !Self::prices_match(t1.price, avg, MATCH_TOLERANCE)
                || !Self::prices_match(t2.price, avg, MATCH_TOLERANCE)
                || !Self::prices_match(t3.price, avg, MATCH_TOLERANCE)
            {
                continue;
            }

            let max_dev = [(t1.price - avg).abs(), (t2.price - avg).abs(), (t3.price - avg).abs()]
                .iter()
                .cloned()
                .fold(0.0_f64, f64::max);
            let confidence = (0.6 + 0.3 * (1.0 - max_dev / avg)).min(1.0);

            out.push(ChartPattern {
                pattern_type: "Triple Bottom".to_string(),
                sentiment: "Bullish".to_string(),
                confidence,
                start_idx: t1.idx,
                end_idx: t3.idx,
                description: format!(
                    "Triple Bottom at {:.2}, {:.2}, {:.2}. Strong support zone. \
                     Break above resistance confirms reversal.",
                    t1.price, t2.price, t3.price
                ),
            });
        }
    }

    /// Rising Wedge: Both support and resistance lines slope upward, but
    /// the lower line (support) is steeper → converging upward.
    fn detect_rising_wedge(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| (s.idx as f64, s.price))
            .collect();
        let troughs: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| (s.idx as f64, s.price))
            .collect();

        if peaks.len() < 2 || troughs.len() < 2 {
            return;
        }

        let (resistance_slope, _) = Self::linear_regression(&peaks);
        let (support_slope, _) = Self::linear_regression(&troughs);

        // Both slopes positive, support steeper than resistance → converging upward
        if resistance_slope > 0.0 && support_slope > 0.0 && support_slope > resistance_slope {
            let convergence = (support_slope - resistance_slope) / support_slope.abs().max(1e-9);
            let confidence = (0.45 + convergence.min(0.4) * 0.5).min(0.9);

            let start = swings.first().map(|s| s.idx).unwrap_or(0);
            let end = swings.last().map(|s| s.idx).unwrap_or(0);

            out.push(ChartPattern {
                pattern_type: "Rising Wedge".to_string(),
                sentiment: "Bearish".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Rising Wedge: support slope {:.6}, resistance slope {:.6}. \
                     Converging upward — bearish reversal pattern.",
                    support_slope, resistance_slope
                ),
            });
        }
    }

    /// Falling Wedge: Both slopes negative, upper line (resistance) steeper
    /// downward → converging downward.
    fn detect_falling_wedge(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| (s.idx as f64, s.price))
            .collect();
        let troughs: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| (s.idx as f64, s.price))
            .collect();

        if peaks.len() < 2 || troughs.len() < 2 {
            return;
        }

        let (resistance_slope, _) = Self::linear_regression(&peaks);
        let (support_slope, _) = Self::linear_regression(&troughs);

        // Both slopes negative, resistance steeper (more negative) → converging downward
        if resistance_slope < 0.0
            && support_slope < 0.0
            && resistance_slope < support_slope
        {
            let convergence = (support_slope - resistance_slope).abs()
                / resistance_slope.abs().max(1e-9);
            let confidence = (0.45 + convergence.min(0.4) * 0.5).min(0.9);

            let start = swings.first().map(|s| s.idx).unwrap_or(0);
            let end = swings.last().map(|s| s.idx).unwrap_or(0);

            out.push(ChartPattern {
                pattern_type: "Falling Wedge".to_string(),
                sentiment: "Bullish".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Falling Wedge: support slope {:.6}, resistance slope {:.6}. \
                     Converging downward — bullish reversal pattern.",
                    support_slope, resistance_slope
                ),
            });
        }
    }

    // ── Continuation Patterns ──────────────────────────────────────────────

    /// Detect Bullish/Bearish Flags and Pennants.
    /// Requires a sharp flagpole move followed by a small consolidation channel.
    fn detect_flags_and_pennants(
        candles: &[Candle],
        swings: &[SwingPoint],
        out: &mut Vec<ChartPattern>,
    ) {
        if candles.len() < MIN_FLAGPOLE_CANDLES + 10 || swings.len() < 4 {
            return;
        }

        // Try to find a flagpole in the recent candle history.
        // We scan backward looking for a sharp directional move.
        let n = candles.len();
        let pole_end = n.saturating_sub(10); // consolidation zone in last ~10 candles
        if pole_end < MIN_FLAGPOLE_CANDLES {
            return;
        }

        let pole_start = pole_end.saturating_sub(20).max(0);
        let pole_candles = &candles[pole_start..pole_end];

        if pole_candles.is_empty() {
            return;
        }

        let pole_open = pole_candles.first().unwrap().open;
        let pole_close = pole_candles.last().unwrap().close;
        let pole_range = (pole_close - pole_open).abs();
        let avg_price = (pole_open + pole_close) / 2.0;

        if avg_price < 1e-9 || pole_range / avg_price < FLAGPOLE_MIN_RANGE_RATIO {
            return; // No meaningful flagpole
        }

        let is_bullish_pole = pole_close > pole_open;

        // Consolidation zone: the swings in the last portion of the data
        let consol_swings: Vec<&SwingPoint> = swings
            .iter()
            .filter(|s| s.idx >= pole_end)
            .collect();

        if consol_swings.len() < 2 {
            return;
        }

        let consol_peaks: Vec<(f64, f64)> = consol_swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| (s.idx as f64, s.price))
            .collect();
        let consol_troughs: Vec<(f64, f64)> = consol_swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| (s.idx as f64, s.price))
            .collect();

        if consol_peaks.is_empty() || consol_troughs.is_empty() {
            return;
        }

        let (res_slope, _) = if consol_peaks.len() >= 2 {
            Self::linear_regression(&consol_peaks)
        } else {
            (0.0, consol_peaks[0].1)
        };

        let (sup_slope, _) = if consol_troughs.len() >= 2 {
            Self::linear_regression(&consol_troughs)
        } else {
            (0.0, consol_troughs[0].1)
        };

        // Consolidation price range
        let consol_high = consol_swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| s.price)
            .fold(f64::MIN, f64::max);
        let consol_low = consol_swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| s.price)
            .fold(f64::MAX, f64::min);
        let consol_range = consol_high - consol_low;

        // Flag: roughly parallel lines (slopes have same sign and similar magnitude)
        let slopes_parallel = (res_slope - sup_slope).abs() < 0.001;
        // Pennant: converging lines (slopes have opposite signs or converge)
        let slopes_converge = (res_slope < 0.0 && sup_slope > 0.0)
            || (res_slope.abs() > 0.0001 && sup_slope.abs() > 0.0001
                && (res_slope * sup_slope < 0.0
                    || (res_slope - sup_slope).abs() > res_slope.abs() * 0.3));

        // Consolidation should be small relative to flagpole
        let ratio = if pole_range > 1e-9 {
            consol_range / pole_range
        } else {
            1.0
        };

        if ratio > 0.5 {
            return; // Consolidation too large — not a flag/pennant
        }

        let base_confidence = 0.50 + (1.0 - ratio) * 0.3;

        if slopes_parallel {
            let (pattern, sentiment) = if is_bullish_pole {
                ("Bullish Flag", "Bullish")
            } else {
                ("Bearish Flag", "Bearish")
            };
            out.push(ChartPattern {
                pattern_type: pattern.to_string(),
                sentiment: sentiment.to_string(),
                confidence: base_confidence.min(0.9),
                start_idx: pole_start,
                end_idx: n - 1,
                description: format!(
                    "{}: Flagpole from {:.2} to {:.2} ({:.1}% move). \
                     Consolidation channel with parallel boundaries.",
                    pattern, pole_open, pole_close,
                    (pole_range / avg_price) * 100.0
                ),
            });
        }

        if slopes_converge {
            let (pattern, sentiment) = if is_bullish_pole {
                ("Bullish Pennant", "Bullish")
            } else {
                ("Bearish Pennant", "Bearish")
            };
            out.push(ChartPattern {
                pattern_type: pattern.to_string(),
                sentiment: sentiment.to_string(),
                confidence: (base_confidence + 0.05).min(0.9),
                start_idx: pole_start,
                end_idx: n - 1,
                description: format!(
                    "{}: Flagpole from {:.2} to {:.2} ({:.1}% move). \
                     Small converging triangle after sharp move.",
                    pattern, pole_open, pole_close,
                    (pole_range / avg_price) * 100.0
                ),
            });
        }
    }

    /// Cup and Handle: U-shaped base followed by a short consolidation (handle).
    fn detect_cup_and_handle(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let troughs: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Trough).collect();
        let peaks: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Peak).collect();

        if troughs.len() < 2 || peaks.len() < 3 {
            return;
        }

        // Look for a pattern: Peak(rim) → Trough(cup bottom) → Peak(rim) → small dip (handle)
        for i in 0..peaks.len().saturating_sub(2) {
            let left_rim = peaks[i];
            let right_rim = peaks[i + 1];

            // Rims should be at similar levels
            if !Self::prices_match(left_rim.price, right_rim.price, CUP_ASYMMETRY_TOLERANCE) {
                continue;
            }

            // Find the deepest trough between the two rims
            let cup_bottom: Option<&SwingPoint> = troughs
                .iter()
                .filter(|t| t.idx > left_rim.idx && t.idx < right_rim.idx)
                .min_by(|a, b| a.price.partial_cmp(&b.price).unwrap_or(std::cmp::Ordering::Equal))
                .copied();

            if cup_bottom.is_none() {
                continue;
            }
            let bottom = cup_bottom.unwrap();

            let rim_avg = (left_rim.price + right_rim.price) / 2.0;
            let cup_depth = (rim_avg - bottom.price) / rim_avg;

            // Cup should be meaningful (at least 2% deep)
            if cup_depth < 0.02 || cup_depth > 0.35 {
                continue;
            }

            // Look for a handle: a small dip after the right rim
            let handle_trough: Option<&SwingPoint> = troughs
                .iter()
                .filter(|t| t.idx > right_rim.idx)
                .next()
                .copied();

            if let Some(handle) = handle_trough {
                // Handle should be shallower than the cup
                let handle_depth = (right_rim.price - handle.price) / right_rim.price;
                if handle_depth > 0.0 && handle_depth < cup_depth * 0.5 {
                    let confidence = (0.55 + cup_depth.min(0.2) + (1.0 - handle_depth / cup_depth) * 0.15).min(0.95);

                    out.push(ChartPattern {
                        pattern_type: "Cup and Handle".to_string(),
                        sentiment: "Bullish".to_string(),
                        confidence,
                        start_idx: left_rim.idx,
                        end_idx: handle.idx,
                        description: format!(
                            "Cup and Handle: rims at {:.2}/{:.2}, cup bottom {:.2} ({:.1}% deep), \
                             handle dip to {:.2}. Breakout above rim confirms continuation.",
                            left_rim.price, right_rim.price, bottom.price,
                            cup_depth * 100.0, handle.price
                        ),
                    });
                }
            }
        }
    }

    /// Inverse Cup and Handle: dome-shaped top followed by a short consolidation.
    fn detect_inverse_cup_and_handle(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Peak).collect();
        let troughs: Vec<&SwingPoint> =
            swings.iter().filter(|s| s.kind == SwingKind::Trough).collect();

        if peaks.len() < 2 || troughs.len() < 3 {
            return;
        }

        for i in 0..troughs.len().saturating_sub(2) {
            let left_rim = troughs[i];
            let right_rim = troughs[i + 1];

            if !Self::prices_match(left_rim.price, right_rim.price, CUP_ASYMMETRY_TOLERANCE) {
                continue;
            }

            // Find highest peak between the two rims (dome)
            let dome_top: Option<&SwingPoint> = peaks
                .iter()
                .filter(|p| p.idx > left_rim.idx && p.idx < right_rim.idx)
                .max_by(|a, b| a.price.partial_cmp(&b.price).unwrap_or(std::cmp::Ordering::Equal))
                .copied();

            if dome_top.is_none() {
                continue;
            }
            let dome = dome_top.unwrap();

            let rim_avg = (left_rim.price + right_rim.price) / 2.0;
            let dome_height = (dome.price - rim_avg) / rim_avg;

            if dome_height < 0.02 || dome_height > 0.35 {
                continue;
            }

            // Handle: small peak after right rim
            let handle_peak: Option<&SwingPoint> = peaks
                .iter()
                .filter(|p| p.idx > right_rim.idx)
                .next()
                .copied();

            if let Some(handle) = handle_peak {
                let handle_height = (handle.price - right_rim.price) / right_rim.price;
                if handle_height > 0.0 && handle_height < dome_height * 0.5 {
                    let confidence = (0.55 + dome_height.min(0.2) + (1.0 - handle_height / dome_height) * 0.15).min(0.95);

                    out.push(ChartPattern {
                        pattern_type: "Inverse Cup and Handle".to_string(),
                        sentiment: "Bearish".to_string(),
                        confidence,
                        start_idx: left_rim.idx,
                        end_idx: handle.idx,
                        description: format!(
                            "Inverse Cup and Handle: rims at {:.2}/{:.2}, dome peak {:.2} ({:.1}% high), \
                             handle bump to {:.2}. Breakdown below rim confirms continuation.",
                            left_rim.price, right_rim.price, dome.price,
                            dome_height * 100.0, handle.price
                        ),
                    });
                }
            }
        }
    }

    // ── Bilateral Patterns ─────────────────────────────────────────────────

    /// Detect Symmetrical, Ascending, and Descending Triangles.
    fn detect_triangles(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| (s.idx as f64, s.price))
            .collect();
        let troughs: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| (s.idx as f64, s.price))
            .collect();

        if peaks.len() < 2 || troughs.len() < 2 {
            return;
        }

        let (res_slope, _) = Self::linear_regression(&peaks);
        let (sup_slope, _) = Self::linear_regression(&troughs);

        let start = swings.first().map(|s| s.idx).unwrap_or(0);
        let end = swings.last().map(|s| s.idx).unwrap_or(0);

        // Ascending Triangle: flat resistance, rising support
        if res_slope.abs() < FLAT_SLOPE_THRESHOLD && sup_slope > FLAT_SLOPE_THRESHOLD {
            let confidence = (0.5 + sup_slope.abs().min(0.01) * 30.0).min(0.85);
            out.push(ChartPattern {
                pattern_type: "Ascending Triangle".to_string(),
                sentiment: "Bullish".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Ascending Triangle: flat resistance (slope {:.6}), rising support (slope {:.6}). \
                     Breakout above resistance is a bullish signal.",
                    res_slope, sup_slope
                ),
            });
        }

        // Descending Triangle: flat support, falling resistance
        if sup_slope.abs() < FLAT_SLOPE_THRESHOLD && res_slope < -FLAT_SLOPE_THRESHOLD {
            let confidence = (0.5 + res_slope.abs().min(0.01) * 30.0).min(0.85);
            out.push(ChartPattern {
                pattern_type: "Descending Triangle".to_string(),
                sentiment: "Bearish".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Descending Triangle: falling resistance (slope {:.6}), flat support (slope {:.6}). \
                     Breakdown below support is a bearish signal.",
                    res_slope, sup_slope
                ),
            });
        }

        // Symmetrical Triangle: resistance falling, support rising → converging
        if res_slope < -FLAT_SLOPE_THRESHOLD && sup_slope > FLAT_SLOPE_THRESHOLD {
            let convergence_rate = (res_slope.abs() + sup_slope.abs()) / 2.0;
            let confidence = (0.45 + convergence_rate.min(0.01) * 30.0).min(0.85);
            out.push(ChartPattern {
                pattern_type: "Symmetrical Triangle".to_string(),
                sentiment: "Neutral".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Symmetrical Triangle: converging trendlines (res slope {:.6}, sup slope {:.6}). \
                     Breakout direction determines bias.",
                    res_slope, sup_slope
                ),
            });
        }
    }

    /// Rectangle: Both resistance and support are roughly flat.
    fn detect_rectangle(swings: &[SwingPoint], out: &mut Vec<ChartPattern>) {
        let peaks: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Peak)
            .map(|s| (s.idx as f64, s.price))
            .collect();
        let troughs: Vec<(f64, f64)> = swings
            .iter()
            .filter(|s| s.kind == SwingKind::Trough)
            .map(|s| (s.idx as f64, s.price))
            .collect();

        if peaks.len() < 2 || troughs.len() < 2 {
            return;
        }

        let (res_slope, _res_intercept) = Self::linear_regression(&peaks);
        let (sup_slope, _sup_intercept) = Self::linear_regression(&troughs);

        if res_slope.abs() < FLAT_SLOPE_THRESHOLD && sup_slope.abs() < FLAT_SLOPE_THRESHOLD {
            let avg_res = peaks.iter().map(|p| p.1).sum::<f64>() / peaks.len() as f64;
            let avg_sup = troughs.iter().map(|p| p.1).sum::<f64>() / troughs.len() as f64;
            let channel_width = avg_res - avg_sup;
            let mid = (avg_res + avg_sup) / 2.0;

            if mid.abs() < 1e-9 || channel_width / mid < 0.005 {
                return; // Too narrow
            }

            // Check how tightly peaks/troughs cluster around their averages
            let res_dev: f64 = peaks.iter().map(|p| (p.1 - avg_res).abs()).sum::<f64>()
                / peaks.len() as f64;
            let sup_dev: f64 = troughs.iter().map(|p| (p.1 - avg_sup).abs()).sum::<f64>()
                / troughs.len() as f64;
            let avg_dev = (res_dev + sup_dev) / 2.0;
            let tightness = 1.0 - (avg_dev / channel_width).min(0.5);

            let confidence = (0.45 + tightness * 0.4).min(0.9);

            let start = swings.first().map(|s| s.idx).unwrap_or(0);
            let end = swings.last().map(|s| s.idx).unwrap_or(0);

            out.push(ChartPattern {
                pattern_type: "Rectangle".to_string(),
                sentiment: "Neutral".to_string(),
                confidence,
                start_idx: start,
                end_idx: end,
                description: format!(
                    "Rectangle: horizontal channel between {:.2} (support) and {:.2} (resistance). \
                     Width {:.2} ({:.1}% of price). Breakout direction determines bias.",
                    avg_sup, avg_res, channel_width, (channel_width / mid) * 100.0
                ),
            });
        }
    }
}
