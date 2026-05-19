// services/live_bridges.rs — Lazy WebSocket → IPC bridges
//
// ── Purpose (Alpha Suite V3 Lazy-Loading Directive) ─────────────────────
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
use tauri::{AppHandle, Emitter};
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
/// On connection failure the task logs a warning and exits — the user
/// can re-trigger bootstrap by changing symbols once the upstream
/// service comes online (the OnceCell guard is reset only on process
/// restart, so reconnect is best-effort here; full resilience is a
/// separate concern).
fn spawn_bridge(app: AppHandle, port: u16, event_name: &'static str) {
    tauri::async_runtime::spawn(async move {
        let url = format!("ws://127.0.0.1:{}", port);
        match connect_async(&url).await {
            Ok((ws_stream, _)) => {
                info!("[live_bridges] Connected → {} (event '{}')", url, event_name);
                let (_, mut read) = ws_stream.split();
                while let Some(message) = read.next().await {
                    let Ok(msg) = message else { continue };
                    let Ok(text) = msg.into_text() else { continue };
                    let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) else {
                        continue;
                    };
                    let _ = app.emit(event_name, json);
                }
                warn!(
                    "[live_bridges] Stream closed for {} — '{}' events will stop \
                     until the next process restart.",
                    url, event_name
                );
            }
            Err(e) => {
                warn!(
                    "[live_bridges] Could not connect to {} ({}). Frontend will not \
                     receive '{}' events from this bridge.",
                    url, e, event_name
                );
            }
        }
    });
}
