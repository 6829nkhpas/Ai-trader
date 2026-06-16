// services/option_chain.rs — Pure option-chain resolution math (Phase F1).
//
// This module is the deterministic core of the Options Data Foundation. It has
// NO I/O, NO clock, and NO globals — every function is a pure, deterministic
// function of its arguments, so it is fully unit- and property-testable without
// a live feed.
//
// Responsibilities (task 4.1):
//   - resolve_ladder           (R3.1) strike-ordered CE/PE ladder for one
//                                      underlying + expiry
//   - select_atm               (R3.2) nearest listed strike to spot, lower-strike
//                                      tie-break
//   - select_strike_band       (R3.3) contiguous strikes ATM-M..ATM+M, clamped,
//                                      size <= 2M+1
//   - select_nearest_expiries  (R3.4) <= N non-expired expiries, ascending
//
// The full pipeline (`build_chain_selection`) and the re-centering decision
// (`should_recenter`) are task 4.6 and are implemented at the bottom of this
// module.

use chrono::NaiveDate;

/// The kind of derivative contract. `Fut` carries no meaningful strike.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum OptionType {
    Ce,
    Pe,
    Fut,
}

/// A single tradable derivative contract resolved from the NFO instrument master.
#[derive(Clone, PartialEq, Debug)]
pub struct OptionContract {
    pub token: u32,
    pub tradingsymbol: String,
    pub underlying: String,
    pub option_type: OptionType,
    /// 0.0 / ignored for `Fut`.
    pub strike: f64,
    pub expiry: NaiveDate,
}

/// Resolved scope of the bounded option-chain subscription.
#[derive(Clone, PartialEq, Debug)]
pub struct ChainConfig {
    /// N — number of nearest non-expired expiries to follow.
    pub nearest_expiries: usize,
    /// M — strikes on each side of ATM (band size is at most 2M+1).
    pub strike_band_half_width: usize,
    /// Minimum ATM move (in strike points) before re-subscribing.
    pub recenter_threshold: f64,
    /// Chain-snapshot cadence in seconds.
    pub snapshot_interval_secs: u64,
}

/// One instrument chosen for subscription.
#[derive(Clone, PartialEq, Debug)]
pub struct SelectedOption {
    pub token: u32,
    pub tradingsymbol: String,
    pub underlying: String,
    pub expiry: NaiveDate,
    pub strike: f64,
    pub option_type: OptionType,
}

/// The bounded set of instruments chosen for one underlying.
#[derive(Clone, PartialEq, Debug)]
pub struct ChainSelection {
    pub underlying: String,
    pub atm_strike: f64,
    pub entries: Vec<SelectedOption>,
}

