// src/services/mod.rs — Backend service modules for the Tauri application
//
// Houses the historical data pipeline, QuestDB integration, and LLM bridge.

pub mod audit_logger;
pub mod history_loader;
pub mod instrument_master;
pub mod live_bridges;
pub mod llm;
