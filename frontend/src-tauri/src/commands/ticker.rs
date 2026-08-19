// commands/ticker.rs — Dynamic symbol subscription command
//
// Manages the currently active chart symbol in a thread-safe Tauri state.
// Called by the frontend on every symbol switch to keep the Rust backend
// in sync with the chart's active instrument.
//
// On symbol switch (PRODUCTION):
//   1. Updates ActiveSymbolState so the mock emitter and UI reads the correct symbol.
//   2. Resolves the Kite instrument token via the aggregator's /api/kite/instruments.
//   3. Sends "subscribe:TOKEN:SYMBOL\n" to the ingestion control TCP server (:8085)
//      so the Kite WebSocket immediately starts streaming the new symbol's ticks.

use tokio::sync::Mutex;
use log::info;

/// Thread-safe container for the currently active chart symbol.
pub struct ActiveSymbolState {
    pub symbol: Mutex<String>,
}

impl ActiveSymbolState {
    pub fn new(initial: &str) -> Self {
        Self {
            symbol: Mutex::new(initial.to_string()),
        }
    }
}

/// Tauri IPC command: switch the active chart symbol.
///
/// Returns immediately — all network side-effects run in a background task.
///
/// # Frontend usage
/// ```ts
/// await invoke('subscribe_ticker', { symbol: 'INFY' });
/// ```
#[tauri::command]
pub async fn subscribe_ticker(
    app: tauri::AppHandle,
    state: tauri::State<'_, ActiveSymbolState>,
    symbol: String,
) -> Result<(), String> {
    let upper = symbol.trim().to_uppercase();
    if upper.is_empty() {
        return Err("subscribe_ticker: symbol must not be empty".to_string());
    }

    // ── Lazy bring-up of the internal WS → IPC bridges ──────────────────
    crate::services::live_bridges::ensure_bootstrapped(&app);

    {
        let mut lock = state.symbol.lock().await;
        let prev = lock.clone();
        *lock = upper.clone();
        info!("[subscribe_ticker] Active symbol: {} → {}", prev, upper);
    }

    // ── Resolve instrument token from local SQLite cache first ───────────
    let local_token: Option<u32> = {
        use tauri::Manager;
        let db_state: tauri::State<'_, crate::db::DbState> = app.state();
        crate::commands::instruments::resolve_instrument_token(&db_state, &upper)
    };

    // Fire-and-forget: notify ingestion service
    let sym = upper.clone();
    tokio::spawn(async move {
        if let Some(token) = local_token {
            // Fast path: token resolved locally — skip HTTP lookup
            info!("[subscribe_ticker] Token {} resolved locally for {}", token, sym);
            send_subscribe_to_ingestion(&sym, token).await;
        } else {
            // Fallback: resolve via aggregator HTTP API
            notify_ingestion_subscribe(&sym).await;
        }
    });

    Ok(())
}

/// Ensure `symbol`'s spot ticks are being ingested into QuestDB `live_ticks`,
/// WITHOUT changing the active chart symbol.
///
/// Reuses the same token-resolution + ingestion-control path as
/// `subscribe_ticker` (local SQLite cache first, then the aggregator HTTP
/// lookup), and bootstraps the live WS→IPC bridges if needed. Intended for
/// callers that need a symbol's spot available for a side feature (e.g. the F&O
/// option-chain subscriber needs the underlying's spot to resolve the ATM) but
/// must not disturb the user's charted instrument. Fire-and-forget; never panics.
pub async fn ensure_spot_subscribed(app: &tauri::AppHandle, symbol: &str) {
    let upper = symbol.trim().to_uppercase();
    if upper.is_empty() {
        return;
    }

    // Bring up the internal WS → IPC bridges so ticks actually flow.
    crate::services::live_bridges::ensure_bootstrapped(app);

    let local_token: Option<u32> = {
        use tauri::Manager;
        let db_state: tauri::State<'_, crate::db::DbState> = app.state();
        crate::commands::instruments::resolve_instrument_token(&db_state, &upper)
    };

    if let Some(token) = local_token {
        info!("[ensure_spot_subscribed] Token {} resolved locally for {}", token, upper);
        send_subscribe_to_ingestion(&upper, token).await;
    } else {
        notify_ingestion_subscribe(&upper).await;
    }
}

