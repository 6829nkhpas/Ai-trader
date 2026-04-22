// proto.rs — Protobuf module bridge for the technical agent.
//
// Uses the `include!` macro to pull in both generated struct files that
// prost-build writes into $OUT_DIR during the build phase.
//
// Usage:
//   use crate::proto::market_data::Tick;         // consumed from Kafka
//   use crate::proto::technical_data::TechSignal; // produced to Kafka

/// Generated Rust structs from `shared_protos/market_data.proto`.
/// Primary type: `Tick` — the raw market tick published by the ingestion service.
pub mod market_data {
    include!(concat!(env!("OUT_DIR"), "/ai_trade.market_data.rs"));
}

/// Generated Rust structs from `shared_protos/technical_data.proto`.
/// Primary type: `TechSignal` — the computed indicator signal we will publish.
pub mod technical_data {
    include!(concat!(env!("OUT_DIR"), "/ai_trade.technical_data.rs"));
}
