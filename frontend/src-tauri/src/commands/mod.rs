// src/commands/mod.rs — Tauri IPC command handlers
//
// Each sub-module exposes #[tauri::command] functions that the frontend
// can invoke via Tauri's invoke() API.

pub mod charts;
pub mod deep_quant;
pub mod sentiment;
