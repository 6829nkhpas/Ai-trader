// ── db.rs — Local SQLite Workspace Persistence Engine ───────────────────
//
// Embeds a SQLite database (`workspace.db`) alongside the Tauri app data
// directory. Persists chart drawings and UI settings per-symbol so that
// a user's workspace survives application restarts.
//
// Tables:
//   workspaces (symbol TEXT PK, state_json TEXT)
//   trades     (id TEXT PK, symbol TEXT, entry_price REAL, exit_price REAL,
//              pnl REAL, pos_type TEXT, size REAL, timestamp INTEGER)
//
// Exposed Tauri commands:
//   save_workspace(symbol, state_json) — UPSERT via ON CONFLICT
//   load_workspace(symbol)             — SELECT state_json or "{}"
// ────────────────────────────────────────────────────────────────────────

use rusqlite::{Connection, params};
use std::path::PathBuf;
use std::sync::Mutex;
use log::{info, error};

/// Thread-safe wrapper around the SQLite connection.
/// Stored as Tauri managed state so every command can access it.
pub struct DbState {
    pub conn: Mutex<Connection>,
}

/// Resolve the SQLite database file path.
///
/// Both debug and release builds use the OS-standard local data directory
/// (e.g. `%LOCALAPPDATA%/com.alphasuite.app/workspace.db` on Windows).
///
/// Previously, debug builds stored the DB as a bare `workspace.db` in the
/// Tauri working directory (src-tauri/), which caused the dev file watcher
/// to detect WAL/SHM changes and trigger infinite rebuild loops.
fn db_path() -> PathBuf {
    let mut dir = dirs_fallback();
    std::fs::create_dir_all(&dir).ok();
    dir.push("workspace.db");
    dir
}

/// Minimal fallback to find the user's local app data directory
/// without pulling in the full `dirs` crate.
fn dirs_fallback() -> PathBuf {
    // Windows: %LOCALAPPDATA%, macOS: ~/Library/Application Support, Linux: ~/.local/share
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        PathBuf::from(local).join("com.alphasuite.app")
    } else if let Ok(home) = std::env::var("HOME") {
        if cfg!(target_os = "macos") {
            PathBuf::from(home).join("Library/Application Support/com.alphasuite.app")
        } else {
            PathBuf::from(home).join(".local/share/com.alphasuite.app")
        }
    } else {
        PathBuf::from(".")
    }
}

/// Initialise the workspace SQLite database.
///
/// Creates the file if it doesn't exist and runs the schema migration.
/// Returns a `DbState` that should be registered with `app.manage()`.
pub fn init_db() -> Result<DbState, String> {
    let path = db_path();
    info!("[Workspace DB] Opening SQLite at {}", path.display());

    let conn = Connection::open(&path).map_err(|e| {
        let msg = format!("[Workspace DB] Failed to open SQLite: {}", e);
        error!("{}", msg);
        msg
    })?;

    // WAL journal mode for better concurrent read performance
    conn.execute_batch("PRAGMA journal_mode=WAL;").ok();

    // Create the workspaces table if it doesn't exist
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workspaces (
            symbol     TEXT PRIMARY KEY,
            state_json TEXT NOT NULL DEFAULT '{}'
        );",
        [],
    ).map_err(|e| {
        let msg = format!("[Workspace DB] Migration failed: {}", e);
        error!("{}", msg);
        msg
    })?;

    // Create the trades table for paper trading journal
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trades (
            id          TEXT PRIMARY KEY,
            symbol      TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price  REAL NOT NULL,
            pnl         REAL NOT NULL,
            pos_type    TEXT NOT NULL DEFAULT 'LONG',
            size        REAL NOT NULL DEFAULT 1.0,
            timestamp   INTEGER NOT NULL
        );",
        [],
    ).map_err(|e| {
        let msg = format!("[Workspace DB] Trades table migration failed: {}", e);
        error!("{}", msg);
        msg
    })?;

    info!("[Workspace DB] Schema ready — workspaces + trades tables initialised.");
    Ok(DbState { conn: Mutex::new(conn) })
}

