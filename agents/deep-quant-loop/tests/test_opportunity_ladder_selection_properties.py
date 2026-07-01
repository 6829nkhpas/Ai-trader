"""Property-based test for the tiered opportunity ladder (opportunity.py, task 2.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 1: Ladder selection picks the highest
satisfied tier, else stands aside**:

    For any evidence dict and configuration, ``evaluate_tier`` returns the
    highest-priority tier in the order ``a_plus -> b_continuation -> scalp`` whose
    criteria are met, and returns ``stand_aside`` with a non-empty rationale when
    no tier's criteria are met; whenever the ``a_plus`` criteria hold the result is
    never a lower tier. The returned tier is always one of ``OPPORTUNITY_TIERS``.

Validates: Requirements 1.1, 1.2.

The strategy fuzzes the ``evidence`` dict across the full documented input space —
structural pattern confidence (in-band, out-of-band, non-numeric, missing), the
defensible entry/stop/target triple (explicit ``has_defensible_triple`` bool,
valid long/short brackets, degenerate same-side levels, missing), and each of the
six availability/alignment signals (favorable/aligned, unfavorable/misaligned,
neutral, unavailable, garbage) — together with a configuration whose
``lower_tiers_enabled`` flag varies. Boundary confidences (0.39/0.40/0.54/0.55/
0.74/0.75) are over-sampled so the tier thresholds are exercised precisely.

Correctness is checked against an **independent reference oracle**
(``_expected_tier``) that re-derives the highest-satisfied tier straight from the
documented tier criteria — it does NOT call ``evaluate_tier`` — so the property is
a genuine check rather than a tautology. The structural invariants of the property
(result is a known tier, a_plus dominance, non-empty stand-aside rationale) are
additionally asserted directly.

The sys.path / import pattern mirrors
``tests/test_opportunity_config_resolution_properties.py`` and the sibling
deep-quant-loop property tests.
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
    A_PLUS_MIN_ALIGNED,
    A_PLUS_MIN_PATTERN_CONF,
    B_CONTINUATION_MIN_ALIGNED,
    B_CONTINUATION_MIN_PATTERN_CONF,
    OPPORTUNITY_TIERS,
    OpportunityConfig,
    SCALP_MIN_ALIGNED,
    SCALP_MIN_PATTERN_CONF,
    TierEvaluation,
    evaluate_tier,
)

# ── Independent reference oracle for the documented tier semantics ────────────
# These re-derive the tier criteria WITHOUT calling the module under test, so the
# property is a genuine check rather than a tautology. They encode the criteria
# stated in the design / task: signals favorable|aligned -> +1, unfavorable|
# misaligned -> -1, else 0; a_plus needs a defensible triple, conf >= 0.75,
# >= 3 aligned, and zero misaligned; b_continuation needs a triple, conf >= 0.55,
# >= 2 aligned (gated by regime/session + the lower-tier flag); scalp needs a
# triple, conf >= 0.40, >= 1 aligned (gated the same way); otherwise stand_aside.

_POSITIVE = frozenset({"favorable", "aligned"})
_NEGATIVE = frozenset({"unfavorable", "misaligned"})

# The (sub-dict key(s)) that carry each of the six signals' state string.
_SIGNAL_KEYS = {
    "regime": ("favorability",),
    "session": ("time_favorability", "favorability"),
    "relative_strength": ("alignment",),
    "forecast": ("forecast_alignment", "alignment"),
    "macro": ("alignment",),
    "options": ("alignment",),
}


def _is_finite_number(value) -> bool:
    """True for a real, finite int/float (bool is excluded — a bool is not a level)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _oracle_signal(sub, keys) -> int:
    """Independently normalize one signal to +1 / 0 / -1."""
    if not isinstance(sub, dict):
        return 0
    if sub.get("available") is False:
        return 0
    raw = None
    for key in keys:
        candidate = sub.get(key)
        if isinstance(candidate, str):
            raw = candidate.strip().lower()
            break
    if raw in _POSITIVE:
        return 1
    if raw in _NEGATIVE:
        return -1
    return 0


