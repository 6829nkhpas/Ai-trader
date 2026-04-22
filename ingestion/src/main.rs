// src/main.rs — AI-Trade Ingestion Service (Power Phase 1.2 — Subphases 16-18)
//
// Pipeline topology — DUAL SINK ARCHITECTURE:
//
//   [Kite WebSocket] ──binary frame──► [parser::parse_binary_frame]
//                                              │
//                                    Vec<proto::Tick> produced
//                                              │
//                            for each Tick ─  tokio::spawn (×2, concurrent):
//                                    ├─► [kafka_producer::publish_tick]  → topic: market.ticks
//                                    └─► [questdb_sink::insert_tick]     → live_ticks table (:8812)
//
// Additionally, the legacy high-throughput ILP writer is available:
//                                    └─► [questdb_writer::write_tick]    → ILP TCP :9009
//
// Environment variables required:
//   KAFKA_BROKER_URL       — Kafka bootstrap servers  (default: localhost:9092)
//   QUESTDB_POSTGRES_URL   — QuestDB PG wire URL      (default: postgresql://admin:quest@localhost:8812/qdb)
//   KITE_API_KEY           — Kite Connect API key
//   KITE_API_SECRET        — Kite Connect API secret  (used only when KITE_ACCESS_TOKEN absent)
//   KITE_REQUEST_TOKEN     — OAuth request token      (used only when KITE_ACCESS_TOKEN absent)
//   KITE_ACCESS_TOKEN      — Pre-fetched access token (if set, skips OAuth exchange)
//   KITE_INSTRUMENT_TOKENS — "token:SYMBOL,..." pairs (default: 738561:RELIANCE,260105:BANKNIFTY)
//   QUESTDB_ILP_ADDR       — QuestDB ILP endpoint     (default: 127.0.0.1:9009)
//   KAFKA_BROKERS          — alias for KAFKA_BROKER_URL used by KafkaProducer struct
//
// Feature flags:
//   kafka (default = on) — enables rdkafka / Kafka paths.
//   Disable with `cargo check --no-default-features` on Windows without CMake.

// ── Module declarations ───────────────────────────────────────────────────────
mod proto;          // Protobuf contract — must be first (others depend on crate::proto)
mod kite_client;    // Low-level WS transport: connect_ticker()
mod parser;         // Binary tick frame parser: parse_binary_tick() / parse_binary_frame()
mod kite_auth;      // OAuth access_token exchange
mod kite_ws;        // High-level WS client: subscription + auto-reconnect loop
mod questdb_writer; // ILP TCP writer → QuestDB :9009  (highest-throughput path)
mod questdb_sink;   // SQLx PG writer → QuestDB :8812  (SQL-accessible archive path)
mod types;          // ParsedTick — shared internal data contract

#[cfg(feature = "kafka")]
mod kafka_producer; // rdkafka FutureProducer → market.ticks  (requires CMake)

// ── Imports ───────────────────────────────────────────────────────────────────
use std::collections::HashMap;
use std::sync::Arc;

use futures_util::StreamExt;
use log::{error, info, warn};
use tokio::signal;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

#[cfg(feature = "kafka")]
use rdkafka::producer::FutureProducer;

use questdb_writer::QuestDbWriter;
use types::ParsedTick;

/// Channel buffer: holds up to 10,000 ticks for burst absorption without
/// blocking the WS reader task.
const CHANNEL_CAPACITY: usize = 10_000;

/// Default Kafka topic for live market tick data.
#[cfg(feature = "kafka")]
const KAFKA_TOPIC: &str = "market.ticks";

