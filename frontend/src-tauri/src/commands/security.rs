// commands/security.rs — System command(s).
//
// The local encrypted credential vault (Stronghold-backed API-key store) was
// removed: exposing an API-key / endpoint entry surface on the client is an
// exfiltration and pentesting risk. All credentials (LLM/OpenRouter keys, broker
// tokens) are now provisioned and managed exclusively by the backend and never
// stored on or entered through the desktop client.
//
// Only the OS "open URL in browser" helper remains here (used by the auth /
// broker OAuth redirect flows), plus a host-restricted HTTP proxy (api_fetch)
// used by the frontend to reach the backend without hitting webview CORS —
// the packaged app's origin (tauri.localhost) is not on the server's CORS
// allowlist, so a browser-context fetch() is blocked. reqwest runs
// server-to-server and is not subject to CORS.

use log::info;
use std::collections::HashMap;

/// Opens a URL in the user's default OS browser.
/// Used by the auth / broker OAuth redirect flows where an in-app webview open
/// is unreliable.
#[tauri::command]
pub fn open_browser(url: String) -> Result<(), String> {
    info!("[system] Request to open URL in browser: {}", url);
    #[cfg(target_os = "windows")]
    {
        // `rundll32 url.dll,FileProtocolHandler` is the most reliable way to
        // open a URL in the default browser from a GUI (no-console) process.
        // `explorer.exe <url>` opens File Explorer when the URL has query
        // parameters, so it must NOT be used for URLs. Fall back to
        // `cmd /c start` if rundll32 is somehow unavailable.
        let r = std::process::Command::new("rundll32.exe")
            .args(["url.dll,FileProtocolHandler", &url])
            .spawn();
        if let Err(e1) = r {
            info!("[system] rundll32 launch failed ({e1}); trying cmd start");
            std::process::Command::new("cmd")
                .args(["/c", "start", "", &url])
                .spawn()
                .map_err(|e2| format!("Failed to open browser (rundll32:{e1}; cmd:{e2})"))?;
        }
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&url)
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(&url)
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
    }
    Ok(())
}

/// Response returned by `api_fetch` to the frontend. Mirrors the parts of the
/// web `Response` the API client consumes (status, ok, text body).
#[derive(serde::Serialize)]
pub struct ApiFetchResponse {
    pub status: u16,
    pub ok: bool,
    pub body: String,
}

/// Host-restricted HTTP proxy for backend calls. The packaged webview origin
/// (`tauri.localhost`) is not on the backend CORS allowlist, so a browser
/// `fetch()` to api-web.stratai.live is blocked. reqwest runs server-to-server
/// (no CORS), so the frontend routes backend requests through this command.
///
/// Only `*.stratai.live` (and localhost, for dev) hosts are permitted, so this
/// cannot be abused as an open proxy.
#[tauri::command]
pub async fn api_fetch(
    method: String,
    url: String,
    headers: Option<HashMap<String, String>>,
    body: Option<String>,
) -> Result<ApiFetchResponse, String> {
    // ── Host allowlist ────────────────────────────────────────────────────
    let parsed = url::Url::parse(&url).map_err(|e| format!("Invalid URL: {e}"))?;
    let host = parsed.host_str().unwrap_or_default();
    let host_ok = host == "localhost"
        || host == "127.0.0.1"
        || host == "stratai.live"
        || host.ends_with(".stratai.live");
    if !host_ok {
        return Err(format!("api_fetch: host not permitted: {host}"));
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {e}"))?;

    let http_method = reqwest::Method::from_bytes(method.to_uppercase().as_bytes())
        .map_err(|e| format!("Invalid HTTP method '{method}': {e}"))?;

    let mut req = client.request(http_method, parsed);
    if let Some(map) = headers {
        for (k, v) in map {
            req = req.header(k, v);
        }
    }
    if let Some(payload) = body {
        req = req.body(payload);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("api_fetch request failed: {e}"))?;
    let status = resp.status();
    let text = resp
        .text()
        .await
        .map_err(|e| format!("api_fetch failed reading body: {e}"))?;

    Ok(ApiFetchResponse {
        status: status.as_u16(),
        ok: status.is_success(),
        body: text,
    })
}
