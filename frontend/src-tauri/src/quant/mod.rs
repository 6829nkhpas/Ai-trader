// quant/mod.rs — Re-export shim over the shared `quant-core` crate.
//
// The full V3 consensus engine, indicator matrix, pattern/chart-pattern
// engines, VWEPR, predictive projection, S/R, validators and their types were
// extracted verbatim into the Tauri-free `quant-core` crate so the standalone
// `tool-server` binary can share them. They are re-exported here so every
// existing `crate::quant::*` path across the desktop app keeps resolving
// unchanged (same types, one source of truth).
//
// Only the Tauri-coupled, desktop-only modules remain physically in this crate.

pub use quant_core::*;

// Desktop-only, Tauri-coupled modules (AppHandle / event emits / WS bridges).
pub mod radar;
pub mod tool_server;
