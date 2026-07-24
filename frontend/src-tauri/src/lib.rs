use tauri::{Manager, Emitter};
use log::{info, error};

pub mod commands;
pub mod db;
pub mod quant;
pub mod services;
pub mod execution;
pub mod server;

use commands::security::SecureKeyStore;

/// Handles deep links received from the OS (e.g. alphasuite://broker-callback?token=XXXXX)
fn handle_deep_link(app: &tauri::AppHandle, url_str: &str) {
    info!("[deep link] Intercepted incoming URL: {}", url_str);
    if let Ok(parsed_url) = url::Url::parse(url_str) {
        // Match path or host structure
        let is_callback = parsed_url.host_str() == Some("broker-callback")
            || parsed_url.path().contains("broker-callback");

        let is_payment_success = parsed_url.host_str() == Some("payment-success")
            || parsed_url.path().contains("payment-success");

        let is_login = parsed_url.host_str() == Some("login")
            || parsed_url.path().contains("login");

        if is_callback {
            let mut access_token = None;
            for (key, val) in parsed_url.query_pairs() {
                if key == "token" || key == "access_token" {
                    access_token = Some(val.into_owned());
                    break;
                }
            }

            if let Some(token) = access_token {
                info!("[deep link] Parsed Zerodha token. Encryption-saving to vault...");
                if let Some(store) = app.try_state::<SecureKeyStore>() {
                    store.insert("zerodha", &token);
                    info!("[deep link] Encrypted token cached in SecureKeyStore.");

                    // Emit event to React frontend so the UI clears any loader screen
                    if let Err(e) = app.emit("broker-connection-success", serde_json::json!({})) {
                        error!("[deep link] Failed to emit broker-connection-success: {:?}", e);
                    } else {
                        info!("[deep link] Emitted connection success event to UI.");
                    }
                } else {
                    error!("[deep link] SecureKeyStore is not initialized.");
                }
            } else {
                log::warn!("[deep link] No token found in url parameters.");
            }
        } else if is_payment_success {
            info!("[deep link] Parsed payment-success deep link. Emitting to UI...");
            if let Err(e) = app.emit("payment-success", serde_json::json!({})) {
                error!("[deep link] Failed to emit payment-success: {:?}", e);
            } else {
                info!("[deep link] Emitted payment-success event to UI.");
            }
        } else if is_login {
            let mut login_token = None;
            for (key, val) in parsed_url.query_pairs() {
                if key == "t" {
                    login_token = Some(val.into_owned());
                    break;
                }
            }

            if let Some(token) = login_token {
                info!("[deep link] Parsed desktop login token. Emitting to UI...");
                if let Err(e) = app.emit("desktop-login-success", serde_json::json!({ "token": token })) {
                    error!("[deep link] Failed to emit desktop-login-success: {:?}", e);
                } else {
                    info!("[deep link] Emitted desktop-login-success event to UI.");
                }
            }
        }
    } else {
        error!("[deep link] Failed to parse deep link URL structure.");
    }
}

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
    .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
        // Focus the existing window(s)
        for window in app.webview_windows().values() {
            let _ = window.set_focus();
        }
        
        // Pass any deep link URLs from command line arguments to the active instance
        for arg in args {
            if arg.starts_with("strat://") || arg.contains("broker-callback") || arg.contains("payment-success") {
                handle_deep_link(app, &arg);
            }
        }
    }))
    .plugin(tauri_plugin_deep_link::init())
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
    .manage(quant::radar::RadarRegistry::new())
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

      // ── OS Deep Linking Interceptor (Tauri Plugin) ─────────────────
      #[cfg(any(target_os = "windows", target_os = "macos", target_os = "linux"))]
      {
          use tauri_plugin_deep_link::DeepLinkExt;
          
          // Programmatically register the scheme in the OS registry at startup
          let _ = app.deep_link().register("strat");
          
          let handle = app.handle().clone();
          app.deep_link().on_open_url(move |event| {
              for url in event.urls() {
                  handle_deep_link(&handle, url.as_str());
              }
          });
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

              // ── NFO Derivatives Master: Non-blocking daily sync ────────
              // Downloads + persists the Kite NFO segment (options/futures)
              // into the `nfo_instruments` SQLite table on the same 24h
              // cache schedule. Independent of the NSE sync above.
              let app_handle_nfo = app.handle().clone();
              tauri::async_runtime::spawn(async move {
                  services::instrument_master::run_nfo_sync(app_handle_nfo).await;
              });

              // ── Option-Chain Subscriber (Options Data Foundation F1) ───
              // Periodically resolves the bounded option-chain selection for
              // each configured underlying and pushes it to the ingestion
              // control port (:8085). Skips underlyings without spot; retries
              // push failures on the next tick. Runs on its own task — never
              // blocks the equity tick path.
              //
              // The RequestedUnderlyings registry lets the UI dynamically add
              // stock/extra chains (opened from search) that the subscriber
              // ingests alongside the configured indexes.
              app.manage(services::option_chain_subscriber::RequestedUnderlyings::default());
              let app_handle_chain = app.handle().clone();
              tauri::async_runtime::spawn(async move {
                  services::option_chain_subscriber::run_option_chain_subscriber(app_handle_chain).await;
              });
          }
          Err(e) => {
              error!("Workspace DB init failed: {} — drawings will not persist.", e);
          }
      }

      // ── Quant Radar: User-Driven Live Market Scanner (FEAT-037) ────
      // Spawns an async background worker that evaluates the located
      // pattern/strategy scanner across the user's chosen radar symbols
      // (held in the shared RadarRegistry) and emits enriched
      // `radar-alert` events carrying located detections for on-chart
      // visualization.  Runs on a dedicated tokio task — never blocks UI.
      // Opt-in via RADAR_ENABLED=true; on-demand scan_quant_radar always on.
      quant::radar::spawn_radar_worker(app.handle().clone());

      // ── Local Tool Server (port 8084) ─────────────────────────────
      let app_handle_server = app.handle().clone();
      tauri::async_runtime::spawn(async move {
          quant::tool_server::run_tool_server(app_handle_server).await;
      });

      // ── QuestDB Connection Pool (PG wire :8812) ─────────────────────
      let questdb_url = std::env::var("QUESTDB_POSTGRES_URL")
          .unwrap_or_else(|_| format!("postgresql://admin:quest@{}:8812/qdb", crate::server::host()));

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
        commands::deep_quant::cancel_deep_quant_agent,
        commands::deep_quant::ask_trade_question,
        commands::deep_quant::get_multi_timeframe_chart_patterns,
        commands::deep_quant::deploy_ai_sentinel,
        execution::paper::execute_paper_trade,
        execution::paper::get_paper_portfolio,
        commands::sentiment::fetch_symbol_sentiment,
        commands::quant::compute_ghost_curve,
        commands::radar::scan_radar_symbol,
        commands::radar::scan_quant_radar,
        commands::radar::set_radar_symbols,
        commands::radar::get_radar_symbols,
        commands::fno::get_fno_analytics,
        commands::fno::fno_list_chains,
        commands::fno::fno_list_expiries,
        commands::fno::fno_request_underlying,
        commands::fno::fno_subscribe,
        commands::fno::fno_unsubscribe,
        commands::fno::fno_resolve_nearest_contract,
        commands::fno::fno_resolve_option_contract,
        commands::security::save_api_key,
        commands::security::check_api_key_exists,
        commands::security::hydrate_key_cache,
        commands::security::vault_store_token,
        commands::security::open_browser,
        db::save_workspace,
        db::load_workspace,
        db::log_completed_trade,
        db::get_trade_history,
    ])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
