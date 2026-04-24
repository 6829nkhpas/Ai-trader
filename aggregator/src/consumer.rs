// consumer.rs — Multi-topic Kafka consumer for the aggregator decision engine.
//
// Subscribes to BOTH `technical_signals` and `sentiment_signals` topics
// simultaneously using a single StreamConsumer instance. Incoming messages
// are routed based on `msg.topic()` to the appropriate Protobuf decoder:
//
//   - "technical_signals" → prost::Message::decode → TechSignal
//   - "sentiment_signals" → prost::Message::decode → NewsSentiment
//
// This module is the ingress point for all upstream signals that the
// aggregator's decision engine will fuse into AggregatedDecision outputs.

#[cfg(feature = "kafka")]
pub mod consumer {
    use crate::proto::technical_data::TechSignal;
    use crate::proto::sentiment_data::NewsSentiment;
    use futures_util::StreamExt;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::consumer::{Consumer, StreamConsumer};
    use rdkafka::message::Message as KafkaMessage;

    /// Topic name for technical signals published by the technical agent.
    const TOPIC_TECHNICAL: &str = "technical_signals";

    /// Topic name for sentiment signals published by the sentiment agent.
    const TOPIC_SENTIMENT: &str = "sentiment_signals";

    /// Creates and configures a Kafka [`StreamConsumer`] subscribed to both
    /// the `technical_signals` and `sentiment_signals` topics.
    ///
    /// Configuration:
    /// - `auto.offset.reset = "latest"` — only process real-time signals,
    ///   not historical replay from previous sessions.
    /// - `enable.auto.commit = "true"` — offsets committed automatically.
    /// - `session.timeout.ms = "6000"` — standard consumer group timeout.
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

        // Subscribe to BOTH signal topics simultaneously.
        // The single consumer will receive messages from either topic; we route
        // based on msg.topic() in the processing loop.
        consumer
            .subscribe(&[TOPIC_TECHNICAL, TOPIC_SENTIMENT])
            .unwrap_or_else(|e| {
                panic!(
                    "Failed to subscribe to topics [{}, {}]: {}",
                    TOPIC_TECHNICAL, TOPIC_SENTIMENT, e
                )
            });

        log::info!(
            "Kafka StreamConsumer ready. group_id='{}' topics=[{}, {}]",
            group_id,
            TOPIC_TECHNICAL,
            TOPIC_SENTIMENT,
        );

        consumer
    }

    /// Runs the multi-topic message processing loop.
    ///
    /// For each incoming message:
    /// - Inspects `msg.topic()` to determine the source topic.
    /// - If `technical_signals`: decodes payload as `TechSignal` via prost.
    /// - If `sentiment_signals`: decodes payload as `NewsSentiment` via prost.
    /// - Logs the decoded struct to stdout for verification.
    ///
    /// This function runs indefinitely until the consumer is shut down or
    /// the stream is closed.
    pub async fn run_consumer_loop(consumer: StreamConsumer) {
        let mut stream = consumer.stream();

        log::info!("Aggregator consumer loop started — waiting for signals...");

        while let Some(message_result) = stream.next().await {
            match message_result {
                Ok(msg) => {
                    let topic = msg.topic();
                    let payload = match msg.payload() {
                        Some(p) => p,
                        None => {
                            log::warn!("Received message with empty payload on topic '{}'", topic);
                            continue;
                        }
                    };

                    match topic {
                        TOPIC_TECHNICAL => {
                            match TechSignal::decode(payload) {
                                Ok(signal) => {
                                    println!(
                                        "[TECH] symbol={:<20} rsi={:>6.2}  vwap_dist={:>8.4}%  \
                                         score={:>3}  ts={}",
                                        signal.symbol,
                                        signal.rsi_value,
                                        signal.vwap_distance,
                                        signal.technical_conviction_score,
                                        signal.timestamp_ms,
                                    );
                                }
                                Err(e) => {
                                    log::warn!(
                                        "TechSignal decode error on topic '{}': {}",
                                        topic, e
                                    );
                                }
                            }
                        }
                        TOPIC_SENTIMENT => {
                            match NewsSentiment::decode(payload) {
                                Ok(sentiment) => {
                                    println!(
                                        "[SENT] symbol={:<20} score={:>3}  headline=\"{}\"  \
                                         reason=\"{}\"  ts={}",
                                        sentiment.symbol,
                                        sentiment.claude_conviction_score,
                                        sentiment.headline,
                                        sentiment.reasoning_snippet,
                                        sentiment.timestamp_ms,
                                    );
                                }
                                Err(e) => {
                                    log::warn!(
                                        "NewsSentiment decode error on topic '{}': {}",
                                        topic, e
                                    );
                                }
                            }
                        }
                        unknown => {
                            log::warn!(
                                "Received message from unexpected topic '{}' — ignoring",
                                unknown
                            );
                        }
                    }
                }
                Err(e) => {
                    log::error!("Kafka consumer error: {}", e);
                }
            }
        }

        log::warn!("Consumer stream ended — aggregator loop exiting.");
    }
}
