/// Kafka producer — Subphase 14
///
/// Encodes a `ParsedTick` as a Protobuf `market_data::Tick` and produces it
/// to the `market.ticks` Kafka topic using rdkafka's async FutureProducer.
///
/// The FutureProducer is non-blocking: it enqueues messages in an internal
/// buffer and flushes them in the background, giving us fire-and-forget
/// semantics with back-pressure via the queue size limit.

use log::{error, info, warn};
use prost::Message as ProstMessage;
use rdkafka::{
    config::ClientConfig,
    producer::{FutureProducer, FutureRecord},
    util::Timeout,
};
use std::time::Duration;

use crate::types::ParsedTick;

// Protobuf module compiled from shared_protos/market_data.proto by build.rs
pub mod market_data {
    include!(concat!(env!("OUT_DIR"), "/ai_trade.market_data.rs"));
}

/// Topic name — matches the Kafka topic plan in SESSION_MEMORY.md
const TOPIC: &str = "market.ticks";

/// Wraps an rdkafka `FutureProducer` with tick-specific encoding logic.
pub struct KafkaProducer {
    inner: FutureProducer,
}

impl KafkaProducer {
    /// Construct a new producer from environment variables.
    ///
    /// Reads `KAFKA_BROKERS` (default: `localhost:9092`).
    /// Uses `message.max.bytes = 1MB` and a 1-second linger for batching.
    pub fn new() -> Result<Self, rdkafka::error::KafkaError> {
        let brokers = std::env::var("KAFKA_BROKERS")
            .unwrap_or_else(|_| "localhost:9092".to_string());

        let producer: FutureProducer = ClientConfig::new()
            .set("bootstrap.servers", &brokers)
            .set("message.timeout.ms", "5000")
            .set("linger.ms", "5")           // micro-batch for throughput
            .set("batch.num.messages", "1000")
            .set("compression.type", "lz4") // lightweight compression
            .set("queue.buffering.max.messages", "100000")
            .create()?;

        info!("Kafka producer connected → brokers: {}", brokers);
        Ok(Self { inner: producer })
    }

    /// Encode `tick` as Protobuf and produce it to `market.ticks`.
    ///
    /// Uses the tick's symbol as the Kafka message key so that all ticks for
    /// the same symbol land on the same partition (preserving order per symbol).
    pub async fn send_tick(&self, tick: &ParsedTick) {
        // Build the Protobuf message
        let proto_tick = market_data::Tick {
            symbol: tick.symbol.clone(),
            timestamp_ms: tick.timestamp_ms,
            last_traded_price: tick.last_price,
            volume: tick.volume as i32,
            best_bid: tick.best_bid,
            best_ask: tick.best_ask,
        };

        // Encode to bytes
        let mut payload = Vec::with_capacity(proto_tick.encoded_len());
        if let Err(e) = proto_tick.encode(&mut payload) {
            error!("Protobuf encode failed for {}: {}", tick.symbol, e);
            return;
        }

        // Produce — key = symbol for partition affinity
        let record = FutureRecord::to(TOPIC)
            .key(tick.symbol.as_bytes())
            .payload(&payload);

        match self
            .inner
            .send(record, Timeout::After(Duration::from_secs(5)))
            .await
        {
            Ok((partition, offset)) => {
                log::trace!(
                    "→ Kafka [{}] partition={} offset={} symbol={}",
                    TOPIC, partition, offset, tick.symbol
                );
            }
            Err((e, _)) => {
                warn!("Kafka produce failed for {}: {}", tick.symbol, e);
            }
        }
    }

    /// Flush all buffered messages — call on graceful shutdown.
    pub fn flush(&self) {
        self.inner.flush(Timeout::After(Duration::from_secs(10)));
        info!("Kafka producer flushed.");
    }
}
