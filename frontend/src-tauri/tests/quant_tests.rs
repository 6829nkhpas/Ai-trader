// ── Alpha Suite V3 — Rust Integration Test Suite ────────────────────────────
//
// Validates the quant consensus engine and local SQLite trade journaling
// using hardcoded static data — zero network dependencies.
//
// Run: cargo test --test quant_tests -- --nocapture

use app_lib::quant::patterns::Candle;
use app_lib::quant::{ConsensusEngine, IndicatorState};
use rusqlite::{params, Connection};

// ═══════════════════════════════════════════════════════════════════════════════
// TEST 1: Consensus Math Accuracy
// ═══════════════════════════════════════════════════════════════════════════════

/// Constructs a known "Golden Cross + Bullish Engulfing" candle dataset and
/// verifies that `compile_consensus` correctly identifies the patterns and
/// produces the expected trend score.
///
/// Setup:
///   - 2 candles forming a Bullish Engulfing pattern
///   - IndicatorState configured for a Golden Cross (SMA50 crossing above SMA200)
///   - All momentum/volatility indicators set to produce deterministic output
#[test]
fn test_consensus_math_accuracy() {
    // ── Candle data: Classic Bullish Engulfing ──────────────────────────
    // Previous candle: bearish (open > close)
    // Current candle: bullish, body fully engulfs previous candle's body
    let candles = vec![
        // Padding candles to satisfy SMA lookback (50 candles minimum)
        // We'll use 50 identical candles + 2 pattern candles
        // First, generate 50 "neutral" candles at price ~100
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        // 10
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        // 20
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        // 30
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        // 40
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        Candle { open: 100.0, high: 101.0, low: 99.0, close: 100.0, volume: 1000.0 },
        // 50 — now the pattern candles:
        // Candle 51: Bearish candle (previous for engulfing)
        Candle { open: 105.0, high: 106.0, low: 101.0, close: 102.0, volume: 1500.0 },
        // Candle 52: Bullish engulfing — body fully wraps previous
        Candle { open: 101.0, high: 110.0, low: 100.0, close: 108.0, volume: 2000.0 },
    ];

    // ── Indicator state: Golden Cross setup ─────────────────────────────
    // SMA50 just crossed above SMA200 (prev: 50 < 200, now: 50 > 200)
    let indicators = IndicatorState {
        sma_50: 105.0,       // Current SMA50 above SMA200
        sma_200: 100.0,      // SMA200 below SMA50
        prev_sma_50: 99.0,   // Previous SMA50 was below SMA200
        prev_sma_200: 100.0, // Previous SMA200
        macd_histogram: 2.5, // Positive MACD → bullish
        parabolic_sar: 95.0, // SAR below price → bullish
        rsi_14: 55.0,        // Neutral RSI (not overbought/oversold)
        stoch_k: 60.0,       // Neutral stochastic
        bb_upper: 115.0,
        bb_lower: 90.0,
        atr_20_ma: 5.0,
        obv_current: 50000.0,
        obv_previous: 48000.0, // Rising OBV
        cmf: 0.08,             // Positive CMF → accumulation
        vwap: 103.0,
        average_volume: 1200.0,
        orb_high: 107.0,
        orb_low: 98.0,
    };

    // ── Execute consensus compilation ───────────────────────────────────
    let report = ConsensusEngine::compile_consensus("TESTSTOCK", &candles, &indicators);

    println!("═══ Consensus Report ═══");
    println!("Symbol:          {}", report.symbol);
    println!("Trend Score:     {}", report.trend_score);
    println!("Momentum:        {}", report.momentum_state);
    println!("Volatility:      {}", report.volatility_state);
    println!("Volume Flow:     {}", report.volume_flow_state);
    println!("Active Patterns: {:?}", report.active_patterns);
    println!("Active Strategies: {:?}", report.active_strategies);

    // ── Assertions ──────────────────────────────────────────────────────

    // Pattern detection: Bullish Engulfing must be detected
    assert!(
        report.active_patterns.contains(&"Bullish Engulfing".to_string()),
        "Expected 'Bullish Engulfing' in active_patterns, got: {:?}",
        report.active_patterns
    );

    // Strategy detection: Golden Cross must be detected
    // (prev_sma_50 < prev_sma_200 AND sma_50 > sma_200)
    assert!(
        report.active_strategies.contains(&"Golden Cross".to_string()),
        "Expected 'Golden Cross' in active_strategies, got: {:?}",
        report.active_strategies
    );

    // Trend score: All 4 indicators are bullish:
    //   close(108) > sma_50(105) → +25
    //   close(108) > sma_200(100) → +25
    //   macd_histogram(2.5) > 0 → +25
    //   parabolic_sar(95) < close(108) → +25
    //   Total = 100
    assert_eq!(
        report.trend_score, 100,
        "Expected trend_score=100 (all bullish), got: {}",
        report.trend_score
    );

    // Momentum: RSI=55, Stoch=60 → neither OB nor OS → NEUTRAL
    assert_eq!(report.momentum_state, "NEUTRAL");

    // Volume flow: OBV rising + CMF > 0.05 → ACCUMULATION
    assert_eq!(report.volume_flow_state, "ACCUMULATION");

    println!("\n✅ test_consensus_math_accuracy PASSED");
}

