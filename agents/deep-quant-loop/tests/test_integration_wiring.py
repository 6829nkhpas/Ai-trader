"""Integration tests for external-service wiring (task 18.2).

Feature: deep-quant-analysis-hardening

These are integration-style tests that exercise the Python tool clients in
``tools.py`` end-to-end against MOCKED HTTP responses (``httpx.post`` is
patched). They require NO live Rust Tool_Server, QuestDB, or Sentiment_Service.

Covered wiring:

  1. R10.1 — ``get_news_context`` surfaces a mocked Sentiment_Service
     classification, and returns an honest ``Unavailable`` marker (no fabricated
     classification) when the service errors.

  2. R12.1 — ``get_prediction`` fetches/returns a mocked predictive projection
     during directional analysis, and returns an honest ``unavailable`` marker
     when the predictive engine errors.

  3. R4.1 — the tool clients run every result through ``validate_contract``: a
     seeded / well-formed endpoint payload (as would come from a seeded QuestDB)
     passes the contract and reaches the model unchanged, while a malformed
     payload yields a structured contract violation that never reaches the model
     as valid market data.

  4. R14.2 — watcher lifecycle (register → triggering candle → remove-on-fire).
     CHOICE: the register→trigger→remove-once registry transition is pure
     Rust-side state, so the authoritative lifecycle assertion lives in a Rust
     integration test (``tool_server.rs`` → ``watcher_registry_proptests::
     watcher_lifecycle_fires_once_and_is_removed``). Here we cover the Python
     half of the wiring: a successful registration POSTs to
     ``/tools/watch_condition`` and then suspends the run via ``interrupt`` with
     the watched parameters (the resumable-suspend handoff that ``/resume``
     later continues).

All tests call the underlying tool functions past the LangChain ``@tool``
wrapper via ``.func`` (matching the convention in test_watch_registration_failure).
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / graph.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
import graph  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _mock_response(json_data, status_code=200):
    """Build a stand-in for an httpx.Response carrying ``json_data``."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.text = json.dumps(json_data)
    resp.json = mock.Mock(return_value=json_data)
    resp.raise_for_status = mock.Mock(return_value=None)
    return resp


def _is_violation(result):
    return isinstance(result, dict) and "contract_violation" in result


# ── 1. Sentiment_Service classification via get_news_context (R10.1) ─────────
def test_get_news_context_surfaces_mocked_sentiment_classification():
    """Validates: Requirements 10.1

    A mocked Sentiment_Service classification (proxied through the Rust
    ``/tools/get_news_context`` endpoint) is surfaced by ``get_news_context``
    with its recent headlines and a directional sentiment label.
    """
    service_payload = {
        "symbol": "RELIANCE",
        "headlines": [
            "Reliance Q3 profit beats street estimates",
            "Brokerages raise target price after results",
        ],
        "sentiment_summary": "Bullish",
    }

    with mock.patch.object(
        tools.httpx, "post", return_value=_mock_response(service_payload)
    ) as mock_post:
        result = _raw(tools.get_news_context)(symbol="RELIANCE")

    # The client hit the sentiment proxy endpoint for the requested symbol.
    assert mock_post.call_count == 1
    url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
    assert url.endswith("/tools/get_news_context")
    assert mock_post.call_args.kwargs["json"] == {"symbol": "RELIANCE"}

    # The classification + headlines are surfaced unchanged (contract passes).
    assert result["sentiment_summary"] == "Bullish"
    assert result["headlines"] == service_payload["headlines"]
    assert not _is_violation(result)


def test_get_news_context_returns_unavailable_marker_on_service_failure():
    """Validates: Requirements 10.1, 10.3

    When the Sentiment_Service is unreachable, ``get_news_context`` returns an
    explicit ``Unavailable`` marker and never fabricates a classification.
    """
    with mock.patch.object(
        tools.httpx, "post", side_effect=ConnectionError("sentiment service down")
    ):
        result = _raw(tools.get_news_context)(symbol="RELIANCE")

    assert result["sentiment_summary"] == "Unavailable"
    assert "error" in result
    assert result["headlines"] == []
    # graph treats this honest marker as a non-blocking missing input (R10.4).
    assert graph._tool_result_is_error(json.dumps(result)) is True


