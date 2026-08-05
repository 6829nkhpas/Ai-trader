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
    resolve_env(runtime, compile, default)
}

/// Public version of [`resolve`] for use by other modules (sentiment, llm).
pub fn resolve_env(runtime: Result<String, std::env::VarError>, compile: Option<&str>, default: &str) -> String {
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
/// (e.g. `https://app-api.stratai.live`).
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

/// Base HTTP URL for the Kite REST proxy (for quotes, instruments, etc.).
///
/// Priority: explicit `KITE_API_URL` → the public HTTPS gateway
/// (`http_base()/kite`) when configured → direct `http://<host>:8087/api/kite`.
pub fn kite_url() -> String {
    if let Ok(u) = std::env::var("KITE_API_URL") {
        let u = u.trim();
        if !u.is_empty() {
            return u.to_string();
        }
    }
    let base = http_base();
    if !base.is_empty() {
        return format!("{}/kite", base.trim_end_matches('/'));
    }
    format!("http://{}:8087/api/kite", host())
}

/// The local-dev default password. A stock, unconfigured QuestDB accepts this,
/// so locally it is correct; behind the public gateway it is guaranteed-wrong.
pub const DEV_QUESTDB_PASSWORD: &str = "quest";

/// True when this build targets the public HTTPS gateway but carries no real
/// gateway password — i.e. the installer will 401 on every authenticated route.
///
/// WHY THIS EXISTS: `questdb_password()` falls back to the local-dev default
/// (`"quest"`) when nothing is baked at compile time. On a developer's machine
/// `lib.rs` loads the repo `.env` at runtime, so the real password is present and
/// the app works. A shipped installer has no `.env`, so it depends ENTIRELY on
/// the compile-time bake. A build made without `QUESTDB_PASSWORD` in scope (a
/// plain `npm run tauri:build`, or CI where the secret is unset — note even
/// `tauri:build:remote` sets only QUESTDB_USER) therefore ships credentials the
/// gateway rejects.
///
/// The failure is silent and looks like missing data, not a config error: Caddy
/// protects `/questdb/*`, `/deepquant/*` and `/kite/*` with basic auth but leaves
/// `/ws/*` open, so live WebSocket panels (order book) keep streaming while the
/// chart, LTP, and technical consensus render empty. That is the exact
/// "works on my machine, blank for everyone else" report this guards against.
///
/// Checked at startup (see `lib.rs`) and by the authenticated-fetch paths so the
/// condition is reported as a credential fault instead of an empty chart.
pub fn gateway_credentials_missing() -> bool {
    // Only meaningful in gateway mode; direct-IP / local dev legitimately uses
    // the dev default against an unconfigured QuestDB.
    if http_base().is_empty() {
        return false;
    }
    let pass = questdb_password();
    pass.trim().is_empty() || pass == DEV_QUESTDB_PASSWORD
}

/// One-line, non-secret description of the resolved backend wiring, for logs.
/// Deliberately reports only whether the password is *present*, never its value.
pub fn config_summary() -> String {
    let http = http_base();
    let ws = ws_base();
    let pass = questdb_password();
    format!(
        "host={} http_base={} ws_base={} questdb_user={} questdb_password={}",
        host(),
        if http.is_empty() { "<direct-ip>" } else { &http },
        if ws.is_empty() { "<direct-ip>" } else { &ws },
        questdb_user(),
        if pass.trim().is_empty() {
            "<empty>"
        } else if pass == DEV_QUESTDB_PASSWORD {
            "<dev-default>"
        } else {
            "<set>"
        },
    )
}

/// `ws://<host>:<port>`
pub fn ws_url(port: u16) -> String {
    format!("ws://{}:{}", host(), port)
}

/// Optional public WebSocket gateway base (e.g. `wss://app-api.stratai.live/ws`).
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
/// `wss://app-api.stratai.live/ws/alpha`) when `STRATAI_WS_BASE_URL` is configured;
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

#[cfg(test)]
mod tests {
    use super::*;

    // `resolve_env` is the pure core of every setting here, so the guard's
    // behaviour is tested through it rather than by mutating process env
    // (which is global and would race across the test binary's threads).

    #[test]
    fn runtime_env_wins_over_compile_and_default() {
        let got = resolve_env(Ok("runtime.example".into()), Some("compile.example"), "default");
        assert_eq!(got, "runtime.example");
    }

    #[test]
    fn blank_runtime_falls_through_to_compile_then_default() {
        // A whitespace-only runtime value must not shadow a real baked value —
        // otherwise an empty CI env var would silently win.
        let got = resolve_env(Ok("   ".into()), Some("compile.example"), "default");
        assert_eq!(got, "compile.example");

        let got = resolve_env(Err(std::env::VarError::NotPresent), Some(""), "default");
        assert_eq!(got, "default", "an empty baked value must fall through");

        let got = resolve_env(Err(std::env::VarError::NotPresent), None, "default");
        assert_eq!(got, "default");
    }

    #[test]
    fn dev_password_constant_matches_the_documented_fallback() {
        // questdb_password()'s default and the guard MUST agree; if this drifts,
        // the guard stops recognizing an unbaked build.
        assert_eq!(DEV_QUESTDB_PASSWORD, "quest");
        let defaulted = resolve_env(
            Err(std::env::VarError::NotPresent),
            None,
            DEV_QUESTDB_PASSWORD,
        );
        assert_eq!(defaulted, DEV_QUESTDB_PASSWORD);
    }

    #[test]
    fn config_summary_never_leaks_the_password() {
        // The summary is logged on every start, so it must report presence only.
        let summary = config_summary();
        let actual = questdb_password();
        assert!(
            !summary.contains(&actual) || actual == DEV_QUESTDB_PASSWORD,
            "config_summary must not embed the real password"
        );
        assert!(
            summary.contains("questdb_password=<set>")
                || summary.contains("questdb_password=<dev-default>")
                || summary.contains("questdb_password=<empty>"),
            "unexpected password rendering: {summary}"
        );
    }

    #[test]
    fn guard_is_inert_in_direct_ip_mode() {
        // With no gateway configured, the dev default is legitimate (a stock
        // local QuestDB accepts it), so the guard must not fire.
        if http_base().is_empty() {
            assert!(!gateway_credentials_missing());
        }
    }
}
