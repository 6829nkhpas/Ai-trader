// engine.rs — Kafka Consumer & Producer loop for the Predictive Agent.
//
// Phase 6.3 — Strat Ai Event Loop + WebSocket Broadcast.
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
    use crate::metrics::PredictiveMetrics;
    use crate::proto::market_data::OhlcCandle;
    use crate::proto::predictive_data::PredictiveSignal;
    use futures_util::StreamExt;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::consumer::{Consumer, StreamConsumer};
    use rdkafka::message::Message as KafkaMessage;
    use rdkafka::producer::{FutureProducer, FutureRecord};
    use std::collections::HashMap;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use tokio::sync::broadcast;

    // ── Constants ────────────────────────────────────────────────────────────
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
    ///
    /// Returns whether the send succeeded. Drop behaviour is unchanged — a
    /// failed publish is still logged and abandoned — but the caller needs the
    /// outcome to count predictions that were computed and then lost, which
    /// `predictions_total` alone would hide.
    async fn publish_signal(
        producer: &FutureProducer,
        topic: &str,
        signal: &PredictiveSignal,
    ) -> bool {
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
                true
            }
            Err((kafka_err, _owned_msg)) => {
                log::error!(
                    "[engine] Failed to publish PredictiveSignal for symbol='{}': {}",
                    signal.symbol,
                    kafka_err,
                );
                false
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

    /// Assemble the signal for a prediction made after consuming `candle`.
    ///
    /// `predict_next` fits `x = 0..14` over the last 14 closes and evaluates the
    /// line at `x = 14`: that is the close of the candle immediately AFTER
    /// `candle`, whose close time is `candle.end_timestamp_ms + 10min`. The
    /// previous code stamped `target_timestamp_ms` with `end_timestamp_ms +
    /// 10min` too, but `alpha-terminal` sets `end_timestamp_ms = start + 10min`
    /// — the candle's own close — so the target landed one bar past the bar the
    /// regression actually predicted. The anchor is `candle` itself, the last
    /// point on the fitted line.
    fn build_signal(candle: &OhlcCandle, predicted_close: f64, confidence: f64) -> PredictiveSignal {
        let bar_ms = candle.end_timestamp_ms.saturating_sub(candle.start_timestamp_ms);
        PredictiveSignal {
            symbol: candle.symbol.clone(),
            timestamp_ms: now_ms(),
            target_timestamp_ms: candle.end_timestamp_ms + bar_ms,
            predicted_close_price: predicted_close,
            confidence_score: confidence,
            model_version: MODEL_VERSION.to_string(),
            anchor_timestamp_ms: candle.end_timestamp_ms,
            anchor_close: candle.close,
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn target_is_the_close_of_the_next_candle_and_anchor_is_this_one() {
            let candle = OhlcCandle {
                symbol: "NIFTY".into(),
                start_timestamp_ms: 1_000_000,
                end_timestamp_ms: 1_600_000,
                close: 24_000.5,
                ..Default::default()
            };
            let s = build_signal(&candle, 24_010.0, 80.0);
            assert_eq!(s.anchor_timestamp_ms, 1_600_000);
            assert_eq!(s.anchor_close, 24_000.5);
            assert_eq!(s.target_timestamp_ms, 2_200_000);
            assert_eq!(s.target_timestamp_ms - s.anchor_timestamp_ms, 600_000);
        }
    }

    /// Entry point for the Kafka consume → predict → produce → broadcast loop.
    ///
    /// This function blocks indefinitely, processing each incoming candle
    /// on the `market.ohlc.10m` topic.  After publishing each prediction
    /// to Kafka, the signal is serialized to JSON and sent through `ws_tx`
    /// for WebSocket fan-out on port 8082.
    ///
    /// Each symbol gets its own [`PredictionEngine`]. A single shared window
    /// used to mix closes across instruments (e.g. NIFTY ~25k with a ₹500
    /// equity) and emit wild `predicted_close_price` spikes.
    ///
    /// `metrics` records input, output, and both failure modes. The work
    /// heartbeat beats on each *consumed candle*, not on each published
    /// prediction: the regression needs 14 candles before it can predict at all,
    /// so beating on output would report a stall for the whole 140-minute
    /// warm-up after every restart.
    pub async fn run(
        engines: &mut HashMap<String, PredictionEngine>,
        ws_tx: broadcast::Sender<String>,
        metrics: PredictiveMetrics,
    ) {
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
                                metrics.decode_failed();
                                continue;
                            }
                        };

                        log::debug!(
                            "[engine] candle: symbol={} close={:.2} end_ts={}",
                            candle.symbol,
                            candle.close,
                            candle.end_timestamp_ms,
                        );

                        // ── Feed into per-symbol prediction engine ───────
                        let engine = engines
                            .entry(candle.symbol.clone())
                            .or_insert_with(PredictionEngine::new);
                        if !engine.add_close_price(candle.close) {
                            log::warn!(
                                "[engine] skipping non-finite/non-positive close for symbol={}: {}",
                                candle.symbol,
                                candle.close,
                            );
                            continue;
                        }

                        // The work point. Reported after the window advances so
                        // window_fill and the candle count can never disagree.
                        metrics.candle_consumed(engine.window_fill());

                        // ── Attempt prediction ───────────────────────────
                        if let Some((predicted_close, confidence)) = engine.predict_next()
                        {
                            if !predicted_close.is_finite() || predicted_close <= 0.0 {
                                log::warn!(
                                    "[engine] skipping non-finite/non-positive prediction for symbol={}",
                                    candle.symbol,
                                );
                                continue;
                            }
                            let signal = build_signal(&candle, predicted_close, confidence);

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
                                "anchor_timestamp_ms": signal.anchor_timestamp_ms,
                                "anchor_close": signal.anchor_close,
                            });

                            // Counted before the spawn: the prediction exists at
                            // this point regardless of whether the publish that
                            // follows succeeds, and the two are separate facts.
                            metrics.prediction_emitted(signal.confidence_score);

                            // Best-effort WS broadcast — receivers may be absent.
                            let _ = ws_tx.send(json.to_string());

                            // Fire-and-forget publish in a spawned task so
                            // producer latency doesn't stall the consume loop.
                            let producer_clone = producer.clone();
                            let topic_clone = produce_topic.clone();
                            let pub_metrics = metrics.clone();

                            tokio::spawn(async move {
                                let published = publish_signal(
                                    &producer_clone,
                                    &topic_clone,
                                    &signal,
                                )
                                .await;
                                if !published {
                                    // The prediction was computed and then lost
                                    // in transit — the aggregator never saw it.
                                    pub_metrics.publish_failed();
                                }
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
