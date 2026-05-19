// src/main.rs â€” AI-Trade Ingestion Service (Power Phase 1.2 â€” Subphases 16-18)
//
// Pipeline topology â€” DUAL SINK ARCHITECTURE:
//
//   [Kite WebSocket] â”€â”€binary frameâ”€â”€â–º [parser::parse_binary_frame]
//                                              â”‚
//                                    Vec<proto::Tick> produced
//                                              â”‚
//                            for each Tick â”€  tokio::spawn (Ã—2, concurrent):
//                                    â”œâ”€â–º [kafka_producer::publish_tick]  â†’ topic: market.ticks
//                                    â””â”€â–º [questdb_sink::insert_tick]     â†’ live_ticks table (:8812)
//
// Additionally, the legacy high-throughput ILP writer is available:
//                                    â””â”€â–º [questdb_writer::write_tick]    â†’ ILP TCP :9009
//
// Dynamic subscription:
//   POST tcp://localhost:8085  "subscribe:TOKEN:SYMBOL\n"
//   â†’ Sends a new Kite WS subscribe + mode message for the given token.
//   â†’ Called by the Tauri frontend's subscribe_ticker command on symbol switch.
//
// Environment variables required:
//   KAFKA_BROKER_URL         â€” Kafka bootstrap servers  (default: localhost:9092)
//   QUESTDB_POSTGRES_URL     â€” QuestDB PG wire URL      (default: postgresql://admin:quest@localhost:8812/qdb)
//   KITE_API_KEY             â€” Kite Connect API key
//   KITE_API_SECRET          â€” Kite Connect API secret  (used only when KITE_ACCESS_TOKEN absent)
//   KITE_REQUEST_TOKEN       â€” OAuth request token      (used only when KITE_ACCESS_TOKEN absent)
//   KITE_ACCESS_TOKEN        â€” Pre-fetched access token (if set, skips OAuth exchange)
//   KITE_INSTRUMENT_TOKENS   â€” "token:SYMBOL,..." pairs (default: 738561:RELIANCE,260105:BANKNIFTY)
//   QUESTDB_ILP_ADDR         â€” QuestDB ILP endpoint     (default: 127.0.0.1:9009)
//   KAFKA_BROKERS            â€” alias for KAFKA_BROKER_URL used by KafkaProducer struct
//   INGESTION_CONTROL_PORT   â€” TCP control port for dynamic subscribe (default: 8085)
//
// Feature flags:
//   kafka (default = on) â€” enables rdkafka / Kafka paths.
//   Disable with `cargo check --no-default-features` on Windows without CMake.

// â”€â”€ Module declarations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
mod proto;          // Protobuf contract â€” must be first (others depend on crate::proto)
mod kite_client;    // Low-level WS transport: connect_ticker()
mod parser;         // Binary tick frame parser: parse_binary_tick() / parse_binary_frame()
mod kite_auth;      // OAuth access_token exchange
mod questdb_writer; // ILP TCP writer â†’ QuestDB :9009  (highest-throughput path)
mod questdb_sink;   // SQLx PG writer â†’ QuestDB :8812  (SQL-accessible archive path)
mod types;          // ParsedTick â€” shared internal data contract

#[cfg(feature = "kafka")]
mod kafka_producer; // rdkafka FutureProducer â†’ market.ticks  (requires CMake)

// â”€â”€ Imports â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
use std::collections::HashMap;
use std::sync::Arc;

use futures_util::{SinkExt, StreamExt};
use log::{error, info, warn};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::TcpListener;
use tokio::signal;
use tokio::sync::{mpsc, Mutex, RwLock};
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

// â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

/// Command from the control server to the WS writer task.
enum SubscribeCmd {
    /// Subscribe to a new instrument token with the given symbol name.
    Add { token: u32, symbol: String },
}

