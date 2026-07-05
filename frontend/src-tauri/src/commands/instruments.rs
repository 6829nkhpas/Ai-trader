// commands/instruments.rs — F&O-aware Instrument Search API
//
// Provides a fast local search over the cached Kite instrument master.
// Searches BOTH the NSE equity table (`instruments`) and the NFO derivatives
// table (`nfo_instruments`, ingested in F1), returning a typed union so the
// frontend can render equities and option/future contracts distinctly.
//
// Called by the React search bar as the user types to provide autocomplete
// results without hitting the Kite API on every keystroke. No new ingestion
// is performed here — this only queries the already-synced masters.

use log::info;
use rusqlite::{params, Connection};
use serde::Serialize;

use crate::db::DbState;

/// A single search result. Serializes to the frontend `SearchResult` union:
///
/// ```ts
/// type SearchResult =
///   | { kind: 'EQ'; symbol: string; name: string; exchange: string }
///   | { kind: 'FNO'; tradingsymbol: string; underlying: string; expiry: string;
///       strike: number | null; optionType: 'CE' | 'PE' | 'FUT' };
/// ```
///
/// The `kind` tag is emitted by serde's internally-tagged enum representation.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "kind")]
pub enum SearchResult {
    /// An NSE equity instrument.
    #[serde(rename = "EQ")]
    Eq {
        symbol: String,
        name: String,
        exchange: String,
    },
    /// An NFO derivatives contract (option or future).
    #[serde(rename = "FNO")]
    Fno {
        tradingsymbol: String,
        underlying: String,
        expiry: String,
        /// `None` for futures (and any contract without a positive strike); the
        /// frontend renders this as `null`.
        strike: Option<f64>,
        #[serde(rename = "optionType")]
        option_type: String,
    },
}

/// Return whether a table with the given name exists in the connection.
fn table_exists(conn: &Connection, table: &str) -> bool {
    conn.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1;",
        params![table],
        |row| row.get::<_, i64>(0),
    )
    .map(|count| count > 0)
    .unwrap_or(false)
}

/// Pure-ish search core: query both masters for `query` against the given
/// connection. Extracted from the Tauri command so it is unit-testable without
/// a Tauri `State` / app handle.
///
/// Returns `Err` only when NEITHER master table exists yet (initial sync not
/// finished). When at least one table exists, returns whatever matches were
/// found (possibly an empty vec → the UI shows a no-results state).
pub fn search_in_db(conn: &Connection, query: &str) -> Result<Vec<SearchResult>, String> {
    let q = query.trim().to_uppercase();
    if q.is_empty() {
        return Ok(vec![]);
    }

    let like_prefix = format!("{}%", q);
    let like_contains = format!("%{}%", q);

    let eq_exists = table_exists(conn, "instruments");
    let nfo_exists = table_exists(conn, "nfo_instruments");

    if !eq_exists && !nfo_exists {
        return Err("Instrument master not yet populated. Please wait for initial sync.".into());
    }

    let mut results: Vec<SearchResult> = Vec::new();

    // ── Equities ─────────────────────────────────────────────────────────
    // Prefix matches on tradingsymbol first, then shorter symbols, preserving
    // the original equity-search ordering behavior.
    if eq_exists {
        let mut stmt = conn
            .prepare(
                "SELECT tradingsymbol, name, exchange
                 FROM instruments
                 WHERE tradingsymbol LIKE ?1 OR name LIKE ?2
                 ORDER BY
                     CASE WHEN tradingsymbol LIKE ?1 THEN 0 ELSE 1 END,
                     LENGTH(tradingsymbol) ASC
                 LIMIT 10;",
            )
            .map_err(|e| format!("SQL prepare error (equities): {}", e))?;

        let rows = stmt
            .query_map(params![like_prefix, like_contains], |row| {
                Ok(SearchResult::Eq {
                    symbol: row.get(0)?,
                    name: row.get(1)?,
                    exchange: row.get(2)?,
                })
            })
            .map_err(|e| format!("SQL query error (equities): {}", e))?;

        for r in rows.filter_map(|r| r.ok()) {
            results.push(r);
        }
    }

    // ── NFO derivatives ──────────────────────────────────────────────────
    // Token-based search: splits "NIFTY 24000 CE" into tokens and matches
    // each one against tradingsymbol / underlying / strike / instrument_type.
    // Recognizes option-type aliases (PUT→PE, CALL→CE) and numeric strikes.
    if nfo_exists {
        let nfo_results = search_nfo_tokenized(conn, &q)?;
        results.extend(nfo_results);
    }

    Ok(results)
}

