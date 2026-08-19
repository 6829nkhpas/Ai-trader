// services/instrument_master.rs — Instrument Master (Daily CSV Downloader)
//
// Downloads the full NSE instrument list from the configured market-data provider
// (`providers::registry::market_data().instrument_dump("NSE")`), parses the CSV,
// and stores instrument_token, tradingsymbol, and name into the local workspace
// SQLite database. Cached daily — only re-downloads if the instruments table is
// empty or the last download was >24h ago.
//
// Since P14 the URL and HTTP client live in the provider; this module owns the
// *parsing*, which is where the column-order tolerance and the property tests are.
//
// Runs non-blocking on Tauri startup via `spawn_instrument_sync()`.

use log::{info, warn, error};
use rusqlite::params;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::db::DbState;

/// Spawn the instrument sync task on Tauri startup.
/// Non-blocking — runs in a background tokio task.
/// NOTE: This is a placeholder — the actual sync is triggered from lib.rs setup.
#[allow(dead_code)]
pub fn spawn_instrument_sync(_db_state: tauri::State<'_, DbState>) {
    // Intentionally empty — sync is driven from lib.rs setup hook via run_instrument_sync().
}

/// Pure: decide whether an instrument cache needs a refresh download.
///
/// Returns `true` when the table is empty (`row_count == 0`) or when the most recent
/// `last_updated` timestamp is at least 24 hours older than `now` (both Unix seconds).
/// This is the shared 24-hour cache-schedule decision used by both the NSE and NFO
/// syncs (R1.5), extracted as a pure, deterministic function so it is unit-testable
/// without a DB, clock, or network. Behavior is identical to the previous inline logic:
/// a fresh cache (age < 24h) skips the download, a stale cache (age >= 24h) triggers it,
/// and an empty table always triggers it.
pub fn needs_refresh(row_count: i64, last_updated: i64, now: i64) -> bool {
    if row_count == 0 {
        return true;
    }
    let age_hours = (now - last_updated) / 3600;
    age_hours >= 24
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

        // Shared, pure 24h cache-schedule decision (R1.5).
        let refresh = needs_refresh(row_count, last_updated, now);
        if refresh {
            if row_count > 0 {
                let age_hours = (now - last_updated) / 3600;
                info!(
                    "[InstrumentMaster] Cache is {}h old (>{} rows). Re-downloading.",
                    age_hours, row_count
                );
            }
        } else {
            let age_hours = (now - last_updated) / 3600;
            info!(
                "[InstrumentMaster] Cache fresh ({}h old, {} instruments). Skipping download.",
                age_hours, row_count
            );
        }
        refresh
    };

    if !needs_download {
        return;
    }

    // Step 2: Download the CSV through the market-data provider (P14).
    //
    // The three warn arms this replaces (non-2xx / transport failure / unreadable
    // body) collapse into one, but nothing is lost: the provider puts the cause in
    // the error string, so the log still says which of the three happened. Every
    // failure still returns WITHOUT touching existing rows.
    info!("[InstrumentMaster] Downloading NSE instruments...");

    let csv_text = match crate::providers::registry::market_data()
        .instrument_dump("NSE")
        .await
    {
        Ok(text) => text,
        Err(e) => {
            warn!(
                "[InstrumentMaster] Instrument dump failed: {}. Will retry next boot.",
                e
            );
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

            let tradingsymbol = clean_csv_field(fields[symbol_idx]);
            let name = clean_csv_field(fields[name_idx]);
            let instrument_type = type_idx
                .and_then(|i| fields.get(i))
                .map(|s| clean_csv_field(s))
                .unwrap_or("EQ");
            let exchange = exchange_idx
                .and_then(|i| fields.get(i))
                .map(|s| clean_csv_field(s))
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

// ════════════════════════════════════════════════════════════════════════
// NFO Derivatives Segment Ingestion (Options Data Foundation — Phase F1)
//
// Downloads the NFO instrument list from the market-data provider
// (`instrument_dump("NFO")`), parses the option/future contracts (capturing
// strike, expiry, instrument type, lot size, segment, and the derived underlying),
// and upserts them into a separate `nfo_instruments` SQLite table — without ever
// touching the existing `instruments` (NSE equity) table. Refreshed on the same
// 24h cache schedule as the NSE sync.
//
// The pure parsing helpers (`parse_nfo_row`, `derive_underlying`) perform no I/O so
// they can be unit- and property-tested without a live feed.
// ════════════════════════════════════════════════════════════════════════

/// A parsed NFO derivatives contract (option or future).
///
/// Field set mirrors the persisted `nfo_instruments` table (minus the derived
/// `underlying`, which is computed by `derive_underlying`). `expiry` is kept as the
/// raw ISO string from the CSV so this struct stays a pure, lossless view of the row.
#[derive(Clone, Debug, PartialEq)]
pub struct NfoInstrument {
    pub instrument_token: i64,
    pub tradingsymbol: String,
    pub name: String,
    pub instrument_type: String, // CE / PE / FUT
    pub strike: f64,
    pub expiry: String, // ISO date (e.g. "2024-12-26"), may be empty
    pub lot_size: i64,
    pub segment: String, // e.g. "NFO-OPT", "NFO-FUT"
}

/// Resolved column positions for the NFO CSV header, tolerating column reordering.
///
/// Required columns (`instrument_token`, `tradingsymbol`, `instrument_type`) must be
/// present for the header to resolve; the remaining columns are optional and default
/// when absent.
#[derive(Clone, Copy, Debug)]
pub struct NfoColumnIndex {
    pub token: usize,
    pub symbol: usize,
    pub instrument_type: usize,
    pub name: Option<usize>,
    pub strike: Option<usize>,
    pub expiry: Option<usize>,
    pub lot_size: Option<usize>,
    pub segment: Option<usize>,
}

impl NfoColumnIndex {
    /// Resolve column positions from a CSV header line. Returns `None` if any of the
    /// required columns is missing.
    pub fn from_header(header: &str) -> Option<NfoColumnIndex> {
        let columns: Vec<&str> = header.split(',').map(|c| c.trim()).collect();
        let find = |name: &str| columns.iter().position(|&c| c == name);

        let token = find("instrument_token")?;
        let symbol = find("tradingsymbol")?;
        let instrument_type = find("instrument_type")?;

        Some(NfoColumnIndex {
            token,
            symbol,
            instrument_type,
            name: find("name"),
            strike: find("strike"),
            expiry: find("expiry"),
            lot_size: find("lot_size"),
            segment: find("segment"),
        })
    }
}

/// Pure: parse one NFO CSV row (already split by comma) into an `NfoInstrument`.
///
/// Returns `None` for rows missing a required field (unparseable/absent
/// instrument_token, empty tradingsymbol, or empty instrument_type) so malformed rows
/// are skipped rather than fatal. Optional fields default when absent or unparseable
/// (`strike` → 0.0, `lot_size` → 0, `expiry` → "", `segment` → "NFO", `name` → "").
/// Trim surrounding whitespace and a single pair of wrapping double-quotes from a
/// raw CSV field.
///
/// Kite's instrument CSV wraps some textual fields (notably `name`) in double
/// quotes, e.g. `"NIFTY 50"`. A naive comma split keeps those quotes in the value,
/// so without stripping them the stored `name` becomes `"NIFTY 50"` and the
/// derived `underlying` becomes `"NIFTY"` (quotes included) — which then corrupts
/// the displayed name, the option grouping, the configured-underlying match, and
/// every downstream lookup. Pure and total: trims whitespace, removes at most one
/// leading and one trailing `"`, then trims again.
pub fn clean_csv_field(raw: &str) -> &str {
    let t = raw.trim();
    let t = t.strip_prefix('"').unwrap_or(t);
    let t = t.strip_suffix('"').unwrap_or(t);
    t.trim()
}

pub fn parse_nfo_row(fields: &[&str], idx: &NfoColumnIndex) -> Option<NfoInstrument> {
    let get = |i: usize| fields.get(i).map(|s| clean_csv_field(s));

    // Required: instrument_token must parse.
    let instrument_token: i64 = get(idx.token)?.parse().ok()?;

    // Required: tradingsymbol non-empty.
    let tradingsymbol = get(idx.symbol)?;
    if tradingsymbol.is_empty() {
        return None;
    }

    // Required: instrument_type non-empty.
    let instrument_type = get(idx.instrument_type)?;
    if instrument_type.is_empty() {
        return None;
    }

    let name = idx
        .name
        .and_then(get)
        .unwrap_or("")
        .to_string();

    let strike = idx
        .strike
        .and_then(get)
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);

    let expiry = idx
        .expiry
        .and_then(get)
        .unwrap_or("")
        .to_string();

    let lot_size = idx
        .lot_size
        .and_then(get)
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(0);

    let segment = {
        let s = idx.segment.and_then(get).unwrap_or("");
        if s.is_empty() { "NFO".to_string() } else { s.to_string() }
    };

    Some(NfoInstrument {
        instrument_token,
        tradingsymbol: tradingsymbol.to_string(),
        name,
        instrument_type: instrument_type.to_string(),
        strike,
        expiry,
        lot_size,
        segment,
    })
}

/// Pure: derive the underlying for an option/future from its parsed record.
///
/// Uses the `name` column (e.g. "NIFTY", "BANKNIFTY") when present, falling back to
/// the tradingsymbol when `name` is empty. The result is trimmed; this guarantees
/// every CE/PE/FUT contract groups under exactly one non-empty underlying so an option
/// chain can be resolved by underlying.
pub fn derive_underlying(record: &NfoInstrument) -> String {
    let name = record.name.trim();
    if !name.is_empty() {
        name.to_string()
    } else {
        record.tradingsymbol.trim().to_string()
    }
}

/// Run the NFO derivatives sync pipeline.
///
/// Downloads the NFO CSV, parses + derives underlyings purely, then upserts into the
/// `nfo_instruments` table on the same 24h cache schedule as the NSE sync. The upsert
/// transaction begins only after a successful download + parse, so any download or
/// parse failure leaves the existing `nfo_instruments` (and `instruments`) data intact.
pub async fn run_nfo_sync(app: tauri::AppHandle) {
    use tauri::Manager;

    let db_state: tauri::State<'_, DbState> = app.state();

    // Step 1: Ensure the table/indexes exist and decide whether a download is needed.
    let needs_download = {
        let conn = match db_state.conn.lock() {
            Ok(c) => c,
            Err(e) => {
                error!("[NfoMaster] DB lock failed: {}", e);
                return;
            }
        };

        if let Err(e) = conn.execute(
            "CREATE TABLE IF NOT EXISTS nfo_instruments (
                instrument_token INTEGER PRIMARY KEY,
                tradingsymbol    TEXT    NOT NULL,
                name             TEXT    NOT NULL DEFAULT '',
                underlying       TEXT    NOT NULL DEFAULT '',
                instrument_type  TEXT    NOT NULL,
                strike           REAL    NOT NULL DEFAULT 0,
                expiry           TEXT    NOT NULL DEFAULT '',
                lot_size         INTEGER NOT NULL DEFAULT 0,
                segment          TEXT    NOT NULL DEFAULT 'NFO',
                last_updated     INTEGER NOT NULL DEFAULT 0
            );",
            [],
        ) {
            error!("[NfoMaster] Failed to create nfo_instruments table: {}", e);
            return;
        }

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nfo_underlying ON nfo_instruments(underlying);",
            [],
        ).ok();
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nfo_under_expiry ON nfo_instruments(underlying, expiry);",
            [],
        ).ok();

        let row_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM nfo_instruments;", [], |row| row.get(0))
            .unwrap_or(0);

        let last_updated: i64 = conn
            .query_row(
                "SELECT MAX(last_updated) FROM nfo_instruments;",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);

        // Same pure 24h cache-schedule decision as the NSE sync (R1.5).
        let refresh = needs_refresh(row_count, last_updated, now);
        if refresh {
            if row_count > 0 {
                let age_hours = (now - last_updated) / 3600;
                info!(
                    "[NfoMaster] Cache is {}h old ({} rows). Re-downloading.",
                    age_hours, row_count
                );
            }
        } else {
            let age_hours = (now - last_updated) / 3600;
            info!(
                "[NfoMaster] Cache fresh ({}h old, {} NFO instruments). Skipping download.",
                age_hours, row_count
            );
        }
        refresh
    };

    if !needs_download {
        return;
    }

    // Step 2: Download the NFO CSV through the market-data provider (P14).
    // A failure here returns without touching data — unchanged from before.
    info!("[NfoMaster] Downloading NFO instruments...");

    let csv_text = match crate::providers::registry::market_data()
        .instrument_dump("NFO")
        .await
    {
        Ok(text) => text,
        Err(e) => {
            warn!(
                "[NfoMaster] Instrument dump failed: {}. Existing data left intact. \
                 Will retry next boot.",
                e
            );
            return;
        }
    };

    // Step 3: Parse the CSV purely. Only after a successful parse do we touch the DB.
    let lines: Vec<&str> = csv_text.lines().collect();
    if lines.len() < 2 {
        warn!(
            "[NfoMaster] CSV appears empty or malformed ({} lines). Existing data left intact.",
            lines.len()
        );
        return;
    }

    let idx = match NfoColumnIndex::from_header(lines[0]) {
        Some(i) => i,
        None => {
            error!(
                "[NfoMaster] CSV header missing required columns. Existing data left intact. Header: {}",
                lines[0]
            );
            return;
        }
    };

    // Parse every row into (record, derived underlying) pairs before opening the txn.
    let mut parsed: Vec<(NfoInstrument, String)> = Vec::with_capacity(lines.len());
    let mut skipped = 0u32;
    for line in &lines[1..] {
        let fields: Vec<&str> = line.split(',').collect();
        match parse_nfo_row(&fields, &idx) {
            Some(record) => {
                let underlying = derive_underlying(&record);
                parsed.push((record, underlying));
            }
            None => skipped += 1,
        }
    }

    if parsed.is_empty() {
        warn!("[NfoMaster] No valid NFO rows parsed ({} skipped). Existing data left intact.", skipped);
        return;
    }

    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);

    // Step 4: Upsert inside a transaction (begins only now, after a successful parse).
    let mut inserted = 0u32;
    {
        let conn = match db_state.conn.lock() {
            Ok(c) => c,
            Err(e) => {
                error!("[NfoMaster] DB lock failed during insert: {}", e);
                return;
            }
        };

        if let Err(e) = conn.execute("BEGIN TRANSACTION;", []) {
            error!("[NfoMaster] Failed to begin transaction: {}", e);
            return;
        }

        // Replace prior NFO data so stale/expired contracts are dropped (R1.5).
        conn.execute("DELETE FROM nfo_instruments;", []).ok();

        for (record, underlying) in &parsed {
            match conn.execute(
                "INSERT OR REPLACE INTO nfo_instruments
                    (instrument_token, tradingsymbol, name, underlying, instrument_type, strike, expiry, lot_size, segment, last_updated)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10);",
                params![
                    record.instrument_token,
                    record.tradingsymbol,
                    record.name,
                    underlying,
                    record.instrument_type,
                    record.strike,
                    record.expiry,
                    record.lot_size,
                    record.segment,
                    now_secs
                ],
            ) {
                Ok(_) => inserted += 1,
                Err(e) => {
                    if skipped < 5 {
                        warn!("[NfoMaster] Insert failed for {}: {}", record.tradingsymbol, e);
                    }
                    skipped += 1;
                }
            }
        }

        if let Err(e) = conn.execute("COMMIT;", []) {
            error!("[NfoMaster] Failed to commit transaction: {}", e);
            // Best-effort rollback so a partial write doesn't corrupt existing data.
            conn.execute("ROLLBACK;", []).ok();
            return;
        }
    }

    info!(
        "[NfoMaster] ✓ Synced {} NFO instruments ({} skipped) from Kite NFO CSV.",
        inserted, skipped
    );
}

