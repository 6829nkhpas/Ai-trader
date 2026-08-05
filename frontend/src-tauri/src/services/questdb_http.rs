// src/services/questdb_http.rs — Authenticated QuestDB reads over HTTP(S).
//
// ── Why this exists ─────────────────────────────────────────────────────────
// The desktop app historically read QuestDB through the PostgreSQL wire
// protocol on `:8812`. That has two problems in a shipped thin client:
//
//   1. **Raw PG wire across the public internet.** Port 8812 must be exposed to
//      every end user's machine. It is a plaintext TCP protocol with no TLS in
//      this deployment, so queries and results travel in the clear.
//   2. **Database credentials inside installers.** `questdb_pg_url()` bakes
//      `QUESTDB_USER`/`QUESTDB_PASSWORD` into the binary at compile time, so
//      anyone with an installer holds full wire-protocol access to the database.
//
// Reading through the Caddy gateway (`https://app-api.stratai.live/questdb`)
// fixes both: traffic is TLS-terminated, the credential is scoped to an HTTP
// endpoint that the reverse proxy can rate-limit, log, or revoke centrally, and
// `:8812` no longer needs to be reachable for reads.
//
// ── Scope ───────────────────────────────────────────────────────────────────
// This module covers READS. Writes (the Kite backfill in `history_loader`)
// still use PG wire, because that path relies on parameterised multi-row
// INSERTs that QuestDB's `/exec` endpoint does not accept. Port 8812 therefore
// cannot be closed off entirely yet — but the read path, which is what every
// chart load exercises, no longer depends on it.
//
// ── Endpoint ────────────────────────────────────────────────────────────────
//   GET {questdb_http_url()}/exec?query=<sql>&fmt=json
//   Authorization: Basic <questdb_user:questdb_password>
//
// Success returns `{"columns":[{"name":..,"type":..}],"dataset":[[..],..]}`.
// Failure returns HTTP 400 with `{"error":"..","position":N}`.
//
// In local development `questdb_http_url()` resolves to `http://127.0.0.1:9000`
// and an unconfigured QuestDB simply ignores the Authorization header, so the
// same code path works unchanged on a developer machine.

use serde::Deserialize;

/// How long a single QuestDB read may take before it is abandoned.
///
/// Generous enough for a wide `historical_intraday` scan over a ~80ms link,
/// short enough that a wedged gateway surfaces as an error rather than a
/// permanently spinning chart.
const QUERY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(20);

/// A decoded `/exec` response.
#[derive(Debug, Deserialize)]
struct ExecResponse {
    /// Column metadata, in the same order as each row in `dataset`.
    #[serde(default)]
    columns: Vec<ExecColumn>,
    /// Result rows. Absent on error responses.
    #[serde(default)]
    dataset: Vec<Vec<serde_json::Value>>,
    /// Present only when the query was rejected.
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ExecColumn {
    name: String,
}

/// A result set with column names resolved, so callers can address fields by
/// name instead of by fragile positional index.
#[derive(Debug, Default)]
pub struct QuestRows {
    columns: Vec<String>,
    pub rows: Vec<Vec<serde_json::Value>>,
}

impl QuestRows {
    /// Index of a column by name, or `None` if the query didn't select it.
    fn index_of(&self, column: &str) -> Option<usize> {
        self.columns.iter().position(|c| c == column)
    }

    /// Borrow a cell by row and column name.
    ///
    /// Returns `None` for an unknown column, a short row, or a SQL `NULL` —
    /// callers treat all three the same way (skip the value).
    pub fn get<'a>(&'a self, row: &'a [serde_json::Value], column: &str) -> Option<&'a serde_json::Value> {
        let idx = self.index_of(column)?;
        match row.get(idx) {
            Some(serde_json::Value::Null) | None => None,
            Some(v) => Some(v),
        }
    }

    /// Read a column as `f64`.
    pub fn f64(&self, row: &[serde_json::Value], column: &str) -> Option<f64> {
        self.get(row, column)?.as_f64()
    }

