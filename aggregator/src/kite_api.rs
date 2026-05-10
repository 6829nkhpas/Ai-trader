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
    pub tick_size: f64,
    pub lot_size: u32,
    pub instrument_type: String,
    pub segment: String,
    pub exchange: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct QuoteData {
    pub symbol: String,
    pub instrument_token: u64,
    pub last_price: f64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub change: f64,
    pub net_change: f64,
}

#[derive(Debug, Deserialize)]
pub struct InstrumentSearchParams {
    q: Option<String>,
    exchange: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct QuoteParams {
    /// Kite instrument identifiers, e.g. "NSE:RELIANCE"
    #[serde(rename = "i")]
    instruments: Option<String>,
}

// ── Shared State ─────────────────────────────────────────────────────────────

struct InstrumentCache {
    instruments: Vec<Instrument>,
    fetched_at: Option<Instant>,
    exchange: String,
}

pub struct KiteApiState {
    api_key: String,
    access_token: String,
    http_client: reqwest::Client,
    cache: RwLock<InstrumentCache>,
}

const CACHE_TTL: Duration = Duration::from_secs(24 * 60 * 60); // 24 hours

impl KiteApiState {
    fn new() -> Self {
        let api_key = std::env::var("KITE_API_KEY")
            .unwrap_or_else(|_| String::new());
        let access_token = std::env::var("KITE_ACCESS_TOKEN")
            .unwrap_or_else(|_| String::new());

        if api_key.is_empty() || access_token.is_empty() {
            log::warn!("KITE_API_KEY or KITE_ACCESS_TOKEN not set — Kite REST API will return errors");
        }

        Self {
            api_key,
            access_token,
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .build()
                .expect("Failed to create HTTP client"),
            cache: RwLock::new(InstrumentCache {
                instruments: Vec::new(),
                fetched_at: None,
                exchange: String::new(),
            }),
        }
    }

    fn auth_header(&self) -> String {
        format!("token {}:{}", self.api_key, self.access_token)
    }

