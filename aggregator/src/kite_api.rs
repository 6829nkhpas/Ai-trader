// kite_api.rs — Kite Connect REST API proxy for the frontend.
//
// Provides two HTTP endpoints served via axum:
//
//   GET /api/kite/instruments?q=RELI&exchange=NSE
//     Downloads and caches the full Kite instrument CSV for the exchange (24h TTL),
//     then returns up to 15 matching instruments as JSON.
//
//   GET /api/kite/quote?i=NSE:RELIANCE&i=NSE:TCS
//     Proxies to Kite Quote API and returns LTP + OHLC + change data.
//
// All Kite credentials stay server-side — never exposed to the browser.

use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::Query;
use axum::http::StatusCode;
use axum::response::Json;
use axum::Router;
use axum::routing::get;
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;
use tower_http::cors::{Any, CorsLayer};

// ── Types ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Instrument {
    pub instrument_token: u64,
    pub exchange_token: u64,
    pub tradingsymbol: String,
    pub name: String,
    pub last_price: f64,
    /// Expiry as the CSV carries it (`YYYY-MM-DD`), empty for cash instruments.
    /// Needed by the web instrument search, which renders the expiry on F&O rows
    /// (`lib/bridge/webAdapters.ts::rowsToSearchResults`).
    #[serde(default)]
    pub expiry: String,
    /// Strike price; `0.0` for futures and cash instruments.
    #[serde(default)]
    pub strike: f64,
    pub tick_size: f64,
    pub lot_size: u32,
    pub instrument_type: String,
    pub segment: String,
    pub exchange: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize)]
pub struct QuoteData {
    pub symbol: String,
    pub instrument_token: u64,
    /// Last traded price. A quote WITHOUT one is never emitted at all (see the
    /// mapper), because every consumer's primary reading is this field.
    pub last_price: f64,
    /// Session OHLC and volume.
    ///
    /// `Option`, serialized as `null` when Kite omits them — which it does for the
    /// lighter `/quote/ltp` mode, for some indices, and for any malformed row.
    /// These were previously `unwrap_or(0.0)` / `unwrap_or(0)`, so an absent field
    /// became a hard `0.0` that a HUD then rendered as "Open ₹0.00" and a chart
    /// would happily plot. Zero is a real price a real instrument can never trade
    /// at; absent must stay absent, exactly as the `depth` field below already
    /// documents.
    pub open: Option<f64>,
    pub high: Option<f64>,
    pub low: Option<f64>,
    pub close: Option<f64>,
    pub volume: Option<u64>,
    pub oi: Option<u64>,
    /// Percent and absolute change against the previous close.
    ///
    /// `Option` because both are DERIVED from `ohlc.close`: with no previous close
    /// there is no change to report. They used to fall back to `0.0`, which reads
    /// as "unchanged" — a specific, confident claim about the market — when the
    /// truth was "we don't know the previous close".
    pub change: Option<f64>,
    pub net_change: Option<f64>,
    /// Five-level market depth, passed through from Kite verbatim:
    /// `{ buy: [{price, quantity, orders} ×5], sell: [… ×5] }`.
    ///
    /// Kite's `/quote` has always returned this and this handler always dropped
    /// it, which is why the order book had nothing to render — the frontend's
    /// `orderbook-update` event had no producer on the web, and the only other
    /// depth source (`live_ticks.best_bid`/`best_ask`) is level-1 only.
    ///
    /// `Option`, and omitted from the JSON when absent: Kite returns depth for
    /// `/quote` but NOT for the lighter `/quote/ohlc` and `/quote/ltp` modes, and
    /// a synthesised empty ladder would render as "no bids" — indistinguishable
    /// from a genuinely empty book. Absent must stay absent.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub depth: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct InstrumentSearchParams {
    q: Option<String>,
    exchange: Option<String>,
}

// PENDING: GET /api/kite/quote?i=NSE:RELIANCE&i=NSE:TCS
// QuoteParams and QuoteData are the skeleton for the Kite Quote API proxy.
// Once the quote_handler function is added to the router, remove these allows.
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct QuoteParams {
    /// Kite instrument identifiers, e.g. "NSE:RELIANCE"
    #[serde(rename = "i")]
    instruments: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct HistoricalParams {
    /// Instrument token (numeric ID from Kite)
    pub instrument_token: Option<u64>,
    /// Symbol name — used to resolve token from cached instruments if token not provided
    pub symbol: Option<String>,
    /// Interval: "day", "minute", "3minute", "5minute", "10minute", "15minute", "60minute"
    pub interval: Option<String>,
    /// Start date (yyyy-mm-dd). Defaults to 1 year ago.
    pub from: Option<String>,
    /// End date (yyyy-mm-dd). Defaults to today.
    pub to: Option<String>,
}

// ── Shared State ─────────────────────────────────────────────────────────────

/// Cached instrument list for ONE exchange.
///
/// Previously this was a single shared slot with an `exchange: String` tag, so a
/// lookup for the other exchange invalidated it. Since `resolve_token` picks NSE
/// or NFO per symbol, alternating an equity and an option chart evicted the cache
/// on every request and refetched the ~100k-row NFO CSV each time. Keyed per
/// exchange, both lists stay warm.
struct InstrumentCache {
    instruments: Vec<Instrument>,
    fetched_at: Option<Instant>,
    /// Timestamp of the last FAILED fetch attempt (0 instruments or HTTP error).
    /// Used to enforce a 60-second cooldown so a bad token doesn't cause
    /// per-request hammering of the Kite instruments endpoint.
    last_failed_at: Option<Instant>,
}

pub struct KiteApiState {
    api_key: String,
    access_token: String,
    http_client: reqwest::Client,
    /// Per-exchange instrument caches, keyed by the upper-case exchange code.
    cache: RwLock<HashMap<String, InstrumentCache>>,
    /// Prevents thundering herd: only one task can fetch instruments at a time.
    /// Others wait for the first to finish and then read from cache.
    fetch_lock: tokio::sync::Mutex<()>,
    /// Prometheus handle, used to count upstream Kite failures by endpoint.
    metrics: crate::metrics::AggregatorMetrics,
}

const CACHE_TTL: Duration = Duration::from_secs(24 * 60 * 60); // 24 hours

/// Disk path for a persisted instrument cache — survives aggregator restarts.
/// Keyed by exchange for the same reason the memory cache is: one un-keyed file
/// meant NSE and NFO overwrote each other's snapshot on every refresh.
fn disk_cache_path(exchange: &str) -> String {
    format!("instruments_cache_{}.json", exchange.to_lowercase())
}

// Per-symbol token cache: symbol → instrument_token.
// Avoids re-scanning the full instrument CSV on every historical request.
use std::collections::HashMap;
use std::sync::OnceLock;
static TOKEN_CACHE: OnceLock<tokio::sync::RwLock<HashMap<String, u64>>> = OnceLock::new();
fn token_cache() -> &'static tokio::sync::RwLock<HashMap<String, u64>> {
    TOKEN_CACHE.get_or_init(|| tokio::sync::RwLock::new(HashMap::new()))
}

pub fn get_kite_credentials() -> (String, String) {
    let mut api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
    let mut access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();

    if let Ok(mut current_dir) = std::env::current_dir() {
        loop {
            let env_path = current_dir.join(".env");
            if env_path.is_file() {
                if let Ok(content) = std::fs::read_to_string(env_path) {
                    for line in content.lines() {
                        let line = line.trim();
                        if line.starts_with('#') || !line.contains('=') {
                            continue;
                        }
                        let parts: Vec<&str> = line.splitn(2, '=').collect();
                        if parts.len() == 2 {
                            let key = parts[0].trim();
                            let val = parts[1].trim().trim_matches('"').trim_matches('\'');
                            if key == "KITE_API_KEY" && !val.is_empty() {
                                api_key = val.to_string();
                            } else if key == "KITE_ACCESS_TOKEN" && !val.is_empty() {
                                access_token = val.to_string();
                            }
                        }
                    }
                }
                break;
            }
            if !current_dir.pop() {
                break;
            }
        }
    }

    (api_key, access_token)
}