    /// Read a column as `i64`.
    ///
    /// QuestDB returns LONG as a JSON number, but a DOUBLE column that happens
    /// to hold whole numbers arrives as `1234.0`, so fall back to truncating a
    /// float rather than dropping the value.
    pub fn i64(&self, row: &[serde_json::Value], column: &str) -> Option<i64> {
        let v = self.get(row, column)?;
        v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))
    }

    /// Read a TIMESTAMP column as microseconds since the Unix epoch.
    ///
    /// Over `/exec` (unlike PG wire) timestamps arrive as ISO-8601 strings such
    /// as `"2024-01-15T09:15:00.000000Z"`. A numeric value is also accepted so
    /// that `count()`-style or already-µs columns still decode.
    pub fn timestamp_micros(&self, row: &[serde_json::Value], column: &str) -> Option<i64> {
        match self.get(row, column)? {
            serde_json::Value::String(s) => parse_iso_micros(s),
            v => v.as_i64(),
        }
    }
}

/// Parse a QuestDB ISO-8601 timestamp string into microseconds since epoch.
///
/// QuestDB emits UTC with microsecond precision and a `Z` suffix, but the
/// fractional part is elided when zero, so both `...T09:15:00.000000Z` and
/// `...T09:15:00Z` must parse.
fn parse_iso_micros(s: &str) -> Option<i64> {
    // `%.f` matches an optional fractional-second part, including absent.
    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.fZ")
        .ok()
        .map(|dt| dt.and_utc().timestamp_micros())
}

/// Escape a string for use as a SQL literal.
///
/// `/exec` takes SQL as text with no bind-parameter support, so every
/// user-influenced value (symbol names, timeframes) must be escaped at the call
/// site. SQL's own rule applies: a single quote inside a literal is written by
/// doubling it. Callers must wrap the result in quotes themselves — see
/// [`sql_literal`].
pub fn escape_sql_literal(value: &str) -> String {
    value.replace('\'', "''")
}

/// Render a value as a complete, quoted SQL string literal.
pub fn sql_literal(value: &str) -> String {
    format!("'{}'", escape_sql_literal(value))
}