/// Strike-ordered CE/PE ladder for one underlying + expiry. (R3.1)
///
/// Returns only `Ce`/`Pe` contracts whose `underlying` and `expiry` match the
/// arguments, sorted in non-decreasing strike order. `Fut` contracts are
/// excluded (they are not part of a strike ladder). The input slice is never
/// mutated. The sort is stable, so contracts sharing a strike keep their
/// original relative order, giving deterministic output.
pub fn resolve_ladder(
    instruments: &[OptionContract],
    underlying: &str,
    expiry: NaiveDate,
) -> Vec<OptionContract> {
    let mut ladder: Vec<OptionContract> = instruments
        .iter()
        .filter(|c| {
            c.underlying == underlying
                && c.expiry == expiry
                && matches!(c.option_type, OptionType::Ce | OptionType::Pe)
        })
        .cloned()
        .collect();

    // Stable sort by strike; NaN strikes (degenerate) are pushed to the end
    // deterministically rather than panicking on a partial comparison.
    ladder.sort_by(|a, b| {
        a.strike
            .partial_cmp(&b.strike)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    ladder
}

/// Select the At-The-Money strike: the listed strike nearest to `spot`. (R3.2)
///
/// Ties (two strikes equidistant from spot) resolve to the LOWER strike. Returns
/// `None` only when `strikes` is empty or `spot` is non-finite. The input slice
/// is never mutated. Non-finite strike values are ignored.
pub fn select_atm(strikes: &[f64], spot: f64) -> Option<f64> {
    if !spot.is_finite() {
        return None;
    }

    let mut best: Option<f64> = None;
    let mut best_dist = f64::INFINITY;

    for &strike in strikes {
        if !strike.is_finite() {
            continue;
        }
        let dist = (strike - spot).abs();
        match best {
            None => {
                best = Some(strike);
                best_dist = dist;
            }
            Some(current) => {
                // Strictly closer wins; on an exact tie keep the lower strike.
                if dist < best_dist || (dist == best_dist && strike < current) {
                    best = Some(strike);
                    best_dist = dist;
                }
            }
        }
    }

    best
}

/// Select the contiguous strike band ATM-M .. ATM+M, clamped to available. (R3.3)
///
/// `strikes` is treated as the listed ladder of strikes; it is internally sorted
/// and de-duplicated so the band is a contiguous run of distinct listed strikes
/// centered on `atm`. The returned band:
///   - is contiguous in the sorted/de-duplicated ladder,
///   - is centered on the ATM index (M strikes each side where available),
///   - has size at most `2M + 1`,
///   - is clamped to the available strikes when the ladder is shorter than the
///     band on either side.
///
/// Returns an empty vector if `strikes` is empty or `atm` is not present in the
/// (finite) ladder. The input slice is never mutated.
pub fn select_strike_band(strikes: &[f64], atm: f64, m: usize) -> Vec<f64> {
    if !atm.is_finite() {
        return Vec::new();
    }

    // Build a sorted, de-duplicated ladder of finite strikes.
    let mut ladder: Vec<f64> = strikes.iter().copied().filter(|s| s.is_finite()).collect();
    ladder.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    ladder.dedup();

    if ladder.is_empty() {
        return Vec::new();
    }

    // Locate the ATM index. The ATM should be an actual listed strike (chosen by
    // `select_atm`); if it is not present, there is no center to band around.
    let atm_idx = match ladder.iter().position(|&s| s == atm) {
        Some(i) => i,
        None => return Vec::new(),
    };

    // Clamp the window [atm_idx - m, atm_idx + m] to the ladder bounds.
    let lo = atm_idx.saturating_sub(m);
    let hi = atm_idx.saturating_add(m).min(ladder.len() - 1);

    ladder[lo..=hi].to_vec()
}

/// Select the N nearest non-expired expiries, ascending. (R3.4)
///
/// Considers only `Ce`/`Pe`/`Fut` contracts of `underlying` whose expiry is on or
/// after `today` (non-expired). Returns at most `n` distinct expiries, sorted
/// ascending — the N nearest such expiries. Returns an empty vector when `n` is 0
/// or no non-expired expiries exist. The input slice is never mutated.
pub fn select_nearest_expiries(
    instruments: &[OptionContract],
    underlying: &str,
    today: NaiveDate,
    n: usize,
) -> Vec<NaiveDate> {
    if n == 0 {
        return Vec::new();
    }

    let mut expiries: Vec<NaiveDate> = instruments
        .iter()
        .filter(|c| c.underlying == underlying && c.expiry >= today)
        .map(|c| c.expiry)
        .collect();

    expiries.sort();
    expiries.dedup();
    expiries.truncate(n);
    expiries
}

/// Build the full bounded chain selection for one underlying. (R3.5, R3.6, R3.7, R4.4)
///
/// Pipeline:
///   1. Select the N nearest non-expired expiries (`select_nearest_expiries`).
///   2. Compute the ATM strike from the UNION of listed CE/PE strikes across
///      those expiries (`select_atm`).
///   3. Take the contiguous, clamped strike band around ATM (`select_strike_band`).
///   4. Produce the de-duplicated cross product
///      (selected expiries × strike band × {CE, PE}) of ACTUAL listed instrument
///      tokens — at most one token per (expiry, strike, type) cell, de-duplicated
///      by token.
///
/// This function is TOTAL, DETERMINISTIC, and NON-MUTATING:
///   - It never panics. Degenerate input — an empty ladder, a missing underlying,
///     or a non-finite spot — yields a selection with an empty `entries` vector.
///   - Called twice on the same input it returns identical output (the pipeline
///     relies only on the deterministic helpers above).
///   - It never observes or mutates its inputs (it reads `&[OptionContract]` and
///     clones the fields it keeps).
///
/// The size of `entries` never exceeds `nearest_expiries × (2M + 1) × 2`, because
/// it takes at most one CE and one PE token per (expiry, band-strike) cell and the
/// band has size at most `2M + 1` over at most `nearest_expiries` expiries — so it
/// is never the full chain.
pub fn build_chain_selection(
    instruments: &[OptionContract],
    underlying: &str,
    spot: f64,
    today: NaiveDate,
    cfg: &ChainConfig,
) -> ChainSelection {
    // The canonical "nothing selected" result. ATM defaults to 0.0 for degenerate
    // input; `entries` is empty.
    let empty = ChainSelection {
        underlying: underlying.to_string(),
        atm_strike: 0.0,
        entries: Vec::new(),
    };

    // Degenerate spot → empty (R3.6).
    if !spot.is_finite() {
        return empty;
    }

    // Step 1: nearest non-expired expiries. Missing underlying / empty ladder
    // naturally yields no expiries here (R3.7).
    let expiries = select_nearest_expiries(instruments, underlying, today, cfg.nearest_expiries);
    if expiries.is_empty() {
        return empty;
    }

    // Step 2: union of listed CE/PE strikes across the selected expiries, then ATM.
    let mut union_strikes: Vec<f64> = Vec::new();
    for &exp in &expiries {
        for c in resolve_ladder(instruments, underlying, exp) {
            if c.strike.is_finite() {
                union_strikes.push(c.strike);
            }
        }
    }

    let atm = match select_atm(&union_strikes, spot) {
        Some(a) => a,
        None => return empty,
    };

    // Step 3: contiguous, clamped strike band around ATM.
    let band = select_strike_band(&union_strikes, atm, cfg.strike_band_half_width);
    if band.is_empty() {
        return empty;
    }

    // Step 4: de-duplicated cross product of actual listed tokens. At most one CE
    // and one PE per (expiry, band-strike) cell guarantees the size bound; a token
    // de-dup guard guards against any repeated token across cells.
    let mut entries: Vec<SelectedOption> = Vec::new();
    let mut seen_tokens: std::collections::HashSet<u32> = std::collections::HashSet::new();

    for &exp in &expiries {
        let ladder = resolve_ladder(instruments, underlying, exp);
        for &strike in &band {
            for opt_type in [OptionType::Ce, OptionType::Pe] {
                if let Some(c) = ladder
                    .iter()
                    .find(|c| c.strike == strike && c.option_type == opt_type)
                {
                    if seen_tokens.insert(c.token) {
                        entries.push(SelectedOption {
                            token: c.token,
                            tradingsymbol: c.tradingsymbol.clone(),
                            underlying: c.underlying.clone(),
                            expiry: c.expiry,
                            strike: c.strike,
                            option_type: c.option_type,
                        });
                    }
                }
            }
        }
    }

    ChainSelection {
        underlying: underlying.to_string(),
        atm_strike: atm,
        entries,
    }
}

/// Decide whether the live selection should be re-centered (re-pushed). (R4.3)
///
/// Returns `true` if and only if the absolute ATM movement `|new_atm - current_atm|`
/// is at least `threshold`. A non-finite operand makes the comparison `false`
/// (no re-center on garbage), keeping the function total and deterministic.
pub fn should_recenter(current_atm: f64, new_atm: f64, threshold: f64) -> bool {
    (new_atm - current_atm).abs() >= threshold
}

#[cfg(test)]
mod ladder_property_tests {
    // Feature: options-data-foundation, Property 4: Ladder is strike-ordered and scoped
    //
    // For any set of option contracts, `resolve_ladder(instruments, underlying, expiry)`
    // returns only CE/PE contracts of that underlying and expiry, in non-decreasing
    // strike order (FUT excluded; other underlyings/expiries excluded).
    //
    // Validates: Requirements 3.1

    use super::*;
    use chrono::Duration;
    use proptest::prelude::*;

    /// CE / PE / FUT, all reachable so the generator exercises the FUT-exclusion path.
    fn arb_option_type() -> impl Strategy<Value = OptionType> {
        prop_oneof![
            Just(OptionType::Ce),
            Just(OptionType::Pe),
            Just(OptionType::Fut),
        ]
    }

    /// A small fixed set of underlyings so collisions on (underlying, expiry) are
    /// likely, exercising both the in-scope and out-of-scope filtering branches.
    fn arb_underlying() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("NIFTY".to_string()),
            Just("BANKNIFTY".to_string()),
            Just("FINNIFTY".to_string()),
        ]
    }

    /// A small set of weekly expiries so multiple contracts share expiries.
    fn arb_expiry() -> impl Strategy<Value = NaiveDate> {
        let base = NaiveDate::from_ymd_opt(2024, 12, 26).unwrap();
        (0i64..5).prop_map(move |w| base + Duration::days(w * 7))
    }

    /// A mixed CE/PE/FUT contract across multiple underlyings/expiries with a
    /// realistic listed strike step.
    fn arb_contract() -> impl Strategy<Value = OptionContract> {
        (
            any::<u32>(),
            arb_underlying(),
            arb_option_type(),
            100u32..500u32,
            arb_expiry(),
        )
            .prop_map(|(token, underlying, option_type, step, expiry)| OptionContract {
                token,
                tradingsymbol: format!("SYM{token}"),
                underlying,
                option_type,
                strike: f64::from(step) * 50.0,
                expiry,
            })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]
        #[test]
        fn ladder_is_strike_ordered_and_scoped(
            instruments in prop::collection::vec(arb_contract(), 0..40),
            target_underlying in arb_underlying(),
            target_expiry in arb_expiry(),
        ) {
            let ladder = resolve_ladder(&instruments, &target_underlying, target_expiry);

            // Scope: every returned contract is a CE/PE of the requested underlying
            // and expiry — FUTs and other underlyings/expiries are excluded.
            for c in &ladder {
                prop_assert_eq!(&c.underlying, &target_underlying);
                prop_assert_eq!(c.expiry, target_expiry);
                prop_assert!(
                    matches!(c.option_type, OptionType::Ce | OptionType::Pe),
                    "FUT contract leaked into ladder: {:?}",
                    c.option_type
                );
            }

            // Ordering: strikes are non-decreasing.
            for w in ladder.windows(2) {
                prop_assert!(
                    w[0].strike <= w[1].strike,
                    "ladder not strike-ordered: {} then {}",
                    w[0].strike,
                    w[1].strike
                );
            }

            // Completeness: the ladder contains exactly the in-scope CE/PE contracts
            // (nothing matching is dropped, nothing out-of-scope is added).
            let expected = instruments
                .iter()
                .filter(|c| {
                    c.underlying == target_underlying
                        && c.expiry == target_expiry
                        && matches!(c.option_type, OptionType::Ce | OptionType::Pe)
                })
                .count();
            prop_assert_eq!(ladder.len(), expected);
        }
    }
}