impl KiteApiState {
    fn new(metrics: crate::metrics::AggregatorMetrics) -> Self {
        let (api_key, access_token) = get_kite_credentials();

        if api_key.is_empty() || access_token.is_empty() {
            log::warn!("KITE_API_KEY or KITE_ACCESS_TOKEN not set — Kite REST API will return errors");
        }

        // Pre-load from disk cache on startup so the first request is instant.
        // All three exchanges are loaded: an F&O chart must not have to wait on a
        // cold 100k-row NFO fetch just because the last process only warmed NSE,
        // and instrument search now queries BSE on every keystroke — that is where
        // SENSEX, BANKEX and 71 other indices live — so a cold 13k-row BSE fetch
        // would otherwise stall the first search after every restart.
        let mut cache: HashMap<String, InstrumentCache> = HashMap::new();
        for exchange in ["NSE", "NFO", "BSE"] {
            let instruments = Self::load_disk_cache(exchange);
            if !instruments.is_empty() {
                cache.insert(
                    exchange.to_string(),
                    InstrumentCache {
                        instruments,
                        // `None` marks it as servable-but-stale: `get_instruments`
                        // returns it immediately and still refreshes.
                        fetched_at: None,
                        last_failed_at: None,
                    },
                );
            }
        }

        Self {
            api_key,
            access_token,
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .build()
                .expect("Failed to create HTTP client"),
            cache: RwLock::new(cache),
            fetch_lock: tokio::sync::Mutex::new(()),
            metrics,
        }
    }

    /// Instruments for one exchange, using the same memory → disk → Kite path the
    /// HTTP handlers use.
    ///
    /// Exposed for `option_chain_selector`, which needs the NFO ladder. It reads
    /// through the same cache deliberately: a second fetch path would double the
    /// daily instrument download and could disagree with what the search endpoint
    /// serves.
    pub(crate) async fn instruments_for(&self, exchange: &str) -> Result<Vec<Instrument>, String> {
        self.get_instruments(exchange).await
    }

    /// Latest traded price for one Kite instrument key (e.g. `NSE:NIFTY 50`).
    ///
    /// `Ok(None)` means Kite answered but had no price for the key — a data
    /// outcome, not a fault — so the caller can skip that underlying this cycle
    /// instead of treating it as an outage.
    /// Last traded price for MANY instruments in ONE `/quote` call.
    ///
    /// Kite's quote endpoint accepts a repeated `i=` parameter and its REST limit
    /// is per REQUEST, not per instrument, so asking for N instruments separately
    /// burns N of a 1-per-second budget for no reason. The chain selector reads a
    /// spot price for every configured underlying on each 60-second cycle; issuing
    /// those back-to-back was already close to the ceiling with two underlyings and
    /// would exceed it with nine.
    ///
    /// Instruments absent from the response are absent from the map rather than
    /// zero-filled — "no quote" and "priced at zero" have to stay distinguishable,
    /// because a zero spot would resolve an ATM strike at the bottom of the ladder.
    pub(crate) async fn last_prices_for(
        &self,
        instruments: &[String],
    ) -> Result<HashMap<String, f64>, String> {
        if instruments.is_empty() {
            return Ok(HashMap::new());
        }
        let query: String = instruments
            .iter()
            .map(|i| format!("i={}", urlencoding::encode(i)))
            .collect::<Vec<_>>()
            .join("&");
        let url = format!("https://api.kite.trade/quote?{query}");

        let response = self
            .http_client
            .get(&url)
            .header("X-Kite-Version", "3")
            .header("Authorization", self.auth_header())
            .send()
            .await
            .map_err(|e| {
                self.metrics.kite_api_failed("quote");
                format!("quote transport error: {e}")
            })?;

        if !response.status().is_success() {
            self.metrics.kite_api_failed("quote");
            return Err(format!("quote returned HTTP {}", response.status().as_u16()));
        }

        let body: serde_json::Value = response
            .json()
            .await
            .map_err(|e| format!("quote decode error: {e}"))?;

        Ok(parse_quote_prices(&body))
    }

    fn auth_header(&self) -> String {
        let (api_key, access_token) = get_kite_credentials();
        let final_key = if api_key.is_empty() { &self.api_key } else { &api_key };
        let final_token = if access_token.is_empty() { &self.access_token } else { &access_token };
        format!("token {}:{}", final_key, final_token)
    }

    /// Load one exchange's instrument cache from disk. Empty vec on any error.
    fn load_disk_cache(exchange: &str) -> Vec<Instrument> {
        match std::fs::read_to_string(disk_cache_path(exchange)) {
            Ok(json) => {
                match serde_json::from_str::<Vec<Instrument>>(&json) {
                    Ok(instruments) if !instruments.is_empty() => {
                        log::info!(
                            "[Kite API] Loaded {} {} instruments from disk cache",
                            instruments.len(),
                            exchange
                        );
                        instruments
                    }
                    _ => Vec::new(),
                }
            }
            Err(_) => Vec::new(),
        }
    }

    /// Save one exchange's instrument list to disk for persistence across restarts.
    fn save_disk_cache(exchange: &str, instruments: &[Instrument]) {
        if let Ok(json) = serde_json::to_string(instruments) {
            if let Err(e) = std::fs::write(disk_cache_path(exchange), &json) {
                log::warn!("[Kite API] Failed to write {} disk cache: {}", exchange, e);
            } else {
                log::info!(
                    "[Kite API] Persisted {} {} instruments to disk cache",
                    instruments.len(),
                    exchange
                );
            }
        }
    }

    /// Resolve a tradingsymbol to its instrument_token.
    /// Detects F&O symbols (digits + CE/PE/FUT suffix) and queries NFO directly.
    /// Uses a fast in-process cache before falling back to get_instruments.
    pub async fn resolve_token(&self, symbol: &str) -> Option<u64> {
        let sym = symbol.trim().to_uppercase();

        // Fast path: per-symbol memory cache
        {
            let cache = token_cache().read().await;
            if let Some(&token) = cache.get(&sym) {
                return Some(token);
            }
        }

        // Detect exchange: F&O symbols contain digits + end with CE/PE/FUT
        let is_nfo = sym.bytes().any(|b| b.is_ascii_digit())
            && (sym.ends_with("CE") || sym.ends_with("PE") || sym.ends_with("FUT"));
        let exchange = if is_nfo { "NFO" } else { "NSE" };

        // Direct lookup — no wasteful fallback to the wrong exchange
        let instruments = self.get_instruments(exchange).await.ok()?;
        let inst = instruments.iter().find(|i| i.tradingsymbol.to_uppercase() == sym)?;
        let token = inst.instrument_token;
        token_cache().write().await.insert(sym, token);
        Some(token)
    }

