// src/providers/kite.rs — the Zerodha Kite implementation of the P14 traits.
//
// This file is where the direct-vs-proxy dual transport lives. It was previously
// duplicated across `history_loader.rs`, `fno_service.rs`, `ticker.rs` and
// `instrument_master.rs`, each with its own slightly different basic-auth
// condition; consolidating it means a second provider is a new file rather than a
// fifth copy.
//
// ## The two transports, and why both exist
//
// * **Direct** — `https://api.kite.trade/...` with an
//   `Authorization: token <api_key>:<access_token>` header. Local development,
//   where a `.env` holds real credentials.
// * **Proxy** — `{server::kite_url()}/...`, the server-side Kite REST proxy
//   (`aggregator/src/kite_api.rs`). A SHIPPED app has no Kite credentials at all:
//   the access token is per-user and expires daily, so the release pipeline bakes
//   none. Without this fallback every historical backfill in production failed
//   silently, leaving `historical_intraday` unwritten and starving downstream
//   consumers with only a warning in the log.
//
// The selector is unchanged from the code this replaces: empty api_key OR empty
// access_token ⇒ proxy.
//
// The instrument CSV dump has no proxy route and needs none — Kite serves
// `/instruments/{EXCHANGE}` unauthenticated.

use std::collections::HashMap;

use chrono::NaiveDate;
use log::{info, warn};
use once_cell::sync::Lazy;
use serde::Deserialize;

use super::{Candle, MarketDataProvider, ProviderFuture, Quote};

/// One process-wide HTTP client for every Kite fetch.
///
/// Moved here from `history_loader.rs` unchanged, including the reasoning: each
/// loader invocation used to build its own client, so every backfill paid a fresh
/// TLS handshake to `api.kite.trade` (or to the gateway proxy) before its first
/// chunk, and connections were never reused between invocations. One pooled client
/// removes that per-backfill setup cost and lets consecutive chunk requests ride
/// the same connection — now across all four call sites rather than one.
static KITE_CLIENT: Lazy<reqwest::Client> = Lazy::new(|| {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .pool_idle_timeout(std::time::Duration::from_secs(90))
        .pool_max_idle_per_host(4)
        .build()
        .unwrap_or_else(|_| reqwest::Client::new())
});

/// A separate short-timeout client for the instrument-token lookup.
///
/// That lookup sits directly in front of a live-tick subscription, so a slow feed
/// must not hold the subscription open for the 30 s the backfill client allows.
/// Preserves the 5 s timeout `ticker.rs` used.
static KITE_LOOKUP_CLIENT: Lazy<reqwest::Client> = Lazy::new(|| {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .unwrap_or_default()
});

// ── Kite wire types ─────────────────────────────────────────────────────────

/// Top-level response from the Kite Historical API.
#[derive(Debug, Deserialize)]
struct KiteHistoricalResponse {
    #[allow(dead_code)] // Present on the wire; not read, but documents the shape.
    status: String,
    data: KiteHistoricalData,
}

#[derive(Debug, Deserialize)]
struct KiteHistoricalData {
    candles: Vec<Vec<serde_json::Value>>,
}

#[derive(Debug, Deserialize)]
struct KiteQuoteResponse {
    #[allow(dead_code)]
    status: String,
    data: HashMap<String, KiteQuoteData>,
}

#[derive(Debug, Deserialize)]
struct KiteQuoteData {
    last_price: f64,
    oi: Option<u64>,
}

/// The server proxy's quote shape, which differs from Kite's own.
#[derive(Debug, Deserialize)]
struct ServerProxyQuote {
    symbol: String,
    last_price: f64,
    oi: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct ServerQuoteProxyResponse {
    quotes: Vec<ServerProxyQuote>,
}

// ── The provider ────────────────────────────────────────────────────────────

/// Zerodha Kite Connect, over whichever transport the credentials allow.
pub struct KiteProvider;

impl KiteProvider {
    pub fn new() -> Self {
        KiteProvider
    }

