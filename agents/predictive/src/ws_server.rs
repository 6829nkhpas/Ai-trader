// ws_server.rs — Predictive Agent WebSocket server.
//
// Phase 6.3 — Ghost Candle broadcast endpoint.
//
// Identical architecture to the Alpha Terminal's WS server but bound to
// port 8082.  Each connected client receives a private `broadcast::Receiver`
// clone, ensuring late joiners don't miss in-flight messages and
// disconnected clients are cleaned up automatically.

use crate::metrics::PredictiveMetrics;
use futures_util::SinkExt;
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

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

    while let Ok((stream, _)) = listener.accept().await {
        let mut client_rx = rx.resubscribe();
        let client_metrics = metrics.clone();

        tokio::spawn(async move {
            if let Ok(mut ws_stream) = accept_async(stream).await {
                client_metrics.ws_client_connected();

                while let Ok(msg) = client_rx.recv().await {
                    if ws_stream.send(Message::Text(msg)).await.is_err() {
                        break;
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
