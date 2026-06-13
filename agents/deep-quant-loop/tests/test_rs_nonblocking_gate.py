"""Integration test for the non-blocking relative-strength data gate (graph.py, task 14.1).

Feature: relative-strength-context

A single ``get_relative_strength`` result carrying an Unavailable_Marker
(``{"unavailable": true, "reason": ...}``) is, by itself, an *honest missing
optional input* — never usable directional data and never a forcing/blocking
signal. This integration-style test pins down that contract across the gate
helpers in ``graph.py``:

  * ``_market_data_seen([unavailable_rs])`` is **False** — an unavailable
    relative-strength result does NOT satisfy the first-turn market-data
    acquisition gate, so it can never, on its own, make the loop believe usable
    data has arrived (R5.4). This is true even though ``get_relative_strength``
    is a member of ``MARKET_DATA_TOOL_NAMES``.
  * ``_market_data_attempted([unavailable_rs])`` is **True** — a market-data
    Analysis_Tool WAS called, so the relative-strength result is treated as a
    *sought-but-unavailable* optional input rather than a never-attempted one.
    This is exactly the non-blocking semantics of R5.4: the agent proceeds with
    the remaining analysis and does not abort/fail solely because relative
    strength is unavailable.

Contrast cases prove the gate is not trivially always-False:

  * A usable RS *label* (a real index_direction / relative_strength_state /
    alignment, no unavailable marker) WOULD satisfy ``_market_data_seen``.
  * A usable *non-RS* market-data result (e.g. ``get_candles``) WOULD satisfy
    ``_market_data_seen`` as well.

Together these confirm that an unavailable relative-strength result alone
neither satisfies the data gate nor forces a committed decision (R5.4), while
real market data still does.

Validates: Requirements 5.4

The sys.path / import pattern mirrors
``tests/test_regime_nonblocking_data_gate_integration.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable
when pytest is run from anywhere. The real LLM / Rust server is never invoked —
a lightweight stub ToolMessage (``type == "tool"`` with ``.name`` and
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

RS_TOOL = "get_relative_strength"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _unavailable_rs_message(
    reason="insufficient aligned data: 12 aligned candles available, 31 required",
):
    """A single get_relative_strength ToolMessage carrying an Unavailable_Marker.

    Per R5.3 the marker omits index_direction / relative_strength_state /
    alignment rather than fabricating them.
    """
    payload = {
        "unavailable": True,
        "reason": reason,
        "symbol": "RELIANCE",
        "timeframe": "5m",
        "benchmark": "NIFTY 50",
    }
    return StubToolMessage(content=json.dumps(payload), name=RS_TOOL)


def _usable_rs_message():
    """A single get_relative_strength ToolMessage carrying a usable label."""
    payload = {
        "index_direction": "up",
        "relative_strength_state": "leader",
        "alignment": "aligned",
        "measures": {
            "rs_ratio": 0.0142,
            "rs_ratio_slope": 0.00031,
            "relative_return": 0.035,
            "correlation": 0.72,
            "beta": 1.18,
        },
        "benchmark": "NIFTY 50",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "aligned_candles": 64,
    }
    return StubToolMessage(content=json.dumps(payload), name=RS_TOOL)


def _usable_non_rs_message():
    """A usable non-RS market-data result (get_candles) for the contrast case."""
    payload = {"symbol": "RELIANCE", "timeframe": "15m", "candles": [{"close": 100.0}]}
    return StubToolMessage(content=json.dumps(payload), name="get_candles")


# ─────────────────────────────────────────────────────────────────────────────
# R5.4: an unavailable relative-strength result is a non-blocking missing input.
# ─────────────────────────────────────────────────────────────────────────────


def test_unavailable_rs_does_not_satisfy_market_data_gate():
    """Validates: Requirements 5.4

    A lone unavailable relative-strength result does NOT set ``market_data_seen``
    — it is an honest missing input, not usable directional data — even though
    ``get_relative_strength`` participates in the market-data gate.
    """
    # Precondition: the RS tool participates in the market-data gate at all.
    assert RS_TOOL in MARKET_DATA_TOOL_NAMES

    msg = _unavailable_rs_message()

    assert _market_data_seen([msg]) is False


def test_unavailable_rs_counts_as_a_market_data_attempt():
    """Validates: Requirements 5.4

    A lone unavailable relative-strength result IS a market-data *attempt*: the
    tool was called but yielded no usable data, so it is treated as a sought-but-
    unavailable optional input (non-blocking), distinct from "never attempted".
    """
    msg = _unavailable_rs_message()

    assert _market_data_attempted([msg]) is True


def test_unavailable_rs_alone_does_not_force_a_committed_decision():
    """Validates: Requirements 5.4

    Combined gate read: a single unavailable relative-strength ToolMessage
    neither satisfies the data gate (seen=False) nor, on its own, forces a
    decision — it is simply a missing optional input that was attempted
    (attempted=True). The gating logic never fabricates "usable data has arrived"
    from an unavailable relative-strength result.
    """
    msg = _unavailable_rs_message()

    seen = _market_data_seen([msg])
    attempted = _market_data_attempted([msg])

    # Sought, but unavailable: attempted yet not usable.
    assert (seen, attempted) == (False, True)


# ─────────────────────────────────────────────────────────────────────────────
# Contrast cases: usable data DOES satisfy the gate (the gate is not trivial).
# ─────────────────────────────────────────────────────────────────────────────


def test_usable_rs_label_satisfies_market_data_gate():
    """Validates: Requirements 5.4

    A usable relative-strength label (a real alignment, no unavailable marker)
    WOULD satisfy the data gate — proving the gate distinguishes a usable RS
    result from an unavailable one rather than rejecting RS results wholesale.
    """
    msg = _usable_rs_message()

    assert _market_data_seen([msg]) is True


def test_usable_non_rs_market_data_satisfies_gate_alongside_unavailable_rs():
    """Validates: Requirements 5.4

    A usable non-RS market-data result (get_candles) satisfies the gate. When it
    accompanies an unavailable RS result, the gate is satisfied by the usable
    data, never by the unavailable RS marker — the RS marker remains non-blocking
    and simply contributes nothing on its own.
    """
    candles = _usable_non_rs_message()
    unavailable_rs = _unavailable_rs_message()

    # The usable candles satisfy the gate on their own.
    assert _market_data_seen([candles]) is True
    # Together, the gate is satisfied (by the candles, not the RS marker).
    assert _market_data_seen([unavailable_rs, candles]) is True