# ── 2. Predictive projection via get_prediction (R12.1) ──────────────────────
def test_get_prediction_fetches_mocked_projection():
    """Validates: Requirements 12.1, 12.2

    During directional analysis ``get_prediction`` fetches a forward projection
    from the Predictive_Engine and returns its direction + value + confidence.
    """
    projection = {
        "symbol": "RELIANCE",
        "timeframe": "1d",
        "projected_direction": "Up",
        "projected_value": 2512.5,
        "confidence": 0.68,
    }

    with mock.patch.object(
        tools.httpx, "post", return_value=_mock_response(projection)
    ) as mock_post:
        result = _raw(tools.get_prediction)(symbol="RELIANCE", timeframe="1d")

    assert mock_post.call_count == 1
    url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
    assert url.endswith("/tools/get_prediction")
    assert mock_post.call_args.kwargs["json"] == {"symbol": "RELIANCE", "timeframe": "1d"}

    assert result["projected_direction"] == "Up"
    assert result["projected_value"] == 2512.5
    assert result["confidence"] == 0.68
    assert not _is_violation(result)


def test_get_prediction_returns_unavailable_marker_on_engine_failure():
    """Validates: Requirements 12.1, 12.4

    When the Predictive_Engine cannot be reached, ``get_prediction`` returns an
    explicit ``unavailable`` marker rather than a fabricated forecast.
    """
    with mock.patch.object(
        tools.httpx, "post", side_effect=ConnectionError("predictive engine down")
    ):
        result = _raw(tools.get_prediction)(symbol="RELIANCE", timeframe="1d")

    assert result["unavailable"] is True
    assert "reason" in result
    # No fabricated forecast is produced — the honest marker carries neither a
    # projected direction nor a projected value.
    assert "projected_direction" not in result
    assert "projected_value" not in result
    # The contract revalidator passes the honest marker through unchanged
    # (it is data, not a violation).
    assert tools.validate_contract("get_prediction", result) is result


