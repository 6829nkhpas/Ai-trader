"""Property-based test for debate-step-before-DECISION ordering (stream_events.py, task 12.4).

Feature: multi-agent-debate

This module implements design **Property 22: The debate-consensus step precedes
the DECISION event**:

    For ANY DEBATE-mode decision (a structured decision dict whose defensibility
    record carries a ``debate`` entry), in the ordered event stream produced by
    ``decision_events`` the debate-consensus ``VERIFICATION_STEP`` event appears
    BEFORE the ``DECISION`` event of that run.

Validates: Requirements 8.5.

``decision_events`` first yields every ``VERIFICATION_STEP`` tuple built by
``build_verification_steps`` and only then the ``DECISION`` tuple built by
``build_decision_event``. The debate-consensus step (check id
``debate-consensus``) is one of those VERIFICATION_STEPs — appended in both the
FIND-style derivation and the VERIFY-mode (``validator_checks``) derivation — so
it is always ordered ahead of the DECISION.

The strategy constructs decision dicts that exercise BOTH derivation paths:

  * FIND-style records (no ``validator_checks``): the four self-verification
    checks plus the regime / relative-strength / forecast / management / session
    / debate steps are derived from the recorded evidence.
  * VERIFY-mode records (an explicit non-empty ``validator_checks`` list): those
    checks are surfaced verbatim and the debate step is appended.

In every case the record carries a ``debate`` entry (consensus drawn from
``strong_agree`` / ``lean`` / ``contested`` plus conviction / basis / stances)
and the decision carries an action and conviction score so
``build_decision_event`` emits a DECISION. The test then asserts the
debate-consensus step index is strictly less than the DECISION index, and that
exactly one debate-consensus step and exactly one DECISION event are present.

The sys.path / import pattern mirrors the sibling ``test_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    DECISION,
    VERIFICATION_STEP,
    decision_events,
)

_CONSENSUS_VALUES = ["strong_agree", "lean", "contested"]
_ACTIONS = ["BUY", "SELL", "HOLD"]


@st.composite
def _debate_entry(draw):
    """A defensibility ``debate`` sub-entry as built by ``graph`` for a DEBATE run.

    Shape mirrors ``_debate_consensus_step``'s documented contract: a recognized
    consensus plus conviction / basis and (optionally) the bull/bear stances and
    the committed-against-contested statement.
    """
    consensus = draw(st.sampled_from(_CONSENSUS_VALUES))
    entry = {
        "consensus": consensus,
        "conviction": draw(st.integers(min_value=0, max_value=100)),
    }
    if draw(st.booleans()):
        entry["conviction_basis"] = draw(
            st.text(min_size=0, max_size=40).filter(lambda s: "\x00" not in s)
        )
    if draw(st.booleans()):
        entry["bull_stance"] = {"lean": "long", "strength": draw(st.integers(0, 100))}
    if draw(st.booleans()):
        entry["bear_stance"] = {"lean": "short", "strength": draw(st.integers(0, 100))}
    if consensus == "contested" and draw(st.booleans()):
        entry["committed_against_contested"] = "Committed a directional trade against a contested debate."
    return entry


@st.composite
def _availability_entry(draw, labels):
    """An optional availability-style sub-entry (regime/session/forecast/...).

    Either an Unavailable_Marker, an available labelled entry, or absent — so the
    FIND-style derivation exercises its pass/fail/informational/not-evaluable
    branches across the sibling steps without changing the debate ordering.
    """
    choice = draw(st.integers(min_value=0, max_value=2))
    if choice == 0:
        return None  # absent
    if choice == 1:
        return {"available": False, "reason": draw(st.sampled_from(["no data", "stale", ""]))}
    return {"available": True, **{k: draw(st.sampled_from(v)) for k, v in labels.items()}}


@st.composite
def _validator_checks(draw):
    """A non-empty ``validator_checks`` list (drives the VERIFY-mode derivation)."""
    n = draw(st.integers(min_value=1, max_value=4))
    checks = []
    for _ in range(n):
        checks.append(
            {
                "check": draw(
                    st.sampled_from(
                        ["risk-rules", "data-availability", "setup-quality", "sizing"]
                    )
                ),
                "outcome": draw(st.sampled_from(["pass", "fail", "informational"])),
            }
        )
    return checks


@st.composite
def _debate_decision(draw):
    """A DEBATE-mode decision dict, in FIND-style or VERIFY-mode shape.

    Always carries a ``debate`` entry and an action/conviction score so
    ``decision_events`` emits both the debate-consensus VERIFICATION_STEP and the
    DECISION event.
    """
    record = {"debate": draw(_debate_entry())}

    # Optionally populate sibling availability entries to vary the FIND-style and
    # VERIFY-mode sibling steps (never affects the debate-step ordering).
    record["regime"] = draw(
        _availability_entry(
            {"favorability": ["favorable", "unfavorable", "neutral", "bogus"]}
        )
    )
    record["relative_strength"] = draw(
        _availability_entry({"alignment": ["aligned", "misaligned", "neutral", "bogus"]})
    )
    record["forecast"] = draw(
        _availability_entry(
            {"forecast_alignment": ["aligned", "misaligned", "neutral", "bogus"]}
        )
    )
    record["session"] = draw(
        _availability_entry(
            {"time_favorability": ["favorable", "unfavorable", "neutral", "bogus"]}
        )
    )

    # VERIFY mode vs FIND mode: an explicit non-empty validator_checks list flips
    # build_verification_steps onto the VERIFY derivation; otherwise it derives
    # the FIND-style steps.
    if draw(st.booleans()):
        record["validator_checks"] = draw(_validator_checks())

    decision = {
        "defensibility": record,
        "action": draw(st.sampled_from(_ACTIONS)),
        "conviction_score": draw(st.integers(min_value=0, max_value=100)),
        "reason": draw(st.text(min_size=0, max_size=30).filter(lambda s: "\x00" not in s)),
    }
    if draw(st.booleans()):
        decision["setup_validation"] = draw(
            st.text(min_size=1, max_size=30).filter(lambda s: "\x00" not in s)
        )
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: The debate-consensus step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 22: The debate-consensus step precedes the DECISION event
@settings(max_examples=100, deadline=None)
@given(decision=_debate_decision())
def test_property_22_debate_step_precedes_decision(decision):
    """Validates: Requirements 8.5

    For any DEBATE-mode decision, the debate-consensus VERIFICATION_STEP event is
    emitted before the DECISION event, with exactly one of each present.
    """
    events = list(decision_events(decision))

    debate_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == "debate-consensus"
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one debate-consensus step and exactly one DECISION event (R8.5).
    assert len(debate_indices) == 1, (
        f"expected exactly one debate-consensus VERIFICATION_STEP, got "
        f"{len(debate_indices)}: {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, got {len(decision_indices)}: {events!r}"
    )

    # The debate-consensus step is ordered strictly before the DECISION (R8.5).
    assert debate_indices[0] < decision_indices[0], (
        f"debate-consensus step at index {debate_indices[0]} must precede the "
        f"DECISION at index {decision_indices[0]}: {events!r}"
    )