    /// Credentials for the direct transport, or empty strings for the proxy one.
    ///
    /// Delegates to the existing `OnceLock`-cached resolver (env vars, then a
    /// `.env` walked up from the cwd) rather than reading the environment again:
    /// one resolution per process, and one place where the resolution order is
    /// defined.
    fn credentials() -> (String, String) {
        crate::commands::charts::get_kite_credentials()
    }

    // NOTE: there is deliberately no shared `must_proxy()` helper. The call sites
    // this provider replaced did not agree on the test — the historical path used
    // `trim().is_empty()` while the quote path used a bare `is_empty()` — so each
    // method below keeps its original condition verbatim. A single helper would
    // have to pick one, which is a behaviour change smuggled into a refactor whose
    // whole contract is that behaviour does not shift. Normalising them is a
    // separate, deliberate change.

    /// Attach gateway basic-auth to a proxied request when it applies.
    ///
    /// The public HTTPS gateway route is behind basic auth; a direct-IP proxy in
    /// local dev is not. `http_base()` being empty is what distinguishes the two,
    /// so credentials are attached only when a gateway is actually configured.
    ///
    /// NOTE: the four call sites this replaces were inconsistent here —
    /// `fno_service` attached credentials whenever a username resolved (which is
    /// always: `questdb_user()` defaults to `"admin"`), while `history_loader` and
    /// `ticker` also required a non-empty `http_base()`. The stricter form is kept,
    /// because sending basic-auth to a local unauthenticated proxy is at best
    /// pointless and at worst leaks a credential to a plaintext local port. The
    /// local proxy ignores the header either way, so no working path changes.
    fn with_proxy_auth(req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let user = crate::server::questdb_user();
        let pass = crate::server::questdb_password();
        if !user.is_empty() && !crate::server::http_base().is_empty() {
            return req.basic_auth(&user, Some(&pass));
        }
        req
    }

    /// Historical candles straight from Kite, with an ISO-8601 timestamp string.
    async fn historical_direct(
        instrument_token: u32,
        interval: &str,
        from: &NaiveDate,
        to: &NaiveDate,
        api_key: &str,
        access_token: &str,
    ) -> Result<Vec<Candle>, String> {
        let url = format!(
            "https://api.kite.trade/instruments/historical/{}/{}",
            instrument_token, interval
        );

        let response = KITE_CLIENT
            .get(&url)
            .query(&[
                ("from", from.format("%Y-%m-%d").to_string()),
                ("to", to.format("%Y-%m-%d").to_string()),
            ])
            .header(
                "Authorization",
                format!("token {}:{}", api_key, access_token),
            )
            .header("X-Kite-Version", "3")
            .send()
            .await
            .map_err(|e| format!("HTTP request failed: {}", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "unable to read body".into());
            return Err(format!(
                "Kite API error {} for interval '{}': {}",
                status, interval, body
            ));
        }

        let api_response: KiteHistoricalResponse = response
            .json()
            .await
            .map_err(|e| format!("JSON parse failed: {}", e))?;

        // Candle arrays are positional: [timestamp, open, high, low, close, volume].
        let candles: Vec<Candle> = api_response
            .data
            .candles
            .iter()
            .filter_map(|row| {
                if row.len() < 6 {
                    warn!("Skipping malformed candle row: {:?}", row);
                    return None;
                }
                Some(Candle {
                    timestamp: row[0].as_str().unwrap_or_default().to_string(),
                    open: row[1].as_f64().unwrap_or(0.0),
                    high: row[2].as_f64().unwrap_or(0.0),
                    low: row[3].as_f64().unwrap_or(0.0),
                    close: row[4].as_f64().unwrap_or(0.0),
                    volume: row[5].as_i64().unwrap_or(0),
                })
            })
            .collect();

        Ok(candles)
    }