    /// Fetch instruments from Kite and cache them. Returns cached data if fresh.
    /// Priority: memory cache → disk cache → Kite API (with 429 back-off).
    async fn get_instruments(&self, exchange: &str) -> Result<Vec<Instrument>, String> {
        // ── Level 1: Memory cache (fast path) ────────────────────────────
        {
            let cache = self.cache.read().await;
            if let Some(entry) = cache.get(exchange) {
                if let Some(fetched_at) = entry.fetched_at {
                    if fetched_at.elapsed() < CACHE_TTL && !entry.instruments.is_empty() {
                        return Ok(entry.instruments.clone());
                    }
                }
                // Disk cache loaded on startup has fetched_at=None.
                // Serve it immediately but allow a background refresh.
                if entry.fetched_at.is_none() && !entry.instruments.is_empty() {
                    log::info!(
                        "[Kite API] Serving {} {} instruments from disk cache (will refresh)",
                        entry.instruments.len(),
                        exchange
                    );
                    return Ok(entry.instruments.clone());
                }
            }
        }
        // ── Level 1b: Cooldown check ─────────────────────────────────
        // If the last API attempt failed (0 instruments / HTTP error) less than
        // 60 seconds ago, return immediately with whatever cache we have.
        // This prevents per-second hammering when the Kite token is invalid.
        // Per exchange: an NFO failure must not put NSE into cooldown.
        {
            let cache = self.cache.read().await;
            if let Some(entry) = cache.get(exchange) {
                if let Some(failed_at) = entry.last_failed_at {
                    const COOLDOWN: Duration = Duration::from_secs(60);
                    if failed_at.elapsed() < COOLDOWN {
                        if !entry.instruments.is_empty() {
                            log::debug!("[Kite API] Cooldown active — serving stale {} cache", exchange);
                            return Ok(entry.instruments.clone());
                        } else {
                            return Err(format!(
                                "Kite {} instruments unavailable (cooldown {}s remaining)",
                                exchange,
                                COOLDOWN.saturating_sub(failed_at.elapsed()).as_secs()
                            ));
                        }
                    }
                }
            }
        }

        let _guard = self.fetch_lock.lock().await;

        // Double-check after acquiring lock
        {
            let cache = self.cache.read().await;
            if let Some(entry) = cache.get(exchange) {
                if let Some(fetched_at) = entry.fetched_at {
                    if fetched_at.elapsed() < CACHE_TTL && !entry.instruments.is_empty() {
                        return Ok(entry.instruments.clone());
                    }
                }
            }
        }

        // ── Level 2: Kite API fetch with 429-aware backoff ────────────────
        log::info!("[Kite API] Fetching instruments for exchange: {}", exchange);

        let url = format!("https://api.kite.trade/instruments/{}", exchange);
        let mut last_err = String::new();

        for attempt in 0..3u32 {
            if attempt > 0 {
                // Exponential backoff: 4s, 8s — longer than before to respect Kite limits
                let backoff = Duration::from_secs(4 * (1u64 << (attempt - 1)));
                log::warn!("[Kite API] Retry #{} after {}s backoff", attempt, backoff.as_secs());
                tokio::time::sleep(backoff).await;
            }

            let response = match self.http_client
                .get(&url)
                .header("X-Kite-Version", "3")
                .header("Authorization", self.auth_header())
                .send()
                .await
            {
                Ok(r) => r,
                Err(e) => {
                    last_err = format!("Kite HTTP request failed: {}", e);
                    continue;
                }
            };

            if response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS {
                last_err = "Kite API rate limited (429)".to_string();
                log::warn!("[Kite API] 429 Too Many Requests — will retry");
                continue;
            }

            if !response.status().is_success() {
                let status = response.status();
                let body = response.text().await.unwrap_or_default();
                last_err = format!("Kite API returned {}: {}", status, body);
                continue;
            }

            let csv_text = match response.text().await {
                Ok(t) => t,
                Err(e) => {
                    last_err = format!("Failed to read response body: {}", e);
                    continue;
                }
            };

            let instruments = parse_instruments_csv(&csv_text);

            // Validate: NSE should have thousands of instruments.
            // 0 (or very few) means the response was an error JSON, HTML, or
            // empty body — NOT the real instruments CSV.
            if instruments.len() < 100 {
                last_err = format!(
                    "Kite API returned only {} instruments (expected >100). Response snippet: {:?}",
                    instruments.len(),
                    &csv_text[..csv_text.len().min(200)]
                );
                log::warn!("[Kite API] {}", last_err);
                // Mark failed — 60s cooldown kicks in below
                continue;
            }

            log::info!("[Kite API] Fetched {} instruments for {}", instruments.len(), exchange);

            // Persist to disk so next restart is instant
            Self::save_disk_cache(exchange, &instruments);

            // Update memory cache — clear any previous failure mark
            {
                let mut cache = self.cache.write().await;
                cache.insert(
                    exchange.to_string(),
                    InstrumentCache {
                        instruments: instruments.clone(),
                        fetched_at: Some(Instant::now()),
                        last_failed_at: None, // clear cooldown on success
                    },
                );
            }

            return Ok(instruments);
        }

        // All attempts failed — set the cooldown for THIS exchange so we don't
        // hammer Kite, without taking the other exchange down with it.
        log::warn!(
            "[Kite API] {} instruments fetch failed after 3 attempts: {}",
            exchange, last_err
        );
        {
            let mut cache = self.cache.write().await;
            let entry = cache.entry(exchange.to_string()).or_insert_with(|| InstrumentCache {
                instruments: Vec::new(),
                fetched_at: None,
                last_failed_at: None,
            });
            entry.last_failed_at = Some(Instant::now());

            if !entry.instruments.is_empty() {
                log::warn!(
                    "[Kite API] All retries failed — serving stale {} cache ({} instruments). Error: {}",
                    exchange, entry.instruments.len(), last_err
                );
                return Ok(entry.instruments.clone());
            }
        }

        Err(last_err)
    }
}

/// Parse a single CSV line respecting RFC-4180 quoting (fields may contain commas).
/// Kite's instruments CSV wraps `name` fields like "DR. REDDY'S LABS, LTD" in quotes.
/// A naive split(',') would shatter those into extra columns, shifting every index right.
fn parse_csv_line(line: &str) -> Vec<String> {
    let mut fields = Vec::new();
    let mut current = String::new();
    let mut in_quotes = false;
    let chars: Vec<char> = line.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '"' {
            if in_quotes && i + 1 < chars.len() && chars[i + 1] == '"' {
                // Escaped quote inside quoted field
                current.push('"');
                i += 1;
            } else {
                in_quotes = !in_quotes;
            }
        } else if c == ',' && !in_quotes {
            fields.push(current.trim().to_string());
            current = String::new();
        } else {
            current.push(c);
        }
        i += 1;
    }
    fields.push(current.trim().to_string());
    fields
}

/// Parse the Kite instruments CSV into a Vec<Instrument>.
///
/// Keeps the tradeable instrument types and drops the rest, so search results
/// stay clean without the parser needing to know which exchange it is reading:
/// the NSE CSV only contains `EQ`/`INDEX` rows and the NFO CSV only `CE`/`PE`/`FUT`.
///
/// The derivative types used to be dropped here unconditionally, which made the
/// NFO parse yield ~0 rows. That tripped the `instruments.len() < 100` sanity
/// guard in `get_instruments`, so `resolve_token` could never resolve an F&O
/// tradingsymbol over HTTP — every option/future chart on the browser path failed,
/// and the 60-second failure cooldown then suppressed retries.
///
/// Kite CSV columns (0-indexed):
///   0  instrument_token
///   1  exchange_token
///   2  tradingsymbol
///   3  name           ← may contain commas inside quotes
///   4  last_price
///   5  expiry
///   6  strike
///   7  tick_size
///   8  lot_size
///   9  instrument_type  ← "EQ", "INDEX", "FUT", "CE", "PE" etc.
///   10 segment
///   11 exchange
/// Pure: pull `instrument -> last_price` out of a Kite `/quote` response body.
///
/// An instrument whose quote is missing, non-numeric, zero, or non-finite is
/// OMITTED rather than mapped to 0.0. The caller resolves an ATM strike from this
/// price, and a zero would silently pick the bottom of the ladder — a wrong
/// contract presented with the same confidence as a right one. Absent means
/// absent, and the caller skips the underlying for that cycle.
fn parse_quote_prices(body: &serde_json::Value) -> HashMap<String, f64> {
    let mut out = HashMap::new();
    if let Some(data) = body.get("data").and_then(|d| d.as_object()) {
        for (key, quote) in data {
            if let Some(price) = quote
                .get("last_price")
                .and_then(|p| p.as_f64())
                .filter(|p| p.is_finite() && *p > 0.0)
            {
                out.insert(key.clone(), price);
            }
        }
    }
    out
}

