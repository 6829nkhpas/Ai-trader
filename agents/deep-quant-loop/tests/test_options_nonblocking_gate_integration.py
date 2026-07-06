"""Integration test for the non-blocking options data gate (graph.py, task 12.1).

Feature: options-agent-integration

A single ``get_options_analytics`` result carrying an Unavailable_Marker
(``{"unavailable": true, "reason": ...}``) is, by itself, an *honest missing
optional input* — never usable directional data and never a forcing/blocking
signal. This integration-style test pins down that contract across the
market-data gate helpers in ``graph.py``:

  * ``_market_data_seen([unavailable_options])`` is **False** — an unavailable
    options result does NOT satisfy the first-turn market-data acquisition gate,
    so it can never, on its own, make the loop believe usable data has arrived
    (R3.3). This is true even though ``get_options_analytics`` is a member of
    ``MARKET_DATA_TOOL_NAMES``.
  * ``_market_data_attempted([unavailable_options])`` is **True** — a market-data
    Analysis_Tool WAS called, so the options result is treated as a
    *sought-but-unavailable* optional input rather than a never-attempted one.
    This is exactly the non-blocking semantics of R3.3: the agent proceeds with
    the remaining analysis and does not abort/block solely because options
    context is unavailable.

Contrast case proves the gate is not trivially always-False:

  * A usable options *label* (a real options_bias_state / alignment /
    chain_context, no unavailable marker) WOULD satisfy ``_market_data_seen``.

Together these confirm that an unavailable options result alone neither
satisfies the data gate nor forces a committed decision (R3.3), while a usable
options result still does.

Validates: Requirements 3.3

The sys.path / import pattern mirrors
``tests/test_rs_nonblocking_gate.py``: the service directory (one level up) is
prepended to ``sys.path`` so ``graph`` is importable when pytest is run from
anywhere. The real LLM / Rust server is never invoked — a real
``langchain_core.messages.ToolMessage`` (``type == "tool"`` with ``.name`` and
``.content``) stands in for the live tool result, exactly the shape the gate
code reads.
"""

import json
import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from langchain_core.messages import ToolMessage  # noqa: E402

import graph  # noqa: E402
from graph import (  # noqa: E402
    MARKET_DATA_TOOL_NAMES,
    _market_data_attempted,
    _market_data_seen,
    _tool_result_is_unavailable,
)

OPTIONS_TOOL = "get_options_analytics"


def _unavailable_options_message(
    reason="no option-chain snapshot available for BANKNIFTY (outside market hours)",
):
    """A single get_options_analytics ToolMessage carrying an Unavailable_Marker.

    Per R3.2 the marker omits options_bias_state / alignment rather than
    fabricating them.
    """
    payload = {
        "unavailable": True,
        "reason": reason,
        "symbol": "RELIANCE",
        "underlying": "NIFTY 50",
        "chain_context": "broad-market",
    }
    return ToolMessage(
        content=json.dumps(payload),
        name=OPTIONS_TOOL,
        tool_call_id="opt-unavailable-1",
    )


def _usable_options_message():
    """A single get_options_analytics ToolMessage carrying a usable label."""
    payload = {
        "options_bias_state": "bullish",
        "alignment": "aligned",
        "chain_context": "broad-market",
        "underlying": "NIFTY 50",
        "symbol": "RELIANCE",
        "expiry": "2024-01-25",
        "spot": 21420.0,
        "pcr_oi": 1.42,
        "pcr_volume": 1.18,
        "max_pain": 21500.0,
        "oi_buildup": {"call": "short_buildup", "put": "long_buildup"},
        "oi_walls": {"support": 21400.0, "resistance": 21800.0},
        "iv_skew": {"put_minus_call": -0.012},
        "futures_basis": 18.5,
        "signals": {
            "pcr_oi": 1.42,
            "oi_buildup": {"call": "short_buildup", "put": "long_buildup"},
            "max_pain": 21500.0,
            "spot": 21420.0,
            "max_pain_vs_spot": "above",
            "oi_walls": {"support": 21400.0, "resistance": 21800.0},
            "iv_skew_put_minus_call": -0.012,
            "futures_basis": 18.5,
        },
    }
    return ToolMessage(
        content=json.dumps(payload),
        name=OPTIONS_TOOL,
        tool_call_id="opt-usable-1",
    )


# ─────────────────────────────────────────────────────────────────────────────
# R3.3: an unavailable options result is a non-blocking missing input.
# ─────────────────────────────────────────────────────────────────────────────


def test_unavailable_options_does_not_satisfy_market_data_gate():
    """Validates: Requirements 3.3

    A lone unavailable options result does NOT set ``market_data_seen`` — it is
    an honest missing input, not usable directional data — even though
    ``get_options_analytics`` participates in the market-data gate.
    """
    # Precondition: the options tool participates in the market-data gate at all.
    assert OPTIONS_TOOL in MARKET_DATA_TOOL_NAMES

    msg = _unavailable_options_message()

    # The classifying predicate confirms this is an explicit unavailable marker.
    assert _tool_result_is_unavailable(msg.content) is True
    assert _market_data_seen([msg]) is False


def test_unavailable_options_counts_as_a_market_data_attempt():
    """Validates: Requirements 3.3

    A lone unavailable options result IS a market-data *attempt*: the tool was
    called but yielded no usable data, so it is treated as a sought-but-
    unavailable optional input (non-blocking), distinct from "never attempted".
    """
    msg = _unavailable_options_message()

    assert _market_data_attempted([msg]) is True


def test_unavailable_options_alone_does_not_force_a_committed_decision():
    """Validates: Requirements 3.3

    Combined gate read: a single unavailable options ToolMessage neither
    satisfies the data gate (seen=False) nor, on its own, forces a decision — it
    is simply a missing optional input that was attempted (attempted=True). The
    gating logic never fabricates "usable data has arrived" from an unavailable
    options result, and the agent proceeds with the remaining analysis.
    """
    msg = _unavailable_options_message()

    seen = _market_data_seen([msg])
    attempted = _market_data_attempted([msg])

    # Sought, but unavailable: attempted yet not usable.
    assert (seen, attempted) == (False, True)


# ─────────────────────────────────────────────────────────────────────────────
# Contrast case: a usable options result DOES satisfy the gate (not trivial).
# ─────────────────────────────────────────────────────────────────────────────


def test_usable_options_label_satisfies_market_data_gate():
    """Validates: Requirements 3.3

    A usable options label (a real options_bias_state / alignment, no
    unavailable marker) WOULD satisfy the data gate — proving the gate
    distinguishes a usable options result from an unavailable one rather than
    rejecting options results wholesale.
    """
    msg = _usable_options_message()

    # It is neither an error nor an explicit unavailable marker.
    assert _tool_result_is_unavailable(msg.content) is False
    assert _market_data_seen([msg]) is True
