// engine.rs — Kafka Consumer & Producer loop for the Quant-RAG Agent.
//
// Phase 9.2 — Anomaly Detection & Gemini LLM Insight Pipeline.
//
// Pipeline:
//   1. Consume Protobuf-encoded OHLCCandle messages from `market.ohlc.10m`.
//   2. For each candle, compute absolute % change: |close − open| / open × 100.
//   3. If change_pct >= 2.0% (anomaly threshold), invoke the Gemini LLM client.
//   4. Construct a MarketInsight Protobuf payload and publish to `signals.insights`.
//   5. Serialize the insight to JSON and broadcast over the WS channel (port 8083).
//
// The consumer uses `auto.offset.reset = "latest"` so only real-time candles
// are processed (no historical replay).  The producer uses low-latency
// buffering (5 ms) to prioritise freshness over throughput.

#[cfg(feature = "kafka")]
pub mod engine {
    use crate::llm::LlmClient;
    use crate::proto::insight_data::MarketInsight;
    use crate::proto::market_data::OhlcCandle;
    use futures_util::StreamExt;
    use prost::Message;
    use rdkafka::config::ClientConfig;
    use rdkafka::consumer::{Consumer, StreamConsumer};
    use rdkafka::message::Message as KafkaMessage;
    use rdkafka::producer::{FutureProducer, FutureRecord};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    use tokio::sync::broadcast;

    // ── Constants ────────────────────────────────────────────────────────────

    /// Anomaly threshold — absolute % change required to trigger an LLM insight.
    const ANOMALY_THRESHOLD_PCT: f64 = 2.0;

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

    /// Creates a Kafka [`FutureProducer`] for publishing MarketInsight messages.
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

    // ── Insight publishing ───────────────────────────────────────────────────

    /// Encodes a [`MarketInsight`] and publishes it to the given topic,
    /// keyed by the insight's symbol for partition co-locality.
    async fn publish_insight(producer: &FutureProducer, topic: &str, insight: &MarketInsight) {
        let payload = insight.encode_to_vec();
        let key = insight.symbol.as_str();

        let record = FutureRecord::to(topic)
            .payload(payload.as_slice())
            .key(key);

        match producer.send(record, Duration::from_secs(5)).await {
            Ok((partition, offset)) => {
                log::debug!(
                    "[engine] MarketInsight published: symbol={} topic={} \
                     partition={} offset={} sentiment={}",
                    insight.symbol,
                    topic,
                    partition,
                    offset,
                    insight.sentiment_score,
                );
            }
            Err((kafka_err, _owned_msg)) => {
                log::error!(
                    "[engine] Failed to publish MarketInsight for symbol='{}': {}",
                    insight.symbol,
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

    /// Entry point for the Kafka consume → detect anomaly → LLM → produce → broadcast loop.
    ///
    /// This function blocks indefinitely, processing each incoming candle
    /// on the `market.ohlc.10m` topic.  When an anomaly (>= 2% absolute change)
    /// is detected, the Gemini LLM is invoked, and the resulting insight is:
    ///   1. Published to Kafka `signals.insights` as Protobuf.
    ///   2. Serialized to JSON and sent through `ws_tx` for WebSocket fan-out on port 8083.
    pub async fn run(llm_client: &LlmClient, ws_tx: broadcast::Sender<String>) {
        // ── Configuration ────────────────────────────────────────────────
        let brokers = std::env::var("KAFKA_BROKER_URL")
            .unwrap_or_else(|_| "localhost:9092".to_string());

        let group_id = std::env::var("QUANT_RAG_GROUP_ID")
            .unwrap_or_else(|_| "quant-rag-agent-group".to_string());

        let consume_topic = std::env::var("KAFKA_TOPIC_OHLC")
            .unwrap_or_else(|_| "market.ohlc.10m".to_string());

        let produce_topic = std::env::var("KAFKA_TOPIC_INSIGHTS")
            .unwrap_or_else(|_| "signals.insights".to_string());

        log::info!("Kafka broker     : {}", brokers);
        log::info!("Consumer group   : {}", group_id);
        log::info!("Consume topic    : {}", consume_topic);
        log::info!("Produce topic    : {}", produce_topic);
        log::info!("Anomaly threshold: >= {:.1}%", ANOMALY_THRESHOLD_PCT);

        // ── Initialise Kafka handles ─────────────────────────────────────
        let consumer = init_consumer(&brokers, &group_id, &consume_topic);
        let producer = init_producer(&brokers);

        let mut stream = consumer.stream();

        log::info!("Anomaly detection loop started — waiting for OHLC candles...");
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

                        // ── Anomaly detection ────────────────────────────
                        // Guard against division by zero
                        if candle.open == 0.0 {
                            log::warn!(
                                "[engine] candle.open is zero for symbol={}, skipping",
                                candle.symbol
                            );
                            continue;
                        }

                        let change_pct = ((candle.close - candle.open) / candle.open).abs() * 100.0;

                        log::debug!(
                            "[engine] candle: symbol={} open={:.2} close={:.2} change={:.2}%",
                            candle.symbol,
                            candle.open,
                            candle.close,
                            change_pct,
                        );

                        if change_pct < ANOMALY_THRESHOLD_PCT {
                            continue;
                        }

                        // ── Anomaly detected! Invoke Gemini LLM ──────────
                        log::info!(
                            "🚨 ANOMALY DETECTED: symbol={} change={:.2}% — invoking Gemini...",
                            candle.symbol,
                            change_pct,
                        );

                        let signed_change = if candle.close >= candle.open {
                            change_pct
                        } else {
                            -change_pct
                        };

                        match llm_client.generate_insight(&candle.symbol, signed_change).await {
                            Ok((headline, analysis, sentiment)) => {
                                let insight = MarketInsight {
                                    symbol: candle.symbol.clone(),
                                    timestamp_ms: now_ms(),
                                    headline: headline.clone(),
                                    analysis_text: analysis.clone(),
                                    sentiment_score: sentiment,
                                    anomaly_pct: change_pct,
                                };

                                log::info!(
                                    "🧠 INSIGHT GENERATED: symbol={} headline=\"{}\" sentiment={}/100",
                                    insight.symbol,
                                    insight.headline,
                                    insight.sentiment_score,
                                );

                                // ── Broadcast over WebSocket (port 8083) ─────
                                let json = serde_json::json!({
                                    "symbol": insight.symbol,
                                    "timestamp_ms": insight.timestamp_ms,
                                    "headline": insight.headline,
                                    "analysis_text": insight.analysis_text,
                                    "sentiment_score": insight.sentiment_score,
                                    "anomaly_pct": insight.anomaly_pct,
                                });

                                // Best-effort WS broadcast — receivers may be absent.
                                let _ = ws_tx.send(json.to_string());

                                // ── Publish to Kafka signals.insights ─────────
                                // Fire-and-forget in a spawned task so producer
                                // latency doesn't stall the consume loop.
                                let producer_clone = producer.clone();
                                let topic_clone = produce_topic.clone();

                                tokio::spawn(async move {
                                    publish_insight(
                                        &producer_clone,
                                        &topic_clone,
                                        &insight,
                                    )
                                    .await;
                                });
                            }
                            Err(e) => {
                                log::error!(
                                    "❌ Gemini LLM call failed for symbol={}: {}",
                                    candle.symbol,
                                    e,
                                );
                            }
                        }
                    }
                }
                Err(e) => {
                    log::error!("[engine] Kafka consumer error: {}", e);
                }
            }
        }

        log::warn!("OHLC stream closed — Quant-RAG agent shutting down.");
    }
}
