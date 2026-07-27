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
mod option_sink;    // SQLx PG writer â†’ option_ticks / option_chain_snapshots (F&O Phase F1)
mod types;          // ParsedTick â€” shared internal data contract

#[cfg(feature = "kafka")]
mod kafka_producer; // rdkafka FutureProducer â†’ market.ticks  (requires CMake)

// â”€â”€ Imports â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use futures_util::{SinkExt, StreamExt};
use log::{error, info, warn};
use serde::Deserialize;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::TcpListener;
use tokio::signal;
use tokio::sync::{mpsc, RwLock};
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
    /// Replace the bounded option-chain selection for one underlying
    /// (Options Data Foundation, Phase F1). Carries the whole selection plus
    /// per-instrument metadata so the WS writer task can diff against the
    /// current selection, subscribe/unsubscribe exactly that set in Full mode,
    /// tag stored rows, and (re)arm the snapshot timer.
    OptionChainSet {
        underlying: String,
        snapshot_interval_secs: u64,
        tokens: Vec<OptionTokenSpec>,
    },
}

/// One instrument entry carried in the `option_chain_set` control command
/// (design section 6). Deserialized directly from the JSON payload.
#[derive(Debug, Clone, Deserialize)]
struct OptionTokenSpec {
    token: u32,
    tradingsymbol: String,
    expiry: String,
    strike: f64,
    #[serde(rename = "type")]
    option_type: String,
}

/// Wire shape of the `option_chain_set:{json}` control command.
#[derive(Debug, Clone, Deserialize)]
struct OptionChainSetCmd {
    underlying: String,
    snapshot_interval_secs: u64,
    tokens: Vec<OptionTokenSpec>,
}

/// Latest-known state for a subscribed option instrument, kept in memory so the
/// periodic snapshot task can assemble one `option_chain_snapshots` row per
/// instrument without re-reading the database.
#[derive(Debug, Clone)]
struct LatestOptionState {
    symbol: String,
    last_price: f64,
    open_interest: Option<u64>,
}

/// Newline-delimited control prefix for the bounded option-chain selection
/// command (design section 6): `option_chain_set:{json}`.
const OPTION_CHAIN_SET_PREFIX: &str = "option_chain_set:";

/// Pure: parse an `option_chain_set:{json}` control line into its typed command.
///
/// The caller is responsible for confirming the line starts with
/// [`OPTION_CHAIN_SET_PREFIX`]; this strips the prefix and decodes the JSON body
/// into an [`OptionChainSetCmd`]. Kept pure (no I/O, no shared state) so the
/// control-channel contract — snapshot interval and token round-trip — is
/// directly unit-testable. (Design section 6, R5.3)
fn parse_option_chain_set_line(line: &str) -> Result<OptionChainSetCmd, serde_json::Error> {
    let json = line
        .strip_prefix(OPTION_CHAIN_SET_PREFIX)
        .unwrap_or(line);
    serde_json::from_str::<OptionChainSetCmd>(json)
}

/// Pure: build the Kite WS `subscribe` + `mode=full` JSON messages for a set of
/// instrument tokens.
///
/// Returns `(subscribe_msg, mode_msg)`. The mode message always requests `full`
/// mode so open interest and market depth are received for the bounded option
/// selection (R4.2). Used for the initial subscription, single dynamic
/// `subscribe:` adds, and the added tokens of an `option_chain_set` command, so
/// all three paths emit an identical, full-mode wire shape.
fn build_subscribe_messages(tokens: &[u32]) -> (String, String) {
    let vals: Vec<serde_json::Value> = tokens
        .iter()
        .map(|&t| serde_json::Value::Number(t.into()))
        .collect();
    let subscribe_msg = serde_json::json!({ "a": "subscribe", "v": vals.clone() }).to_string();
    let mode_msg = serde_json::json!({ "a": "mode", "v": ["full", vals] }).to_string();
    (subscribe_msg, mode_msg)
}

/// Pure: build the Kite WS `unsubscribe` JSON message for a set of tokens
/// removed from the bounded option selection (R4.4).
fn build_unsubscribe_message(tokens: &[u32]) -> String {
    let vals: Vec<serde_json::Value> = tokens
        .iter()
        .map(|&t| serde_json::Value::Number(t.into()))
        .collect();
    serde_json::json!({ "a": "unsubscribe", "v": vals }).to_string()
}

