// commands/ticker.rs — Dynamic symbol subscription command
//
// Manages the currently active chart symbol in a thread-safe Tauri state.
// Called by the frontend on every symbol switch to keep the Rust backend
// in sync with the chart's active instrument.
//
// In TEST MODE: the mock OHLC emitter reads this state on each tick so that
//   switching symbols immediately changes what the mock emits.
// In PRODUCTION: the WS bridge (lib.rs) is symbol-agnostic (all symbols flow
//   from the aggregator); this state is available for future server-side
//   symbol-filtered broadcasting.

use tokio::sync::Mutex;
use log::info;

/// Thread-safe container for the currently active chart symbol.
/// Managed as Tauri state — accessible from any #[tauri::command] function
/// and from the background tokio tasks in lib.rs.
///
/// Defaults to "RELIANCE" on startup.
pub struct ActiveSymbolState {
    pub symbol: Mutex<String>,
}

impl ActiveSymbolState {
    pub fn new(initial: &str) -> Self {
        Self {
            symbol: Mutex::new(initial.to_string()),
        }
    }
}

/// Tauri IPC command: set the active chart symbol.
///
/// Called by the frontend whenever the user switches instruments.
/// Updates shared Tauri state read by the mock emitter and available
/// to any future server-side filtering logic.
///
/// # Frontend usage
/// ```ts
/// import { invoke } from '@tauri-apps/api/core';
/// await invoke('subscribe_ticker', { symbol: 'INFY' });
/// ```
#[tauri::command]
pub async fn subscribe_ticker(
    state: tauri::State<'_, ActiveSymbolState>,
    symbol: String,
) -> Result<(), String> {
    let upper = symbol.trim().to_uppercase();
    if upper.is_empty() {
        return Err("subscribe_ticker: symbol must not be empty".to_string());
    }
    let mut lock = state.symbol.lock().await;
    let prev = lock.clone();
    *lock = upper.clone();
    info!("[subscribe_ticker] Active symbol: {} → {}", prev, upper);
    Ok(())
}
