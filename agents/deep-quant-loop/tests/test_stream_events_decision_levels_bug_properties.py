"""R1 bug-condition exploration test — DECISION payload level threading.

Feature: deep-quant-runtime-hardening (bugfix)

Property 11 (Bug Condition), Python `stream_events.build_decision_event` seam —
"HOLD / stand-aside must never render as an executable trade":

    For any committed decision, ``build_decision_event`` should thread the
    decision ``action`` and, ONLY for a directional (BUY/SELL) decision whose
    entry/stop_loss/take_profit are all finite numbers, an ``execution_levels``
    object carrying those three validated prices; a HOLD / stand-aside decision
    should carry no ``execution_levels``. This is the structural contract the UI
    needs to gate the APPROVE & EXECUTE card on (design Property 11).

    Validates: Requirements 1.1, 1.5.

*** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***

The unfixed ``build_decision_event`` (``stream_events.py`` ~1015) builds a
payload of only ``{action, conviction_score, rationale, execution_plan}`` (plus
an optional ``opportunity_tier`` / ``size_factor``). It NEVER adds
``execution_levels``, even when the committed decision is a directional BUY/SELL
carrying validated entry/stop_loss/take_profit. So the frontend receives no
structural levels to gate on and is forced to scrape/synthesize them from prose
(the R1 capital-safety defect). The failure of the directional assertion below
is the informative, expected outcome — it proves the UI "has nothing structural
to gate on". DO NOT fix the code here; task 2.1 threads the gated levels and
task 2.6 re-runs THIS SAME test to confirm the fix.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import stream_events`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import build_decision_event  # noqa: E402


# ── Generators ────────────────────────────────────────────────────────────────

# Finite, positive, sanely-bounded prices — the shape a validated directional
# trade actually carries.
_price = st.floats(
    min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)


def _directional_decision(action, entry, stop_loss, take_profit):
    """A committed directional decision as produced by ``_decision_from_declare``:
    it carries the validated entry/stop_loss/take_profit (design R1 mechanism)."""
    return {
        "action": action,
        "conviction_score": 82,
        "opportunity_tier": "a_plus",
        "setup_validation": "Momentum breakout confirmed with volume.",
        "execution_plan": f"Entry: {entry}. Stop-loss: {stop_loss}. Target 1: {take_profit}.",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


# ── Property 11 (bug condition): directional levels are threaded structurally ──


@settings(max_examples=150)
@given(
    action=st.sampled_from(["BUY", "SELL"]),
    entry=_price,
    stop_loss=_price,
    take_profit=_price,
)
def test_directional_decision_payload_threads_execution_levels(
    action, entry, stop_loss, take_profit
):
    """A directional BUY/SELL decision with three finite validated prices SHOULD
    yield a payload carrying ``execution_levels`` == those prices.

    EXPECTED FAIL on unfixed code: ``build_decision_event`` omits
    ``execution_levels`` entirely, so the UI has no structural levels to gate on.
    """
    decision = _directional_decision(action, entry, stop_loss, take_profit)

    payload = build_decision_event(decision)
    assert payload is not None

    # The action must be threaded (contract R1.1).
    assert payload.get("action") == action

    # The validated levels must be threaded structurally (contract R1.5 / P11).
    assert "execution_levels" in payload, (
        "directional decision produced no execution_levels — the UI has nothing "
        "structural to gate on and is forced to synthesize levels from prose"
    )
    levels = payload["execution_levels"]
    assert isinstance(levels, dict)
    assert math.isclose(levels.get("entry"), entry)
    assert math.isclose(levels.get("stop_loss"), stop_loss)
    assert math.isclose(levels.get("take_profit"), take_profit)


# ── Property 11 (preservation side): HOLD / stand-aside carries no levels ───────


@settings(max_examples=100)
@given(
    tier=st.sampled_from(["stand_aside", "watch", ""]),
    conviction=st.sampled_from([74, 60, None]),
)
def test_hold_decision_payload_carries_no_execution_levels(tier, conviction):
    """A committed HOLD / stand-aside decision must never carry execution_levels
    (there are no validated directional levels to thread)."""
    decision = {
        "action": "HOLD",
        "conviction_score": conviction,
        "opportunity_tier": tier,
        "setup_validation": "Standing aside — chop, no edge. Rule: stop >= 1.5x ATR.",
        "execution_plan": "No trade. Watch for a break. Target 1: reassess.",
    }

    payload = build_decision_event(decision)
    assert payload is not None
    assert payload.get("action") == "HOLD"
    assert "execution_levels" not in payload


# ── Documented concrete counterexample (the real CUPID HOLD) ───────────────────


def test_concrete_directional_hold_has_no_structural_gate():
    """Concrete counterexample documenting the defect: a directional decision
    carrying validated levels still emits NO execution_levels on unfixed code,
    so the UI cannot structurally distinguish it from a HOLD.

    EXPECTED FAIL on unfixed code: ``execution_levels`` is absent.
    """
    decision = _directional_decision("BUY", 24150.0, 23667.0, 25357.5)
    payload = build_decision_event(decision)
    assert payload is not None
    # Counterexample: the payload the UI receives has no levels to gate on.
    assert "execution_levels" in payload, (
        "counterexample: BUY entry=24150 stop=23667 target=25357.5 produced a "
        "payload with no execution_levels — the UI must synthesize levels"
    )
    assert payload["execution_levels"] == {
        "entry": 24150.0,
        "stop_loss": 23667.0,
        "take_profit": 25357.5,
    }
