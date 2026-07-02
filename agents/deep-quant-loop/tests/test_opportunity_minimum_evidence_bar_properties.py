"""Property-based test for the lower-tier minimum evidence bar (opportunity.py, task 2.6).

Feature: adaptive-opportunity-engine

This module implements design **Property 5: Lower tiers require a minimum evidence
bar**:

    For any evidence lacking a complete defensible entry/stop/target triple,
    ``evaluate_tier`` never returns ``b_continuation`` or ``scalp`` (it falls
    through to ``stand_aside``).

Validates: Requirements 2.2.

The overtrading guardrail (Requirement 2.2) requires a lower tier to still satisfy
a minimum evidence bar — a defensible entry, stop, and target — rather than trading
on the mere absence of a better setup. So even with a favorable regime and session
and lower tiers enabled, a setup with no defensible entry/stop/target triple must
NOT be taken at ``b_continuation`` or ``scalp``.

The defensible-triple rule (from task 2.1) is: an explicit ``has_defensible_triple``
bool, when present, is authoritative; otherwise entry/stop/target must all be finite
numbers AND ``(target - entry) * (entry - stop) > 0`` (stop and target on OPPOSITE
sides of entry). This strategy generates arbitrary, richly-varied evidence (pattern
confidence across and beyond [0,1], all six alignment signals fuzzed) but FORCES the
no-defensible-triple condition every way it can arise:

  * missing — no triple keys at all,
  * partial — only some of entry/stop/target present (or some non-finite),
  * degenerate — all three present but stop and target on the SAME side of entry,
  * explicit_false — ``has_defensible_triple=False`` overriding any levels.

Regime and session are held favorable and ``lower_tiers_enabled=True`` so that the
ONLY thing that can forbid the lower tiers is the missing evidence bar under test —
not the regime/session gate (Property 4) and not the config gate (Property 6). An
independent oracle (``_oracle_triple``) re-derives the defensibility rule WITHOUT
calling the module under test, and every generated example is asserted to genuinely
lack a defensible triple, so the property is a real check rather than a tautology.

The sys.path bootstrap and the ``@settings``/``@given`` convention mirror
``tests/test_opportunity_ladder_selection_properties.py`` and
``tests/test_opportunity_regime_session_gate_properties.py``.
"""

import math
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


# ── Independent reference oracle for the defensible-triple rule ───────────────
# Re-derives defensibility WITHOUT calling the module under test, so the "no
# defensible triple" precondition of every generated example is verified
# independently and the property stays a genuine check.

def _is_finite_number(value) -> bool:
    """True for a real, finite int/float (bool excluded — a bool is not a level)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _oracle_triple(ev) -> bool:
    """Independently decide whether a defensible entry/stop/target triple exists."""
    override = ev.get("has_defensible_triple")
    if isinstance(override, bool):
        return override
    entry = ev.get("entry")
    stop = ev.get("stop")
    target = ev.get("target")
    if not (
        _is_finite_number(entry)
        and _is_finite_number(stop)
        and _is_finite_number(target)
    ):
        return False
    return (target - entry) * (entry - stop) > 0.0


# ── Strategies ────────────────────────────────────────────────────────────────

# Structural pattern confidence: mostly in [0,1] but deliberately including
# out-of-band and non-numeric values so the module's clamping/degradation is
# exercised alongside the missing evidence bar. Boundary values are over-sampled
# so a high confidence cannot mask a missing triple.
_pattern_confidence = st.one_of(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=-2.0, max_value=3.0),
    st.sampled_from([0.40, 0.55, 0.75, 0.9, 1.0, None, "0.9", float("nan"), True]),
)

_price = st.floats(min_value=1.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _no_triple_fragment(draw):
    """A triple fragment that is NEVER a defensible entry/stop/target bracket.

    Covers every documented way the evidence bar can be missing: no levels at all,
    partial levels (missing or non-finite), degenerate same-side levels, and an
    explicit ``has_defensible_triple=False`` override.
    """
    mode = draw(
        st.sampled_from(
            ["missing", "partial", "degenerate_long", "degenerate_short", "explicit_false"]
        )
    )
    if mode == "missing":
        # No triple keys at all.
        return {}
    if mode == "partial":
        # Only some of the levels present, or some non-finite -> not a complete triple.
        entry = draw(st.one_of(st.none(), _price, st.just(float("nan")), st.just(float("inf"))))
        keys = draw(st.sampled_from([("entry",), ("entry", "stop"), ("stop", "target"), ("target",)]))
        frag = {}
        if "entry" in keys:
            frag["entry"] = entry
        if "stop" in keys:
            frag["stop"] = draw(st.one_of(st.none(), _price))
        if "target" in keys:
            frag["target"] = draw(st.one_of(st.none(), _price))
        return frag
    if mode == "degenerate_long":
        # stop and target on the SAME side (both below entry) -> not defensible.
        entry = draw(_price)
        d1 = draw(st.floats(min_value=0.5, max_value=200.0, allow_nan=False, allow_infinity=False))
        d2 = draw(st.floats(min_value=0.5, max_value=200.0, allow_nan=False, allow_infinity=False))
        return {"entry": entry, "stop": entry - d1, "target": entry - d2}
    if mode == "degenerate_short":
        # stop and target on the SAME side (both above entry) -> not defensible.
        entry = draw(_price)
        d1 = draw(st.floats(min_value=0.5, max_value=200.0, allow_nan=False, allow_infinity=False))
        d2 = draw(st.floats(min_value=0.5, max_value=200.0, allow_nan=False, allow_infinity=False))
        return {"entry": entry, "stop": entry + d1, "target": entry + d2}
    # explicit_false: the override wins even if the (otherwise valid) levels are present.
    entry = draw(_price)
    dist = draw(st.floats(min_value=0.5, max_value=200.0, allow_nan=False, allow_infinity=False))
    return {
        "has_defensible_triple": False,
        "entry": entry,
        "stop": entry - dist,
        "target": entry + dist,
    }


def _alignment_sub(state_field):
    """A fuzzed alignment sub-dict for a non-gate signal keyed by ``state_field``.

    Mixes availability true/false with positive/neutral/negative/garbage states,
    plus occasional malformed shapes, so the four non-gate confluence signals range
    over their whole readable space (and can push the aligned count high enough to
    clear a lower-tier confidence bar — the missing triple must still forbid it).
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