def _oracle_triple(ev) -> bool:
    """Independently decide whether a defensible entry/stop/target triple exists."""
    override = ev.get("has_defensible_triple")
    if isinstance(override, bool):
        return override
    entry = ev.get("entry")
    stop = ev.get("stop")
    target = ev.get("target")
    if not (_is_finite_number(entry) and _is_finite_number(stop) and _is_finite_number(target)):
        return False
    return (target - entry) * (entry - stop) > 0.0


def _oracle_conf(ev) -> float:
    """Independently read the clamped structural pattern confidence."""
    val = ev.get("pattern_confidence")
    if not _is_finite_number(val):
        return 0.0
    return max(0.0, min(1.0, float(val)))


def _expected_tier(ev, cfg) -> str:
    """The highest satisfied tier per the documented ladder criteria (oracle)."""
    triple = _oracle_triple(ev)
    conf = _oracle_conf(ev)
    states = {name: _oracle_signal(ev.get(name), keys) for name, keys in _SIGNAL_KEYS.items()}
    aligned = sum(1 for s in states.values() if s > 0)
    misaligned = sum(1 for s in states.values() if s < 0)
    regime_unfavorable = states["regime"] < 0
    session_unfavorable = states["session"] < 0
    lower_enabled = bool(cfg.lower_tiers_enabled)

    if (
        triple
        and conf >= A_PLUS_MIN_PATTERN_CONF
        and aligned >= A_PLUS_MIN_ALIGNED
        and misaligned == 0
    ):
        return "a_plus"

    b_bar = triple and conf >= B_CONTINUATION_MIN_PATTERN_CONF and aligned >= B_CONTINUATION_MIN_ALIGNED
    scalp_bar = triple and conf >= SCALP_MIN_PATTERN_CONF and aligned >= SCALP_MIN_ALIGNED

    if b_bar or scalp_bar:
        if not lower_enabled:
            return "stand_aside"
        if regime_unfavorable or session_unfavorable:
            return "stand_aside"
        if b_bar:
            return "b_continuation"
        return "scalp"

    return "stand_aside"


# ── Generators over the documented evidence input space ───────────────────────

# Confidence: in-band, out-of-band, boundary-heavy, non-numeric, and missing.
_conf_strategy = st.one_of(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=-1.0, max_value=2.0),
    st.sampled_from(
        [0.0, 0.39, 0.40, 0.41, 0.54, 0.55, 0.56, 0.74, 0.75, 0.76, 0.9, 1.0, -0.2, 1.5]
    ),
    st.none(),
    st.text(max_size=3),
)


@st.composite
def _triple_fragment(draw):
    """A fragment contributing the defensible-triple part of the evidence dict."""
    mode = draw(
        st.sampled_from(["explicit", "long", "short", "same_side", "missing", "partial"])
    )
    if mode == "explicit":
        return {"has_defensible_triple": draw(st.booleans())}
    if mode == "long":
        entry = draw(st.floats(min_value=10.0, max_value=1000.0))
        stop = entry - draw(st.floats(min_value=0.5, max_value=50.0))
        target = entry + draw(st.floats(min_value=0.5, max_value=50.0))
        return {"entry": entry, "stop": stop, "target": target}
    if mode == "short":
        entry = draw(st.floats(min_value=10.0, max_value=1000.0))
        stop = entry + draw(st.floats(min_value=0.5, max_value=50.0))
        target = entry - draw(st.floats(min_value=0.5, max_value=50.0))
        return {"entry": entry, "stop": stop, "target": target}
    if mode == "same_side":
        # stop and target on the SAME side of entry -> not a defensible bracket.
        entry = draw(st.floats(min_value=10.0, max_value=1000.0))
        stop = entry + draw(st.floats(min_value=1.0, max_value=20.0))
        target = entry + draw(st.floats(min_value=1.0, max_value=20.0))
        return {"entry": entry, "stop": stop, "target": target}
    if mode == "partial":
        # Only some of the levels present -> not a complete triple.
        return draw(
            st.fixed_dictionaries(
                {"entry": st.one_of(st.none(), st.floats(min_value=10.0, max_value=100.0))}
            )
        )
    return {}  # missing: no triple keys at all


