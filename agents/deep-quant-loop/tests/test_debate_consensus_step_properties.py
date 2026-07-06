"""Property-based test for the debate-consensus verification step (task 12.2).

Feature: multi-agent-debate

This module implements design **Property 20: Exactly one debate-consensus
verification step with a total outcome mapping** (Requirements 8.2, 8.3, 8.4).

Two parts are exercised against the pure stream helpers in ``stream_events.py``:

  1. ``_debate_consensus_step(record)`` is a **total** mapping over arbitrary
     defensibility records. For any record (with or without a ``debate`` key, a
     non-dict ``debate`` value, or a ``debate`` dict whose ``consensus`` is drawn
     from the recognized enumeration, ``None``, or an arbitrary string), the
     returned step always carries ``check == "debate-consensus"`` and an
     ``outcome`` whose leading token is exactly the value the consensus maps to:
     ``strong_agree -> "pass"``, ``lean -> "informational"``,
     ``contested -> "fail"``, and everything else -> ``"not-evaluable"``
     (Requirements 8.2, 8.3, 8.4).

  2. ``build_verification_steps(decision)`` emits **exactly one**
     debate-consensus step for both a FIND-style decision (no
     ``validator_checks``) and a VERIFY-style decision (with
     ``validator_checks``), even when the validator checks themselves already
     carry a ``debate-consensus`` check (Requirement 8.2).

The sys.path / import pattern mirrors the sibling ``test_*_step_*`` modules.
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
    _debate_consensus_step,
    build_verification_steps,
)

DEBATE_CHECK = "debate-consensus"

# The recognized consensus -> leading outcome token mapping (R8.3).
_CONSENSUS_TO_TOKEN = {
    "strong_agree": "pass",
    "lean": "informational",
    "contested": "fail",
}


def _expected_token(record):
    """The outcome token ``_debate_consensus_step`` must produce for ``record``.

    A non-dict ``debate`` value (including a missing key) or an unrecognized /
    missing consensus both collapse to ``not-evaluable`` (R8.4).
    """
    debate = record.get("debate")
    if not isinstance(debate, dict):
        return "not-evaluable"
    return _CONSENSUS_TO_TOKEN.get(debate.get("consensus"), "not-evaluable")


# Consensus values: the recognized enumeration, the `unknown` sentinel, None,
# and arbitrary strings (which must all fall through to not-evaluable).
_consensus_values = st.one_of(
    st.sampled_from(["strong_agree", "lean", "contested", "unknown"]),
    st.none(),
    st.text(max_size=20),
)


@st.composite
def _debate_dicts(draw):
    """A debate sub-entry dict with an arbitrary consensus + optional fields."""
    entry = {"consensus": draw(_consensus_values)}
    if draw(st.booleans()):
        entry["conviction"] = draw(st.integers(min_value=0, max_value=100))
    if draw(st.booleans()):
        entry["conviction_basis"] = draw(st.text(max_size=20))
    if draw(st.booleans()):
        entry["committed_against_contested"] = draw(st.text(max_size=20))
    return entry


@st.composite
def _records(draw):
    """An arbitrary defensibility record exercising every debate-key shape."""
    record = {}
    kind = draw(st.sampled_from(["dict", "nondict", "absent"]))
    if kind == "dict":
        record["debate"] = draw(_debate_dicts())
    elif kind == "nondict":
        # A non-dict debate value must be treated as no debate entry.
        record["debate"] = draw(
            st.one_of(
                st.none(),
                st.integers(),
                st.text(max_size=10),
                st.lists(st.integers(), max_size=3),
                st.booleans(),
            )
        )
    # "absent": no debate key at all.

    # Sprinkle arbitrary unrelated record fields so the helper must ignore them.
    if draw(st.booleans()):
        record["risk_reward"] = draw(st.floats(allow_nan=False, allow_infinity=False))
    if draw(st.booleans()):
        record["regime"] = draw(st.dictionaries(st.text(max_size=5), st.integers(), max_size=2))
    return record


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: the outcome mapping is total over arbitrary records.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 20: Exactly one debate-consensus verification step with a total outcome mapping
@settings(max_examples=100, deadline=None)
@given(record=_records())
def test_property_20_consensus_step_mapping_is_total(record):
    """Validates: Requirements 8.2, 8.3, 8.4

    For any record, ``_debate_consensus_step`` returns a step keyed
    ``debate-consensus`` whose outcome's leading token matches the deterministic
    consensus mapping (strong_agree->pass, lean->informational, contested->fail,
    everything else->not-evaluable).
    """
    step = _debate_consensus_step(record)

    # Always the stable check id (R8.2).
    assert step["check"] == DEBATE_CHECK, f"unexpected check id: {step['check']!r}"

    outcome = step["outcome"]
    assert isinstance(outcome, str) and outcome, f"outcome must be a non-empty string, got {outcome!r}"

    expected = _expected_token(record)
    assert outcome.startswith(expected), (
        f"outcome {outcome!r} does not lead with expected token {expected!r} "
        f"for record {record!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: exactly one debate-consensus step in build_verification_steps output.
# ─────────────────────────────────────────────────────────────────────────────

@st.composite
def _validator_checks(draw):
    """A non-empty validator_checks list (VERIFY mode), occasionally already
    carrying a single ``debate-consensus`` check so the dedup path is exercised.

    The real VERIFY validator never emits duplicate ``debate-consensus`` checks
    (that dimension is derived, not a validator check), so the generator
    constrains the input space to at most one pre-existing debate-consensus
    entry — matching the assumption the dedup guard relies on.
    """
    outcomes = ["pass", "fail", "informational", "not-evaluable"]
    n = draw(st.integers(min_value=1, max_value=4))
    checks = [
        {
            "check": draw(st.sampled_from(["risk-reward", "volatility-stop", "macro-trend-alignment"])),
            "outcome": draw(st.sampled_from(outcomes)),
        }
        for _ in range(n)
    ]
    # Optionally inject exactly one pre-existing debate-consensus check at an
    # arbitrary position to exercise the dedup path.
    if draw(st.booleans()):
        checks.insert(
            draw(st.integers(min_value=0, max_value=len(checks))),
            {"check": DEBATE_CHECK, "outcome": draw(st.sampled_from(outcomes))},
        )
    return checks


@st.composite
def _decisions(draw):
    """A decision dict with a defensibility record, in FIND or VERIFY shape."""
    record = {"debate": draw(_debate_dicts())}
    mode = draw(st.sampled_from(["FIND", "VERIFY"]))
    if mode == "VERIFY":
        record["validator_checks"] = draw(_validator_checks())
    return {"action": draw(st.sampled_from(["BUY", "SELL", "HOLD"])), "defensibility": record}


# Feature: multi-agent-debate, Property 20: Exactly one debate-consensus verification step with a total outcome mapping
@settings(max_examples=100, deadline=None)
@given(decision=_decisions())
def test_property_20_exactly_one_debate_consensus_step(decision):
    """Validates: Requirements 8.2

    For any FIND- or VERIFY-style decision carrying a debate entry,
    ``build_verification_steps`` produces exactly one step keyed
    ``debate-consensus``, and its outcome matches the recorded consensus.
    """
    steps = build_verification_steps(decision)

    debate_steps = [s for s in steps if s.get("check") == DEBATE_CHECK]
    assert len(debate_steps) == 1, (
        f"expected exactly one debate-consensus step, got {len(debate_steps)} "
        f"for decision {decision!r}"
    )

    # The single step's outcome must lead with the token the consensus maps to.
    expected = _expected_token(decision["defensibility"])
    outcome = debate_steps[0]["outcome"]
    # In VERIFY mode a pre-existing validator debate-consensus check may supply a
    # bare outcome; only assert the mapping when the derived step was appended
    # (i.e. the validator checks did not already carry one).
    checks = decision["defensibility"].get("validator_checks") or []
    pre_existing = any(isinstance(c, dict) and c.get("check") == DEBATE_CHECK for c in checks)
    if not pre_existing:
        assert outcome.startswith(expected), (
            f"outcome {outcome!r} does not lead with expected token {expected!r}"
        )
