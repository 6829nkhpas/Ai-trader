use tauri::Manager;
use log::{info, error};

pub mod commands;
pub mod db;
pub mod quant;
pub mod services;
pub mod execution;

use commands::security::SecureKeyStore;


/// Check if the application is running in E2E test mode.
/// When ALPHA_TEST_MODE is set, live APIs (Zerodha/DeepSeek) are bypassed
/// and replaced with deterministic mock data.
pub fn is_test_mode() -> bool {
    std::env::var("ALPHA_TEST_MODE").is_ok()
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

  // ── Active Symbol State (shared between test mock + subscribe_ticker cmd) ─
  // Managed directly (no Arc wrapper) — Tauri wraps managed state in Arc internally.
  // Accessible in commands via `tauri::State<'_, commands::ticker::ActiveSymbolState>`.
  let active_symbol_state = commands::ticker::ActiveSymbolState::new("RELIANCE");
  let (tx, _rx) = tokio::sync::broadcast::channel::<(String, quant::vwepr::OhlcCandle)>(1024);

  let is_test_env = is_test_mode();

  if is_test_env {
      info!("╔══════════════════════════════════════════════════╗");
      info!("║  🧪 ALPHA_TEST_MODE ACTIVE — Mocking Live APIs  ║");
      info!("╚══════════════════════════════════════════════════╝");
  }

  tauri::Builder::default()
    .plugin({
      // ── Stronghold Encrypted Credential Vault ──────────────────────────
      // Argon2id derives a 32-byte key from the vault password.
      // Fixed salt ensures the same key is derived on every launch.
      // The password is application-defined (not user-visible).
      tauri_plugin_stronghold::Builder::new(|password| {
          // argon2 v0.5 (RustCrypto) raw key derivation path.
          // salt must be ≥ 8 bytes; we use 32 fixed bytes.
          let salt = b"alpha_suite_v3_stronghold_salt_01"; // 32 bytes
          let mut output = vec![0u8; 32];
          argon2::Argon2::default()
               .hash_password_into(password.as_bytes(), salt, &mut output)
               .unwrap_or_else(|_| {
                   // Should never fail with valid static inputs, but we
                   // must not panic in the hash closure.
                   for (i, b) in output.iter_mut().enumerate() { *b = i as u8; }
               });
          output
      })
      .build()
    })
    .manage(active_symbol_state)
    .manage(tx.clone())
    .manage(SecureKeyStore::new())
    .manage(std::sync::Mutex::new(execution::paper::VirtualPortfolio {
        balance: 1000000.0,
        active_positions: vec![],
        trade_history: vec![],
    }))
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

              // ── Instrument Master: Non-blocking daily CSV sync ─────────
              // Downloads the full NSE instrument list from Kite and caches
              // it in the local SQLite DB for fast search. Runs in background.
              let app_handle_instruments = app.handle().clone();
              tauri::async_runtime::spawn(async move {
                  services::instrument_master::run_instrument_sync(app_handle_instruments).await;
              });
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

      // ── Local Tool Server (port 8084) ─────────────────────────────
      let app_handle_server = app.handle().clone();
      tauri::async_runtime::spawn(async move {
          quant::tool_server::run_tool_server(app_handle_server).await;
      });

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

                  // ── Historical data is now LAZY-LOADED ────────────────────────
                  //
                  // Historical data is now fetched on-demand from the React UI
                  // via `invoke("load_historical", { symbol, instrumentToken })`
                  // (see commands::charts::load_historical) and cached in
                  // QuestDB on first request. Subsequent reads hit the cache
                  // through `get_historical_view` with dynamic SAMPLE BY.
                  info!(
                      "Historical auto-loader disabled — data loads on-demand per UI request."
                  );
              }
              Err(e) => {
                  error!("QuestDB connection failed: {} — historical commands will be unavailable.", e);
              }
          }
      });

      // ── OHLC / Predictive / Insight WS → IPC Bridges ───────────────
      //
      // Bridges are now bootstrapped lazily on the first
      // `subscribe_ticker` IPC call from the UI — see
      // `services::live_bridges::ensure_bootstrapped()`.
      info!(
          "Live WS bridges (OHLC/Predictive/Insight) deferred — \
           will start on first subscribe_ticker."
      );

      Ok(())
    })
    .invoke_handler(tauri::generate_handler![
        commands::ticker::subscribe_ticker,
        commands::instruments::search_instruments,
        commands::charts::get_historical_view,
        commands::charts::load_historical,
        commands::charts::fetch_questdb,
        commands::charts::get_pool_status,
        commands::deep_quant::run_deep_quant_analysis,
        commands::deep_quant::run_ai_analysis,
        commands::deep_quant::run_deep_quant_agent,
        commands::deep_quant::deploy_ai_sentinel,
        execution::paper::execute_paper_trade,
        execution::paper::get_paper_portfolio,
        commands::sentiment::fetch_symbol_sentiment,
        commands::quant::compute_ghost_curve,
        commands::security::save_api_key,
        commands::security::check_api_key_exists,
        commands::security::hydrate_key_cache,
        db::save_workspace,
        db::load_workspace,
        db::log_completed_trade,
        db::get_trade_history,
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