#[cfg(test)]
mod atm_property_tests {
    // Feature: options-data-foundation, Property 5: ATM is the nearest listed strike
    //
    // For any non-empty list of listed strikes and any finite spot price,
    // `select_atm` returns a listed strike that minimizes the absolute distance to
    // the spot, with the documented tie-break (the LOWER strike wins on an exact
    // tie); it returns `None` only when no strikes exist (empty list / all
    // non-finite) or the spot is non-finite.
    //
    // Validates: Requirements 3.2

    use super::*;
    use proptest::prelude::*;

    /// Finite listed strikes on a coarse grid (multiples of 50), so that exact
    /// equidistant ties between adjacent strikes are reachable.
    fn arb_strike() -> impl Strategy<Value = f64> {
        (0i64..200).prop_map(|n| n as f64 * 50.0)
    }

    /// Spot prices spanning three regimes:
    ///   - on-grid (coincides with a listed strike),
    ///   - half-grid `+25.0` (exactly between two adjacent grid strikes → ties),
    ///   - arbitrary finite values including far out-of-range (below/above the ladder).
    fn arb_spot() -> impl Strategy<Value = f64> {
        prop_oneof![
            (0i64..200).prop_map(|n| n as f64 * 50.0),
            (0i64..200).prop_map(|n| n as f64 * 50.0 + 25.0),
            -100_000.0f64..100_000.0,
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]
        #[test]
        fn atm_is_nearest_listed_strike(
            strikes in prop::collection::vec(arb_strike(), 1..40),
            spot in arb_spot(),
        ) {
            let result = select_atm(&strikes, spot);

            // All generated strikes are finite and the spot is finite, so a
            // non-empty list must yield Some(..) (R3.2: None only on empty /
            // non-finite spot).
            let atm = result.expect("non-empty finite strikes + finite spot must yield Some");

            // The result is one of the listed strikes.
            prop_assert!(
                strikes.contains(&atm),
                "returned strike {atm} is not a listed strike"
            );

            // It minimizes the absolute distance to spot.
            let atm_dist = (atm - spot).abs();
            for &s in &strikes {
                prop_assert!(
                    atm_dist <= (s - spot).abs(),
                    "strike {s} is closer to spot {spot} than the selected ATM {atm}"
                );
            }

            // Tie-break: among all strikes at the minimal distance, the selected
            // ATM is the lowest (lower-strike tie-break).
            for &s in &strikes {
                if (s - spot).abs() == atm_dist {
                    prop_assert!(
                        atm <= s,
                        "tie at distance {atm_dist}: selected {atm} is not the lowest tied strike (saw {s})"
                    );
                }
            }
        }

        #[test]
        fn atm_is_none_on_empty_or_non_finite_spot(
            strikes in prop::collection::vec(arb_strike(), 0..40),
            spot in arb_spot(),
        ) {
            // No strikes → None regardless of spot.
            prop_assert_eq!(select_atm(&[], spot), None);

            // Non-finite spot → None even when strikes are present.
            prop_assert_eq!(select_atm(&strikes, f64::NAN), None);
            prop_assert_eq!(select_atm(&strikes, f64::INFINITY), None);
            prop_assert_eq!(select_atm(&strikes, f64::NEG_INFINITY), None);
        }
    }
}

#[cfg(test)]
mod strike_band_property_tests {
    // Feature: options-data-foundation, Property 6: Strike band is centered, contiguous, clamped, and bounded
    //
    // For any strike ladder, ATM strike, and half-width M, `select_strike_band`
    // returns a contiguous run of listed strikes centered on the ATM, of size at
    // most `2M + 1`, clamped to the available strikes when the ladder is shorter
    // than the band on either side. (The impl sorts/de-dups the ladder internally
    // and requires `atm` to be a listed strike; otherwise it returns empty.)
    //
    // Validates: Requirements 3.3

    use super::*;
    use proptest::prelude::*;
    use proptest::sample::Index;

