// ws_server.rs — Predictive Agent WebSocket server.
//
// Phase 6.3 — Ghost Candle broadcast endpoint.
//
// Identical architecture to the Alpha Terminal's WS server but bound to
// port 8082.  Each connected client receives a private `broadcast::Receiver`
// clone, ensuring late joiners don't miss in-flight messages and
// disconnected clients are cleaned up automatically.
//
// On connect, the last signal per symbol is replayed first. The engine emits
// one signal per symbol per completed 10-minute candle, so a client that
// connects (or reconnects after a drop) mid-candle would otherwise see nothing
// for up to ten minutes — the "forecast mode shows no line after a refresh"
// report. `resubscribe()` is future-only and cannot fix that on its own.

use crate::metrics::PredictiveMetrics;
use futures_util::SinkExt;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

/// Last signal JSON per symbol, kept for replay on connect. Small (one entry
/// per instrument) and only ever touched from the two tasks below.
pub type LastSignals = Arc<Mutex<HashMap<String, String>>>;

/// * `metrics` — Prometheus handle. Subscribers are counted from the point the
///   handshake succeeds, not from `accept()`: a TCP connection that never
///   upgrades is not a subscriber, and counting it would leak the gauge upward
///   every time a port scanner or health probe touched this socket.
pub async fn start_server(
    port: u16,
    rx: tokio::sync::broadcast::Receiver<String>,
    metrics: PredictiveMetrics,
) {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await.expect("Failed to bind Predictive WebSocket server");

    log::info!("Predictive WebSocket server listening on: {}", addr);

    let last: LastSignals = Arc::new(Mutex::new(HashMap::new()));

    // Record the latest signal per symbol from the same stream clients get.
    // Keyed by the JSON `symbol` field so no second channel is needed.
    let mut record_rx = rx.resubscribe();
    let record_last = last.clone();
    tokio::spawn(async move {
        loop {
            match record_rx.recv().await {
                Ok(msg) => {
                    if let Some(sym) = symbol_of(&msg) {
                        record_last.lock().unwrap().insert(sym, msg);
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    while let Ok((stream, _)) = listener.accept().await {
        // Subscribe BEFORE snapshotting the replay set so a signal that lands
        // in between is delivered by the live stream rather than lost.
        let mut client_rx = rx.resubscribe();
        let replay: Vec<String> = last.lock().unwrap().values().cloned().collect();
        let client_metrics = metrics.clone();

        tokio::spawn(async move {
            if let Ok(mut ws_stream) = accept_async(stream).await {
                client_metrics.ws_client_connected();

                let mut alive = true;
                for msg in replay {
                    if ws_stream.send(Message::Text(msg)).await.is_err() {
                        alive = false;
                        break;
                    }
                }

                while alive {
                    match client_rx.recv().await {
                        Ok(msg) => {
                            if ws_stream.send(Message::Text(msg)).await.is_err() {
                                break;
                            }
                        }
                        Err(_) => break,
                    }
                }

                // Every exit from the forward loop — send failure, lagged
                // receiver, channel closed — falls through to here, so the
                // gauge cannot drift upward over the process lifetime.
                client_metrics.ws_client_disconnected();
            }
        });
    }
}

/// The `symbol` of a broadcast signal, for the replay map key.
fn symbol_of(json: &str) -> Option<String> {
    serde_json::from_str::<serde_json::Value>(json)
        .ok()?
        .get("symbol")?
        .as_str()
        .map(str::to_owned)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbol_of_reads_the_signal_symbol() {
        assert_eq!(symbol_of(r#"{"symbol":"NIFTY","predicted_close_price":1.0}"#).as_deref(), Some("NIFTY"));
        assert_eq!(symbol_of("not json"), None);
        assert_eq!(symbol_of(r#"{"x":1}"#), None);
    }
}
