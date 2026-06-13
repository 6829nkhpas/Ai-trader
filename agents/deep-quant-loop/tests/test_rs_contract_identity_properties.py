"""Property-based test for contract identity on conforming results/markers (tools.py, task 5.8).

Feature: relative-strength-context

This Hypothesis property exercises ``validate_contract``'s
``get_relative_strength`` branch:

  * Property 15 (4.6, 4.8) — ``validate_contract`` is the identity on conforming
    results and markers: for any generated conforming ``get_relative_strength``
    Relative_Strength_Label, and for any Unavailable_Marker, ``validate_contract(
    "get_relative_strength", result)`` returns that result unchanged.

A conforming Relative_Strength_Label carries the three categorical states drawn
from their fixed enums (``index_direction`` in {up, down, flat},
``relative_strength_state`` in {leader, inline, laggard}, ``alignment`` in
{aligned, misaligned, neutral}), a ``benchmark`` string, plus a ``measures``
object whose every named measure (rs_ratio / rs_ratio_slope / relative_return /
correlation / beta) is a finite number or ``null``. An Unavailable_Marker
carries ``{"unavailable": true, "reason": ...}`` and (per AD-4) omits the states.

The test asserts the call never raises and returns the *same object* unchanged
(both object identity and deep equality), pinning the contract's pass-through
behavior across the full conforming input space.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    INDEX_DIRECTIONS,
    RELATIVE_STRENGTH_STATES,
    _RS_MEASURE_FIELDS,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

# A finite number or null — exactly what each named Relative_Strength_Measure is
# allowed to be in a conforming label (R3.3, R3.5). Bools are excluded because
# the contract's ``_is_number`` rejects them.
_finite_number = st.floats(allow_nan=False, allow_infinity=False)
_measure_value = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    _finite_number,
)

_timeframe = st.sampled_from(sorted({"1m", "5m", "10m", "15m", "1h", "4h", "1d"}))
_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)
_benchmark = st.sampled_from(["NIFTY 50", "BANKNIFTY", "FINNIFTY", "NIFTY IT"])


@st.composite
def _conforming_label(draw):
    """A conforming ``get_relative_strength`` Relative_Strength_Label.

    Every categorical state is drawn from its fixed enum, ``benchmark`` is a
    string, and the ``measures`` object carries each named measure as a finite
    number or null, so the label satisfies the contract that
    ``validate_contract`` enforces.
    """
    measures = {
        field: draw(_measure_value) for field in _RS_MEASURE_FIELDS
    }
    label = {
        "index_direction": draw(st.sampled_from(sorted(INDEX_DIRECTIONS))),
        "relative_strength_state": draw(
            st.sampled_from(sorted(RELATIVE_STRENGTH_STATES))
        ),
        "alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "measures": measures,
        "benchmark": draw(_benchmark),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "aligned_candles": draw(st.integers(min_value=1, max_value=10_000)),
    }
    return label


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker ({"unavailable": true, "reason": ...}).

    Per AD-4 the marker omits Index_Direction / Relative_Strength_State /
    Alignment; it is recognized as an honest non-fatal result and must pass
    through unchanged.
    """
    marker = {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "insufficient aligned data: 12 aligned candles available, 31 required",
                    "candle retrieval timed out",
                    "benchmark 'BANKNIFTY' candle retrieval failed",
                    "no relative-strength measure could be computed",
                ]
            )
        ),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "benchmark": draw(_benchmark),
    }
    return marker


_conforming_result = st.one_of(_conforming_label(), _unavailable_marker())


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: validate_contract is the identity on conforming results & markers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 15: validate_contract is the identity on conforming results and markers
@settings(max_examples=100, deadline=None)
@given(result=_conforming_result)
def test_property_15_validate_contract_is_identity_on_conforming_rs(result):
    """Validates: Requirements 4.6, 4.8

    For any conforming Relative_Strength_Label or any Unavailable_Marker,
    ``validate_contract("get_relative_strength", result)`` returns that result
    unchanged (object identity + deep equality) and never raises.
    """
    # Snapshot for an after-the-fact equality check (defends against any
    # accidental mutation of the input by the validator).
    import copy

    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_relative_strength", result)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} on a conforming result/marker"
        )

    # Identity: the exact same object is passed through (the branch returns
    # ``payload`` unchanged; the marker path returns it via _has_honest_marker).
    assert returned is result, "validate_contract did not return the same object"

    # It is not flagged as a violation.
    assert not (
        isinstance(returned, dict) and "contract_violation" in returned
    ), "conforming result/marker was incorrectly flagged as a contract violation"

    # Unchanged: the returned object equals the pre-call snapshot.
    assert returned == snapshot, "validate_contract altered the input result"
