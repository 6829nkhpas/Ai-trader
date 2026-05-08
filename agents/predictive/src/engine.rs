// engine.rs — Kafka Consumer & Producer loop for the Predictive Agent.
//
// Phase 6.3 — Alpha Suite Event Loop + WebSocket Broadcast.
//
// Pipeline:
//   1. Consume Protobuf-encoded OHLCCandle messages from `market.ohlc.10m`.
//   2. Feed each candle's `close` price into `PredictionEngine::add_close_price()`.
//   3. Call `predict_next()` — if a prediction is generated, construct a
//      `PredictiveSignal` and publish it to `signals.predictive`.
//   4. Serialize the signal to JSON and broadcast over the WS channel (port 8082).
//
// The consumer uses `auto.offset.reset = "latest"` so only real-time candles
// are processed (no historical replay).  The producer uses low-latency
// buffering (5 ms) to prioritise freshness over throughput.

#[cfg(feature = "kafka")]
pub mod engine {
    use crate::math::PredictionEngine;
    use crate::proto::market_data::OhlcCandle;
    use crate::proto::predictive_data::PredictiveSignal;
    use futures_util::StreamExt;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::consumer::{Consumer, StreamConsumer};
    use rdkafka::message::Message as KafkaMessage;
    use rdkafka::producer::{FutureProducer, FutureRecord};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use tokio::sync::broadcast;

    // ── Constants ────────────────────────────────────────────────────────────
    /// 10 minutes in milliseconds — offset added to `end_timestamp_ms` to
    /// compute the prediction's target timestamp.
    const TEN_MINUTES_MS: u64 = 600_000;

    /// Model identifier embedded in every published signal.
    const MODEL_VERSION: &str = "alpha-linreg-v1";

    // ── Consumer initialisation ──────────────────────────────────────────────

    /// Creates a Kafka [`StreamConsumer`] subscribed to the OHLC topic.
    fn init_consumer(brokers: &str, group_id: &str, topic: &str) -> StreamConsumer {
        let consumer: StreamConsumer = ClientConfig::new()
            .set("bootstrap.servers", brokers)
            .set("group.id", group_id)
            .set("auto.offset.reset", "latest")
            .set("enable.auto.commit", "true")
            .set("session.timeout.ms", "6000")
            .create()
            .expect(
                "Failed to create Kafka StreamConsumer — \
                 check KAFKA_BROKER_URL and CMake rdkafka build",
            );

        consumer
            .subscribe(&[topic])
            .unwrap_or_else(|e| panic!("Failed to subscribe to topic '{}': {}", topic, e));

        log::info!(
            "Kafka StreamConsumer ready. group_id='{}' topic='{}'",
            group_id,
            topic
        );

        consumer
    }

    // ── Producer initialisation ──────────────────────────────────────────────

    /// Creates a Kafka [`FutureProducer`] for publishing PredictiveSignals.
    fn init_producer(brokers: &str) -> FutureProducer {
        ClientConfig::new()
            .set("bootstrap.servers", brokers)
            .set("message.timeout.ms", "5000")
            .set("queue.buffering.max.ms", "5")
            .set("retries", "3")
            .create()
            .expect(
                "Failed to create Kafka FutureProducer — \
                 check KAFKA_BROKER_URL and CMake rdkafka build",
            )
    }

    // ── Signal publishing ────────────────────────────────────────────────────

    /// Encodes a [`PredictiveSignal`] and publishes it to the given topic,
    /// keyed by the signal's symbol for partition co-locality.
    async fn publish_signal(producer: &FutureProducer, topic: &str, signal: &PredictiveSignal) {
        let payload = signal.encode_to_vec();
        let key = signal.symbol.as_str();

        let record = FutureRecord::to(topic)
            .payload(payload.as_slice())
            .key(key);

        match producer.send(record, Duration::from_secs(5)).await {
            Ok((partition, offset)) => {
                log::debug!(
                    "[engine] PredictiveSignal published: symbol={} topic={} \
                     partition={} offset={} predicted={:.2} confidence={:.1}",
                    signal.symbol,
                    topic,
                    partition,
                    offset,
                    signal.predicted_close_price,
                    signal.confidence_score,
                );
            }
            Err((kafka_err, _owned_msg)) => {
                log::error!(
                    "[engine] Failed to publish PredictiveSignal for symbol='{}': {}",
                    signal.symbol,
                    kafka_err,
                );
            }
        }
    }

    // ── Main event loop ──────────────────────────────────────────────────────

