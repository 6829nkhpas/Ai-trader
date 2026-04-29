// main.rs — Technical Agent entry point.
//
// Power Phase 1.3 — Subphases 25-27: Fully Operational Event Loop.
//
// Pipeline on each incoming Tick:
//   1. Decode Protobuf Tick from Kafka (market.ticks)
//   2. Look up / create SymbolState for tick.symbol
//   3. Compute volume_delta (cumulative volume is Kite-reported; we track prev)
//   4. update_rsi()  — feed LTP into ta::RSI, gate on 14-tick warm-up
//   5. update_vwap() — update intraday accumulators with volume_delta
//   6. When both indicators are ready: evaluate_signal() → TechSignal Protobuf
//   7. tokio::spawn → kafka_producer::publish_signal() → signals.technical topic
//
// Thread-safety:
//   MarketState is Arc<RwLock<HashMap>> — single writer (this loop), safe to
//   share clones with spawned publish tasks via Arc::clone.
//
// Topic note:
//   `signals.technical` is created by the `kafka-init` one-shot container in
//   docker-compose.yml.  If auto-topic creation is enabled on the broker it
//   will also be created on first publish without any manual intervention.

mod indicators;
mod kafka_consumer;
mod kafka_producer;
mod proto;
mod signal_engine;
mod state;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────────
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::dotenv().ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════╗");
    log::info!("║  Technical Agent — Quantitative Math Engine  ║");
    log::info!("║  Master Phase 1 → Power Phase 1.3  SP 25-27  ║");
    log::info!("╚══════════════════════════════════════════════╝");

    // ── Configuration ────────────────────────────────────────────────────────
    let brokers = std::env::var("KAFKA_BROKER_URL")
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let group_id = std::env::var("TECHNICAL_AGENT_GROUP_ID")
        .unwrap_or_else(|_| "technical-agent-group".to_string());

    let signal_topic = std::env::var("KAFKA_TOPIC_SIGNALS")
        .unwrap_or_else(|_| "signals.technical".to_string());

    log::info!("Kafka broker   : {}", brokers);
    log::info!("Consumer group : {}", group_id);
    log::info!("Signal topic   : {}", signal_topic);

    // ── Kafka-gated block ─────────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        use kafka_consumer::kafka_consumer::{init_consumer, run_listener};
        use kafka_producer::kafka_producer::{init_producer, publish_signal};
        use indicators::{update_rsi, update_vwap};
        use signal_engine::evaluate_signal;
        use state::{MarketState, SymbolState};
        use std::collections::HashMap;
        use std::sync::Arc;
        use tokio::sync::RwLock;

        // ── State initialisation ──────────────────────────────────────────────
        // Shared in-memory state: Arc<RwLock<HashMap<symbol, SymbolState>>>.
        // The RwLock allows multiple concurrent readers (e.g. future REST API)
        // with exclusive writes from this processing loop.
        let market_state: Arc<RwLock<HashMap<String, SymbolState>>> =
            MarketState::new().shared();

        // ── Kafka Consumer ────────────────────────────────────────────────────
        let consumer = init_consumer(&brokers, &group_id).await;
        let mut rx = run_listener(consumer).await;

        // ── Kafka Producer ────────────────────────────────────────────────────
        // FutureProducer is internally Arc-backed → cheap to clone into tasks.
        let producer = init_producer(&brokers);

        log::info!("All subsystems initialised. Entering main event loop...");
        log::info!("─────────────────────────────────────────────────────────");

        // ── Main event loop ───────────────────────────────────────────────────
        while let Some(tick) = rx.recv().await {
            let symbol = tick.symbol.clone();
            let price  = tick.last_traded_price;
            let vol    = tick.volume as u64;   // cumulative intraday volume (u64)
            let ts_ms  = tick.timestamp_ms;

            log::debug!(
                "[tick] symbol={} ltp={:.2} vol={} ts_ms={}",
                symbol, price, vol, ts_ms
            );

            // ── Write lock: update SymbolState ────────────────────────────────
            let (rsi_opt, vwap_opt) = {
                let mut state_map = market_state.write().await;

                // Get-or-insert the per-symbol state entry.
                let sym_state = state_map
                    .entry(symbol.clone())
                    .or_insert_with(SymbolState::new);

                // Compute volume delta from the previous cumulative tick volume.
                // On first tick for this symbol prev_volume = 0, so delta = vol.
                let volume_delta = vol.saturating_sub(sym_state.prev_cumulative_volume);
                sym_state.prev_cumulative_volume = vol;

                // Feed LTP into RSI; returns Some once 14 prices have been seen.
                let rsi = update_rsi(sym_state, price);

                // Accumulate VWAP using the delta volume (not cumulative).
                let vwap = update_vwap(sym_state, price, volume_delta);

                (rsi, vwap)
            }; // write lock released here

            // ── Publish only when both indicators are ready ────────────────────
            // RSI requires 14 prices (warm-up); VWAP requires at least 1 volume tick.
            if let (Some(rsi), Some(vwap)) = (rsi_opt, vwap_opt) {
                let signal = evaluate_signal(&symbol, rsi, vwap, price, ts_ms);

                log::info!(
                    "[signal] symbol={:<20} rsi={:>6.2}  vwap={:>10.2}  \
                     price={:>10.2}  score={:>3}",
                    signal.symbol,
                    signal.rsi_value,
                    vwap,
                    price,
                    signal.technical_conviction_score,
                );

                // Clone producer + topic + signal into a fire-and-forget task.
                // This prevents publish latency from blocking the consume loop.
                let producer_clone  = producer.clone();
                let topic_clone     = signal_topic.clone();
                let signal_clone    = signal;

                tokio::spawn(async move {
                    publish_signal(&producer_clone, &topic_clone, &signal_clone).await;
                });
            }
        }

        log::warn!("Tick channel closed — technical agent shutting down.");
    }

    #[cfg(not(feature = "kafka"))]
    {
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
