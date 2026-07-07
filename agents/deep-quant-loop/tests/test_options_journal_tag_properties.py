"""Property-based test for the journal options setup-fingerprint tag.

Feature: options-agent-integration (journal.py, task 11.2)

This module implements design **Property 16: The journal options tag is a single
fixed-position value from a bounded enumeration**:

    For any committed decision, ``journal.derive_setup_tags`` appends EXACTLY ONE
    ``opt:<value>`` tag at a fixed position such that the resulting ``setup_key``
    is deterministic, where ``<value>`` is drawn from ``journal.OPT_TAG_VALUES``
    (a fixed enumeration of at most 8 values including ``unknown``), collapsing
    (``Options_Bias_State`` x ``Alignment``); a decision with no usable options
    entry, an empty value, or a value outside the enumeration yields
    ``opt:unknown``.

Validates: Requirements 8.1, 8.2, 8.3.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags(decision)`` — builds the coarse setup fingerprint and
    appends the options tag at the FINAL fixed position (after the ``db:`` tag).
  - ``_options_tag(decision)`` — collapses (Options_Bias_State x Alignment) into
    one fixed enumeration value (a directional bias pairs with its
    aligned/misaligned Alignment; a neutral bias collapses to ``neutral``;
    everything else collapses to ``unknown``).
  - ``setup_key_from_tags(tags)`` — joins the tags into the deterministic key.
  - ``OPT_TAG_VALUES`` — the fixed bounded enumeration
    (bullish-aligned / bullish-misaligned / bearish-aligned / bearish-misaligned
    / neutral / unknown).

The sys.path / import pattern mirrors the sibling property tests in this
directory (``test_debate_tag_properties.py`` /
``test_forecast_journal_tag_properties.py``): the service directory (one level
up, where ``journal.py`` lives) is prepended to ``sys.path`` so ``journal`` is
importable when pytest is run from anywhere. Importing ``journal`` is safe: it
imports ``trade_manager`` and ``sqlite3`` only, performs no network at import,
and ``derive_setup_tags`` / ``_options_tag`` are pure (no DB write).
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from journal import (  # noqa: E402
    OPT_TAG_VALUES,
    _options_tag,
    derive_setup_tags,
    setup_key_from_tags,
)

# The recognized Options_Bias_State and Alignment values (everything else maps to
# ``unknown``). Kept local so the test asserts the contract independently of
# journal's private ``_OPT_BIAS_STATES`` / ``_OPT_ALIGNMENTS`` sets.
_BIAS_STATES = ["bullish", "bearish", "neutral"]
_ALIGNMENTS = ["aligned", "misaligned", "neutral"]


def _expected_opt_value(options_entry) -> str:
    """The contract-expected opt value, computed independently of journal internals.

    Mirrors Property 16's mapping: a directional bias (bullish/bearish) pairs
    with its aligned/misaligned Alignment -> ``<bias>-<alignment>``; a neutral
    bias collapses to ``neutral`` regardless of Alignment; any missing/non-dict/
    unavailable entry, empty value, or unrecognized combination (including a
    directional bias with a neutral Alignment, which is outside the enumeration)
    collapses to ``unknown``.
    """
    if not isinstance(options_entry, dict):
        return "unknown"
    if options_entry.get("available") is False:
        return "unknown"
    bias = str(options_entry.get("options_bias_state") or "").strip().lower()
    alignment = str(options_entry.get("alignment") or "").strip().lower()
    if bias not in _BIAS_STATES or alignment not in _ALIGNMENTS:
        return "unknown"
    if bias == "neutral":
        return "neutral"
    value = f"{bias}-{alignment}"
    return value if value in OPT_TAG_VALUES else "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Generators: arbitrary options entries spanning every bias x alignment combo
# plus the missing / non-dict / unavailable / garbage shapes.
# ─────────────────────────────────────────────────────────────────────────────

# A bias / alignment value spanning recognized values, the empty string, None,
# and arbitrary text (so the unrecognized -> ``unknown`` branch is hit).
_bias_value = st.one_of(
    st.sampled_from(_BIAS_STATES),
    st.just(""),
    st.none(),
    st.text(max_size=12),
)
_alignment_value = st.one_of(
    st.sampled_from(_ALIGNMENTS),
    st.just(""),
    st.none(),
    st.text(max_size=12),
)


@st.composite
def _options_entry(draw):
    """An arbitrary value for ``defensibility['options']``.

    Spans every shape the classifier / graph can produce or that a degraded run
    can leave behind:
      * an available dict carrying any bias x alignment combination (including
        the out-of-enum directional-x-neutral pairings);
      * an explicitly unavailable dict (available=False), possibly leaking
        fields, which must collapse to ``unknown``;
      * a dict missing the bias / alignment keys;
      * a non-dict value (None / text / int / list) which must collapse to
        ``unknown``;
      * arbitrary garbage field types.
    Returns the value to store under ``defensibility['options']`` (the sentinel
    ``"__MISSING__"`` means: do not add an options entry at all).
    """
    shape = draw(st.integers(min_value=0, max_value=6))
    if shape == 0:
        # Available, recognized-typed bias/alignment (covers all valid combos and
        # the out-of-enum directional-x-neutral pairings).
        return {
            "available": True,
            "options_bias_state": draw(st.sampled_from(_BIAS_STATES)),
            "alignment": draw(st.sampled_from(_ALIGNMENTS)),
            "source": draw(st.sampled_from(["own-chain", "broad-market"])),
        }
    if shape == 1:
        # Available but with arbitrary (possibly invalid) bias/alignment values.
        return {
            "available": True,
            "options_bias_state": draw(_bias_value),
            "alignment": draw(_alignment_value),
        }
    if shape == 2:
        # Explicitly unavailable — even if it leaks bias/alignment fields.
        return {
            "available": False,
            "reason": draw(st.sampled_from(["outside market hours", "no snapshot", ""])),
            "options_bias_state": draw(st.sampled_from(_BIAS_STATES + [""])),
            "alignment": draw(st.sampled_from(_ALIGNMENTS + [""])),
        }
    if shape == 3:
        # Available dict missing the bias / alignment keys entirely.
        return {"available": True}
    if shape == 4:
        # Non-dict options entry (-> unknown).
        return draw(st.one_of(st.none(), st.text(max_size=10), st.integers(), st.lists(st.integers())))
    if shape == 5:
        # Garbage field types.
        return {
            "available": True,
            "options_bias_state": draw(st.sampled_from([None, 7, [], {}, True])),
            "alignment": draw(st.sampled_from([None, 9, (), {}, False])),
        }
    # No options entry at all (non-options run carries none).
    return "__MISSING__"


@st.composite
def _other_defensibility(draw):
    """Arbitrary additional defensibility entries so the full tag list runs.

    These feed the other dimensions ``derive_setup_tags`` reads (dir/macro/pred/
    va/regime/rs/fc/tm/sess/db) so the opt: tag is asserted to be the FINAL tag
    across a richly varied tag list, not just a minimal one.
    """
    deff = {}
    if draw(st.booleans()):
        deff["macro_trend_conflict"] = draw(
            st.sampled_from(["macro conflict detected", "aligned with the 1d trend", "unavailable", ""])
        )
    if draw(st.booleans()):
        deff["predictive_conflict"] = draw(
            st.sampled_from(["CONFLICT: opposes bias", "No predictive conflict: aligns with trade bias", ""])
        )
    if draw(st.booleans()):
        deff["volume_profile"] = {
            "price_vs_value_area": draw(
                st.sampled_from(["above_value_area", "inside_value_area", "below_value_area", "n/a"])
            )
        }
    if draw(st.booleans()):
        deff["regime"] = {
            "available": True,
            "trend_state": draw(st.sampled_from(["trending", "ranging", "transitional", ""])),
            "favorability": draw(st.sampled_from(["favorable", "unfavorable", "neutral", ""])),
        }
    if draw(st.booleans()):
        deff["relative_strength"] = {
            "available": True,
            "relative_strength_state": draw(st.sampled_from(["leader", "inline", "laggard", ""])),
            "alignment": draw(st.sampled_from(["aligned", "misaligned", "neutral", ""])),
        }
    if draw(st.booleans()):
        deff["forecast"] = {
            "available": True,
            "forecast_alignment": draw(st.sampled_from(["aligned", "misaligned", "neutral", ""])),
            "up_probability": draw(st.floats(min_value=0.0, max_value=1.0)),
        }
    if draw(st.booleans()):
        deff["session"] = {
            "available": True,
            "session_phase": draw(
                st.sampled_from(["opening", "morning", "midday", "afternoon", "closing", "pre_open", ""])
            ),
        }
    if draw(st.booleans()):
        deff["debate"] = {"consensus": draw(st.sampled_from(["strong_agree", "lean", "contested", "unknown", ""]))}
    return deff


@st.composite
def _decision(draw):
    """Draw a decision plus the EXPECTED options tag value.

    Mixes an arbitrary options entry with the other defensibility fields that
    drive the dir/macro/pred/va/regime/rs/fc/tm/sess/db tags."""
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD"]))
    deff = draw(_other_defensibility())
    opts = draw(_options_entry())
    if opts != "__MISSING__":
        deff["options"] = opts
        expected = _expected_opt_value(opts)
    else:
        # No options entry -> missing -> unknown.
        expected = "unknown"
    return {"action": action, "defensibility": deff}, expected


# ── Static invariant: the enumeration itself is small and contains ``unknown`` ─

def test_opt_tag_values_is_low_cardinality_and_contains_unknown():
    """Validates: Requirements 8.3 — fixed enumeration, <= 8 values incl unknown."""
    assert len(OPT_TAG_VALUES) <= 8
    assert "unknown" in OPT_TAG_VALUES


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: The journal options tag is a single fixed-position value from a
# bounded enumeration
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 16: The journal options tag is a single fixed-position value from a bounded enumeration
@settings(max_examples=200, deadline=None)
@given(payload=_decision())
def test_property_16_single_fixed_position_low_cardinality_opt_tag(payload):
    """Validates: Requirements 8.1, 8.2, 8.3

    For ANY decision: ``_options_tag`` returns a bounded, correctly-classified
    value; ``derive_setup_tags`` appends EXACTLY ONE ``opt:<value>`` tag at the
    FINAL fixed position (after the ``db:`` tag); a missing/non-dict/unavailable/
    out-of-enum options entry collapses to ``opt:unknown`` while an available
    entry maps to the (bias x alignment) value (a neutral bias -> ``neutral``);
    and the result (tags + setup_key) is deterministic for identical inputs.
    """
    decision, expected_value = payload

    # ── Boundedness (R8.3): the enumeration has at most 8 members incl unknown.
    assert len(OPT_TAG_VALUES) <= 8, f"OPT_TAG_VALUES must be bounded <= 8, got {len(OPT_TAG_VALUES)}"
    assert "unknown" in OPT_TAG_VALUES

    # ── _options_tag is always in the bounded enumeration (R8.2, R8.3). ───────
    tag_value = _options_tag(decision)
    assert tag_value in OPT_TAG_VALUES, f"_options_tag value {tag_value!r} not in OPT_TAG_VALUES"

    # ── Correct classification: directional bias -> <bias>-<alignment>, neutral
    # bias -> neutral, everything else -> unknown (R8.1 mapping, R8.2 collapse). ─
    assert tag_value == expected_value, f"_options_tag returned {tag_value!r}, expected {expected_value!r}"

    # ── derive_setup_tags: EXACTLY ONE opt: tag, in-enum, correctly mapped. ───
    tags = derive_setup_tags(decision)
    opt_tags = [t for t in tags if t.startswith("opt:")]
    assert len(opt_tags) == 1, f"expected exactly one opt: tag, got {opt_tags}"

    value = opt_tags[0][len("opt:"):]
    assert value in OPT_TAG_VALUES, f"opt: tag value {value!r} not in OPT_TAG_VALUES"
    assert value == expected_value, f"opt: tag value {value!r}, expected {expected_value!r}"

    # ── Fixed position: the opt: tag sits immediately after the ``db:`` tag and
    # immediately before the opportunity ``tier:`` tag, which is in turn followed
    # by the final event ``evt:`` tag (R8.1; tier: appended by
    # adaptive-opportunity-engine R9.2, evt: appended last by
    # earnings-event-risk-gate R10.1). ─────────────────────────────────────────
    assert tags[-3] == opt_tags[0], f"opt: tag must be third-to-last (before tier:/evt:), got tags={tags}"
    assert tags[-4].startswith("db:"), f"opt: tag must come right after db:, got tags={tags}"
    assert tags[-2].startswith("tier:"), f"tier: tag must be second-to-last, got tags={tags}"
    assert tags[-1].startswith("evt:"), f"evt: tag must be the final tag, got tags={tags}"

    # ── Determinism (R8.1): identical inputs -> identical tag list + setup_key. ─
    tags_again = derive_setup_tags(decision)
    assert tags_again == tags, "derive_setup_tags must be deterministic for identical inputs"
    assert setup_key_from_tags(tags_again) == setup_key_from_tags(tags)

    # The opt value occupies its deterministic slot in setup_key: after db: and
    # before the opportunity tier: component, which is in turn followed by the
    # final event evt: component (R9.2; evt: appended last per R10.1).
    key_parts = setup_key_from_tags(tags).split("|")
    assert key_parts[-1].startswith("evt:"), f"evt: component must be last in setup_key, got {key_parts}"
    assert key_parts[-2].startswith("tier:"), f"tier: component must be second-to-last in setup_key, got {key_parts}"
    assert key_parts[-3] == opt_tags[0], f"opt: component must be third-to-last in setup_key, got {key_parts}"
    assert key_parts[-4].startswith("db:")