    /// Finite listed strikes on a coarse grid (multiples of 50). Generating from a
    /// modest integer range makes duplicate strikes likely, exercising the
    /// internal de-dup path.
    fn arb_strike() -> impl Strategy<Value = f64> {
        (0i64..60).prop_map(|n| n as f64 * 50.0)
    }

    /// The sorted, de-duplicated ladder of finite strikes — the canonical view the
    /// implementation bands over.
    fn sorted_dedup(strikes: &[f64]) -> Vec<f64> {
        let mut ladder: Vec<f64> = strikes.iter().copied().filter(|s| s.is_finite()).collect();
        ladder.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        ladder.dedup();
        ladder
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        /// Main property: with `atm` chosen to be an actual listed strike, the band
        /// is centered, contiguous, clamped, and bounded. M ranges up to 80, which
        /// exceeds the maximum ladder length (so the "M larger than ladder" /
        /// full-clamp case is exercised).
        #[test]
        fn strike_band_is_centered_contiguous_clamped_and_bounded(
            raw_strikes in prop::collection::vec(arb_strike(), 1..40),
            atm_index in any::<Index>(),
            m in 0usize..80,
        ) {
            let ladder = sorted_dedup(&raw_strikes);
            // `ladder` is non-empty because raw_strikes is non-empty and all
            // generated strikes are finite.
            let atm = ladder[atm_index.index(ladder.len())];

            let band = select_strike_band(&raw_strikes, atm, m);

            // The expected window in the sorted/de-duplicated ladder.
            let atm_idx = ladder.iter().position(|&s| s == atm).unwrap();
            let lo = atm_idx.saturating_sub(m);
            let hi = atm_idx.saturating_add(m).min(ladder.len() - 1);
            let expected = ladder[lo..=hi].to_vec();

            // Exact clamped window: centered on ATM, clamped to ladder bounds.
            prop_assert_eq!(&band, &expected);

            // Bounded: size never exceeds 2M + 1.
            prop_assert!(
                band.len() <= 2 * m + 1,
                "band len {} exceeds 2M+1 = {}",
                band.len(),
                2 * m + 1
            );

            // Centered: the ATM strike is present in the band.
            prop_assert!(band.contains(&atm), "band does not contain the ATM strike {atm}");

            // Contiguous: the band is an exact contiguous slice of the sorted,
            // de-duplicated ladder (every band element is consecutive there).
            for (offset, &strike) in band.iter().enumerate() {
                prop_assert_eq!(
                    strike,
                    ladder[lo + offset],
                    "band element at offset {} is not contiguous in the ladder",
                    offset
                );
            }

            // Clamped boundaries: the band reaches the ladder edge whenever the
            // requested half-width runs past it.
            if atm_idx < m {
                // Not enough strikes below ATM → clamped to the lowest strike.
                prop_assert_eq!(band.first().copied(), Some(ladder[0]));
            }
            if atm_idx + m > ladder.len() - 1 {
                // Not enough strikes above ATM → clamped to the highest strike.
                prop_assert_eq!(band.last().copied(), Some(ladder[ladder.len() - 1]));
            }

            // M larger than the ladder on both sides → the whole ladder is returned.
            if atm_idx >= m {
                // (no-op guard; covered above)
            }
            if m >= ladder.len() {
                prop_assert_eq!(&band, &ladder);
            }
        }

        /// Degenerate centers yield an empty band rather than a panic: an `atm`
        /// that is not a listed strike, and a non-finite `atm`.
        #[test]
        fn strike_band_empty_when_atm_absent_or_non_finite(
            raw_strikes in prop::collection::vec(arb_strike(), 1..40),
            off_grid in (0i64..60).prop_map(|n| n as f64 * 50.0 + 25.0),
            m in 0usize..20,
        ) {
            // `off_grid` is a multiple of 50 plus 25, so it never coincides with a
            // listed (multiple-of-50) strike → no center to band around.
            prop_assert!(
                select_strike_band(&raw_strikes, off_grid, m).is_empty(),
                "expected empty band for ATM {off_grid} not present in the ladder"
            );

            // Non-finite ATM → empty band.
            prop_assert!(select_strike_band(&raw_strikes, f64::NAN, m).is_empty());
            prop_assert!(select_strike_band(&raw_strikes, f64::INFINITY, m).is_empty());
            prop_assert!(select_strike_band(&raw_strikes, f64::NEG_INFINITY, m).is_empty());
        }

        /// An empty ladder yields an empty band for any ATM and M (totality).
        #[test]
        fn strike_band_empty_for_empty_ladder(
            atm in arb_strike(),
            m in 0usize..20,
        ) {
            prop_assert!(select_strike_band(&[], atm, m).is_empty());
        }
    }
}

#[cfg(test)]
mod nearest_expiries_property_tests {
    // Feature: options-data-foundation, Property 7: Nearest expiries are non-expired and ordered
    //
    // For any set of contracts, a reference date `today`, and a count N,
    // `select_nearest_expiries` returns at most N expiries, all on or after
    // `today`, in ascending order, distinct, and equal to the N nearest such
    // (non-expired) expiries.
    //
    // Validates: Requirements 3.4

    use super::*;
    use chrono::Duration;
    use proptest::prelude::*;

