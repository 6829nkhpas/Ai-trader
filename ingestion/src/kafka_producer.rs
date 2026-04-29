/// Kafka producer — Subphases 14 & 16
///
/// Provides two complementary APIs:
///
/// **Free-function API** (Subphase 16 — used by the new direct-stream loop in main.rs):
///   - `init_producer(brokers) -> FutureProducer`  — construct a producer
///   - `publish_tick(producer, topic, tick) -> ()`  — encode Protobuf + send
///
/// **Struct API** (Subphase 14 — kept for the legacy mpsc-channel pipeline path):
///   - `KafkaProducer::new() -> Self`
///   - `KafkaProducer::send_tick(&self, &ParsedTick)`
///
/// The FutureProducer is non-blocking: it enqueues messages in an internal
/// buffer and flushes them in the background, giving us fire-and-forget
/// semantics with back-pressure via the queue size limit.
///
/// Compiled only when the `kafka` feature is enabled (default = on).
/// For a quick type-check on Windows (no CMake): `cargo check --no-default-features`

use log::{error, info, warn};
use prost::Message as ProstMessage;
use rdkafka::{
    config::ClientConfig,
    producer::{FutureProducer, FutureRecord, Producer},
    util::Timeout,
};
use std::time::Duration;

use crate::proto::market_data;
use crate::types::ParsedTick;

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
            instrument_token: tick.instrument_token,
            open: tick.open,
            high: tick.high,
            low: tick.low,
            close: tick.close,
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

// ── Free-function API (Subphase 16) ─────────────────────────────────────────

/// Build a `FutureProducer` from a broker list string.
///
/// # Arguments
/// * `brokers` — comma-separated host:port list, e.g. `"localhost:9092"`
///
/// # Panics
/// Panics if librdkafka cannot create the producer (invalid config / broker
/// string). In production, prefer wrapping in `Result`.
pub fn init_producer(brokers: &str) -> FutureProducer {
    ClientConfig::new()
        .set("bootstrap.servers", brokers)
        .set("message.timeout.ms", "5000")
        .set("linger.ms", "5")
        .set("batch.num.messages", "1000")
        .set("compression.type", "lz4")
        .set("queue.buffering.max.messages", "100000")
        .create()
        .expect("Failed to create Kafka FutureProducer")
}

/// Encode a Protobuf `Tick` and publish it to the given Kafka topic.
///
/// Uses `prost::Message::encode_to_vec` for zero-copy serialisation into an
/// owned byte vector, then fires a non-blocking rdkafka send. The message key
/// is set to `tick.symbol` to guarantee per-symbol partition affinity.
///
/// Failures are logged as warnings — a dropped tick is preferable to stalling
/// the hot WebSocket ingestion path.
pub async fn publish_tick(
    producer: &FutureProducer,
    topic: &str,
    tick: &crate::proto::market_data::Tick,
) {
    // Protobuf → bytes
    let payload = prost::Message::encode_to_vec(tick);

    let record = FutureRecord::to(topic)
        .key(tick.symbol.as_bytes())
        .payload(&payload);

    match producer
        .send(record, Timeout::After(Duration::from_secs(5)))
        .await
    {
        Ok((partition, offset)) => {
            log::trace!(
                "→ Kafka [{}] partition={} offset={} symbol={}",
                topic, partition, offset, tick.symbol
            );
        }
        Err((e, _)) => {
            warn!("Kafka publish_tick failed for {}: {}", tick.symbol, e);
        }
    }
}
