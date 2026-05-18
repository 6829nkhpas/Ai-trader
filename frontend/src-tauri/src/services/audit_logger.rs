// services/audit_logger.rs — Alpha Suite V3 API Audit Logger.
//
// When `ALPHA_TEST_MODE=1` is set, every external API transaction
// (DeepSeek LLM, News API, Market Data, etc.) is captured verbatim and
// appended to a structured JSON-Lines audit log on disk.
//
// The audit report is written to `<src-tauri>/api_audit_report.log` and is
// fully human-readable: each entry is a self-contained JSON object holding
// the endpoint, exact request payload, exact response payload, HTTP status
// code, and a UTC timestamp.
//
// Usage:
//   audit_logger::log_api_transaction(
//       "POST https://api.deepseek.com/v1/chat/completions",
//       &request_json,
//       &response_json,
//       status_code,
//   );
//
// The function is a no-op outside test mode, so it is safe to wrap every
// production HTTP call without performance impact.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

use log::{debug, warn};
use once_cell::sync::Lazy;
use serde_json::{json, Value};

// ── Path Resolution ─────────────────────────────────────────────────────────
//
// Resolved once at first use. We deliberately place the file next to the
// `src-tauri` Cargo manifest so every test run on every machine writes to
// the same predictable location.

static AUDIT_PATH: Lazy<PathBuf> = Lazy::new(|| {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")
        .unwrap_or_else(|_| std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| ".".to_string()));
    PathBuf::from(manifest_dir).join("api_audit_report.log")
});

// Serialize concurrent writers — tests may run multi-threaded.
static AUDIT_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

// ── Public API ──────────────────────────────────────────────────────────────

/// Returns `true` only when the harness is running under
/// `ALPHA_TEST_MODE=1` — the same flag that gates mock data in `lib.rs`.
#[inline]
pub fn is_audit_enabled() -> bool {
    matches!(std::env::var("ALPHA_TEST_MODE"), Ok(v) if v == "1" || v.eq_ignore_ascii_case("true"))
}

/// Append a single API transaction to the audit report.
///
/// # Arguments
/// * `endpoint` — Human-readable identifier (e.g., `"POST /v1/chat/completions"`).
/// * `req`      — The exact request payload sent over the wire.
/// * `res`      — The exact response payload received.
/// * `status`   — HTTP status code returned (use `0` if the call never reached the server).
///
/// # Behaviour
/// * No-op when `ALPHA_TEST_MODE` is not set — zero overhead in production.
/// * Each entry is written as a single pretty-printed JSON object followed by
///   a separator line, making the log readable both for humans and for any
///   downstream JSON-stream parser.
pub fn log_api_transaction(endpoint: &str, req: &Value, res: &Value, status: u16) {
    if !is_audit_enabled() {
        return;
    }

    let entry = json!({
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "endpoint": endpoint,
        "status":   status,
        "request":  req,
        "response": res,
    });

    let serialized = match serde_json::to_string_pretty(&entry) {
        Ok(s) => s,
        Err(e) => {
            warn!("[audit_logger] serialise failed: {}", e);
            return;
        }
    };

    let path = AUDIT_PATH.clone();

    // Lock to keep concurrent appenders from interleaving bytes mid-record.
    let _guard = AUDIT_LOCK.lock().unwrap_or_else(|p| p.into_inner());

    match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            let block = format!(
                "──────────────────────────────────────────────────────────────────\n\
                 {}\n\
                 ──────────────────────────────────────────────────────────────────\n",
                serialized
            );
            if let Err(e) = f.write_all(block.as_bytes()) {
                warn!("[audit_logger] write failed: {}", e);
            } else {
                debug!("[audit_logger] entry appended → {} ({})", endpoint, status);
            }
        }
        Err(e) => warn!(
            "[audit_logger] cannot open {}: {}",
            path.display(),
            e
        ),
    }
}

/// Convenience helper: log a request that produced a transport-level error
/// (e.g., DNS failure, connection refused). Records the error message in
/// place of a response body and uses status code `0`.
pub fn log_api_error(endpoint: &str, req: &Value, error_message: &str) {
    let res = json!({ "error": error_message });
    log_api_transaction(endpoint, req, &res, 0);
}
