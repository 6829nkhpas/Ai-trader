use tauri::Emitter;
use tauri::Manager;
use futures_util::StreamExt;
use tokio_tungstenite::connect_async;
use log::{info, warn, error};

mod commands;
mod db;
mod quant;
mod services;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  // Load .env from monorepo root (two directories up from src-tauri)
  let _ = dotenvy::from_filename("../../.env");

  tauri::Builder::default()
    .setup(|app| {
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
                  // Reads KITE_INSTRUMENT_TOKENS from .env (format: "token:SYMBOL,...")
                  // and triggers background 5-year historical fetch for each.
                  let api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
                  let access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();
                  let instrument_tokens = std::env::var("KITE_INSTRUMENT_TOKENS").unwrap_or_default();

                  if !api_key.is_empty() && !access_token.is_empty() && !instrument_tokens.is_empty() {
                      let pool_bg = pool.clone();
                      tokio::spawn(async move {
                          // Parse "738561:RELIANCE,260105:BANKNIFTY,..." format
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

                              // Small delay between symbols to avoid Kite rate limits
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

      Ok(())
    })
    .invoke_handler(tauri::generate_handler![
        commands::charts::get_historical_view,
        commands::charts::load_historical,
        commands::charts::fetch_questdb,
        commands::charts::get_pool_status,
        commands::deep_quant::run_deep_quant_analysis,
        db::save_workspace,
        db::load_workspace,
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