/// Normalize an option-type alias to its canonical form.
fn normalize_option_type(token: &str) -> Option<&'static str> {
    match token {
        "CE" | "CALL" => Some("CE"),
        "PE" | "PUT" => Some("PE"),
        "FUT" | "FUTURE" | "FUTURES" => Some("FUT"),
        _ => None,
    }
}

/// Token-based NFO search. Splits multi-word queries (e.g. "NIFTY 24000 CE")
/// into individual tokens and classifies each as:
///   - option type  (CE, PE, PUT, CALL, FUT)
///   - numeric strike (24000)
///   - symbol/underlying text (NIFTY, RELIANCE)
///
/// Builds a dynamic WHERE clause requiring all tokens to match, producing
/// results identical to the Zerodha Kite search modal for F&O queries.
fn search_nfo_tokenized(conn: &Connection, query: &str) -> Result<Vec<SearchResult>, String> {
    let tokens: Vec<&str> = query.split_whitespace().collect();
    if tokens.is_empty() {
        return Ok(vec![]);
    }

    // Classify tokens
    let mut text_tokens: Vec<String> = Vec::new();
    let mut option_type_filter: Option<&str> = None;
    let mut strike_filter: Option<f64> = None;

    for token in &tokens {
        if let Some(ot) = normalize_option_type(token) {
            option_type_filter = Some(ot);
        } else if let Ok(num) = token.parse::<f64>() {
            strike_filter = Some(num);
        } else {
            text_tokens.push(token.to_string());
        }
    }

    // Build dynamic SQL WHERE clause
    let mut conditions: Vec<String> = Vec::new();
    let mut bind_values: Vec<String> = Vec::new();

    // Each text token must appear in tradingsymbol, underlying, or name
    for text in &text_tokens {
        let like_val = format!("%{}%", text);
        let idx = bind_values.len() + 1;
        bind_values.push(like_val);
        conditions.push(format!(
            "(tradingsymbol LIKE ?{idx} OR underlying LIKE ?{idx} OR name LIKE ?{idx})"
        ));
    }

    // Option type filter (exact match)
    if let Some(ot) = option_type_filter {
        let idx = bind_values.len() + 1;
        bind_values.push(ot.to_string());
        conditions.push(format!("instrument_type = ?{idx}"));
    }

    // Strike filter (prefix match — 2400 matches 24000, 24050, etc.)
    if let Some(strike_num) = strike_filter {
        let strike_str = format!("{}", strike_num as i64);
        let idx = bind_values.len() + 1;
        bind_values.push(format!("{}%", strike_str));
        conditions.push(format!("CAST(CAST(strike AS INTEGER) AS TEXT) LIKE ?{idx}"));
    }

    if conditions.is_empty() {
        return Ok(vec![]);
    }

    let where_clause = conditions.join(" AND ");
    let sql = format!(
        "SELECT tradingsymbol, underlying, expiry, strike, instrument_type \
         FROM nfo_instruments \
         WHERE {} \
         ORDER BY expiry ASC, strike ASC \
         LIMIT 25;",
        where_clause
    );

    let mut stmt = conn
        .prepare(&sql)
        .map_err(|e| format!("SQL prepare error (nfo tokenized): {}", e))?;

    // Bind all values dynamically
    let params_ref: Vec<&dyn rusqlite::types::ToSql> =
        bind_values.iter().map(|v| v as &dyn rusqlite::types::ToSql).collect();

    let rows = stmt
        .query_map(params_ref.as_slice(), |row| {
            let option_type: String = row.get(4)?;
            let strike_val: f64 = row.get(3)?;
            let strike = if option_type.eq_ignore_ascii_case("FUT") || strike_val <= 0.0 {
                None
            } else {
                Some(strike_val)
            };
            Ok(SearchResult::Fno {
                tradingsymbol: row.get(0)?,
                underlying: row.get(1)?,
                expiry: row.get(2)?,
                strike,
                option_type,
            })
        })
        .map_err(|e| format!("SQL query error (nfo tokenized): {}", e))?;

    let mut results = Vec::new();
    for r in rows.filter_map(|r| r.ok()) {
        results.push(r);
    }
    Ok(results)
}

/// Search the local instrument master (equities + NFO) for the query.
///
/// Performs a fast `LIKE` search across both the NSE equity table and the NFO
/// derivatives table, returning a typed `EQ` / `FNO` union. On any backend
/// error (DB lock, missing master, SQL failure) it returns `Err(String)` so the
/// UI can surface an explicit error/no-results state without crashing.
///
/// # Frontend usage
/// ```ts
/// const results = await invoke<SearchResult[]>('search_instruments', { query: 'NIFTY' });
/// // → [{ kind: 'FNO', tradingsymbol: 'NIFTY24DEC24000CE', underlying: 'NIFTY', ... }, ...]
/// ```
#[tauri::command]
pub async fn search_instruments(
    query: String,
    state: tauri::State<'_, DbState>,
) -> Result<Vec<SearchResult>, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock error: {}", e))?;
    let results = search_in_db(&conn, &query)?;

    info!(
        "[search_instruments] query='{}' → {} results",
        query.trim().to_uppercase(),
        results.len()
    );

    Ok(results)
}