    /// A fixed reference date. Expiries are generated as offsets around it so the
    /// generated set straddles `today` (some expired, some non-expired, some equal).
    fn today() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 12, 26).unwrap()
    }

    /// A small set of underlyings so collisions on `underlying` are likely,
    /// exercising both the in-scope and out-of-scope filtering branches.
    fn arb_underlying() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("NIFTY".to_string()),
            Just("BANKNIFTY".to_string()),
            Just("FINNIFTY".to_string()),
        ]
    }

    /// CE / PE / FUT — expiry selection considers all contract types of the
    /// underlying, so all three are reachable.
    fn arb_option_type() -> impl Strategy<Value = OptionType> {
        prop_oneof![
            Just(OptionType::Ce),
            Just(OptionType::Pe),
            Just(OptionType::Fut),
        ]
    }

    /// An expiry offset in days around `today`, spanning roughly -30..+90 days so
    /// the set straddles the reference date (expired, equal-to-today, and future).
    fn arb_expiry() -> impl Strategy<Value = NaiveDate> {
        (-30i64..90).prop_map(|d| today() + Duration::days(d))
    }

    /// A contract whose expiry straddles `today`, across multiple underlyings/types.
    fn arb_contract() -> impl Strategy<Value = OptionContract> {
        (
            any::<u32>(),
            arb_underlying(),
            arb_option_type(),
            0u32..500u32,
            arb_expiry(),
        )
            .prop_map(|(token, underlying, option_type, step, expiry)| OptionContract {
                token,
                tradingsymbol: format!("SYM{token}"),
                underlying,
                option_type,
                strike: f64::from(step) * 50.0,
                expiry,
            })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        /// Main property: `select_nearest_expiries` returns at most N distinct,
        /// ascending, non-expired expiries equal to the N nearest such expiries.
        /// N ranges up to 12, which exceeds the number of distinct expiries the
        /// generator can produce (so the "N larger than available" case is hit),
        /// and includes N == 0.
        #[test]
        fn nearest_expiries_are_non_expired_and_ordered(
            instruments in prop::collection::vec(arb_contract(), 0..50),
            target_underlying in arb_underlying(),
            n in 0usize..12,
        ) {
            let today = today();
            let result = select_nearest_expiries(&instruments, &target_underlying, today, n);

            // At most N expiries.
            prop_assert!(result.len() <= n, "returned {} expiries, exceeds N = {n}", result.len());

            // All on or after `today` (non-expired).
            for &exp in &result {
                prop_assert!(exp >= today, "expiry {exp} is before today {today}");
            }

            // Ascending order.
            for w in result.windows(2) {
                prop_assert!(w[0] < w[1], "expiries not strictly ascending: {} then {}", w[0], w[1]);
            }

            // Distinct (strict ascending above already implies this, but assert
            // explicitly against the contract).
            let mut deduped = result.clone();
            deduped.dedup();
            prop_assert_eq!(deduped.len(), result.len(), "expiries are not distinct");

            // Equal to the N nearest such (non-expired) expiries: independently
            // compute the sorted, distinct, non-expired expiry set for the target
            // underlying and take its first N.
            let mut expected: Vec<NaiveDate> = instruments
                .iter()
                .filter(|c| c.underlying == target_underlying && c.expiry >= today)
                .map(|c| c.expiry)
                .collect();
            expected.sort();
            expected.dedup();
            expected.truncate(n);

            prop_assert_eq!(&result, &expected, "result is not the N nearest non-expired expiries");
        }

        /// N == 0 always yields an empty vector, regardless of the instrument set.
        #[test]
        fn nearest_expiries_empty_when_n_zero(
            instruments in prop::collection::vec(arb_contract(), 0..50),
            target_underlying in arb_underlying(),
        ) {
            let result = select_nearest_expiries(&instruments, &target_underlying, today(), 0);
            prop_assert!(result.is_empty(), "N=0 must yield an empty selection, got {result:?}");
        }

        /// When every contract's expiry is before `today`, no expiry is selected
        /// (all expired), for any positive N.
        #[test]
        fn nearest_expiries_empty_when_all_expired(
            tokens in prop::collection::vec(any::<u32>(), 1..20),
            target_underlying in arb_underlying(),
            n in 1usize..12,
        ) {
            let today = today();
            let instruments: Vec<OptionContract> = tokens
                .into_iter()
                .enumerate()
                .map(|(i, token)| OptionContract {
                    token,
                    tradingsymbol: format!("SYM{token}"),
                    underlying: target_underlying.clone(),
                    option_type: OptionType::Ce,
                    strike: 100.0,
                    // All strictly before today (expired).
                    expiry: today - Duration::days((i as i64) + 1),
                })
                .collect();

            let result = select_nearest_expiries(&instruments, &target_underlying, today, n);
            prop_assert!(result.is_empty(), "all-expired set must yield no expiries, got {result:?}");
        }
    }
}

#[cfg(test)]
mod selection_membership_property_tests {
    // Feature: options-data-foundation, Property 8: Chain selection is the de-duplicated, bounded cross product
    //
    // For any instrument set, spot, reference date, and configuration, the
    // resulting `ChainSelection.entries`:
    //   - contains DE-DUPLICATED instrument tokens (every token appears once),
    //   - is drawn EXACTLY from the cross product
    //       (selected expiries × strike band × {CE, PE}),
    //     where every entry corresponds to an ACTUAL listed CE/PE contract of the
    //     underlying within the selected nearest expiries and the strike band
    //     around the resolved ATM,
    //   - has size never exceeding `nearest_expiries × (2M + 1) × 2` — it is never
    //     the full chain.
    //
    // The expected cross product is recomputed here from the separately-tested
    // pure helpers (`select_nearest_expiries`, `resolve_ladder`, `select_atm`,
    // `select_strike_band`) and compared as a token set, which validates the
    // integration, de-duplication, and bound that `build_chain_selection` adds on
    // top of those helpers.
    //
    // Validates: Requirements 3.5, 4.4, 8.4

    use super::*;
    use chrono::Duration;
    use proptest::prelude::*;
    use std::collections::HashSet;

