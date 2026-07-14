"""Unit test for the terminal read carried by a ``force_hold`` decision (graph.py, task 4.3).

Feature: agent-loop-responsiveness

Design Property 3 (facet: ``force_hold`` path) / Requirements 3.1, 3.2:

    When the loop commits a terminal HOLD via reasoning-budget exhaustion
    (``force_hold``), the injected decision SHALL carry the interim
    Best_Current_Read (bias + key levels + why standing aside) and the
    Opportunity_Tier ``stand_aside`` via ``_finalize_decision`` — identically to
    the bounded-hunt ``force_terminal`` path — and SHALL NOT fabricate directional
    entry/stop/target levels for the stand-aside.

    Validates: Requirements 3.1, 3.2.

``force_hold(state)`` takes an ``AgentState`` (a dict) and returns
``{"decision": ..., "messages": [...]}``. ``_finalize_decision`` also journals the
decision (``journal.record_decision``); that write is best-effort/try-wrapped, but
this test monkeypatches it to a no-op so the unit test touches no journal DB.

The sys.path / import bootstrap mirrors the sibling ``tests/test_loop_routing.py``
module (which exercises ``force_hold`` from the same service dir).
"""

import math
import os
import sys

from langchain_core.messages import HumanMessage

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` / ``import journal`` resolve exactly as every sibling test
# module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import journal  # noqa: E402
from graph import force_hold  # noqa: E402

# The directional execution levels a committed BUY/SELL carries; a stand-aside
# HOLD must never surface a finite value for any of these.
_DIRECTIONAL_LEVEL_KEYS = ("entry", "stop_loss", "take_profit")


def _is_finite_number(x) -> bool:
    """A real, finite, non-bool number (mirrors ``graph._is_finite_num``)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _minimal_hold_state():
    """A minimal AgentState for a reasoning-exhaustion HOLD.

    ``force_hold`` -> ``_finalize_decision`` -> ``_stamp_opportunity_tier`` reads
    ``messages`` (for the evidence/defensibility scan), ``mode``, ``symbol``,
    ``timeframe``, and ``manual_trade``. No tool results are present (the agent
    ran out of reasoning turns without gathering a validated setup), so the
    evidence is empty and no directional levels can be surfaced.
    """
    return {
        "messages": [HumanMessage(content="Find a trade on NIFTY.")],
        "mode": "FIND",
        "symbol": "NIFTY",
        "timeframe": "INTRADAY",
        "manual_trade": None,
        "reasoning_turns": graph.MAX_REASONING_TURNS,
        "decision": None,
    }


def test_force_hold_carries_stand_aside_tier_and_best_current_read(monkeypatch):
    """A ``force_hold`` decision carries ``opportunity_tier == "stand_aside"`` and a
    non-None Best_Current_Read, and fabricates no directional entry/stop/target
    levels.

    Validates: Requirements 3.1, 3.2
    """
    # Isolate the best-effort journal write so the unit test never touches a DB.
    recorded = {}

    def _fake_record_decision(decision, **kwargs):
        recorded["called"] = True
        recorded["decision"] = decision

    monkeypatch.setattr(journal, "record_decision", _fake_record_decision)

    state = _minimal_hold_state()
    update = force_hold(state)

    decision = update["decision"]

    # Sanity: this is the reasoning-exhaustion HOLD.
    assert decision["action"] == "HOLD"
    assert decision["reason"] == "no-decision-reached"

    # R3.2: the terminal stand-aside carries the Opportunity_Tier ``stand_aside``.
    assert decision["opportunity_tier"] == "stand_aside", (
        f"expected stand_aside tier, got {decision.get('opportunity_tier')!r}"
    )

    # R3.1: it carries the interim Best_Current_Read (present and non-None), so a
    # reasoning-exhaustion HOLD is as actionable as a bounded-hunt one.
    assert decision.get("best_current_read") is not None, (
        "force_hold decision is missing the Best_Current_Read"
    )
    read = decision["best_current_read"]
    assert isinstance(read, dict)
    # The Best_Current_Read is the non-committal {bias, levels, why_standing_aside}.
    assert set(read.keys()) == {"bias", "levels", "why_standing_aside"}
    assert read["bias"] in ("bullish", "bearish", "neutral")
    assert isinstance(read["why_standing_aside"], str) and read["why_standing_aside"].strip()

    # R3.2: no fabricated directional levels — each entry/stop_loss/take_profit key
    # is absent or None on the decision (never a finite committed level).
    for key in _DIRECTIONAL_LEVEL_KEYS:
        value = decision.get(key)
        assert value is None or not _is_finite_number(value), (
            f"stand-aside HOLD fabricated a directional {key} level: {value!r}"
        )

    # The interim read likewise surfaces no directional levels (nothing was
    # gathered, so nothing defensible can be surfaced).
    for key in _DIRECTIONAL_LEVEL_KEYS:
        assert not _is_finite_number(read["levels"].get(key)), (
            f"Best_Current_Read fabricated a directional {key} level"
        )

    # The finalize chokepoint journaled the decision (best-effort, here stubbed).
    assert recorded.get("called") is True