// ── Tauri IPC Commands ──────────────────────────────────────────────────

/// Save (UPSERT) a symbol's workspace state to the local SQLite database.
///
/// Uses `INSERT ... ON CONFLICT DO UPDATE` to atomically create or replace
/// the JSON blob for the given symbol key.
#[tauri::command]
pub fn save_workspace(
    state: tauri::State<'_, DbState>,
    symbol: &str,
    state_json: &str,
) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock error: {}", e))?;
    conn.execute(
        "INSERT INTO workspaces (symbol, state_json)
         VALUES (?1, ?2)
         ON CONFLICT(symbol) DO UPDATE SET state_json = excluded.state_json;",
        params![symbol, state_json],
    ).map_err(|e| format!("Failed to save workspace for {}: {}", symbol, e))?;

    Ok(())
}

/// Load a symbol's workspace state from the local SQLite database.
///
/// Returns the stored JSON string, or an empty JSON object `"{}"` if no
/// workspace has been saved for this symbol yet.
#[tauri::command]
pub fn load_workspace(
    state: tauri::State<'_, DbState>,
    symbol: &str,
) -> Result<String, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock error: {}", e))?;

    let result: Result<String, rusqlite::Error> = conn.query_row(
        "SELECT state_json FROM workspaces WHERE symbol = ?1;",
        params![symbol],
        |row| row.get(0),
    );

    match result {
        Ok(json) => Ok(json),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok("{}".to_string()),
        Err(e) => Err(format!("Failed to load workspace for {}: {}", symbol, e)),
    }
}

// ── Trade Journal Commands ──────────────────────────────────────────────

/// Log a completed paper trade to the local SQLite database.
///
/// Called by the frontend when a simulated position is closed (via auto-exit
/// or manual close). Provides persistent trade history for PNL review.
#[tauri::command]
pub fn log_completed_trade(
    state: tauri::State<'_, DbState>,
    id: &str,
    symbol: &str,
    entry_price: f64,
    exit_price: f64,
    pnl: f64,
    pos_type: &str,
    size: f64,
    timestamp: i64,
) -> Result<(), String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock error: {}", e))?;
    conn.execute(
        "INSERT INTO trades (id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
         ON CONFLICT(id) DO UPDATE SET
             exit_price = excluded.exit_price,
             pnl = excluded.pnl;",
        params![id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp],
    ).map_err(|e| format!("Failed to log trade {}: {}", id, e))?;

    info!("[Trade Journal] Logged: {} {} @ {} → {} | PNL: {:.2}", pos_type, symbol, entry_price, exit_price, pnl);
    Ok(())
}

/// Retrieve all completed trades from the local SQLite database.
///
/// Returns a JSON-serialized array of trade records, ordered most recent first.
#[tauri::command]
pub fn get_trade_history(
    state: tauri::State<'_, DbState>,
) -> Result<String, String> {
    let conn = state.conn.lock().map_err(|e| format!("DB lock error: {}", e))?;

    let mut stmt = conn.prepare(
        "SELECT id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp
         FROM trades ORDER BY timestamp DESC LIMIT 200;"
    ).map_err(|e| format!("Failed to prepare trade query: {}", e))?;

    let trades: Vec<serde_json::Value> = stmt.query_map([], |row| {
        Ok(serde_json::json!({
            "id": row.get::<_, String>(0)?,
            "symbol": row.get::<_, String>(1)?,
            "entry_price": row.get::<_, f64>(2)?,
            "exit_price": row.get::<_, f64>(3)?,
            "pnl": row.get::<_, f64>(4)?,
            "type": row.get::<_, String>(5)?,
            "size": row.get::<_, f64>(6)?,
            "timestamp": row.get::<_, i64>(7)?,
        }))
    }).map_err(|e| format!("Trade query failed: {}", e))?
    .filter_map(|r| r.ok())
    .collect();

    serde_json::to_string(&trades).map_err(|e| format!("Trade serialization failed: {}", e))
}
