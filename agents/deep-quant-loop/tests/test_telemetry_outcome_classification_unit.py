"""Unit tests for telemetry outcome classification examples (telemetry.py, task 4.7).

Feature: session-telemetry

Concrete, example-based tests for ``classify_outcome`` covering each representative
terminal case (Requirement 1.4):

    * BUY               -> (``trade_buy``, None)
    * SELL              -> (``trade_sell``, None)
    * forced HOLD       -> (``hold``, ``forced``)
    * data-gated HOLD   -> (``hold``, ``data-gated``)
    * plain HOLD        -> (``hold``, ``voluntary``)
    * errored run       -> (``error``, None)

These complement the Property 3 property test (task 4.2) with pinned examples that
document the exact marker semantics the Deep Quant graph emits.

Validates: Requirements 1.4.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_classification_properties.py``.
"""

import os
import sys

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    HOLD_DATA_GATED,
    HOLD_FORCED,
    HOLD_VOLUNTARY,
    OUTCOME_ERROR,
    OUTCOME_HOLD,
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    classify_outcome,
)


# ── Directional trades ────────────────────────────────────────────────────────

def test_buy_decision_classifies_as_trade_buy():
    """A committed BUY decision is a ``trade_buy`` with no hold_reason."""
    outcome, hold_reason = classify_outcome({"action": "BUY"}, "completed", False)
    assert outcome == OUTCOME_TRADE_BUY
    assert hold_reason is None


def test_sell_decision_classifies_as_trade_sell():
    """A committed SELL decision is a ``trade_sell`` with no hold_reason."""
    outcome, hold_reason = classify_outcome({"action": "SELL"}, "completed", False)
    assert outcome == OUTCOME_TRADE_SELL
    assert hold_reason is None


# ── HOLD sub-reasons ──────────────────────────────────────────────────────────

def test_forced_hold_via_source_marker_classifies_as_hold_forced():
    """A HOLD carrying the ``forced_hold`` source marker is a forced HOLD."""
    outcome, hold_reason = classify_outcome(
        {"action": "HOLD", "source": "forced_hold"}, "completed", False
    )
    assert outcome == OUTCOME_HOLD
    assert hold_reason == HOLD_FORCED


def test_forced_hold_via_no_decision_reason_classifies_as_hold_forced():
    """A HOLD with the ``no-decision-reached`` reason is a forced HOLD."""
    outcome, hold_reason = classify_outcome(
        {"action": "HOLD", "reason": "no-decision-reached"}, "completed", False
    )
    assert outcome == OUTCOME_HOLD
    assert hold_reason == HOLD_FORCED


def test_data_gated_hold_classifies_as_hold_data_gated():
    """A HOLD with the ``directional-data-unavailable`` reason is data-gated."""
    outcome, hold_reason = classify_outcome(
        {"action": "HOLD", "reason": "directional-data-unavailable"}, "completed", False
    )
    assert outcome == OUTCOME_HOLD
    assert hold_reason == HOLD_DATA_GATED


def test_plain_hold_classifies_as_hold_voluntary():
    """A HOLD with no forced/gated marker is a voluntary stand-aside."""
    outcome, hold_reason = classify_outcome({"action": "HOLD"}, "completed", False)
    assert outcome == OUTCOME_HOLD
    assert hold_reason == HOLD_VOLUNTARY


# ── Error ─────────────────────────────────────────────────────────────────────

def test_errored_run_classifies_as_error():
    """An errored run is terminal ``error`` regardless of any partial decision."""
    outcome, hold_reason = classify_outcome(None, "error", True)
    assert outcome == OUTCOME_ERROR
    assert hold_reason is None


def test_errored_run_takes_precedence_over_decision():
    """The error flag is authoritative even when a decision record is present."""
    outcome, hold_reason = classify_outcome({"action": "BUY"}, "error", True)
    assert outcome == OUTCOME_ERROR
    assert hold_reason is None