# ── 3. Seeded-endpoint contract checks through validate_contract (R4.1) ──────
# Each entry: (tool_callable, kwargs, seeded_well_formed_payload, malformed_payload)
def _seeded_candles():
    return [
        {"timestamp_ms": 1_000, "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.5, "volume": 12000.0},
        {"timestamp_ms": 2_000, "open": 100.5, "high": 102.0, "low": 100.0,
         "close": 101.5, "volume": 15000.0},
    ]


def _seeded_consensus():
    return {field: 1.0 for field in tools._CONSENSUS_REQUIRED_FIELDS}


def _seeded_sr():
    return {"pivot": 100.0, "s1": 95.0, "s2": 90.0, "s3": 85.0,
            "r1": 105.0, "r2": 110.0, "r3": 115.0,
            "recent_high": 116.0, "recent_low": 84.0}


def test_seeded_endpoint_payloads_pass_contract():
    """Validates: Requirements 4.1

    A seeded / well-formed payload from each QuestDB-backed endpoint passes the
    consumer-side contract and is surfaced unchanged.
    """
    cases = [
        (tools.get_candles, {"symbol": "RELIANCE", "timeframe": "1d", "limit": 2},
         _seeded_candles()),
        (tools.get_consensus_report, {"symbol": "RELIANCE", "timeframe": "1d"},
         _seeded_consensus()),
        (tools.get_support_resistance, {"symbol": "RELIANCE", "timeframe": "1d"},
         _seeded_sr()),
    ]
    for tool_obj, kwargs, seeded in cases:
        with mock.patch.object(
            tools.httpx, "post", return_value=_mock_response(seeded)
        ):
            result = _raw(tool_obj)(**kwargs)
        assert not _is_violation(result), (
            f"{tool_obj.name}: seeded payload was wrongly flagged as a violation"
        )
        assert result == seeded, f"{tool_obj.name}: seeded payload mutated"


def test_malformed_endpoint_payloads_yield_contract_violation():
    """Validates: Requirements 4.1

    A malformed payload from each QuestDB-backed endpoint yields a structured
    contract violation (recognized by the loop as a non-fatal tool error) and
    never reaches the model as valid market data.
    """
    malformed_cases = [
        # Candle missing the required 'volume' field.
        (tools.get_candles, {"symbol": "RELIANCE", "timeframe": "1d", "limit": 2},
         [{"timestamp_ms": 1_000, "open": 100.0, "high": 101.0, "low": 99.0,
           "close": 100.5}]),
        # Consensus 'rsi_14' is a non-numeric string.
        (tools.get_consensus_report, {"symbol": "RELIANCE", "timeframe": "1d"},
         {**_seeded_consensus(), "rsi_14": "oversold"}),
        # Support/resistance missing the 'r3' level.
        (tools.get_support_resistance, {"symbol": "RELIANCE", "timeframe": "1d"},
         {k: v for k, v in _seeded_sr().items() if k != "r3"}),
    ]
    for tool_obj, kwargs, malformed in malformed_cases:
        with mock.patch.object(
            tools.httpx, "post", return_value=_mock_response(malformed)
        ):
            result = _raw(tool_obj)(**kwargs)
        assert _is_violation(result), (
            f"{tool_obj.name}: malformed payload was not flagged as a violation"
        )
        # The malformed payload itself must not be surfaced to the model.
        assert result != malformed
        assert graph._tool_result_is_error(json.dumps(result)) is True


# ── 4. Watcher wiring: register POST + resumable suspend (R14.2) ─────────────
def test_watch_price_condition_registers_then_suspends_with_params():
    """Validates: Requirements 14.2 (Python half of the register→resume handoff)

    A successful registration POSTs the watch parameters to
    ``/tools/watch_condition`` and then suspends the run via ``interrupt`` with
    those parameters (the resumable-suspend handoff that a later triggering
    candle's ``/resume`` continues). The authoritative
    register→trigger→remove-once registry transition is asserted in the Rust
    integration test (see module docstring).
    """
    config = {"configurable": {"thread_id": "t-int"}}
    captured = {}

    def _fake_interrupt(payload):
        captured["interrupt_payload"] = payload
        # Simulate /resume delivering the triggering candle back to the run.
        return {"close": 2451.0, "volume": 250000}

    with mock.patch.object(
        tools.httpx, "post", return_value=_mock_response({"status": "watching_registered"})
    ) as mock_post, mock.patch.object(tools, "interrupt", side_effect=_fake_interrupt):
        result = _raw(tools.watch_price_condition)(
            symbol="RELIANCE",
            timeframe="15m",
            price_level=2450.0,
            direction="above",
            volume_multiplier=1.5,
            config=config,
        )

    # Registered exactly once against the watch_condition endpoint.
    assert mock_post.call_count == 1
    url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
    assert url.endswith("/tools/watch_condition")
    sent = mock_post.call_args.kwargs["json"]
    assert sent["thread_id"] == "t-int"
    assert sent["symbol"] == "RELIANCE"
    assert sent["price_level"] == 2450.0
    assert sent["direction"] == "above"
    assert sent["volume_multiplier"] == 1.5

    # The run suspended resumably with the watched parameters before resuming.
    suspend = captured["interrupt_payload"]
    assert suspend["status"] == "watching_registered"
    assert suspend["symbol"] == "RELIANCE"
    assert suspend["price_level"] == 2450.0

    # After /resume delivers the triggering candle, the tool reports the trigger.
    assert "Condition met" in result