/// Direct path: send subscribe command to ingestion when token is already known.
/// Skips the HTTP lookup entirely — used when the local SQLite cache has the token.
async fn send_subscribe_to_ingestion(symbol: &str, token: u32) {
    let control_port = std::env::var("INGESTION_CONTROL_PORT")
        .unwrap_or_else(|_| "8085".to_string());

    use tokio::io::AsyncWriteExt;
    let addr = format!("{}:{}", crate::server::host(), control_port);
    match tokio::net::TcpStream::connect(&addr).await {
        Ok(mut stream) => {
            let cmd = format!("subscribe:{}:{}\n", token, symbol);
            match stream.write_all(cmd.as_bytes()).await {
                Ok(_) => info!(
                    "[subscribe_ticker] ✓ {} (token {}) → ingestion subscribed (local resolve)",
                    symbol, token
                ),
                Err(e) => log::warn!("[subscribe_ticker] Control write error: {}", e),
            }
        }
        Err(e) => {
            log::warn!(
                "[subscribe_ticker] Cannot reach ingestion control :{} — {}\
                 \n  (ingestion may not be running or INGESTION_CONTROL_PORT is wrong)",
                control_port, e
            );
        }
    }
}

/// Resolves the instrument token for `symbol` through the market-data provider,
/// then sends a `subscribe:TOKEN:SYMBOL\n` command to the ingestion service's TCP
/// control port.
async fn notify_ingestion_subscribe(symbol: &str) {
    let control_port = std::env::var("INGESTION_CONTROL_PORT")
        .unwrap_or_else(|_| "8085".to_string());

    // ── Step 1: Token lookup ─────────────────────────────────────────────────
    // Delegated to `providers::registry::market_data()` (P14). The HTTP call it
    // makes is the one that used to be inline here, including the fix this
    // comment records: resolution goes through `server::kite_url()` so it follows
    // the same path as every other broker REST call — the public HTTPS gateway
    // (`{base}/kite`) in a shipped thin client, or the direct
    // `http://<host>:8087/api/kite` proxy in local dev. It previously built
    // `http://<host>:{KITE_API_PORT}/api/kite` with KITE_API_PORT defaulting to
    // 8084 — the tool-server's port, not the Kite proxy's (8087). In production
    // 8084 is neither published by docker-compose nor open in the firewall, so
    // the lookup always failed and live ticks were never subscribed.
    //
    // `Ok(None)` (the feed answered, no such symbol) and `Err` (the lookup itself
    // failed) are both non-fatal here and both warn — but they warn differently,
    // because one is a bad symbol and the other is an outage.
    let token: Option<u32> = match crate::providers::registry::market_data()
        .instrument_token(symbol, "NSE")
        .await
    {
        Ok(found) => found,
        Err(e) => {
            log::warn!("[subscribe_ticker] Instrument lookup failed for {}: {}", symbol, e);
            None
        }
    };

    let token = match token {
        Some(t) => t,
        None => {
            log::warn!(
                "[subscribe_ticker] No token found for {} — live ticks unavailable until resolved.",
                symbol
            );
            return;
        }
    };

    // ── Step 2: Notify ingestion control server ───────────────────────────────
    use tokio::io::AsyncWriteExt;
    let addr = format!("{}:{}", crate::server::host(), control_port);
    match tokio::net::TcpStream::connect(&addr).await {
        Ok(mut stream) => {
            let cmd = format!("subscribe:{}:{}\n", token, symbol);
            match stream.write_all(cmd.as_bytes()).await {
                Ok(_) => info!(
                    "[subscribe_ticker] ✓ {} (token {}) → ingestion subscribed (HTTP resolve)",
                    symbol, token
                ),
                Err(e) => log::warn!("[subscribe_ticker] Control write error: {}", e),
            }
        }
        Err(e) => {
            log::warn!(
                "[subscribe_ticker] Cannot reach ingestion control :{} — {}\
                 \n  (ingestion may not be running or INGESTION_CONTROL_PORT is wrong)",
                control_port, e
            );
        }
    }
}
