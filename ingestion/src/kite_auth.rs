/// Kite Connect OAuth session token generation.
///
/// Flow:
///   1. User visits: https://kite.zerodha.com/connect/login?v=3&api_key={api_key}
///   2. After login, Zerodha redirects to your redirect_url with ?request_token=xxx
///   3. This module POSTs to /session/token with the request_token + checksum to
///      exchange it for a long-lived access_token (valid until midnight IST).
///
/// For headless/automated trading: set KITE_ACCESS_TOKEN in .env directly if you
/// already have a valid token. This module is invoked only when it is absent.

use serde::Deserialize;
use sha2::{Sha256, Digest};
use log::{info, error};

/// Shape of the Kite /session/token API success response.
#[derive(Debug, Deserialize)]
struct KiteSessionResponse {
    status: String,
    data: Option<KiteSessionData>,
    message: Option<String>,
}

#[derive(Debug, Deserialize)]
struct KiteSessionData {
    access_token: String,
    user_id: String,
    user_name: String,
}

/// Generate a Kite access_token by exchanging a request_token.
///
/// # Arguments
/// * `api_key`       — from KITE_API_KEY env var
/// * `api_secret`    — from KITE_API_SECRET env var
/// * `request_token` — obtained from OAuth redirect URL parameter
///
/// # Returns
/// The access_token string on success, or an error.
pub async fn generate_access_token(
    api_key: &str,
    api_secret: &str,
    request_token: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    // Checksum = SHA-256(api_key + request_token + api_secret) as hex string
    let raw = format!("{}{}{}", api_key, request_token, api_secret);
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    let checksum = hex::encode(hasher.finalize());

    let client = reqwest::Client::new();
    let response = client
        .post("https://api.kite.trade/session/token")
        .header("X-Kite-Version", "3")
        .form(&[
            ("api_key", api_key),
            ("request_token", request_token),
            ("checksum", checksum.as_str()),
        ])
        .send()
        .await?;

    let body: KiteSessionResponse = response.json().await?;

    if body.status != "success" {
        let msg = body.message.unwrap_or_else(|| "Unknown Kite auth error".into());
        error!("Kite session token exchange failed: {}", msg);
        return Err(msg.into());
    }

    let data = body.data.ok_or("Missing data in Kite session response")?;
    info!(
        "Kite auth successful — user: {} ({})",
        data.user_name, data.user_id
    );
    Ok(data.access_token)
}
