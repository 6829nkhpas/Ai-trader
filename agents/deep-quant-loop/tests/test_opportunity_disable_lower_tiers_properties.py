"""Property-based test for disabling lower tiers (opportunity.py, task 2.7).

Feature: adaptive-opportunity-engine

This module implements design **Property 6: Disabling lower tiers restores
A+-only behavior**:

    For any evidence, when ``lower_tiers_enabled`` is false, ``evaluate_tier``
    returns only ``a_plus`` or ``stand_aside``.

Validates: Requirements 2.4, 11.3.

Requirement 2.4 makes the lower-tier behavior config-gated so tiers below
``a_plus`` can be disabled entirely, restoring the pre-engine A+-only policy;
Requirement 11.3 states that with the lower tiers (and heartbeat) disabled the
Deep_Quant_Agent behaves as an A+-only, cap-bounded hunter. This property proves
the binding half of that guarantee at the pure-core level: no matter how strong a
``b_continuation`` / ``scalp`` setup the evidence describes, with
``lower_tiers_enabled=False`` the ladder can only ever land on ``a_plus`` or
``stand_aside`` — never a lower tier.

The strategy fuzzes the ``evidence`` dict across the full documented input space
(pattern confidence in-band / out-of-band / boundary-heavy / non-numeric /
missing; the defensible entry/stop/target triple via explicit flag, valid
long/short brackets, degenerate same-side levels, partial, or missing; and each
of the six availability/alignment signals across favorable / unfavorable /
neutral / unavailable / garbage / absent). Crucially it OVER-SAMPLES evidence that
WOULD qualify for a lower tier had the tiers been enabled — a defensible triple
plus a mid-band confidence and one-or-two aligned signals — so the test actively
exercises the case the config gate must suppress rather than trivially landing on
stand_aside. Configs are always built with ``lower_tiers_enabled=False``.

The sys.path bootstrap and the ``@settings`` / ``@given`` convention mirror
``tests/test_opportunity_regime_session_gate_properties.py`` and the sibling
``opportunity`` property tests.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    DEFAULT_HEARTBEAT_CADENCE_SECS,
    DEFAULT_HEARTBEAT_MAX,
    DEFAULT_PRUNE_KEEP_RECENT_TURNS,
    DEFAULT_PRUNE_MAX_MESSAGES,
    DEFAULT_SESSION_MAX_TURNS,
    DEFAULT_SESSION_MAX_WALL_SECS,
    DEFAULT_WATCH_CAP,
    OPPORTUNITY_TIERS,
    OpportunityConfig,
    TierEvaluation,
    evaluate_tier,
)

# ── Strategies ────────────────────────────────────────────────────────────────

# The six confluence signals and the sub-dict key that carries each one's state.
_SIGNAL_KEYS = {
    "regime": "favorability",
    "session": "time_favorability",
    "relative_strength": "alignment",
    "forecast": "forecast_alignment",
    "macro": "alignment",
    "options": "alignment",
}

# Structural pattern confidence: in-band, out-of-band, boundary-heavy, non-numeric,
# and missing, so clamping/degradation is exercised under the config gate.
_pattern_confidence = st.one_of(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=-2.0, max_value=3.0),
    st.sampled_from(
        [0.0, 0.39, 0.40, 0.41, 0.54, 0.55, 0.56, 0.74, 0.75, 0.76, 0.9, 1.0]
    ),
    st.sampled_from([None, "0.9", float("nan"), True]),
)

_price = st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _triple(draw):
    """An entry/stop/target bundle spanning defensible and non-defensible shapes.

    A defensible triple has stop and target on OPPOSITE sides of entry. We bias
    toward defensible so the lower-tier evidence bar is frequently met (the case
    the config gate must suppress).
    """
    entry = draw(_price)
    dist = draw(st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False))
    kind = draw(
        st.sampled_from(
            ["long", "long", "short", "short", "degenerate", "partial", "missing",
             "explicit_true", "explicit_false"]
        )
    )
    if kind == "long":
        return {"entry": entry, "stop": entry - dist, "target": entry + dist}
    if kind == "short":
        return {"entry": entry, "stop": entry + dist, "target": entry - dist}
    if kind == "degenerate":
        # stop and target on the SAME side -> not a defensible bracket.
        return {"entry": entry, "stop": entry - dist, "target": entry - 2 * dist}
    if kind == "partial":
        return {"entry": entry}
    if kind == "explicit_true":
        return {"has_defensible_triple": True}
    if kind == "explicit_false":
        return {"has_defensible_triple": False}
    return {}  # missing: no triple keys at all


def _signal_sub(state_field):
    """A fuzzed alignment/favorability sub-dict for a signal keyed by ``state_field``.

    Positive tokens are over-sampled so aligned counts are frequently high enough
    to clear the lower-tier evidence bar — the scenario the config gate suppresses.
    """
    return st.one_of(
        st.none(),
        st.just({}),
        st.builds(
            lambda avail, val: {"available": avail, state_field: val},
            st.booleans(),
            st.sampled_from(
                [
                    "favorable", "aligned", "favorable", "aligned",  # over-sample positive
                    "neutral", "unfavorable", "misaligned", "", "??", None,
                ]
            ),
        ),
        st.just("not-a-dict"),
    )


@st.composite
def _evidence_strategy(draw):
    """Arbitrary evidence spanning the documented input space.

    Over-samples evidence that WOULD qualify for b_continuation / scalp had the
    lower tiers been enabled (a defensible triple + a mid/high confidence + one or
    more aligned signals), so the config gate is actively exercised rather than the
    test trivially landing on stand_aside via a weak setup.
    """
    evidence = {"pattern_confidence": draw(_pattern_confidence)}
    for name, state_field in _SIGNAL_KEYS.items():
        sub = draw(_signal_sub(state_field))
        if sub is not None:
            evidence[name] = sub
    evidence.update(draw(_triple()))
    return evidence


@st.composite
def _lower_tier_qualifying_evidence(draw):
    """Evidence CONSTRUCTED to clear a lower tier's bar when tiers are enabled.

    A defensible triple, a mid-band confidence (>= the scalp/B floor), and two
    aligned signals with a neutral (favorable-or-neutral) regime/session — so if
    lower tiers were on this would land on b_continuation or scalp. With them off,
    the property demands it lands on a_plus or stand_aside only.
    """
    entry = draw(_price)
    dist = draw(st.floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False))
    long_side = draw(st.booleans())
    if long_side:
        triple = {"entry": entry, "stop": entry - dist, "target": entry + dist}
    else:
        triple = {"entry": entry, "stop": entry + dist, "target": entry - dist}

    # Mid/high confidence clearing at least the scalp floor (often the B floor too).
    conf = draw(st.floats(min_value=0.40, max_value=1.0, allow_nan=False, allow_infinity=False))

    evidence = {
        "pattern_confidence": conf,
        # Non-unfavorable regime/session so the regime/session gate is NOT the
        # reason a lower tier would be blocked — isolating the config gate.
        "regime": {"available": True, "favorability": draw(st.sampled_from(["favorable", "neutral"]))},
        "session": {
            "available": True,
            "time_favorability": draw(st.sampled_from(["favorable", "neutral"])),
        },
        "relative_strength": {"available": True, "alignment": "aligned"},
        "forecast": {"available": True, "forecast_alignment": "aligned"},
        "macro": {"available": True, "alignment": draw(st.sampled_from(["aligned", "neutral"]))},
        "options": {"available": True, "alignment": draw(st.sampled_from(["aligned", "neutral"]))},
    }
    evidence.update(triple)
    return evidence


@st.composite
def _config_lower_tiers_off(draw):
    """A valid OpportunityConfig with ``lower_tiers_enabled=False``.

    Size factors stay in (0.0, 1.0] and ordered so the config is realistic; the
    lower-tier factors are present but must be unreachable given the disabled flag.
    """
    a_plus = draw(st.floats(min_value=0.5, max_value=1.0))
    b_cont = draw(st.floats(min_value=0.2, max_value=a_plus))
    scalp = draw(st.floats(min_value=0.05, max_value=b_cont))
    return OpportunityConfig(
        watch_cap=DEFAULT_WATCH_CAP,
        session_max_turns=DEFAULT_SESSION_MAX_TURNS,
        session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
        size_factor_a_plus=a_plus,
        size_factor_b_continuation=b_cont,
        size_factor_scalp=scalp,
        lower_tiers_enabled=False,
        heartbeat_enabled=False,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=DEFAULT_HEARTBEAT_MAX,
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )


# Draw from either the broad fuzzed space or the deliberately lower-tier-qualifying
# space, so the property covers both arbitrary evidence and the specific evidence
# the config gate must suppress.
_evidence = st.one_of(_evidence_strategy(), _lower_tier_qualifying_evidence())


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (task 2.7): Disabling lower tiers restores A+-only behavior
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 6: For any evidence, when lower_tiers_enabled is false, evaluate_tier returns only a_plus or stand_aside (never b_continuation or scalp), restoring the pre-engine A+-only policy.
@settings(max_examples=300, deadline=None)
@given(evidence=_evidence, cfg=_config_lower_tiers_off())
def test_property_6_disabling_lower_tiers_restores_a_plus_only(evidence, cfg):
    """Feature: adaptive-opportunity-engine, Property 6: Disabling lower tiers
    restores A+-only behavior — for ANY evidence, with ``lower_tiers_enabled=False``
    ``evaluate_tier`` returns only ``a_plus`` or ``stand_aside`` and never a lower
    tier, even when the evidence would otherwise qualify for b_continuation/scalp.

    Validates: Requirements 2.4, 11.3
    """
    result = evaluate_tier(evidence, cfg)

    # Shape: a total function returning a TierEvaluation over a known tier.
    assert isinstance(result, TierEvaluation)
    assert result.tier in OPPORTUNITY_TIERS

    # ── Core guarantee: only a_plus or stand_aside are reachable (R2.4 / R11.3). ─
    assert result.tier in ("a_plus", "stand_aside"), (
        f"lower_tiers_enabled=False but evaluate_tier returned {result.tier!r} "
        f"(rationale={result.rationale!r}, gated_by={result.gated_by!r}, "
        f"evidence={evidence!r})"
    )

    # A stand_aside must carry a stated, non-empty rationale and a zero size factor.
    if result.tier == "stand_aside":
        assert isinstance(result.rationale, str) and result.rationale.strip()
        assert result.size_factor == 0.0