    /// Historical candles via the server-side Kite REST proxy (thin-client mode).
    ///
    /// `GET {kite_url()}/historical?instrument_token=…&interval=…&from=…&to=…`
    /// → `{ "candles": [ { "time": <unix_sec>, "open", … } ] }`
    /// (see `aggregator/src/kite_api.rs::historical_handler`).
    ///
    /// NOTE on timestamps: the proxy returns `time` as UNIX SECONDS, whereas direct
    /// Kite returns an ISO-8601 string. `parse_kite_timestamp` downstream accepts
    /// only the two ISO-8601 shapes, so a bare epoch integer would fail to parse and
    /// every row would be dropped at insert time. The epoch seconds are formatted
    /// back into `%Y-%m-%dT%H:%M:%S%z` here, keeping [`Candle`]'s contract uniform
    /// across transports.
    async fn historical_via_proxy(
        instrument_token: u32,
        interval: &str,
        from: &NaiveDate,
        to: &NaiveDate,
    ) -> Result<Vec<Candle>, String> {
        let base = crate::server::kite_url();
        let url = format!("{}/historical", base.trim_end_matches('/'));

        let req = KITE_CLIENT.get(&url).query(&[
            ("instrument_token", instrument_token.to_string()),
            ("interval", interval.to_string()),
            ("from", from.format("%Y-%m-%d").to_string()),
            ("to", to.format("%Y-%m-%d").to_string()),
        ]);

        let response = Self::with_proxy_auth(req)
            .send()
            .await
            .map_err(|e| format!("Kite proxy request failed: {}", e))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "unable to read body".into());
            return Err(format!(
                "Kite proxy error {} for interval '{}': {}",
                status, interval, body
            ));
        }

        let json: serde_json::Value = response
            .json()
            .await
            .map_err(|e| format!("Kite proxy JSON parse failed: {}", e))?;

        let rows = json
            .get("candles")
            .and_then(|c| c.as_array())
            .ok_or_else(|| "Kite proxy response missing 'candles' array".to_string())?;

        let candles: Vec<Candle> = rows
            .iter()
            .filter_map(|row| {
                let time_sec = row.get("time").and_then(|t| t.as_i64())?;
                let dt = chrono::DateTime::from_timestamp(time_sec, 0)?;
                Some(Candle {
                    timestamp: dt.format("%Y-%m-%dT%H:%M:%S%z").to_string(),
                    open: row.get("open").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    high: row.get("high").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    low: row.get("low").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    close: row.get("close").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    volume: row.get("volume").and_then(|v| v.as_i64()).unwrap_or(0),
                })
            })
            .collect();

        info!(
            "[kite] proxy historical: {} candles (token {}, interval {})",
            candles.len(),
            instrument_token,
            interval
        );

