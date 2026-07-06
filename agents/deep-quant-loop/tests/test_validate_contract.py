"""Unit tests for consumer-side Tool_Result_Contract revalidation.

Feature: deep-quant-analysis-hardening (task 12.3)

Requirement 4.1:
    WHEN an Analysis_Tool returns a result, THE system SHALL validate it against
    the documented Tool_Result_Contract before the result reaches the model; a
    contract-violating payload SHALL yield a structured error result (not an
    exception) and SHALL NOT reach the model as valid data.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). They verify that ``tools.validate_contract``:

  (a) returns a structured ``{"error", "contract_violation"}`` dict — and does
      NOT raise — for a malformed payload from EACH contract tool;
  (b) returns a conforming payload unchanged;
  (c) passes honest graceful-degradation markers through unchanged (they are
      data, not violations);
  (d) produces violations that ``graph._tool_result_is_error`` recognizes as a
      non-fatal tool error, so the loop never treats the violation as valid
      market data reaching the model.
"""

import json
import os
import sys

# Make the service package importable (tools.py / graph.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
import graph  # noqa: E402
from tools import validate_contract  # noqa: E402


# ── Conforming payloads, one per contract tool ───────────────────────────────
def _conforming_candles():
    return [
        {"timestamp_ms": 1_000, "open": 10.0, "high": 11.0, "low": 9.5,
         "close": 10.5, "volume": 1000.0},
        {"timestamp_ms": 2_000, "open": 10.5, "high": 12.0, "low": 10.0,
         "close": 11.5, "volume": 1500.0},
    ]


def _conforming_consensus():
    return {field: 1.0 for field in tools._CONSENSUS_REQUIRED_FIELDS}


def _conforming_sr():
    return {"pivot": 100.0, "s1": 95.0, "s2": 90.0, "s3": 85.0,
            "r1": 105.0, "r2": 110.0, "r3": 115.0,
            "recent_high": 116.0, "recent_low": 84.0}


def _conforming_news():
    return {"symbol": "RELIANCE", "headlines": ["Co beats estimates"],
            "sentiment_summary": "Bullish"}


def _conforming_prediction():
    return {"symbol": "RELIANCE", "projected_direction": "Up",
            "projected_value": 2500.0, "confidence": 0.72}


def _conforming_multi_tf():
    return {"symbol": "RELIANCE", "trend_1h": "Bullish",
            "trend_4h": "Bullish", "trend_1d": "Neutral"}


def _conforming_patterns():
    return {"symbol": "RELIANCE", "timeframe": "1d", "patterns": [
        {"pattern_type": "Inverse Head & Shoulders", "sentiment": "Bullish",
         "confidence": 0.71, "description": "Reversal formation"},
    ]}


# Each entry: (tool_name, conforming_payload, malformed_payload)
CONTRACT_CASES = [
    (
        "get_candles",
        _conforming_candles(),
        # Second candle is missing the required 'volume' field.
        [
            {"timestamp_ms": 1_000, "open": 10.0, "high": 11.0, "low": 9.5,
             "close": 10.5, "volume": 1000.0},
            {"timestamp_ms": 2_000, "open": 10.5, "high": 12.0, "low": 10.0,
             "close": 11.5},
        ],
    ),
    (
        "get_consensus_report",
        _conforming_consensus(),
        # 'rsi_14' present but a non-numeric string — not numeric-or-null.
        {**_conforming_consensus(), "rsi_14": "oversold"},
    ),
    (
        "get_support_resistance",
        _conforming_sr(),
        # Missing the required 'r3' level.
        {k: v for k, v in _conforming_sr().items() if k != "r3"},
    ),
    (
        "get_news_context",
        _conforming_news(),
        # No 'sentiment_summary' and no honest 'error'/unavailable marker.
        {"symbol": "RELIANCE", "headlines": ["Co beats estimates"]},
    ),
    (
        "get_prediction",
        _conforming_prediction(),
        # 'projected_direction' outside the {Up, Down, Flat} enum.
        {**_conforming_prediction(), "projected_direction": "Sideways"},
    ),
    (
        "get_multi_tf_trend",
        _conforming_multi_tf(),
        # Missing the '4h' horizon bias.
        {k: v for k, v in _conforming_multi_tf().items() if k != "trend_4h"},
    ),
    (
        "get_chart_patterns",
        _conforming_patterns(),
        # Pattern confidence outside the [0.0, 1.0] contract range.
        {"symbol": "RELIANCE", "timeframe": "1d", "patterns": [
            {"pattern_type": "Double Top", "sentiment": "Bearish",
             "confidence": 1.8, "description": "Out-of-range confidence"},
        ]},
    ),
]

