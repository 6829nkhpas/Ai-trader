// services/instrument_master.rs — Kite Instrument Master (Daily CSV Downloader)
//
// Downloads the full NSE instrument list from https://api.kite.trade/instruments/NSE,
// parses the CSV, and stores instrument_token, tradingsymbol, and name into the
// local workspace SQLite database. Cached daily — only re-downloads if the
// instruments table is empty or the last download was >24h ago.
//
// Runs non-blocking on Tauri startup via `spawn_instrument_sync()`.

use log::{info, warn, error};
use rusqlite::params;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::db::DbState;

const KITE_INSTRUMENTS_URL: &str = "https://api.kite.trade/instruments/NSE";

/// Spawn the instrument sync task on Tauri startup.
/// Non-blocking — runs in a background tokio task.
/// NOTE: This is a placeholder — the actual sync is triggered from lib.rs setup.
#[allow(dead_code)]
pub fn spawn_instrument_sync(_db_state: tauri::State<'_, DbState>) {
    // Intentionally empty — sync is driven from lib.rs setup hook via run_instrument_sync().
}

/// Run the full instrument sync pipeline.
/// Called from the Tauri setup hook with access to the app handle.
pub async fn run_instrument_sync(app: tauri::AppHandle) {
    use tauri::Manager;

    let db_state: tauri::State<'_, DbState> = app.state();

    // Step 1: Check if we need to download (table exists + has recent data)
    let needs_download = {
        let conn = match db_state.conn.lock() {
            Ok(c) => c,
            Err(e) => {
                error!("[InstrumentMaster] DB lock failed: {}", e);
                return;
            }
        };

        // Create the instruments table if it doesn't exist
        if let Err(e) = conn.execute(
            "CREATE TABLE IF NOT EXISTS instruments (
                instrument_token INTEGER PRIMARY KEY,
                tradingsymbol    TEXT NOT NULL,
                name             TEXT NOT NULL DEFAULT '',
                instrument_type  TEXT NOT NULL DEFAULT 'EQ',
                exchange         TEXT NOT NULL DEFAULT 'NSE',
                last_updated     INTEGER NOT NULL DEFAULT 0
            );",
            [],
        ) {
            error!("[InstrumentMaster] Failed to create instruments table: {}", e);
            return;
        }

        // Create index for fast LIKE searches
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments(tradingsymbol);",
            [],
        ).ok();

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instruments_name ON instruments(name);",
            [],
        ).ok();

        // Check row count and freshness
        let row_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM instruments;", [], |row| row.get(0))
            .unwrap_or(0);

        if row_count == 0 {
            true
        } else {
            // Check if last_updated is older than 24 hours
            let last_updated: i64 = conn
                .query_row(
                    "SELECT MAX(last_updated) FROM instruments;",
                    [],
                    |row| row.get(0),
                )
                .unwrap_or(0);

            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);

            let age_hours = (now - last_updated) / 3600;
            if age_hours >= 24 {
                info!(
                    "[InstrumentMaster] Cache is {}h old (>{} rows). Re-downloading.",
                    age_hours, row_count
                );
                true
            } else {
                info!(
                    "[InstrumentMaster] Cache fresh ({}h old, {} instruments). Skipping download.",
                    age_hours, row_count
                );
                false
            }
        }
    };

    if !needs_download {
        return;
    }

    // Step 2: Download the CSV from Kite
    info!("[InstrumentMaster] Downloading NSE instruments from Kite...");

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap_or_default();

    let csv_text = match client.get(KITE_INSTRUMENTS_URL).send().await {
        Ok(resp) if resp.status().is_success() => match resp.text().await {
            Ok(text) => text,
            Err(e) => {
                error!("[InstrumentMaster] Failed to read response body: {}", e);
                return;
            }
        },
        Ok(resp) => {
            warn!(
                "[InstrumentMaster] Kite API returned HTTP {}. Will retry next boot.",
                resp.status()
            );
            return;
        }
        Err(e) => {
            warn!("[InstrumentMaster] HTTP request failed: {}. Will retry next boot.", e);
            return;
        }
    };

    // Step 3: Parse CSV and insert into SQLite
    // Kite CSV format (header line):
    // instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
    let lines: Vec<&str> = csv_text.lines().collect();
    if lines.len() < 2 {
        warn!("[InstrumentMaster] CSV appears empty or malformed ({} lines)", lines.len());
        return;
    }

    // Parse header to find column indices
    let header = lines[0];
    let columns: Vec<&str> = header.split(',').collect();
    let token_idx = columns.iter().position(|&c| c == "instrument_token");
    let symbol_idx = columns.iter().position(|&c| c == "tradingsymbol");
    let name_idx = columns.iter().position(|&c| c == "name");
    let type_idx = columns.iter().position(|&c| c == "instrument_type");
    let exchange_idx = columns.iter().position(|&c| c == "exchange");

    let (token_idx, symbol_idx, name_idx) = match (token_idx, symbol_idx, name_idx) {
        (Some(t), Some(s), Some(n)) => (t, s, n),
        _ => {
            error!(
                "[InstrumentMaster] CSV header missing required columns. Header: {}",
                header
            );
            return;
        }
    };

    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);

    let mut inserted = 0u32;
    let mut skipped = 0u32;

    {
        let conn = match db_state.conn.lock() {
            Ok(c) => c,
            Err(e) => {
                error!("[InstrumentMaster] DB lock failed during insert: {}", e);
                return;
            }
        };

        // Use a transaction for bulk insert performance
        if let Err(e) = conn.execute("BEGIN TRANSACTION;", []) {
            error!("[InstrumentMaster] Failed to begin transaction: {}", e);
            return;
        }

        // Clear old data before re-inserting
        conn.execute("DELETE FROM instruments;", []).ok();

        for line in &lines[1..] {
            let fields: Vec<&str> = line.split(',').collect();
            if fields.len() <= token_idx || fields.len() <= symbol_idx || fields.len() <= name_idx {
                skipped += 1;
                continue;
            }

            let token: i64 = match fields[token_idx].parse() {
                Ok(t) => t,
                Err(_) => { skipped += 1; continue; }
            };

            let tradingsymbol = fields[symbol_idx].trim();
            let name = fields[name_idx].trim();
            let instrument_type = type_idx
                .and_then(|i| fields.get(i))
                .map(|s| s.trim())
                .unwrap_or("EQ");
            let exchange = exchange_idx
                .and_then(|i| fields.get(i))
                .map(|s| s.trim())
                .unwrap_or("NSE");

            // Skip empty symbols
            if tradingsymbol.is_empty() {
                skipped += 1;
                continue;
            }

            match conn.execute(
                "INSERT OR REPLACE INTO instruments (instrument_token, tradingsymbol, name, instrument_type, exchange, last_updated)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6);",
                params![token, tradingsymbol, name, instrument_type, exchange, now_secs],
            ) {
                Ok(_) => inserted += 1,
                Err(e) => {
                    if skipped < 5 {
                        warn!("[InstrumentMaster] Insert failed for {}: {}", tradingsymbol, e);
                    }
                    skipped += 1;
                }
            }
        }

        if let Err(e) = conn.execute("COMMIT;", []) {
            error!("[InstrumentMaster] Failed to commit transaction: {}", e);
            return;
        }
    }

    info!(
        "[InstrumentMaster] ✓ Synced {} instruments ({} skipped) from Kite NSE CSV.",
        inserted, skipped
    );
}
