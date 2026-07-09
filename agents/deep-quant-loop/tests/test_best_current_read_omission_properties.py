"""R4 verification property test — no defensible level yields an omitted field.

Feature: deep-quant-runtime-hardening

Property 7 (verification), Python ``graph._parse_levels_from_text`` +
``opportunity.best_current_read`` seams — "Trustworthy Best-Current-Read key
levels":

    (a) Over rule-text / ordinal-only prose that carries NO defensible price
        (e.g. "stop >= 1.5x ATR", "Target 1: reassess"),
        ``graph._parse_levels_from_text`` returns ``None`` or a subset that
        OMITS the undefensible field — it never surfaces the ATR multiplier
        (``1.5``) as a stop-loss nor the target ordinal (``1``) as a take-profit
        (design Property 5 / 6 feed this).

    (b) ``opportunity.best_current_read`` PREFERS the structured
        ``evidence["reference_levels"]`` (support/resistance, VWAP, value-area,
        or the registered watch levels) when present — surfacing only their
        finite values — and does not fall back to the prose entry/stop/target
        triple in that case.

    (c) A field with no defensible price is OMITTED, never filled with a
        spurious value: non-finite reference values are dropped, and
        prose-sourced ``entry``/``stop``/``target`` are NOT surfaced when
        ``evidence["levels_structural"]`` is ``False`` (they are only surfaced
        when finite AND structurally sourced — ``levels_structural`` truthy or
        absent, which defaults to ``True``).

    Validates: Requirements 4.1, 4.4.

This is a dedicated VERIFICATION test — it encodes the FIXED behavior and must
PASS against the current (post-task-11.1/11.2) code. The sys.path / import
bootstrap and the ``@settings`` / ``@given`` convention mirror the sibling
``tests/test_level_extractor_bug_properties.py`` and
``tests/test_opportunity_best_current_read_properties.py`` modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` / ``import opportunity`` resolve exactly as every sibling test
# module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import _parse_levels_from_text  # noqa: E402
from opportunity import TierEvaluation, best_current_read  # noqa: E402


def _finite(x) -> bool:
    """Mirror ``opportunity._is_finite_number``: a real, finite non-bool number."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ── Strategies ────────────────────────────────────────────────────────────────

# ATR multipliers / target ordinals as they appear in rule-text prose.
_multiplier = st.sampled_from([1.0, 1.5, 2.0, 2.5, 3.0])
_mult_token = st.sampled_from(["x", "X", "\u00d7"])
_ordinal = st.integers(min_value=1, max_value=12)

# A finite number or one of the non-defensible sentinels (None / string / NaN /
# inf / bool) that must always be dropped.
_level = st.one_of(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.sampled_from([None, "abc", float("nan"), float("inf"), True, False]),
)

_ref_key = st.sampled_from(
    [
        "support",
        "resistance",
        "vwap",
        "value_area_high",
        "value_area_low",
        "price_level",
        "invalidation_level",
    ]
)
_reference_levels = st.dictionaries(_ref_key, _level, max_size=6)

_signal = st.fixed_dictionaries(
    {
        "available": st.booleans(),
        "favorability": st.sampled_from(["favorable", "unfavorable", "neutral"]),
        "alignment": st.sampled_from(["aligned", "misaligned", "neutral"]),
    }
)

_tier_evals = st.one_of(
    st.none(),
    st.builds(
        TierEvaluation,
        tier=st.sampled_from(["a_plus", "b_continuation", "scalp", "stand_aside"]),
        size_factor=st.floats(min_value=0.0, max_value=1.0),
        rationale=st.text(max_size=40),
        gated_by=st.sampled_from([None, "regime", "session", "evidence-bar", "config"]),
    ),
)


@st.composite
def _rule_ordinal_prose(draw):
    """Build free-form prose containing ONLY ATR multipliers and target ordinals —
    i.e. rule-text with no defensible price anywhere."""
    fragments = []
    if draw(st.booleans()):
        m = draw(_multiplier)
        tok = draw(_mult_token)
        fragments.append(f"Rule: stop >= {m:g}{tok} ATR below structure.")
    if draw(st.booleans()):
        m = draw(_multiplier)
        tok = draw(_mult_token)
        fragments.append(f"Entry only on a {m:g}{tok} ATR expansion.")
    if draw(st.booleans()):
        n = draw(_ordinal)
        fragments.append(f"Target {n}: reassess on a clean break.")
    if not fragments:
        # Guarantee at least one rule fragment so the prose is never empty.
        n = draw(_ordinal)
        fragments.append(f"Standing aside. Target {n}: reassess.")
    lead = draw(st.sampled_from(["Standing aside — chop, no edge. ", "No trade. ", ""]))
    return lead + " ".join(fragments)


@st.composite
def _evidences(draw):
    """A (possibly partial / malformed) evidence dict shaped like the one
    ``_evidence_for_tier`` assembles for ``best_current_read``."""
    ev = {}
    if draw(st.booleans()):
        ev["reference_levels"] = draw(_reference_levels)
    for key in ("entry", "stop", "target"):
        if draw(st.booleans()):
            ev[key] = draw(_level)
    if draw(st.booleans()):
        ev["levels_structural"] = draw(st.booleans())
    for key in ("regime", "session", "relative_strength", "forecast", "macro", "options"):
        if draw(st.booleans()):
            ev[key] = draw(_signal)
    return ev


# ═════════════════════════════════════════════════════════════════════════════
# Property 7, facet (a) — rule/ordinal prose yields no spurious level
# ═════════════════════════════════════════════════════════════════════════════