CONTRACT_TOOL_NAMES = [case[0] for case in CONTRACT_CASES]


# ── (a) Malformed payload → structured error, never an exception ─────────────
def test_each_tool_malformed_payload_yields_structured_error():
    """Validates: Requirements 4.1

    For every contract tool, a malformed payload returns a structured error
    dict carrying both 'error' and 'contract_violation' keys, and does NOT
    raise.
    """
    for tool_name, _conforming, malformed in CONTRACT_CASES:
        # Must not raise — contract failures are data, not exceptions.
        try:
            result = validate_contract(tool_name, malformed)
        except Exception as exc:  # pragma: no cover - failure path
            raise AssertionError(
                f"validate_contract({tool_name!r}) raised {exc!r} instead of "
                f"returning a structured error"
            )

        assert isinstance(result, dict), (
            f"{tool_name}: expected a dict result, got {type(result).__name__}"
        )
        assert "error" in result, f"{tool_name}: missing 'error' key"
        assert "contract_violation" in result, (
            f"{tool_name}: missing 'contract_violation' key"
        )
        # The malformed payload itself must NOT be returned to the model.
        assert result != malformed, (
            f"{tool_name}: malformed payload passed through unchanged"
        )


# ── (b) Conforming payload → returned unchanged ──────────────────────────────
def test_each_tool_conforming_payload_passes_through_unchanged():
    """Validates: Requirements 4.1

    A conforming payload is returned unchanged (identity), not flagged as a
    violation.
    """
    for tool_name, conforming, _malformed in CONTRACT_CASES:
        result = validate_contract(tool_name, conforming)
        assert result is conforming, (
            f"{tool_name}: conforming payload was not returned unchanged"
        )
        # A conforming result is not a contract violation.
        assert not (isinstance(result, dict) and "contract_violation" in result), (
            f"{tool_name}: conforming payload was incorrectly flagged"
        )


# ── (c) Honest graceful-degradation markers pass through unchanged ───────────
def test_honest_markers_pass_through_unchanged():
    """Validates: Requirements 4.1

    Honest, non-fatal markers (an 'error' key, ``{"unavailable": true}``, an
    ``Unavailable`` sentiment, or a ``watch_registration_failed`` status) are
    legitimate graceful-degradation results — NOT contract violations — so they
    pass through unchanged.
    """
    honest_markers = [
        {"error": "upstream timeout"},
        {"unavailable": True, "reason": "insufficient candles"},
        {"sentiment_summary": "Unavailable"},
        {"status": "watch_registration_failed", "action": "HOLD", "trade": None},
    ]

    # The marker must not be reclassified as a violation regardless of which
    # contract tool produced it.
    for marker in honest_markers:
        for tool_name in CONTRACT_TOOL_NAMES:
            result = validate_contract(tool_name, marker)
            assert result is marker, (
                f"{tool_name}: honest marker {marker} was not passed through "
                f"unchanged"
            )
            assert "contract_violation" not in result, (
                f"{tool_name}: honest marker {marker} was wrongly flagged as a "
                f"contract violation"
            )


# ── (d) Violations are recognized as non-fatal errors by the loop ────────────
def test_violation_is_recognized_as_non_fatal_error_by_loop():
    """Validates: Requirements 4.1

    A contract violation serializes to JSON that ``graph._tool_result_is_error``
    recognizes as a tool error, so the ReAct loop treats it as a non-fatal error
    that never reaches the model as valid market data.
    """
    for tool_name, _conforming, malformed in CONTRACT_CASES:
        violation = validate_contract(tool_name, malformed)
        serialized = json.dumps(violation)
        assert graph._tool_result_is_error(serialized) is True, (
            f"{tool_name}: contract violation not recognized as a tool error"
        )

    # A conforming consensus report (real market data) is NOT an error, so this
    # heuristic does not over-trigger on valid results.
    ok = validate_contract("get_consensus_report", _conforming_consensus())
    assert graph._tool_result_is_error(json.dumps(ok)) is False


# ── unknown / non-contract tools pass through untouched ──────────────────────
def test_unknown_tool_passes_through_unchanged():
    """A tool with no contract (e.g. declare_trade) is returned unchanged."""
    payload = {"any": "shape", "is": ["fine"]}
    assert validate_contract("declare_trade", payload) is payload
