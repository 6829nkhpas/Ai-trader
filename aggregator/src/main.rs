// main.rs — Aggregator Decision Engine entry point.
//
// Master Phase 1 → Power Phase 1.5 → Subphases 37-42.
//
// Initializes the central decision engine with:
//   - AggregatorState — caches latest sentiment per symbol (SP40)
//   - engine          — dynamic weighting & conflict resolution (SP41)
//   - consumer        — multi-topic Kafka consumer with integrated state (SP42)
//
// Consumer loop routes incoming messages:
//   - `sentiment_signals` → update AggregatorState
//   - `technical_signals` → read AggregatorState → calculate_decision → println!

mod consumer;
mod engine;
mod proto;
mod state;

use state::AggregatorState;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────────
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::dotenv().ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════╗");
    log::info!("║  Aggregator — Central Decision Engine        ║");
    log::info!("║  Master Phase 1 → Power Phase 1.5  SP 37-42  ║");
    log::info!("╚══════════════════════════════════════════════╝");

    // ── Configuration ────────────────────────────────────────────────────────
    let brokers = std::env::var("KAFKA_BROKER_URL")
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let group_id = std::env::var("AGGREGATOR_GROUP_ID")
        .unwrap_or_else(|_| "aggregator-group".to_string());

    log::info!("Kafka broker   : {}", brokers);
    log::info!("Consumer group : {}", group_id);

    // ── Aggregator State (SP40) ──────────────────────────────────────────────
    // Shared sentiment cache: updated by sentiment consumer, read by tech consumer.
    let agg_state = AggregatorState::new();
    log::info!("AggregatorState initialised (sentiment cache ready)");

    // ── Kafka-gated block ─────────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        use consumer::consumer::{init_consumer, run_consumer_loop};

        let consumer = init_consumer(&brokers, &group_id).await;

        log::info!("All subsystems initialised. Entering aggregator consumer loop...");
        log::info!("─────────────────────────────────────────────────────────");

        run_consumer_loop(consumer, &agg_state).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        // Suppress unused variable warning when Kafka feature is off.
        let _ = agg_state;
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
