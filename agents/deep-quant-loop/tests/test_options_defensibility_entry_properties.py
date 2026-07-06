# Feature: options-agent-integration, Property 12: The defensibility entry mirrors the tool result verbatim or records unavailable
"""Property-based test for the defensibility options entry (graph.py, task 7.2).

Feature: options-agent-integration

This module implements design **Property 12: The defensibility entry mirrors the
tool result verbatim or records unavailable**:

    For any message history, when a usable ``get_options_analytics`` result is
    present the options defensibility entry reproduces the PCR (``pcr_oi`` /
    ``pcr_volume``), ``max_pain``, the aggregate ``oi_buildup``, the ``oi_walls``,
    the ``iv_skew``, the ``futures_basis``, the ``options_bias_state``, the
    ``alignment``, and the ``chain_context`` exactly as returned by the tool (no
    substitution); when no usable result is present — none in history, a non-dict
    result, an Unavailable_Marker, or a malformed label — the entry is recorded
    as unavailable with no fabricated bias values.

Validates: Requirements 6.1, 6.2, 6.3.

The implementation under test lives in ``graph.py``:
  - ``_options_entry(results)`` — reads ``results['get_options_analytics']`` (the
    ``_latest_tool_results`` map entry, already parsed to a dict) and mirrors a
    usable Options_Bias_Label into the defensibility record, or records it as
    unavailable.
  - ``build_defensibility_record(messages, decision, mode, ...)`` — assembles the
    record whose ``"options"`` key holds the entry, picking the MOST RECENT
    non-error result per tool name via ``_latest_tool_results``.

The real LLM / Rust server is never invoked. ``_options_entry`` operates purely
on an in-memory results map, so the categorical branches run fully in-memory; a
lightweight stub ToolMessage (``type == "tool"`` with ``.name`` and ``.content``)
stands in for the LangChain ``ToolMessage`` for the most-recent-wins path, with
tool results serialized both as JSON and as Python dict-repr strings since both
quoting styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_session_defensibility_mirror_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable when
pytest is run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    build_defensibility_record,
    _options_entry,
    _latest_tool_results,
    OPTIONS_BIAS_STATES,
    OPTIONS_CHAIN_CONTEXTS,
    ALIGNMENT_VALUES,
)

OPTIONS_TOOL = "get_options_analytics"

# The analytics fields the entry mirrors VERBATIM from a usable label (R6.1).
_MIRRORED_FIELDS = (
    "pcr_oi",
    "pcr_volume",
    "max_pain",
    "oi_buildup",
    "oi_walls",
    "iv_skew",
    "futures_basis",
    "options_bias_state",
    "alignment",
    "chain_context",
)
# The categorical bias fields that must NEVER be fabricated on an unavailable
# / missing / non-dict / malformed entry (R6.3).
_BIAS_FIELDS = ("options_bias_state", "alignment", "chain_context")


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/underlying/expiry restricted to tokens that can never contain the
# "error" or "unavailable" substrings, so usable results are classified purely
# by their structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_underlying = st.sampled_from(["NIFTY 50", "BANKNIFTY", "NIFTY", "FINNIFTY"])
_expiry = st.sampled_from(["2024-06-27", "2024-07-25", "2024-12-26", ""])
_options_bias_state = st.sampled_from(sorted(OPTIONS_BIAS_STATES))
_alignment = st.sampled_from(sorted(ALIGNMENT_VALUES))
_chain_context = st.sampled_from(sorted(OPTIONS_CHAIN_CONTEXTS))
_serialization_style = st.sampled_from(["json", "repr"])

# A numeric-or-null analytic leaf, per the options Tool_Result_Contract.
_num_or_null = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
_buildup_label = st.sampled_from(
    ["long_buildup", "short_buildup", "long_unwinding", "short_covering", "neutral"]
)
_iv_skew = st.one_of(
    st.none(),
    st.fixed_dictionaries(
        {
            "put_minus_call": _num_or_null,
            "slope": _num_or_null,
            "atm_iv": _num_or_null,
        }
    ),
)


@st.composite
def _usable_label(draw):
    """A full, usable Options_Bias_Label as produced by ``get_options_analytics``.

    A usable label must carry an ``options_bias_state``, an ``alignment``, and a
    ``chain_context`` each from their fixed enums — exactly the recognition
    predicate ``_options_entry`` applies — plus the named analytics fields.
    """
    return {
        "symbol": draw(_symbol),
        "underlying": draw(_underlying),
        "expiry": draw(_expiry),
        "spot": draw(_num_or_null),
        "pcr_oi": draw(_num_or_null),
        "pcr_volume": draw(_num_or_null),
        "max_pain": draw(_num_or_null),
        "oi_buildup": {"call": draw(_buildup_label), "put": draw(_buildup_label)},
        "oi_walls": {"support": draw(_num_or_null), "resistance": draw(_num_or_null)},
        "iv_skew": draw(_iv_skew),
        "futures_basis": draw(_num_or_null),
        "options_bias_state": draw(_options_bias_state),
        "alignment": draw(_alignment),
        "chain_context": draw(_chain_context),
    }


# Sentinel for the "no get_options_analytics result in history" scenario.
_MISSING = object()


@st.composite
def _unavailable_marker(draw):
    """An honest Unavailable_Marker — bias fields OMITTED (R3.2)."""
    return {
        "symbol": draw(_symbol),
        "underlying": draw(_underlying),
        "chain_context": draw(_chain_context),
        "unavailable": True,
        "reason": draw(st.sampled_from(
            ["no chain snapshot available", "outside market hours", "unsubscribed underlying"]
        )),
    }


@st.composite
def _malformed_label(draw):
    """A dict that is NOT a usable label: at least one of options_bias_state /
    alignment / chain_context is missing or outside its fixed enum, so
    ``_options_entry`` must record it as unavailable without fabrication."""
    label = draw(_usable_label())
    # Corrupt one of the three categorical fields so recognition fails.
    field = draw(st.sampled_from(_BIAS_FIELDS))
    corruption = draw(st.sampled_from(["drop", "bad_value"]))
    if corruption == "drop":
        label.pop(field, None)
    else:
        label[field] = draw(st.sampled_from(["sideways", "agreed", "stock-chain", "", "unknown"]))
    return label


@st.composite
def _non_usable_value(draw):
    """A latest-result value that must yield an unavailable entry: a missing
    result, a non-dict result, an Unavailable_Marker, or a malformed label."""
    kind = draw(st.sampled_from(["missing", "non_dict", "unavailable", "malformed"]))
    if kind == "missing":
        return _MISSING
    if kind == "non_dict":
        return draw(st.one_of(
            st.none(),
            st.integers(),
            st.text(max_size=12),
            st.lists(st.integers(), max_size=3),
        ))
    if kind == "unavailable":
        return draw(_unavailable_marker())
    return draw(_malformed_label())


def _assert_mirrors(entry, label):
    """The options entry mirrors the source label verbatim, with no fabrication."""
    assert entry.get("available") is True
    for field in _MIRRORED_FIELDS:
        assert entry[field] == label[field], f"field {field} not mirrored verbatim"
    # The categorical states are exactly the source's (drawn from the fixed enums).
    assert entry["options_bias_state"] in OPTIONS_BIAS_STATES
    assert entry["alignment"] in ALIGNMENT_VALUES
    assert entry["chain_context"] in OPTIONS_CHAIN_CONTEXTS


def _assert_unavailable(entry):
    """An unavailable entry carries available False + a reason, and fabricates
    NO bias values (R6.3)."""
    assert entry.get("available") is False
    assert isinstance(entry.get("reason"), str) and entry["reason"]
    for field in _BIAS_FIELDS:
        assert field not in entry, f"fabricated bias field {field} on unavailable entry"
    # No analytics leaves are invented either.
    for field in ("pcr_oi", "pcr_volume", "max_pain", "oi_buildup", "oi_walls",
                  "iv_skew", "futures_basis"):
        assert field not in entry


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: the defensibility options entry mirrors the tool result verbatim
#              or records unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 12: The defensibility entry mirrors the tool result verbatim or records unavailable
@settings(max_examples=200, deadline=None)
@given(
    is_usable=st.booleans(),
    label=_usable_label(),
    non_usable=_non_usable_value(),
    earlier=st.lists(_usable_label(), min_size=0, max_size=3),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_12_defensibility_options_entry_mirrors_or_unavailable(
    is_usable, label, non_usable, earlier, style, action
):
    """Validates: Requirements 6.1, 6.2, 6.3

    (6.1) The defensibility record includes an options entry carrying the PCR,
          max pain, aggregate OI buildup, OI walls, IV skew, futures basis,
          Options_Bias_State, Alignment, and chain context taken from the most
          recent ``get_options_analytics`` result.
    (6.2) The entry is populated using ONLY values returned by the tool — every
          mirrored field equals the source verbatim, with no substitution.
    (6.3) When no usable result is present (none in history, a non-dict, an
          Unavailable_Marker, or a malformed label), the entry is recorded as
          unavailable with no fabricated bias values.
    """
    if is_usable:
        # ── Usable latest result: the entry mirrors every field verbatim ──────
        results = {OPTIONS_TOOL: label}
        entry = _options_entry(results)
        _assert_mirrors(entry, label)

        # Context fields (symbol/underlying/expiry/spot) carried verbatim too.
        for k in ("symbol", "underlying", "expiry", "spot"):
            assert entry.get(k) == label[k]

        # Determinism: a second build over an identical source yields an identical entry.
        assert _options_entry({OPTIONS_TOOL: dict(label)}) == entry

        # ── Most-recent-wins via the full record builder ──────────────────────
        # Earlier (stale) usable labels first, then the target as the LATEST one.
        messages = [
            StubToolMessage(content=_serialize(lbl, style), name=OPTIONS_TOOL)
            for lbl in earlier
        ]
        messages.append(StubToolMessage(content=_serialize(label, style), name=OPTIONS_TOOL))
        decision = {"action": action, "source": "declare_trade"}
        record = build_defensibility_record(messages, decision, mode="FIND")
        _assert_mirrors(record["options"], label)
        # The mirror is also reachable via _options_entry over _latest_tool_results.
        _assert_mirrors(_options_entry(_latest_tool_results(messages)), label)
    else:
        # ── Non-usable latest result: recorded as unavailable, no fabrication ─
        results = {} if non_usable is _MISSING else {OPTIONS_TOOL: non_usable}
        entry = _options_entry(results)
        _assert_unavailable(entry)

        # Determinism holds on the unavailable branch as well.
        results2 = {} if non_usable is _MISSING else {OPTIONS_TOOL: non_usable}
        assert _options_entry(results2) == entry
