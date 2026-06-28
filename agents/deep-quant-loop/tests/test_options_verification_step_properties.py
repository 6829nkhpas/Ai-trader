"""Property-based test for the options verification step (task 10.2).

Feature: options-agent-integration

This module implements design **Property 14: Exactly one options verification
step maps alignment to outcome**:

    For any decision record, the event stream emits exactly one options
    ``VERIFICATION_STEP`` with the stable check id ``options`` and an outcome of
    ``pass``, ``fail``, ``informational``, or ``not-evaluable``, where
    ``aligned`` -> ``pass``, ``misaligned`` -> ``fail``, ``neutral`` ->
    ``informational``, and an unavailable/unrecognized entry -> ``not-evaluable``
    (never a fabricated alignment).

Validates: Requirements 7.1, 7.2, 7.3.

The implementation under test lives in ``stream_events.py``:
  - ``_options_step(record)`` — maps the defensibility ``options`` entry to a
    single step under the fixed check id ``options`` (R7.1-R7.3).
  - ``_derive_find_mode_steps(record)`` — FIND-mode derivation; appends exactly
    one ``_options_step(record)``.
  - ``build_verification_steps(decision)`` — surfaces exactly one options step
    in both FIND mode (no ``validator_checks``) and VERIFY mode (an explicit
    ``validator_checks`` list).

The real LLM / graph is never invoked. The defensibility ``options`` entry is
built directly in the shape ``graph._options_entry`` produces: a usable
Options_Bias_Label ``{"available": True, "options_bias_state": ...,
"alignment": ..., "chain_context": ..., "pcr_oi": ..., "max_pain": ...,
"oi_walls": {...}, ...}`` or an Unavailable_Marker ``{"available": False,
"reason": ...}``.

The sys.path / import pattern mirrors
``tests/test_session_verification_step_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``stream_events`` is importable
when pytest is run from anywhere.
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
    _options_step,
    _derive_find_mode_steps,
    build_verification_steps,
)

OPTIONS_CHECK = "options"

# The Alignment -> outcome mapping the step must implement (R7.2).
_ALIGNMENT_OUTCOME = {
    "aligned": "pass",
    "misaligned": "fail",
    "neutral": "informational",
}
# Outcomes that would betray a fabricated Alignment on the unavailable path.
_FABRICATED_OUTCOMES = set(_ALIGNMENT_OUTCOME.values())

_BIAS_STATES = ["bullish", "bearish", "neutral"]
_CHAIN_CONTEXTS = ["own-chain", "broad-market"]


# ── Strategies ───────────────────────────────────────────────────────────────
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _oi_walls(draw):
    """An OI-wall support/resistance dict (either side may be null)."""
    return {
        "support": draw(_measure_value),
        "resistance": draw(_measure_value),
    }


@st.composite
def _available_options_entry(draw):
    """A usable options entry across all three Alignment values (R7.2)."""
    alignment = draw(st.sampled_from(["aligned", "misaligned", "neutral"]))
    return {
        "available": True,
        "options_bias_state": draw(st.sampled_from(_BIAS_STATES)),
        "alignment": alignment,
        "chain_context": draw(st.sampled_from(_CHAIN_CONTEXTS)),
        "pcr_oi": draw(_measure_value),
        "max_pain": draw(_measure_value),
        "oi_walls": draw(_oi_walls()),
        "iv_skew": draw(_measure_value),
        "futures_basis": draw(_measure_value),
    }


# An Unavailable_Marker entry: available False, only an optional reason (R7.3).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "no option chain snapshot available for NIFTY 50",
            "options analytics unavailable outside market hours",
            "unsubscribed underlying: RELIANCE",
            "spot unavailable for max-pain positioning",
        ]
    ),
)
_unavailable_options_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

# An "available but unrecognized alignment" entry must also be treated as
# unavailable (no fabricated outcome, R7.3).
_unrecognized_alignment_entry = st.builds(
    lambda align: {
        "available": True,
        "options_bias_state": "bullish",
        "alignment": align,
        "chain_context": "own-chain",
        "pcr_oi": 0.9,
        "max_pain": 22000.0,
        "oi_walls": {"support": 21800.0, "resistance": 22200.0},
    },
    st.one_of(st.none(), st.text(max_size=8).filter(lambda s: s not in _ALIGNMENT_OUTCOME)),
)

# Malformed / missing entries route to not-evaluable as well.
_degenerate_options_entry = st.one_of(
    st.none(),
    st.just({}),
    st.text(max_size=6),
    st.integers(),
)

_options_entry = st.one_of(
    _available_options_entry(),
    _unavailable_options_entry,
    _unrecognized_alignment_entry,
    _degenerate_options_entry,
)

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect the single options step. Crucially the record carries NO
# ``validator_checks`` so it routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_options_step(steps):
    """Return the single options step, asserting exactly one (R7.1)."""
    options_steps = [s for s in steps if s.get("check") == OPTIONS_CHECK]
    assert len(options_steps) == 1, (
        f"expected exactly one '{OPTIONS_CHECK}' step, got {len(options_steps)}"
    )
    return options_steps[0]


def _assert_outcome_matches_entry(step, entry):
    """Assert the step's outcome maps the entry per R7.2 / R7.3."""
    assert step["check"] == OPTIONS_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present
    # The outcome is always one of the four documented tokens (R7.1).
    primary = outcome.split()[0] if outcome else outcome
    assert primary in {"pass", "fail", "informational", "not-evaluable"}, (
        f"outcome {outcome!r} is not one of the four documented tokens"
    )

    alignment = entry.get("alignment") if isinstance(entry, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("available")
        and alignment in _ALIGNMENT_OUTCOME
    ):
        # ── R7.2: alignment maps to the exact outcome ────────────────────────
        expected = _ALIGNMENT_OUTCOME[alignment]
        assert outcome == expected, (
            f"alignment={alignment!r} -> outcome {outcome!r}, expected {expected!r}"
        )
    else:
        # ── R7.3: unavailable -> not-evaluable, no fabricated alignment ──────
        assert outcome.startswith("not-evaluable"), (
            f"unavailable options must report not-evaluable, got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable options outcome must carry an 'unavailable' "
            f"indication, got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents an alignment field.
        assert "alignment" not in step


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: exactly one options verification step + correct outcome mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 14: Exactly one options verification step maps alignment to outcome
@settings(max_examples=200, deadline=None)
@given(
    options=_options_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_14_options_verification_step_outcome_mapping(options, extras, action):
    """Validates: Requirements 7.1, 7.2, 7.3

    For any options entry shape (each Alignment value, an unavailable marker, an
    unrecognized alignment, or a malformed entry):

      * ``_options_step`` returns a single step under the stable check id
        ``options`` whose outcome maps alignment correctly (pass / fail /
        informational), or ``not-evaluable`` (with an 'unavailable' indication,
        no fabricated alignment) when unavailable;
      * FIND-mode derivation (``_derive_find_mode_steps``) contains EXACTLY ONE
        options step with that same outcome;
      * VERIFY-mode surfacing (``build_verification_steps`` over a record with an
        explicit ``validator_checks`` list) contains EXACTLY ONE options step
        with that same outcome.
    """
    record = dict(extras)
    record["options"] = options

    # ── Direct mapping via _options_step (R7.1-R7.3) ─────────────────────────
    direct = _options_step(record)
    _assert_outcome_matches_entry(direct, options)
    expected_outcome = direct["outcome"]

    # ── FIND mode: build_verification_steps routes here (no validator_checks) ─
    find_decision = {"action": action, "defensibility": record}
    find_steps = build_verification_steps(find_decision)
    find_step = _only_options_step(find_steps)
    assert find_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(find_step, options)

    # ── FIND mode: the raw derivation also yields exactly one options step ───
    derived_steps = _derive_find_mode_steps(record)
    derived_step = _only_options_step(derived_steps)
    assert derived_step["outcome"] == expected_outcome

    # ── VERIFY mode: an explicit validator_checks list surfaces exactly one ──
    verify_record = dict(record)
    verify_record["validator_checks"] = [
        {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
        {"check": "macro-trend-alignment", "outcome": "informational"},
    ]
    verify_decision = {"action": action, "defensibility": verify_record}
    verify_steps = build_verification_steps(verify_decision)
    verify_step = _only_options_step(verify_steps)
    assert verify_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(verify_step, options)


# Feature: options-agent-integration, Property 14: Exactly one options verification step maps alignment to outcome
def test_property_14_explicit_state_table():
    """Validates: Requirements 7.1, 7.2, 7.3

    A non-Hypothesis exhaustive check of the four mandated states (available
    aligned / misaligned / neutral, and unavailable), confirming the exact
    outcome and that the unavailable path never fabricates an alignment. Covers
    both FIND mode and VERIFY mode.
    """
    base = {
        "options_bias_state": "bullish",
        "chain_context": "own-chain",
        "pcr_oi": 0.9,
        "max_pain": 22000.0,
        "oi_walls": {"support": 21800.0, "resistance": 22200.0},
    }
    cases = [
        ({"available": True, "alignment": "aligned", **base}, "pass"),
        ({"available": True, "alignment": "misaligned", **base}, "fail"),
        ({"available": True, "alignment": "neutral", **base}, "informational"),
        ({"available": False, "reason": "no option chain snapshot"}, "not-evaluable"),
    ]

    for entry, want in cases:
        record = {"options": entry}
        step = _options_step(record)
        assert step["check"] == OPTIONS_CHECK
        if want == "not-evaluable":
            assert step["outcome"].startswith("not-evaluable")
            assert "unavailable" in step["outcome"]
            assert step["outcome"] not in _FABRICATED_OUTCOMES
            assert "alignment" not in step
        else:
            assert step["outcome"] == want

        # FIND mode: the derivation surfaces exactly one options step.
        derived = _derive_find_mode_steps(record)
        only_find = _only_options_step(derived)
        assert only_find["outcome"] == step["outcome"]

        # VERIFY mode: an explicit validator_checks list still surfaces exactly one.
        verify_record = dict(record)
        verify_record["validator_checks"] = [
            {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
        ]
        verify_steps = build_verification_steps(
            {"action": "BUY", "defensibility": verify_record}
        )
        only_verify = _only_options_step(verify_steps)
        assert only_verify["outcome"] == step["outcome"]
