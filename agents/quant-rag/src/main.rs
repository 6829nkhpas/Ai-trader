mod llm;

use llm::LlmClient;
use log::{error, info};

#[tokio::main]
async fn main() {
    // Load .env from the monorepo root (two levels up from agents/quant-rag/)
    dotenvy::from_path("../../.env").ok();
    env_logger::init();

    info!("🚀 Quant-RAG Agent starting (Gemini 1.5 Flash)...");

    let client = match LlmClient::new() {
        Ok(c) => {
            info!("✅ LlmClient initialized — GEMINI_API_KEY loaded");
            c
        }
        Err(e) => {
            error!("❌ Failed to initialize LlmClient: {}", e);
            std::process::exit(1);
        }
    };

    // Phase 9.1 — Smoke test: generate a sample insight
    info!("📡 Running Gemini API smoke test...");
    match client.generate_insight("NIFTY50", -2.35).await {
        Ok((headline, analysis, sentiment)) => {
            info!("── Gemini Insight ──────────────────────────────");
            info!("  Headline:  {}", headline);
            info!("  Analysis:  {}", analysis);
            info!("  Sentiment: {}/100", sentiment);
            info!("────────────────────────────────────────────────");
            info!("✅ Phase 9.1 smoke test passed.");
        }
        Err(e) => {
            error!("❌ Gemini API call failed: {}", e);
            error!("   Verify GEMINI_API_KEY is set correctly in .env");
            std::process::exit(1);
        }
    }

    // Phase 9.2 will add: Kafka consumer loop on `anomalies` topic
    // and broadcast insights to `insights` topic / Edge Terminal WebSocket.
    info!("⏸️  Agent idle — Kafka consumer loop will be wired in Phase 9.2.");
}