    /// Fetch instruments from Kite and cache them. Returns cached data if fresh.
    async fn get_instruments(&self, exchange: &str) -> Result<Vec<Instrument>, String> {
        // Check cache under read lock first
        {
            let cache = self.cache.read().await;
            if cache.exchange == exchange {
                if let Some(fetched_at) = cache.fetched_at {
                    if fetched_at.elapsed() < CACHE_TTL && !cache.instruments.is_empty() {
                        return Ok(cache.instruments.clone());
                    }
                }
            }
        }

        // Cache miss — fetch from Kite
        log::info!("[Kite API] Fetching instruments for exchange: {}", exchange);

        let url = format!("https://api.kite.trade/instruments/{}", exchange);
        let response = self.http_client
            .get(&url)
            .header("X-Kite-Version", "3")
            .header("Authorization", self.auth_header())
            .send()
            .await
            .map_err(|e| format!("Kite HTTP request failed: {}", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(format!("Kite API returned {}: {}", status, body));
        }

        let csv_text = response.text().await
            .map_err(|e| format!("Failed to read response body: {}", e))?;

        let instruments = parse_instruments_csv(&csv_text);
        log::info!("[Kite API] Cached {} instruments for {}", instruments.len(), exchange);

        // Update cache under write lock
        {
            let mut cache = self.cache.write().await;
            cache.instruments = instruments.clone();
            cache.fetched_at = Some(Instant::now());
            cache.exchange = exchange.to_string();
        }

        Ok(instruments)
    }
}

/// Parse the Kite instruments CSV into a Vec<Instrument>.
/// Only includes EQ (equity) and INDEX types for cleaner search results.
fn parse_instruments_csv(csv: &str) -> Vec<Instrument> {
    let mut instruments = Vec::new();
    let mut lines = csv.lines();

    // Skip header
    lines.next();

    for line in lines {
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 12 {
            continue;
        }

        let instrument_type = cols[7].trim();
        // Only include equity and index instruments
        if instrument_type != "EQ" && instrument_type != "" && instrument_type != "INDEX" {
            continue;
        }

        let instrument = Instrument {
            instrument_token: cols[0].trim().parse().unwrap_or(0),
            exchange_token: cols[1].trim().parse().unwrap_or(0),
            tradingsymbol: cols[2].trim().to_string(),
            name: cols[3].trim().to_string(),
            last_price: cols[4].trim().parse().unwrap_or(0.0),
            tick_size: cols[5].trim().parse().unwrap_or(0.0),
            lot_size: cols[6].trim().parse().unwrap_or(0),
            instrument_type: instrument_type.to_string(),
            segment: cols[10].trim().to_string(),
            exchange: cols[11].trim().to_string(),
        };

        instruments.push(instrument);
    }

    instruments
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/kite/instruments?q=RELI&exchange=NSE
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
        log::error!("[Kite instruments] {}", e);
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e, "results": [] })),
        )
    })?;

    // Filter: prefix matches first, then contains matches
    let mut prefix_matches = Vec::new();
    let mut contains_matches = Vec::new();

    for inst in &instruments {
        let sym = inst.tradingsymbol.to_uppercase();
        let name = inst.name.to_uppercase();

        if sym.starts_with(&query) {
            prefix_matches.push(inst.clone());
        } else if sym.contains(&query) || name.contains(&query) {
            contains_matches.push(inst.clone());
        }

        if prefix_matches.len() + contains_matches.len() >= 30 {
            break;
        }
    }

    let mut results: Vec<Instrument> = prefix_matches;
    results.extend(contains_matches);
    results.truncate(15);

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
            log::error!("[Kite quote] HTTP error: {}", e);
            (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({ "error": e.to_string(), "quotes": [] })),
            )
        })?;

    if !response.status().is_success() {
        let status = response.status().as_u16();
        let body = response.text().await.unwrap_or_default();
        log::error!("[Kite quote] API returned {}: {}", status, body);
        return Err((
            StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(serde_json::json!({ "error": format!("Kite API error: {}", status), "quotes": [] })),
        ));
    }

    let json: serde_json::Value = response.json().await.map_err(|e| {
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
        .map(|(key, value)| {
            let symbol = key.split(':').nth(1).unwrap_or(key).to_string();
            let ohlc = value.get("ohlc").cloned().unwrap_or(serde_json::json!({}));
            let prev_close = ohlc.get("close").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let last_price = value.get("last_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let net_change = if prev_close > 0.0 { last_price - prev_close } else { 0.0 };
            let pct_change = if prev_close > 0.0 {
                (net_change / prev_close) * 100.0
            } else {
                0.0
            };

            QuoteData {
                symbol,
                instrument_token: value.get("instrument_token").and_then(|v| v.as_u64()).unwrap_or(0),
                last_price,
                open: ohlc.get("open").and_then(|v| v.as_f64()).unwrap_or(0.0),
                high: ohlc.get("high").and_then(|v| v.as_f64()).unwrap_or(0.0),
                low: ohlc.get("low").and_then(|v| v.as_f64()).unwrap_or(0.0),
                close: prev_close,
                volume: value.get("volume").and_then(|v| v.as_u64()).unwrap_or(0),
                change: (pct_change * 100.0).round() / 100.0,
                net_change: (net_change * 100.0).round() / 100.0,
            }
        })
        .collect();

    Ok(Json(serde_json::json!({ "quotes": quotes })))
}

// ── Server ───────────────────────────────────────────────────────────────────

/// Build and start the Kite REST API server on the given port.
/// Call this from main.rs via `tokio::spawn`.
pub async fn run_kite_api_server(port: &str) {
    let state = Arc::new(KiteApiState::new());

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/api/kite/instruments", get(instruments_search))
        .route("/api/kite/quote", get(quote_handler))
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
