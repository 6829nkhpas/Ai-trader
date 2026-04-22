// indicators.rs — RSI and VWAP computation for the Technical Agent.
//
// Each function takes a mutable reference to a SymbolState, updates the
// relevant accumulators/indicators in-place, and returns the freshly-computed
// indicator value.
//
// Design contract:
//   • update_rsi()  — feeds a new LTP into the stateful ta::RSI instance.
//                     Returns Some(rsi) once warmed up (>= 14 prices), else None.
//   • compute_vwap() — pure calculation from SymbolState accumulators.
//                      Returns None if no volume has been recorded yet (division-by-zero guard).
//
// Both functions are intentionally synchronous (no async) — they execute in the
// context of the async tick-processing loop but do not perform I/O.

use crate::state::SymbolState;
use ta::Next;

// ─────────────────────────────────────────────────────────────────────────────
// RSI
// ─────────────────────────────────────────────────────────────────────────────

/// Feeds `price` into the symbol's stateful RSI calculator and returns the
/// current RSI value once the indicator has been warmed up.
///
/// The `ta` crate uses Wilder's smoothing method (same as TradingView's default
/// RSI implementation).  Each call to `next()` consumes one price and returns
/// the updated RSI value.  The first [`RSI_PERIOD`] values are used internally
/// to initialise the smoothed averages; the indicator is considered reliable
/// only after that point.
///
/// # Returns
/// - `Some(rsi)` — a value in `[0.0, 100.0]` when `price_count >= RSI_PERIOD`.
/// - `None` — while the indicator is still accumulating its warm-up window.
///
/// # Arguments
/// - `state` — mutable reference to the [`SymbolState`] for the symbol.
/// - `price` — the latest `last_traded_price` from the incoming [`Tick`].
pub fn update_rsi(state: &mut SymbolState, price: f64) -> Option<f64> {
    // Feed the price into the incremental RSI calculator.
    // `next()` always returns a value; we gate on price_count to avoid acting
    // on the meaningless initial output before Wilder's smoothing is seeded.
    let rsi_value = state.rsi_indicator.next(price);
    state.price_count += 1;

    if state.rsi_warmed_up() {
        Some(rsi_value)
    } else {
        None
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// VWAP
// ─────────────────────────────────────────────────────────────────────────────

/// Updates the VWAP accumulators in `state` with the current tick data and
/// returns the recomputed intraday VWAP.
///
/// ## Approximation note
/// True VWAP requires intraday `high`, `low`, and `close` for each bar to
/// compute `typical_price = (H + L + C) / 3`.  Kite's LTP-only tick feed does
/// not include per-tick high/low, so we approximate:
///
/// ```text
/// typical_price ≈ last_traded_price   (LTP-only approximation)
/// ```
///
/// This is the industry-standard compromise for live-tick VWAP on LTP feeds.
/// The resulting VWAP will be slightly smoother than bar-based VWAP but tracks
/// it closely enough for the confluence scoring layer.
///
/// ## Formula
/// ```text
/// cumulative_tp_volume += typical_price × volume_delta
/// cumulative_volume    += volume_delta
/// VWAP = cumulative_tp_volume / cumulative_volume
/// ```
///
/// `volume_delta` is the change in cumulative volume since the last tick.
/// Because Kite reports *cumulative* intraday volume, we track the previous
/// value and subtract.
///
/// # Returns
/// - `Some(vwap)` — the current VWAP when cumulative volume > 0.
/// - `None` — if no volume has been recorded (division-by-zero guard).
///
/// # Arguments
/// - `state`          — mutable reference to the [`SymbolState`] for the symbol.
/// - `price`          — `last_traded_price` from the incoming [`Tick`].
/// - `volume_delta`   — new volume traded since the previous tick.
///                      Pass the raw cumulative volume from the first tick;
///                      callers are responsible for computing the delta.
pub fn update_vwap(state: &mut SymbolState, price: f64, volume_delta: u64) -> Option<f64> {
    if volume_delta == 0 {
        // No new volume — return existing VWAP without updating accumulators.
        if state.cumulative_volume > 0.0 {
            return Some(state.cumulative_tp_volume / state.cumulative_volume);
        }
        return None;
    }

    let vol = volume_delta as f64;

    // Update running accumulators.
    state.cumulative_tp_volume += price * vol;
    state.cumulative_volume += vol;

    // Guard against zero-division (should be impossible after the check above).
    if state.cumulative_volume == 0.0 {
        return None;
    }

    Some(state.cumulative_tp_volume / state.cumulative_volume)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::SymbolState;

    /// Verify that RSI returns None for the first RSI_PERIOD - 1 updates,
    /// then Some() thereafter.
    #[test]
    fn rsi_warm_up_gating() {
        let mut state = SymbolState::new();
        let prices = [
            100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 107.0, 105.0, 108.0, 110.0, 109.0,
            111.0,
        ];

        // First 13 prices (< 14): all return None.
        for p in prices {
            let result = update_rsi(&mut state, p);
            assert!(
                result.is_none(),
                "Expected None before warm-up, got {:?}",
                result
            );
        }

        // 14th price: should return Some.
        let result = update_rsi(&mut state, 112.0);
        assert!(
            result.is_some(),
            "Expected Some(rsi) after warm-up, got None"
        );

        // RSI must be in [0, 100].
        let rsi = result.unwrap();
        assert!(rsi >= 0.0 && rsi <= 100.0, "RSI out of range: {}", rsi);
    }

    /// Verify that VWAP correctly computes a weighted average.
    #[test]
    fn vwap_basic_calculation() {
        let mut state = SymbolState::new();

        // Tick 1: price=100, volume=10  → cumulative TP×V = 1000, cumV = 10
        let v1 = update_vwap(&mut state, 100.0, 10);
        assert_eq!(v1, Some(100.0));

        // Tick 2: price=110, volume=10  → cumulative TP×V = 2100, cumV = 20
        let v2 = update_vwap(&mut state, 110.0, 10);
        assert_eq!(v2, Some(105.0)); // (1000 + 1100) / 20 = 105.0

        // Tick 3: zero-volume tick should not change VWAP.
        let v3 = update_vwap(&mut state, 999.0, 0);
        assert_eq!(v3, Some(105.0));
    }

    /// Verify that VWAP returns None when no volume has been seen.
    #[test]
    fn vwap_no_volume_returns_none() {
        let mut state = SymbolState::new();
        let result = update_vwap(&mut state, 100.0, 0);
        assert!(result.is_none());
    }
}
