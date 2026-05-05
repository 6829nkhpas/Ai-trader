// main.rs — Predictive Agent entry point.
//
// Phase 6.1 & 6.2 — Alpha Suite Linear Regression Prediction Engine.
//
// Pipeline:
//   1. Consume Protobuf-encoded OHLCCandle messages from `market.ohlc.10m`
//   2. Feed each candle's close into a 14-period rolling window
//   3. Run OLS linear regression to predict the next candle's close
//   4. Publish PredictiveSignal (with R²-based confidence) to `signals.predictive`
//
// The engine is single-symbol for now; per-symbol state will be added in
// Phase 6.3 if multi-symbol OHLC streams are introduced.

mod engine;
mod math;
mod proto;

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::dotenv().ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("╔══════════════════════════════════════════════════╗");
    log::info!("║  Predictive Agent — Linear Regression Engine     ║");
    log::info!("╚══════════════════════════════════════════════════╝");

    // ── Instantiate the math engine ──────────────────────────────────────
    let mut prediction_engine = math::PredictionEngine::new();

    // ── Kafka-gated block ────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        engine::engine::run(&mut prediction_engine).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        log::warn!(
            "Binary built WITHOUT the 'kafka' feature (--no-default-features). \
             Run with `cargo run` (default features enabled) for full functionality."
        );
    }
}
