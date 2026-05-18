// ── db.rs — Local SQLite Workspace Persistence Engine ───────────────────
//
// Embeds a SQLite database (`workspace.db`) alongside the Tauri app data
// directory. Persists chart drawings and UI settings per-symbol so that
// a user's workspace survives application restarts.
//
// Tables:
//   workspaces (symbol TEXT PK, state_json TEXT)
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
/// In debug builds the DB lives next to the executable for convenience.
/// In release (production) it lives in the OS-standard local data directory
/// (e.g. `%APPDATA%/com.alphasuite.app/workspace.db` on Windows).
fn db_path() -> PathBuf {
    if cfg!(debug_assertions) {
        // Dev mode — store next to the Tauri binary for easy inspection
        PathBuf::from("workspace.db")
    } else {
        // Production — respect OS conventions via dirs crate fallback
        let mut dir = dirs_fallback();
        std::fs::create_dir_all(&dir).ok();
        dir.push("workspace.db");
        dir
    }
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

    info!("[Workspace DB] Schema ready — workspaces table initialised.");
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