// ─────────────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    // ── 1. Load environment ──────────────────────────────────────────────────
    dotenvy::dotenv().ok();
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    info!("╔══════════════════════════════════════════════════════════╗");
    info!("║       AI-Trade Ingestion Service — Power Phase 1.2      ║");
    info!("╠══════════════════════════════════════════════════════════╣");
    info!("║  Kite WS  →  parser  →  Kafka (market.ticks)           ║");
    info!("║  Kite WS  →  parser  →  QuestDB PG  (:8812 / live_ticks) ║");
    info!("║  Kite WS  →  parser  →  QuestDB ILP (:9009)             ║");
    info!("╚══════════════════════════════════════════════════════════╝");

    // ── 2. Read required config from environment ─────────────────────────────
    #[cfg_attr(not(feature = "kafka"), allow(unused_variables))]
    let kafka_broker_url = std::env::var("KAFKA_BROKER_URL")
        .or_else(|_| std::env::var("KAFKA_BROKERS"))
        .unwrap_or_else(|_| "localhost:9092".to_string());

    let questdb_postgres_url = std::env::var("QUESTDB_POSTGRES_URL")
        .unwrap_or_else(|_| "postgresql://admin:quest@localhost:8812/qdb".to_string());

    let api_key = std::env::var("KITE_API_KEY")
        .expect("KITE_API_KEY must be set in .env");

    // Access token: either pre-set in .env, or generate via request_token exchange
    let access_token = match std::env::var("KITE_ACCESS_TOKEN") {
        Ok(token) if !token.is_empty() => {
            info!("Using KITE_ACCESS_TOKEN from environment");
            token
        }
        _ => {
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

    // ── 3. Build instrument token → symbol map ───────────────────────────────
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
    info!(
        "Subscribing to {} instruments: {:?}",
        instrument_tokens.len(),
        symbol_map.values().collect::<Vec<_>>()
    );

    // ── 4. Initialise Kafka producer (Subphase 16) ───────────────────────────
    #[cfg(feature = "kafka")]
    let kafka_producer: Arc<FutureProducer> = {
        info!("Initialising Kafka producer → {}", kafka_broker_url);
        Arc::new(kafka_producer::init_producer(&kafka_broker_url))
    };

    // ── 5. Initialise QuestDB PG pool + create table (Subphases 16-17) ───────
    let pg_pool = match questdb_sink::init_pool(&questdb_postgres_url).await {
        Ok(pool) => {
            questdb_sink::create_table_if_not_exists(&pool).await;
            Arc::new(pool)
        }
        Err(e) => {
            error!(
                "QuestDB PG connection failed ({}). \
                 live_ticks inserts will be skipped. Cause: {}",
                questdb_postgres_url, e
            );
            // Continue running — ILP path still works.
            // Use an explicit type annotation to satisfy the Arc<PgPool> type.
            // We wrap a dummy pool that will always fail its queries.
            // In practice you'd abort here; we log and continue for resilience.
            panic!("Cannot continue without QuestDB — fix QUESTDB_POSTGRES_URL and retry.");
        }
    };

    // ── 6. Initialise QuestDB ILP writer (Subphase 15, legacy high-throughput) ─
    let mut ilp_writer = QuestDbWriter::connect()
        .await
        .expect("Failed to connect to QuestDB ILP — is the container running?");

    // ── 7. Legacy mpsc-channel pipeline (kept for kite_ws.rs auto-reconnect) ─
    //    The raw direct-stream loop below is the new primary path (Subphase 18).
    //    The mpsc channel feeds the ILP writer from the high-level kite_ws task.
    let (tx, mut rx) = mpsc::channel::<ParsedTick>(CHANNEL_CAPACITY);

    // Spawn high-level Kite WS task (handles subscribe + auto-reconnect)
    let ws_handle = tokio::spawn(kite_ws::run(
        api_key.clone(),
        access_token.clone(),
        instrument_tokens.clone(),
        symbol_map.clone(),
        tx,
    ));

    // Drain mpsc channel → ILP writer (legacy path)
    let ilp_handle = tokio::spawn(async move {
        while let Some(tick) = rx.recv().await {
            ilp_writer.write_tick(&tick).await;
        }
        info!("ILP channel closed — legacy writer task exiting");
    });

    // ── 8. Direct-stream event loop (Subphase 18 — primary path) ────────────
    //    Opens a *second* WebSocket connection directly via kite_client,
    //    parses binary frames inline, and concurrently dispatches each Tick to
    //    both Kafka and QuestDB PG via tokio::spawn.
    //
    //    This is the canonical Power Phase 1.2 event loop specified in the
    //    subphase directive.

    let symbol_map_arc = Arc::new(symbol_map.clone());

    #[cfg(feature = "kafka")]
    let kafka_producer_clone = Arc::clone(&kafka_producer);
    let pg_pool_clone = Arc::clone(&pg_pool);

    let direct_handle = tokio::spawn(async move {
        info!("Direct-stream loop: connecting to Kite WebSocket...");

        let (mut ws_reader, _ws_writer) =
            match kite_client::connect_ticker(&api_key, &access_token).await {
                Ok(pair) => pair,
                Err(e) => {
                    error!("Direct-stream: Kite WS connect failed: {}", e);
                    return;
                }
            };

        info!("Direct-stream loop: WebSocket connected. Entering event loop.");

        // ── Main event loop ──────────────────────────────────────────────────
        while let Some(msg) = ws_reader.next().await {
            match msg {
                Ok(Message::Binary(payload)) => {
                    // Parse all tick packets from the binary frame
                    let ticks = parser::parse_binary_frame(&payload, &symbol_map_arc);

                    for tick in ticks {
                        // Clone Arc handles for the spawned task
                        #[cfg(feature = "kafka")]
                        let kp = Arc::clone(&kafka_producer_clone);
                        let pg = Arc::clone(&pg_pool_clone);
                        let tick_clone = tick.clone();

                        // Concurrently send to Kafka and QuestDB PG
                        tokio::spawn(async move {
                            // Kafka publish (feature-gated)
                            #[cfg(feature = "kafka")]
                            let kafka_fut = kafka_producer::publish_tick(&kp, KAFKA_TOPIC, &tick_clone);

                            // QuestDB PG insert
                            let questdb_fut = questdb_sink::insert_tick(&pg, &tick_clone);

                            #[cfg(feature = "kafka")]
                            tokio::join!(kafka_fut, questdb_fut);

                            #[cfg(not(feature = "kafka"))]
                            questdb_fut.await;
                        });
                    }
                }
                Ok(Message::Ping(data)) => {
                    log::trace!("Direct-stream: Ping received ({} bytes)", data.len());
                }
                Ok(Message::Close(frame)) => {
                    warn!("Direct-stream: WebSocket closed by server: {:?}", frame);
                    break;
                }
                Ok(_) => { /* Text / Pong / Frame — ignore */ }
                Err(e) => {
                    error!("Direct-stream: WebSocket error: {}", e);
                    break;
                }
            }
        }

        info!("Direct-stream loop exited.");
    });

    // ── 9. Graceful shutdown on Ctrl-C / SIGTERM ─────────────────────────────
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("SIGINT received — shutting down ingestion service...");
        }
        res = ws_handle => {
            error!("Kite WS task exited unexpectedly: {:?}", res);
        }
        res = ilp_handle => {
            error!("ILP writer task exited unexpectedly: {:?}", res);
        }
        res = direct_handle => {
            error!("Direct-stream task exited unexpectedly: {:?}", res);
        }
    }

    info!("Ingestion service stopped.");
}