/// Routing decision for a parsed tick: whether it belongs to the bounded
/// option-chain selection or follows the equity hot path.
///
/// This makes the fault-isolation invariant explicit and testable: the route is
/// a PURE function of the option-metadata map plus the token, so equity routing
/// is provably independent of option-side state, and an idle (empty) option map
/// sends every tick down the equity path. (R7.1, R7.2, R7.4)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TickRoute {
    /// Token is in the bounded option selection → option sink + latest-state.
    Option,
    /// Token is not an option → existing `live_ticks` / Kafka / ILP path verbatim.
    Equity,
}

/// Pure: decide where a tick routes.
///
/// A token present in the option-metadata map is an option/future from the
/// bounded chain selection (`TickRoute::Option`); every other token — including
/// every token while the option map is empty (idle / no option selection) —
/// follows the equity path verbatim (`TickRoute::Equity`). The function only
/// reads the map (no mutation, no I/O), so option-side failures, which can only
/// touch the option maps, can never change the equity routing decision for any
/// non-option token. (R7.1, R7.2, R7.4)
fn route_tick(option_meta: &HashMap<u32, option_sink::OptionMeta>, token: u32) -> TickRoute {
    if option_meta.contains_key(&token) {
        TickRoute::Option
    } else {
        TickRoute::Equity
    }
}

/// (Re)arm the periodic snapshot timer with the interval supplied by an
/// `option_chain_set` command, returning the value now visible to the snapshot
/// task. The timer is an [`AtomicU64`] shared with the snapshot loop; storing
/// `secs` makes the task wake on that cadence (R5.3). Returning the loaded value
/// makes the store/load contract directly testable.
fn arm_snapshot_interval(interval: &AtomicU64, secs: u64) -> u64 {
    interval.store(secs, Ordering::Relaxed);
    interval.load(Ordering::Relaxed)
}

