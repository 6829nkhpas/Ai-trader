pub mod predictive_data {
    include!(concat!(env!("OUT_DIR"), "/ai_trade.predictive_data.rs"));
}
pub mod market_data {
    include!(concat!(env!("OUT_DIR"), "/ai_trade.market_data.rs"));
}