/// Resolve a tradingsymbol to its instrument_token from the local cache.
/// Used internally by subscribe_ticker to avoid hitting the aggregator API.
///
/// Returns None if the symbol is not found in the local instruments table.
pub fn resolve_instrument_token(db_state: &DbState, symbol: &str) -> Option<u32> {
    let conn = db_state.conn.lock().ok()?;
    let upper = symbol.trim().to_uppercase();

    conn.query_row(
        "SELECT instrument_token FROM instruments WHERE tradingsymbol = ?1 LIMIT 1;",
        params![upper],
        |row| row.get::<_, i64>(0),
    )
    .ok()
    .map(|t| t as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build an in-memory DB with both master tables and a few rows.
    fn seed_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE instruments (
                instrument_token INTEGER PRIMARY KEY,
                tradingsymbol    TEXT NOT NULL,
                name             TEXT NOT NULL DEFAULT '',
                instrument_type  TEXT NOT NULL DEFAULT 'EQ',
                exchange         TEXT NOT NULL DEFAULT 'NSE',
                last_updated     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE nfo_instruments (
                instrument_token INTEGER PRIMARY KEY,
                tradingsymbol    TEXT NOT NULL,
                name             TEXT NOT NULL DEFAULT '',
                underlying       TEXT NOT NULL DEFAULT '',
                instrument_type  TEXT NOT NULL,
                strike           REAL NOT NULL DEFAULT 0,
                expiry           TEXT NOT NULL DEFAULT '',
                lot_size         INTEGER NOT NULL DEFAULT 0,
                segment          TEXT NOT NULL DEFAULT 'NFO',
                last_updated     INTEGER NOT NULL DEFAULT 0
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO instruments (instrument_token, tradingsymbol, name, instrument_type, exchange)
             VALUES (738561, 'RELIANCE', 'Reliance Industries', 'EQ', 'NSE'),
                    (256265, 'NIFTY 50', 'NIFTY 50 Index', 'EQ', 'NSE');",
            [],
        )
        .unwrap();

        conn.execute(
            "INSERT INTO nfo_instruments
                (instrument_token, tradingsymbol, name, underlying, instrument_type, strike, expiry, lot_size, segment)
             VALUES
                (12001, 'NIFTY24DEC24000CE', 'NIFTY', 'NIFTY', 'CE', 24000.0, '2024-12-26', 50, 'NFO-OPT'),
                (12002, 'NIFTY24DEC24000PE', 'NIFTY', 'NIFTY', 'PE', 24000.0, '2024-12-26', 50, 'NFO-OPT'),
                (12003, 'NIFTY24DECFUT',     'NIFTY', 'NIFTY', 'FUT', 0.0,    '2024-12-26', 50, 'NFO-FUT');",
            [],
        )
        .unwrap();

        conn
    }

    #[test]
    fn empty_query_returns_empty() {
        let conn = seed_db();
        let out = search_in_db(&conn, "   ").unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn equity_query_returns_eq_results() {
        let conn = seed_db();
        let out = search_in_db(&conn, "RELI").unwrap();
        assert_eq!(
            out,
            vec![SearchResult::Eq {
                symbol: "RELIANCE".into(),
                name: "Reliance Industries".into(),
                exchange: "NSE".into(),
            }]
        );
    }

    #[test]
    fn nfo_query_returns_fno_results_with_strike_and_type() {
        let conn = seed_db();
        let out = search_in_db(&conn, "NIFTY24DEC24000C").unwrap();
        assert_eq!(out.len(), 1);
        match &out[0] {
            SearchResult::Fno {
                tradingsymbol,
                underlying,
                expiry,
                strike,
                option_type,
            } => {
                assert_eq!(tradingsymbol, "NIFTY24DEC24000CE");
                assert_eq!(underlying, "NIFTY");
                assert_eq!(expiry, "2024-12-26");
                assert_eq!(*strike, Some(24000.0));
                assert_eq!(option_type, "CE");
            }
            other => panic!("expected FNO, got {:?}", other),
        }
    }

    #[test]
    fn future_has_null_strike() {
        let conn = seed_db();
        let out = search_in_db(&conn, "NIFTY24DECFUT").unwrap();
        assert_eq!(out.len(), 1);
        match &out[0] {
            SearchResult::Fno { strike, option_type, .. } => {
                assert_eq!(*strike, None);
                assert_eq!(option_type, "FUT");
            }
            other => panic!("expected FNO FUT, got {:?}", other),
        }
    }

    #[test]
    fn underlying_query_returns_both_eq_and_fno() {
        let conn = seed_db();
        let out = search_in_db(&conn, "NIFTY").unwrap();
        // Equity "NIFTY 50" + three NFO contracts.
        let eq_count = out.iter().filter(|r| matches!(r, SearchResult::Eq { .. })).count();
        let fno_count = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).count();
        assert_eq!(eq_count, 1, "expected the NIFTY 50 equity");
        assert_eq!(fno_count, 3, "expected the three NFO contracts");
    }

    #[test]
    fn no_match_returns_empty_not_error() {
        let conn = seed_db();
        let out = search_in_db(&conn, "ZZZZNOMATCH").unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn missing_both_tables_is_error() {
        let conn = Connection::open_in_memory().unwrap();
        let res = search_in_db(&conn, "NIFTY");
        assert!(res.is_err());
    }

    #[test]
    fn serializes_to_tagged_union() {
        let eq = SearchResult::Eq {
            symbol: "RELIANCE".into(),
            name: "Reliance".into(),
            exchange: "NSE".into(),
        };
        let json = serde_json::to_string(&eq).unwrap();
        assert!(json.contains("\"kind\":\"EQ\""));
        assert!(json.contains("\"symbol\":\"RELIANCE\""));

        let fno = SearchResult::Fno {
            tradingsymbol: "NIFTY24DECFUT".into(),
            underlying: "NIFTY".into(),
            expiry: "2024-12-26".into(),
            strike: None,
            option_type: "FUT".into(),
        };
        let json = serde_json::to_string(&fno).unwrap();
        assert!(json.contains("\"kind\":\"FNO\""));
        assert!(json.contains("\"optionType\":\"FUT\""));
        assert!(json.contains("\"strike\":null"));
    }

    #[test]
    fn multi_word_nfo_search_underlying_and_strike() {
        let conn = seed_db();
        // "NIFTY 24000" → should match both CE + PE with strike 24000
        let out = search_in_db(&conn, "NIFTY 24000").unwrap();
        let fno: Vec<_> = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).collect();
        assert_eq!(fno.len(), 2, "expected CE + PE for NIFTY 24000");
    }

    #[test]
    fn multi_word_nfo_search_underlying_strike_and_type() {
        let conn = seed_db();
        // "NIFTY 24000 CE" → should match exactly 1 CE contract
        let out = search_in_db(&conn, "NIFTY 24000 CE").unwrap();
        let fno: Vec<_> = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).collect();
        assert_eq!(fno.len(), 1, "expected exactly one CE contract");
        match fno[0] {
            SearchResult::Fno { option_type, strike, .. } => {
                assert_eq!(option_type, "CE");
                assert_eq!(*strike, Some(24000.0));
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn multi_word_nfo_search_with_alias_put() {
        let conn = seed_db();
        // "NIFTY PUT" → should match the PE contract
        let out = search_in_db(&conn, "NIFTY PUT").unwrap();
        let fno: Vec<_> = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).collect();
        assert_eq!(fno.len(), 1, "expected exactly one PE contract for 'PUT'");
        match fno[0] {
            SearchResult::Fno { option_type, .. } => {
                assert_eq!(option_type, "PE");
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn multi_word_nfo_search_with_alias_call() {
        let conn = seed_db();
        // "NIFTY CALL" → should match the CE contract
        let out = search_in_db(&conn, "NIFTY CALL").unwrap();
        let fno: Vec<_> = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).collect();
        assert_eq!(fno.len(), 1, "expected exactly one CE contract for 'CALL'");
        match fno[0] {
            SearchResult::Fno { option_type, .. } => {
                assert_eq!(option_type, "CE");
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn multi_word_nfo_search_fut_alias() {
        let conn = seed_db();
        // "NIFTY FUT" → should match the FUT contract
        let out = search_in_db(&conn, "NIFTY FUT").unwrap();
        let fno: Vec<_> = out.iter().filter(|r| matches!(r, SearchResult::Fno { .. })).collect();
        assert_eq!(fno.len(), 1, "expected exactly one FUT contract");
        match fno[0] {
            SearchResult::Fno { option_type, strike, .. } => {
                assert_eq!(option_type, "FUT");
                assert_eq!(*strike, None);
            }
            _ => unreachable!(),
        }
    }
}
