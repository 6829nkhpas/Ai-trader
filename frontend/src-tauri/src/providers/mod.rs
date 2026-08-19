// src/providers/mod.rs — P14: the market-data / broker seam.
//
// `docs/business/PLAN_OF_ACTION.md` §4.2 blocker **P14**. Two compliance
// requirements sit behind this module, and neither is about abstraction for its
// own sake:
//
//  1. **Broker independence.** A research product whose data feed is welded to one
//     broker's credentials cannot be sold to that broker's competitors' clients,
//     and cannot survive that broker withdrawing API access. The blueprint treats
//     single-broker coupling as a business-continuity risk to be disclosed. One
//     trait plus one env value turns "rewrite four files" into "add a file".
//
//  2. **The LLM must not be able to place an order** (blueprint §1.3). That is
//     stated here structurally rather than in prose: [`BrokerProvider`] has no
//     order-placement method, so no amount of tool-calling can reach one through
//     this seam. See the trait's own docs.
//
// Only FOUR places in the Tauri backend actually reach a broker API — historical
// candles, quotes, an instrument-token lookup, and the daily instrument CSV dump.
// (Kite is *named* in 28 files; the rest only pass symbols around.) So the trait
// has exactly four methods: the seam is the real one, not a speculative one.
//
// ## Why futures are hand-boxed
//
// The registry picks a provider at runtime from an env value, so the call sites
// hold a `&dyn MarketDataProvider`. Native `async fn` in traits (Rust 1.75+) is
// not `dyn`-compatible, and this crate does not depend on `async-trait`, so each
// method returns a [`ProviderFuture`] — a pinned boxed future, which is exactly
// what `#[async_trait]` would generate. Implementations write
// `Box::pin(async move { … })` and read normally.

pub mod kite;
pub mod registry;

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;

use chrono::NaiveDate;

/// A boxed, `Send` future — the `dyn`-compatible return type for the traits below.
pub type ProviderFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// One OHLCV bar as a provider returns it.
///
/// `timestamp` is deliberately a String in ISO-8601 form (`%Y-%m-%dT%H:%M:%S%z`)
/// rather than a parsed `DateTime`: that is the shape
/// `history_loader::parse_kite_timestamp` accepts on the way into QuestDB, and a
/// provider whose wire format is epoch seconds is responsible for formatting into
/// it. Handing back a bare epoch integer would silently drop every row at insert
/// time — the failure mode this comment exists to prevent.
#[derive(Clone, Debug, PartialEq)]
pub struct Candle {
    pub timestamp: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

/// A last-traded price with optional open interest.
///
/// `oi` is `Option` because it is meaningful only for derivatives; an equity quote
/// leaves it `None` rather than reporting a fabricated zero.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quote {
    pub last_price: f64,
    pub oi: Option<u64>,
}

/// A read-only market-data feed.
///
/// Everything here is a *read*. There is no subscribe/unsubscribe: live ticks
/// arrive over the ingestion service's websocket bridge, and the only broker call
/// that path makes is the token lookup below.
pub trait MarketDataProvider: Send + Sync {
    /// Short stable identifier (`"kite"`), for logs and the registry.
    fn id(&self) -> &'static str;

    /// Historical OHLCV for one instrument token over one date range.
    ///
    /// **The provider does not page.** Per-interval range caps (Kite's 7-day
    /// minute limit and friends) are enforced by the caller, which slices a wide
    /// request into provider-sized windows and paces them — see
    /// `history_loader::KiteRatePacer` and `KITE_INTERVAL_MAX_DAYS` in
    /// `charting/datafeed.ts`. Moving paging in here would put per-provider rate
    /// limits and the caller's QuestDB-cache-first ordering in two places.
    fn historical<'a>(
        &'a self,
        instrument_token: u32,
        interval: &'a str,
        from: &'a NaiveDate,
        to: &'a NaiveDate,
    ) -> ProviderFuture<'a, Result<Vec<Candle>, String>>;

    /// Quotes for a batch of exchange-qualified symbols (e.g. `NFO:NIFTY…CE`).
    ///
    /// Keyed by the symbol as it was requested, so a caller that built the request
    /// from its own instrument map can look the result straight back up.
    /// A provider that batches internally is free to do so; the caller passes the
    /// whole list.
    fn quotes<'a>(
        &'a self,
        symbols: &'a [String],
    ) -> ProviderFuture<'a, Result<HashMap<String, Quote>, String>>;

    /// Resolve a tradingsymbol to its numeric instrument token.
    ///
    /// `Ok(None)` means "the feed answered, and has no such symbol on that
    /// exchange"; `Err` means the lookup itself failed. The distinction matters to
    /// the caller: the first is a bad symbol, the second is an outage.
    fn instrument_token<'a>(
        &'a self,
        symbol: &'a str,
        exchange: &'a str,
    ) -> ProviderFuture<'a, Result<Option<u32>, String>>;

    /// The full instrument list for an exchange segment, as raw CSV text.
    ///
    /// Returned unparsed on purpose: `instrument_master.rs` owns the CSV column
    /// resolution (and tolerates reordering), and those parsers are pure and
    /// property-tested. A provider that has to synthesise CSV from a JSON feed is
    /// the odd one out and should carry that cost itself.
    fn instrument_dump<'a>(&'a self, exchange: &'a str)
        -> ProviderFuture<'a, Result<String, String>>;
}

/// A read-only view of a brokerage account.
///
/// **This trait has no order-placement method, and must not acquire one.**
/// `docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` §1.3 draws the line that decides
/// what licence the product needs: a Research Analyst publishes research, while
/// anything that routes an order is a different registration with a different
/// capital requirement. The agent reaches market data through
/// [`MarketDataProvider`]; if it could reach an order endpoint through any trait
/// it can name, that line would be one tool definition away from being crossed.
/// Keeping the capability absent from the type system is a stronger control than
/// a prompt instruction or a code-review convention.
///
/// Paper trading lives in `execution/paper.rs` and is deliberately not modelled
/// here: it never touches a broker.
///
/// No implementation exists yet, because nothing in the app reads positions or
/// margins — the Kite REST proxy exposes only `instruments`, `quote` and
/// `historical` (`aggregator/src/kite_api.rs`). The trait is declared now so the
/// shape is fixed before a caller appears; writing speculative HTTP calls against
/// routes that do not exist would be worse than declaring the interface.
#[allow(dead_code)] // No caller yet — see the note above. Deliberate, not stale.
pub trait BrokerProvider: Send + Sync {
    /// Short stable identifier, matching the market-data provider where both are
    /// backed by the same broker.
    fn id(&self) -> &'static str;

    /// Currently held positions. Read-only.
    fn positions<'a>(&'a self) -> ProviderFuture<'a, Result<serde_json::Value, String>>;

    /// Available margin / funds. Read-only.
    fn margins<'a>(&'a self) -> ProviderFuture<'a, Result<serde_json::Value, String>>;
}