    /// Fixed reference date; expiries are generated as day offsets around it.
    fn today() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 12, 26).unwrap()
    }

    const TARGET: &str = "NIFTY";

    /// Generate a realistic, full-grid option chain for the target underlying plus
    /// out-of-scope noise (a future, another underlying, and an expired contract),
    /// together with a spot price and a chain configuration.
    ///
    /// Every (expiry, strike) cell carries both a CE and a PE with a unique,
    /// sequentially-assigned token, so tokens are unique by construction and the
    /// cross product is well defined.
    fn arb_chain_scenario() -> impl Strategy<Value = (Vec<OptionContract>, f64, ChainConfig)> {
        (
            // Distinct non-expired expiry offsets (days from today). 1..6 distinct
            // expiries lets N exceed the number available on some cases.
            prop::collection::hash_set(0i64..120, 1..6),
            // Distinct strike steps (× 50). 1..16 distinct strikes exercises bands
            // both narrower and wider than the ladder.
            prop::collection::hash_set(1u32..40, 1..16),
            // Spot: spans below-range, in-range, on-grid, mid-grid, and above-range.
            prop_oneof![
                -200.0f64..2200.0,
                (1u32..40).prop_map(|s| s as f64 * 50.0),       // on-grid
                (1u32..40).prop_map(|s| s as f64 * 50.0 + 25.0), // mid-grid (ties)
            ],
            0usize..4, // N — nearest expiries (includes 0)
            0usize..6, // M — strike-band half-width (includes 0)
        )
            .prop_map(|(exp_offsets, strike_steps, spot, n, m)| {
                let today = today();

                let mut expiries: Vec<NaiveDate> =
                    exp_offsets.into_iter().map(|d| today + Duration::days(d)).collect();
                expiries.sort();

                let mut steps: Vec<u32> = strike_steps.into_iter().collect();
                steps.sort_unstable();

                let mut instruments: Vec<OptionContract> = Vec::new();
                let mut token: u32 = 1;

                // Full CE/PE grid for the target underlying across all expiries.
                for &exp in &expiries {
                    for &step in &steps {
                        let strike = f64::from(step) * 50.0;
                        instruments.push(OptionContract {
                            token,
                            tradingsymbol: format!("CE{token}"),
                            underlying: TARGET.to_string(),
                            option_type: OptionType::Ce,
                            strike,
                            expiry: exp,
                        });
                        token += 1;
                        instruments.push(OptionContract {
                            token,
                            tradingsymbol: format!("PE{token}"),
                            underlying: TARGET.to_string(),
                            option_type: OptionType::Pe,
                            strike,
                            expiry: exp,
                        });
                        token += 1;
                    }
                }

                // Out-of-scope noise that must NEVER leak into the selection:
                //   - a FUT of the target underlying (wrong option_type),
                //   - a CE of a different underlying,
                //   - an EXPIRED CE of the target underlying.
                instruments.push(OptionContract {
                    token,
                    tradingsymbol: "NIFTYFUT".to_string(),
                    underlying: TARGET.to_string(),
                    option_type: OptionType::Fut,
                    strike: 0.0,
                    expiry: expiries[0],
                });
                token += 1;
                instruments.push(OptionContract {
                    token,
                    tradingsymbol: "BANKCE".to_string(),
                    underlying: "BANKNIFTY".to_string(),
                    option_type: OptionType::Ce,
                    strike: 100.0,
                    expiry: expiries[0],
                });
                token += 1;
                instruments.push(OptionContract {
                    token,
                    tradingsymbol: "NIFTYEXPCE".to_string(),
                    underlying: TARGET.to_string(),
                    option_type: OptionType::Ce,
                    strike: 100.0,
                    expiry: today - Duration::days(5),
                });

                let cfg = ChainConfig {
                    nearest_expiries: n,
                    strike_band_half_width: m,
                    recenter_threshold: 1.0,
                    snapshot_interval_secs: 60,
                };

                (instruments, spot, cfg)
            })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]
        #[test]
        fn chain_selection_is_deduplicated_bounded_cross_product(
            (instruments, spot, cfg) in arb_chain_scenario(),
        ) {
            let today = today();
            let selection = build_chain_selection(&instruments, TARGET, spot, today, &cfg);

            let n = cfg.nearest_expiries;
            let m = cfg.strike_band_half_width;

            // --- Bound (R4.4 / R8.4): size never exceeds N × (2M + 1) × 2, so it is
            // never the full chain. ---
            let bound = n
                .saturating_mul(2usize.saturating_mul(m).saturating_add(1))
                .saturating_mul(2);
            prop_assert!(
                selection.entries.len() <= bound,
                "selection size {} exceeds bound N*(2M+1)*2 = {}",
                selection.entries.len(),
                bound
            );

            // --- De-duplicated tokens: every token appears exactly once. ---
            let got_tokens: HashSet<u32> =
                selection.entries.iter().map(|e| e.token).collect();
            prop_assert_eq!(
                got_tokens.len(),
                selection.entries.len(),
                "selection contains duplicate instrument tokens"
            );

            // --- Independently recompute the expected cross product from the
            // separately-tested helpers. ---
            let expiries = select_nearest_expiries(&instruments, TARGET, today, n);

            // Union of listed CE/PE strikes across the selected expiries (the basis
            // `build_chain_selection` uses for ATM + band).
            let mut union_strikes: Vec<f64> = Vec::new();
            for &exp in &expiries {
                for c in resolve_ladder(&instruments, TARGET, exp) {
                    union_strikes.push(c.strike);
                }
            }
            let atm = select_atm(&union_strikes, spot);
            let band: Vec<f64> = match atm {
                Some(a) => select_strike_band(&union_strikes, a, m),
                None => Vec::new(),
            };

            // --- Membership: every entry is an ACTUAL listed CE/PE contract of the
            // target underlying within the selected expiries and the band. ---
            for e in &selection.entries {
                prop_assert_eq!(
                    &e.underlying,
                    TARGET,
                    "entry underlying is not the target underlying"
                );
                prop_assert!(
                    matches!(e.option_type, OptionType::Ce | OptionType::Pe),
                    "non-CE/PE contract leaked into the selection: {:?}",
                    e.option_type
                );
                prop_assert!(
                    expiries.contains(&e.expiry),
                    "entry expiry {} is not among the selected nearest expiries",
                    e.expiry
                );
                prop_assert!(
                    band.contains(&e.strike),
                    "entry strike {} is outside the resolved strike band",
                    e.strike
                );
                prop_assert!(
                    instruments.iter().any(|c| {
                        c.token == e.token
                            && c.underlying == e.underlying
                            && c.option_type == e.option_type
                            && c.strike == e.strike
                            && c.expiry == e.expiry
                            && c.tradingsymbol == e.tradingsymbol
                    }),
                    "entry token {} does not correspond to a listed contract",
                    e.token
                );
            }

            // --- "Exactly from" (R3.5): the selected token set equals the set of
            // tokens of ALL listed CE/PE contracts in
            // (selected expiries × band × {CE, PE}). ---
            let expected_tokens: HashSet<u32> = instruments
                .iter()
                .filter(|c| {
                    c.underlying == TARGET
                        && matches!(c.option_type, OptionType::Ce | OptionType::Pe)
                        && expiries.contains(&c.expiry)
                        && band.contains(&c.strike)
                })
                .map(|c| c.token)
                .collect();
            prop_assert_eq!(
                got_tokens,
                expected_tokens,
                "selection is not exactly the (selected expiries × band × {{CE,PE}}) cross product"
            );

            // --- The reported ATM matches the independently resolved ATM whenever a
            // non-empty selection was produced. ---
            if !selection.entries.is_empty() {
                prop_assert_eq!(
                    Some(selection.atm_strike),
                    atm,
                    "selection.atm_strike does not match the resolved ATM"
                );
            }
        }
    }
}

