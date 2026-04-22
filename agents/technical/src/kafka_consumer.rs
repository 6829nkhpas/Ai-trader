// kafka_consumer.rs — Kafka StreamConsumer for the technical agent.
//
// Subscribes to the `market.ticks` Kafka topic (the same topic the Rust
// ingestion service publishes to) and deserialises each message payload
// from a Protobuf-encoded `Tick` struct using prost::Message::decode.
//
// Architecture:
//   init_consumer()  → creates & configures the StreamConsumer, subscribes
//   run_listener()   → spawns a Tokio task that streams decoded Ticks through
//                      an mpsc channel so main.rs can process them
//
// Topic note: The Kafka topic is `market.ticks` (3 partitions, 6h retention).
// The QuestDB table is named `live_ticks` — these are different things.
// Override the topic at runtime via the KAFKA_TOPIC_TICKS env var.

#[cfg(feature = "kafka")]
pub mod kafka_consumer {
    use crate::proto::market_data::Tick;
    use futures_util::StreamExt;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::consumer::{Consumer, StreamConsumer};
    use rdkafka::message::Message as KafkaMessage;
    use tokio::sync::mpsc;

    /// Creates and configures a Kafka [`StreamConsumer`] subscribed to the
    /// live ticks topic.
    ///
    /// Configuration:
    /// - `auto.offset.reset = "latest"` — we only care about real-time ticks,
    ///   not historical replay.
    /// - `enable.auto.commit = "true"` — offsets committed automatically.
    ///
    /// # Panics
    /// Panics if the broker is unreachable at startup or subscription fails.
    pub async fn init_consumer(brokers: &str, group_id: &str) -> StreamConsumer {
        let consumer: StreamConsumer = ClientConfig::new()
            .set("bootstrap.servers", brokers)
            .set("group.id", group_id)
            .set("auto.offset.reset", "latest")
            .set("enable.auto.commit", "true")
            .set("session.timeout.ms", "6000")
            .create()
            .expect("Failed to create Kafka StreamConsumer — check broker address and CMake build");

        // Resolve topic from env (default: market.ticks — infra-defined topic)
        let topic = std::env::var("KAFKA_TOPIC_TICKS")
            .unwrap_or_else(|_| "market.ticks".to_string());

        consumer
            .subscribe(&[topic.as_str()])
            .unwrap_or_else(|e| panic!("Failed to subscribe to topic '{}': {}", topic, e));

        log::info!(
            "Kafka StreamConsumer ready. group_id='{}' topic='{}'",
            group_id,
            topic
        );

        consumer
    }

    /// Spawns a Tokio task that reads from the [`StreamConsumer`], decodes each
    /// Protobuf-encoded payload into a [`Tick`] struct, and forwards it through
    /// an async `mpsc` channel.
    ///
    /// Returns the receiver end of the channel. Dropping the receiver will
    /// cause the background task to exit cleanly on the next send attempt.
    pub async fn run_listener(consumer: StreamConsumer) -> mpsc::Receiver<Tick> {
        // Buffer up to 1 024 decoded ticks before back-pressure kicks in.
        let (tx, rx) = mpsc::channel::<Tick>(1_024);

        tokio::spawn(async move {
            let mut stream = consumer.stream();

            log::info!("Tick listener loop started — waiting for messages...");

            while let Some(message_result) = stream.next().await {
                match message_result {
                    Ok(msg) => {
                        if let Some(payload) = msg.payload() {
                            match Tick::decode(payload) {
                                Ok(tick) => {
                                    if tx.send(tick).await.is_err() {
                                        // Receiver was dropped — shut down cleanly.
                                        log::info!("Tick channel receiver dropped. Stopping listener.");
                                        break;
                                    }
                                }
                                Err(e) => {
                                    log::warn!("Protobuf decode error (skipping message): {}", e);
                                }
                            }
                        }
                    }
                    Err(e) => {
                        log::error!("Kafka consumer error: {}", e);
                    }
                }
            }

            log::info!("Tick listener loop exited.");
        });

        rx
    }
}
