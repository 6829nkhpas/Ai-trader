"""Integration test for the non-blocking forecast data gate (graph.py, task 16.1).

Feature: volatility-aware-forecaster

A single ``get_forecast`` result carrying an Unavailable_Marker
(``{"unavailable": true, "reason": ...}``) is, by itself, an *honest missing
optional input* — never usable directional data and never a forcing/blocking
signal. This integration-style test pins down that contract across the gate
helpers in ``graph.py``:

  * ``_market_data_seen([unavailable_forecast])`` is **False** — an unavailable
    forecast result does NOT satisfy the first-turn market-data acquisition gate,
    so it can never, on its own, make the loop believe usable data has arrived
    (R6.4). This is true even though ``get_forecast`` is a member of
    ``MARKET_DATA_TOOL_NAMES``.
  * ``_market_data_attempted([unavailable_forecast])`` is **True** — a
    market-data Analysis_Tool WAS called, so the forecast result is treated as a
    *sought-but-unavailable* optional input rather than a never-attempted one.
    This is exactly the non-blocking semantics of R6.4: the agent proceeds with
    the remaining analysis and does not abort/fail solely because the forecast is
    unavailable.

Contrast cases prove the gate is not trivially always-False:

  * A usable forecast *label* (a real projected_direction / up_probability /
    forecast_alignment, no unavailable marker) WOULD satisfy
    ``_market_data_seen``.
  * A usable *non-forecast* market-data result (e.g. ``get_candles``) WOULD
    satisfy ``_market_data_seen`` as well — confirming that when usable data
    accompanies an unavailable forecast, the gate is satisfied by the usable
    data, not the unavailable forecast marker.

Together these confirm that an unavailable forecast result alone neither
satisfies the data gate nor forces a committed decision (R6.4), while real
market data still does — the unavailable forecast is simply a non-blocking
missing input.

Validates: Requirements 6.4

The sys.path / import pattern mirrors ``tests/test_rs_nonblocking_gate.py``: the
service directory (one level up) is prepended to ``sys.path`` so ``graph`` is
importable when pytest is run from anywhere. The real LLM / Rust server is never
invoked — a lightweight stub ToolMessage (``type == "tool"`` with ``.name`` and
``.content``) stands in for the LangChain ``ToolMessage``, exactly the shape the
gate code reads.
"""

import json
import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    MARKET_DATA_TOOL_NAMES,
    _market_data_attempted,
    _market_data_seen,
)

FORECAST_TOOL = "get_forecast"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _unavailable_forecast_message(
    reason="insufficient data: 12 valid candles available, 31 required",
):
    """A single get_forecast ToolMessage carrying an Unavailable_Marker.

    Per R6.3 the marker omits projected_direction / up_probability /
    expected_move_atr / forecast_confidence / forecast_alignment rather than
    fabricating them.
    """
    payload = {
        "unavailable": True,
        "reason": reason,
        "symbol": "RELIANCE",
        "timeframe": "5m",
    }
    return StubToolMessage(content=json.dumps(payload), name=FORECAST_TOOL)


def _usable_forecast_message():
    """A single get_forecast ToolMessage carrying a usable Forecast_Label."""
    payload = {
        "projected_direction": "up",
        "up_probability": 0.63,
        "expected_move_atr": 0.41,
        "forecast_confidence": 0.26,
        "forecast_alignment": "aligned",
        "measures": {
            "drift": 0.0021,
            "volatility": 0.0134,
            "standardized_drift": 0.157,
            "atr": 1.82,
        },
        "regime_trend_state": "trending",
        "symbol": "RELIANCE",
        "timeframe": "15m",
    }
    return StubToolMessage(content=json.dumps(payload), name=FORECAST_TOOL)


def _usable_non_forecast_message():
    """A usable non-forecast market-data result (get_candles) for the contrast case."""
    payload = {"symbol": "RELIANCE", "timeframe": "15m", "candles": [{"close": 100.0}]}
    return StubToolMessage(content=json.dumps(payload), name="get_candles")


# ─────────────────────────────────────────────────────────────────────────────
# R6.4: an unavailable forecast result is a non-blocking missing input.
# ─────────────────────────────────────────────────────────────────────────────


def test_unavailable_forecast_does_not_satisfy_market_data_gate():
    """Validates: Requirements 6.4

    A lone unavailable forecast result does NOT set ``market_data_seen`` — it is
    an honest missing input, not usable directional data — even though
    ``get_forecast`` participates in the market-data gate.
    """
    # Precondition: the forecast tool participates in the market-data gate at all.
    assert FORECAST_TOOL in MARKET_DATA_TOOL_NAMES

    msg = _unavailable_forecast_message()

    assert _market_data_seen([msg]) is False


def test_unavailable_forecast_counts_as_a_market_data_attempt():
    """Validates: Requirements 6.4

    A lone unavailable forecast result IS a market-data *attempt*: the tool was
    called but yielded no usable data, so it is treated as a sought-but-
    unavailable optional input (non-blocking), distinct from "never attempted".
    """
    msg = _unavailable_forecast_message()

    assert _market_data_attempted([msg]) is True


def test_unavailable_forecast_alone_does_not_force_a_committed_decision():
    """Validates: Requirements 6.4

    Combined gate read: a single unavailable forecast ToolMessage neither
    satisfies the data gate (seen=False) nor, on its own, forces a decision — it
    is simply a missing optional input that was attempted (attempted=True). The
    gating logic never fabricates "usable data has arrived" from an unavailable
    forecast result, so the agent proceeds with the remaining analysis.
    """
    msg = _unavailable_forecast_message()

    seen = _market_data_seen([msg])
    attempted = _market_data_attempted([msg])

    # Sought, but unavailable: attempted yet not usable.
    assert (seen, attempted) == (False, True)


# ─────────────────────────────────────────────────────────────────────────────
# Contrast cases: usable data DOES satisfy the gate (the gate is not trivial).
# ─────────────────────────────────────────────────────────────────────────────


def test_usable_forecast_label_satisfies_market_data_gate():
    """Validates: Requirements 6.4

    A usable forecast label (a real projected_direction / alignment, no
    unavailable marker) WOULD satisfy the data gate — proving the gate
    distinguishes a usable forecast result from an unavailable one rather than
    rejecting forecast results wholesale. This confirms the unavailable forecast
    was simply a non-blocking missing input, not a forced/blocked gate.
    """
    msg = _usable_forecast_message()

    assert _market_data_seen([msg]) is True


def test_usable_market_data_satisfies_gate_alongside_unavailable_forecast():
    """Validates: Requirements 6.4

    A usable market-data result (get_candles) satisfies the gate. When it
    accompanies an unavailable forecast result, the gate is satisfied by the
    usable data, never by the unavailable forecast marker — the forecast marker
    remains non-blocking and simply contributes nothing on its own, so the agent
    proceeds with the remaining analysis.
    """
    candles = _usable_non_forecast_message()
    unavailable_forecast = _unavailable_forecast_message()

    # The usable candles satisfy the gate on their own.
    assert _market_data_seen([candles]) is True
    # Together, the gate is satisfied (by the candles, not the forecast marker).
    assert _market_data_seen([unavailable_forecast, candles]) is True
    # And adding a usable forecast label likewise satisfies the gate, confirming
    # the unavailable forecast was a non-blocking missing input all along.
    usable_forecast = _usable_forecast_message()
    assert _market_data_seen([unavailable_forecast, usable_forecast]) is True