        Ok(candles)
    }

    /// Quotes straight from Kite, in batches of 500 symbols.
    async fn quotes_direct(
        symbols: &[String],
        api_key: &str,
        access_token: &str,
    ) -> Result<HashMap<String, Quote>, String> {
        let mut all_quotes = HashMap::new();
        let url = "https://api.kite.trade/quote";

        for chunk in symbols.chunks(500) {
            let query: Vec<(&str, String)> =
                chunk.iter().map(|sym| ("i", sym.clone())).collect();

            let resp = KITE_CLIENT
                .get(url)
                .query(&query)
                .header(
                    "Authorization",
                    format!("token {}:{}", api_key, access_token),
                )
                .header("X-Kite-Version", "3")
                .send()
                .await
                .map_err(|e| format!("HTTP request failed: {}", e))?;

            if !resp.status().is_success() {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                return Err(format!("Kite API returned status {}: {}", status, text));
            }

            let body: KiteQuoteResponse = resp
                .json()
                .await
                .map_err(|e| format!("JSON parse failed: {}", e))?;

            for (key, value) in body.data {
                all_quotes.insert(
                    key,
                    Quote {
                        last_price: value.last_price,
                        oi: value.oi,
                    },
                );
            }
        }

        Ok(all_quotes)
    }

    /// Quotes via the server-side proxy, re-keyed onto the requested symbols.
    ///
    /// The proxy may answer with a bare tradingsymbol (`NIFTY26JUL24000CE`) where
    /// the request used an exchange-qualified one (`NFO:NIFTY26JUL24000CE`), so each
    /// returned symbol is mapped back onto the caller's key: exact match first, then
    /// a suffix match, then the proxy's own symbol as a last resort. Preserved
    /// verbatim from `fno_service::fetch_kite_quotes_api` — the caller looks results
    /// up by the string it asked with, so a mis-key silently empties the chain.
    async fn quotes_via_proxy(symbols: &[String]) -> Result<HashMap<String, Quote>, String> {
        let mut all_quotes = HashMap::new();
        let kite_base = crate::server::kite_url();
        let url = format!("{}/quote", kite_base.trim_end_matches('/'));

        for chunk in symbols.chunks(500) {
            let query: Vec<(&str, String)> =
                chunk.iter().map(|sym| ("i", sym.clone())).collect();

            let req = KITE_CLIENT.get(&url).query(&query);

            let resp = Self::with_proxy_auth(req)
                .send()
                .await
                .map_err(|e| format!("Server Kite proxy request failed: {}", e))?;

            if !resp.status().is_success() {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                return Err(format!(
                    "Server Kite proxy returned status {}: {}",
                    status, text
                ));
            }

            let body: ServerQuoteProxyResponse = resp
                .json()
                .await
                .map_err(|e| format!("JSON parse failed from server proxy: {}", e))?;

            for q in body.quotes {
                let key = resolve_proxy_quote_key(&q.symbol, symbols);
                all_quotes.insert(
                    key,
                    Quote {
                        last_price: q.last_price,
                        oi: q.oi,
                    },
                );
            }
        }

        Ok(all_quotes)
    }
}

/// Map a symbol as the proxy reported it back onto the key the caller requested.
///
/// Extracted as a free function so the precedence — exact match, then suffix match,
/// then the reported symbol unchanged — is unit-testable without a live proxy.
pub fn resolve_proxy_quote_key(reported: &str, requested: &[String]) -> String {
    if requested.iter().any(|s| s == reported) {
        return reported.to_string();
    }
    requested
        .iter()
        .find(|s| s.ends_with(reported) || s.as_str() == reported)
        .cloned()
        .unwrap_or_else(|| reported.to_string())
}

impl Default for KiteProvider {
    fn default() -> Self {
        Self::new()
    }
}

