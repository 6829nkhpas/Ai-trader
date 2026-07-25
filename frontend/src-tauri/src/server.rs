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

/// Resolve a value with priority: runtime env → compile-time env → default.
///
/// `compile` should be the result of `option_env!("KEY")` at the call site
/// (the macro must expand inside this crate to bake the release value).
fn resolve(runtime: Result<String, std::env::VarError>, compile: Option<&str>, default: &str) -> String {
    if let Ok(v) = runtime {
        let v = v.trim();
        if !v.is_empty() {
            return v.to_string();
        }
    }
    match compile {
        Some(v) if !v.is_empty() => v.to_string(),
        _ => default.to_string(),
    }
}

/// Resolve the backend server host (no scheme, no port).
pub fn host() -> String {
    resolve(
        std::env::var("STRATAI_SERVER_HOST"),
        option_env!("STRATAI_SERVER_HOST"),
        "127.0.0.1",
    )
}

/// QuestDB auth username — a single shared beta credential used for BOTH the
/// authenticated HTTP `/exec` endpoint (via the Caddy basic-auth gateway) and
/// the PostgreSQL wire protocol. Baked into release builds by the pipeline.
pub fn questdb_user() -> String {
    resolve(
        std::env::var("QUESTDB_USER"),
        option_env!("QUESTDB_USER"),
        "admin",
    )
}

/// QuestDB auth password (see `questdb_user`). Local-dev default matches an
/// unconfigured QuestDB (`quest`), so basic-auth is simply ignored locally.
pub fn questdb_password() -> String {
    resolve(
        std::env::var("QUESTDB_PASSWORD"),
        option_env!("QUESTDB_PASSWORD"),
        "quest",
    )
}

/// Full PostgreSQL-wire connection URL for QuestDB (`:8812`).
///
/// An explicit `QUESTDB_POSTGRES_URL` override wins; otherwise the URL is
/// built from `questdb_user()`, `questdb_password()` and `host()`.
pub fn questdb_pg_url() -> String {
    if let Ok(u) = std::env::var("QUESTDB_POSTGRES_URL") {
        let u = u.trim();
        if !u.is_empty() {
            return u.to_string();
        }
    }
    format!(
        "postgresql://{}:{}@{}:8812/qdb",
        questdb_user(),
        questdb_password(),
        host()
    )
}

/// Optional public HTTPS gateway base for the request/response services
/// (e.g. `https://app.stratai.live`).
///
/// When set (runtime or baked at compile time via `STRATAI_HTTP_BASE_URL`),
/// QuestDB HTTP and the deep-quant service are reached through this single TLS
/// domain by path (`/questdb`, `/deepquant`) instead of raw
/// `http://<host>:<port>`. Empty by default (direct-IP mode / local dev). The
/// PostgreSQL wire (`:8812`) is unaffected — it is a raw TCP protocol and stays
/// on `host()`.
pub fn http_base() -> String {
    resolve(
        std::env::var("STRATAI_HTTP_BASE_URL"),
        option_env!("STRATAI_HTTP_BASE_URL"),
        "",
    )
}

/// Base HTTP URL for QuestDB (no path).
///
/// Priority: explicit `QUESTDB_HTTP_URL` → the public HTTPS gateway
/// (`http_base()/questdb`) when configured → direct `http://<host>:9000`.
/// Behind either gateway this endpoint requires basic auth; callers should
/// attach `questdb_user()` / `questdb_password()` as credentials.
pub fn questdb_http_url() -> String {
    if let Ok(u) = std::env::var("QUESTDB_HTTP_URL") {
        let u = u.trim();
        if !u.is_empty() {
            return u.to_string();
        }
    }
    let base = http_base();
    if !base.is_empty() {
        return format!("{}/questdb", base.trim_end_matches('/'));
    }
    format!("http://{}:9000", host())
}

/// Base URL for the deep-quant FastAPI service (callers append `/run`, `/qa`,
/// `/resume`, `/cancel`).
///
/// Prefers the public HTTPS gateway (`http_base()/deepquant`) when
/// `STRATAI_HTTP_BASE_URL` is set; otherwise the direct `http://<host>:8086`.
/// An explicit `DEEP_QUANT_URL` env var still wins at the call sites.
pub fn deep_quant_url() -> String {
    let base = http_base();
    if !base.is_empty() {
        return format!("{}/deepquant", base.trim_end_matches('/'));
    }
    http_url(8086)
}

/// `ws://<host>:<port>`
pub fn ws_url(port: u16) -> String {
    format!("ws://{}:{}", host(), port)
}

/// Optional public WebSocket gateway base (e.g. `wss://app.stratai.live/ws`).
///
/// When set (runtime or baked at compile time via `STRATAI_WS_BASE_URL`), the
/// live data-plane feeds are routed through this single TLS domain by path
/// instead of raw `ws://<host>:<port>`. Empty by default (direct-IP mode / local
/// dev).
pub fn ws_base() -> String {
    resolve(
        std::env::var("STRATAI_WS_BASE_URL"),
        option_env!("STRATAI_WS_BASE_URL"),
        "",
    )
}

/// Resolve the live-feed WebSocket URL for a named stream.
///
/// Prefers the public WSS gateway (`ws_base()/<name>`, e.g.
/// `wss://app.stratai.live/ws/alpha`) when `STRATAI_WS_BASE_URL` is configured;
/// otherwise falls back to the direct `ws://<host>:<port>` form used for local
/// dev and raw-IP deployments. `name` MUST match the gateway route (see
/// infra/caddy/Caddyfile): `aggregator`, `alpha`, `predictive`, `insight`.
pub fn feed_ws_url(name: &str, port: u16) -> String {
    let base = ws_base();
    if !base.is_empty() {
        return format!("{}/{}", base.trim_end_matches('/'), name);
    }
    ws_url(port)
}

/// `<host>:<port>` (for raw TCP control sockets)
pub fn tcp_addr(port: u16) -> String {
    format!("{}:{}", host(), port)
}

/// `http://<host>:<port>`
pub fn http_url(port: u16) -> String {
    format!("http://{}:{}", host(), port)
}
