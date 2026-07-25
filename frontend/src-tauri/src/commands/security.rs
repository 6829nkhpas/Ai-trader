// commands/security.rs — System command(s).
//
// The local encrypted credential vault (Stronghold-backed API-key store) was
// removed: exposing an API-key / endpoint entry surface on the client is an
// exfiltration and pentesting risk. All credentials (LLM/OpenRouter keys, broker
// tokens) are now provisioned and managed exclusively by the backend and never
// stored on or entered through the desktop client.
//
// Only the OS "open URL in browser" helper remains here (used by the auth /
// broker OAuth redirect flows).

use log::info;

/// Opens a URL in the user's default OS browser.
/// Used by the auth / broker OAuth redirect flows where an in-app webview open
/// is unreliable.
#[tauri::command]
pub fn open_browser(url: String) -> Result<(), String> {
    info!("[system] Request to open URL in browser: {}", url);
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/c", "start", "", &url])
            .spawn()
            .map_err(|e| format!("Failed to open browser: {}", e))?;
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
