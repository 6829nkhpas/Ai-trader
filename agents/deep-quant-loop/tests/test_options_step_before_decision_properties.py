"""Property-based test for options-step-before-DECISION ordering (stream_events.py, task 10.3).

Feature: options-agent-integration

This module implements design **Property 15: The options step precedes the
decision event**:

    For ANY run that emits decision events (a structured decision dict whose
    defensibility record carries an ``options`` entry), in the ordered event
    stream produced by ``decision_events`` the options ``VERIFICATION_STEP``
    event (check id ``options``) appears BEFORE the ``DECISION`` event of that
    run.

Validates: Requirements 7.4.

``decision_events`` first yields every ``VERIFICATION_STEP`` tuple built by
``build_verification_steps`` and only then the ``DECISION`` tuple built by
``build_decision_event``. The options step (check id ``options``) is one of
those VERIFICATION_STEPs — appended in both the FIND-style derivation and the
VERIFY-mode (``validator_checks``) derivation — so it is always ordered ahead of
the DECISION.

The strategy constructs decision dicts that exercise BOTH derivation paths:

  * FIND-style records (no ``validator_checks``): the sibling regime / relative-
    strength / forecast / management / session steps and the options step are
    derived from the recorded evidence.
  * VERIFY-mode records (an explicit non-empty ``validator_checks`` list): those
    checks are surfaced verbatim and the options step is appended.

In every case the record carries an ``options`` entry that is either a usable
Options_Bias_Label (``available`` True with an ``alignment`` drawn from
``aligned`` / ``misaligned`` / ``neutral`` / an unrecognized value) or an
Unavailable_Marker (``available`` False), so the options step is emitted in all
of its outcome branches (pass / fail / informational / not-evaluable). The
decision carries an action and conviction score so ``build_decision_event``
emits a DECISION. The test then asserts the options step index is strictly less
than the DECISION index, and that exactly one options step and exactly one
DECISION event are present.

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

# Recognized alignments plus an unrecognized sentinel so the options step
# exercises pass / fail / informational AND its not-evaluable (unrecognized)
# branch — every path keeps the step ordered ahead of the DECISION.
_ALIGNMENTS = ["aligned", "misaligned", "neutral", "bogus"]
_BIAS_STATES = ["bullish", "bearish", "neutral", "unknown"]
_ACTIONS = ["BUY", "SELL", "HOLD"]


@st.composite
def _options_entry(draw):
    """A defensibility ``options`` sub-entry as built by ``graph._options_entry``.

    Either a usable Options_Bias_Label (``available`` True with an alignment +
    bias state and optional chain context) or an Unavailable_Marker
    (``available`` False with a reason) — so the options step is exercised across
    its pass / fail / informational / not-evaluable branches without ever
    changing the options-step ordering relative to the DECISION.
    """
    if draw(st.booleans()):
        # Unavailable_Marker: drives the not-evaluable branch (R7.3).
        return {
            "available": False,
            "reason": draw(st.sampled_from(["no chain", "stale snapshot", ""])),
        }
    # Usable Options_Bias_Label.
    entry = {
        "available": True,
        "alignment": draw(st.sampled_from(_ALIGNMENTS)),
        "options_bias_state": draw(st.sampled_from(_BIAS_STATES)),
    }
    if draw(st.booleans()):
        entry["pcr_oi"] = draw(st.floats(min_value=0.0, max_value=5.0, allow_nan=False))
    if draw(st.booleans()):
        entry["max_pain"] = draw(st.integers(min_value=0, max_value=100000))
    if draw(st.booleans()):
        entry["oi_walls"] = {
            "support": draw(st.integers(min_value=0, max_value=100000)),
            "resistance": draw(st.integers(min_value=0, max_value=100000)),
        }
    return entry


@st.composite
def _availability_entry(draw, labels):
    """An optional availability-style sibling sub-entry (regime/session/forecast/...).

    Either an Unavailable_Marker, an available labelled entry, or absent — so the
    FIND-style derivation exercises its pass/fail/informational/not-evaluable
    branches across the sibling steps without changing the options-step ordering.
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
def _options_decision(draw):
    """A decision dict carrying an ``options`` entry, in FIND-style or VERIFY-mode shape.

    Always carries an ``options`` entry and an action/conviction score so
    ``decision_events`` emits both the options VERIFICATION_STEP and the DECISION
    event.
    """
    record = {"options": draw(_options_entry())}

    # Optionally populate sibling availability entries to vary the FIND-style and
    # VERIFY-mode sibling steps (never affects the options-step ordering).
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
# Property 15: The options step precedes the decision event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 15: The options step precedes the decision event
@settings(max_examples=200, deadline=None)
@given(decision=_options_decision())
def test_property_15_options_step_precedes_decision(decision):
    """Validates: Requirements 7.4

    For any run that emits decision events, the options VERIFICATION_STEP event
    is emitted before the DECISION event, with exactly one of each present.
    """
    events = list(decision_events(decision))

    options_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == "options"
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one options step and exactly one DECISION event (R7.4).
    assert len(options_indices) == 1, (
        f"expected exactly one options VERIFICATION_STEP, got "
        f"{len(options_indices)}: {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, got {len(decision_indices)}: {events!r}"
    )

    # The options step is ordered strictly before the DECISION (R7.4).
    assert options_indices[0] < decision_indices[0], (
        f"options step at index {options_indices[0]} must precede the "
        f"DECISION at index {decision_indices[0]}: {events!r}"
    )
