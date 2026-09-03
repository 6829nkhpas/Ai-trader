// main.rs — Predictive Agent entry point.
//
// Phase 6.3 — Strat Ai Linear Regression + Ghost Candle Broadcast.
//
// Pipeline:
//   1. Consume Protobuf-encoded OHLCCandle messages from `market.ohlc.10m`
//   2. Feed each candle's close into a 14-period rolling window
//   3. Run OLS linear regression to predict the next candle's close
//   4. Publish PredictiveSignal (with R²-based confidence) to Kafka `signals.predictive`
//   5. Broadcast the same signal as JSON over WebSocket on port 8082

mod engine;
mod math;
mod metrics;
mod proto;
mod ws_server;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::dotenv().ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════════╗");
    log::info!("║  Predictive Agent — Linear Regression Engine     ║");
    log::info!("║  Phase 6.3 — Ghost Candle WS Broadcast (8082)    ║");
    log::info!("╚══════════════════════════════════════════════════╝");

    // ── Metrics ──────────────────────────────────────────────────────────
    // Started before the WS server and the Kafka loop so that /health answers
    // during boot. If the agent then wedges while connecting to Kafka, the
    // probe reports an unready service rather than a connection refused —
    // the difference between a diagnosable state and a blank panel.
    let metrics = metrics::PredictiveMetrics::new();
    metrics.serve();

    // ── Broadcast channel for WebSocket fan-out ──────────────────────────
    let (ws_tx, _) = tokio::sync::broadcast::channel::<String>(100);

    // ── Spawn the WebSocket server on port 8082 ──────────────────────────
    let ws_tx_clone = ws_tx.clone();
    let ws_metrics = metrics.clone();
    tokio::spawn(async move {
        ws_server::start_server(8082, ws_tx_clone.subscribe(), ws_metrics).await;
    });

    // ── Per-symbol math engines ──────────────────────────────────────────
    // One rolling OLS window per symbol. A shared engine mixed closes across
    // instruments and produced wild predicted_close spikes on the Ghost Line.
    let mut prediction_engines = std::collections::HashMap::<String, math::PredictionEngine>::new();

    // ── Kafka-gated block ────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        engine::engine::run(&mut prediction_engines, ws_tx, metrics).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        let _ = (ws_tx, prediction_engines); // suppress unused-variable warning
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