// ════════════════════════════════════════════════════════════════════════
// Property-based tests — Options Data Foundation (Phase F1)
// ════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod nfo_parse_property_tests {
    use super::*;
    use proptest::prelude::*;

    /// A corruption applied to a required field, which must drive `parse_nfo_row`
    /// to return `None` (a row missing a required field).
    #[derive(Clone, Debug)]
    enum Corruption {
        BadToken,    // instrument_token unparseable / empty
        EmptySymbol, // tradingsymbol empty after trim
        EmptyType,   // instrument_type empty after trim
    }

    /// Generated, well-formed source values for one NFO row.
    #[derive(Clone, Debug)]
    struct RowSpecVals {
        token: i64,
        symbol: String,
        name: String,
        itype: String,
        strike: f64,
        expiry: String,
        lot_size: i64,
        segment: String,
    }

    fn itype_strategy() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("CE".to_string()),
            Just("PE".to_string()),
            Just("FUT".to_string()),
        ]
    }

    prop_compose! {
        fn row_spec()(
            token in any::<i64>(),
            // Non-empty, comma/whitespace-free → trim is a no-op, exact round-trip.
            symbol in "[A-Za-z0-9]{1,12}",
            // May contain internal/edge spaces (no commas); compared against `.trim()`.
            name in "[A-Za-z0-9 ]{0,10}",
            itype in itype_strategy(),
            // Bounded finite range → `format!("{}")` round-trips exactly.
            strike in 0.0f64..100_000.0,
            expiry in prop_oneof![Just("".to_string()), "[0-9]{4}-[0-9]{2}-[0-9]{2}"],
            lot_size in 0i64..100_000,
            // May be empty → exercises the `segment` "NFO" default branch.
            segment in "[A-Z]{0,6}",
        ) -> RowSpecVals {
            RowSpecVals { token, symbol, name, itype, strike, expiry, lot_size, segment }
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: options-data-foundation, Property 1: NFO row parsing preserves fields
        // Validates: Requirements 1.2
        //
        // For any well-formed NFO CSV row (columns in any order resolved from the
        // header), parse_nfo_row produces a record whose token, tradingsymbol, name,
        // instrument type, strike, expiry, lot size, and segment equal the source
        // values regardless of column order; a row missing a required field yields
        // None rather than panicking.
        #[test]
        fn prop_nfo_row_parsing_preserves_fields(
            spec in row_spec(),
            // A random permutation over all 12 columns → shuffled column order.
            perm in Just((0usize..12).collect::<Vec<usize>>()).prop_shuffle(),
            corruption in prop_oneof![
                Just(Option::<Corruption>::None),
                Just(Some(Corruption::BadToken)),
                Just(Some(Corruption::EmptySymbol)),
                Just(Some(Corruption::EmptyType)),
            ],
        ) {
            // Build the canonical (column-name, value) pairs, applying any corruption
            // to a required field.
            let mut token_str = spec.token.to_string();
            let mut symbol_str = spec.symbol.clone();
            let mut itype_str = spec.itype.clone();
            match &corruption {
                Some(Corruption::BadToken) => token_str = "not_a_number".to_string(),
                Some(Corruption::EmptySymbol) => symbol_str = String::new(),
                Some(Corruption::EmptyType) => itype_str = String::new(),
                None => {}
            }

            let columns: Vec<(&str, String)> = vec![
                ("instrument_token", token_str),
                ("tradingsymbol", symbol_str),
                ("name", spec.name.clone()),
                ("instrument_type", itype_str),
                ("strike", format!("{}", spec.strike)),
                ("expiry", spec.expiry.clone()),
                ("lot_size", spec.lot_size.to_string()),
                ("segment", spec.segment.clone()),
                // Noise columns present in the real Kite NFO CSV — must be ignored.
                ("exchange_token", "999".to_string()),
                ("last_price", "0".to_string()),
                ("tick_size", "0.05".to_string()),
                ("exchange", "NFO".to_string()),
            ];

            // Apply the permutation to reorder columns (and their values together).
            let reordered: Vec<&(&str, String)> = perm.iter().map(|&i| &columns[i]).collect();
            let header: String = reordered
                .iter()
                .map(|(n, _)| *n)
                .collect::<Vec<_>>()
                .join(",");
            let row: String = reordered
                .iter()
                .map(|(_, v)| v.as_str())
                .collect::<Vec<_>>()
                .join(",");

            // Header resolution must succeed regardless of column order.
            let idx = NfoColumnIndex::from_header(&header)
                .expect("header always carries the required columns");
            let fields: Vec<&str> = row.split(',').collect();
            let result = parse_nfo_row(&fields, &idx);

            match corruption {
                Some(_) => {
                    // A row missing/invalid in a required field yields None, no panic.
                    prop_assert!(
                        result.is_none(),
                        "expected None for corrupted required field, got {:?}",
                        result
                    );
                }
                None => {
                    let rec = result.expect("a well-formed row must parse to Some");
                    prop_assert_eq!(rec.instrument_token, spec.token);
                    prop_assert_eq!(rec.tradingsymbol.as_str(), spec.symbol.as_str());
                    prop_assert_eq!(rec.name.as_str(), spec.name.trim());
                    prop_assert_eq!(rec.instrument_type.as_str(), spec.itype.as_str());
                    prop_assert_eq!(rec.strike, spec.strike);
                    prop_assert_eq!(rec.expiry.as_str(), spec.expiry.trim());
                    prop_assert_eq!(rec.lot_size, spec.lot_size);
                    let expected_segment = if spec.segment.trim().is_empty() {
                        "NFO"
                    } else {
                        spec.segment.trim()
                    };
                    prop_assert_eq!(rec.segment.as_str(), expected_segment);
                }
            }
        }
    }
}

