use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use futures_util::SinkExt;
use tokio::net::TcpListener;
use tokio::sync::broadcast::error::RecvError;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

use crate::metrics::AlphaMetrics;

/// Latest in-progress candle JSON per symbol, written by the consumer on every
/// tick and replayed to a client right after its handshake.
///
/// Without this a fresh connection (page load, refresh, reconnect) received
/// nothing until the NEXT tick of the symbol it charts, which for an illiquid
/// contract or a quiet minute read as "the chart is not live".
pub type LiveSnapshot = Arc<RwLock<HashMap<String, String>>>;

pub fn new_snapshot() -> LiveSnapshot {
    Arc::new(RwLock::new(HashMap::new()))
}

/// Broadcasts candle JSON to every attached client.
///
/// `metrics` counts clients from handshake completion rather than from
/// `accept()`: a TCP connection that never upgrades is not a subscriber, and
/// counting it would leak the gauge upward every time a port scanner or a health
/// probe touched this socket.
pub async fn start_server(
    port: u16,
    rx: tokio::sync::broadcast::Receiver<String>,
    snapshot: LiveSnapshot,
    metrics: AlphaMetrics,
) {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await.expect("Failed to bind WebSocket server");

    log::info!("Alpha Terminal WebSocket server listening on: {}", addr);

    while let Ok((stream, _)) = listener.accept().await {
        let mut client_rx = rx.resubscribe();
        let client_metrics = metrics.clone();
        let client_snapshot = snapshot.clone();

        tokio::spawn(async move {
            if let Ok(mut ws_stream) = accept_async(stream).await {
                client_metrics.ws_client_connected();

                // Replay the current state so the first paint is immediate.
                // Clone out of the lock before awaiting: a std lock must not be
                // held across an await point.
                let replay: Vec<String> = client_snapshot
                    .read()
                    .map(|m| m.values().cloned().collect())
                    .unwrap_or_default();
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
                        // A burst outran this client. The old loop treated this as
                        // fatal and dropped the socket, which made the browser sit
                        // out a 3s reconnect and then wait for the next tick again.
                        // Skipped messages are stale in-progress candles that the
                        // next tick supersedes, so just keep going.
                        Err(RecvError::Lagged(n)) => {
                            log::warn!("WS client lagged; skipped {} candle updates", n);
                        }
                        Err(RecvError::Closed) => break,
                    }
                }

                // Paired with the connect above. Every exit from the forward loop
                // — send failure, lag, channel closed — falls through here, so
                // the gauge cannot drift upward.
                client_metrics.ws_client_disconnected();
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::StreamExt;
    use tokio::time::{timeout, Duration};

    /// A fresh client must receive the snapshot BEFORE any live broadcast, and a
    /// `Lagged` receiver must keep the connection instead of dropping it.
    #[tokio::test]
    async fn replays_snapshot_on_connect_and_survives_lag() {
        let (tx, _) = tokio::sync::broadcast::channel::<String>(2);
        let snapshot = new_snapshot();
        snapshot.write().unwrap().insert("RELIANCE".into(), "snap".into());

        let port = 18081;
        tokio::spawn(start_server(port, tx.subscribe(), snapshot, AlphaMetrics::new()));
        tokio::time::sleep(Duration::from_millis(100)).await;

        let (mut ws, _) = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{port}"))
            .await
            .expect("connect");

        let first = timeout(Duration::from_secs(2), ws.next()).await.unwrap().unwrap().unwrap();
        assert_eq!(first.into_text().unwrap(), "snap");

        // Overrun the capacity-2 channel; the client should still get the tail.
        for i in 0..10 {
            tx.send(format!("live{i}")).unwrap();
        }
        let mut got_tail = false;
        while let Ok(Some(Ok(msg))) = timeout(Duration::from_millis(500), ws.next()).await {
            if msg.into_text().unwrap() == "live9" {
                got_tail = true;
                break;
            }
        }
        assert!(got_tail, "client was dropped on Lagged instead of catching up");
    }
}
