// src/parser.rs — Kite binary tick frame parser
//
// Kite sends binary WebSocket frames containing one or more tick packets.
// Frame layout (big-endian throughout):
//
//   ┌────────────────────────────────────────────────────────────┐
//   │  2 bytes  │ number_of_packets  (u16)                       │
//   ├────────────────────────────────────────────────────────────┤
//   │  2 bytes  │ packet_length      (u16) — first packet        │
//   │  N bytes  │ packet_data                                    │
//   │  ...      │ (repeat for each packet)                       │
//   └────────────────────────────────────────────────────────────┘
//
// Individual packet offsets (all integers big-endian, prices in paise ÷ 100):
//
//   Bytes  0-3   instrument_token   (u32)
//   Bytes  4-7   last_traded_price  (i32, paise)
//   Bytes  8-11  last_traded_qty    (i32)  [present in Quote / Full modes]
//   Bytes 12-15  average_price      (i32, paise)
//   Bytes 16-19  volume             (i32)
//   Bytes 20-23  buy_qty            (i32)
//   Bytes 24-27  sell_qty           (i32)
//   Bytes 28-31  open               (i32, paise)
//   Bytes 32-35  high               (i32, paise)
//   Bytes 36-39  low                (i32, paise)
//   Bytes 40-43  close              (i32, paise)
//   [Full mode — 184 bytes — additionally carries 5-level market depth,
//    OI, exchange timestamp, etc.]
//
// Mode detection by packet length:
//   8   bytes → LTP   mode  (token + last price only)
//   44  bytes → Quote mode  (OHLCV + buy/sell qty)
//   184 bytes → Full  mode  (Quote + depth + OI + exchange ts)
//
// This parser handles all three modes and maps each tick into the canonical
// `crate::proto::market_data::Tick` Protobuf struct.

use byteorder::{BigEndian, ReadBytesExt};
use std::io::Cursor;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::proto::market_data::Tick;

// ─── Mode thresholds ────────────────────────────────────────────────────────

const MODE_LTP: usize = 8;
const MODE_QUOTE: usize = 44;
const MODE_FULL: usize = 184;

// ─── Depth offset constants (Full mode only) ────────────────────────────────

/// Offset where the 5-level bid depth begins inside a Full-mode packet.
const DEPTH_BID_OFFSET: usize = 84;
/// Offset where the 5-level ask depth begins inside a Full-mode packet.
const DEPTH_ASK_OFFSET: usize = 124;
/// Each depth entry: 4 bytes qty + 4 bytes price + 2 bytes orders = 10 bytes.
const DEPTH_ENTRY_LEN: usize = 10;

/// Offset of the big-endian i32 `open_interest` field inside a Full-mode packet
/// (after the 44-byte quote block + `last_traded_timestamp`).
const OI_OFFSET: usize = 48;

// ─── Public API ─────────────────────────────────────────────────────────────