#[cfg(test)]
mod resolution_totality_property_tests {
    // Feature: options-data-foundation, Property 9: Chain resolution is total, deterministic, and non-mutating
    //
    // For any input — including degenerate input such as an empty ladder, a
    // missing underlying (an underlying with no contracts), or a non-finite spot
    // (NaN / +inf / -inf) — `build_chain_selection`:
    //   - returns WITHOUT panicking (totality), yielding an empty `entries` vector
    //     for degenerate input,
    //   - produces IDENTICAL output when called twice on the same input
    //     (determinism),
    //   - leaves its inputs UNCHANGED (non-mutating) — the instrument slice is
    //     cloned beforehand and asserted equal afterward.
    //
    // Validates: Requirements 3.6, 3.7

    use super::*;
    use chrono::Duration;
    use proptest::prelude::*;

    /// Fixed reference date; expiries are generated as day offsets around it so the
    /// generated set straddles `today` (expired, equal-to-today, and future).
    fn today() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 12, 26).unwrap()
    }

    /// CE / PE / FUT — all reachable so ladders may contain only FUTs (a
    /// degenerate ladder for selection purposes) as well as real CE/PE grids.
    fn arb_option_type() -> impl Strategy<Value = OptionType> {
        prop_oneof![
            Just(OptionType::Ce),
            Just(OptionType::Pe),
            Just(OptionType::Fut),
        ]
    }

    /// Underlyings that actually appear in generated contracts.
    fn arb_existing_underlying() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("NIFTY".to_string()),
            Just("BANKNIFTY".to_string()),
        ]
    }

    /// The underlying passed to `build_chain_selection`. Includes underlyings that
    /// have NO contracts in the generated set ("MISSING", "FINNIFTY") so the
    /// missing-underlying degenerate path is exercised, alongside ones that do.
    fn arb_query_underlying() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("NIFTY".to_string()),
            Just("BANKNIFTY".to_string()),
            Just("FINNIFTY".to_string()), // never generated as a contract underlying
            Just("MISSING".to_string()),  // never generated as a contract underlying
        ]
    }

    /// An expiry offset (days around `today`), spanning roughly -30..+90 days so the
    /// set straddles the reference date (expired, equal, and future expiries).
    fn arb_expiry() -> impl Strategy<Value = NaiveDate> {
        (-30i64..90).prop_map(|d| today() + Duration::days(d))
    }

    /// A contract with a FINITE strike (finite so the non-mutation equality check,
    /// which uses `PartialEq` on `f64`, is well defined — NaN strikes would never
    /// compare equal to themselves). Degeneracy in this property comes from empty
    /// sets, missing underlyings, and non-finite SPOT, not from NaN strikes.
    fn arb_contract() -> impl Strategy<Value = OptionContract> {
        (
            any::<u32>(),
            arb_existing_underlying(),
            arb_option_type(),
            0u32..40u32,
            arb_expiry(),
        )
            .prop_map(|(token, underlying, option_type, step, expiry)| OptionContract {
                token,
                tradingsymbol: format!("SYM{token}"),
                underlying,
                option_type,
                strike: f64::from(step) * 50.0,
                expiry,
            })
    }

    /// Spot price spanning normal finite values AND the three non-finite degenerate
    /// cases (NaN, +inf, -inf) required by the task.
    fn arb_spot() -> impl Strategy<Value = f64> {
        prop_oneof![
            // Normal finite spots (in-range, on-grid, mid-grid, out-of-range).
            -500.0f64..2500.0,
            (0u32..40).prop_map(|s| s as f64 * 50.0),
            (0u32..40).prop_map(|s| s as f64 * 50.0 + 25.0),
            // Degenerate non-finite spots.
            Just(f64::NAN),
            Just(f64::INFINITY),
            Just(f64::NEG_INFINITY),
        ]
    }

    /// A chain configuration with N (incl. 0) and M (incl. 0) in modest ranges.
    fn arb_cfg() -> impl Strategy<Value = ChainConfig> {
        (0usize..4, 0usize..6).prop_map(|(n, m)| ChainConfig {
            nearest_expiries: n,
            strike_band_half_width: m,
            recenter_threshold: 1.0,
            snapshot_interval_secs: 60,
        })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]
        #[test]
        fn chain_resolution_is_total_deterministic_and_non_mutating(
            // `0..` lower bound makes the empty-ladder degenerate case reachable.
            instruments in prop::collection::vec(arb_contract(), 0..30),
            underlying in arb_query_underlying(),
            spot in arb_spot(),
            cfg in arb_cfg(),
        ) {
            let today = today();

            // Clone the inputs beforehand to assert non-mutation afterward.
            let instruments_before = instruments.clone();

            // --- Totality: neither call panics (reaching here proves it). ---
            let first = build_chain_selection(&instruments, &underlying, spot, today, &cfg);
            let second = build_chain_selection(&instruments, &underlying, spot, today, &cfg);

            // --- Determinism: identical output on the same input. ---
            prop_assert_eq!(
                &first,
                &second,
                "build_chain_selection is not deterministic for the same input"
            );

            // --- Non-mutating: the instrument slice is unchanged after both calls. ---
            prop_assert_eq!(
                &instruments,
                &instruments_before,
                "build_chain_selection mutated its instrument input"
            );

            // The reported underlying always echoes the query underlying.
            prop_assert_eq!(
                &first.underlying,
                &underlying,
                "selection underlying does not echo the query underlying"
            );

            // --- Degenerate input → empty selection (R3.6, R3.7). ---
            // Degenerate when: the spot is non-finite, the ladder is empty, or the
            // queried underlying has no contracts at all.
            let underlying_missing =
                !instruments.iter().any(|c| c.underlying == underlying);
            let is_degenerate = !spot.is_finite() || instruments.is_empty() || underlying_missing;

            if is_degenerate {
                prop_assert!(
                    first.entries.is_empty(),
                    "degenerate input (non-finite spot / empty ladder / missing underlying) \
                     produced a non-empty selection of {} entries",
                    first.entries.len()
                );
                // Degenerate selections carry the canonical 0.0 ATM.
                prop_assert_eq!(
                    first.atm_strike,
                    0.0,
                    "degenerate input produced a non-zero ATM strike"
                );
            }
        }
    }
}

