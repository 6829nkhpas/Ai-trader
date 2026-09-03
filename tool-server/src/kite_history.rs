//! kite_history.rs — Paged Kite historical fetch via the aggregator proxy.
//!
//! When QuestDB `historical_*` tables are empty (post-Tauri), tool-server
//! backfills on demand through `GET {KITE_API_URL}/historical` — the same
//! surface the frontend datafeed uses — then persists into QuestDB.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use chrono::{Duration as ChronoDuration, NaiveDate, Utc};
use serde::Deserialize;
use tokio::sync::Mutex;

/// One OHLCV bar from the aggregator historical response (`time` is unix seconds).
#[derive(Debug, Clone)]
pub struct HistoryBar {
    pub time_sec: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

#[derive(Debug, Deserialize)]
struct HistoricalResponse {
    candles: Option<Vec<RawCandle>>,
}

#[derive(Debug, Deserialize)]
struct RawCandle {
    time: serde_json::Value,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: Option<f64>,
}

/// HTTP client for aggregator `/api/kite/historical` with per-(symbol,tf) locks.
#[derive(Clone)]
pub struct KiteHistoryClient {
    client: reqwest::Client,
    base_url: String,
    locks: Arc<Mutex<HashMap<String, Arc<Mutex<()>>>>>,
}

impl KiteHistoryClient {
    /// Build from `KITE_API_URL` (e.g. `http://aggregator:8087/api/kite`).
    /// Returns `None` when the URL is empty (backfill disabled).
    pub fn from_env() -> Option<Self> {
        let raw = std::env::var("KITE_API_URL").unwrap_or_else(|_| {
            "http://127.0.0.1:8087/api/kite".to_string()
        });
        let base = raw.trim().trim_end_matches('/').to_string();
        if base.is_empty() {
            return None;
        }
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(8))
            .build()
            .ok()?;
        Some(Self {
            client,
            base_url: base,
            locks: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    /// Serialize concurrent backfills for the same symbol+timeframe.
    pub async fn lock_key(&self, symbol: &str, timeframe: &str) -> Arc<Mutex<()>> {
        let key = format!("{}::{}", symbol.to_uppercase(), timeframe.to_lowercase());
        let mut map = self.locks.lock().await;
        map.entry(key)
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
    }

    /// Fetch enough bars for `need` candles, paging within Kite interval caps.
    pub async fn fetch_bars(
        &self,
        symbol: &str,
        display_tf: &str,
        need: usize,
    ) -> Result<Vec<HistoryBar>, String> {
        let symbol = normalize_symbol(symbol);
        if symbol.is_empty() {
            return Err("empty symbol".into());
        }
        let (interval, max_days) = kite_interval_for_tf(display_tf);
        let need = need.max(30).min(2000);

        let mut collected: Vec<HistoryBar> = Vec::new();
        let mut to = Utc::now().date_naive();
        // Cap pages so we stay inside the 8s client budget.
        for _page in 0..4 {
            let from = to
                .checked_sub_signed(ChronoDuration::days(max_days as i64))
                .unwrap_or(NaiveDate::from_ymd_opt(2000, 1, 1).unwrap());
            let page = self
                .fetch_page(&symbol, interval, from, to)
                .await?;
            if page.is_empty() {
                break;
            }
            collected.extend(page);
            collected.sort_by_key(|b| b.time_sec);
            collected.dedup_by_key(|b| b.time_sec);
            if collected.len() >= need {
                break;
            }
            to = from
                .checked_sub_signed(ChronoDuration::days(1))
                .unwrap_or(from);
        }

        if collected.len() > need {
            let start = collected.len() - need;
            collected = collected[start..].to_vec();
        }
        Ok(collected)
    }

    async fn fetch_page(
        &self,
        symbol: &str,
        interval: &str,
        from: NaiveDate,
        to: NaiveDate,
    ) -> Result<Vec<HistoryBar>, String> {
        let url = format!("{}/historical", self.base_url);
        let resp = self
            .client
            .get(&url)
            .query(&[
                ("symbol", symbol),
                ("interval", interval),
                ("from", &from.format("%Y-%m-%d").to_string()),
                ("to", &to.format("%Y-%m-%d").to_string()),
            ])
            .send()
            .await
            .map_err(|e| format!("kite historical transport: {e}"))?;

        let status = resp.status();
        let body = resp
            .text()
            .await
            .map_err(|e| format!("kite historical body: {e}"))?;
        if !status.is_success() {
            return Err(format!("kite historical HTTP {status}: {body}"));
        }

        parse_historical_json(&body)
    }
}

/// Strip exchange prefixes and uppercase — QuestDB / Kite tradingsymbol form.
pub fn normalize_symbol(symbol: &str) -> String {
    let s = symbol.trim();
    let upper = s.to_uppercase();
    for prefix in ["NSE:", "BSE:", "NFO:", "MCX:"] {
        if let Some(rest) = upper.strip_prefix(prefix) {
            return rest.trim().to_string();
        }
    }
    upper
}

/// Map display timeframe → (Kite interval string, max days per request).
pub fn kite_interval_for_tf(tf: &str) -> (&'static str, u32) {
    match tf.to_lowercase().as_str() {
        "1m" | "1min" | "2m" | "2min" | "4m" | "4min" => ("minute", 7),
        "3m" | "3min" => ("3minute", 30),
        "5m" | "5min" => ("5minute", 30),
        "10m" | "10min" => ("10minute", 30),
        "15m" | "15min" | "75m" | "75min" | "125m" | "125min" => ("15minute", 60),
        "30m" | "30min" => ("30minute", 60),
        "1h" | "60m" | "2h" | "120m" | "3h" | "180m" | "4h" | "240m" => ("60minute", 60),
        "1d" | "day" | "1w" | "1week" | "week" | "1mth" | "1month" | "1mon" | "month" => {
            ("day", 2000)
        }
        _ => ("10minute", 30),
    }
}

/// Whether this display TF should be persisted as daily (no timeframe column).
pub fn is_daily_interval(tf: &str) -> bool {
    matches!(
        tf.to_lowercase().as_str(),
        "1d" | "day" | "1w" | "1week" | "week" | "1mth" | "1month" | "1mon" | "month"
    )
}

/// Persist timeframe column for intraday — matches `base_timeframe` / loader keys.
pub fn persist_timeframe(tf: &str) -> &'static str {
    match tf.to_lowercase().as_str() {
        "1m" | "1min" | "2m" | "2min" | "4m" | "4min" => "1m",
        "3m" | "3min" => "3m",
        "5m" | "5min" => "5m",
        "10m" | "10min" => "10m",
        "15m" | "15min" | "75m" | "75min" | "125m" | "125min" => "15m",
        "30m" | "30min" => "30m",
        "1h" | "60m" | "2h" | "120m" | "3h" | "180m" | "4h" | "240m" => "1h",
        _ => "10m",
    }
}

pub fn parse_historical_json(body: &str) -> Result<Vec<HistoryBar>, String> {
    let parsed: HistoricalResponse =
        serde_json::from_str(body).map_err(|e| format!("kite historical JSON: {e}"))?;
    let mut out = Vec::new();
    for c in parsed.candles.unwrap_or_default() {
        let time_sec = match &c.time {
            serde_json::Value::Number(n) => n.as_i64().unwrap_or(0),
            serde_json::Value::String(s) => s.parse::<i64>().unwrap_or(0),
            _ => 0,
        };
        if time_sec <= 0 {
            continue;
        }
        if ![c.open, c.high, c.low, c.close]
            .iter()
            .all(|v| v.is_finite())
        {
            continue;
        }
        out.push(HistoryBar {
            time_sec,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume.filter(|v| v.is_finite()).unwrap_or(0.0) as i64,
        });
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_strips_exchange_prefix() {
        assert_eq!(normalize_symbol("NSE:HDFCBANK"), "HDFCBANK");
        assert_eq!(normalize_symbol("nfo:banknifty"), "BANKNIFTY");
        assert_eq!(normalize_symbol("  RELIANCE  "), "RELIANCE");
    }

    #[test]
    fn interval_map_matches_frontend_caps() {
        assert_eq!(kite_interval_for_tf("10m"), ("10minute", 30));
        assert_eq!(kite_interval_for_tf("1m"), ("minute", 7));
        assert_eq!(kite_interval_for_tf("1h"), ("60minute", 60));
        assert_eq!(kite_interval_for_tf("1d"), ("day", 2000));
        assert_eq!(kite_interval_for_tf("4h"), ("60minute", 60));
    }

    #[test]
    fn persist_tf_uses_base_grid() {
        assert_eq!(persist_timeframe("10m"), "10m");
        assert_eq!(persist_timeframe("4h"), "1h");
        assert_eq!(persist_timeframe("2m"), "1m");
    }

    #[test]
    fn parse_aggregator_shape() {
        let body = r#"{
          "candles": [
            {"time": 1700000000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100},
            {"time": "1700000600", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": null}
          ]
        }"#;
        let bars = parse_historical_json(body).unwrap();
        assert_eq!(bars.len(), 2);
        assert_eq!(bars[0].time_sec, 1_700_000_000);
        assert_eq!(bars[0].volume, 100);
        assert_eq!(bars[1].volume, 0);
    }

    #[test]
    fn parse_skips_bad_rows() {
        let body = r#"{"candles":[{"time":0,"open":1,"high":1,"low":1,"close":1,"volume":1}]}"#;
        assert!(parse_historical_json(body).unwrap().is_empty());
    }
}