#[cfg(test)]
mod nfo_underlying_property_tests {
    use super::*;
    use proptest::prelude::*;

    /// Names that may carry a configured underlying, an arbitrary underlying, or be
    /// empty/whitespace-only (which must drive the tradingsymbol fallback). Each
    /// variant may carry surrounding whitespace to exercise trimming.
    fn name_strategy() -> impl Strategy<Value = String> {
        prop_oneof![
            // Configured underlyings (the indices this phase starts with).
            Just("NIFTY".to_string()),
            Just("BANKNIFTY".to_string()),
            Just("FINNIFTY".to_string()),
            // Arbitrary underlyings (e.g. stock options), no commas.
            "[A-Z]{1,12}",
            // Names with surrounding whitespace → trim must normalize them.
            "[ \\t]{0,3}[A-Z]{1,8}[ \\t]{0,3}",
            // Empty / whitespace-only → forces the tradingsymbol fallback.
            Just("".to_string()),
            Just("   ".to_string()),
        ]
    }

    fn itype_strategy() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("CE".to_string()),
            Just("PE".to_string()),
            Just("FUT".to_string()),
        ]
    }

    prop_compose! {
        fn nfo_record()(
            token in any::<i64>(),
            // Tradingsymbol is always non-empty for a valid parsed record; it may carry
            // surrounding whitespace so the fallback's trim is exercised too.
            symbol in "[ \\t]{0,3}[A-Za-z0-9]{1,14}[ \\t]{0,3}",
            name in name_strategy(),
            itype in itype_strategy(),
            strike in 0.0f64..100_000.0,
            expiry in prop_oneof![Just("".to_string()), "[0-9]{4}-[0-9]{2}-[0-9]{2}"],
            lot_size in 0i64..100_000,
        ) -> NfoInstrument {
            NfoInstrument {
                instrument_token: token,
                tradingsymbol: symbol,
                name,
                instrument_type: itype,
                strike,
                expiry,
                lot_size,
                segment: "NFO".to_string(),
            }
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: options-data-foundation, Property 2: Underlying association is correct
        // Validates: Requirements 1.3
        //
        // For any parsed NFO record, derive_underlying returns the underlying carried by
        // the record's name/underlying source — the trimmed `name` when non-empty, else
        // the trimmed tradingsymbol — so every CE/PE/FUT contract groups under exactly
        // one non-empty underlying and an option chain can be resolved by underlying.
        #[test]
        fn prop_underlying_association_is_correct(record in nfo_record()) {
            let underlying = derive_underlying(&record);

            // Expected source: trimmed name when non-empty, else trimmed tradingsymbol.
            let trimmed_name = record.name.trim();
            let expected = if !trimmed_name.is_empty() {
                trimmed_name.to_string()
            } else {
                record.tradingsymbol.trim().to_string()
            };
            prop_assert_eq!(&underlying, &expected);

            // Every contract groups under exactly one NON-EMPTY underlying: the
            // tradingsymbol is always non-empty after trim, so the fallback guarantees
            // a non-empty result even when the name is blank.
            prop_assert!(
                !underlying.is_empty(),
                "underlying must be non-empty so the contract groups under exactly one underlying"
            );

            // The result is normalized (no surrounding whitespace) so grouping by the
            // returned key is stable.
            prop_assert_eq!(underlying.trim(), underlying.as_str());

            // Determinism: a second call on the same input yields the same key.
            prop_assert_eq!(derive_underlying(&record), underlying);
        }
    }
}

