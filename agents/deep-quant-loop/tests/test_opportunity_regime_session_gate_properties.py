"""Property-based test for the regime/session lower-tier gate (opportunity.py, task 2.5).

Feature: adaptive-opportunity-engine

This module implements design **Property 4: Unfavorable regime or session forbids
lower tiers**:

    For any evidence in which the regime favorability is unfavorable OR the session
    time-favorability is unfavorable, ``evaluate_tier`` never returns
    ``b_continuation`` or ``scalp``.

Validates: Requirements 2.1.

The overtrading guardrail (Requirement 2.1) keeps the lower tiers gated by the
regime and session filters, so loosening the A+-only rule does not turn into
overtrading in chop or dead-zone windows: while the regime favorability is
unfavorable OR the session time-favorability is unfavorable, the agent must NOT
take a ``scalp`` or ``b_continuation`` trade and must prefer ``stand_aside``.

The strategy generates arbitrary, richly-varied evidence dicts (pattern
confidence across and beyond [0,1], a frequently-defensible entry/stop/target
triple, and each of the six alignment signals fuzzed across
available/unavailable × positive/neutral/negative/garbage) and then FORCES at
least one of the two negative gate signals — regime ``unfavorable`` OR session
``unfavorable`` — in the branch under test. Configs are built with
``lower_tiers_enabled=True`` so the gate under test (regime/session), not the
config gate, is what forbids the lower tiers. An additional A+ exclusion check
confirms that an unfavorable gate signal (a NEGATIVE signal, so ``misaligned > 0``)
also structurally excludes ``a_plus``, leaving only ``stand_aside`` reachable.

The sys.path bootstrap and the ``@settings``/``@given`` convention mirror
``tests/test_opportunity_config_resolution_properties.py`` and the sibling
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
    OpportunityConfig,
    TierEvaluation,
    evaluate_tier,
)

# ── Strategies ────────────────────────────────────────────────────────────────

# Structural pattern confidence: mostly in [0,1] but deliberately including
# out-of-band and non-numeric values so the module's clamping/degradation is
# exercised under the gate.
_pattern_confidence = st.one_of(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=-2.0, max_value=3.0),
    st.sampled_from([None, "0.9", float("nan"), True]),
)

# A finite price used to build a defensible (or non-defensible) triple.
_price = st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _triple(draw):
    """An entry/stop/target bundle that is frequently (but not always) defensible.

    A defensible triple has stop and target on OPPOSITE sides of entry. We bias
    toward defensible so the lower-tier evidence bar is often met and the gate is
    the only thing that can forbid the lower tiers.
    """
    entry = draw(_price)
    dist = draw(st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False))
    kind = draw(st.sampled_from(["long", "short", "degenerate", "explicit_true", "explicit_false"]))
    if kind == "long":
        return {"entry": entry, "stop": entry - dist, "target": entry + dist}
    if kind == "short":
        return {"entry": entry, "stop": entry + dist, "target": entry - dist}
    if kind == "degenerate":
        # stop and target on the SAME side -> not a defensible bracket
        return {"entry": entry, "stop": entry - dist, "target": entry - 2 * dist}
    if kind == "explicit_true":
        return {"has_defensible_triple": True}
    return {"has_defensible_triple": False}


def _alignment_sub(state_field):
    """A fuzzed alignment sub-dict for a signal keyed by ``state_field``.

    Mixes availability true/false with positive/neutral/negative/garbage states,
    plus occasional malformed shapes, so the six confluence signals range over
    their whole readable space.
    """
    return st.one_of(
        st.none(),
        st.just({}),
        st.builds(
            lambda avail, val: {"available": avail, state_field: val},
            st.booleans(),
            st.sampled_from(
                ["favorable", "aligned", "neutral", "unfavorable", "misaligned", "", "??", None]
            ),
        ),
        st.just("not-a-dict"),
    )


# Favorability tokens for the gate signals when they are NOT forced unfavorable.
_regime_fav = st.sampled_from(["favorable", "neutral", "unfavorable", "", None])
_session_fav = st.sampled_from(["favorable", "neutral", "unfavorable", "", None])


@st.composite
def _evidence_with_forced_gate(draw):
    """Arbitrary evidence in which regime OR session (or both) is unfavorable.

    We pick which gate signal(s) to force unfavorable — regime only, session only,
    or both — and leave the other gate signal free to be anything (favorable /
    neutral / unfavorable / absent / garbage). This exercises the disjunction in
    Property 4 across all three ways the antecedent can hold.
    """
    which = draw(st.sampled_from(["regime", "session", "both"]))

    if which in ("regime", "both"):
        regime = {"available": True, "favorability": "unfavorable"}
    else:
        regime = {"available": True, "favorability": draw(_regime_fav)}

    if which in ("session", "both"):
        session = {"available": True, "time_favorability": "unfavorable"}
    else:
        session = {"available": True, "time_favorability": draw(_session_fav)}

    evidence = {
        "pattern_confidence": draw(_pattern_confidence),
        "regime": regime,
        "session": session,
        "relative_strength": draw(_alignment_sub("alignment")),
        "forecast": draw(_alignment_sub("forecast_alignment")),
        "macro": draw(_alignment_sub("alignment")),
        "options": draw(_alignment_sub("alignment")),
    }
    evidence.update(draw(_triple()))
    return evidence, which


@st.composite
def _config_lower_tiers_on(draw):
    """A valid OpportunityConfig with ``lower_tiers_enabled=True``.

    Lower tiers are ON so the config gate cannot be the reason a lower tier is
    forbidden — the regime/session gate under test is the only thing that can.
    Size factors stay in (0.0, 1.0] and ordered so the config is realistic.
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
        lower_tiers_enabled=True,
        heartbeat_enabled=False,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=DEFAULT_HEARTBEAT_MAX,
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 4 (task 2.5): Unfavorable regime or session forbids lower tiers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 4: For any evidence in which the regime favorability is unfavorable OR the session time-favorability is unfavorable, evaluate_tier never returns b_continuation or scalp.
@settings(max_examples=200, deadline=None)
@given(bundle=_evidence_with_forced_gate(), cfg=_config_lower_tiers_on())
def test_property_4_unfavorable_regime_or_session_forbids_lower_tiers(bundle, cfg):
    """Feature: adaptive-opportunity-engine, Property 4: Unfavorable regime or
    session forbids lower tiers — for any evidence in which regime favorability is
    unfavorable OR session time-favorability is unfavorable, ``evaluate_tier`` never
    returns ``b_continuation`` or ``scalp`` (with lower tiers config-enabled, so the
    regime/session gate is what forbids them).

    Validates: Requirements 2.1
    """
    evidence, which = bundle

    result = evaluate_tier(evidence, cfg)

    # Result shape is always a valid TierEvaluation.
    assert isinstance(result, TierEvaluation)

    # ── Core guarantee: no lower tier while regime/session is unfavorable. ──────
    assert result.tier not in ("b_continuation", "scalp"), (
        f"gate '{which}' unfavorable but evaluate_tier returned lower tier "
        f"{result.tier!r} (rationale={result.rationale!r}, gated_by={result.gated_by!r})"
    )

    # An unfavorable gate signal is a NEGATIVE confluence signal (misaligned > 0),
    # which also excludes a_plus (whose criteria require misaligned == 0). So the
    # ONLY reachable tier under this antecedent is stand_aside.
    assert result.tier == "stand_aside", (
        f"gate '{which}' unfavorable should force stand_aside, got {result.tier!r}"
    )

    # stand_aside must carry a stated (non-empty) rationale (Requirement 1.2).
    assert isinstance(result.rationale, str) and result.rationale.strip()
    # A stand_aside has zero size factor.
    assert result.size_factor == 0.0
