// src/commands/updater.rs — Auto-update: check, download, install.
//
// ── Why this is driven from Rust rather than the JS plugin API ──────────────
// The update feed CANNOT be public. Shipped installers have credentials baked
// into the binary at compile time (`QUESTDB_PASSWORD`, `LLM_API_KEY` via
// `option_env!` — see server.rs / services/llm.rs), so anyone who can download
// an installer can extract them. The feed therefore sits behind the same
// basic-auth the rest of the gateway uses (`/updates/*` in infra/caddy/Caddyfile).
//
// `tauri.conf.json` can declare static request headers, but putting a credential
// there would write it into a plaintext config shipped inside the bundle. Going
// through `updater_builder()` instead lets the header be assembled at runtime
// from the SAME baked credential the app already uses for QuestDB and
// deep-quant, so the update channel introduces no new secret and no new
// plaintext copy of an existing one.
//
// ── What is verified ────────────────────────────────────────────────────────
// Transport auth only proves the feed is ours. Authenticity of the payload is
// enforced by the updater's minisign signature check against the `pubkey` in
// tauri.conf.json: an installer that was not signed with our private key is
// rejected before it is ever executed. A compromised host serving a valid
// response therefore still cannot push arbitrary code.

use log::{error, info, warn};
use serde::Serialize;
use tauri::{AppHandle, Emitter};
use tauri_plugin_updater::UpdaterExt;

/// Update metadata handed to the UI. Deliberately small — the frontend only
/// needs enough to tell the user what is available.
#[derive(Debug, Clone, Serialize)]
pub struct UpdateInfo {
    /// Version being offered (from the manifest).
    pub version: String,
    /// Version currently running.
    pub current_version: String,
    /// Release notes, when the manifest carries them.
    pub notes: Option<String>,
    /// Publish date as reported by the manifest.
    pub date: Option<String>,
}

/// Progress of an in-flight download, emitted as `update-download-progress`.
#[derive(Debug, Clone, Serialize)]
struct DownloadProgress {
    downloaded: u64,
    /// `None` when the server did not send a Content-Length.
    total: Option<u64>,
}

/// Build an updater bound to the authenticated feed.
///
/// Returns the plugin's own error type so callers can distinguish "no update"
/// from "could not reach the feed" — the UI must never present a network blip as
/// "you are up to date".
fn authenticated_updater(app: &AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    // Same shared credential as the QuestDB / deep-quant / Kite routes.
    let user = crate::server::questdb_user();
    let password = crate::server::questdb_password();
    let basic = {
        use base64::Engine as _;
        base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", user, password))
    };

    app.updater_builder()
        .header("Authorization", format!("Basic {}", basic))
        .map_err(|e| format!("updater header rejected: {}", e))?
        .build()
        .map_err(|e| format!("updater init failed: {}", e))
}

/// Check the feed for a newer version.
///
/// `Ok(None)` means "definitively up to date". A transport/manifest failure is
/// an `Err`, so the caller can stay silent instead of claiming the app is
/// current when it simply could not ask.
#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    let updater = authenticated_updater(&app)?;

    match updater.check().await {
        Ok(Some(update)) => {
            info!(
                "[updater] update available: {} -> {}",
                update.current_version, update.version
            );
            Ok(Some(UpdateInfo {
                version: update.version.clone(),
                current_version: update.current_version.clone(),
                notes: update.body.clone(),
                date: update.date.map(|d| d.to_string()),
            }))
        }
        Ok(None) => {
            info!("[updater] no update available — running the latest version.");
            Ok(None)
        }
        Err(e) => {
            // Downgraded to a warning on purpose: an unreachable feed (offline
            // user, gateway restart) is expected operationally and must not
            // surface as an error dialog on every launch.
            warn!("[updater] check failed: {}", e);
            Err(format!("update check failed: {}", e))
        }
    }
}

/// Download and install the pending update, then report completion.
///
/// Emits `update-download-progress` while downloading and `update-ready` when
/// the installer has been staged. This deliberately does NOT relaunch: the app
/// is a live trading terminal and killing it mid-position is unacceptable, so
/// the restart is left to an explicit user action (see `relaunch_app`).
#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<String, String> {
    let updater = authenticated_updater(&app)?;

    let update = updater
        .check()
        .await
        .map_err(|e| format!("update check failed: {}", e))?
        .ok_or_else(|| "no update available".to_string())?;

    let version = update.version.clone();
    info!("[updater] downloading {} …", version);

    let mut downloaded: u64 = 0;
    let progress_app = app.clone();

    update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                // Best-effort telemetry: a failed emit must not abort a download
                // that is otherwise progressing fine.
                let _ = progress_app.emit(
                    "update-download-progress",
                    DownloadProgress { downloaded, total },
                );
            },
            || {
                info!("[updater] download finished — installing.");
            },
        )
        .await
        .map_err(|e| {
            error!("[updater] install failed: {}", e);
            format!("update install failed: {}", e)
        })?;

    info!("[updater] {} staged — pending relaunch.", version);
    let _ = app.emit("update-ready", version.clone());
    Ok(version)
}

/// Restart the app so a staged update takes effect.
///
/// Separate from `install_update` so the user chooses the moment. `restart`
/// never returns.
#[tauri::command]
pub fn relaunch_app(app: AppHandle) {
    info!("[updater] relaunching to apply the update.");
    app.restart();
}
