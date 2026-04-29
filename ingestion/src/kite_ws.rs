/// Kite Connect WebSocket client — Subphase 13
///
/// Connects to wss://ws.kite.trade, subscribes to instrument tokens in Full mode,
/// parses Kite's proprietary binary tick protocol, and forwards ParsedTick structs
/// to an async mpsc channel consumed by the Kafka + QuestDB pipeline.
///
/// Binary frame layout (per Kite Connect API v3 spec):
///   Frame:  [2-byte packet count] [packet...]+
///   Packet: [2-byte length] [length-byte data]
///
///   Data layout by mode:
///   LTP   (8  bytes): token(4) + last_price(4)
///   Quote (44 bytes): + last_qty(4) + avg_price(4) + volume(4) + buy_qty(4)
///                       + sell_qty(4) + open(4) + high(4) + low(4) + close(4)
///   Full  (184 bytes): Quote + last_trade_time(4) + OI(4) + OI_hi(4) + OI_lo(4)
///                        + exchange_ts(4) + 5×bid(12) + 5×ask(12)
///   All multi-byte integers are big-endian. Prices are integer×100 (paise).

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use byteorder::{BigEndian, ReadBytesExt};
use futures_util::{SinkExt, StreamExt};
use log::{debug, error, info, warn};
use serde_json::json;
use std::io::Cursor;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

use crate::types::ParsedTick;

const KITE_WS_URL: &str = "wss://ws.kite.trade";

// Canonical packet sizes per mode
const LTP_LEN: usize = 8;
const QUOTE_LEN: usize = 44;
const FULL_LEN: usize = 184;

// Depth entry size: price(4) + quantity(4) + orders(4)
const DEPTH_ENTRY_BYTES: usize = 12;

/// Returns current Unix time in milliseconds (fallback when exchange_ts is absent).
fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// Parse a single Kite binary packet into a `ParsedTick`.
///
/// # Arguments
/// * `data`       — raw bytes of one packet (length prefix already stripped)
/// * `symbol_map` — instrument_token → NSE symbol name
fn parse_packet(data: &[u8], symbol_map: &HashMap<u32, String>) -> Option<ParsedTick> {
    if data.len() < LTP_LEN {
        return None;
    }

    let mut cur = Cursor::new(data);

    // ── Header (all modes) ────────────────────────────────────────────────────
    let instrument_token = cur.read_u32::<BigEndian>().ok()?;
    let last_price = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;

    let symbol = symbol_map
        .get(&instrument_token)
        .cloned()
        .unwrap_or_else(|| format!("TOKEN_{}", instrument_token));

    // ── LTP-only fast path ────────────────────────────────────────────────────
    if data.len() < QUOTE_LEN {
        return Some(ParsedTick {
            instrument_token,
            symbol,
            last_price,
            volume: 0,
            best_bid: last_price,
            best_ask: last_price,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            close: 0.0,
            timestamp_ms: now_ms(),
        });
    }

    // ── Quote / Full fields ───────────────────────────────────────────────────
    let _last_qty = cur.read_i32::<BigEndian>().ok()?;       // 8–11
    let _avg_price = cur.read_i32::<BigEndian>().ok()?;      // 12–15
    let volume = cur.read_i32::<BigEndian>().ok()? as u32;   // 16–19
    let _buy_qty = cur.read_i32::<BigEndian>().ok()?;        // 20–23
    let _sell_qty = cur.read_i32::<BigEndian>().ok()?;       // 24–27
    let open = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;  // 28–31
    let high = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;  // 32–35
    let low = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;   // 36–39
    let close = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0; // 40–43

    // ── Full mode: exchange timestamp + market depth ──────────────────────────
    let (best_bid, best_ask, timestamp_ms) = if data.len() >= FULL_LEN {
        let _last_trade_time = cur.read_i32::<BigEndian>().ok()?; // 44–47
        let _oi = cur.read_i32::<BigEndian>().ok()?;              // 48–51
        let _oi_day_high = cur.read_i32::<BigEndian>().ok()?;     // 52–55
        let _oi_day_low = cur.read_i32::<BigEndian>().ok()?;      // 56–59
        let exchange_ts = cur.read_i32::<BigEndian>().ok()?;      // 60–63

        // Depth: 5 bids then 5 asks, each DEPTH_ENTRY_BYTES wide
        // Best bid = first bid entry price (byte 64)
        let best_bid = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;
        let _bid1_qty = cur.read_i32::<BigEndian>().ok()?;
        let _bid1_orders = cur.read_i32::<BigEndian>().ok()?;

        // Skip remaining 4 bid entries
        let mut skip = [0u8; DEPTH_ENTRY_BYTES * 4];
        std::io::Read::read_exact(&mut cur, &mut skip).ok()?;

        // Best ask = first ask entry price
        let best_ask = cur.read_i32::<BigEndian>().ok()? as f64 / 100.0;

        let ts_ms = if exchange_ts > 0 {
            exchange_ts as i64 * 1000
        } else {
            now_ms()
        };

        (best_bid, best_ask, ts_ms)
    } else {
        (last_price, last_price, now_ms())
    };

    Some(ParsedTick {
        instrument_token,
        symbol,
        last_price,
        volume,
        best_bid,
        best_ask,
        open,
        high,
        low,
        close,
        timestamp_ms,
    })
}