def _signal_value_strategy():
    """A signal state string spanning positive / negative / neutral / garbage."""
    return st.sampled_from(
        [
            "favorable",
            "aligned",
            "unfavorable",
            "misaligned",
            "neutral",
            "FAVORABLE",
            " aligned ",
            "",
            "sideways",
            "unknown",
        ]
    )


@st.composite
def _signal_fragment(draw, name, keys):
    """A sub-dict for one of the six confluence signals (or its absence)."""
    mode = draw(st.sampled_from(["present", "unavailable", "empty", "absent"]))
    if mode == "absent":
        return None
    if mode == "empty":
        return {}
    key = keys[0]
    if mode == "unavailable":
        return {"available": False, key: draw(_signal_value_strategy())}
    return {"available": True, key: draw(_signal_value_strategy())}


@st.composite
def _evidence_strategy(draw):
    """A full evidence dict spanning the documented input space."""
    ev = {}
    ev.update(draw(_triple_fragment()))
    ev["pattern_confidence"] = draw(_conf_strategy)
    for name, keys in _SIGNAL_KEYS.items():
        sub = draw(_signal_fragment(name, keys))
        if sub is not None:
            ev[name] = sub
    return ev


_config_strategy = st.builds(
    OpportunityConfig,
    watch_cap=st.just(3),
    session_max_turns=st.just(40),
    session_max_wall_secs=st.just(3600.0),
    size_factor_a_plus=st.floats(min_value=0.5, max_value=1.0),
    size_factor_b_continuation=st.floats(min_value=0.1, max_value=0.9),
    size_factor_scalp=st.floats(min_value=0.05, max_value=0.5),
    lower_tiers_enabled=st.booleans(),
    heartbeat_enabled=st.just(False),
    heartbeat_cadence_secs=st.just(300.0),
    heartbeat_max=st.just(6),
    prune_keep_recent_turns=st.just(8),
    prune_max_messages=st.just(40),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 2.2): Ladder selection picks the highest satisfied tier, else
# stands aside
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 1: For any evidence dict and configuration, evaluate_tier returns the highest-priority tier in the order a_plus -> b_continuation -> scalp whose criteria are met, returns stand_aside with a non-empty rationale when no tier's criteria are met, never returns a lower tier when the a_plus criteria hold, and always returns a tier in OPPORTUNITY_TIERS.
@settings(max_examples=300, deadline=None)
@given(evidence=_evidence_strategy(), cfg=_config_strategy)
def test_property_1_ladder_selection_highest_satisfied_else_stand_aside(evidence, cfg):
    """Feature: adaptive-opportunity-engine, Property 1: Ladder selection picks the
    highest satisfied tier, else stands aside — ``evaluate_tier`` returns the
    highest-priority satisfied tier in order a_plus -> b_continuation -> scalp,
    returns stand_aside with a non-empty rationale when no tier's criteria are met,
    never drops below a_plus when the a_plus criteria hold, and always returns a
    known tier.

    Validates: Requirements 1.1, 1.2
    """
    result = evaluate_tier(evidence, cfg)

    # ── Shape: a total function returning a TierEvaluation over a known tier. ──
    assert isinstance(result, TierEvaluation)
    assert result.tier in OPPORTUNITY_TIERS

    expected = _expected_tier(evidence, cfg)

    # ── Highest satisfied tier (R1.1) / stand_aside when none (R1.2). ─────────
    assert result.tier == expected, (
        f"evaluate_tier returned {result.tier!r} but the highest satisfied tier is "
        f"{expected!r} for evidence={evidence!r}, lower_tiers_enabled="
        f"{cfg.lower_tiers_enabled}"
    )

    # ── a_plus dominance: when the a_plus criteria hold the result is never a
    # lower tier (R1.1). Checked independently of the equality above. ──────────
    if expected == "a_plus":
        assert result.tier == "a_plus"

    # ── stand_aside always carries a stated, non-empty rationale (R1.2). ──────
    assert isinstance(result.rationale, str)
    if result.tier == "stand_aside":
        assert result.rationale.strip(), "stand_aside must carry a non-empty rationale"