    /// Returns the current Unix epoch time in milliseconds.
    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    }

    /// Entry point for the Kafka consume → predict → produce → broadcast loop.
    ///
    /// This function blocks indefinitely, processing each incoming candle
    /// on the `market.ohlc.10m` topic.  After publishing each prediction
    /// to Kafka, the signal is serialized to JSON and sent through `ws_tx`
    /// for WebSocket fan-out on port 8082.
    pub async fn run(prediction_engine: &mut PredictionEngine, ws_tx: broadcast::Sender<String>) {
        // ── Configuration ────────────────────────────────────────────────
        let brokers = std::env::var("KAFKA_BROKER_URL")
            .or_else(|_| std::env::var("KAFKA_BROKERS"))
            .unwrap_or_else(|_| "localhost:19092".to_string());

        let group_id = std::env::var("PREDICTIVE_AGENT_GROUP_ID")
            .unwrap_or_else(|_| "predictive-agent-group".to_string());

        let consume_topic = std::env::var("KAFKA_TOPIC_OHLC")
            .unwrap_or_else(|_| "market.ohlc.10m".to_string());

        let produce_topic = std::env::var("KAFKA_TOPIC_PREDICTIVE")
            .unwrap_or_else(|_| "signals.predictive".to_string());

        log::info!("Kafka broker     : {}", brokers);
        log::info!("Consumer group   : {}", group_id);
        log::info!("Consume topic    : {}", consume_topic);
        log::info!("Produce topic    : {}", produce_topic);

        // ── Initialise Kafka handles ─────────────────────────────────────
        let consumer = init_consumer(&brokers, &group_id, &consume_topic);
        let producer = init_producer(&brokers);

        let mut stream = consumer.stream();

        log::info!("Prediction loop started — waiting for OHLC candles...");
        log::info!("─────────────────────────────────────────────────────");

        // ── Event loop ───────────────────────────────────────────────────
        while let Some(message_result) = stream.next().await {
            match message_result {
                Ok(msg) => {
                    if let Some(payload) = msg.payload() {
                        // ── Decode OHLCCandle ────────────────────────────
                        let candle = match OhlcCandle::decode(payload) {
                            Ok(c) => c,
                            Err(e) => {
                                log::warn!(
                                    "[engine] Protobuf decode error (skipping): {}",
                                    e
                                );
                                continue;
                            }
                        };

                        log::debug!(
                            "[engine] candle: symbol={} close={:.2} end_ts={}",
                            candle.symbol,
                            candle.close,
                            candle.end_timestamp_ms,
                        );

                        // ── Feed into prediction engine ──────────────────
                        prediction_engine.add_close_price(candle.close);

                        // ── Attempt prediction ───────────────────────────
                        if let Some((predicted_close, confidence)) =
                            prediction_engine.predict_next()
                        {
                            let signal = PredictiveSignal {
                                symbol: candle.symbol.clone(),
                                timestamp_ms: now_ms(),
                                target_timestamp_ms: candle.end_timestamp_ms + TEN_MINUTES_MS,
                                predicted_close_price: predicted_close,
                                confidence_score: confidence,
                                model_version: MODEL_VERSION.to_string(),
                            };

                            log::info!(
                                "[prediction] symbol={:<20} predicted={:>10.2}  \
                                 confidence={:>5.1}  target_ts={}",
                                signal.symbol,
                                signal.predicted_close_price,
                                signal.confidence_score,
                                signal.target_timestamp_ms,
                            );

                            // ── Broadcast over WebSocket ─────────────────
                            // Serialize to JSON for the frontend Ghost Line.
                            let json = serde_json::json!({
                                "symbol": signal.symbol,
                                "timestamp_ms": signal.timestamp_ms,
                                "target_timestamp_ms": signal.target_timestamp_ms,
                                "predicted_close_price": signal.predicted_close_price,
                                "confidence_score": signal.confidence_score,
                                "model_version": signal.model_version,
                            });

                            // Best-effort WS broadcast — receivers may be absent.
                            let _ = ws_tx.send(json.to_string());

                            // Fire-and-forget publish in a spawned task so
                            // producer latency doesn't stall the consume loop.
                            let producer_clone = producer.clone();
                            let topic_clone = produce_topic.clone();

                            tokio::spawn(async move {
                                publish_signal(
                                    &producer_clone,
                                    &topic_clone,
                                    &signal,
                                )
                                .await;
                            });
                        }
                    }
                }
                Err(e) => {
                    log::error!("[engine] Kafka consumer error: {}", e);
                }
            }
        }

        log::warn!("OHLC stream closed — predictive agent shutting down.");
    }
}
