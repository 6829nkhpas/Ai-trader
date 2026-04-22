// src/main.rs — AI-Trade Ingestion Service entry point
//
// Pipeline topology:
//
//   [Kite WebSocket] ──binary ticks──► [kite_ws::run]
//                                              │  mpsc channel (capacity 10_000)
//                                              ▼
//                                     [pipeline_task]
//                                      ├─► [KafkaProducer]   → topic: market.ticks  (feature: kafka)
//                                      └─► [QuestDbWriter]   → ILP TCP :9009
//
// The kite_ws task and pipeline task run concurrently via tokio::spawn.
// A SIGINT/SIGTERM handler triggers graceful shutdown.
//
// Feature flags:
//   kafka (default = on) — enables rdkafka / KafkaProducer; disable to cargo check
//                          on Windows without CMake installed.

// ── Module declarations ──────────────────────────────────────────────────────
mod proto;          // Protobuf contract — must be first (others depend on crate::proto)
mod kite_client;    // Low-level WS transport: connect_ticker()
mod parser;         // Binary tick frame parser: parse_binary_tick() / parse_binary_frame()
mod kite_auth;      // OAuth access_token exchange
mod kite_ws;        // High-level WS client: subscription + auto-reconnect loop
mod questdb_writer; // ILP TCP writer → QuestDB :9009
mod types;          // ParsedTick — shared internal data contract

#[cfg(feature = "kafka")]
mod kafka_producer; // rdkafka FutureProducer → market.ticks (requires CMake)

// ── Imports ──────────────────────────────────────────────────────────────────
use std::collections::HashMap;
use log::{error, info};
use tokio::sync::mpsc;
use tokio::signal;

#[cfg(feature = "kafka")]
use kafka_producer::KafkaProducer;
use questdb_writer::QuestDbWriter;
use types::ParsedTick;

/// Channel buffer: holds up to 10,000 ticks for burst absorption without blocking the WS reader
const CHANNEL_CAPACITY: usize = 10_000;

#[tokio::main]
async fn main() {
    // ── 1. Load environment ─────────────────────────────────────────────────
    dotenvy::dotenv().ok();
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    info!("╔══════════════════════════════════════════════════╗");
    info!("║       AI-Trade Ingestion Service Starting        ║");
    info!("╠══════════════════════════════════════════════════╣");
    info!("║  Kite WS  →  Kafka (market.ticks)               ║");
    info!("║  Kite WS  →  QuestDB ILP (:9009)                ║");
    info!("╚══════════════════════════════════════════════════╝");

    // ── 2. Read required config from environment ────────────────────────────
    let api_key = std::env::var("KITE_API_KEY")
        .expect("KITE_API_KEY must be set in .env");

    // Access token: either pre-set in .env, or generate via request_token exchange
    let access_token = match std::env::var("KITE_ACCESS_TOKEN") {
        Ok(token) if !token.is_empty() => {
            info!("Using KITE_ACCESS_TOKEN from environment");
            token
        }
        _ => {
            // Attempt to generate from request_token (set after OAuth redirect)
            let api_secret = std::env::var("KITE_API_SECRET")
                .expect("KITE_API_SECRET must be set when KITE_ACCESS_TOKEN is absent");
            let request_token = std::env::var("KITE_REQUEST_TOKEN")
                .expect("KITE_REQUEST_TOKEN must be set when KITE_ACCESS_TOKEN is absent");

            info!("Generating access token via request_token exchange...");
            kite_auth::generate_access_token(&api_key, &api_secret, &request_token)
                .await
                .expect("Failed to generate Kite access token")
        }
    };

    // ── 3. Build instrument token → symbol map ─────────────────────────────
    // KITE_INSTRUMENT_TOKENS = "738561:RELIANCE,260105:BANKNIFTY,256265:NIFTY 50"
    let tokens_env = std::env::var("KITE_INSTRUMENT_TOKENS")
        .unwrap_or_else(|_| "738561:RELIANCE,260105:BANKNIFTY".to_string());

    let mut symbol_map: HashMap<u32, String> = HashMap::new();
    let mut instrument_tokens: Vec<u32> = Vec::new();

    for pair in tokens_env.split(',') {
        let parts: Vec<&str> = pair.trim().splitn(2, ':').collect();
        if parts.len() == 2 {
            if let Ok(token) = parts[0].parse::<u32>() {
                symbol_map.insert(token, parts[1].to_string());
                instrument_tokens.push(token);
            }
        }
    }
    info!("Subscribing to {} instruments: {:?}", instrument_tokens.len(), symbol_map.values().collect::<Vec<_>>());

    // ── 4. Initialise downstream sinks ──────────────────────────────────────
    #[cfg(feature = "kafka")]
    let kafka = KafkaProducer::new()
        .expect("Failed to create Kafka producer — is the broker reachable?");

    let mut questdb = QuestDbWriter::connect()
        .await
        .expect("Failed to connect to QuestDB ILP — is the container running?");

    // ── 5. Create tick channel ───────────────────────────────────────────────
    let (tx, mut rx) = mpsc::channel::<ParsedTick>(CHANNEL_CAPACITY);

    // ── 6. Spawn Kite WebSocket reader task ─────────────────────────────────
    let ws_handle = tokio::spawn(kite_ws::run(
        api_key,
        access_token,
        instrument_tokens,
        symbol_map,
        tx,
    ));

    // ── 7. Pipeline task: drain channel → Kafka + QuestDB ───────────────────
    let pipeline_handle = tokio::spawn(async move {
        while let Some(tick) = rx.recv().await {
            // Both sinks receive every tick; failures are logged but non-fatal
            #[cfg(feature = "kafka")]
            let kafka_fut = kafka.send_tick(&tick);
            let questdb_fut = questdb.write_tick(&tick);

            #[cfg(feature = "kafka")]
            tokio::join!(kafka_fut, questdb_fut);

            #[cfg(not(feature = "kafka"))]
            questdb_fut.await;
        }
        info!("Tick channel closed — pipeline task exiting");

        #[cfg(feature = "kafka")]
        kafka.flush();
    });

    // ── 8. Graceful shutdown on Ctrl-C / SIGTERM ────────────────────────────
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("SIGINT received — shutting down ingestion service...");
        }
        res = ws_handle => {
            error!("Kite WS task exited unexpectedly: {:?}", res);
        }
        res = pipeline_handle => {
            error!("Pipeline task exited unexpectedly: {:?}", res);
        }
    }

    info!("Ingestion service stopped.");
}
