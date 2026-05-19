// ── Alpha Suite V3 — External API Contract Tests ───────────────────────────
//
// Strict data-contract testing for every outbound API the Tauri backend
// touches (DeepSeek LLM today; News API & Market Data follow the same
// pattern). These tests stand up a `mockito::Server`, redirect the real
// reqwest client at it, and verify two opposing axes:
//
//   1. Happy path — the request payload contains the exact ConsensusReport
//      strings, and the mocked response parses cleanly into the typed
//      `AiExecutionPlan` struct without panics or field mismatches.
//
//   2. Resilience — when the upstream returns 429 Too Many Requests,
//      malformed JSON, or an empty body, the backend safely surfaces a
//      `Result::Err("LLM API Failure: …")` instead of panicking.
//
// Run: cargo test --test api_tests -- --nocapture

use app_lib::quant::{AiExecutionPlan, ConsensusReport};
use app_lib::services::llm::{self, build_request_body};
use mockito::Matcher;
use serde_json::json;
use std::sync::Mutex;

// All tests in this file mutate process-wide environment variables
// (`DEEPSEEK_API_KEY`, `ALPHA_TEST_MODE`, …). Cargo runs `#[tokio::test]`
// cases in parallel by default, so we serialise execution through a
// global lock to keep env state deterministic.
static ENV_LOCK: Mutex<()> = Mutex::new(());

fn lock_env<'a>() -> std::sync::MutexGuard<'a, ()> {
    ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner())
}

// ── Fixtures ────────────────────────────────────────────────────────────────

fn fixture_consensus() -> ConsensusReport {
    ConsensusReport {
        symbol: "RELIANCE".to_string(),
        trend_score: 75,
        momentum_state: "NEUTRAL".to_string(),
        volatility_state: "NORMAL".to_string(),
        volume_flow_state: "ACCUMULATION".to_string(),
        active_patterns: vec!["Bullish Engulfing".to_string(), "Hammer".to_string()],
        active_strategies: vec!["Golden Cross".to_string(), "VWAP Bounce (Bullish)".to_string()],
    }
}

const FIXTURE_NEWS: &str = "Reliance posts strong Q3 earnings; refining margins expand sequentially.";