#[tokio::main]
async fn main() {
    // â”€â”€ 1. Load environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    dotenvy::dotenv().ok();
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    info!("â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—");
    info!("â•‘       AI-Trade Ingestion Service â€” Power Phase 1.2      â•‘");
    info!("â• â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•£");
    info!("â•‘  Kite WS  â†’  parser  â†’  Kafka (market.ticks)           â•‘");
    info!("â•‘  Kite WS  â†’  parser  â†’  QuestDB PG  (:8812 / live_ticks) â•‘");
    info!("â•‘  Kite WS  â†’  parser  â†’  QuestDB ILP (:9009)             â•‘");
    info!("â•‘  Control  â†’  TCP :8085  â†’  dynamic subscribe            â•‘");
    info!("â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•");

    // â”€â”€ 2. Read required config from environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    #[cfg_attr(not(feature = "kafka"), allow(unused_variables))]
    let kafka_broker_url = std::env::var("KAFKA_BROKER_URL")
        .or_else(|_| std::env::var("KAFKA_BROKERS"))
        .unwrap_or_else(|_| "localhost:19092".to_string());

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

    // â”€â”€ 3. Dynamic instrument map (starts EMPTY â€” no env scaffolding) â”€â”€â”€â”€â”€â”€â”€
    //
    // KITE_INSTRUMENT_TOKENS is NO LONGER read from the environment.
    // The service boots with zero subscriptions and waits for dynamic
    // `subscribe:TOKEN:SYMBOL` commands on the TCP control socket (:8085).
    // This is driven by the Tauri frontend's subscribe_ticker IPC command
    // when the user selects a symbol from the search bar / watchlist.
    let symbol_map: Arc<RwLock<HashMap<u32, String>>> = Arc::new(RwLock::new(HashMap::new()));

    info!(
        "Instrument map initialised EMPTY. \
         Subscriptions arrive dynamically via TCP control port."
    );

    // â”€â”€ 4. Initialise Kafka producer (Subphase 16) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    #[cfg(feature = "kafka")]
    let kafka_producer: Arc<FutureProducer> = {
        info!("Initialising Kafka producer â†’ {}", kafka_broker_url);
        Arc::new(kafka_producer::init_producer(&kafka_broker_url))
    };

    // â”€â”€ 5. Initialise QuestDB PG pool + create table (Subphases 16-17) â”€â”€â”€â”€â”€â”€â”€
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
            panic!("Cannot continue without QuestDB â€” fix QUESTDB_POSTGRES_URL and retry.");
        }
    };

    // â”€â”€ 6. Initialise QuestDB ILP writer (Subphase 15, legacy high-throughput) â”€
    let mut ilp_writer = QuestDbWriter::connect()
        .await
        .expect("Failed to connect to QuestDB ILP â€” is the container running?");

    // â”€â”€ 7. Legacy mpsc-channel pipeline (kept for ILP writer) â”€
    let (tx, mut rx) = mpsc::channel::<ParsedTick>(CHANNEL_CAPACITY);

    // Drain mpsc channel â†’ ILP writer (legacy path)
    let ilp_handle = tokio::spawn(async move {
        while let Some(tick) = rx.recv().await {
            ilp_writer.write_tick(&tick).await;
        }
        info!("ILP channel closed â€” legacy writer task exiting");
    });

    // â”€â”€ 8. Dynamic subscribe command channel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // Control server â†’ WS writer task.  Buffer of 64 is plenty (human-speed input).
    let (sub_tx, mut sub_rx) = mpsc::channel::<SubscribeCmd>(64);

    // â”€â”€ 9. Control server: TCP :8085 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // Accepts newline-delimited commands:
    //   subscribe:TOKEN:SYMBOL   â€” subscribe to a new Kite instrument token
    //
    // Called by the Tauri `subscribe_ticker` command after updating local state.
    let control_port = std::env::var("INGESTION_CONTROL_PORT")
        .unwrap_or_else(|_| "8085".to_string());
    let control_addr = format!("127.0.0.1:{}", control_port);
    let sub_tx_control = sub_tx.clone();
    let symbol_map_control = Arc::clone(&symbol_map);

    tokio::spawn(async move {
        let listener = match TcpListener::bind(&control_addr).await {
            Ok(l) => {
                info!("[Control] TCP control server listening on {}", control_addr);
                l
            }
            Err(e) => {
                error!("[Control] Failed to bind control port {}: {}", control_addr, e);
                return;
            }
        };

        loop {
            match listener.accept().await {
                Ok((stream, peer)) => {
                    let sub_tx = sub_tx_control.clone();
                    let symbol_map = Arc::clone(&symbol_map_control);
                    tokio::spawn(async move {
                        let reader = BufReader::new(stream);
                        let mut lines = reader.lines();
                        while let Ok(Some(line)) = lines.next_line().await {
                            let line = line.trim().to_string();
                            if line.starts_with("subscribe:") {
                                // Format: subscribe:TOKEN:SYMBOL
                                let parts: Vec<&str> = line.splitn(3, ':').collect();
                                if parts.len() == 3 {
                                    if let Ok(token) = parts[1].parse::<u32>() {
                                        let symbol = parts[2].to_uppercase();
                                        // Update symbol map
                                        {
                                            let mut map = symbol_map.write().await;
                                            if map.contains_key(&token) {
                                                info!("[Control] {} (token {}) already subscribed.", symbol, token);
                                                continue;
                                            }
                                            map.insert(token, symbol.clone());
                                        }
                                        info!("[Control] {} â€” new subscribe request from {}", symbol, peer);
                                        let _ = sub_tx.send(SubscribeCmd::Add { token, symbol }).await;
                                    } else {
                                        warn!("[Control] Invalid token in command: {}", line);
                                    }
                                } else {
                                    warn!("[Control] Malformed subscribe command: {}", line);
                                }
                            } else if !line.is_empty() {
                                warn!("[Control] Unknown command from {}: {}", peer, line);
                            }
                        }
                    });
                }
                Err(e) => {
                    error!("[Control] Accept error: {}", e);
                }
            }
        }
    });

    // â”€â”€ 10. Direct-stream event loop (Subphase 18 â€” primary path) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    //    Opens a WebSocket connection directly via kite_client, subscribes to
    //    all configured tokens, parses binary frames, and dispatches each Tick
    //    to both Kafka and QuestDB PG via tokio::spawn.
    //    Also listens on sub_rx for dynamic subscribe commands from the control server.

    let symbol_map_arc = Arc::clone(&symbol_map);

    #[cfg(feature = "kafka")]
    let kafka_producer_clone = Arc::clone(&kafka_producer);
    let pg_pool_clone = Arc::clone(&pg_pool);

    let direct_handle = tokio::spawn(async move {
        info!("Direct-stream loop: connecting to Kite WebSocket...");

        let (ws_reader, ws_writer) =
            match kite_client::connect_ticker(&api_key, &access_token).await {
                Ok(pair) => pair,
                Err(e) => {
                    error!("Direct-stream: Kite WS connect failed: {}", e);
                    return;
                }
            };

        // Wrap the writer in Arc<Mutex> so the sub_rx handler can send messages
        // while the reader loop is running concurrently.
        let ws_writer = Arc::new(Mutex::new(ws_writer));

        info!("Direct-stream loop: WebSocket connected. Sending subscription.");

        // Subscribe to any pre-existing tokens (will be empty on clean boot).
        // Dynamic subscriptions arrive via the control socket as the user
        // selects symbols in the UI.
        {
            let map = symbol_map_arc.read().await;
            if map.is_empty() {
                info!(
                    "Direct-stream: No initial subscriptions. \
                     Sitting idle — awaiting dynamic subscribe commands on TCP control port."
                );
            } else {
                let token_vals: Vec<serde_json::Value> = map
                    .keys()
                    .map(|&t| serde_json::Value::Number(t.into()))
                    .collect();

                let subscribe_msg = serde_json::json!({ "a": "subscribe", "v": token_vals }).to_string();
                let mode_msg = serde_json::json!({ "a": "mode", "v": ["full", token_vals] }).to_string();

                let mut writer = ws_writer.lock().await;
                if let Err(e) = writer.send(Message::Text(subscribe_msg)).await {
                    error!("Failed to send subscribe message: {}", e);
                }
                if let Err(e) = writer.send(Message::Text(mode_msg)).await {
                    error!("Failed to send mode message: {}", e);
                }
                info!("Subscribed to {} instruments in Full mode", map.len());
            }
        }

        // â”€â”€ Dynamic subscribe handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        // Runs as a separate task so the reader loop isn't blocked waiting for commands.
        let ws_writer_sub = Arc::clone(&ws_writer);
        tokio::spawn(async move {
            while let Some(cmd) = sub_rx.recv().await {
                match cmd {
                    SubscribeCmd::Add { token, symbol } => {
                        let token_val = serde_json::json!([token]);
                        let subscribe_msg = serde_json::json!({ "a": "subscribe", "v": token_val }).to_string();
                        let mode_msg = serde_json::json!({ "a": "mode", "v": ["full", token_val] }).to_string();

                        // ── DIAGNOSTIC TRACER — Kite WS dynamic subscribe payload ──
                        info!(
                            "[Control] Subscribing token={} symbol={}", token, symbol
                        );

                        let mut writer = ws_writer_sub.lock().await;
                        let ok = writer.send(Message::Text(subscribe_msg)).await.is_ok()
                            && writer.send(Message::Text(mode_msg)).await.is_ok();

                        if ok {
                            info!("[Control] âœ“ Dynamically subscribed: {} (token {})", symbol, token);
                        } else {
                            error!("[Control] âœ— Failed to subscribe {} â€” WS may be disconnected", symbol);
                        }
                    }
                }
            }
        });

        // â”€â”€ Main event loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        let mut ws_reader = ws_reader;
        while let Some(msg) = ws_reader.next().await {
            match msg {
                Ok(Message::Binary(payload)) => {
                    // Parse all tick packets from the binary frame.
                    // Hold the read lock for the duration of parsing only.
                    let ticks = {
                        let map = symbol_map_arc.read().await;
                        parser::parse_binary_frame(&payload, &*map)
                    };

                    for tick in ticks {
                        let parsed_tick = crate::types::ParsedTick {
                            instrument_token: tick.instrument_token,
                            symbol: tick.symbol.clone(),
                            last_price: tick.last_traded_price,
                            volume: tick.volume as u32,
                            best_bid: tick.best_bid,
                            best_ask: tick.best_ask,
                            open: tick.open,
                            high: tick.high,
                            low: tick.low,
                            close: tick.close,
                            timestamp_ms: tick.timestamp_ms,
                        };
                        let _ = tx.send(parsed_tick).await;
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
                Ok(_) => { /* Text / Pong / Frame â€” ignore */ }
                Err(e) => {
                    error!("Direct-stream: WebSocket error: {}", e);
                    break;
                }
            }
        }

        info!("Direct-stream loop exited.");
    });

    // â”€â”€ 11. Graceful shutdown on Ctrl-C / SIGTERM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("SIGINT received â€” shutting down ingestion service...");
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
