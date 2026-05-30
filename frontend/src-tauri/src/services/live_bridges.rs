// services/live_bridges.rs — Lazy WebSocket → IPC bridges
//
// ── Purpose (Strat Ai Lazy-Loading Directive) ─────────────────────
// The Tauri backend used to open three internal WebSocket clients on
// boot — to the aggregator's OHLC server (:8081), the Predictive engine
// (:8082) and the Quant-RAG insights stream (:8083) — regardless of
// whether the user had clicked anything.  That triggered the spurious
// `[INFO aggregator::ws_server] [WS] New connection ...` log lines on
// startup and held three live sockets open against a backend that may
// not even be running.
//
// This module exposes a single `ensure_bootstrapped()` entry point.
// The first call (driven by `subscribe_ticker` from the UI) spawns the
// three bridge tasks; subsequent calls are no-ops.
//
// ── Why a separate module ───────────────────────────────────────────────
// Keeps the bring-up logic out of `lib.rs::run()` so it can stay tightly
// scoped: this file is the *only* place that owns the bridge sockets.

use std::sync::atomic::{AtomicBool, Ordering};

use futures_util::StreamExt;
use log::{info, warn};
use tauri::{AppHandle, Emitter, Manager};
use tokio_tungstenite::connect_async;

/// Boot-once guard.  AtomicBool::compare_exchange ensures exactly one
/// task wins the race even if `subscribe_ticker` is invoked concurrently
/// for two symbols on first paint.
static BOOTSTRAPPED: AtomicBool = AtomicBool::new(false);

/// Bring the three internal WS → Tauri-event bridges online if they
/// have not been started yet.
///
/// Idempotent and lock-free; safe to call from any tokio task.
pub fn ensure_bootstrapped(app: &AppHandle) {
    // Atomically flip false → true.  If we lost the race, somebody else
    // is already wiring the bridges; we're done.
    if BOOTSTRAPPED
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }

    info!(
        "[live_bridges] First subscribe_ticker received — bootstrapping internal \
         WS bridges (OHLC :8081, Predictive :8082, Insight :8083)."
    );

    spawn_bridge(app.clone(), 8081, "ohlc-tick");
    spawn_bridge(app.clone(), 8082, "predictive-tick");
    spawn_bridge(app.clone(), 8083, "insight-tick");
}

/// Spawn one WS → Tauri-event forwarding task.
///
/// The bridge connects to `ws://127.0.0.1:<port>`, parses each text frame
/// as JSON, and re-emits it on `<event_name>` for the React layer.
///
/// The task runs a resilient reconnect loop: if the connection cannot be
/// established or the stream closes, it waits with a capped backoff and
/// retries indefinitely. This keeps live data (and the tool-server price
/// watchers fed by the OHLC bridge) flowing across transient upstream
/// restarts without requiring a full app restart.
fn spawn_bridge(app: AppHandle, port: u16, event_name: &'static str) {
    tauri::async_runtime::spawn(async move {
        let url = format!("ws://127.0.0.1:{}", port);
        let mut backoff_secs = 1u64;
        const MAX_BACKOFF_SECS: u64 = 30;

        loop {
            match connect_async(&url).await {
                Ok((ws_stream, _)) => {
                    info!("[live_bridges] Connected → {} (event '{}')", url, event_name);
                    backoff_secs = 1; // reset backoff on a successful connect
                    let (_, mut read) = ws_stream.split();
                    while let Some(message) = read.next().await {
                        let Ok(msg) = message else { continue };
                        let Ok(text) = msg.into_text() else { continue };
                        let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) else {
                            continue;
                        };
                        let _ = app.emit(event_name, json.clone());

                        if event_name == "ohlc-tick" {
                            if let (Some(symbol), Some(close)) = (
                                json.get("symbol").and_then(|s| s.as_str()),
                                json.get("close").and_then(|c| c.as_f64()),
                            ) {
                                crate::execution::paper::process_tick_for_positions(&app, symbol, close);
                            }

                            // Broadcast to the local tool server watchers
                            if let Some((symbol, candle)) = crate::quant::tool_server::parse_ohlc_tick(&json) {
                                if let Some(tx) = app.try_state::<tokio::sync::broadcast::Sender<(String, crate::quant::vwepr::OhlcCandle)>>() {
                                    let _ = tx.send((symbol, candle));
                                }
                            }
                        }
                    }
                    warn!(
                        "[live_bridges] Stream closed for {} — reconnecting in {}s.",
                        url, backoff_secs
                    );
                }
                Err(e) => {
                    warn!(
                        "[live_bridges] Could not connect to {} ({}). Retrying in {}s.",
                        url, e, backoff_secs
                    );
                }
            }

            tokio::time::sleep(std::time::Duration::from_secs(backoff_secs)).await;
            backoff_secs = (backoff_secs * 2).min(MAX_BACKOFF_SECS);
        }
    });
}
