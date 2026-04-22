// kafka_producer.rs — Kafka FutureProducer for the Technical Agent.
//
// Responsibilities:
//   init_producer()    → creates a FutureProducer connected to the broker(s).
//   publish_signal()   → encodes a TechSignal via prost and delivers it to the
//                        `signals.technical` Kafka topic asynchronously.
//
// Serialisation contract:
//   TechSignal is encoded as a length-prefixed Protobuf byte vector using
//   `prost::Message::encode_to_vec`.  Consumers on the other side (aggregator,
//   decision engine) MUST decode with the same `technical_data.proto` schema.
//
// Reliability notes:
//   • message.timeout.ms = 5000 — give up after 5 s; prevents unbounded blocking
//     during broker outages.  The tick loop logs an error and continues.
//   • queue.buffering.max.ms = 5 — low latency over throughput (real-time signals).
//   • FutureProducer is Clone (Arc-backed internally) — safe to pass to tokio::spawn.

#[cfg(feature = "kafka")]
pub mod kafka_producer {
    use crate::proto::technical_data::TechSignal;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::producer::{FutureProducer, FutureRecord};
    use std::time::Duration;

    // ── Producer initialisation ──────────────────────────────────────────────

    /// Creates a [`FutureProducer`] connected to `brokers`.
    ///
    /// Configuration is deliberately lean for a real-time signal publisher:
    /// - Low `queue.buffering.max.ms` keeps end-to-end latency minimal.
    /// - `message.timeout.ms` prevents the task from blocking forever on
    ///   a network partition.
    ///
    /// # Panics
    /// Panics immediately if the producer cannot be created (usually indicates
    /// an invalid broker address or a missing CMake build of librdkafka).
    pub fn init_producer(brokers: &str) -> FutureProducer {
        ClientConfig::new()
            .set("bootstrap.servers", brokers)
            // Flush individual messages within 5 s; don't block the signal loop.
            .set("message.timeout.ms", "5000")
            // Low batching delay → real-time delivery.
            .set("queue.buffering.max.ms", "5")
            // Retry up to 3 times on transient broker errors.
            .set("retries", "3")
            .create()
            .expect(
                "Failed to create Kafka FutureProducer — \
                 check KAFKA_BROKER_URL and CMake rdkafka build",
            )
    }

    // ── Signal publishing ────────────────────────────────────────────────────

    /// Encodes `signal` as a Protobuf byte vector and publishes it to `topic`.
    ///
    /// The message key is set to `signal.symbol` so that all signals for the
    /// same symbol are routed to the same Kafka partition (preserving order).
    ///
    /// # Delivery guarantees
    /// Uses `FutureRecord` with a 5-second delivery timeout.  On failure the
    /// error is logged and the call returns without panicking — individual
    /// signal delivery failures are non-fatal for the tick processing loop.
    pub async fn publish_signal(producer: &FutureProducer, topic: &str, signal: &TechSignal) {
        // Serialise the TechSignal Protobuf struct into a raw byte vector.
        let payload: Vec<u8> = signal.encode_to_vec();

        // Use the symbol as the partition key to maintain per-symbol ordering.
        let key = signal.symbol.as_str();

        let record = FutureRecord::to(topic)
            .payload(payload.as_slice())
            .key(key);

        // Await delivery with a 5-second timeout.
        match producer.send(record, Duration::from_secs(5)).await {
            Ok((partition, offset)) => {
                log::debug!(
                    "[kafka_producer] TechSignal published: symbol={} topic={} \
                     partition={} offset={} score={}",
                    signal.symbol,
                    topic,
                    partition,
                    offset,
                    signal.technical_conviction_score,
                );
            }
            Err((kafka_err, _owned_msg)) => {
                log::error!(
                    "[kafka_producer] Failed to publish TechSignal for symbol='{}': {}",
                    signal.symbol,
                    kafka_err,
                );
            }
        }
    }
}
