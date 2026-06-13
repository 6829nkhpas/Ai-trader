"""Property-based test for the relative-strength verification step (task 10.2).

Feature: relative-strength-context

This module implements design **Property 23: Exactly one relative-strength
verification step with the correct outcome mapping**:

    For any defensibility record, building the verification steps yields EXACTLY
    ONE step carrying the stable check id ``relative-strength``, whose outcome
    maps the recorded Alignment as:

        aligned      -> pass            (R9.2)
        misaligned   -> fail            (R9.3)
        neutral      -> informational   (R9.4)
        unavailable  -> not-evaluable (carrying an 'unavailable' indication;
                                       no fabricated Alignment, R9.5)

    and the single step is present for every relative-strength shape (R9.1).

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5.

The implementation under test lives in ``stream_events.py``:
  - ``build_verification_steps(decision)`` — FIND-mode records (no
    ``validator_checks``) route to ``_derive_find_mode_steps`` which appends
    exactly one ``_relative_strength_step(record)``.
  - ``_relative_strength_step(record)`` — maps the defensibility
    ``relative_strength`` entry to a single step under the fixed check id
    ``relative-strength``.

The real LLM / graph is never invoked. The defensibility ``relative_strength``
entry is built directly in the shape ``graph._relative_strength_entry``
produces: a usable label ``{"available": True, "alignment": ...,
"index_direction": ..., "relative_strength_state": ..., ...}`` or an
Unavailable_Marker ``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors ``tests/test_stream_events.py``: the
service directory (one level up) is prepended to ``sys.path`` so
``stream_events`` is importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import build_verification_steps  # noqa: E402

RS_CHECK = "relative-strength"

# The Alignment -> outcome mapping the step must implement (R9.2-R9.4).
_ALIGNMENT_OUTCOME = {
    "aligned": "pass",
    "misaligned": "fail",
    "neutral": "informational",
}
# Outcomes that would betray a fabricated Alignment on the unavailable path.
_FABRICATED_OUTCOMES = set(_ALIGNMENT_OUTCOME.values())


# ── Strategies ───────────────────────────────────────────────────────────────
_index_direction = st.sampled_from(["up", "down", "flat"])
_relative_strength_state = st.sampled_from(["leader", "inline", "laggard"])

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _available_rs_entry(draw):
    """A usable relative-strength entry across all three Alignment values (R9.2-9.4)."""
    alignment = draw(st.sampled_from(["aligned", "misaligned", "neutral"]))
    return {
        "available": True,
        "alignment": alignment,
        "index_direction": draw(_index_direction),
        "relative_strength_state": draw(_relative_strength_state),
        "benchmark": draw(st.sampled_from(["NIFTY 50", "BANKNIFTY"])),
        "measures": {
            "rs_ratio": draw(_measure_value),
            "rs_ratio_slope": draw(_measure_value),
            "relative_return": draw(_measure_value),
            "correlation": draw(_measure_value),
            "beta": draw(_measure_value),
        },
    }


# An Unavailable_Marker entry: available False, only a reason (no states, R9.5).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "insufficient aligned data: 12 aligned candles available, 31 required",
            "candle retrieval timed out",
            "benchmark BANKNIFTY has no available candle data",
            "no relative-strength measure could be computed",
        ]
    ),
)
_unavailable_rs_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

_rs_entry = st.one_of(_available_rs_entry(), _unavailable_rs_entry)

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect the single relative-strength step. Crucially the record
# carries NO ``validator_checks`` so it routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_rs_step(steps):
    """Return the single relative-strength step, asserting exactly one (R9.1)."""
    rs_steps = [s for s in steps if s.get("check") == RS_CHECK]
    assert len(rs_steps) == 1, (
        f"expected exactly one '{RS_CHECK}' step, got {len(rs_steps)}"
    )
    return rs_steps[0]


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: exactly one relative-strength verification step + outcome mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 23: Exactly one relative-strength verification step with the correct outcome mapping
@settings(max_examples=100, deadline=None)
@given(
    rs=_rs_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_23_relative_strength_verification_step_outcome_mapping(rs, extras, action):
    """Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5

    For any relative-strength entry shape (each Alignment value or unavailable),
    building the FIND-mode verification steps yields exactly ONE step with check
    id ``relative-strength`` whose outcome maps Alignment correctly, and for the
    unavailable case carries an 'unavailable' indication with no fabricated
    Alignment.
    """
    record = dict(extras)
    record["relative_strength"] = rs
    decision = {"action": action, "defensibility": record}

    steps = build_verification_steps(decision)

    # ── R9.1: exactly one relative-strength step under the stable check id ───
    step = _only_rs_step(steps)
    assert step["check"] == RS_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    if rs.get("available") and rs.get("alignment") in _ALIGNMENT_OUTCOME:
        # ── R9.2 / R9.3 / R9.4: alignment maps to the exact outcome ──────────
        expected = _ALIGNMENT_OUTCOME[rs["alignment"]]
        assert outcome == expected, (
            f"alignment={rs['alignment']} -> outcome {outcome!r}, "
            f"expected {expected!r}"
        )
    else:
        # ── R9.5: unavailable -> not-evaluable, no fabricated alignment ──────
        assert outcome.startswith("not-evaluable"), (
            f"unavailable relative strength must report not-evaluable, "
            f"got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable relative-strength outcome must carry an 'unavailable' "
            f"indication, got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents an alignment field.
        assert "alignment" not in step