fn parse_instruments_csv(csv: &str) -> Vec<Instrument> {
    /// Instrument types worth serving. Anything else (bonds, ETF variants Kite
    /// tags separately, exercise rows) is noise in a symbol search.
    const KEEP_TYPES: [&str; 5] = ["EQ", "INDEX", "CE", "PE", "FUT"];

    let mut instruments = Vec::new();
    let mut lines = csv.lines();

    // Skip header row
    lines.next();

    for line in lines {
        if line.trim().is_empty() {
            continue;
        }

        let cols = parse_csv_line(line);

        // Need at least 12 columns (0..=11)
        if cols.len() < 12 {
            continue;
        }

        // col 9 = instrument_type
        let instrument_type = cols[9].as_str();
        if !KEEP_TYPES.contains(&instrument_type) {
            continue;
        }

        let instrument = Instrument {
            instrument_token: cols[0].parse().unwrap_or(0),
            exchange_token:   cols[1].parse().unwrap_or(0),
            tradingsymbol:    cols[2].clone(),
            name:             cols[3].clone(),
            last_price:       cols[4].parse().unwrap_or(0.0),
            expiry:           cols[5].clone(),                // empty for cash
            strike:           cols[6].parse().unwrap_or(0.0), // 0 for FUT / cash
            tick_size:        cols[7].parse().unwrap_or(0.0), // col 7, NOT 5
            lot_size:         cols[8].parse().unwrap_or(0),   // col 8, NOT 6
            instrument_type:  instrument_type.to_string(),
            segment:          cols[10].clone(),
            exchange:         cols[11].clone(),
        };

        // Skip instruments with no tradingsymbol (malformed rows)
        if instrument.tradingsymbol.is_empty() || instrument.instrument_token == 0 {
            continue;
        }

        instruments.push(instrument);
    }

    instruments
}


// ── Instrument search (pure) ─────────────────────────────────────────────────
//
// Kept as free functions over a slice so they are unit-testable without an axum
// state or a live Kite fetch. They deliberately mirror
// `frontend/src-tauri/src/commands/instruments.rs` — the desktop path searches
// the local SQLite masters (`search_in_db` + `search_nfo_tokenized`) while the
// browser path comes through here, and the two must agree or the same keystroke
// yields different results on desktop and on the website.

/// Cash-side result cap.
///
/// Was 10, to match the desktop equity query's `LIMIT 10`, and that was far too
/// tight for the index families. NSE's master carries 136 rows in the `INDICES`
/// segment and BSE another 73, and they share long common prefixes with a crowd
/// of ETFs: searching "NIFTY" scored 10 results out of well over a hundred
/// candidates and `NIFTY BANK` was not among them — it lost the length tiebreak
/// to `NIFTY 50`, `NIFTY EV`, `NIFTY IT`, `NIFTY 100`, `NIFTY 200` and to plain
/// ETFs like `NIFTY1` and `NIFTYETF`. The most-traded index in the country was
/// unreachable from the obvious query.
///
/// 50 is chosen to comfortably clear one index family (NIFTY has ~40 rows in the
/// NSE master) while still bounding the payload; the ranking below is what puts
/// the right rows at the top, and this only decides how many survive.
const EQ_SEARCH_LIMIT: usize = 50;
/// Derivative-side result cap — matches the desktop NFO query's `LIMIT 25`.
const FNO_SEARCH_LIMIT: usize = 25;

/// Normalize an option-type alias to its canonical form.
///
/// Same table as `commands/instruments.rs::normalize_option_type`, so "NIFTY
/// 24000 PUT" and "NIFTY 24000 PE" behave identically on both transports.
fn normalize_option_type(token: &str) -> Option<&'static str> {
    match token {
        "CE" | "CALL" => Some("CE"),
        "PE" | "PUT" => Some("PE"),
        "FUT" | "FUTURE" | "FUTURES" => Some("FUT"),
        _ => None,
    }
}

/// Search cash instruments (`EQ` / `INDEX`).
///
/// Ordering mirrors the desktop SQL: prefix matches on tradingsymbol first, then
/// shorter tradingsymbols, so "REL" surfaces `RELIANCE` above `RELINFRA`. A
/// `name` match (e.g. "Reliance Industries") is admitted but ranks after the
/// symbol prefixes, exactly as `CASE WHEN tradingsymbol LIKE ?1 THEN 0 ELSE 1`
/// does.
/// Whether an instrument row is an exchange INDEX rather than a tradable scrip.
///
/// Kite puts indices in their own segment (`INDICES`) on both NSE and BSE, so the
/// segment is the authoritative test. Matching on the NAME instead needs a
/// hand-maintained list and silently misfiles every index nobody remembered to
/// add — which is what the frontend's `isIndex` did.
fn is_index_row(inst: &Instrument) -> bool {
    inst.segment.eq_ignore_ascii_case("INDICES")
}

fn search_cash(instruments: &[Instrument], query: &str) -> Vec<Instrument> {
    let mut scored: Vec<(u8, usize, &Instrument)> = Vec::new();

    for inst in instruments {
        let sym = inst.tradingsymbol.to_uppercase();
        let is_index = is_index_row(inst);
        // Ranking, best first. The index tiers exist because an index shares its
        // prefix with a pile of ETFs that track it: "NIFTY" matches `NIFTY1`,
        // `NIFTYADD`, `NIFTYETF`, `NIFTYBEES` and ~40 rows in the INDICES segment,
        // and a plain prefix-then-shortest ordering handed the top slots to the
        // ETFs. Someone typing an index name wants the index.
        let rank = if sym == query {
            0 // exact ticker — never outranked
        } else if is_index && sym.starts_with(query) {
            1 // NIFTY 50 / NIFTY BANK for "NIFTY", SENSEX for "SENS"
        } else if is_index && sym.contains(query) {
            2 // "BSE BANKEX" for "BANKEX"
        } else if sym.starts_with(query) {
            3
        } else if sym.contains(query) || inst.name.to_uppercase().contains(query) {
            4
        } else {
            continue;
        };
        scored.push((rank, sym.len(), inst));
    }

    scored.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)).then(a.2.tradingsymbol.cmp(&b.2.tradingsymbol)));
    scored.truncate(EQ_SEARCH_LIMIT);
    scored.into_iter().map(|(_, _, i)| i.clone()).collect()
}

/// Token-aware derivative search (`CE` / `PE` / `FUT`).
///
/// Splits the query on whitespace and classifies every token as an option type,
/// a numeric strike, or free text — the port of `search_nfo_tokenized`. All
/// tokens must match (AND), so "NIFTY 24000 CE" narrows instead of widening.
///
/// Results are sorted by (expiry, strike) and only THEN truncated. The previous
/// implementation broke out of the scan at 30 candidates and truncated to 15,
/// which on the ~100k-row NFO list returned whichever strikes happened to appear
/// first in the CSV — so a search for `NIFTY` never showed a near-month contract.
fn search_derivatives(instruments: &[Instrument], query: &str) -> Vec<Instrument> {
    let mut text_tokens: Vec<&str> = Vec::new();
    let mut option_type_filter: Option<&str> = None;
    let mut strike_prefix: Option<String> = None;

    for token in query.split_whitespace() {
        if let Some(ot) = normalize_option_type(token) {
            option_type_filter = Some(ot);
        } else if let Ok(num) = token.parse::<f64>() {
            // Prefix match on the integer strike, so "2400" finds 24000 / 24050
            // — the desktop `CAST(strike AS INTEGER) AS TEXT LIKE '2400%'`.
            strike_prefix = Some(format!("{}", num as i64));
        } else {
            text_tokens.push(token);
        }
    }

    let mut matches: Vec<&Instrument> = Vec::new();

    for inst in instruments {
        let sym = inst.tradingsymbol.to_uppercase();
        let name = inst.name.to_uppercase();

        // Every text token must appear in the tradingsymbol or the underlying
        // name (`name` is what `derive_underlying` keys `nfo_instruments` on).
        if !text_tokens.iter().all(|t| sym.contains(t) || name.contains(t)) {
            continue;
        }
        if let Some(ot) = option_type_filter {
            if inst.instrument_type != ot {
                continue;
            }
        }
        if let Some(prefix) = &strike_prefix {
            if !format!("{}", inst.strike as i64).starts_with(prefix.as_str()) {
                continue;
            }
        }
        matches.push(inst);
    }

    // Sort the FULL candidate set before capping: nearest expiry first, then
    // ascending strike, then tradingsymbol so the order is total (two contracts
    // can share expiry+strike across CE/PE).
    matches.sort_by(|a, b| {
        a.expiry
            .cmp(&b.expiry)
            .then(a.strike.partial_cmp(&b.strike).unwrap_or(std::cmp::Ordering::Equal))
            .then(a.tradingsymbol.cmp(&b.tradingsymbol))
    });
    matches.truncate(FNO_SEARCH_LIMIT);
    matches.into_iter().cloned().collect()
}