impl MarketDataProvider for KiteProvider {
    fn id(&self) -> &'static str {
        "kite"
    }

    fn historical<'a>(
        &'a self,
        instrument_token: u32,
        interval: &'a str,
        from: &'a NaiveDate,
        to: &'a NaiveDate,
    ) -> ProviderFuture<'a, Result<Vec<Candle>, String>> {
        Box::pin(async move {
            let (api_key, access_token) = Self::credentials();
            if api_key.trim().is_empty() || access_token.trim().is_empty() {
                return Self::historical_via_proxy(instrument_token, interval, from, to).await;
            }
            Self::historical_direct(
                instrument_token,
                interval,
                from,
                to,
                &api_key,
                &access_token,
            )
            .await
        })
    }

    fn quotes<'a>(
        &'a self,
        symbols: &'a [String],
    ) -> ProviderFuture<'a, Result<HashMap<String, Quote>, String>> {
        Box::pin(async move {
            let (api_key, access_token) = Self::credentials();
            if api_key.is_empty() || access_token.is_empty() {
                return Self::quotes_via_proxy(symbols).await;
            }
            Self::quotes_direct(symbols, &api_key, &access_token).await
        })
    }

    /// Always proxied.
    ///
    /// This is the one call that has no direct-Kite variant, and deliberately so:
    /// it resolves through `server::kite_url()`, which is the public HTTPS gateway
    /// (`{base}/kite`) in a shipped thin client and the direct
    /// `http://<host>:8087/api/kite` proxy in local dev. An earlier version built
    /// `http://<host>:{KITE_API_PORT}/api/kite` with KITE_API_PORT defaulting to
    /// 8084 — the tool server's port, not the Kite proxy's — and in production 8084
    /// is neither published by docker-compose nor open in the firewall, so the
    /// lookup always failed and live ticks were never subscribed.
    fn instrument_token<'a>(
        &'a self,
        symbol: &'a str,
        exchange: &'a str,
    ) -> ProviderFuture<'a, Result<Option<u32>, String>> {
        Box::pin(async move {
            let kite_base = crate::server::kite_url();
            let url = format!(
                "{}/instruments?q={}&exchange={}",
                kite_base.trim_end_matches('/'),
                urlencoding::encode(symbol),
                urlencoding::encode(exchange)
            );

            let req = KITE_LOOKUP_CLIENT.get(&url);
            let resp = Self::with_proxy_auth(req)
                .send()
                .await
                .map_err(|e| format!("failed: {}", e))?;

            if !resp.status().is_success() {
                return Err(format!("HTTP {}", resp.status()));
            }

            let json = resp
                .json::<serde_json::Value>()
                .await
                .map_err(|e| format!("JSON parse failed: {}", e))?;

            // Case-insensitive exact match on tradingsymbol, then the token of that
            // entry. `q=` is a prefix search, so the response routinely contains
            // near-misses that must not be subscribed to by accident.
            let token = json
                .as_array()
                .and_then(|rows| {
                    rows.iter().find(|inst| {
                        inst.get("tradingsymbol")
                            .and_then(|s| s.as_str())
                            .map(|s| s.eq_ignore_ascii_case(symbol))
                            .unwrap_or(false)
                    })
                })
                .and_then(|inst| inst.get("instrument_token")?.as_u64())
                .map(|t| t as u32);

            Ok(token)
        })
    }

    /// Unauthenticated: Kite serves the daily instrument dump without credentials,
    /// which is why an installed app with no Kite keys can still sync it.
    fn instrument_dump<'a>(
        &'a self,
        exchange: &'a str,
    ) -> ProviderFuture<'a, Result<String, String>> {
        Box::pin(async move {
            let url = format!("https://api.kite.trade/instruments/{}", exchange);
            let resp = KITE_CLIENT
                .get(&url)
                .send()
                .await
                .map_err(|e| format!("HTTP request failed: {}", e))?;

            if !resp.status().is_success() {
                return Err(format!("HTTP {}", resp.status()));
            }

            resp.text()
                .await
                .map_err(|e| format!("failed to read response body: {}", e))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_symbol_match_wins() {
        let requested = vec!["NFO:NIFTY26JUL24000CE".to_string()];
        assert_eq!(
            resolve_proxy_quote_key("NFO:NIFTY26JUL24000CE", &requested),
            "NFO:NIFTY26JUL24000CE"
        );
    }

    #[test]
    fn bare_symbol_is_mapped_back_onto_the_qualified_request() {
        // The proxy strips the exchange prefix; the caller's map is keyed with it.
        let requested = vec![
            "NFO:NIFTY26JUL24000CE".to_string(),
            "NFO:NIFTY26JUL24000PE".to_string(),
        ];
        assert_eq!(
            resolve_proxy_quote_key("NIFTY26JUL24000PE", &requested),
            "NFO:NIFTY26JUL24000PE"
        );
    }

    #[test]
    fn an_unrequested_symbol_keeps_its_own_key() {
        // Rather than being silently attached to an unrelated strike.
        let requested = vec!["NFO:NIFTY26JUL24000CE".to_string()];
        assert_eq!(resolve_proxy_quote_key("SBIN", &requested), "SBIN");
    }

    #[test]
    fn the_provider_reports_a_stable_id() {
        assert_eq!(KiteProvider::new().id(), "kite");
    }
}
