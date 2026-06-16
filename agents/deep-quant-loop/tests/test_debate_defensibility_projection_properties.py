"""Property-based test for the faithful debate defensibility projection.

Feature: multi-agent-debate (task 11.2)

This module implements design **Property 17: The debate defensibility entry is
a faithful projection**:

    The ``debate`` sub-entry in the defensibility record is built ONLY from the
    stored Bull/Bear stances and the Judge verdict — ``bull_stance``,
    ``bear_stance``, ``consensus``, and ``conviction`` are faithfully projected
    (mirrored, never fabricated). A missing/garbled stance is marked
    ``{"available": False}`` rather than invented, an unrecognized consensus
    degrades to ``"unknown"`` (no fabrication of a different valid value), and the
    conviction is the stored value coerced to an int (or ``None``), never a
    fabricated number.

Validates: Requirements 7.1, 7.2.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` attaches
    a ``debate`` entry (via ``_debate_entry``) for a DEBATE-mode decision that
    carries a private ``_debate`` carrier =
    ``{bull_stance, bear_stance, consensus, conviction}``.

The strategy generates a ``_debate`` carrier with arbitrary bull/bear stances
(dicts or ``None``/missing), arbitrary consensus (valid enum members + invalid
strings/None/ints) and arbitrary conviction (in-range ints, out-of-range ints,
``None`` and non-numeric values), together with an arbitrary committed action
(BUY / SELL / HOLD). For each it asserts the projected ``debate`` entry is a
faithful mirror with exactly the expected key set and no invented content.

The sys.path / import pattern mirrors the sibling
``test_session_defensibility_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import build_defensibility_record  # noqa: E402
from debate import DEBATE_CONSENSUS_VALUES  # noqa: E402

# The exact key set the faithful projection must produce (committed_against_
# contested is the only conditionally-added key).
_BASE_KEYS = {"bull_stance", "bear_stance", "consensus", "conviction", "conviction_basis"}


# ── Generators ──────────────────────────────────────────────────────────────


@st.composite
def _stance_dicts(draw):
    """An arbitrary serialized stance dict (mirrored verbatim by the projection).

    Strength is drawn across ints / floats / bools / None / missing so the
    conviction-basis derivation (``_stance_strength``) is exercised, but the
    projection mirrors whatever dict is supplied byte-for-byte regardless.
    """
    d = {}
    if draw(st.booleans()):
        d["role"] = draw(st.sampled_from(["bull", "bear"]))
    if draw(st.booleans()):
        d["lean"] = draw(st.sampled_from(["long", "short", "neutral"]))
    if draw(st.booleans()):
        d["strength"] = draw(
            st.one_of(
                st.integers(min_value=-50, max_value=200),
                st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
                st.booleans(),
                st.none(),
            )
        )
    if draw(st.booleans()):
        d["arguments"] = draw(st.lists(st.text(max_size=12), max_size=4))
    if draw(st.booleans()):
        d["biggest_risk"] = draw(st.text(max_size=20))
    if draw(st.booleans()):
        d["available"] = draw(st.booleans())
    return d


# A stance is either a present dict, or absent (None) -> marked unavailable.
_stance = st.one_of(_stance_dicts(), st.none())

# Consensus: valid enum members + invalid values (degrade to "unknown").
_consensus = st.one_of(
    st.sampled_from(list(DEBATE_CONSENSUS_VALUES)),
    st.text(max_size=12),
    st.none(),
    st.integers(),
)

# Conviction: in-range ints, out-of-range ints, None, and non-numeric values.
_conviction = st.one_of(
    st.integers(min_value=0, max_value=100),
    st.integers(min_value=-500, max_value=500),
    st.none(),
    st.text(max_size=6),
)

_action = st.sampled_from(["BUY", "SELL", "HOLD"])


def _expected_conviction(raw):
    """Mirror the implementation's conviction coercion exactly."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ── Property 17 ───────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 17: The debate defensibility entry is a faithful projection
@settings(max_examples=100, deadline=None)
@given(
    bull=_stance,
    bear=_stance,
    consensus=_consensus,
    conviction=_conviction,
    action=_action,
)
def test_property_17_debate_entry_is_a_faithful_projection(
    bull, bear, consensus, conviction, action
):
    """Validates: Requirements 7.1, 7.2

    The ``debate`` entry mirrors the stored Bull/Bear stances and the Judge
    verdict verbatim, marks a missing stance unavailable rather than inventing
    it, degrades an unrecognized consensus to ``"unknown"`` (no different valid
    value fabricated), coerces the conviction faithfully, and carries exactly the
    expected key set (no arguments/evidence beyond the stored stances).
    """
    decision = {
        "action": action,
        "source": "declare_trade",
        "_debate": {
            "bull_stance": bull,
            "bear_stance": bear,
            "consensus": consensus,
            "conviction": conviction,
        },
    }

    record = build_defensibility_record([], decision, mode="DEBATE", manual_trade=None)

    assert isinstance(record, dict)
    assert "debate" in record, "DEBATE-mode decision with a _debate carrier must yield a debate entry"
    entry = record["debate"]

    # ── Faithful stance projection (R7.2): a dict is mirrored verbatim; a
    # missing/None stance is marked unavailable, never invented. ──────────────
    if isinstance(bull, dict):
        assert entry["bull_stance"] == bull
    else:
        assert entry["bull_stance"] == {"available": False}
    if isinstance(bear, dict):
        assert entry["bear_stance"] == bear
    else:
        assert entry["bear_stance"] == {"available": False}

    # ── Consensus projection (R7.1, R7.2): a valid member is preserved; any
    # other value degrades to "unknown" (no different valid value fabricated). ─
    if consensus in DEBATE_CONSENSUS_VALUES:
        assert entry["consensus"] == consensus
    else:
        assert entry["consensus"] == "unknown"
    assert entry["consensus"] in set(DEBATE_CONSENSUS_VALUES) | {"unknown"}

    # ── Conviction projection (R7.2): the stored value coerced to int, else
    # None — never a fabricated number. ───────────────────────────────────────
    expected_conv = _expected_conviction(conviction)
    assert entry["conviction"] == expected_conv
    assert entry["conviction"] is None or isinstance(entry["conviction"], int)

    # ── conviction_basis is a present, faithful statement (a string). ─────────
    assert isinstance(entry["conviction_basis"], str)
    assert entry["conviction_basis"]

    # ── Exactly the expected keys (R7.2): no arguments/evidence beyond the
    # stored stances appear. committed_against_contested only when a directional
    # BUY/SELL was committed against a contested consensus. ───────────────────
    expected_keys = set(_BASE_KEYS)
    if entry["consensus"] == "contested" and action in ("BUY", "SELL"):
        expected_keys.add("committed_against_contested")
    assert set(entry.keys()) == expected_keys, (
        f"unexpected debate-entry keys {set(entry.keys())} != {expected_keys}"
    )