/// Dispatch to the cash or derivative search based on what the exchange holds.
///
/// The caller asks per exchange (`webAdapters.ts::search_instruments` fires NSE
/// and NFO in parallel and concatenates), so the split is by exchange rather
/// than by inspecting each row.
fn search_instrument_list(instruments: &[Instrument], query: &str, exchange: &str) -> Vec<Instrument> {
    if exchange == "NFO" || exchange == "BFO" {
        search_derivatives(instruments, query)
    } else {
        search_cash(instruments, query)
    }
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/kite/instruments?q=RELI&exchange=NSE
///
/// `q` accepts multi-token derivative queries ("NIFTY 24000 CE"); see
/// `search_derivatives`.
async fn instruments_search(
    Query(params): Query<InstrumentSearchParams>,
    state: axum::extract::State<Arc<KiteApiState>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let query = params.q.unwrap_or_default().trim().to_uppercase();
    let exchange = params.exchange.unwrap_or_else(|| "NSE".to_string()).to_uppercase();

    if query.is_empty() {
        return Ok(Json(serde_json::json!({ "results": [] })));
    }

    let instruments = state.get_instruments(&exchange).await.map_err(|e| {
        state.metrics.kite_api_failed("instruments");
        log::error!("[Kite instruments] {}", e);
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e, "results": [] })),
        )
    })?;

    let results = search_instrument_list(&instruments, &query, &exchange);

    Ok(Json(serde_json::json!({ "results": results })))
}

/// GET /api/kite/quote?i=NSE:RELIANCE&i=NSE:TCS
///
/// Note: axum doesn't natively support repeated query params with the same key,
/// so we accept a comma-separated list: ?i=NSE:RELIANCE,NSE:TCS
async fn quote_handler(
    axum::extract::RawQuery(raw_query): axum::extract::RawQuery,
    state: axum::extract::State<Arc<KiteApiState>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    // Parse repeated `i=` params from raw query string
    let raw = raw_query.unwrap_or_default();
    let instruments: Vec<String> = raw
        .split('&')
        .filter_map(|pair| {
            let mut parts = pair.splitn(2, '=');
            let key = parts.next()?;
            let val = parts.next()?;
            if key == "i" {
                Some(urlencoding::decode(val).unwrap_or_default().to_string())
            } else {
                None
            }
        })
        .collect();

    if instruments.is_empty() {
        return Ok(Json(serde_json::json!({ "quotes": [] })));
    }

    // Build Kite query string
    let query_string: String = instruments
        .iter()
        .map(|i| format!("i={}", urlencoding::encode(i)))
        .collect::<Vec<_>>()
        .join("&");

    let url = format!("https://api.kite.trade/quote?{}", query_string);

    let response = state
        .http_client
        .get(&url)
        .header("X-Kite-Version", "3")
        .header("Authorization", state.auth_header())
        .send()
        .await
        .map_err(|e| {
            state.metrics.kite_api_failed("quote");
            log::error!("[Kite quote] HTTP error: {}", e);
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": e.to_string(), "quotes": [] })),
            )
        })?;

    if !response.status().is_success() {
        let status = response.status().as_u16();
        let body = response.text().await.unwrap_or_default();
        state.metrics.kite_api_failed("quote");
        log::error!("[Kite quote] API returned {}: {}", status, body);
        return Err((
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(serde_json::json!({ "error": format!("Kite API error: {}", status), "quotes": [] })),
        ));
    }

    let json: serde_json::Value = response.json().await.map_err(|e| {
        state.metrics.kite_api_failed("quote");
        log::error!("[Kite quote] JSON parse error: {}", e);
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "Failed to parse Kite response", "quotes": [] })),
        )
    })?;

    let data = json.get("data").cloned().unwrap_or(serde_json::json!({}));
    let data_map = data.as_object().cloned().unwrap_or_default();

    let quotes: Vec<QuoteData> = data_map
        .iter()
        // `filter_map`, not `map`: a row with no usable `last_price` is DROPPED
        // rather than emitted with a zero price.
        //
        // It used to be `unwrap_or(0.0)`, so an instrument Kite returned without a
        // last price — halted, not yet traded today, or simply a malformed row —
        // was published as trading at ₹0.00. Every consumer displays this field as
        // the live price, so a fabricated zero is about as wrong as a market data
        // point can be. Omitting the row instead is a case they all already handle:
        // the watchlist keeps its last known price, the order book keeps its last
        // ladder and drops its live flag, and the ticker simply skips the symbol.
        .filter_map(|(key, value)| {
            let symbol = key.split(':').nth(1).unwrap_or(key).to_string();

            let last_price = match value.get("last_price").and_then(|v| v.as_f64()) {
                Some(p) if p.is_finite() && p > 0.0 => p,
                _ => {
                    log::warn!(
                        "[Kite quote] Dropping {} — no usable last_price in the upstream response",
                        symbol
                    );
                    return None;
                }
            };

            let ohlc = value.get("ohlc").cloned().unwrap_or(serde_json::json!({}));
            let finite = |v: Option<f64>| v.filter(|n| n.is_finite());
            let prev_close = finite(ohlc.get("close").and_then(|v| v.as_f64())).filter(|c| *c > 0.0);

            // Both change figures exist only when there is a previous close to
            // measure against.
            let net_change = prev_close.map(|pc| last_price - pc);
            let pct_change = prev_close
                .zip(net_change)
                .map(|(pc, nc)| (nc / pc) * 100.0);
            let round2 = |v: Option<f64>| v.map(|n| (n * 100.0).round() / 100.0);

            Some(QuoteData {
                symbol,
                instrument_token: value.get("instrument_token").and_then(|v| v.as_u64()).unwrap_or(0),
                last_price,
                open: finite(ohlc.get("open").and_then(|v| v.as_f64())),
                high: finite(ohlc.get("high").and_then(|v| v.as_f64())),
                low: finite(ohlc.get("low").and_then(|v| v.as_f64())),
                close: prev_close,
                volume: value.get("volume").and_then(|v| v.as_u64()),
                oi: value.get("oi").and_then(|v| v.as_u64()),
                change: round2(pct_change),
                net_change: round2(net_change),
                // Passed through verbatim rather than reshaped: the order book
                // renders `{price, quantity, orders}` directly, and re-modelling it
                // here would add a second place for the field names to drift from
                // Kite's. Absent (LTP/OHLC modes) stays absent — see the field doc.
                depth: value.get("depth").cloned().filter(|d| !d.is_null()),
            })
        })
        .collect();

    Ok(Json(serde_json::json!({ "quotes": quotes })))
}