/// Parse a single Kite binary tick **packet** (already sliced out of the outer
/// frame by the caller) into a Protobuf [`Tick`].
///
/// # Arguments
/// * `payload` — raw bytes of a single tick packet (length = 8, 44, or 184).
/// * `symbol`  — instrument symbol resolved from the instrument_token by the
///               caller's token→symbol map (e.g., `"RELIANCE"`).
///
/// # Returns
/// `Ok(Tick)` populated with all available fields for the given mode, or
/// `Err(String)` if the payload is too short to read required fields.
///
/// # Notes
/// * Prices are stored in paise (integer) by Kite; this function converts to
///   INR by dividing by `100.0`.
/// * `timestamp_ms` is set to the current system wall-clock time in
///   milliseconds (UTC). Full mode will carry the exchange timestamp in the
///   future once the exact offset is verified against live data.
pub fn parse_binary_tick(payload: &[u8], symbol: &str) -> Result<Tick, String> {
    if payload.len() < MODE_LTP {
        return Err(format!(
            "Packet too short: {} bytes (minimum {})",
            payload.len(),
            MODE_LTP
        ));
    }

    let mut cur = Cursor::new(payload);

    // Bytes 0-3: instrument_token (u32 big-endian) — already resolved to
    // `symbol` by the caller, but we read it here so cursor advances correctly.
    let instrument_token = cur
        .read_u32::<BigEndian>()
        .map_err(|e| format!("Failed to read instrument_token: {e}"))?;

    // Bytes 4-7: last_traded_price (i32 paise big-endian)
    let ltp_paise = cur
        .read_i32::<BigEndian>()
        .map_err(|e| format!("Failed to read LTP: {e}"))?;
    let last_traded_price = ltp_paise as f64 / 100.0;

    // Default all optional fields; fill in based on available packet length.
    let mut volume: i32 = 0;
    let mut best_bid: f64 = 0.0;
    let mut best_ask: f64 = 0.0;
    let mut open: f64 = 0.0;
    let mut high: f64 = 0.0;
    let mut low: f64 = 0.0;
    let mut close: f64 = 0.0;
    // Open interest is present only in Full-mode option/future packets. It
    // stays `None` for LTP/Quote packets and on any short/failed read so an
    // absent value is never fabricated as a real `Some(0)` (R2.4).
    let mut open_interest: Option<u64> = None;

    if payload.len() >= MODE_QUOTE {
        // Quote / Full mode — additional OHLCV fields start at byte 8.
        // Skip last_traded_qty (bytes 8-11) and average_price (bytes 12-15).
        cur.read_i32::<BigEndian>()
            .map_err(|e| format!("Failed to read last_traded_qty: {e}"))?;
        cur.read_i32::<BigEndian>()
            .map_err(|e| format!("Failed to read avg_price: {e}"))?;

        // Bytes 16-19: volume (i32)
        volume = cur
            .read_i32::<BigEndian>()
            .map_err(|e| format!("Failed to read volume: {e}"))?;

        // Bytes 20-23: buy_quantity  (top-of-book total bid size)
        let buy_qty = cur
            .read_i32::<BigEndian>()
            .map_err(|e| format!("Failed to read buy_qty: {e}"))?;

        // Bytes 24-27: sell_quantity (top-of-book total ask size)
        let sell_qty = cur
            .read_i32::<BigEndian>()
            .map_err(|e| format!("Failed to read sell_qty: {e}"))?;

        // Use aggregate bid/ask quantities as a proxy until Full-mode depth
        // offsets are confirmed against live data from Kite.
        // In Full mode (below) these are overwritten with actual level-1 prices.
        let _ = buy_qty;
        let _ = sell_qty;

        // Read OHLC prices (bytes 28-43)
        open = cur.read_i32::<BigEndian>().unwrap_or(0) as f64 / 100.0;
        high = cur.read_i32::<BigEndian>().unwrap_or(0) as f64 / 100.0;
        low = cur.read_i32::<BigEndian>().unwrap_or(0) as f64 / 100.0;
        close = cur.read_i32::<BigEndian>().unwrap_or(0) as f64 / 100.0;
    }

    if payload.len() >= MODE_FULL {
        // Full mode — extract best bid/ask from level-1 market depth entries.
        // Level-1 bid is the first entry of the bid side (highest buyer price).
        // Level-1 ask is the first entry of the ask side (lowest seller price).

        if let Some(bid_entry) = payload.get(DEPTH_BID_OFFSET..DEPTH_BID_OFFSET + DEPTH_ENTRY_LEN)
        {
            let mut bid_cur = Cursor::new(bid_entry);
            let _bid_qty = bid_cur.read_i32::<BigEndian>().unwrap_or(0);
            let bid_price_paise = bid_cur.read_i32::<BigEndian>().unwrap_or(0);
            best_bid = bid_price_paise as f64 / 100.0;
        }

        if let Some(ask_entry) = payload.get(DEPTH_ASK_OFFSET..DEPTH_ASK_OFFSET + DEPTH_ENTRY_LEN)
        {
            let mut ask_cur = Cursor::new(ask_entry);
            let _ask_qty = ask_cur.read_i32::<BigEndian>().unwrap_or(0);
            let ask_price_paise = ask_cur.read_i32::<BigEndian>().unwrap_or(0);
            best_ask = ask_price_paise as f64 / 100.0;
        }

        // Bytes 48-51: open_interest (i32 big-endian). Present on full-mode
        // option/future packets; index full packets carry no meaningful OI.
        // Read defensively — a short/failed read leaves `open_interest = None`.
        if let Some(oi_bytes) = payload.get(OI_OFFSET..OI_OFFSET + 4) {
            let mut oi_cur = Cursor::new(oi_bytes);
            if let Ok(oi_value) = oi_cur.read_i32::<BigEndian>() {
                open_interest = Some(oi_value as u64);
            }
        }
    }

    // Timestamp: system wall-clock in UTC milliseconds.
    // Will be replaced by the exchange timestamp from Full-mode packets once
    // byte offsets are confirmed against live production data.
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);

    Ok(Tick {
        symbol: symbol.to_string(),
        timestamp_ms,
        last_traded_price,
        volume,
        best_bid,
        best_ask,
        instrument_token,
        open,
        high,
        low,
        close,
        open_interest,
    })
}