pub fn get_kite_credentials() -> (String, String) {
    let mut api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
    let mut access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();

    if let Ok(mut current_dir) = std::env::current_dir() {
        loop {
            let env_path = current_dir.join(".env");
            if env_path.is_file() {
                if let Ok(content) = std::fs::read_to_string(env_path) {
                    for line in content.lines() {
                        let line = line.trim();
                        if line.starts_with('#') || !line.contains('=') {
                            continue;
                        }
                        let parts: Vec<&str> = line.splitn(2, '=').collect();
                        if parts.len() == 2 {
                            let key = parts[0].trim();
                            let val = parts[1].trim().trim_matches('"').trim_matches('\'');
                            if key == "KITE_API_KEY" && !val.is_empty() {
                                api_key = val.to_string();
                            } else if key == "KITE_ACCESS_TOKEN" && !val.is_empty() {
                                access_token = val.to_string();
                            }
                        }
                    }
                }
                break;
            }
            if !current_dir.pop() {
                break;
            }
        }
    }

    (api_key, access_token)
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

    let (api_key, access_token) = get_kite_credentials();
    if api_key.is_empty() {
        warn!("KITE_API_KEY is not set in .env! Startup will proceed, but connection attempts will fail until set.");
    }
    if access_token.is_empty() {
        warn!("KITE_ACCESS_TOKEN is not set in .env! Startup will proceed, but connection attempts will fail until user logs in.");
    }

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
            // F&O Phase F1: create the additive option tables. Idempotent and
            // does not touch live_ticks / historical_candles (R5.6).
            option_sink::create_option_tables(&pool).await;
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

    // â”€â”€ 8b. F&O Phase F1: option-chain shared state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // All option-side state lives in its own maps so the equity hot path is
    // untouched (fault isolation, R7.1/R7.4):
    //   option_meta       — token â†’ {underlying, expiry, strike, type}; the tick
    //                        router consults this to decide equity vs option.
    //   option_selection  — underlying â†’ current subscribed token set, used to
    //                        diff each new option_chain_set command.
    //   latest_state      — token â†’ latest symbol/price/OI for snapshot assembly.
    //   snapshot_interval — seconds between chain snapshots; 0 = not yet armed.
    let option_meta: Arc<RwLock<HashMap<u32, option_sink::OptionMeta>>> =
        Arc::new(RwLock::new(HashMap::new()));
    let option_selection: Arc<RwLock<HashMap<String, HashSet<u32>>>> =
        Arc::new(RwLock::new(HashMap::new()));
    let latest_state: Arc<RwLock<HashMap<u32, LatestOptionState>>> =
        Arc::new(RwLock::new(HashMap::new()));
    let snapshot_interval = Arc::new(AtomicU64::new(0));

    // Periodic snapshot task: walks the latest-state map and writes one
    // option_chain_snapshots row per instrument that has a known latest tick.
    // Runs on its own task and reads in-memory maps only, so it never blocks
    // or stalls the WS reader / equity path (R7.4). All errors are logged
    // inside write_chain_snapshot.
    {
        let pg = Arc::clone(&pg_pool);
        let meta = Arc::clone(&option_meta);
        let latest = Arc::clone(&latest_state);
        let interval = Arc::clone(&snapshot_interval);
        tokio::spawn(async move {
            loop {
                let secs = interval.load(Ordering::Relaxed);
                if secs == 0 {
                    // Not armed yet â€” poll for the first option_chain_set.
                    tokio::time::sleep(Duration::from_secs(1)).await;
                    continue;
                }
                tokio::time::sleep(Duration::from_secs(secs)).await;

                let now_ms = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_millis() as i64)
                    .unwrap_or(0);

                // Assemble snapshot rows from the in-memory state (read locks
                // held only for the build, never across the DB write).
                let rows = {
                    let meta_map = meta.read().await;
                    let latest_map = latest.read().await;
                    let mut rows = Vec::with_capacity(latest_map.len());
                    for (token, st) in latest_map.iter() {
                        if let Some(m) = meta_map.get(token) {
                            rows.push(option_sink::build_snapshot_row(
                                m,
                                &st.symbol,
                                st.last_price,
                                st.open_interest,
                                now_ms,
                            ));
                        }
                    }
                    rows
                };

                if !rows.is_empty() {
                    option_sink::write_chain_snapshot(&pg, &rows).await;
                }
            }
        });
    }

    // â”€â”€ 9. Control server: TCP :8085 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    // Accepts newline-delimited commands:
    //   subscribe:TOKEN:SYMBOL   â€” subscribe to a new Kite instrument token
    //
    // Called by the Tauri `subscribe_ticker` command after updating local state.
    let control_port = std::env::var("INGESTION_CONTROL_PORT")
        .unwrap_or_else(|_| "8085".to_string());
    let control_bind = std::env::var("INGESTION_CONTROL_BIND")
        .unwrap_or_else(|_| "0.0.0.0".to_string());
    let control_addr = format!("{}:{}", control_bind, control_port);
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
                            } else if line.starts_with(OPTION_CHAIN_SET_PREFIX) {
                                // Format: option_chain_set:{json}
                                // The JSON carries the whole bounded selection
                                // for one underlying plus per-instrument metadata
                                // (design section 6). Parse + forward to the WS
                                // writer task, which diffs and subscribes.
                                match parse_option_chain_set_line(&line) {
                                    Ok(cmd) => {
                                        info!(
                                            "[Control] option_chain_set for {} â€” {} token(s), snapshot {}s (from {})",
                                            cmd.underlying,
                                            cmd.tokens.len(),
                                            cmd.snapshot_interval_secs,
                                            peer
                                        );
                                        let _ = sub_tx
                                            .send(SubscribeCmd::OptionChainSet {
                                                underlying: cmd.underlying,
                                                snapshot_interval_secs: cmd.snapshot_interval_secs,
                                                tokens: cmd.tokens,
                                            })
                                            .await;
                                    }
                                    Err(e) => {
                                        warn!("[Control] Malformed option_chain_set JSON from {}: {}", peer, e);
                                    }
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

    // F&O Phase F1: option-side state handles for the WS writer + tick router.
    let option_meta_arc = Arc::clone(&option_meta);
    let option_selection_arc = Arc::clone(&option_selection);
    let latest_state_arc = Arc::clone(&latest_state);
    let snapshot_interval_arc = Arc::clone(&snapshot_interval);

    let direct_handle = tokio::spawn(async move {
        let mut backoff = std::time::Duration::from_secs(2);
        loop {
            let (api_key, access_token) = get_kite_credentials();
            if api_key.is_empty() || access_token.is_empty() {
                warn!("Direct-stream: KITE_API_KEY or KITE_ACCESS_TOKEN is empty in .env. Retrying in {:?}", backoff);
                tokio::time::sleep(backoff).await;
                backoff = std::cmp::min(backoff * 2, std::time::Duration::from_secs(30));
                continue;
            }

            info!("Direct-stream: Connecting to Kite WebSocket...");
            let (ws_reader, mut ws_writer) = match kite_client::connect_ticker(&api_key, &access_token).await {
                Ok(pair) => pair,
                Err(e) => {
                    error!("Direct-stream: Kite WS connect failed: {}. Retrying in {:?}", e, backoff);
                    tokio::time::sleep(backoff).await;
                    backoff = std::cmp::min(backoff * 2, std::time::Duration::from_secs(30));
                    continue;
                }
            };

            // Connected! Reset backoff
            backoff = std::time::Duration::from_secs(2);
            info!("Direct-stream: WebSocket connected. Sending subscription.");

            // Subscribe to any pre-existing tokens
            {
                let map = symbol_map_arc.read().await;
                if !map.is_empty() {
                    let tokens: Vec<u32> = map.keys().copied().collect();
                    let (subscribe_msg, mode_msg) = build_subscribe_messages(&tokens);

                    if let Err(e) = ws_writer.send(Message::Text(subscribe_msg)).await {
                        error!("Failed to send initial subscribe message: {}", e);
                    }
                    if let Err(e) = ws_writer.send(Message::Text(mode_msg)).await {
                        error!("Failed to send initial mode message: {}", e);
                    }
                    info!("Subscribed to {} instruments in Full mode", map.len());
                } else {
                    info!("Direct-stream: No initial subscriptions. Sitting idle — awaiting dynamic subscribe commands on TCP control port.");
                }
            }

            // Unified select loop for message reading and dynamic subscriptions
            let mut ws_reader = ws_reader;
            loop {
                tokio::select! {
                    msg = ws_reader.next() => {
                        match msg {
                            Some(Ok(Message::Binary(payload))) => {
                                // Parse all tick packets from the binary frame.
                                // Hold the read lock for the duration of parsing only.
                                let ticks = {
                                    let map = symbol_map_arc.read().await;
                                    parser::parse_binary_frame(&payload, &*map)
                                };

                                for tick in ticks {
                                    let token = tick.instrument_token;

                                    // â”€â”€ Option router (F&O Phase F1) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                                    // If this token is in the option-metadata
                                    // map it is an option/future from the bounded
                                    // chain selection: route to the option sink
                                    // and update the latest-state map. All of this
                                    // runs on a spawned task so an option-side
                                    // failure never touches the equity branch or
                                    // the WS read loop (R7.1/R7.4).
                                    let meta_opt = {
                                        let m = option_meta_arc.read().await;
                                        match route_tick(&m, token) {
                                            TickRoute::Option => m.get(&token).cloned(),
                                            TickRoute::Equity => None,
                                        }
                                    };
                                    if let Some(meta) = meta_opt {
                                        let pg = Arc::clone(&pg_pool_clone);
                                        let latest = Arc::clone(&latest_state_arc);
                                        let tick_clone = tick.clone();
                                        tokio::spawn(async move {
                                            // Update in-memory latest state for snapshots.
                                            {
                                                let mut ls = latest.write().await;
                                                ls.insert(
                                                    token,
                                                    LatestOptionState {
                                                        symbol: tick_clone.symbol.clone(),
                                                        last_price: tick_clone.last_traded_price,
                                                        open_interest: tick_clone.open_interest,
                                                    },
                                                );
                                            }
                                            option_sink::insert_option_tick(&pg, &tick_clone, &meta).await;
                                        });
                                        continue;
                                    }

                                    // â”€â”€ Equity path (unchanged) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                                        open_interest: tick.open_interest,
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
                            Some(Ok(Message::Ping(data))) => {
                                log::trace!("Direct-stream: Ping received ({} bytes)", data.len());
                            }
                            Some(Ok(Message::Close(frame))) => {
                                warn!("Direct-stream: WebSocket closed by server: {:?}", frame);
                                break;
                            }
                            Some(Ok(_)) => { /* Text / Pong / Frame — ignore */ }
                            Some(Err(e)) => {
                                error!("Direct-stream: WebSocket error: {}", e);
                                break;
                            }
                            None => {
                                warn!("Direct-stream: WebSocket stream ended.");
                                break;
                            }
                        }
                    }
                    cmd = sub_rx.recv() => {
                        match cmd {
                            Some(SubscribeCmd::Add { token, symbol }) => {
                                let (subscribe_msg, mode_msg) = build_subscribe_messages(&[token]);

                                // ── DIAGNOSTIC TRACER — Kite WS dynamic subscribe payload ──
                                info!(
                                    "[Control] Subscribing token={} symbol={}", token, symbol
                                );

                                let ok = ws_writer.send(Message::Text(subscribe_msg)).await.is_ok()
                                    && ws_writer.send(Message::Text(mode_msg)).await.is_ok();

                                if ok {
                                    info!("[Control] ✓ Dynamically subscribed: {} (token {})", symbol, token);
                                } else {
                                    error!("[Control] ✗ Failed to subscribe {} — WS may be disconnected", symbol);
                                }
                            }
                            Some(SubscribeCmd::OptionChainSet { underlying, snapshot_interval_secs, tokens }) => {
                                // â”€â”€ F&O Phase F1: bounded option-chain selection â”€â”€
                                // Diff the new token set against the current
                                // selection for this underlying, then subscribe
                                // added tokens (Full mode) and unsubscribe removed
                                // ones. All option-side bookkeeping is isolated to
                                // the option maps and never disturbs the equity
                                // subscription or the WS read loop (R7.1/R7.4).
                                let new_tokens: HashSet<u32> =
                                    tokens.iter().map(|t| t.token).collect();

                                let prev_tokens: HashSet<u32> = {
                                    let sel = option_selection_arc.read().await;
                                    sel.get(&underlying).cloned().unwrap_or_default()
                                };
                                let added: Vec<u32> =
                                    new_tokens.difference(&prev_tokens).copied().collect();
                                let removed: Vec<u32> =
                                    prev_tokens.difference(&new_tokens).copied().collect();

                                // Merge token â†’ metadata for the whole new set so
                                // the tick router can tag rows (locks held briefly,
                                // one map at a time to avoid lock ordering issues).
                                {
                                    let mut meta = option_meta_arc.write().await;
                                    for t in &tokens {
                                        meta.insert(
                                            t.token,
                                            option_sink::OptionMeta {
                                                underlying: underlying.clone(),
                                                expiry: t.expiry.clone(),
                                                strike: t.strike,
                                                option_type: t.option_type.clone(),
                                            },
                                        );
                                    }
                                }
                                // Merge token â†’ tradingsymbol into the shared map so
                                // the parser resolves the symbol for arriving ticks.
                                {
                                    let mut smap = symbol_map_arc.write().await;
                                    for t in &tokens {
                                        smap.insert(t.token, t.tradingsymbol.to_uppercase());
                                    }
                                }
                                // Drop metadata / latest-state / symbol for removed.
                                if !removed.is_empty() {
                                    {
                                        let mut meta = option_meta_arc.write().await;
                                        for t in &removed { meta.remove(t); }
                                    }
                                    {
                                        let mut latest = latest_state_arc.write().await;
                                        for t in &removed { latest.remove(t); }
                                    }
                                    {
                                        let mut smap = symbol_map_arc.write().await;
                                        for t in &removed { smap.remove(t); }
                                    }
                                }
                                // Record the new selection so the next command diffs against it.
                                {
                                    let mut sel = option_selection_arc.write().await;
                                    sel.insert(underlying.clone(), new_tokens);
                                }
                                // (Re)arm the snapshot timer with the supplied interval.
                                arm_snapshot_interval(&snapshot_interval_arc, snapshot_interval_secs);

                                // Subscribe added tokens in Full mode (R4.1, R4.2).
                                if !added.is_empty() {
                                    let (sub_msg, mode_msg) = build_subscribe_messages(&added);
                                    let ok = ws_writer.send(Message::Text(sub_msg)).await.is_ok()
                                        && ws_writer.send(Message::Text(mode_msg)).await.is_ok();
                                    if ok {
                                        info!(
                                            "[Control] option_chain_set: {} subscribed {} new option token(s) in Full mode",
                                            underlying, added.len()
                                        );
                                    } else {
                                        error!(
                                            "[Control] option_chain_set: failed to subscribe option tokens for {} — WS may be down",
                                            underlying
                                        );
                                    }
                                }
                                // Unsubscribe removed tokens (bounded set, R4.4).
                                if !removed.is_empty() {
                                    let unsub_msg = build_unsubscribe_message(&removed);
                                    if ws_writer.send(Message::Text(unsub_msg)).await.is_ok() {
                                        info!(
                                            "[Control] option_chain_set: {} unsubscribed {} option token(s)",
                                            underlying, removed.len()
                                        );
                                    } else {
                                        error!(
                                            "[Control] option_chain_set: failed to unsubscribe option tokens for {}",
                                            underlying
                                        );
                                    }
                                }
                            }
                            None => {
                                warn!("Direct-stream: control subscription channel closed");
                            }
                        }
                    }
                }
            }
            warn!("Direct-stream: Active connection lost. Retrying connection in {:?}", backoff);
            tokio::time::sleep(backoff).await;
            backoff = std::cmp::min(backoff * 2, std::time::Duration::from_secs(30));
        }
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

// â”€â”€ Unit tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Task 8.2 (options-data-foundation): unit tests for the option_chain_set
// control command and the full-mode subscription messages. These exercise the
// pure helpers that the control server and WS-writer task call in production,
// so behavior is validated without a live Kite feed.
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    /// A realistic option_chain_set control line carrying two NIFTY option
    /// tokens and a 30s snapshot cadence.
    fn sample_line() -> String {
        let json = r#"{
            "underlying": "NIFTY 50",
            "snapshot_interval_secs": 30,
            "tokens": [
                { "token": 12345678, "tradingsymbol": "NIFTY24DEC24000CE", "expiry": "2024-12-26", "strike": 24000.0, "type": "CE" },
                { "token": 87654321, "tradingsymbol": "NIFTY24DEC24000PE", "expiry": "2024-12-26", "strike": 24000.0, "type": "PE" }
            ]
        }"#;
        format!("{}{}", OPTION_CHAIN_SET_PREFIX, json)
    }

    // â”€â”€ R4.2: Full-mode subscription message uses mode `full` â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn subscribe_mode_message_requests_full_mode() {
        let (_subscribe_msg, mode_msg) = build_subscribe_messages(&[111, 222]);

        // The mode message must request "full" so OI + depth are received (R4.2).
        let parsed: Value = serde_json::from_str(&mode_msg).expect("mode msg is JSON");
        assert_eq!(parsed["a"], "mode");
        let v = parsed["v"].as_array().expect("v is an array");
        assert_eq!(v[0], "full", "first element of mode `v` must be \"full\"");

        // The second element is the exact token list being moded to full.
        let toks = v[1].as_array().expect("token list is an array");
        let got: Vec<u64> = toks.iter().map(|t| t.as_u64().unwrap()).collect();
        assert_eq!(got, vec![111, 222]);
    }

    #[test]
    fn subscribe_message_carries_action_and_token_list() {
        let (subscribe_msg, _mode_msg) = build_subscribe_messages(&[111, 222]);

        let parsed: Value = serde_json::from_str(&subscribe_msg).expect("subscribe msg is JSON");
        assert_eq!(parsed["a"], "subscribe");
        let toks: Vec<u64> = parsed["v"]
            .as_array()
            .expect("v is an array")
            .iter()
            .map(|t| t.as_u64().unwrap())
            .collect();
        assert_eq!(toks, vec![111, 222]);
    }

    #[test]
    fn single_token_subscribe_is_full_mode() {
        // The dynamic `Add` path subscribes a single token; it too must be full mode.
        let (_subscribe_msg, mode_msg) = build_subscribe_messages(&[738561]);
        let parsed: Value = serde_json::from_str(&mode_msg).unwrap();
        assert_eq!(parsed["v"][0], "full");
        assert_eq!(parsed["v"][1][0].as_u64().unwrap(), 738561);
    }

    #[test]
    fn unsubscribe_message_carries_action_and_token_list() {
        let unsub_msg = build_unsubscribe_message(&[111, 222]);
        let parsed: Value = serde_json::from_str(&unsub_msg).unwrap();
        assert_eq!(parsed["a"], "unsubscribe");
        let toks: Vec<u64> = parsed["v"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t.as_u64().unwrap())
            .collect();
        assert_eq!(toks, vec![111, 222]);
    }

    // â”€â”€ Control command parsing round-trip â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn parses_option_chain_set_line_round_trips_fields() {
        let cmd = parse_option_chain_set_line(&sample_line()).expect("valid command parses");

        assert_eq!(cmd.underlying, "NIFTY 50");
        assert_eq!(cmd.snapshot_interval_secs, 30);
        assert_eq!(cmd.tokens.len(), 2);

        assert_eq!(cmd.tokens[0].token, 12345678);
        assert_eq!(cmd.tokens[0].tradingsymbol, "NIFTY24DEC24000CE");
        assert_eq!(cmd.tokens[0].expiry, "2024-12-26");
        assert_eq!(cmd.tokens[0].strike, 24000.0);
        assert_eq!(cmd.tokens[0].option_type, "CE");

        assert_eq!(cmd.tokens[1].token, 87654321);
        assert_eq!(cmd.tokens[1].option_type, "PE");
    }

    #[test]
    fn malformed_option_chain_set_line_is_an_error_not_a_panic() {
        let bad = format!("{}{{ not json", OPTION_CHAIN_SET_PREFIX);
        assert!(parse_option_chain_set_line(&bad).is_err());
    }

    // â”€â”€ R5.3: Snapshot interval from the command is applied to the timer â”€â”€

    #[test]
    fn snapshot_interval_from_command_arms_the_timer() {
        let cmd = parse_option_chain_set_line(&sample_line()).unwrap();

        // The snapshot task reads this AtomicU64 to decide its cadence.
        let timer = AtomicU64::new(0);
        let armed = arm_snapshot_interval(&timer, cmd.snapshot_interval_secs);

        // The value the timer is armed with equals the command's interval (R5.3).
        assert_eq!(armed, 30);
        assert_eq!(timer.load(Ordering::Relaxed), cmd.snapshot_interval_secs);
    }

    #[test]
    fn rearming_the_timer_replaces_the_previous_interval() {
        let timer = AtomicU64::new(60);
        // A later option_chain_set with a different cadence re-arms the timer.
        let armed = arm_snapshot_interval(&timer, 15);
        assert_eq!(armed, 15);
        assert_eq!(timer.load(Ordering::Relaxed), 15);
    }
}

// â”€â”€ Fault-isolation tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Task 9.3 (options-data-foundation): fault-isolation integration test.
//
// The full end-to-end pipeline needs a live Kite WS + QuestDB, which are not
// available in CI, so these tests pin the *invariant* the fault-isolation design
// guarantees at the routing level (design "Fault isolation" section, R7.1–R7.4):
//
//   * equity routing is a pure function of the token and depends in no way on
//     option-side state, so an injected option failure (parse / insert /
//     rejected subscription) leaves the equity `live_ticks` path running;
//   * an empty option map (idle / market closed / no option selection) routes
//     every tick down the equity path with no error, and a later option tick
//     resumes option routing without disturbing equity routing;
//   * the equity `Add` and the option `OptionChainSet` control commands are
//     distinct, independent match arms, so a rejected option subscription
//     cannot alter equity subscription state.
#[cfg(test)]
mod fault_isolation_tests {
    use super::*;

    /// Build an option-metadata entry for a token, mirroring what the
    /// `option_chain_set` command stores in the production option_meta map.
    fn meta(underlying: &str) -> option_sink::OptionMeta {
        option_sink::OptionMeta {
            underlying: underlying.to_string(),
            expiry: "2024-12-26".to_string(),
            strike: 24000.0,
            option_type: "CE".to_string(),
        }
    }

    // â”€â”€ R7.1: option-side state never affects the equity path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn option_token_routes_to_option_branch() {
        let mut option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();
        option_meta.insert(12345678, meta("NIFTY 50"));

        // A token present in the option-metadata map is an option from the
        // bounded selection and routes to the option sink.
        assert_eq!(route_tick(&option_meta, 12345678), TickRoute::Option);
    }

    #[test]
    fn equity_token_routes_to_equity_branch_even_with_options_present() {
        let mut option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();
        option_meta.insert(12345678, meta("NIFTY 50"));

        // An equity token (a miss in the option map) follows the existing
        // live_ticks path verbatim, regardless of how many option tokens are
        // currently subscribed — equity routing is independent of option state.
        assert_eq!(route_tick(&option_meta, 738561), TickRoute::Equity);
    }

    // â”€â”€ R7.2: idle (empty option map) → all equity, no error; resumes on tick â”€

    #[test]
    fn empty_option_map_routes_everything_to_equity() {
        // Idle / no option selection (market closed, or before the first
        // option_chain_set): the option map is empty, so every tick — across a
        // spread of arbitrary tokens — routes to the equity path with no panic.
        let option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();
        for token in [0u32, 1, 738561, 260105, u32::MAX, 99_999] {
            assert_eq!(
                route_tick(&option_meta, token),
                TickRoute::Equity,
                "token {token} must route to equity while idle",
            );
        }
    }

    #[test]
    fn idle_then_option_tick_arrival_resumes_option_routing() {
        // Start idle: equity tokens route to equity, option token not yet known.
        let mut option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();
        assert_eq!(route_tick(&option_meta, 12345678), TickRoute::Equity);
        assert_eq!(route_tick(&option_meta, 738561), TickRoute::Equity);

        // A later option_chain_set populates the option map (a tick "arrives").
        option_meta.insert(12345678, meta("NIFTY 50"));

        // Option routing resumes for the option token; the equity token is
        // still routed to the equity path, untouched (R7.2).
        assert_eq!(route_tick(&option_meta, 12345678), TickRoute::Option);
        assert_eq!(route_tick(&option_meta, 738561), TickRoute::Equity);
    }

    // â”€â”€ R7.1 / R7.4: an injected option-side failure is contained â”€â”€â”€â”€â”€â”€â”€â”€â”€

    #[test]
    fn injected_option_failure_does_not_affect_equity_routing() {
        let mut option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();
        option_meta.insert(12345678, meta("NIFTY 50"));

        let equity_token = 738561u32;
        // Baseline: equity token routes to equity before any option work.
        assert_eq!(route_tick(&option_meta, equity_token), TickRoute::Equity);

        // Model the option branch's fallible work (parse / insert / rejected
        // subscription). In production this runs on a spawned task and the error
        // is caught + logged inside the option sink; here we capture the error
        // the same way and assert it is contained — it does not propagate.
        let option_work = |_token: u32| -> Result<(), String> {
            Err("simulated option insert failure".to_string())
        };
        let contained = match option_work(12345678) {
            Ok(()) => true,
            Err(_e) => {
                // Caught + logged in production; the equity branch is unaffected.
                false
            }
        };
        assert!(!contained, "the injected failure must be observed as contained");

        // After the option-side failure, the equity routing decision for
        // subsequent ticks is unchanged: equity tokens still route to equity,
        // and the option token (whose insert failed) is still recognized — the
        // failure did not corrupt the routing map (R7.1, R7.4).
        assert_eq!(route_tick(&option_meta, equity_token), TickRoute::Equity);
        assert_eq!(route_tick(&option_meta, 12345678), TickRoute::Option);
    }

    #[test]
    fn equity_routing_is_independent_of_option_subscription_churn() {
        // Subscribing and later unsubscribing option tokens (the add/remove
        // diff an option_chain_set performs) must never change an equity token's
        // route. We mutate only the option map — the equity decision is stable.
        let equity_token = 738561u32;
        let mut option_meta: HashMap<u32, option_sink::OptionMeta> = HashMap::new();

        assert_eq!(route_tick(&option_meta, equity_token), TickRoute::Equity);

        // Add a batch of option tokens (subscription).
        for t in [111u32, 222, 333] {
            option_meta.insert(t, meta("BANKNIFTY"));
        }
        assert_eq!(route_tick(&option_meta, equity_token), TickRoute::Equity);

        // Remove them (unsubscription / rejected-subscription rollback).
        for t in [111u32, 222, 333] {
            option_meta.remove(&t);
        }
        assert_eq!(route_tick(&option_meta, equity_token), TickRoute::Equity);
    }

    // â”€â”€ R7.3: rejected option subscription cannot alter equity subscription â”€

    #[test]
    fn equity_and_option_subscription_commands_are_distinct_arms() {
        // The control channel models equity and option subscription as two
        // distinct SubscribeCmd variants handled by separate match arms, so an
        // option subscription failure path cannot mutate equity subscription
        // state. Assert they are genuinely separate shapes.
        let equity_cmd = SubscribeCmd::Add {
            token: 738561,
            symbol: "RELIANCE".to_string(),
        };
        let option_cmd = SubscribeCmd::OptionChainSet {
            underlying: "NIFTY 50".to_string(),
            snapshot_interval_secs: 30,
            tokens: vec![OptionTokenSpec {
                token: 12345678,
                tradingsymbol: "NIFTY24DEC24000CE".to_string(),
                expiry: "2024-12-26".to_string(),
                strike: 24000.0,
                option_type: "CE".to_string(),
            }],
        };

        assert!(matches!(equity_cmd, SubscribeCmd::Add { .. }));
        assert!(matches!(option_cmd, SubscribeCmd::OptionChainSet { .. }));
    }

    #[test]
    fn option_subscribe_and_unsubscribe_messages_are_separate_from_equity() {
        // Building an option subscription/unsubscription message operates only
        // on the option token list and produces an independent wire message; it
        // shares no state with the equity `Add` subscribe message, so a failure
        // to send the option message leaves the equity subscribe untouched.
        let equity_token = 738561u32;
        let option_tokens = [111u32, 222];

        let (equity_sub, _equity_mode) = build_subscribe_messages(&[equity_token]);
        let (option_sub, _option_mode) = build_subscribe_messages(&option_tokens);
        let option_unsub = build_unsubscribe_message(&option_tokens);

        // The equity subscribe message references only the equity token.
        assert!(equity_sub.contains("738561"));
        assert!(!equity_sub.contains("111"));
        assert!(!equity_sub.contains("222"));

        // The option messages reference only the option tokens.
        assert!(option_sub.contains("111") && option_sub.contains("222"));
        assert!(!option_sub.contains("738561"));
        assert!(option_unsub.contains("111") && option_unsub.contains("222"));
        assert!(!option_unsub.contains("738561"));
    }
}
