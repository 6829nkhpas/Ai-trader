/// QuestDB ILP writer — Subphase 15
///
/// Writes tick data to QuestDB using the InfluxDB Line Protocol (ILP) over a
/// persistent TCP connection to port 9009. ILP is QuestDB's highest-throughput
/// ingest path — it bypasses the SQL engine and appends directly to the WAL.
///
/// ILP line format:
///   <measurement>,<tags> <fields> <unix_timestamp_nanos>
///
/// Example:
///   market_data,symbol=RELIANCE ltp=2500.0,volume=12345i,bid=2499.5,ask=2500.5 1714000000000000000
///
/// QuestDB auto-creates the `market_data` table on first write. Schema:
///   symbol   (SYMBOL)  — indexed tag
///   ltp      (DOUBLE)
///   volume   (LONG)
///   bid      (DOUBLE)
///   ask      (DOUBLE)
///   open     (DOUBLE)
///   high     (DOUBLE)
///   low      (DOUBLE)
///   close    (DOUBLE)
///   timestamp (TIMESTAMP) — QuestDB designated timestamp, set from ILP nanos

use log::{error, info, warn};
use tokio::io::AsyncWriteExt;
use tokio::net::TcpStream;

use crate::types::ParsedTick;

/// Manages a persistent async TCP connection to QuestDB's ILP port (9009).
pub struct QuestDbWriter {
    stream: TcpStream,
    addr: String,
}

impl QuestDbWriter {
    /// Connect to QuestDB ILP endpoint.
    ///
    /// Reads `QUESTDB_ILP_ADDR` (default: `127.0.0.1:9009`).
    pub async fn connect() -> Result<Self, Box<dyn std::error::Error>> {
        let addr = std::env::var("QUESTDB_ILP_ADDR")
            .unwrap_or_else(|_| "127.0.0.1:9009".to_string());

        let stream = TcpStream::connect(&addr).await?;
        // Disable Nagle — we want every write to flush immediately for low latency
        stream.set_nodelay(true)?;

        info!("QuestDB ILP writer connected → {}", addr);
        Ok(Self { stream, addr })
    }

    /// Reconnect after a broken pipe or timeout.
    async fn reconnect(&mut self) {
        warn!("QuestDB connection lost — reconnecting to {}...", self.addr);
        loop {
            match TcpStream::connect(&self.addr).await {
                Ok(stream) => {
                    let _ = stream.set_nodelay(true);
                    self.stream = stream;
                    info!("QuestDB reconnected ✓");
                    return;
                }
                Err(e) => {
                    error!("QuestDB reconnect failed: {}. Retrying in 3s...", e);
                    tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
                }
            }
        }
    }

    /// Write a single tick as an ILP line to QuestDB.
    ///
    /// Timestamp is sent as nanoseconds (QuestDB ILP requirement).
    /// All field names match the planned `market_data` table schema.
    pub async fn write_tick(&mut self, tick: &ParsedTick) {
        // Escape commas and spaces in the symbol tag value per ILP spec
        let escaped_symbol = tick.symbol.replace(',', "\\,").replace(' ', "\\ ");

        // ILP nanosecond timestamp
        let ts_nanos = tick.timestamp_ms * 1_000_000i64;

        // Build the ILP line — integer fields use the `i` suffix
        let line = format!(
            "market_data,symbol={sym} ltp={ltp},volume={vol}i,bid={bid},ask={ask},open={open},high={high},low={low},close={close} {ts}\n",
            sym   = escaped_symbol,
            ltp   = tick.last_price,
            vol   = tick.volume,
            bid   = tick.best_bid,
            ask   = tick.best_ask,
            open  = tick.open,
            high  = tick.high,
            low   = tick.low,
            close = tick.close,
            ts    = ts_nanos,
        );

        if let Err(e) = self.stream.write_all(line.as_bytes()).await {
            error!("QuestDB write error: {}", e);
            self.reconnect().await;
        }
    }
}