/// GET /api/kite/historical?symbol=TCS&interval=day&from=2024-01-01&to=2025-05-13
///
/// Fetches historical OHLCV candles from the Kite Historical API.
/// Resolves the instrument_token from the cached instruments list using the symbol.
/// Falls back to `instrument_token` query param if provided directly.
///
/// Returns: `{ "candles": [ { "time": <unix_sec>, "open": ..., "high": ..., "low": ..., "close": ..., "volume": ... } ] }`
async fn historical_handler(
    Query(params): Query<HistoricalParams>,
    state: axum::extract::State<Arc<KiteApiState>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let symbol = params.symbol.unwrap_or_default().trim().to_uppercase();
    let interval = params.interval.unwrap_or_else(|| "day".to_string());

    // Resolve instrument_token: either provided directly or looked up from symbol
    let token: u64 = if let Some(t) = params.instrument_token {
        t
    } else if !symbol.is_empty() {
        // Use the cached resolve_token helper — avoids re-scanning the full
        // instruments list on every request after the first lookup.
        match state.resolve_token(&symbol).await {
            Some(t) => t,
            None => {
                state.metrics.kite_api_failed("historical");
                log::error!("[Kite historical] Could not resolve token for symbol '{}'", symbol);
                // Name the exchange actually searched, and say what a miss means
                // for a derivative. This message said "NSE instruments" for every
                // symbol, so an F&O miss read as a lookup bug in the wrong
                // exchange when the real answer is usually that the contract has
                // expired — Kite drops expired contracts from the NFO master, so
                // no amount of retrying will ever produce a candle for it.
                let is_derivative = symbol.bytes().any(|b| b.is_ascii_digit())
                    && (symbol.ends_with("CE") || symbol.ends_with("PE") || symbol.ends_with("FUT"));
                let error = if is_derivative {
                    format!(
                        "Contract '{symbol}' is not in the NFO instrument master — it has most \
                         likely expired, or was never listed. Expired contracts are removed by \
                         the exchange and have no further history."
                    )
                } else {
                    format!("Symbol '{symbol}' not found in NSE instruments")
                };
                return Err((
                    StatusCode::NOT_FOUND,
                    Json(serde_json::json!({ "error": error, "candles": [] })),
                ));
            }
        }
    } else {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "Either 'symbol' or 'instrument_token' is required", "candles": [] })),
        ));
    };

    // Date range: default to 1 year of data
    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let one_year_ago = (chrono::Utc::now() - chrono::Duration::days(365))
        .format("%Y-%m-%d")
        .to_string();

    let from_date = params.from.unwrap_or(one_year_ago);
    let to_date = params.to.unwrap_or(today);

    log::info!(
        "[Kite historical] Fetching {} (token {}) interval={} from={} to={}",
        symbol, token, interval, from_date, to_date
    );

    let url = format!(
        "https://api.kite.trade/instruments/historical/{}/{}",
        token, interval
    );

    let response = state
        .http_client
        .get(&url)
        .query(&[("from", &from_date), ("to", &to_date)])
        .header("X-Kite-Version", "3")
        .header("Authorization", state.auth_header())
        .send()
        .await
        .map_err(|e| {
            state.metrics.kite_api_failed("historical");
            log::error!("[Kite historical] HTTP error: {}", e);
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": e.to_string(), "candles": [] })),
            )
        })?;

    if !response.status().is_success() {
        let status = response.status().as_u16();
        let body = response.text().await.unwrap_or_default();
        state.metrics.kite_api_failed("historical");
        log::error!("[Kite historical] API returned {}: {}", status, body);
        return Err((
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(serde_json::json!({ "error": format!("Kite API error {}: {}", status, body), "candles": [] })),
        ));
    }

    let json: serde_json::Value = response.json().await.map_err(|e| {
        state.metrics.kite_api_failed("historical");
        log::error!("[Kite historical] JSON parse error: {}", e);
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "Failed to parse Kite response", "candles": [] })),
        )
    })?;

    // Kite response: { "status": "success", "data": { "candles": [[ts, o, h, l, c, vol], ...] } }
    let candles_raw = json
        .get("data")
        .and_then(|d| d.get("candles"))
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();

    let candles: Vec<serde_json::Value> = candles_raw
        .iter()
        .filter_map(|row| {
            let arr = row.as_array()?;
            if arr.len() < 6 {
                return None;
            }
            // Parse timestamp: Kite returns ISO 8601 string like "2024-01-15T00:00:00+0530"
            let ts_str = arr[0].as_str().unwrap_or_default();
            let time_sec = chrono::DateTime::parse_from_str(ts_str, "%Y-%m-%dT%H:%M:%S%z")
                .or_else(|_| chrono::DateTime::parse_from_rfc3339(ts_str))
                .map(|dt| dt.timestamp())
                .unwrap_or(0);

            if time_sec == 0 {
                return None;
            }

            // A candle is only emitted when all four prices are really present.
            //
            // These were `unwrap_or(0.0)`, which turned a malformed row into a
            // candle priced at zero. That is far worse than a missing bar: it is
            // charted as a catastrophic wick to zero, and every indicator computed
            // over the series — SMA, RSI, ATR, Bollinger, the regression engines —
            // is skewed by it. A gap in the series is honest and every consumer
            // already tolerates one; a zero-priced bar is silent corruption.
            //
            // Volume is treated differently on purpose: an index legitimately
            // reports no volume, so it stays optional and serializes as null rather
            // than disqualifying an otherwise-good bar.
            let price = |i: usize| arr[i].as_f64().filter(|p| p.is_finite() && *p > 0.0);
            let (open, high, low, close) = (price(1)?, price(2)?, price(3)?, price(4)?);

            Some(serde_json::json!({
                "time": time_sec,
                "open": open,
                "high": high,
                "low": low,
                "close": close,
                "volume": arr[5].as_u64(),
            }))
        })
        .collect();

    log::info!(
        "[Kite historical] {} — {} candles returned (interval={})",
        symbol, candles.len(), interval
    );

    Ok(Json(serde_json::json!({ "candles": candles })))
}

// ── Server ───────────────────────────────────────────────────────────────────

