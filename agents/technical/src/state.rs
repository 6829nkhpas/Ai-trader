// state.rs — In-memory market state for the Technical Agent.
//
// Holds the live indicator state for every tracked symbol.
// Updated on every incoming Tick; read by indicators.rs to compute RSI + VWAP.
//
// Thread-safety strategy:
//   MarketState is wrapped in Arc<RwLock<>> so it can be shared between the
//   Kafka listener task (writer) and any future signal-publishing task (reader)
//   without holding a mutex across an await point.

use std::collections::HashMap;
use std::sync::Arc;
use ta::indicators::RelativeStrengthIndex;
use tokio::sync::RwLock;

// ── RSI warm-up period ────────────────────────────────────────────────────────
/// Number of closing prices required before the RSI value is meaningful.
/// Matches the standard 14-period RSI used in technical analysis.
pub const RSI_PERIOD: usize = 14;

// ─────────────────────────────────────────────────────────────────────────────
// SymbolState
// ─────────────────────────────────────────────────────────────────────────────

/// All indicator state needed for a single asset.
///
/// ## RSI
/// The `ta` crate's [`RelativeStrengthIndex`] is a stateful, incremental
/// calculator.  Each call to `rsi.next(price)` advances its internal Wilder
/// smoothed-average and returns the current RSI value.  We track `price_count`
/// so callers can skip publishing signals until the indicator is warmed up
/// (i.e., has seen at least [`RSI_PERIOD`] prices).
///
/// ## VWAP (Intraday)
/// Calculated as:
///   VWAP = Σ(typical_price × volume) / Σ(volume)
///
/// where `typical_price = (high + low + close) / 3`.
/// Because the Kite LTP feed does not include intraday high/low at the tick
/// level, we approximate `typical_price ≈ last_traded_price` (LTP).  This is
/// the standard compromise for LTP-only feeds and is accurate enough for the
/// conviction scoring layer.
///
/// Accumulators reset at the start of each trading session (Phase 1.5 will
/// add session-boundary detection; for now they accumulate per process lifetime).
pub struct SymbolState {
    // ── RSI state ─────────────────────────────────────────────────────────────
    /// Stateful RSI calculator from the `ta` crate.
    /// Seeded with RSI_PERIOD (14) on construction.
    pub rsi_indicator: RelativeStrengthIndex,

    /// How many price updates have been fed into `rsi_indicator`.
    /// RSI is only meaningful once this reaches RSI_PERIOD.
    pub price_count: usize,

    // ── VWAP accumulators ─────────────────────────────────────────────────────
    /// Running sum of (typical_price × volume) — the VWAP numerator.
    pub cumulative_tp_volume: f64,

    /// Running sum of volume — the VWAP denominator.
    pub cumulative_volume: f64,

    // ── Volume delta tracking ─────────────────────────────────────────────────
    /// The cumulative volume reported in the previous Tick for this symbol.
    /// Kite's `volume` field is always the intraday cumulative total; we
    /// subtract this value from the new tick's volume to get the per-tick delta
    /// needed by `update_vwap`.
    pub prev_cumulative_volume: u64,
}

impl SymbolState {
    /// Creates a fresh [`SymbolState`] for a newly-seen symbol.
    ///
    /// # Panics
    /// Panics if RSI_PERIOD is 0 (it is a compile-time constant of 14, so this
    /// cannot happen in practice).
    pub fn new() -> Self {
        Self {
            rsi_indicator: RelativeStrengthIndex::new(RSI_PERIOD)
                .expect("RSI_PERIOD must be > 0"),
            price_count: 0,
            cumulative_tp_volume: 0.0,
            cumulative_volume: 0.0,
            prev_cumulative_volume: 0,
        }
    }

    /// Returns `true` once the RSI calculator has been fed enough data to
    /// produce a statistically meaningful value.
    #[inline]
    pub fn rsi_warmed_up(&self) -> bool {
        self.price_count >= RSI_PERIOD
    }
}

impl Default for SymbolState {
    fn default() -> Self {
        Self::new()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// MarketState
// ─────────────────────────────────────────────────────────────────────────────

/// Container for the live indicator state of every tracked symbol.
///
/// Internally a `HashMap<String, SymbolState>` so lookups are O(1) per symbol.
/// The outer [`Arc`]`<`[`RwLock`]`<>>` allows:
///   - Multiple concurrent readers (signal query tasks).
///   - Exclusive write access during tick processing (one writer at a time).
///
/// # Usage
/// ```rust
/// let state = MarketState::new();
/// let shared = state.shared(); // clone the Arc to pass into tasks
/// ```
pub struct MarketState {
    inner: Arc<RwLock<HashMap<String, SymbolState>>>,
}

impl MarketState {
    /// Creates an empty [`MarketState`].
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Returns a cheaply-cloned handle to the shared state, suitable for
    /// moving into async tasks.
    pub fn shared(&self) -> Arc<RwLock<HashMap<String, SymbolState>>> {
        Arc::clone(&self.inner)
    }
}

impl Default for MarketState {
    fn default() -> Self {
        Self::new()
    }
}
