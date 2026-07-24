// server.rs — Central backend host resolution (thin-client mode)
//
// The Strat Ai desktop app is a THIN CLIENT: its backend (live-data WebSocket
// bridges, ingestion control, QuestDB, deep-quant agent, F&O service) runs on a
// remote server, not on the user's machine. Every connection endpoint derives
// its host from a single value resolved here, so one setting repoints the whole
// app.
//
// Resolution priority:
//   1. STRATAI_SERVER_HOST environment variable at RUNTIME
//      (ops override / power users / tests).
//   2. STRATAI_SERVER_HOST baked at COMPILE time
//      (release pipeline sets this to the droplet IP/domain so shipped
//       installers "just work" for end users).
//   3. "127.0.0.1" — local development default (backend on same machine).
//
// Individual per-service env vars (QUESTDB_HTTP_URL, DEEP_QUANT_URL, etc.) still
// take precedence where present; their DEFAULTS are built from host() so setting
// STRATAI_SERVER_HOST alone is sufficient for a standard deployment.

/// Resolve the backend server host (no scheme, no port).
pub fn host() -> String {
    if let Ok(h) = std::env::var("STRATAI_SERVER_HOST") {
        let h = h.trim();
        if !h.is_empty() {
            return h.to_string();
        }
    }
    match option_env!("STRATAI_SERVER_HOST") {
        Some(h) if !h.is_empty() => h.to_string(),
        _ => "127.0.0.1".to_string(),
    }
}

/// `ws://<host>:<port>`
pub fn ws_url(port: u16) -> String {
    format!("ws://{}:{}", host(), port)
}

/// `<host>:<port>` (for raw TCP control sockets)
pub fn tcp_addr(port: u16) -> String {
    format!("{}:{}", host(), port)
}

/// `http://<host>:<port>`
pub fn http_url(port: u16) -> String {
    format!("http://{}:{}", host(), port)
}