/// Build and start the Kite REST API server on the given port.
/// Call this from main.rs via `tokio::spawn`.
pub async fn run_kite_api_server(port: &str, metrics: crate::metrics::AggregatorMetrics) {
    let state = Arc::new(KiteApiState::new(metrics));

    // Option-chain selection, moved off the retired desktop shell. The selector
    // needs exactly two things this state already owns — the NFO instrument cache
    // and an authenticated Kite quote path — so it runs here rather than as a
    // separate service. Without it, nothing tells the ingestion service which
    // strikes to track and `option_chain_snapshots` stops filling, which is what
    // the website's entire F&O workspace reads.
    tokio::spawn(crate::option_chain_selector::run(state.clone()));

    // Equity/index tick subscriptions. The ingestion service boots with an EMPTY
    // instrument map and streams only the tokens pushed to its control port — so
    // without this it connects to Kite successfully and receives nothing, which
    // looks completely healthy (WS 101, no errors, health checks green) while
    // `live_ticks` silently stops growing. The desktop app used to send these; it
    // is gone, and `option_chain_set` covers F&O only.
    tokio::spawn(crate::spot_subscriber::run(state.clone()));

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/api/kite/instruments", get(instruments_search))
        .route("/api/kite/quote", get(quote_handler))
        .route("/api/kite/historical", get(historical_handler))
        .layer(cors)
        .with_state(state);

    let addr = format!("0.0.0.0:{}", port);
    log::info!("Kite REST API server listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("Failed to bind Kite API server port");

    axum::serve(listener, app)
        .await
        .expect("Kite API server crashed");
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// A minimal Kite instruments CSV header, in the real column order.
    const HEADER: &str = "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange";

    fn csv(rows: &[&str]) -> String {
        let mut out = String::from(HEADER);
        for r in rows {
            out.push('\n');
            out.push_str(r);
        }
        out
    }

    // ── parse_instruments_csv ────────────────────────────────────────────────

    #[test]
    fn parses_an_equity_row_from_the_correct_columns() {
        let out = parse_instruments_csv(&csv(&[
            "738561,2885,RELIANCE,\"RELIANCE INDUSTRIES\",2450.5,,0,0.05,1,EQ,NSE,NSE",
        ]));

        assert_eq!(out.len(), 1);
        let i = &out[0];
        assert_eq!(i.instrument_token, 738561);
        assert_eq!(i.exchange_token, 2885);
        assert_eq!(i.tradingsymbol, "RELIANCE");
        // The quoted name is unwrapped, not split on its inner comma-free spaces.
        assert_eq!(i.name, "RELIANCE INDUSTRIES");
        assert_eq!(i.last_price, 2450.5);
        assert_eq!(i.expiry, "");
        assert_eq!(i.strike, 0.0);
        // tick_size/lot_size come from cols 7/8 — a past bug read 5/6 (expiry,
        // strike), so every instrument reported tick_size 0 and lot_size 0.
        assert_eq!(i.tick_size, 0.05);
        assert_eq!(i.lot_size, 1);
        assert_eq!(i.instrument_type, "EQ");
        assert_eq!(i.exchange, "NSE");
    }

    #[test]
    fn parses_a_quoted_name_containing_a_comma_without_shifting_columns() {
        let out = parse_instruments_csv(&csv(&[
            "111,222,ABC,\"Alpha, Beta & Co\",10.5,,0,0.05,7,EQ,NSE,NSE",
        ]));

        assert_eq!(out.len(), 1);
        assert_eq!(out[0].name, "Alpha, Beta & Co");
        // The inner comma did not shift tick_size/lot_size/type one place left.
        assert_eq!(out[0].tick_size, 0.05);
        assert_eq!(out[0].lot_size, 7);
        assert_eq!(out[0].instrument_type, "EQ");
    }

    #[test]
    fn keeps_derivative_rows_with_expiry_and_strike() {
        // Regression: these three types were dropped unconditionally, so the NFO
        // parse yielded 0 rows, tripped the `len() < 100` guard in
        // `get_instruments`, and `resolve_token` could never resolve an F&O
        // tradingsymbol over HTTP — every option/future chart failed in a browser.
        let out = parse_instruments_csv(&csv(&[
            "12345678,48225,NIFTY26AUG24000CE,NIFTY,120.25,2026-08-25,24000,0.05,75,CE,NFO-OPT,NFO",
            "12345679,48226,NIFTY26AUG24000PE,NIFTY,98.4,2026-08-25,24000,0.05,75,PE,NFO-OPT,NFO",
            "12345680,48227,NIFTY26AUGFUT,NIFTY,24110,2026-08-27,0,0.05,75,FUT,NFO-FUT,NFO",
        ]));

        assert_eq!(out.len(), 3);
        assert_eq!(out[0].instrument_type, "CE");
        assert_eq!(out[0].expiry, "2026-08-25");
        assert_eq!(out[0].strike, 24000.0);
        assert_eq!(out[0].lot_size, 75);
        assert_eq!(out[2].instrument_type, "FUT");
        // Futures carry no strike; 0.0 is what `rowsToSearchResults` maps to null.
        assert_eq!(out[2].strike, 0.0);
    }

    #[test]
    fn keeps_index_rows_and_drops_unknown_types() {
        let out = parse_instruments_csv(&csv(&[
            "256265,0,NIFTY 50,NIFTY 50,0,,0,0,0,INDEX,INDICES,NSE",
            "999001,0,SOMEBOND,Some Bond,0,,0,0,0,BOND,NSE,NSE",
        ]));

        assert_eq!(out.len(), 1);
        assert_eq!(out[0].tradingsymbol, "NIFTY 50");
        assert_eq!(out[0].instrument_type, "INDEX");
    }

    #[test]
    fn skips_malformed_short_and_empty_rows() {
        let out = parse_instruments_csv(&csv(&[
            "",
            "738561,2885,RELIANCE",                              // too few columns
            "0,2885,ZEROTOKEN,Zero,0,,0,0,1,EQ,NSE,NSE",         // token 0
            ",2885,,Blank,0,,0,0,1,EQ,NSE,NSE",                  // empty symbol
            "738562,2886,TCS,TCS LTD,3900,,0,0.05,1,EQ,NSE,NSE", // the only good row
        ]));

        assert_eq!(out.len(), 1);
        assert_eq!(out[0].tradingsymbol, "TCS");
    }

    // ── Fixtures ────────────────────────────────────────────────────────────

    fn eq(symbol: &str, name: &str) -> Instrument {
        Instrument {
            instrument_token: 1,
            exchange_token: 1,
            tradingsymbol: symbol.to_string(),
            name: name.to_string(),
            last_price: 0.0,
            expiry: String::new(),
            strike: 0.0,
            tick_size: 0.05,
            lot_size: 1,
            instrument_type: "EQ".to_string(),
            segment: "NSE".to_string(),
            exchange: "NSE".to_string(),
        }
    }

    fn opt(symbol: &str, underlying: &str, expiry: &str, strike: f64, itype: &str) -> Instrument {
        Instrument {
            instrument_token: 2,
            exchange_token: 2,
            tradingsymbol: symbol.to_string(),
            name: underlying.to_string(),
            last_price: 0.0,
            expiry: expiry.to_string(),
            strike,
            tick_size: 0.05,
            lot_size: 75,
            instrument_type: itype.to_string(),
            segment: "NFO-OPT".to_string(),
            exchange: "NFO".to_string(),
        }
    }

    // ── search_cash ─────────────────────────────────────────────────────────

    #[test]
    fn cash_search_ranks_symbol_prefix_above_name_match_then_shortest_first() {
        let list = vec![
            eq("RELINFRA", "Reliance Infrastructure"),
            eq("RELIANCE", "Reliance Industries"),
            eq("IRFC", "Indian Railway Finance"),
            eq("TCS", "Tata Consultancy"),
        ];

        let hits = search_cash(&list, "RELI");
        let syms: Vec<&str> = hits.iter().map(|i| i.tradingsymbol.as_str()).collect();
        // Both are prefix matches; the shorter symbol wins the tie.
        assert_eq!(syms, vec!["RELIANCE", "RELINFRA"]);
    }

    #[test]
    fn cash_search_matches_on_company_name() {
        let list = vec![eq("BAJFINANCE", "Bajaj Finance"), eq("TCS", "Tata Consultancy")];
        let hits = search_cash(&list, "BAJAJ");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].tradingsymbol, "BAJFINANCE");
    }

    #[test]
    fn cash_search_caps_at_the_result_limit() {
        let list: Vec<Instrument> = (0..EQ_SEARCH_LIMIT + 20)
            .map(|n| eq(&format!("SYM{:03}", n), "Something"))
            .collect();
        assert_eq!(search_cash(&list, "SYM").len(), EQ_SEARCH_LIMIT);
    }

    // ── search_cash: indices ────────────────────────────────────────────────
    //
    // An index shares its prefix with every ETF that tracks it, and the masters
    // carry 209 index rows against a handful of memorable names. Before this,
    // "NIFTY" returned ten rows chosen by prefix-then-shortest and NIFTY BANK was
    // not one of them — it lost to NIFTY 50 / NIFTY EV / NIFTY IT on length and to
    // NIFTY1 / NIFTYETF on nothing at all. The most-traded index in the country
    // could not be found by typing its own name.

    /// An index row: Kite reports these in the `INDICES` segment with type `EQ`.
    fn idx(symbol: &str, exchange: &str) -> Instrument {
        Instrument {
            segment: "INDICES".to_string(),
            exchange: exchange.to_string(),
            ..eq(symbol, symbol)
        }
    }

    #[test]
    fn cash_search_puts_real_indices_above_the_etfs_that_track_them() {
        let list = vec![
            eq("NIFTY1", "Nifty ETF"),
            eq("NIFTYETF", "Nifty ETF"),
            eq("NIFTYADD", "Nifty Additive"),
            eq("NIFTYBEES", "Nippon Nifty ETF"),
            idx("NIFTY 50", "NSE"),
            idx("NIFTY BANK", "NSE"),
            idx("NIFTY IT", "NSE"),
        ];

        let syms: Vec<String> = search_cash(&list, "NIFTY")
            .iter()
            .map(|i| i.tradingsymbol.clone())
            .collect();

        // Every index outranks every tracker, and BANK is reachable at all.
        assert_eq!(&syms[..3], &["NIFTY 50", "NIFTY IT", "NIFTY BANK"]);
        assert!(syms.contains(&"NIFTY BANK".to_string()));
        assert!(
            syms.iter().position(|s| s == "NIFTY BANK")
                < syms.iter().position(|s| s == "NIFTY1"),
            "an ETF must not outrank the index it tracks: {syms:?}"
        );
    }

    #[test]
    fn cash_search_finds_sensex_the_index_not_just_its_etfs() {
        // Every one of these is a real NSE/BSE row. SENSEX itself is a BSE index
        // (segment INDICES, token 265) — which is why searching NSE alone could
        // never surface it, and why the web adapter now queries BSE too.
        let list = vec![
            eq("SENSEXETF", "Sensex ETF"),
            eq("SENSEXBEES", "Nippon Sensex ETF"),
            eq("HDFCSENSEX", "HDFC Sensex ETF"),
            idx("SENSEX", "BSE"),
            idx("BANKEX", "BSE"),
        ];

        let syms: Vec<String> = search_cash(&list, "SENSEX")
            .iter()
            .map(|i| i.tradingsymbol.clone())
            .collect();

        assert_eq!(syms[0], "SENSEX", "the index itself must come first: {syms:?}");
    }

    #[test]
    fn cash_search_still_puts_an_exact_ticker_first() {
        // The index boost must not bury a scrip the user named exactly.
        let list = vec![
            idx("NIFTY IT", "NSE"),
            idx("NIFTY 50", "NSE"),
            eq("TCS", "Tata Consultancy"),
        ];
        let hits = search_cash(&list, "TCS");
        assert_eq!(hits[0].tradingsymbol, "TCS");
    }

    // ── search_derivatives ──────────────────────────────────────────────────

    #[test]
    fn derivative_search_orders_by_expiry_then_strike_before_truncating() {
        let mut list = vec![
            opt("NIFTY26SEP24500CE", "NIFTY", "2026-09-29", 24500.0, "CE"),
            opt("NIFTY26AUG25000CE", "NIFTY", "2026-08-25", 25000.0, "CE"),
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
        ];
        // Pad the FRONT with far-dated strikes: the old implementation broke out
        // of the scan at 30 candidates and truncated to 15, so it returned these
        // and never saw the near-month contracts a trader actually wants.
        for n in 0..40 {
            let strike = 30000 + n * 50;
            list.insert(
                0,
                opt(&format!("NIFTY27JAN{}CE", strike), "NIFTY", "2027-01-28", strike as f64, "CE"),
            );
        }

        let hits = search_derivatives(&list, "NIFTY");
        assert_eq!(hits.len(), FNO_SEARCH_LIMIT);
        let head: Vec<&str> = hits.iter().take(3).map(|i| i.tradingsymbol.as_str()).collect();
        assert_eq!(
            head,
            vec!["NIFTY26AUG24000CE", "NIFTY26AUG25000CE", "NIFTY26SEP24500CE"]
        );
    }

    #[test]
    fn derivative_search_ands_the_tokens() {
        let list = vec![
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
            opt("NIFTY26AUG24000PE", "NIFTY", "2026-08-25", 24000.0, "PE"),
            opt("NIFTY26AUG25000CE", "NIFTY", "2026-08-25", 25000.0, "CE"),
            opt("BANKNIFTY26AUG24000CE", "BANKNIFTY", "2026-08-25", 24000.0, "CE"),
        ];

        let hits = search_derivatives(&list, "NIFTY 24000 CE");
        let syms: Vec<&str> = hits.iter().map(|i| i.tradingsymbol.as_str()).collect();
        // BANKNIFTY matches too — its tradingsymbol contains "NIFTY", exactly as
        // the desktop `LIKE '%NIFTY%'` does. The 25000 strike and the PE do not.
        assert_eq!(syms, vec!["BANKNIFTY26AUG24000CE", "NIFTY26AUG24000CE"]);
    }

    #[test]
    fn derivative_search_accepts_option_type_aliases() {
        let list = vec![
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
            opt("NIFTY26AUG24000PE", "NIFTY", "2026-08-25", 24000.0, "PE"),
        ];

        assert_eq!(search_derivatives(&list, "NIFTY PUT")[0].instrument_type, "PE");
        assert_eq!(search_derivatives(&list, "NIFTY CALL")[0].instrument_type, "CE");
    }

    #[test]
    fn derivative_search_treats_a_numeric_token_as_a_strike_prefix() {
        let list = vec![
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
            opt("NIFTY26AUG24005CE", "NIFTY", "2026-08-25", 24005.0, "CE"),
            opt("NIFTY26AUG24050CE", "NIFTY", "2026-08-25", 24050.0, "CE"),
            opt("NIFTY26AUG25000CE", "NIFTY", "2026-08-25", 25000.0, "CE"),
        ];

        // A genuine PREFIX match, matching the desktop's
        // `CAST(strike AS INTEGER) AS TEXT LIKE '2400%'`: 24000 and 24005 share
        // the prefix, 24050 and 25000 do not. (The desktop comment at
        // `commands/instruments.rs` claims 24050 matches; it does not.)
        let hits = search_derivatives(&list, "NIFTY 2400");
        let syms: Vec<&str> = hits.iter().map(|i| i.tradingsymbol.as_str()).collect();
        assert_eq!(syms, vec!["NIFTY26AUG24000CE", "NIFTY26AUG24005CE"]);

        // The full strike still resolves to the single exact contract.
        let exact = search_derivatives(&list, "NIFTY 24050");
        assert_eq!(exact.len(), 1);
        assert_eq!(exact[0].tradingsymbol, "NIFTY26AUG24050CE");
    }

    #[test]
    fn derivative_search_finds_futures_by_alias() {
        let list = vec![
            opt("NIFTY26AUGFUT", "NIFTY", "2026-08-27", 0.0, "FUT"),
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
        ];

        let hits = search_derivatives(&list, "NIFTY FUTURES");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].tradingsymbol, "NIFTY26AUGFUT");
    }

    // ── Dispatch ────────────────────────────────────────────────────────────

    #[test]
    fn dispatch_routes_nfo_to_the_token_search_and_nse_to_the_cash_search() {
        let cash = vec![eq("RELIANCE", "Reliance Industries")];
        let derivs = vec![
            opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE"),
            opt("NIFTY26AUG24000PE", "NIFTY", "2026-08-25", 24000.0, "PE"),
        ];

        assert_eq!(search_instrument_list(&cash, "RELI", "NSE").len(), 1);
        // A multi-token query only narrows on the derivative path.
        assert_eq!(search_instrument_list(&derivs, "NIFTY 24000 CE", "NFO").len(), 1);
    }

    // ── Serialization contract with the web adapter ──────────────────────────

    #[test]
    fn instrument_json_carries_the_fields_the_web_adapter_reads() {
        // `lib/bridge/webAdapters.ts::rowsToSearchResults` reads exactly these
        // keys off each row; `expiry` and `strike` were missing before, so every
        // F&O search result rendered with a blank expiry and a null strike.
        let json = serde_json::to_value(opt("NIFTY26AUG24000CE", "NIFTY", "2026-08-25", 24000.0, "CE")).unwrap();

        assert_eq!(json["tradingsymbol"], "NIFTY26AUG24000CE");
        assert_eq!(json["name"], "NIFTY");
        assert_eq!(json["expiry"], "2026-08-25");
        assert_eq!(json["strike"], 24000.0);
        assert_eq!(json["instrument_type"], "CE");
        assert_eq!(json["exchange"], "NFO");
    }

    #[test]
    fn batched_quote_parse_keeps_real_prices_and_omits_the_rest() {
        // The chain selector reads one of these per underlying per cycle and turns
        // it into an ATM strike, so a missing quote must NOT arrive as 0.0.
        let body = serde_json::json!({
            "data": {
                "NSE:NIFTY 50":   { "last_price": 24175.65 },
                "NSE:NIFTY BANK": { "last_price": 57496.30 },
                "NSE:RELIANCE":   { "last_price": 1290.5 },
                // Every shape that is not a price:
                "NSE:HALTED":     { "last_price": 0 },
                "NSE:NOFIELD":    { "volume": 100 },
                "NSE:TEXTPRICE":  { "last_price": "1234" },
            }
        });

        let prices = parse_quote_prices(&body);

        assert_eq!(prices.get("NSE:NIFTY 50"), Some(&24175.65));
        assert_eq!(prices.get("NSE:NIFTY BANK"), Some(&57496.30));
        assert_eq!(prices.get("NSE:RELIANCE"), Some(&1290.5));
        for absent in ["NSE:HALTED", "NSE:NOFIELD", "NSE:TEXTPRICE"] {
            assert!(
                !prices.contains_key(absent),
                "{absent} has no usable price and must be absent, not zero-filled"
            );
        }
        assert_eq!(prices.len(), 3);
    }

    #[test]
    fn batched_quote_parse_survives_a_body_that_is_not_a_quote_response() {
        for body in [
            serde_json::json!({}),
            serde_json::json!({ "data": null }),
            serde_json::json!({ "status": "error", "message": "invalid token" }),
        ] {
            assert!(parse_quote_prices(&body).is_empty());
        }
    }
}