/// Parse a complete Kite binary WebSocket frame (may contain multiple packets).
fn parse_frame(bytes: &[u8], symbol_map: &HashMap<u32, String>) -> Vec<ParsedTick> {
    let mut ticks = Vec::new();

    if bytes.len() < 2 {
        return ticks;
    }

    let num_packets = u16::from_be_bytes([bytes[0], bytes[1]]) as usize;
    let mut offset = 2usize;

    for _ in 0..num_packets {
        if offset + 2 > bytes.len() {
            break;
        }
        let pkt_len = u16::from_be_bytes([bytes[offset], bytes[offset + 1]]) as usize;
        offset += 2;

        if offset + pkt_len > bytes.len() {
            warn!("Malformed Kite frame: declared packet length exceeds buffer");
            break;
        }

        let packet_data = &bytes[offset..offset + pkt_len];
        if let Some(tick) = parse_packet(packet_data, symbol_map) {
            ticks.push(tick);
        }
        offset += pkt_len;
    }

    ticks
}

/// Start the Kite WebSocket listener.
///
/// Connects to Kite, subscribes to `instrument_tokens` in Full mode, and sends
/// every parsed tick onto `tx`. Reconnects automatically on disconnect.
///
/// # Arguments
/// * `api_key`          — KITE_API_KEY
/// * `access_token`     — KITE_ACCESS_TOKEN (valid until midnight IST)
/// * `instrument_tokens` — list of Kite token integers to subscribe to
/// * `symbol_map`       — token → symbol name lookup table
/// * `tx`               — mpsc sender to the downstream pipeline
pub async fn run(
    api_key: String,
    access_token: String,
    instrument_tokens: Vec<u32>,
    symbol_map: HashMap<u32, String>,
    tx: mpsc::Sender<ParsedTick>,
) {
    let url = format!("{}/?api_key={}&access_token={}", KITE_WS_URL, api_key, access_token);

    loop {
        info!("Connecting to Kite WebSocket...");
        let ws_stream = match connect_async(&url).await {
            Ok((stream, _)) => {
                info!("Kite WebSocket connected ✓");
                stream
            }
            Err(e) => {
                error!("WebSocket connection failed: {}. Retrying in 5s...", e);
                tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                continue;
            }
        };

        let (mut write, mut read) = ws_stream.split();

        // ── Subscribe: set mode to "full" for all tokens ──────────────────────
        let token_vals: Vec<serde_json::Value> = instrument_tokens
            .iter()
            .map(|&t| serde_json::Value::Number(t.into()))
            .collect();

        let subscribe_msg = json!({ "a": "subscribe", "v": token_vals }).to_string();
        let mode_msg = json!({ "a": "mode", "v": ["full", token_vals] }).to_string();

        if let Err(e) = write.send(Message::Text(subscribe_msg)).await {
            error!("Failed to send subscribe message: {}", e);
            continue;
        }
        if let Err(e) = write.send(Message::Text(mode_msg)).await {
            error!("Failed to send mode message: {}", e);
            continue;
        }
        info!("Subscribed to {} instruments in Full mode", instrument_tokens.len());

        // ── Read loop ─────────────────────────────────────────────────────────
        while let Some(msg) = read.next().await {
            match msg {
                Ok(Message::Binary(bytes)) => {
                    let ticks = parse_frame(&bytes, &symbol_map);
                    for tick in ticks {
                        debug!("[{}] LTP={} vol={}", tick.symbol, tick.last_price, tick.volume);
                        if tx.send(tick).await.is_err() {
                            error!("Tick channel closed — pipeline shutting down");
                            return;
                        }
                    }
                }
                Ok(Message::Text(text)) => {
                    // Kite sends JSON text frames for heartbeat/error messages
                    debug!("Kite text frame: {}", text);
                }
                Ok(Message::Close(frame)) => {
                    warn!("Kite WebSocket closed: {:?}. Reconnecting...", frame);
                    break;
                }
                Err(e) => {
                    error!("WebSocket read error: {}. Reconnecting...", e);
                    break;
                }
                _ => {}
            }
        }

        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
    }
}
