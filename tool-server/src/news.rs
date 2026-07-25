// news.rs — Google News RSS headline fetch (keyless, no API required).
//
// Ported from the desktop `commands::sentiment::fetch_news_headlines` /
// `deep_quant::fetch_google_news_rss_for_context` so the standalone tool-server
// surfaces the same headlines the operator sees, feeding get_news_context.

/// Fetch up to 10 recent Google News RSS headlines for a symbol. Returns an
/// empty vec on any failure (caller degrades gracefully).
pub async fn fetch_news_headlines(symbol: &str) -> Vec<String> {
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };

    let query = format!("{} stock NSE India", symbol);
    let rss_url = format!(
        "https://news.google.com/rss/search?q={}&hl=en-IN&gl=IN&ceid=IN:en",
        urlencoding::encode(&query)
    );

    let body = match client
        .get(&rss_url)
        .header("User-Agent", "Mozilla/5.0 (compatible; StratAi/1.0)")
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => resp.text().await.unwrap_or_default(),
        _ => return Vec::new(),
    };

    let mut headlines: Vec<String> = Vec::new();
    let mut search_from = 0usize;
    loop {
        let start_tag = match body[search_from..].find("<title>") {
            Some(pos) => search_from + pos + 7,
            None => break,
        };
        let end_tag = match body[start_tag..].find("</title>") {
            Some(pos) => start_tag + pos,
            None => break,
        };
        let raw = &body[start_tag..end_tag];
        search_from = end_tag + 8;

        let decoded = raw
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace("<![CDATA[", "")
            .replace("]]>", "");
        let trimmed = decoded.trim().to_string();

        if trimmed.is_empty()
            || trimmed == "Google News"
            || trimmed.starts_with('"')
            || trimmed.len() < 10
        {
            continue;
        }
        headlines.push(trimmed);
        if headlines.len() >= 10 {
            break;
        }
    }

    headlines
}
