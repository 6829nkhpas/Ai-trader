// main.rs — Quant-RAG Agent entry point.
//
// Perfection Phase 1 — Anomaly Detection + DeepSeek v4 Pro + WS Broadcast (8083).
//
// Pipeline:
//   1. Consume Protobuf-encoded OHLCCandle messages from `market.ohlc.10m`
//   2. Detect anomalies (>= 2% absolute price change)
//   3. Invoke DeepSeek v4 Pro for AI-generated insight
//   4. Publish MarketInsight to Kafka `signals.insights`
//   5. Broadcast the same insight as JSON over WebSocket on port 8083

mod engine;
mod llm;
mod metrics;
mod patterns;
mod proto;
mod ws_server;

use llm::LlmClient;
use log::{error, info};

#[tokio::main]
async fn main() {
    // ── Environment ──────────────────────────────────────────────────────
    // Load .env from the monorepo root (two levels up from agents/quant-rag/).
    // Silently ignore a missing .env — Docker injects variables via env_file.
    dotenvy::from_path("../../.env").ok();

    // Structured logging; set RUST_LOG=info (or debug) in .env or shell.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    info!("╔══════════════════════════════════════════════════╗");
    info!("║  Quant-RAG Agent — NVIDIA NIM × DeepSeek v4 Pro  ║");
    info!("║  Perfection Phase 1 — Insight Pipeline (8083)    ║");
    info!("╚══════════════════════════════════════════════════╝");

    // ── Metrics ──────────────────────────────────────────────────────────
    // Started before everything else, including the LLM client. A missing
    // LLM_API_KEY exits the process below, and the whole point of a monitoring
    // surface is that a service which refuses to start is distinguishable from
    // one that was never deployed — that only holds if the listener is already
    // up when the exit happens.
    let metrics = metrics::QuantRagMetrics::new();
    metrics.serve();

    // ── Initialise the LLM client ────────────────────────────────────────
    // The metrics handle is held by the client so each LLM failure is recorded
    // at the point it is detected — network, HTTP status, envelope JSON,
    // missing content, malformed model output — rather than being guessed at
    // from the text of the resulting error.
    let llm_client = match LlmClient::new(metrics.clone()) {
        Ok(c) => {
            info!("✅ LlmClient initialized — LLM_API_KEY loaded");
            c
        }
        Err(e) => {
            error!("❌ Failed to initialize LlmClient: {}", e);
            std::process::exit(1);
        }
    };

    // ── Broadcast channel for WebSocket fan-out ──────────────────────────
    let (ws_tx, _) = tokio::sync::broadcast::channel::<String>(100);

    // ── Spawn the WebSocket server on port 8083 ──────────────────────────
    let ws_tx_clone = ws_tx.clone();
    let ws_metrics = metrics.clone();
    tokio::spawn(async move {
        ws_server::start_server(8083, ws_tx_clone.subscribe(), ws_metrics).await;
    });

    // ── Kafka-gated block ────────────────────────────────────────────────
    #[cfg(feature = "kafka")]
    {
        engine::engine::run(&llm_client, ws_tx, metrics).await;
    }

    #[cfg(not(feature = "kafka"))]
    {
        let _ = ws_tx; // suppress unused-variable warning
        info!("⚠️  Binary built WITHOUT the 'kafka' feature (--no-default-features).");
        info!("   Run with `cargo run` (default features enabled) for full functionality.");
        info!("   LLM client is ready — Kafka consumer loop is disabled.");

        // Keep the process alive so the WS server keeps running for testing.
        info!("⏸️  Agent idle — serving WebSocket on port 8083 only.");
        tokio::signal::ctrl_c().await.ok();
        info!("Shutting down.");
    }
}
