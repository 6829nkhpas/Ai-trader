// main.rs — Aggregator Decision Engine entry point.
//
// Master Phase 1 → Power Phase 1.5 → Subphases 37-39.
//
// Initializes the central decision engine and establishes a multi-topic
// Kafka consumer that asynchronously processes streams from:
//   - `technical_signals` → TechSignal Protobuf
//   - `sentiment_signals` → NewsSentiment Protobuf
//
// The consumer routes each incoming message to the correct decoder based
// on msg.topic() and prints the decoded struct for verification.

mod consumer;
mod proto;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────────
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::dotenv().ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════╗");
    log::info!("║  Aggregator — Central Decision Engine        ║");
    log::info!("║  Master Phase 1 → Power Phase 1.5  SP 37-39  ║");
    log::info!("╚══════════════════════════════════════════════╝");

    // ── Configuration ────────────────────────────────────────────────────────
    let brokers = std::env::var("KAFKA_BROKER_URL")
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let group_id = std::env::var("AGGREGATOR_GROUP_ID")
        .unwrap_or_else(|_| "aggregator-group".to_string());

    log::info!("Kafka broker   : {}", brokers);
    log::info!("Consumer group : {}", group_id);

    // ── Kafka-gated block ─────────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        use consumer::consumer::{init_consumer, run_consumer_loop};

        let consumer = init_consumer(&brokers, &group_id).await;

        log::info!("All subsystems initialised. Entering aggregator consumer loop...");
        log::info!("─────────────────────────────────────────────────────────");

        run_consumer_loop(consumer).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
