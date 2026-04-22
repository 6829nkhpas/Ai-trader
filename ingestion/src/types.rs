/// Internal representation of a single market tick, decoded from Kite's binary
/// WebSocket protocol. This struct is the shared contract between:
///   - `kite_ws`      → parser output
///   - `kafka_producer` → encodes into Protobuf (market_data::Tick) for Kafka
///   - `questdb_writer` → formats as ILP for QuestDB
#[derive(Debug, Clone)]
pub struct ParsedTick {
    /// Kite instrument token (u32 integer ID).
    /// Stored for future use (re-subscription, depth-feed correlation).
    #[allow(dead_code)]
    pub instrument_token: u32,
    /// Resolved NSE symbol name (e.g. "RELIANCE") from the token→symbol map
    pub symbol: String,
    /// Last traded price in INR
    pub last_price: f64,
    /// Cumulative traded volume at this tick
    pub volume: u32,
    /// Top-of-book best bid price (from market depth, Full mode only)
    pub best_bid: f64,
    /// Top-of-book best ask price (from market depth, Full mode only)
    pub best_ask: f64,
    /// Day open price
    pub open: f64,
    /// Day high price
    pub high: f64,
    /// Day low price
    pub low: f64,
    /// Previous day close price
    pub close: f64,
    /// Exchange timestamp in Unix milliseconds
    pub timestamp_ms: i64,
}