@st.composite
def _evidence_without_triple(draw):
    """Arbitrary evidence lacking a defensible triple, with a FAVORABLE gate.

    Regime and session are held favorable so the regime/session gate (Property 4)
    cannot be what forbids the lower tiers — only the missing evidence bar under
    test can. The four non-gate signals and the pattern confidence are fuzzed so a
    lower tier's confidence/alignment bar is frequently otherwise clearable, making
    the missing triple the sole reason a lower tier is refused.
    """
    evidence = {
        "pattern_confidence": draw(_pattern_confidence),
        "regime": {"available": True, "favorability": "favorable"},
        "session": {"available": True, "time_favorability": "favorable"},
        "relative_strength": draw(_alignment_sub("alignment")),
        "forecast": draw(_alignment_sub("forecast_alignment")),
        "macro": draw(_alignment_sub("alignment")),
        "options": draw(_alignment_sub("alignment")),
    }
    evidence.update(draw(_no_triple_fragment()))
    return evidence


@st.composite
def _config_lower_tiers_on(draw):
    """A valid OpportunityConfig with ``lower_tiers_enabled=True``.

    Lower tiers are ON so the config gate (Property 6) cannot be the reason a lower
    tier is forbidden — the missing evidence bar under test is the only thing that
    can. Size factors stay in (0.0, 1.0] and ordered so the config is realistic.
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
# Property 5 (task 2.6): Lower tiers require a minimum evidence bar
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 5: For any evidence lacking a complete defensible entry/stop/target triple, evaluate_tier never returns b_continuation or scalp (it falls through to stand_aside).
@settings(max_examples=200, deadline=None)
@given(evidence=_evidence_without_triple(), cfg=_config_lower_tiers_on())
def test_property_5_lower_tiers_require_minimum_evidence_bar(evidence, cfg):
    """Feature: adaptive-opportunity-engine, Property 5: Lower tiers require a
    minimum evidence bar — for any evidence lacking a complete defensible
    entry/stop/target triple, ``evaluate_tier`` never returns ``b_continuation`` or
    ``scalp`` (it falls through to ``stand_aside``), even with a favorable regime and
    session and lower tiers config-enabled.

    Validates: Requirements 2.2
    """
    # ── Precondition (verified independently): this evidence has NO defensible
    # triple, so the lower-tier evidence bar is genuinely unmet. ────────────────
    assert not _oracle_triple(evidence), (
        f"generator precondition violated: evidence unexpectedly HAS a defensible "
        f"triple: {evidence!r}"
    )

    result = evaluate_tier(evidence, cfg)

    # Result shape is always a valid TierEvaluation over a known tier.
    assert isinstance(result, TierEvaluation)

    # ── Core guarantee: no lower tier without a defensible triple (R2.2). ───────
    assert result.tier not in ("b_continuation", "scalp"), (
        f"missing defensible triple but evaluate_tier returned lower tier "
        f"{result.tier!r} (rationale={result.rationale!r}, gated_by={result.gated_by!r}) "
        f"for evidence={evidence!r}"
    )

    # Without a defensible triple, a_plus is also unreachable (it requires the
    # triple too), so the ONLY reachable tier is stand_aside.
    assert result.tier == "stand_aside", (
        f"missing defensible triple should force stand_aside, got {result.tier!r}"
    )

    # stand_aside must carry a stated (non-empty) rationale (Requirement 1.2) and a
    # zero size factor.
    assert isinstance(result.rationale, str) and result.rationale.strip()
    assert result.size_factor == 0.0
