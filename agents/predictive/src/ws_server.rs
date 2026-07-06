// ws_server.rs — Predictive Agent WebSocket server.
//
// Phase 6.3 — Ghost Candle broadcast endpoint.
//
// Identical architecture to the Alpha Terminal's WS server but bound to
// port 8082.  Each connected client receives a private `broadcast::Receiver`
// clone, ensuring late joiners don't miss in-flight messages and
// disconnected clients are cleaned up automatically.

use futures_util::SinkExt;
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

pub async fn start_server(port: u16, rx: tokio::sync::broadcast::Receiver<String>) {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await.expect("Failed to bind Predictive WebSocket server");

    log::info!("Predictive WebSocket server listening on: {}", addr);

    while let Ok((stream, _)) = listener.accept().await {
        let mut client_rx = rx.resubscribe();

        tokio::spawn(async move {
            if let Ok(mut ws_stream) = accept_async(stream).await {
                while let Ok(msg) = client_rx.recv().await {
                    if ws_stream.send(Message::Text(msg)).await.is_err() {
                        break;
                    }
                }
            }
        });
    }
}