// Ensure the live env doesn't leak into the test (audit log + key fallback).
fn isolate_env() {
    // Remove production keys if exported in the dev shell so tests
    // exercise the fallback path with a deterministic TEST_KEY.
    std::env::remove_var("HF_API_KEY");
    std::env::remove_var("HUGGINGFACE_API_KEY");
    std::env::remove_var("HUGGING_FACE_API_KEY");
    std::env::remove_var("NVIDIA_API_KEY");
    std::env::remove_var("LLM_API_URL");
    std::env::remove_var("HF_API_URL");
    std::env::remove_var("DEEPSEEK_API_URL");
    std::env::remove_var("NVIDIA_NIM_API_URL");
    std::env::set_var("DEEPSEEK_API_KEY", "TEST_KEY");
    std::env::set_var("DEEPSEEK_MODEL", "deepseek-chat");
    std::env::remove_var("ALPHA_TEST_MODE"); // we want the real code path
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 1 — Pure builder contract
//
// Verifies the request payload assembled by `build_request_body` contains
// every ConsensusReport string verbatim. Catches prompt-template drift
// before a single byte hits the network.
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn test_request_contract_carries_consensus_strings() {
    let consensus = fixture_consensus();
    let req = build_request_body("RELIANCE", &consensus, FIXTURE_NEWS, "deepseek-chat");

    // Wire-format snapshot of the request — exactly what reqwest will send.
    let serialized = serde_json::to_string(&req).expect("serialize request");

    assert!(serialized.contains("RELIANCE"),       "symbol missing in payload");
    assert!(serialized.contains("75"),              "trend_score missing");
    assert!(serialized.contains("NEUTRAL"),         "momentum_state missing");
    assert!(serialized.contains("NORMAL"),          "volatility_state missing");
    assert!(serialized.contains("ACCUMULATION"),    "volume_flow_state missing");
    assert!(serialized.contains("Bullish Engulfing"), "pattern missing");
    assert!(serialized.contains("Hammer"),          "pattern missing");
    assert!(serialized.contains("Golden Cross"),    "strategy missing");
    assert!(serialized.contains("VWAP Bounce"),     "strategy missing");
    assert!(serialized.contains("Reliance posts strong"), "news missing");

    // System prompt must constrain the LLM to JSON-only output.
    assert!(serialized.contains("Elite Quantitative Portfolio Manager"));
    assert!(serialized.contains("conviction_score"));
    assert!(serialized.contains("setup_validation"));
    assert!(serialized.contains("execution_plan"));

    // Response format coercion is critical for deterministic parsing.
    // We deliberately omit `response_format` because some providers (NIM,
    // some HF backends) reject it. The system prompt + post-fence strip
    // is what guarantees JSON output.
    assert!(req.response_format.is_none());
    assert_eq!(req.temperature, 0.3);
    assert_eq!(req.model, "deepseek-chat");

    println!("\n✅ test_request_contract_carries_consensus_strings PASSED");
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 2 — Happy Path: mocked DeepSeek returns valid AiExecutionPlan
//
// Mocks a 200 OK on the DeepSeek chat-completions endpoint, asserts the
// request body matches the contract, and asserts the parsed result equals
// the canonical struct.
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn test_deepseek_happy_path_parses_into_struct() {
    let _env_guard = lock_env();
    isolate_env();

    let mut server = mockito::Server::new_async().await;

    let mock_plan = json!({
        "conviction_score": 82,
        "setup_validation": "Golden Cross confirmed by rising OBV and bullish engulfing. \
            Volume flow accumulation supports continuation above VWAP.",
        "execution_plan": "ENTRY 2470 | STOP 2435 | T1 2510 | T2 2550 | SIZE 2% | \
            INVALIDATE on close < SMA50.",
    });

    let envelope = json!({
        "id": "chatcmpl-mock-001",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": mock_plan.to_string(),
            },
            "finish_reason": "stop",
        }],
    });

    let mock = server
        .mock("POST", "/v1/chat/completions")
        // The request must carry the consensus strings — Matcher::AllOf
        // gives us a single assertion that captures the entire contract.
        .match_body(Matcher::AllOf(vec![
            Matcher::PartialJsonString(json!({ "model": "deepseek-chat" }).to_string()),
            Matcher::Regex("RELIANCE".into()),
            Matcher::Regex("Bullish Engulfing".into()),
            Matcher::Regex("Golden Cross".into()),
            Matcher::Regex("ACCUMULATION".into()),
        ]))
        .match_header("authorization", Matcher::Regex("Bearer .+".into()))
        .match_header("content-type", "application/json")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(envelope.to_string())
        .create_async()
        .await;

    let url = format!("{}/v1/chat/completions", server.url());

    let result: Result<AiExecutionPlan, String> = llm::generate_deep_quant_plan_with_url(
        "RELIANCE",
        &fixture_consensus(),
        FIXTURE_NEWS,
        &url,
        None, // no AppHandle in tests — falls back to env var key resolution
    )
    .await;

    mock.assert_async().await;

    let plan = result.expect("happy path must succeed");
    assert_eq!(plan.conviction_score, 82);
    assert!(plan.setup_validation.contains("Golden Cross"));
    assert!(plan.execution_plan.contains("ENTRY 2470"));
    assert!(plan.execution_plan.contains("STOP 2435"));

    println!("\n✅ test_deepseek_happy_path_parses_into_struct PASSED");
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 3 — Resilience: 429 Too Many Requests is handled, not panicked
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn test_deepseek_handles_429_rate_limit() {
    let _env_guard = lock_env();
    isolate_env();

    let mut server = mockito::Server::new_async().await;

    let mock = server
        .mock("POST", "/v1/chat/completions")
        .with_status(429)
        .with_header("content-type", "application/json")
        .with_body(r#"{"error":{"message":"rate_limited"}}"#)
        .create_async()
        .await;

    let url = format!("{}/v1/chat/completions", server.url());

    let result = llm::generate_deep_quant_plan_with_url(
        "RELIANCE",
        &fixture_consensus(),
        FIXTURE_NEWS,
        &url,
        None,
    )
    .await;

    mock.assert_async().await;

    let err = result.expect_err("429 must surface as Err, never panic");
    assert!(
        err.contains("LLM API Failure"),
        "expected 'LLM API Failure' marker, got: {}",
        err
    );
    assert!(err.contains("429"), "error message should reference status: {}", err);

    println!("\n✅ test_deepseek_handles_429_rate_limit PASSED ({})", err);
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 4 — Resilience: malformed JSON envelope is handled
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn test_deepseek_handles_malformed_json() {
    let _env_guard = lock_env();
    isolate_env();

    let mut server = mockito::Server::new_async().await;

    let mock = server
        .mock("POST", "/v1/chat/completions")
        .with_status(200)
        .with_header("content-type", "application/json")
        // Truncated/broken envelope — not a valid ChatResponse.
        .with_body("{ this is :: not json ")
        .create_async()
        .await;

    let url = format!("{}/v1/chat/completions", server.url());

    let result = llm::generate_deep_quant_plan_with_url(
        "RELIANCE",
        &fixture_consensus(),
        FIXTURE_NEWS,
        &url,
        None,
    )
    .await;

    mock.assert_async().await;

    let err = result.expect_err("malformed JSON must surface as Err, never panic");
    assert!(
        err.contains("LLM API Failure"),
        "expected 'LLM API Failure' marker, got: {}",
        err
    );

    println!("\n✅ test_deepseek_handles_malformed_json PASSED ({})", err);
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 5 — Resilience: LLM `content` field is malformed, not the envelope
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn test_deepseek_handles_malformed_inner_content() {
    let _env_guard = lock_env();
    isolate_env();

    let mut server = mockito::Server::new_async().await;

    let envelope = json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "this is not the json you are looking for",
            }
        }]
    });

    let mock = server
        .mock("POST", "/v1/chat/completions")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(envelope.to_string())
        .create_async()
        .await;

    let url = format!("{}/v1/chat/completions", server.url());

    let result = llm::generate_deep_quant_plan_with_url(
        "RELIANCE",
        &fixture_consensus(),
        FIXTURE_NEWS,
        &url,
        None,
    )
    .await;

    mock.assert_async().await;

    let err = result.expect_err("malformed LLM content must surface as Err");
    assert!(
        err.contains("LLM API Failure"),
        "expected 'LLM API Failure' marker, got: {}",
        err
    );

    println!("\n✅ test_deepseek_handles_malformed_inner_content PASSED ({})", err);
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST 6 — Audit logger writes a transaction record under ALPHA_TEST_MODE
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn test_audit_logger_writes_to_disk_in_test_mode() {
    use std::fs;
    use std::path::PathBuf;

    let _env_guard = lock_env();

    // Activate the audit logger.
    std::env::set_var("ALPHA_TEST_MODE", "1");
    std::env::remove_var("HF_API_KEY");
    std::env::remove_var("HUGGINGFACE_API_KEY");
    std::env::remove_var("HUGGING_FACE_API_KEY");
    std::env::remove_var("NVIDIA_API_KEY");
    std::env::remove_var("LLM_API_URL");
    std::env::remove_var("HF_API_URL");
    std::env::remove_var("DEEPSEEK_API_URL");
    std::env::remove_var("NVIDIA_NIM_API_URL");
    std::env::set_var("DEEPSEEK_API_KEY", "TEST_KEY");

    // Clean any prior report so the assertion isn't fooled by stale data.
    let report_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("api_audit_report.log");
    let _ = fs::remove_file(&report_path);

    let mut server = mockito::Server::new_async().await;

    let envelope = json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json!({
                    "conviction_score": 64,
                    "setup_validation": "audit-logger contract test",
                    "execution_plan":   "ENTRY 100 | STOP 95 | T1 110",
                }).to_string(),
            }
        }]
    });

    let _mock = server
        .mock("POST", "/v1/chat/completions")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(envelope.to_string())
        .create_async()
        .await;

    let url = format!("{}/v1/chat/completions", server.url());

    let plan = llm::generate_deep_quant_plan_with_url(
        "RELIANCE",
        &fixture_consensus(),
        FIXTURE_NEWS,
        &url,
        None,
    )
    .await
    .expect("audit happy-path must succeed");
    assert_eq!(plan.conviction_score, 64);

    // The audit file must now exist and contain our endpoint URL.
    let report = fs::read_to_string(&report_path)
        .expect("api_audit_report.log must exist after a logged call");
    assert!(
        report.contains(&url),
        "audit report missing endpoint URL.\n--- report ---\n{}",
        report
    );
    assert!(report.contains("RELIANCE"),     "audit report missing symbol");
    assert!(report.contains("Golden Cross"), "audit report missing consensus strings");
    assert!(report.contains("\"status\": 200"), "audit report missing status field");

    // Reset for downstream tests.
    std::env::remove_var("ALPHA_TEST_MODE");

    println!("\n✅ test_audit_logger_writes_to_disk_in_test_mode PASSED");
    println!("   audit report at: {}", report_path.display());
}
