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

/// Whether `symbol` looks like an NFO tradingsymbol (a CE / PE / FUT contract).
///
/// Mirrors `frontend/src/charting/symbolUtils.ts::isFnoSymbol`.
fn is_fno_symbol(symbol: &str) -> bool {
    let upper = symbol.trim().to_uppercase();
    if upper.is_empty() {
        return false;
    }
    if upper.ends_with("FUT") {
        return true;
    }
    if upper.ends_with("CE") || upper.ends_with("PE") {
        return upper.chars().any(|c| c.is_ascii_digit());
    }
    false
}

/// The instrument whose news a symbol should be looked up under.
///
/// Nobody publishes news about a single option contract — it is published about
/// the company or the index the contract derives from. Both news paths keyed off
/// the raw tradingsymbol, so an F&O run searched Google News for
/// "RELIANCE26AUG1290CE stock NSE India" (no such article exists) and asked the
/// sentiment service for a ticker it has no profile for, and the agent was handed
/// an `Unavailable` news catalyst on every single derivatives run. Resolve to the
/// underlying first: `RELIANCE26AUG1290CE` -> `RELIANCE`,
/// `BANKNIFTY24DECFUT` -> `BANKNIFTY`.
///
/// Only genuine contracts are rewritten, which is why this is gated on
/// `is_fno_symbol` rather than just cutting at the first digit: the equity
/// tickers V2RETAIL and A2ZINFRA would otherwise collapse to "V" and "A".
/// Anything else — cash tickers, index spot names like "NIFTY 50" — is returned
/// with only whitespace trimmed. Mirrors `sentimentSubject` in
/// `frontend/src/store/useQuantStore.ts`.
pub fn news_subject(symbol: &str) -> String {
    let trimmed = symbol.trim();
    if !is_fno_symbol(trimmed) {
        return trimmed.to_string();
    }
    let upper = trimmed.to_uppercase();
    // The leading run of letters is the underlying's derivative name: the expiry
    // and strike that follow always begin with a digit.
    let prefix: String = upper.chars().take_while(|c| c.is_ascii_alphabetic()).collect();
    if prefix.is_empty() || prefix.len() == upper.len() {
        // No digit boundary to cut at (e.g. a bare "...FUT" with no expiry) —
        // there is nothing to strip, so do not guess.
        return trimmed.to_string();
    }
    prefix
}

#[cfg(test)]
mod tests {
    use super::news_subject;

    #[test]
    fn resolves_contracts_to_their_underlying() {
        // Options: the reported case, plus weekly/monthly index formats.
        assert_eq!(news_subject("RELIANCE26AUG1290CE"), "RELIANCE");
        assert_eq!(news_subject("RELIANCE24DEC2500PE"), "RELIANCE");
        assert_eq!(news_subject("NIFTY2670724000CE"), "NIFTY");
        assert_eq!(news_subject("BANKNIFTY26AUG52000PE"), "BANKNIFTY");
        // Futures.
        assert_eq!(news_subject("BANKNIFTY24DECFUT"), "BANKNIFTY");
        assert_eq!(news_subject("RELIANCE26AUGFUT"), "RELIANCE");
        // Lower case in, canonical upper case out.
        assert_eq!(news_subject("reliance26aug1290ce"), "RELIANCE");
    }

    #[test]
    fn leaves_non_contracts_untouched() {
        assert_eq!(news_subject("RELIANCE"), "RELIANCE");
        assert_eq!(news_subject("NIFTY 50"), "NIFTY 50");
        assert_eq!(news_subject("M&M"), "M&M");
        assert_eq!(news_subject(""), "");
        assert_eq!(news_subject("  TCS  "), "TCS");
    }

    #[test]
    fn does_not_truncate_equities_that_merely_contain_a_digit() {
        // The whole reason this is gated on is_fno_symbol: cutting at the first
        // digit unconditionally would turn these real NSE tickers into "V"/"A".
        assert_eq!(news_subject("V2RETAIL"), "V2RETAIL");
        assert_eq!(news_subject("A2ZINFRA"), "A2ZINFRA");
        assert_eq!(news_subject("3MINDIA"), "3MINDIA");
        // Ends in "CE" but is an equity with no digit — Action Construction Equipment.
        assert_eq!(news_subject("ACE"), "ACE");
    }
}
