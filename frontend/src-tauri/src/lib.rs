use tauri::Emitter;
use tauri::Manager;
use futures_util::StreamExt;
use tokio_tungstenite::connect_async;
use log::{info, warn, error};

pub mod commands;
pub mod db;
pub mod quant;
pub mod services;

/// Check if the application is running in E2E test mode.
/// When ALPHA_TEST_MODE is set, live APIs (Zerodha/DeepSeek) are bypassed
/// and replaced with deterministic mock data.
pub fn is_test_mode() -> bool {
    std::env::var("ALPHA_TEST_MODE").is_ok()
}

/// Mock OHLC candle tick emitted every 100ms in test mode.
/// Represents a stable RELIANCE candle for deterministic UI testing.
fn mock_ohlc_tick() -> serde_json::Value {
    serde_json::json!({
        "symbol": "RELIANCE",
        "open": 2450.0,
        "high": 2475.0,
        "low": 2440.0,
        "close": 2468.0,
        "volume": 125000,
        "timestamp": chrono::Utc::now().timestamp_millis()
    })
}

/// Static mocked AiExecutionPlan returned when ALPHA_TEST_MODE is active.
/// Prevents any network call to DeepSeek during E2E tests.
pub fn mock_ai_execution_plan() -> quant::AiExecutionPlan {
    quant::AiExecutionPlan {
        conviction_score: 78,
        setup_validation: "Golden Cross confirmed with rising OBV and bullish engulfing pattern. \
            Volume surge validates breakout above VWAP. RSI at 62 provides room for upside \
            before overbought territory. News sentiment is neutral-positive.".to_string(),
        execution_plan: "ENTRY: 2470 (current breakout level above VWAP) | \
            STOP-LOSS: 2435 (below ORB low and recent swing low) | \
            TARGET 1: 2510 (1:1.14 R:R at prior resistance) | \
            TARGET 2: 2550 (measured move from engulfing pattern) | \
            POSITION SIZE: 2% of capital | \
            INVALIDATION: Close below SMA50 on daily timeframe.".to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  // ── Load .env (robust, cwd-independent) ─────────────────────────────────
  //
  // Tauri's dev command may launch the binary with various working
  // directories (src-tauri, frontend, target/debug, …). A plain relative
  // lookup like "../../.env" silently fails when cwd shifts, leaving the
  // app blind to keys like DEEPSEEK_API_KEY.
  //
  // We anchor the search at CARGO_MANIFEST_DIR (frontend/src-tauri at
  // compile time) and also try a few common fallbacks. The first hit wins.
  {
      use std::path::PathBuf;
      let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
      let candidates: Vec<PathBuf> = vec![
          manifest_dir.join("../../.env"),   // monorepo root (preferred)
          manifest_dir.join("../.env"),      // frontend/.env
          manifest_dir.join(".env"),         // src-tauri/.env
          PathBuf::from("../../.env"),       // cwd-relative fallbacks
          PathBuf::from("../.env"),
          PathBuf::from(".env"),
      ];

      let mut loaded_from: Option<PathBuf> = None;
      for candidate in &candidates {
          if candidate.is_file() {
              match dotenvy::from_path(candidate) {
                  Ok(_) => {
                      loaded_from = Some(candidate.clone());
                      break;
                  }
                  Err(e) => {
                      eprintln!("[env] failed to parse {}: {}", candidate.display(), e);
                  }
              }
          }
      }

      match loaded_from {
          Some(path) => {
              eprintln!("[env] loaded .env from {}", path.display());
          }
          None => {
              eprintln!(
                  "[env] WARNING: no .env found in any of: {:?}",
                  candidates.iter().map(|p| p.display().to_string()).collect::<Vec<_>>()
              );
          }
      }
  }

  let is_test_env = is_test_mode();

  if is_test_env {
      info!("╔══════════════════════════════════════════════════╗");
      info!("║  🧪 ALPHA_TEST_MODE ACTIVE — Mocking Live APIs  ║");
      info!("╚══════════════════════════════════════════════════╝");
  }

  tauri::Builder::default()
    .setup(move |app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // ── Local Workspace SQLite Database ───────────────────────────
      match db::init_db() {
          Ok(db_state) => {
              app.manage(db_state);
              info!("Workspace SQLite database initialised and registered.");
          }
          Err(e) => {
              error!("Workspace DB init failed: {} — drawings will not persist.", e);
          }
      }

      // ── Quant Radar: Live Market Scanner ──────────────────────────
      // Spawns an async background worker that continuously evaluates
      // ConsensusEngine across 50 F&O symbols and emits `radar-alert`
      // events when institutional strategies fire.  Runs on a dedicated
      // tokio task — never blocks the UI thread.
      quant::radar::spawn_radar_worker(app.handle().clone());

      if is_test_env {
          // ══════════════════════════════════════════════════════════════
          // TEST MODE: Bypass all live API connections.
          // Spawn a mock OHLC tick emitter instead of connecting to WS.
          // ══════════════════════════════════════════════════════════════
          let app_handle_mock = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              info!("[TEST MODE] Mock OHLC tick emitter started (100ms interval)");
              loop {
                  let tick = mock_ohlc_tick();
                  let _ = app_handle_mock.emit("ohlc-tick", tick);
                  tokio::time::sleep(std::time::Duration::from_millis(100)).await;
              }
          });

          // Emit a mock consensus report after a short delay (simulates startup)
          let app_handle_consensus = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              tokio::time::sleep(std::time::Duration::from_millis(500)).await;
              let mock_consensus = serde_json::json!({
                  "symbol": "RELIANCE",
                  "trend_score": 75,
                  "momentum_state": "NEUTRAL",
                  "volatility_state": "NORMAL",
                  "volume_flow_state": "ACCUMULATION",
                  "active_patterns": ["Bullish Engulfing", "Hammer"],
                  "active_strategies": ["Golden Cross", "VWAP Bounce (Bullish)"]
              });
              let _ = app_handle_consensus.emit("quant-consensus", mock_consensus);
              info!("[TEST MODE] Mock consensus report emitted.");
          });

      } else {
          // ══════════════════════════════════════════════════════════════
          // PRODUCTION MODE: Connect to live services.
          // ══════════════════════════════════════════════════════════════

          // ── QuestDB Connection Pool (PG wire :8812) ─────────────────────
          let questdb_url = std::env::var("QUESTDB_POSTGRES_URL")
              .unwrap_or_else(|_| "postgresql://admin:quest@localhost:8812/qdb".into());

          let app_handle_db = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              match sqlx::postgres::PgPoolOptions::new()
                  .max_connections(5)
                  .connect(&questdb_url)
                  .await
              {
                  Ok(pool) => {
                      info!("QuestDB PG pool connected → {}", questdb_url);

                      // Run historical_candles migration
                      services::history_loader::run_migration(&pool).await;

                      // Store pool as managed state for Tauri commands
                      app_handle_db.manage(pool.clone());
                      info!("QuestDB pool registered as Tauri managed state.");

                      // ── Auto-load historical data for configured instruments ──
                      let api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
                      let access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();
                      let instrument_tokens = std::env::var("KITE_INSTRUMENT_TOKENS").unwrap_or_default();

                      if !api_key.is_empty() && !access_token.is_empty() && !instrument_tokens.is_empty() {
                          let pool_bg = pool.clone();
                          tokio::spawn(async move {
                              let cleaned = instrument_tokens.replace('"', "");
                              for pair in cleaned.split(',') {
                                  let parts: Vec<&str> = pair.trim().split(':').collect();
                                  if parts.len() < 2 {
                                      warn!("Skipping malformed instrument token pair: {}", pair);
                                      continue;
                                  }
                                  let token: u32 = match parts[0].parse() {
                                      Ok(t) => t,
                                      Err(_) => {
                                          warn!("Invalid instrument token: {}", parts[0]);
                                          continue;
                                      }
                                  };
                                  let symbol = parts[1];

                                  info!("Auto-loading historical data for {} (token {})...", symbol, token);
                                  match services::history_loader::load_historical_data(
                                      &pool_bg,
                                      token,
                                      symbol,
                                      &api_key,
                                      &access_token,
                                  ).await {
                                      Ok(count) => info!("Historical data loaded: {} — {} candles.", symbol, count),
                                      Err(e) => error!("Historical data load failed for {}: {}", symbol, e),
                                  }

                                  tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                              }
                              info!("Historical auto-load complete for all configured instruments.");
                          });
                      } else {
                          warn!("Skipping historical auto-load: KITE_API_KEY, KITE_ACCESS_TOKEN, or KITE_INSTRUMENT_TOKENS not set.");
                      }
                  }
                  Err(e) => {
                      error!("QuestDB connection failed: {} — historical commands will be unavailable.", e);
                  }
              }
          });

          // ── OHLC WS → IPC Bridge (port 8081) ────────────────────────────
          let app_handle = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8081").await {
                  let (_, mut read) = ws_stream.split();
                  while let Some(message) = read.next().await {
                      if let Ok(msg) = message {
                          if let Ok(text) = msg.into_text() {
                              if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                                  let _ = app_handle.emit("ohlc-tick", json);
                              }
                          }
                      }
                  }
              }
          });

          // ── Predictive WS → IPC Bridge (port 8082) ──────────────────────
          let app_handle_2 = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8082").await {
                  let (_, mut read) = ws_stream.split();
                  while let Some(message) = read.next().await {
                      if let Ok(msg) = message {
                          if let Ok(text) = msg.into_text() {
                              if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                                  let _ = app_handle_2.emit("predictive-tick", json);
                              }
                          }
                      }
                  }
              }
          });

          // ── Quant-RAG Insight WS → IPC Bridge (port 8083) ──────────────
          let app_handle_3 = app.handle().clone();
          tauri::async_runtime::spawn(async move {
              if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8083").await {
                  let (_, mut read) = ws_stream.split();
                  while let Some(message) = read.next().await {
                      if let Ok(msg) = message {
                          if let Ok(text) = msg.into_text() {
                              if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                                  let _ = app_handle_3.emit("insight-tick", json);
                              }
                          }
                      }
                  }
              }
          });
      }

      Ok(())
    })
    .invoke_handler(tauri::generate_handler![
        commands::charts::get_historical_view,
        commands::charts::load_historical,
        commands::charts::fetch_questdb,
        commands::charts::get_pool_status,
        commands::deep_quant::run_deep_quant_analysis,
        commands::sentiment::fetch_symbol_sentiment,
        db::save_workspace,
        db::load_workspace,
        db::log_completed_trade,
        db::get_trade_history,
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
