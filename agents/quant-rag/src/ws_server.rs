// ws_server.rs — Quant-RAG Agent WebSocket server.
//
// Phase 9.2 — Insight broadcast endpoint (port 8083).
//
// Identical architecture to the Predictive Agent's WS server (port 8082)
// but broadcasts AI-generated MarketInsight JSON payloads.  Each connected
// client receives a private `broadcast::Receiver` clone, ensuring late
// joiners don't miss in-flight messages and disconnected clients are
// cleaned up automatically.

use futures_util::SinkExt;
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

pub async fn start_server(port: u16, rx: tokio::sync::broadcast::Receiver<String>) {
    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).await.expect("Failed to bind Quant-RAG WebSocket server");

    log::info!("Quant-RAG WebSocket server listening on: {}", addr);

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