# Feature: deep-quant-runtime-hardening, Property 7: rule-text/ordinal-only prose (no defensible price) yields None or a subset omitting the undefensible field — never a spurious multiplier/ordinal value.
@settings(max_examples=200, deadline=None)
@given(text=_rule_ordinal_prose())
def test_property_7_prose_without_defensible_price_omits_the_field(text):
    """Over prose whose only numbers are ATR multipliers / target ordinals,
    ``_parse_levels_from_text`` surfaces NO level: it returns ``None`` (or an
    empty subset), never capturing the multiplier as ``stop_loss``/``entry`` nor
    the ordinal as ``take_profit``.

    Validates: Requirements 4.2, 4.3 (feeding 4.1, 4.4)
    """
    levels = _parse_levels_from_text(text)

    # No defensible price is present, so every field must be omitted.
    assert levels is None or levels == {}, (
        f"prose {text!r} surfaced a spurious level {levels!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Property 7, facet (b) — structured reference_levels are preferred
# ═════════════════════════════════════════════════════════════════════════════

# Feature: deep-quant-runtime-hardening, Property 7: best_current_read prefers structured reference_levels (finite values only) and does not fall back to the prose entry/stop/target triple when any finite reference level is present.
@settings(max_examples=200, deadline=None)
@given(evidence=_evidences(), tier_eval=_tier_evals)
def test_property_7_prefers_structured_reference_levels(evidence, tier_eval):
    """When ``evidence["reference_levels"]`` carries at least one finite value,
    ``best_current_read``'s ``levels`` are exactly the finite subset of
    ``reference_levels`` — the entry/stop/target fallback is NOT used.

    Validates: Requirements 4.1, 4.4
    """
    reference = evidence.get("reference_levels")
    if not isinstance(reference, dict):
        return  # only this facet's precondition
    expected_ref = {k: float(v) for k, v in reference.items() if _finite(v)}
    if not expected_ref:
        return  # covered by the omission facet below

    read = best_current_read(evidence, tier_eval)

    assert read["levels"] == expected_ref, (
        "structured reference_levels not preferred (or a non-finite value leaked / "
        "a prose fallback level was surfaced)"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Property 7, facet (c) — undefensible fields omitted; structural gating
# ═════════════════════════════════════════════════════════════════════════════

# Feature: deep-quant-runtime-hardening, Property 7: a field with no defensible price is omitted — non-finite reference values are dropped, and prose-sourced entry/stop/target are surfaced only when finite AND structurally sourced (levels_structural truthy/absent).
@settings(max_examples=300, deadline=None)
@given(evidence=_evidences(), tier_eval=_tier_evals)
def test_property_7_undefensible_fields_are_omitted(evidence, tier_eval):
    """Every surfaced level is a finite number that appeared in the evidence, and
    no undefensible field is ever filled:

      * non-finite ``reference_levels`` values are dropped;
      * when NO finite reference level exists, the entry/stop/target triple is
        surfaced ONLY when finite AND ``levels_structural`` is truthy/absent
        (default ``True``); when ``levels_structural`` is ``False`` the
        prose-sourced triple is omitted entirely.

    Validates: Requirements 4.1, 4.4
    """
    read = best_current_read(evidence, tier_eval)
    levels = read["levels"]
    assert isinstance(levels, dict)

    reference = evidence.get("reference_levels")
    finite_ref = (
        {k: float(v) for k, v in reference.items() if _finite(v)}
        if isinstance(reference, dict)
        else {}
    )

    if finite_ref:
        # reference levels win — exactly the finite subset, nothing fabricated.
        assert levels == finite_ref
    else:
        structural = evidence.get("levels_structural", True)
        if structural:
            expected = {
                key: float(evidence[key])
                for key in ("entry", "stop", "target")
                if _finite(evidence.get(key))
            }
            assert levels == expected
        else:
            # prose-sourced triple is not structurally sourced → omitted entirely.
            assert levels == {}


# ── Concrete integration counterexample (documents the R4 fix end-to-end) ──────


def test_concrete_hold_prose_prefers_structured_levels_over_rule_text():
    """A stand-aside HOLD whose prose is rule/ordinal text ("stop >= 1.5x ATR",
    "Target 1: reassess") must NOT surface ``1.5`` / ``1`` as levels, and when
    structured ``reference_levels`` are present ``best_current_read`` reports
    those defensible S/R prices instead.
    """
    prose = (
        "Standing aside — chop, no edge. Rule: stop >= 1.5x ATR below structure. "
        "Target 1: reassess on a clean break above value."
    )
    assert _parse_levels_from_text(prose) in (None, {})

    evidence = {
        "reference_levels": {"support": 24180.0, "resistance": 24420.0, "vwap": 24305.0},
        # prose-sourced triple that must be ignored in favor of the structured levels
        "entry": 24310.0,
        "stop": 24000.0,
        "target": 24600.0,
        "levels_structural": False,
    }
    read = best_current_read(evidence, None)
    assert read["levels"] == {"support": 24180.0, "resistance": 24420.0, "vwap": 24305.0}
    assert "entry" not in read["levels"]

    # With no structured levels AND a non-structural (prose) triple, nothing is
    # surfaced — the undefensible fields are omitted.
    read_prose_only = best_current_read(
        {"entry": 24310.0, "stop": 24000.0, "target": 24600.0, "levels_structural": False},
        None,
    )
    assert read_prose_only["levels"] == {}
