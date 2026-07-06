"""Property-based test for a usable tool result carrying all required fields
(tools.py, task 4.5).

Feature: options-agent-integration

This Hypothesis property exercises the ``get_options_analytics`` tool in
``tools.py`` with the F2 analytics engine MOCKED. It covers design **Property 8:
A usable result carries all required fields**: for any well-formed usable
``Options_Analytics_Result`` (``options.compute_options_analytics`` monkeypatched
to return it) and any symbol / proposed direction, a usable
(non-unavailable, non-error) ``get_options_analytics`` result carries ALL of the
required fields — the analytics fields (``pcr_oi``, ``pcr_volume``, ``max_pain``,
``oi_buildup``, ``oi_walls``, ``iv_skew``, ``futures_basis``, ``underlying``,
``expiry``, ``spot``), the bias fields (``options_bias_state`` in its enum,
``alignment`` in its enum, ``signals``), and ``chain_context`` in its enum — and
it passes its own Tool_Result_Contract (no ``error`` key, Requirement 2.5).

The single I/O dependency — the F2 engine ``options.compute_options_analytics``
— is monkeypatched at the module level (``options.compute_options_analytics``)
to return a generated well-formed analytics dict, so the full tool path runs
(arg validation -> chain resolution + label -> analytics -> config resolution ->
classify -> merge -> contract re-validation) with NO live QuestDB and NO F2
read layer. The monkeypatched attribute is restored in a ``finally`` block.

The sys.path / import pattern and the ``_raw`` @tool-unwrap helper mirror
``tests/test_rs_tool_wellformed_properties.py`` and
``tests/test_of_tool_wellformed_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / options.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
import tools  # noqa: E402
from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    INDEX_UNDERLYINGS,
    OPTIONS_BIAS_STATES,
    OPTIONS_CHAIN_CONTEXTS,
    get_options_analytics,
)

# The full set of fields a usable get_options_analytics result must carry
# (Requirement 2.5): the analytics fields merged verbatim from the F2 engine, the
# bias fields from the classifier, and the chain context.
_REQUIRED_FIELDS = (
    "pcr_oi",
    "pcr_volume",
    "max_pain",
    "oi_buildup",
    "oi_walls",
    "iv_skew",
    "futures_basis",
    "underlying",
    "expiry",
    "spot",
    "options_bias_state",
    "alignment",
    "signals",
    "chain_context",
)

# Index underlyings (own-chain) plus a few non-index symbols (broad-market) so
# both chain-resolution branches are exercised.
_SYMBOLS = sorted(INDEX_UNDERLYINGS) + ["RELIANCE", "HDFCBANK", "TCS", "INFY"]

# Proposed directions: empty (neutral), the directional pair, HOLD, lower-case
# variants, and junk — the tool must handle all without raising.
_DIRECTIONS = ["", "BUY", "SELL", "HOLD", "buy", "sell", "sideways"]


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


# A finite real number or null (None) — bool excluded by construction.
_num_or_null = st.one_of(
    st.none(),
    st.floats(min_value=-1.0e6, max_value=1.0e6,
              allow_nan=False, allow_infinity=False),
)

# A strictly-positive finite spot price (the system spot the engine resolves).
_positive = st.floats(min_value=1.0, max_value=1.0e6,
                      allow_nan=False, allow_infinity=False)

# An aggregate OI-buildup classification per side (or null / mixed).
_buildup_side = st.sampled_from(
    ["long_buildup", "short_buildup", "long_unwinding", "short_covering",
     "neutral", None]
)


@st.composite
def _usable_analytics(draw):
    """A well-formed, usable ``Options_Analytics_Result`` dict.

    Carries every field the merge reads and the contract checks: the
    numeric-or-null analytics (``pcr_oi``, ``pcr_volume``, ``max_pain``,
    ``futures_basis``), an ``oi_buildup`` object with ``call`` / ``put``, an
    ``oi_walls`` object with numeric-or-null ``support`` / ``resistance``, an
    ``iv_skew`` object-or-null, plus the resolved ``underlying`` / ``expiry`` /
    ``spot`` context. It NEVER carries an ``unavailable`` flag, so the tool
    always takes the usable path.
    """
    iv_skew = draw(
        st.one_of(
            st.none(),
            st.fixed_dictionaries({"put_minus_call": _num_or_null}),
        )
    )
    return {
        "underlying": draw(st.sampled_from(_SYMBOLS)),
        "expiry": draw(st.sampled_from(["2024-01-25", "2024-02-29", "", None])),
        "spot": draw(_positive),
        "pcr_oi": draw(_num_or_null),
        "pcr_volume": draw(_num_or_null),
        "max_pain": draw(_num_or_null),
        "oi_buildup": {"call": draw(_buildup_side), "put": draw(_buildup_side)},
        "oi_walls": {"support": draw(_num_or_null), "resistance": draw(_num_or_null)},
        "iv_skew": iv_skew,
        "futures_basis": draw(_num_or_null),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: A usable result carries all required fields
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 8: A usable result carries all required fields
@settings(max_examples=150, deadline=None)
@given(
    symbol=st.sampled_from(_SYMBOLS),
    proposed_direction=st.sampled_from(_DIRECTIONS),
    analytics=_usable_analytics(),
)
def test_property_8_usable_result_carries_all_required_fields(
    symbol, proposed_direction, analytics
):
    """Feature: options-agent-integration, Property 8: A usable result carries
    all required fields — for any well-formed usable Options_Analytics_Result
    (the F2 engine MOCKED) and any symbol / proposed direction, a
    non-unavailable, non-error get_options_analytics result carries every
    required analytics field, the bias fields (options_bias_state / alignment in
    their enums, signals), and chain_context in its enum, and passes its own
    contract (no error key).

    Validates: Requirements 2.5
    """
    # Monkeypatch the only I/O dependency (the F2 analytics engine) to return the
    # generated well-formed analytics, so the tool runs with no live QuestDB.
    original = options.compute_options_analytics
    try:
        options.compute_options_analytics = lambda underlying, expiry: analytics
        result = _raw(get_options_analytics)(
            symbol=symbol, proposed_direction=proposed_direction
        )
    finally:
        # Always restore the patched attribute, even on assertion failure.
        options.compute_options_analytics = original

    # The tool must never raise and always return a dict.
    assert isinstance(result, dict), f"tool result is not a dict: {result!r}"

    # A well-formed usable analytics dict must take the usable path: the result
    # is neither an Unavailable_Marker nor a contract error.
    assert result.get("unavailable") is not True, (
        f"usable analytics unexpectedly degraded to unavailable: {result!r}"
    )
    assert "error" not in result, (
        f"usable result unexpectedly failed its own contract: {result.get('error')!r}"
    )

    # ── Every required field must be present (Requirement 2.5) ────────────────
    for field in _REQUIRED_FIELDS:
        assert field in result, f"required field '{field}' missing from {result!r}"

    # ── The three categorical fields are each drawn from their fixed enums ────
    assert result["options_bias_state"] in OPTIONS_BIAS_STATES, (
        f"options_bias_state {result['options_bias_state']!r} "
        f"not in {OPTIONS_BIAS_STATES}"
    )
    assert result["alignment"] in ALIGNMENT_VALUES, (
        f"alignment {result['alignment']!r} not in {ALIGNMENT_VALUES}"
    )
    assert result["chain_context"] in OPTIONS_CHAIN_CONTEXTS, (
        f"chain_context {result['chain_context']!r} not in {OPTIONS_CHAIN_CONTEXTS}"
    )

    # ── The driving signals object must be present as a dict ──────────────────
    assert isinstance(result["signals"], dict), (
        f"'signals' is not a dict: {result['signals']!r}"
    )
