// main.rs — Aggregator Decision Engine entry point.
//
// Master Phase 1 → Power Phase 1.5 → Subphases 37-45.
//
// Initializes the central decision engine with:
//   - AggregatorState — caches latest sentiment per symbol (SP40)
//   - engine          — dynamic weighting & conflict resolution (SP41)
//   - consumer        — multi-topic Kafka consumer with integrated state (SP42)
//   - kafka_producer  — publishes AggregatedDecision protobuf to Kafka (SP43)
//   - ws_server       — broadcasts JSON decisions to Next.js frontend (SP44)
//   - broadcast channel — bridges consumer loop → WebSocket clients (SP45)
//
// Consumer loop routes incoming messages:
//   - `sentiment_signals` → update AggregatorState
//   - `technical_signals` → read AggregatorState → calculate_decision
//       → (1) Kafka publish (protobuf) to `trade_decisions`
//       → (2) WebSocket broadcast (JSON) to all connected frontends

mod consumer;
mod engine;
mod kafka_producer;
mod proto;
mod state;
mod ws_server;

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
    log::info!("║  Master Phase 1 → Power Phase 1.5  SP 37-45  ║");
    log::info!("╚══════════════════════════════════════════════╝");

    // ── Configuration ────────────────────────────────────────────────────────
    let brokers = std::env::var("KAFKA_BROKER_URL")
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let group_id = std::env::var("AGGREGATOR_GROUP_ID")
        .unwrap_or_else(|_| "aggregator-group".to_string());

    let ws_port = std::env::var("WEBSOCKET_PORT")
        .unwrap_or_else(|_| "8080".to_string());

    log::info!("Kafka broker   : {}", brokers);
    log::info!("Consumer group : {}", group_id);
    log::info!("WebSocket port : {}", ws_port);

    // ── Aggregator State (SP40) ──────────────────────────────────────────────
    // Shared sentiment cache: updated by sentiment consumer, read by tech consumer.
    let agg_state = AggregatorState::new();
    log::info!("AggregatorState initialised (sentiment cache ready)");

    // ── Broadcast Channel (SP45) ─────────────────────────────────────────────
    // Bridges the consumer loop to all connected WebSocket clients.
    // Capacity 100: prevents slow WS clients from blocking the decision pipeline.
    // `tx` is cloned into the consumer loop; receivers are created per WS client.
    let (tx, _) = tokio::sync::broadcast::channel::<String>(100);

    // ── WebSocket Server (SP44) ──────────────────────────────────────────────
    // Spawn in a background task — runs forever, accepting WS connections.
    // Receives a subscriber from the broadcast channel to forward JSON decisions.
    let ws_rx = tx.subscribe();
    tokio::spawn(async move {
        ws_server::start_server(&ws_port, ws_rx).await;
    });
    log::info!("WebSocket server spawned (background task)");

    // ── Kafka-gated block ─────────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        use consumer::consumer::{init_consumer, run_consumer_loop};
        use kafka_producer::producer::init_producer;

        // ── Kafka Producer (SP43) ────────────────────────────────────────────
        // FutureProducer for publishing AggregatedDecision protobuf to Kafka.
        let producer = init_producer(&brokers);
        log::info!("Kafka FutureProducer initialised (decision publisher ready)");

        let consumer = init_consumer(&brokers, &group_id).await;

        log::info!("All subsystems initialised. Entering aggregator consumer loop...");
        log::info!("─────────────────────────────────────────────────────────");

        run_consumer_loop(consumer, &agg_state, producer, tx).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        // Suppress unused variable warnings when Kafka feature is off.
        let _ = agg_state;
        let _ = tx;
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