/// Convenience function: parse an entire Kite **frame** (which may contain
/// multiple tick packets) and return a `Vec<Tick>`.
///
/// The frame begins with a 2-byte packet count, followed by alternating
/// 2-byte length + N-byte data sections.
///
/// `symbol_map` maps `instrument_token → symbol` string.
pub fn parse_binary_frame(
    frame: &[u8],
    symbol_map: &std::collections::HashMap<u32, String>,
) -> Vec<Tick> {
    if frame.len() < 2 {
        return vec![];
    }

    let mut cur = Cursor::new(frame);

    let num_packets = match cur.read_u16::<BigEndian>() {
        Ok(n) => n as usize,
        Err(_) => return vec![],
    };

    let mut ticks = Vec::with_capacity(num_packets);

    for _ in 0..num_packets {
        // Read the 2-byte packet length prefix.
        let pkt_len = match cur.read_u16::<BigEndian>() {
            Ok(l) => l as usize,
            Err(_) => break,
        };

        let offset = cur.position() as usize;
        let end = offset + pkt_len;

        if end > frame.len() {
            break; // malformed frame — bail out
        }

        let packet = &frame[offset..end];
        cur.set_position(end as u64);

        // Resolve instrument_token → symbol for the map lookup.
        if packet.len() >= 4 {
            let token = u32::from_be_bytes([packet[0], packet[1], packet[2], packet[3]]);
            let symbol = symbol_map
                .get(&token)
                .map(|s| s.as_str())
                .unwrap_or_else(|| "UNKNOWN");

            match parse_binary_tick(packet, symbol) {
                Ok(tick) => ticks.push(tick),
                Err(e) => log::warn!("Failed to parse packet for token {token}: {e}"),
            }
        }
    }

    ticks
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_ltp_packet(token: u32, price_paise: i32) -> Vec<u8> {
        let mut buf = Vec::with_capacity(8);
        buf.extend_from_slice(&token.to_be_bytes());
        buf.extend_from_slice(&price_paise.to_be_bytes());
        buf
    }

    #[test]
    fn test_ltp_mode_parsing() {
        let packet = make_ltp_packet(738_561, 245_050); // RELIANCE @ ₹2450.50
        let tick = parse_binary_tick(&packet, "RELIANCE").unwrap();
        assert_eq!(tick.symbol, "RELIANCE");
        assert!((tick.last_traded_price - 2450.50).abs() < 0.01);
        assert_eq!(tick.volume, 0);
        assert!(tick.timestamp_ms > 0);
    }

    #[test]
    fn test_packet_too_short() {
        let result = parse_binary_tick(&[0x00, 0x01], "TEST");
        assert!(result.is_err());
    }

    #[test]
    fn test_frame_with_no_packets() {
        let frame = vec![0x00, 0x00]; // 0 packets
        let map = std::collections::HashMap::new();
        let ticks = parse_binary_frame(&frame, &map);
        assert!(ticks.is_empty());
    }
}

// ─── Property-based tests ────────────────────────────────────────────────────

#[cfg(test)]
mod oi_presence_property_tests {
    use super::*;
    use proptest::prelude::*;

    /// Build a synthetic packet for the requested mode.
    ///
    /// * mode 0 → LTP   (8 bytes)              — no OI
    /// * mode 1 → Quote (44 bytes)             — no OI
    /// * mode 2 → truncated full (45..183)     — no OI (full branch never runs)
    /// * mode 3 → Full  (184 bytes), OI at off 48 = `oi`
    fn build_packet(mode: u8, full_bytes: &[u8], trunc_len: usize, oi: i32) -> Vec<u8> {
        match mode {
            0 => full_bytes[..MODE_LTP].to_vec(),
            1 => full_bytes[..MODE_QUOTE].to_vec(),
            2 => full_bytes[..trunc_len].to_vec(),
            _ => {
                let mut buf = full_bytes[..MODE_FULL].to_vec();
                buf[OI_OFFSET..OI_OFFSET + 4].copy_from_slice(&oi.to_be_bytes());
                buf
            }
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: options-data-foundation, Property 3
        // Open-interest presence semantics: parsed `open_interest` is `Some(value)`
        // if and only if the packet is full mode and carries the OI field; LTP-mode
        // and Quote-mode packets, and any truncated/failed read, yield `None` — never
        // a fabricated `Some(0)` indistinguishable from a real zero. For full-mode
        // packets the `Some` value equals the big-endian i32 at offset 48 (as u64).
        // Validates: Requirements 2.1, 2.4
        #[test]
        fn prop_open_interest_presence_semantics(
            mode in 0u8..4u8,
            oi in any::<i32>(),
            trunc_len in (MODE_QUOTE + 1)..MODE_FULL,
            full_bytes in proptest::collection::vec(any::<u8>(), MODE_FULL),
        ) {
            let packet = build_packet(mode, &full_bytes, trunc_len, oi);
            let tick = parse_binary_tick(&packet, "TEST")
                .expect("synthetic packet (>= LTP length) must parse");

            if mode == 3 {
                // Full mode carries OI: present and equal to the written i32 (as u64).
                prop_assert_eq!(tick.open_interest, Some(oi as u64));
            } else {
                // LTP / Quote / truncated: OI must be absent, never a fabricated Some(0).
                prop_assert_eq!(tick.open_interest, None);
            }
        }
    }
}

// ─── Equity-tick backward-compatibility tests ────────────────────────────────

#[cfg(test)]
mod equity_backward_compat_tests {
    use super::*;

