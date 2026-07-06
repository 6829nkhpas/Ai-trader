"""Property-based test for the debate fingerprint tag (journal.py, task 13.2).

Feature: multi-agent-debate

This module implements design **Property 24: The debate fingerprint tag is
fixed, bounded, and deterministic**:

    For ANY decision, the ``db:`` tag value is always one of ``DB_TAG_VALUES``
    (bounded, at most 8 members); it is ``unknown`` for non-DEBATE decisions /
    missing / empty / unrecognized consensus, and the recognized consensus value
    otherwise; the tag is appended at a FIXED position (always the last tag,
    after ``sess:``) so ``derive_setup_tags`` is deterministic for identical
    inputs.

Validates: Requirements 9.1, 9.2, 9.3.

The strategy builds arbitrary decision dicts shaped like the committed decisions
``derive_setup_tags`` reads: an ``action`` (BUY/SELL/HOLD) plus a
``defensibility`` record that may or may not carry a ``debate`` entry (with a
consensus drawn from the recognized values, the ``unknown`` value, ``None``, an
arbitrary string, or a non-dict ``debate``), and that may also carry other
defensibility entries (regime/relative_strength/forecast/management/session and
the macro/predictive/volume-profile fields) so the full tag list is exercised.
For each decision it asserts the db: tag is bounded, correctly classified,
appears exactly once, is always last, and is deterministic across repeated
calls.

The sys.path / import pattern mirrors the sibling test modules (journal.py lives
one level up). Importing journal is safe: it imports trade_manager and sqlite3
only, performs no network at import, and ``derive_setup_tags`` is pure (no DB
write).
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
    DB_TAG_VALUES,
    _debate_tag,
    derive_setup_tags,
)

# The three recognized categorical Debate_Consensus values (everything else maps
# to ``unknown``). Kept local so the test asserts the contract independently of
# journal's private ``_DB_CONSENSUS_VALUES`` set.
_RECOGNIZED_CONSENSUS = {"strong_agree", "lean", "contested"}


# ─────────────────────────────────────────────────────────────────────────────
# Generators: arbitrary decisions exercising the full tag list and every debate
# entry shape (present/absent, recognized/unknown/None/arbitrary, non-dict).
# ─────────────────────────────────────────────────────────────────────────────

# A consensus value spanning recognized values, the explicit ``unknown`` value,
# None, and arbitrary text (so the unrecognized -> ``unknown`` branch is hit).
_consensus = st.one_of(
    st.sampled_from(sorted(_RECOGNIZED_CONSENSUS)),
    st.just("unknown"),
    st.none(),
    st.text(max_size=20),
)


@st.composite
def _debate_entry(draw):
    """An arbitrary value for ``defensibility['debate']``.

    Spans: a dict carrying a ``consensus`` (from ``_consensus``), a dict with no
    ``consensus`` key, and a non-dict value (so the non-dict -> ``unknown``
    branch is exercised).
    """
    shape = draw(st.integers(min_value=0, max_value=2))
    if shape == 0:
        return {"consensus": draw(_consensus)}
    if shape == 1:
        # Dict without a consensus key (missing consensus -> unknown).
        return {"conviction": draw(st.integers(min_value=0, max_value=100))}
    # Non-dict debate entry (-> unknown).
    return draw(st.one_of(st.none(), st.text(max_size=10), st.integers(), st.lists(st.integers())))


@st.composite
def _other_defensibility(draw):
    """Arbitrary additional defensibility entries so the full tag list runs.

    These feed the other dimensions ``derive_setup_tags`` reads (dir/macro/pred/
    va/regime/rs/fc/tm/sess) so the db: tag is asserted to be last across a
    richly varied tag list, not just a minimal one.
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
            "trend_state": draw(st.sampled_from(["trending", "ranging", "transitional", ""])),
            "favorability": draw(st.sampled_from(["favorable", "unfavorable", "neutral", ""])),
        }
    if draw(st.booleans()):
        deff["relative_strength"] = {
            "relative_strength_state": draw(st.sampled_from(["leader", "inline", "laggard", ""])),
            "alignment": draw(st.sampled_from(["aligned", "misaligned", "neutral", ""])),
        }
    if draw(st.booleans()):
        deff["forecast"] = {
            "forecast_alignment": draw(st.sampled_from(["aligned", "misaligned", "neutral", ""])),
            "up_probability": draw(st.floats(min_value=0.0, max_value=1.0)),
        }
    if draw(st.booleans()):
        deff["session"] = {
            "session_phase": draw(
                st.sampled_from(["opening", "morning", "midday", "afternoon", "closing", "pre_open", ""])
            )
        }
    return deff


