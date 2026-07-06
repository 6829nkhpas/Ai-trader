// src/kite_client.rs — Zerodha Kite WebSocket transport layer
//
// Responsibility: open a raw authenticated WebSocket connection to the
// Kite live-tick endpoint and return the stream to the caller.
//
// The higher-level logic (subscription commands, binary frame parsing,
// auto-reconnect) lives in kite_ws.rs. This module is intentionally thin
// so it can be unit-tested independently of the parsing logic.
//
// Kite WebSocket endpoint spec:
//   wss://ws.kite.trade/?api_key=<api_key>&access_token=<access_token>
//
// Authentication is embedded in the query string — no separate handshake
// message is required for the initial connection.

use log::info;
use tokio_tungstenite::{connect_async, MaybeTlsStream};
use tokio_tungstenite::tungstenite::Message;
use tokio::net::TcpStream;
use futures_util::stream::SplitStream;
use tokio_tungstenite::WebSocketStream;

/// The read-half of the authenticated Kite WebSocket connection.
/// The write-half is returned separately so the caller can send
/// subscription and mode commands independently.
pub type KiteWsReader = SplitStream<WebSocketStream<MaybeTlsStream<TcpStream>>>;

/// The write-half of the authenticated Kite WebSocket connection.
pub type KiteWsWriter =
    futures_util::stream::SplitSink<WebSocketStream<MaybeTlsStream<TcpStream>>, Message>;

/// Establishes an authenticated WebSocket connection to the Kite live-tick
/// endpoint and splits the stream into a reader/writer pair.
///
/// # Arguments
/// * `api_key`      — Kite Connect API key (from `.env` → `KITE_API_KEY`)
/// * `access_token` — OAuth access token (from `.env` or `kite_auth` exchange)
///
/// # Returns
/// `Ok((KiteWsReader, KiteWsWriter))` on success, or a boxed error if the
/// TLS/TCP handshake fails.
///
/// # Example
/// ```ignore
/// let (reader, writer) = connect_ticker(&api_key, &access_token).await?;
/// // send subscription commands via writer, receive binary frames via reader
/// ```
pub async fn connect_ticker(
    api_key: &str,
    access_token: &str,
) -> Result<(KiteWsReader, KiteWsWriter), Box<dyn std::error::Error + Send + Sync>> {
    let url = format!(
        "wss://ws.kite.trade/?api_key={}&access_token={}",
        api_key, access_token
    );

    info!("Connecting to Kite WebSocket: wss://ws.kite.trade/?api_key={}...", &api_key[..4.min(api_key.len())]);

    let (ws_stream, response) = connect_async(&url).await?;

    info!(
        "Kite WebSocket handshake complete — HTTP status: {}",
        response.status()
    );

    // Split into independent reader / writer halves so the caller can
    // drive them from separate async tasks if desired.
    use futures_util::StreamExt;
    let (writer, reader) = ws_stream.split();

    Ok((reader, writer))
}