    /// Build a representative Quote-mode (44-byte) equity packet using the exact
    /// byte offsets the parser reads, so the parsed fields can be checked against
    /// the known input values.
    ///
    /// Layout (all big-endian, prices in paise):
    ///   0-3   instrument_token (u32)
    ///   4-7   last_traded_price (i32 paise)
    ///   8-11  last_traded_qty   (i32, skipped by parser)
    ///   12-15 average_price     (i32, skipped by parser)
    ///   16-19 volume            (i32)
    ///   20-23 buy_quantity      (i32)
    ///   24-27 sell_quantity     (i32)
    ///   28-31 open  (i32 paise)
    ///   32-35 high  (i32 paise)
    ///   36-39 low   (i32 paise)
    ///   40-43 close (i32 paise)
    #[allow(clippy::too_many_arguments)]
    fn make_quote_packet(
        token: u32,
        ltp_paise: i32,
        last_qty: i32,
        avg_paise: i32,
        volume: i32,
        buy_qty: i32,
        sell_qty: i32,
        open_paise: i32,
        high_paise: i32,
        low_paise: i32,
        close_paise: i32,
    ) -> Vec<u8> {
        let mut buf = Vec::with_capacity(MODE_QUOTE);
        buf.extend_from_slice(&token.to_be_bytes());
        buf.extend_from_slice(&ltp_paise.to_be_bytes());
        buf.extend_from_slice(&last_qty.to_be_bytes());
        buf.extend_from_slice(&avg_paise.to_be_bytes());
        buf.extend_from_slice(&volume.to_be_bytes());
        buf.extend_from_slice(&buy_qty.to_be_bytes());
        buf.extend_from_slice(&sell_qty.to_be_bytes());
        buf.extend_from_slice(&open_paise.to_be_bytes());
        buf.extend_from_slice(&high_paise.to_be_bytes());
        buf.extend_from_slice(&low_paise.to_be_bytes());
        buf.extend_from_slice(&close_paise.to_be_bytes());
        debug_assert_eq!(buf.len(), MODE_QUOTE);
        buf
    }

    /// R2.5 — An existing equity Quote-mode (44-byte) packet parses to identical
    /// prior field values (token, last price, volume, OHLC) with the new
    /// `open_interest` field absent (`None`) and best bid/ask unset in Quote mode.
    #[test]
    fn test_equity_quote_packet_backward_compatible() {
        // INFY @ ₹1500.25, volume 12 345, OHLC = 1490.00 / 1510.50 / 1485.75 / 1495.00
        let token = 408_065_u32;
        let packet = make_quote_packet(
            token,
            150_025, // ltp paise → 1500.25
            10,      // last_traded_qty (skipped)
            149_900, // avg_price paise (skipped)
            12_345,  // volume
            500,     // buy_qty (not surfaced in Quote mode)
            450,     // sell_qty (not surfaced in Quote mode)
            149_000, // open paise → 1490.00
            151_050, // high paise → 1510.50
            148_575, // low paise → 1485.75
            149_500, // close paise → 1495.00
        );

        let tick = parse_binary_tick(&packet, "INFY").expect("Quote-mode packet must parse");

        // Identical prior field values.
        assert_eq!(tick.symbol, "INFY");
        assert_eq!(tick.instrument_token, token);
        assert!((tick.last_traded_price - 1500.25).abs() < 1e-9);
        assert_eq!(tick.volume, 12_345);
        assert!((tick.open - 1490.00).abs() < 1e-9);
        assert!((tick.high - 1510.50).abs() < 1e-9);
        assert!((tick.low - 1485.75).abs() < 1e-9);
        assert!((tick.close - 1495.00).abs() < 1e-9);

        // Best bid/ask are only populated from Full-mode depth; in Quote mode
        // they remain at their prior default of 0.0.
        assert!((tick.best_bid - 0.0).abs() < 1e-9);
        assert!((tick.best_ask - 0.0).abs() < 1e-9);

        // A timestamp is still stamped as before.
        assert!(tick.timestamp_ms > 0);

        // The new open-interest field is absent for an equity Quote packet —
        // never fabricated as Some(0) (R2.4, R2.5).
        assert_eq!(tick.open_interest, None);
    }
}
