// main.rs — Technical Agent entry point.
//
// Responsibilities (Phase 1.3 scaffold → Phase 1.4 indicator engine):
//   1. Load environment variables from .env
//   2. Initialise the Kafka StreamConsumer (subscribed to `market.ticks`)
//   3. Drive the tick listener loop — print each arriving tick symbol to
//      stdout to confirm the end-to-end Kafka connection is live.
//
// Phase 1.4 additions (modules declared below, not yet wired):
//   • state.rs        — SymbolState + MarketState (Arc<RwLock<HashMap>>)
//   • indicators.rs   — incremental RSI (ta crate) + intraday VWAP
//   • signal_engine.rs — RSI/VWAP confluence → TechSignal conviction score
//
// Integration with the Kafka producer (publish TechSignal to
// `signals.technical`) will be completed in Subphases 25-27.

mod indicators;
mod kafka_consumer;
mod proto;
mod signal_engine;
mod state;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────────
    // Load .env (silently ignore if the file is absent — Docker injects env
    // vars directly via env_file / environment in docker-compose.yml).
    dotenvy::dotenv().ok();

    // Initialise structured logging; set RUST_LOG=info in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════╗");
    log::info!("║  Technical Agent — Quantitative Math Engine  ║");
    log::info!("║  Master Phase 1 → Power Phase 1.3            ║");
    log::info!("╚══════════════════════════════════════════════╝");

    // ── Configuration ────────────────────────────────────────────────────────
    let brokers = std::env::var("KAFKA_BROKER_URL")
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let group_id = std::env::var("TECHNICAL_AGENT_GROUP_ID")
        .unwrap_or_else(|_| "technical-agent-group".to_string());

    log::info!("Kafka broker  : {}", brokers);
    log::info!("Consumer group: {}", group_id);

    // ── Kafka Consumer (feature-gated) ───────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        use kafka_consumer::kafka_consumer::{init_consumer, run_listener};

        let consumer = init_consumer(&brokers, &group_id).await;
        let mut rx = run_listener(consumer).await;

        log::info!("Tick stream open. Printing symbols to verify Kafka connection...");
        log::info!("─────────────────────────────────────────────");

        while let Some(tick) = rx.recv().await {
            // Phase 1.3 verification: confirm ticks are arriving and decodable.
            // Phase 1.4 will replace this with indicator computation.
            println!(
                "[TICK] symbol={:<20} ltp={:>10.2}  vol={:>10}  ts_ms={}",
                tick.symbol,
                tick.last_traded_price,
                tick.volume,
                tick.timestamp_ms,
            );
        }

        log::warn!("Tick channel closed — technical agent shutting down.");
    }

    #[cfg(not(feature = "kafka"))]
    {
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature. \
             Run with `cargo run` (default features) for full functionality."
        );
    }
}