/// Execute a read query against QuestDB through the authenticated HTTP gateway.
///
/// # Errors
/// Returns a human-readable message for transport failures, non-2xx responses,
/// malformed bodies, and QuestDB-reported SQL errors alike — callers only need
/// to decide whether to degrade or surface it.
pub async fn query(sql: &str) -> Result<QuestRows, String> {
    let url = format!("{}/exec", crate::server::questdb_http_url());

    let client = reqwest::Client::builder()
        .timeout(QUERY_TIMEOUT)
        .build()
        .map_err(|e| format!("QuestDB HTTP client build failed: {}", e))?;

    let response = client
        .get(&url)
        // Basic auth for the Caddy gateway fronting QuestDB. An unconfigured
        // local QuestDB ignores the header, so dev is unaffected.
        .basic_auth(crate::server::questdb_user(), Some(crate::server::questdb_password()))
        .query(&[("query", sql), ("fmt", "json")])
        .send()
        .await
        .map_err(|e| format!("QuestDB HTTP request failed: {}", e))?;

    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read QuestDB response body: {}", e))?;

    // A rejected query still returns a JSON body carrying `error`, which is far
    // more useful than the bare status code — so parse before judging status.
    let parsed: ExecResponse = serde_json::from_str(&body).map_err(|e| {
        format!("QuestDB returned HTTP {} with an unparseable body: {}", status, e)
    })?;

    if let Some(err) = parsed.error {
        return Err(format!("QuestDB rejected query: {}", err));
    }
    if !status.is_success() {
        return Err(format!("QuestDB returned HTTP {}", status));
    }

    Ok(QuestRows {
        columns: parsed.columns.into_iter().map(|c| c.name).collect(),
        rows: parsed.dataset,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Building a `QuestRows` the way `query` does, without needing a server.
    fn rows(columns: &[&str], data: Vec<Vec<serde_json::Value>>) -> QuestRows {
        QuestRows {
            columns: columns.iter().map(|c| c.to_string()).collect(),
            rows: data,
        }
    }

    #[test]
    fn escapes_embedded_single_quotes() {
        assert_eq!(escape_sql_literal("O'NEIL"), "O''NEIL");
        assert_eq!(sql_literal("O'NEIL"), "'O''NEIL'");
    }

    /// The escape must neutralise a literal-closing injection attempt rather
    /// than letting it terminate the string early.
    #[test]
    fn escaped_injection_stays_inside_the_literal() {
        let hostile = "x'; DROP TABLE historical_candles; --";
        let literal = sql_literal(hostile);
        assert_eq!(literal, "'x''; DROP TABLE historical_candles; --'");
        // Exactly two unescaped quotes: the ones we added around the value.
        assert!(literal.starts_with('\'') && literal.ends_with('\''));
    }

    #[test]
    fn ordinary_symbols_are_unchanged() {
        assert_eq!(sql_literal("RELIANCE"), "'RELIANCE'");
        assert_eq!(sql_literal("NIFTY 50"), "'NIFTY 50'");
    }

    #[test]
    fn resolves_columns_by_name_not_position() {
        let r = rows(&["close", "ts", "open"], vec![vec![json!(3.0), json!(1), json!(2.0)]]);
        let row = &r.rows[0];
        assert_eq!(r.f64(row, "open"), Some(2.0));
        assert_eq!(r.f64(row, "close"), Some(3.0));
    }

    #[test]
    fn unknown_column_is_none_not_a_panic() {
        let r = rows(&["ts"], vec![vec![json!(1)]]);
        assert_eq!(r.f64(&r.rows[0], "nope"), None);
    }

    /// SQL NULL must read as absent, not as 0 — a fabricated zero price would
    /// silently corrupt a candle.
    #[test]
    fn null_reads_as_none() {
        let r = rows(&["open"], vec![vec![json!(null)]]);
        assert_eq!(r.f64(&r.rows[0], "open"), None);
    }

    /// A row shorter than the column list must not panic.
    #[test]
    fn short_row_is_none() {
        let r = rows(&["ts", "open"], vec![vec![json!(1)]]);
        assert_eq!(r.f64(&r.rows[0], "open"), None);
    }

    #[test]
    fn parses_iso_timestamps_with_and_without_fraction() {
        let with = parse_iso_micros("2024-01-15T09:15:00.000000Z").unwrap();
        let without = parse_iso_micros("2024-01-15T09:15:00Z").unwrap();
        assert_eq!(with, without);
        assert_eq!(with, 1_705_310_100_000_000);
    }

    /// Sub-second precision must survive — 1-minute bars are distinguished by
    /// whole minutes, but SAMPLE BY output and live ticks are not.
    #[test]
    fn preserves_microsecond_precision() {
        let ts = parse_iso_micros("2024-01-15T09:15:00.123456Z").unwrap();
        assert_eq!(ts % 1_000_000, 123_456);
    }

    #[test]
    fn timestamp_column_accepts_string_or_number() {
        let r = rows(
            &["ts"],
            vec![vec![json!("2024-01-15T09:15:00.000000Z")], vec![json!(1_705_310_100_000_000i64)]],
        );
        assert_eq!(
            r.timestamp_micros(&r.rows[0], "ts"),
            r.timestamp_micros(&r.rows[1], "ts")
        );
    }

    #[test]
    fn unparseable_timestamp_is_none() {
        let r = rows(&["ts"], vec![vec![json!("not-a-timestamp")]]);
        assert_eq!(r.timestamp_micros(&r.rows[0], "ts"), None);
    }

    /// QuestDB hands back whole-valued DOUBLEs as floats; volume must still
    /// decode rather than dropping to the `unwrap_or(0)` default.
    #[test]
    fn i64_accepts_a_whole_valued_float() {
        let r = rows(&["volume"], vec![vec![json!(1234.0)], vec![json!(5678i64)]]);
        assert_eq!(r.i64(&r.rows[0], "volume"), Some(1234));
        assert_eq!(r.i64(&r.rows[1], "volume"), Some(5678));
    }

    /// An error body must win over the HTTP status, since QuestDB's message
    /// names the offending token and the status alone does not.
    #[test]
    fn error_body_parses_into_the_error_field() {
        let parsed: ExecResponse =
            serde_json::from_str(r#"{"query":"bad","error":"Invalid column: nope","position":7}"#)
                .unwrap();
        assert_eq!(parsed.error.as_deref(), Some("Invalid column: nope"));
        assert!(parsed.dataset.is_empty());
    }

    #[test]
    fn success_body_parses_columns_and_dataset() {
        let parsed: ExecResponse = serde_json::from_str(
            r#"{"columns":[{"name":"ts","type":"TIMESTAMP"},{"name":"open","type":"DOUBLE"}],
                "dataset":[["2024-01-15T09:15:00.000000Z",100.5]],"count":1}"#,
        )
        .unwrap();
        assert_eq!(parsed.columns.len(), 2);
        assert_eq!(parsed.dataset.len(), 1);
        assert!(parsed.error.is_none());
    }
}