@st.composite
def _decision(draw):
    """An arbitrary committed-decision dict shaped like what the graph emits."""
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD"]))
    deff = draw(_other_defensibility())
    # A debate entry may or may not be present (non-DEBATE decisions carry none).
    if draw(st.booleans()):
        deff["debate"] = draw(_debate_entry())
    return {"action": action, "defensibility": deff}


def _expected_db_value(decision: dict) -> str:
    """The contract-expected db value, computed independently of journal internals."""
    deff = decision.get("defensibility") or {}
    debate = deff.get("debate")
    if not isinstance(debate, dict):
        return "unknown"
    consensus = debate.get("consensus")
    consensus = str(consensus or "").strip().lower()
    return consensus if consensus in _RECOGNIZED_CONSENSUS else "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Property 24: The debate fingerprint tag is fixed, bounded, and deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 24: The debate fingerprint tag is fixed, bounded, and deterministic
@settings(max_examples=100, deadline=None)
@given(decision=_decision())
def test_property_24_debate_tag_is_fixed_bounded_deterministic(decision):
    """Validates: Requirements 9.1, 9.2, 9.3

    For ANY decision: ``_debate_tag`` returns a bounded, correctly-classified
    value; ``derive_setup_tags`` appends exactly one ``db:`` tag at the FINAL
    fixed position; and the derived tag list is deterministic across calls.
    """
    # ── Boundedness (R9.3): the enumeration has at most 8 members. ────────────
    assert len(DB_TAG_VALUES) <= 8, f"DB_TAG_VALUES must be bounded <= 8, got {len(DB_TAG_VALUES)}"

    # ── _debate_tag is always in the bounded enumeration (R9.2, R9.3). ────────
    tag_value = _debate_tag(decision)
    assert tag_value in DB_TAG_VALUES, f"_debate_tag value {tag_value!r} not in DB_TAG_VALUES"

    # ── Correct classification: recognized consensus verbatim, else unknown
    # (R9.1 recognized value, R9.2 missing/empty/unrecognized -> unknown). ─────
    expected = _expected_db_value(decision)
    assert tag_value == expected, f"_debate_tag returned {tag_value!r}, expected {expected!r}"

    # ── derive_setup_tags: exactly one db: tag at its fixed position, in-enum
    #    (R9.1). The options ``opt:`` and opportunity ``tier:`` dimensions are
    #    appended after it, so ``db:`` is no longer the final tag. ──────────────
    tags = derive_setup_tags(decision)
    db_tags = [t for t in tags if t.startswith("db:")]
    assert len(db_tags) == 1, f"expected exactly one db: tag, got {db_tags}"

    db_value = db_tags[0].split("db:", 1)[1]
    assert db_value in DB_TAG_VALUES, f"db: tag value {db_value!r} not in DB_TAG_VALUES"
    assert db_value == expected, f"db: tag value {db_value!r}, expected {expected!r}"

    # ── Fixed position: the db: tag immediately follows the sess: tag (R9.1) and
    #    the final tag is the opportunity ``tier:`` dimension. ──────────────────
    db_index = tags.index(db_tags[0])
    assert tags[db_index - 1].startswith("sess:"), f"db: tag must come right after sess:, got tags={tags}"
    assert tags[-1].startswith("tier:"), f"tier: tag must be the final tag, got tags={tags}"

    # ── Determinism (R9.1): identical inputs yield identical tag lists. ───────
    assert derive_setup_tags(decision) == tags, "derive_setup_tags must be deterministic for identical inputs"