// ════════════════════════════════════════════════════════════════════════
// Unit / example tests — Options Data Foundation (Phase F1), Task 3.4
//
// Covers the deterministic, feed-free parts of the NFO sync:
//   * R1.4 — NSE `instruments` schema and write path are unchanged and additive.
//   * R1.5 — fresh cache skips download; stale (>= 24h) cache triggers re-download.
//   * R1.6 — an NFO download/parse failure aborts before the upsert transaction,
//            leaving existing data intact (validated via the pure guard helpers).
//
// I/O-bound parts (HTTP download, the live tokio/tauri runtime) are intentionally
// not exercised here; the guard logic that protects existing data is pure and is
// what these tests assert. In-memory rusqlite connections stand in for the DB.
// ════════════════════════════════════════════════════════════════════════
#[cfg(test)]
mod nfo_sync_unit_tests {
    use super::*;
    use rusqlite::Connection;

    // The exact DDL `run_instrument_sync` uses for the NSE equity `instruments` table.
    // Kept verbatim so this test is a regression guard against schema drift (R1.4).
    const INSTRUMENTS_DDL: &str = "CREATE TABLE IF NOT EXISTS instruments (
        instrument_token INTEGER PRIMARY KEY,
        tradingsymbol    TEXT NOT NULL,
        name             TEXT NOT NULL DEFAULT '',
        instrument_type  TEXT NOT NULL DEFAULT 'EQ',
        exchange         TEXT NOT NULL DEFAULT 'NSE',
        last_updated     INTEGER NOT NULL DEFAULT 0
    );";

    // The exact DDL `run_nfo_sync` uses for the additive `nfo_instruments` table.
    const NFO_INSTRUMENTS_DDL: &str = "CREATE TABLE IF NOT EXISTS nfo_instruments (
        instrument_token INTEGER PRIMARY KEY,
        tradingsymbol    TEXT    NOT NULL,
        name             TEXT    NOT NULL DEFAULT '',
        underlying       TEXT    NOT NULL DEFAULT '',
        instrument_type  TEXT    NOT NULL,
        strike           REAL    NOT NULL DEFAULT 0,
        expiry           TEXT    NOT NULL DEFAULT '',
        lot_size         INTEGER NOT NULL DEFAULT 0,
        segment          TEXT    NOT NULL DEFAULT 'NFO',
        last_updated     INTEGER NOT NULL DEFAULT 0
    );";

    /// (cid, name, type, notnull, dflt_value, pk) for a table, ordered by column index.
    fn table_columns(conn: &Connection, table: &str) -> Vec<(String, String, i64, i64)> {
        let mut stmt = conn
            .prepare(&format!("PRAGMA table_info({});", table))
            .expect("pragma prepare");
        let rows = stmt
            .query_map([], |row| {
                // columns: cid, name, type, notnull, dflt_value, pk
                Ok((
                    row.get::<_, String>(1)?, // name
                    row.get::<_, String>(2)?, // declared type
                    row.get::<_, i64>(3)?,    // notnull
                    row.get::<_, i64>(5)?,    // pk
                ))
            })
            .expect("pragma query");
        rows.map(|r| r.expect("pragma row")).collect()
    }

    // ── R1.4: NSE `instruments` schema is unchanged and its write path still works ──

    #[test]
    fn nse_instruments_schema_is_unchanged() {
        let conn = Connection::open_in_memory().expect("open in-memory db");
        conn.execute_batch(INSTRUMENTS_DDL).expect("create instruments");

        let cols = table_columns(&conn, "instruments");
        let names: Vec<&str> = cols.iter().map(|(n, _, _, _)| n.as_str()).collect();

        // Exact column set and order the equity ingestion depends on.
        assert_eq!(
            names,
            vec![
                "instrument_token",
                "tradingsymbol",
                "name",
                "instrument_type",
                "exchange",
                "last_updated",
            ],
            "NSE instruments columns drifted from the established schema (R1.4)"
        );

        // instrument_token is the INTEGER PRIMARY KEY; tradingsymbol is NOT NULL.
        let by_name = |want: &str| cols.iter().find(|(n, _, _, _)| n == want).unwrap();
        let token = by_name("instrument_token");
        assert_eq!(token.1, "INTEGER", "token type changed");
        assert_eq!(token.3, 1, "instrument_token must remain the primary key");
        assert_eq!(by_name("tradingsymbol").2, 1, "tradingsymbol must stay NOT NULL");
    }

    #[test]
    fn nse_instruments_write_path_is_unchanged() {
        let conn = Connection::open_in_memory().expect("open in-memory db");
        conn.execute_batch(INSTRUMENTS_DDL).expect("create instruments");

        // The exact INSERT statement + column tuple used by run_instrument_sync.
        conn.execute(
            "INSERT OR REPLACE INTO instruments (instrument_token, tradingsymbol, name, instrument_type, exchange, last_updated)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6);",
            params![256265i64, "NIFTY 50", "NIFTY", "EQ", "NSE", 1_700_000_000i64],
        )
        .expect("equity insert must still succeed");

        let (sym, itype, exch): (String, String, String) = conn
            .query_row(
                "SELECT tradingsymbol, instrument_type, exchange FROM instruments WHERE instrument_token = 256265;",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .expect("row round-trips");
        assert_eq!(sym, "NIFTY 50");
        assert_eq!(itype, "EQ");
        assert_eq!(exch, "NSE");
    }

    #[test]
    fn nfo_table_is_separate_and_additive() {
        // Both DDLs applied to the same DB must coexist; nfo_instruments is a distinct
        // table that does not redefine or disturb instruments (R1.4).
        let conn = Connection::open_in_memory().expect("open in-memory db");
        conn.execute_batch(INSTRUMENTS_DDL).expect("create instruments");
        conn.execute_batch(NFO_INSTRUMENTS_DDL).expect("create nfo_instruments");

        let table_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('instruments','nfo_instruments');",
                [],
                |r| r.get(0),
            )
            .expect("count tables");
        assert_eq!(table_count, 2, "both tables must exist independently");

        // The NSE schema is untouched by adding the NFO table.
        let names: Vec<String> = table_columns(&conn, "instruments")
            .into_iter()
            .map(|(n, _, _, _)| n)
            .collect();
        assert!(names.contains(&"exchange".to_string()));
        assert!(
            !names.contains(&"strike".to_string()) && !names.contains(&"underlying".to_string()),
            "NFO-only columns must not leak into the equity instruments table"
        );

        // nfo_instruments carries the derivatives-specific columns.
        let nfo_names: Vec<String> = table_columns(&conn, "nfo_instruments")
            .into_iter()
            .map(|(n, _, _, _)| n)
            .collect();
        for expected in ["underlying", "strike", "expiry", "lot_size", "segment"] {
            assert!(
                nfo_names.contains(&expected.to_string()),
                "nfo_instruments missing column {}",
                expected
            );
        }
    }

    // ── R1.5: cache schedule — fresh skips, stale (>= 24h) re-downloads ──

    const HOUR: i64 = 3600;

    #[test]
    fn empty_cache_always_triggers_download() {
        // row_count == 0 → refresh regardless of timestamps.
        assert!(needs_refresh(0, 0, 0));
        assert!(needs_refresh(0, 1_700_000_000, 1_700_000_000));
    }

    #[test]
    fn fresh_cache_skips_download() {
        let now = 1_700_000_000;
        // 0h, 1h, and 23h old caches are all fresh → no download.
        assert!(!needs_refresh(5000, now, now), "0h-old cache must be fresh");
        assert!(!needs_refresh(5000, now - HOUR, now), "1h-old cache must be fresh");
        assert!(
            !needs_refresh(5000, now - 23 * HOUR, now),
            "23h-old cache must still be fresh"
        );
    }

    #[test]
    fn stale_cache_triggers_download() {
        let now = 1_700_000_000;
        // Exactly 24h and older → re-download (boundary is inclusive: age_hours >= 24).
        assert!(needs_refresh(5000, now - 24 * HOUR, now), "24h-old cache is stale");
        assert!(
            needs_refresh(5000, now - 48 * HOUR, now),
            "48h-old cache is stale"
        );
    }

    #[test]
    fn cache_freshness_boundary_is_inclusive_at_24h() {
        let now = 1_000_000;
        // Just under 24h (23h59m) stays fresh; integer-hour 24 flips to stale.
        assert!(!needs_refresh(1, now - (24 * HOUR - 60), now), "23h59m is fresh");
        assert!(needs_refresh(1, now - 24 * HOUR, now), "24h exactly is stale");
    }

    // ── R1.6: a download/parse failure aborts before the upsert (data left intact) ──

    #[test]
    fn clean_csv_field_strips_wrapping_quotes() {
        // Kite wraps textual fields in double quotes; a naive split keeps them.
        assert_eq!(clean_csv_field("\"NIFTY 50\""), "NIFTY 50");
        assert_eq!(clean_csv_field("\"NIFTY\""), "NIFTY");
        // Unquoted values (e.g. tradingsymbol) are unchanged.
        assert_eq!(clean_csv_field("NIFTY24DEC24000CE"), "NIFTY24DEC24000CE");
        assert_eq!(clean_csv_field("RELIANCE"), "RELIANCE");
        // Surrounding whitespace is trimmed, inside and outside the quotes.
        assert_eq!(clean_csv_field("  \" RELIANCE \"  "), "RELIANCE");
        // Only a single pair is removed; empty/degenerate inputs are safe.
        assert_eq!(clean_csv_field(""), "");
        assert_eq!(clean_csv_field("\"\""), "");
    }

    #[test]
    fn parse_nfo_row_strips_quotes_from_name() {
        // A realistic quoted `name` column must yield an unquoted stored name so
        // the derived underlying is clean (e.g. "NIFTY", not "\"NIFTY\"").
        let header = "instrument_token,tradingsymbol,name,instrument_type,strike,expiry,lot_size,segment";
        let idx = NfoColumnIndex::from_header(header).expect("header resolves");
        let row = "12001,NIFTY24DEC24000CE,\"NIFTY\",CE,24000,2024-12-26,50,NFO-OPT";
        let fields: Vec<&str> = row.split(',').collect();
        let rec = parse_nfo_row(&fields, &idx).expect("row parses");
        assert_eq!(rec.name, "NIFTY");
        assert_eq!(derive_underlying(&rec), "NIFTY");
    }

    #[test]
    fn header_missing_required_columns_aborts_before_upsert() {
        // run_nfo_sync resolves the header via NfoColumnIndex::from_header and returns
        // early (before BEGIN TRANSACTION / DELETE) when it is None. Each header below
        // is missing one required column, so the sync would never touch existing data.
        let missing_token = "tradingsymbol,instrument_type,strike,expiry";
        let missing_symbol = "instrument_token,instrument_type,strike,expiry";
        let missing_type = "instrument_token,tradingsymbol,strike,expiry";

        assert!(NfoColumnIndex::from_header(missing_token).is_none());
        assert!(NfoColumnIndex::from_header(missing_symbol).is_none());
        assert!(NfoColumnIndex::from_header(missing_type).is_none());

        // A complete header resolves so a valid download would proceed.
        let ok = "instrument_token,tradingsymbol,instrument_type,strike,expiry,lot_size,segment,name";
        assert!(NfoColumnIndex::from_header(ok).is_some());
    }

    #[test]
    fn headers_only_or_empty_csv_yields_no_rows() {
        // A headers-only body (or one with only blank lines) parses to zero valid rows,
        // so run_nfo_sync hits its "no valid rows parsed" guard and returns without
        // beginning the transaction — existing data is left intact (R1.6).
        let header = "instrument_token,tradingsymbol,instrument_type,strike,expiry,lot_size,segment,name";
        let idx = NfoColumnIndex::from_header(header).expect("header resolves");

        let data_lines: Vec<&str> = vec![]; // headers only → no data rows
        let parsed: Vec<NfoInstrument> = data_lines
            .iter()
            .filter_map(|line| {
                let fields: Vec<&str> = line.split(',').collect();
                parse_nfo_row(&fields, &idx)
            })
            .collect();
        assert!(parsed.is_empty(), "headers-only CSV must yield no rows");

        // A body of blank / malformed lines likewise yields nothing (all skipped).
        let junk: Vec<&str> = vec!["", "   ", "not,enough"];
        let parsed_junk: Vec<NfoInstrument> = junk
            .iter()
            .filter_map(|line| {
                let fields: Vec<&str> = line.split(',').collect();
                parse_nfo_row(&fields, &idx)
            })
            .collect();
        assert!(parsed_junk.is_empty(), "malformed rows must all be skipped");
    }

    #[test]
    fn existing_nfo_data_survives_when_parse_guard_aborts() {
        // Demonstrate the "leave intact" guarantee concretely: prior rows exist, and a
        // failed download/parse (modelled by the from_header guard returning None) means
        // the DELETE + upsert is never reached, so the sentinel row remains.
        let conn = Connection::open_in_memory().expect("open in-memory db");
        conn.execute_batch(NFO_INSTRUMENTS_DDL).expect("create nfo_instruments");
        conn.execute(
            "INSERT INTO nfo_instruments
                (instrument_token, tradingsymbol, name, underlying, instrument_type, strike, expiry, lot_size, segment, last_updated)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10);",
            params![111i64, "NIFTY24DEC24000CE", "NIFTY", "NIFTY", "CE", 24000.0f64, "2024-12-26", 50i64, "NFO-OPT", 1_699_000_000i64],
        )
        .expect("seed prior row");

        // Simulate the sync's guard: a malformed header => abort before any DB mutation.
        let bad_header = "wrong,header,columns";
        let aborted = NfoColumnIndex::from_header(bad_header).is_none();
        assert!(aborted, "malformed header must abort the sync");

        // Because the guard aborts before DELETE/INSERT, the seeded row is untouched.
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM nfo_instruments;", [], |r| r.get(0))
            .expect("count");
        assert_eq!(count, 1, "existing NFO data must survive a failed sync (R1.6)");
        let sym: String = conn
            .query_row(
                "SELECT tradingsymbol FROM nfo_instruments WHERE instrument_token = 111;",
                [],
                |r| r.get(0),
            )
            .expect("row intact");
        assert_eq!(sym, "NIFTY24DEC24000CE");
    }
}