#[cfg(test)]
mod recenter_property_tests {
    // Feature: options-data-foundation, Property 10: Re-centering tracks ATM movement
    //
    // Two-part property (Validates: Requirements 4.3):
    //
    //   (a) iff — for any current ATM, new ATM, and threshold, `should_recenter`
    //       returns `true` if and only if the absolute ATM movement
    //       `|new_atm - current_atm|` is at least the `threshold`. Validated with
    //       FINITE generators (the iff is exact only over finite operands; the
    //       impl deliberately returns `false` on non-finite garbage, which is
    //       covered separately below).
    //
    //   (b) tracking — whenever a spot drift triggers a re-center, the recomputed
    //       selection's band is CENTERED ON THE NEW ATM: the recomputed
    //       selection's `atm_strike` equals the ATM resolved for the new spot, and
    //       that new ATM strike is actually present in the recomputed band.
    //
    // Validates: Requirements 4.3

    use super::*;
    use chrono::Duration;
    use proptest::prelude::*;

    /// Fixed reference date; expiries are generated as day offsets around it.
    fn today() -> NaiveDate {
        NaiveDate::from_ymd_opt(2024, 12, 26).unwrap()
    }

    const TARGET: &str = "NIFTY";

    /// A finite f64 over a wide but bounded range — used for the exact iff check
    /// where both operands and the threshold must be finite.
    fn arb_finite() -> impl Strategy<Value = f64> {
        -100_000.0f64..100_000.0
    }

    /// A non-negative finite threshold (a re-center threshold is a distance).
    fn arb_threshold() -> impl Strategy<Value = f64> {
        0.0f64..100_000.0
    }

    /// A full CE/PE grid for the target underlying plus an old and a new spot and a
    /// chain configuration. Every (expiry, strike) cell carries a CE and a PE with
    /// a unique token, so the ladder is a well-formed listed chain. The two spots
    /// model a drift; `recenter_threshold` is generated in a modest range relative
    /// to the 50-point strike step so re-centering both triggers and does not
    /// across the case space.
    fn arb_drift_scenario(
    ) -> impl Strategy<Value = (Vec<OptionContract>, f64, f64, ChainConfig)> {
        (
            prop::collection::hash_set(0i64..120, 1..5), // distinct non-expired expiry offsets
            prop::collection::hash_set(1u32..40, 2..16), // distinct strike steps (× 50)
            -200.0f64..2200.0,                            // old spot
            -200.0f64..2200.0,                            // new spot (drift)
            1usize..4,                                    // N — nearest expiries
            0usize..6,                                    // M — strike-band half-width
            0.0f64..200.0,                                // re-center threshold (points)
        )
            .prop_map(|(exp_offsets, strike_steps, old_spot, new_spot, n, m, thresh)| {
                let today = today();

                let mut expiries: Vec<NaiveDate> =
                    exp_offsets.into_iter().map(|d| today + Duration::days(d)).collect();
                expiries.sort();

                let mut steps: Vec<u32> = strike_steps.into_iter().collect();
                steps.sort_unstable();

                let mut instruments: Vec<OptionContract> = Vec::new();
                let mut token: u32 = 1;
                for &exp in &expiries {
                    for &step in &steps {
                        let strike = f64::from(step) * 50.0;
                        instruments.push(OptionContract {
                            token,
                            tradingsymbol: format!("CE{token}"),
                            underlying: TARGET.to_string(),
                            option_type: OptionType::Ce,
                            strike,
                            expiry: exp,
                        });
                        token += 1;
                        instruments.push(OptionContract {
                            token,
                            tradingsymbol: format!("PE{token}"),
                            underlying: TARGET.to_string(),
                            option_type: OptionType::Pe,
                            strike,
                            expiry: exp,
                        });
                        token += 1;
                    }
                }

                let cfg = ChainConfig {
                    nearest_expiries: n,
                    strike_band_half_width: m,
                    recenter_threshold: thresh,
                    snapshot_interval_secs: 60,
                };

                (instruments, old_spot, new_spot, cfg)
            })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        /// Part (a): the exact iff over finite operands.
        #[test]
        fn should_recenter_iff_abs_move_at_least_threshold(
            current_atm in arb_finite(),
            new_atm in arb_finite(),
            threshold in arb_threshold(),
        ) {
            let expected = (new_atm - current_atm).abs() >= threshold;
            prop_assert_eq!(
                should_recenter(current_atm, new_atm, threshold),
                expected,
                "should_recenter disagreed with |new_atm - current_atm| >= threshold"
            );
        }

        /// Part (b): when a spot drift triggers a re-center, the recomputed
        /// selection's band is centered on the new ATM.
        #[test]
        fn recenter_recomputed_selection_is_centered_on_new_atm(
            (instruments, old_spot, new_spot, cfg) in arb_drift_scenario(),
        ) {
            let today = today();

            let old_selection = build_chain_selection(&instruments, TARGET, old_spot, today, &cfg);
            let new_selection = build_chain_selection(&instruments, TARGET, new_spot, today, &cfg);

            // Independently resolve the ATM for the new spot from the same union
            // basis that `build_chain_selection` uses.
            let expiries =
                select_nearest_expiries(&instruments, TARGET, today, cfg.nearest_expiries);
            let mut union_strikes: Vec<f64> = Vec::new();
            for &exp in &expiries {
                for c in resolve_ladder(&instruments, TARGET, exp) {
                    union_strikes.push(c.strike);
                }
            }
            let new_atm = select_atm(&union_strikes, new_spot);

            // Re-centering is only meaningful when a prior ATM exists (old selection
            // non-empty) and a new ATM is resolvable (new selection non-empty).
            if old_selection.entries.is_empty() || new_selection.entries.is_empty() {
                return Ok(());
            }
            let old_atm = old_selection.atm_strike;
            let new_atm = new_atm.expect("non-empty new selection implies a resolved ATM");

            if should_recenter(old_atm, new_atm, cfg.recenter_threshold) {
                // The recomputed selection's band is centered on the NEW ATM.
                prop_assert_eq!(
                    new_selection.atm_strike,
                    new_atm,
                    "recomputed selection's atm_strike is not the ATM for the new spot"
                );

                // The new ATM strike is actually present in the recomputed band.
                prop_assert!(
                    new_selection.entries.iter().any(|e| e.strike == new_atm),
                    "the new ATM strike {new_atm} is absent from the recomputed band"
                );

                // Sanity: a triggered re-center reflects a real ATM movement of at
                // least the configured threshold.
                prop_assert!(
                    (new_atm - old_atm).abs() >= cfg.recenter_threshold,
                    "re-center triggered without the ATM moving by at least the threshold"
                );
            }
        }
    }
}
