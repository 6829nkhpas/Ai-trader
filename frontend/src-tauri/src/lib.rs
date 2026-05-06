use tauri::Emitter;
use futures_util::StreamExt;
use tokio_tungstenite::connect_async;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      let app_handle = app.handle().clone();
      tauri::async_runtime::spawn(async move {
          if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8081").await {
              let (_, mut read) = ws_stream.split();
              while let Some(message) = read.next().await {
                  if let Ok(msg) = message {
                      if let Ok(text) = msg.into_text() {
                          if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                              let _ = app_handle.emit("ohlc-tick", json);
                          }
                      }
                  }
              }
          }
      });

      let app_handle_2 = app.handle().clone();
      tauri::async_runtime::spawn(async move {
          if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8082").await {
              let (_, mut read) = ws_stream.split();
              while let Some(message) = read.next().await {
                  if let Ok(msg) = message {
                      if let Ok(text) = msg.into_text() {
                          if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                              let _ = app_handle_2.emit("predictive-tick", json);
                          }
                      }
                  }
              }
          }
      });

      // ── Quant-RAG Insight WS → IPC Bridge (port 8083) ──────────────
      let app_handle_3 = app.handle().clone();
      tauri::async_runtime::spawn(async move {
          if let Ok((ws_stream, _)) = connect_async("ws://127.0.0.1:8083").await {
              let (_, mut read) = ws_stream.split();
              while let Some(message) = read.next().await {
                  if let Ok(msg) = message {
                      if let Ok(text) = msg.into_text() {
                          if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
                              let _ = app_handle_3.emit("insight-tick", json);
                          }
                      }
                  }
              }
          }
      });

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
