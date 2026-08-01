use futures_util::SinkExt;
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

use crate::metrics::AlphaMetrics;

/// Broadcasts candle JSON to every attached client.
///
/// `metrics` counts clients from handshake completion rather than from
/// `accept()`: a TCP connection that never upgrades is not a subscriber, and
/// counting it would leak the gauge upward every time a port scanner or a health
/// probe touched this socket.
pub async fn start_server(
    port: u16,
    rx: tokio::sync::broadcast::Receiver<String>,
    metrics: AlphaMetrics,
) {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await.expect("Failed to bind WebSocket server");

    log::info!("Alpha Terminal WebSocket server listening on: {}", addr);

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

                // Paired with the connect above. Every exit from the forward loop
                // — send failure, lag, channel closed — falls through here, so
                // the gauge cannot drift upward.
                client_metrics.ws_client_disconnected();
            }
        });
    }
}