// ═══════════════════════════════════════════════════════════════════════════════
// TEST 2: SQLite Trade Journaling
// ═══════════════════════════════════════════════════════════════════════════════

/// Creates an in-memory SQLite database, runs the trade journal schema,
/// inserts a mock trade, and verifies retrieval — no filesystem dependency.
#[test]
fn test_sqlite_trade_journaling() {
    // ── Create in-memory database ───────────────────────────────────────
    let conn = Connection::open_in_memory()
        .expect("Failed to open in-memory SQLite database");

    // ── Run schema migration (mirrors db.rs init_db) ────────────────────
    conn.execute_batch("PRAGMA journal_mode=WAL;").ok();

    conn.execute(
        "CREATE TABLE IF NOT EXISTS trades (
            id          TEXT PRIMARY KEY,
            symbol      TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price  REAL NOT NULL,
            pnl         REAL NOT NULL,
            pos_type    TEXT NOT NULL DEFAULT 'LONG',
            size        REAL NOT NULL DEFAULT 1.0,
            timestamp   INTEGER NOT NULL
        );",
        [],
    ).expect("Failed to create trades table");

    // ── Insert a mock trade ─────────────────────────────────────────────
    let trade_id = "test-trade-001";
    let symbol = "RELIANCE";
    let entry_price = 2450.50;
    let exit_price = 2520.75;
    let pnl = 70.25;
    let pos_type = "LONG";
    let size = 10.0;
    let timestamp: i64 = 1716000000; // Fixed timestamp

    conn.execute(
        "INSERT INTO trades (id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![trade_id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp],
    ).expect("Failed to insert mock trade");

    println!("Inserted trade: {} {} @ {} → {} | PNL: {}", pos_type, symbol, entry_price, exit_price, pnl);

    // ── Retrieve and verify ─────────────────────────────────────────────
    let (ret_id, ret_symbol, ret_entry, ret_exit, ret_pnl, ret_type, ret_size, ret_ts): (
        String, String, f64, f64, f64, String, f64, i64,
    ) = conn
        .query_row(
            "SELECT id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp
             FROM trades WHERE id = ?1",
            params![trade_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                    row.get(7)?,
                ))
            },
        )
        .expect("Failed to retrieve trade from database");

    // ── Assertions ──────────────────────────────────────────────────────
    assert_eq!(ret_id, trade_id);
    assert_eq!(ret_symbol, symbol);
    assert!((ret_entry - entry_price).abs() < f64::EPSILON);
    assert!((ret_exit - exit_price).abs() < f64::EPSILON);
    assert!((ret_pnl - pnl).abs() < f64::EPSILON);
    assert_eq!(ret_type, pos_type);
    assert!((ret_size - size).abs() < f64::EPSILON);
    assert_eq!(ret_ts, timestamp);

    // ── Test UPSERT (ON CONFLICT) behavior ──────────────────────────────
    let updated_exit = 2550.00;
    let updated_pnl = 99.50;

    conn.execute(
        "INSERT INTO trades (id, symbol, entry_price, exit_price, pnl, pos_type, size, timestamp)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
         ON CONFLICT(id) DO UPDATE SET
             exit_price = excluded.exit_price,
             pnl = excluded.pnl;",
        params![trade_id, symbol, entry_price, updated_exit, updated_pnl, pos_type, size, timestamp],
    ).expect("Failed to upsert trade");

    let (final_exit, final_pnl): (f64, f64) = conn
        .query_row(
            "SELECT exit_price, pnl FROM trades WHERE id = ?1",
            params![trade_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .expect("Failed to retrieve updated trade");

    assert!((final_exit - updated_exit).abs() < f64::EPSILON);
    assert!((final_pnl - updated_pnl).abs() < f64::EPSILON);

    // ── Verify row count (should still be 1 after upsert) ───────────────
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM trades", [], |row| row.get(0))
        .expect("Failed to count trades");

    assert_eq!(count, 1, "Expected 1 trade after upsert, got {}", count);

    println!("\n✅ test_sqlite_trade_journaling PASSED");
}

// ═══════════════════════════════════════════════════════════════════════════════
// TEST 3: Pattern Engine Isolation
// ═══════════════════════════════════════════════════════════════════════════════

/// Validates individual pattern detection with minimal candle data.
#[test]
fn test_pattern_engine_isolation() {
    use app_lib::quant::patterns::PatternEngine;

    // ── Doji detection ──────────────────────────────────────────────────
    let doji_candles = vec![
        Candle { open: 100.0, high: 105.0, low: 95.0, close: 100.05, volume: 500.0 },
    ];
    let doji_result = PatternEngine::analyze(&doji_candles);
    assert!(
        doji_result.contains(&"Doji".to_string()),
        "Expected Doji detection, got: {:?}",
        doji_result
    );

    // ── Hammer detection ────────────────────────────────────────────────
    // Long lower shadow (≥2x body), small upper shadow (≤33% of range)
    let hammer_candles = vec![
        Candle { open: 100.0, high: 101.0, low: 94.0, close: 100.5, volume: 800.0 },
        // body = 0.5, lower_shadow = 100.0 - 94.0 = 6.0 (≥ 0.5*2=1.0 ✓)
        // upper_shadow = 101.0 - 100.5 = 0.5, range = 7.0, 0.5 ≤ 7.0*0.33=2.31 ✓
    ];
    let hammer_result = PatternEngine::analyze(&hammer_candles);
    assert!(
        hammer_result.contains(&"Hammer".to_string()),
        "Expected Hammer detection, got: {:?}",
        hammer_result
    );

    // ── Shooting Star detection ─────────────────────────────────────────
    // Long upper shadow (≥2x body), small lower shadow (≤33% of range)
    let star_candles = vec![
        Candle { open: 100.0, high: 107.0, low: 99.5, close: 100.5, volume: 900.0 },
        // body = 0.5, upper_shadow = 107.0 - 100.5 = 6.5 (≥ 0.5*2=1.0 ✓)
        // lower_shadow = 100.0 - 99.5 = 0.5, range = 7.5, 0.5 ≤ 7.5*0.33=2.475 ✓
    ];
    let star_result = PatternEngine::analyze(&star_candles);
    assert!(
        star_result.contains(&"Shooting Star".to_string()),
        "Expected Shooting Star detection, got: {:?}",
        star_result
    );

    println!("\n✅ test_pattern_engine_isolation PASSED");
}
