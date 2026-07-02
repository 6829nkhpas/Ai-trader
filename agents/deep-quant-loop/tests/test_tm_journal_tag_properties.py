"""Property-based test for the journal management-style setup-fingerprint tag.

Feature: trade-management (journal.py, task 9.3)

This module implements design **Property 23: Management-style tag is exactly one
fixed-enumeration value**:

    For any committed decision, ``journal.derive_setup_tags`` appends EXACTLY ONE
    ``tm:<value>`` tag, whose ``<value>`` is drawn from the fixed
    ``trade_manager.MANAGEMENT_STYLE_TAGS`` enumeration (at most 8 values,
    including ``unknown`` and ``single``). The tag sits at a FIXED position (last,
    immediately after the ``fc:`` tag) so the derived ``setup_key`` is
    deterministic for identical inputs. A missing, unavailable, empty, or
    garbage-style management entry collapses to ``tm:unknown`` (R11.3).

Validates: Requirements 11.1, 11.2, 11.3.

The implementation under test lives in ``journal.py``:
  - ``derive_setup_tags(decision)`` — builds the coarse setup fingerprint and
    appends the management tag last (after the ``fc:`` tag), via
    ``_management_style_tag``.
  - ``_management_style_tag(decision)`` — collapses the defensibility management
    entry's ``style`` into one fixed enumeration value, defaulting to ``unknown``
    on an absent / unavailable / unrecognized entry.
  - ``setup_key_from_tags(tags)`` — joins the tags into the deterministic key.
  - ``trade_manager.MANAGEMENT_STYLE_TAGS`` — the fixed enumeration owned by the
    Trade_Manager (the single source of truth for the style mapping, AD-8).

The sys.path / import pattern mirrors the sibling forecast journal-tag property
test (``test_forecast_journal_tag_properties.py``) in this directory.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py / trade_manager.py live one
# level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import trade_manager  # noqa: E402
from journal import (  # noqa: E402
    derive_setup_tags,
    setup_key_from_tags,
    TM_TAG_VALUES,
)

# The fixed enumeration of style values (bare, without the ``tm:`` prefix).
_STYLE_VALUES = list(trade_manager.MANAGEMENT_STYLE_TAGS)
# The non-``unknown`` styles a present/available entry may legitimately carry.
_KNOWN_STYLES = [s for s in _STYLE_VALUES if s != "unknown"]


# ── Strategies for the other defensibility fields (drive the other tags) ─────
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

_macro = st.sampled_from([
    "Macro conflict: trade opposes the 1d trend",
    "Trade aligned with the 1d trend",
    "1d trend unavailable",
    "",
    None,
])
_predictive = st.sampled_from([
    "CONFLICT: forward projection opposes the trade bias",
    "No predictive conflict: projection aligns with trade bias",
    "",
    None,
])
_value_area = st.sampled_from([
    "above_value_area", "inside_value_area", "below_value_area", "unknown", None,
])
_regime_entry = st.sampled_from([
    {"available": True, "trend_state": "trending", "favorability": "favorable"},
    {"available": True, "trend_state": "ranging", "favorability": "neutral"},
    {"available": False, "reason": "insufficient data"},
    None,
])
_rs_entry = st.sampled_from([
    {"available": True, "relative_strength_state": "leader", "alignment": "aligned"},
    {"available": True, "relative_strength_state": "laggard", "alignment": "misaligned"},
    {"available": False, "reason": "missing benchmark"},
    None,
])
_forecast_entry = st.sampled_from([
    {"available": True, "forecast_alignment": "aligned", "up_probability": 0.8},
    {"available": True, "forecast_alignment": "neutral", "up_probability": 0.5},
    {"available": False, "reason": "insufficient data"},
    None,
])


@st.composite
def _valid_management(draw):
    """An available management entry carrying a recognized style.

    Returns (management_entry, expected_tag_value)."""
    style = draw(st.sampled_from(_KNOWN_STYLES))
    entry = {
        "available": True,
        "style": draw(st.sampled_from([
            style,            # exact
            style.upper(),    # case-insensitive collapse
            f"  {style}  ",   # whitespace-padded collapse
        ])),
        "legs": draw(st.sampled_from([
            None,
            [{"target": 101.0, "fraction": 0.5}],
            [{"target": 101.0, "fraction": 0.5}, {"target": 103.0, "fraction": 0.5}],
        ])),
        "realized_r": draw(st.sampled_from([None, 0.0, 1.5, -1.0])),
    }
    return entry, style


@st.composite
def _unknown_management(draw):
    """A management entry that must collapse to ``unknown``.

    Covers missing/None/non-dict/unavailable/empty/garbage-style cases.
    Returns (management_entry, "unknown")."""
    kind = draw(st.sampled_from([
        "missing",       # no management key at all
        "none",          # management is None
        "not_dict",      # management is a non-dict value
        "unavailable",   # explicit available=False (even if it leaks a style)
        "empty",         # available but blank style
        "bad_style",     # available but unrecognized style string
        "garbage",       # available but non-string junk style
    ]))
    if kind == "missing":
        return "__MISSING__", "unknown"
    if kind == "none":
        return None, "unknown"
    if kind == "not_dict":
        return draw(st.sampled_from(["scale", 42, ["scale"]])), "unknown"
    if kind == "unavailable":
        return {
            "available": False,
            "reason": "no management entry",
            "style": draw(st.sampled_from(_KNOWN_STYLES)),
        }, "unknown"
    if kind == "empty":
        return {"available": True, "style": draw(st.sampled_from(["", "   ", None]))}, "unknown"
    if kind == "bad_style":
        return {
            "available": True,
            "style": draw(st.sampled_from([
                "scaled", "breakeven", "trailing", "managed", "partial", "tm:scale",
            ])),
        }, "unknown"
    # garbage: non-string junk style
    return {
        "available": True,
        "style": draw(st.sampled_from([7, [], {}, True, 3.14])),
    }, "unknown"


@st.composite
def _decision(draw):
    """Draw a decision plus the EXPECTED management tag value.

    Mixes valid and unknown-collapsing management entries with arbitrary other
    defensibility fields that drive the macro/pred/va/regime/rs/fc tags."""
    mgmt, expected = draw(st.one_of(_valid_management(), _unknown_management()))
    deff = {
        "macro_trend_conflict": draw(_macro),
        "predictive_conflict": draw(_predictive),
        "volume_profile": {"price_vs_value_area": draw(_value_area)},
        "regime": draw(_regime_entry),
        "relative_strength": draw(_rs_entry),
        "forecast": draw(_forecast_entry),
    }
    if mgmt != "__MISSING__":
        deff["management"] = mgmt
    decision = {"action": draw(_action), "defensibility": deff}
    return decision, expected


# ── Static invariant: the enumeration itself is small and well-formed ─────────

def test_tm_tag_values_is_low_cardinality_and_contains_unknown_and_single():
    """Validates: Requirements 11.2 — fixed enumeration, <= 8 values incl unknown/single."""
    assert len(TM_TAG_VALUES) <= 8
    assert "unknown" in TM_TAG_VALUES
    assert "single" in TM_TAG_VALUES
    # The journal re-exports the Trade_Manager's enumeration verbatim (AD-8).
    assert set(TM_TAG_VALUES) == set(trade_manager.MANAGEMENT_STYLE_TAGS)


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: management-style tag is exactly one fixed-enumeration value
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 23: Management-style tag is exactly one fixed-enumeration value
@settings(max_examples=50, deadline=None)
@given(payload=_decision())
def test_property_23_single_fixed_position_low_cardinality_tm_tag(payload):
    """Validates: Requirements 11.1, 11.2, 11.3

    derive_setup_tags appends exactly ONE ``tm:<value>`` tag, drawn from the
    fixed MANAGEMENT_STYLE_TAGS enumeration, at a fixed position (last,
    immediately after the ``fc:`` tag); a missing/unavailable/garbage management
    entry collapses to ``tm:unknown`` while an available entry maps to its
    declared style; and the result (tags + setup_key) is deterministic for
    identical inputs.
    """
    decision, expected_value = payload

    tags = derive_setup_tags(decision)

    # ── Exactly ONE management tag (R11.1) ────────────────────────────────────
    tm_tags = [t for t in tags if t.startswith("tm:")]
    assert len(tm_tags) == 1

    # ── Its value is in the fixed low-cardinality enumeration (R11.2) ─────────
    value = tm_tags[0][len("tm:"):]
    assert value in TM_TAG_VALUES
    assert len(TM_TAG_VALUES) <= 8 and "unknown" in TM_TAG_VALUES

    # ── Missing/unavailable/garbage -> tm:unknown; valid -> declared style (R11.2, R11.3) ─
    assert value == expected_value

    # ── Fixed position: tm tag sits immediately after the ``fc:`` tag and
    #    immediately before the ``sess:`` tag. The session dimension and the
    #    multi-agent-debate ``db:`` tag are appended after it, so the ``db:`` tag
    #    is now the FINAL tag (R11.1). ──────────────────────────────────────
    tm_index = tags.index(tm_tags[0])
    # The tag immediately before the tm tag is the forecast tag.
    assert tags[tm_index - 1].startswith("fc:")
    # The tag immediately after the tm tag is the session (sess:) tag.
    assert tags[tm_index + 1].startswith("sess:")
    # The opportunity-tier dimension is always appended last (after sess:/db:/opt:),
    # per adaptive-opportunity-engine R9.2.
    assert tags[-1].startswith("tier:")

    # ── Determinism: identical inputs -> identical tag list and setup_key (R11.1) ─
    tags_again = derive_setup_tags(decision)
    assert tags_again == tags
    assert setup_key_from_tags(tags_again) == setup_key_from_tags(tags)
    # The tm value occupies the same deterministic slot in setup_key, immediately
    # after the fc: component; the db: component is always last.
    key_parts = setup_key_from_tags(tags).split("|")
    assert key_parts[-1].startswith("tier:")
    tm_key_index = key_parts.index(tm_tags[0])
    assert key_parts[tm_key_index - 1].startswith("fc:")
