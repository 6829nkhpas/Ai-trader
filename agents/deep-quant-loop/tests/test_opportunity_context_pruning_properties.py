"""Property-based test for deterministic context pruning (opportunity.py, task 6.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 14: Context pruning is bounded,
preserving, and deterministic**:

    For any message history and configuration, ``prune_messages`` returns a list
    that (a) is BOUNDED — never longer than ``prune_max_messages``; (b) is a
    SUBSEQUENCE of the input in original order, so message/tool-call pairing is
    never reordered and no retained tool result is orphaned from the assistant
    call that issued it; (c) PRESERVES the system message and, when the retained
    set fits, the most-recent usable result of every tool plus the most-recent
    ``prune_keep_recent_turns`` turns; and (d) is DETERMINISTIC — byte-identical on
    repeated calls with the same inputs. A history already within the ceiling is
    returned unchanged.

Validates: Requirements 7.1, 7.2, 7.3.

The sys.path / import bootstrap and the ``@settings`` / ``@given`` convention mirror
``tests/test_opportunity_watch_cap_convergence_properties.py`` and the sibling
``tests/test_opportunity_*_properties.py`` modules.
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
    DEFAULT_HEARTBEAT_ENABLED,
    DEFAULT_HEARTBEAT_MAX,
    DEFAULT_LOWER_TIERS_ENABLED,
    DEFAULT_SESSION_MAX_TURNS,
    DEFAULT_SESSION_MAX_WALL_SECS,
    DEFAULT_SIZE_FACTOR_A_PLUS,
    DEFAULT_SIZE_FACTOR_B_CONTINUATION,
    DEFAULT_SIZE_FACTOR_SCALP,
    DEFAULT_WATCH_CAP,
    OpportunityConfig,
    prune_messages,
)

_TOOL_NAMES = ["get_candles", "get_consensus_report", "get_market_regime", "get_forecast"]


class _Msg:
    """A minimal LangChain-message-shaped stub (duck-typed via ``.type``)."""

    def __init__(self, type, name=None, tool_calls=None, content=""):
        self.type = type
        self.name = name
        self.tool_calls = tool_calls or []
        self.content = content
        self.additional_kwargs = {}

    def __repr__(self):  # pragma: no cover - debug aid only
        return f"<{self.type}:{self.name or self.content[:8]}>"


# ── Config builder: fuzz the prune fields, hold the rest at defaults ───────────
@st.composite
def configs(draw):
    """Build an ``OpportunityConfig`` fuzzing the two fields ``prune_messages``
    consults — ``prune_max_messages`` (>= 1) and ``prune_keep_recent_turns``
    (>= 1) — and holding every other field at its documented default.
    """
    return OpportunityConfig(
        watch_cap=DEFAULT_WATCH_CAP,
        session_max_turns=DEFAULT_SESSION_MAX_TURNS,
        session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
        size_factor_a_plus=DEFAULT_SIZE_FACTOR_A_PLUS,
        size_factor_b_continuation=DEFAULT_SIZE_FACTOR_B_CONTINUATION,
        size_factor_scalp=DEFAULT_SIZE_FACTOR_SCALP,
        lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
        heartbeat_enabled=DEFAULT_HEARTBEAT_ENABLED,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=DEFAULT_HEARTBEAT_MAX,
        prune_keep_recent_turns=draw(st.integers(min_value=1, max_value=12)),
        prune_max_messages=draw(st.integers(min_value=1, max_value=30)),
    )


@st.composite
def histories(draw):
    """Build a message history: an optional system message, a leading human turn,
    then a run of turn-groups (assistant tool-call + its tool result, or assistant
    prose), long enough to exercise the pruning path."""
    msgs = []
    if draw(st.booleans()):
        msgs.append(_Msg("system", content="SYSTEM_INSTRUCTION"))
    msgs.append(_Msg("human", content="find a trade"))
    n_turns = draw(st.integers(min_value=1, max_value=40))
    for i in range(n_turns):
        if draw(st.integers(min_value=0, max_value=3)) == 0:
            # Assistant prose turn (no tool call).
            msgs.append(_Msg("ai", content=f"reasoning {i}"))
        else:
            tool = draw(st.sampled_from(_TOOL_NAMES))
            msgs.append(_Msg("ai", tool_calls=[{"name": tool, "args": {}, "id": f"id{i}"}]))
            msgs.append(_Msg("tool", name=tool, content=f"{tool}#{i}"))
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# Property 14, facet 1 — bounded, subsequence, deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 14: prune_messages is bounded (<= ceiling), a subsequence of the input, and deterministic.
@settings(max_examples=300, deadline=None)
@given(cfg=configs(), msgs=histories())
def test_property_14_bounded_subsequence_deterministic(cfg, msgs):
    """Feature: adaptive-opportunity-engine, Property 14 (bounded/subsequence/
    deterministic): the result is never longer than ``prune_max_messages``, is a
    subsequence of the input in original order, and is identical on repeated calls.

    Validates: Requirements 7.1, 7.2, 7.3
    """
    result = prune_messages(msgs, cfg)

    # (a) Bounded.
    assert len(result) <= cfg.prune_max_messages

    # (b) Subsequence (identity-preserving, original order).
    it = iter(msgs)
    for m in result:
        assert any(m is x for x in it), "result is not an ordered subsequence of the input"

    # (d) Deterministic — same object identities, same order.
    again = prune_messages(msgs, cfg)
    assert [id(x) for x in again] == [id(x) for x in result]


# ─────────────────────────────────────────────────────────────────────────────
# Property 14, facet 2 — no orphaned tool result (pairing preserved)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 14: every retained tool result is preceded by an assistant message that issued a call for it (no orphan).
@settings(max_examples=300, deadline=None)
@given(cfg=configs(), msgs=histories())
def test_property_14_no_orphaned_tool_result(cfg, msgs):
    """Feature: adaptive-opportunity-engine, Property 14 (pairing): a retained
    ToolMessage is never orphaned — some assistant message earlier in the result
    issued a tool call for that tool name.

    Validates: Requirements 7.2
    """
    result = prune_messages(msgs, cfg)
    called_tool_names = set()
    for m in result:
        if m.type == "ai":
            for tc in m.tool_calls:
                called_tool_names.add(tc.get("name"))
        elif m.type == "tool":
            assert m.name in called_tool_names, f"orphaned tool result: {m.name}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 14, facet 3 — preserving: system kept; fits-path is identity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 14: the system message is preserved when present, and a history already within the ceiling is returned unchanged.
@settings(max_examples=300, deadline=None)
@given(cfg=configs(), msgs=histories())
def test_property_14_preserving_system_and_identity(cfg, msgs):
    """Feature: adaptive-opportunity-engine, Property 14 (preserving): the system
    message survives pruning when present, and when the history already fits within
    the ceiling it is returned unchanged.

    Validates: Requirements 7.1, 7.2
    """
    result = prune_messages(msgs, cfg)

    had_system = any(m.type == "system" for m in msgs)
    if had_system:
        assert any(m.type == "system" for m in result), "system message was dropped"

    if len(msgs) <= cfg.prune_max_messages:
        # Fits within the ceiling → returned unchanged (identity-preserving list).
        assert [id(x) for x in result] == [id(x) for x in msgs]


# ─────────────────────────────────────────────────────────────────────────────
# Property 14, facet 4 — latest-per-tool preserved when the retained set fits
# ─────────────────────────────────────────────────────────────────────────────

# A generous config whose ceiling always holds the full keep set for these
# histories (system + <=4 distinct-tool groups + recent turns), isolating the
# latest-per-tool preservation property from the hard-ceiling tradeoff.
_GENEROUS_CFG = OpportunityConfig(
    watch_cap=DEFAULT_WATCH_CAP,
    session_max_turns=DEFAULT_SESSION_MAX_TURNS,
    session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
    size_factor_a_plus=DEFAULT_SIZE_FACTOR_A_PLUS,
    size_factor_b_continuation=DEFAULT_SIZE_FACTOR_B_CONTINUATION,
    size_factor_scalp=DEFAULT_SIZE_FACTOR_SCALP,
    lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
    heartbeat_enabled=DEFAULT_HEARTBEAT_ENABLED,
    heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
    heartbeat_max=DEFAULT_HEARTBEAT_MAX,
    prune_keep_recent_turns=4,
    prune_max_messages=30,
)


# Feature: adaptive-opportunity-engine, Property 14: under a ceiling that holds the full keep set, the most-recent result of every tool is preserved.
@settings(max_examples=300, deadline=None)
@given(msgs=histories())
def test_property_14_latest_per_tool_preserved_when_fits(msgs):
    """Feature: adaptive-opportunity-engine, Property 14 (preserving latest-per-tool):
    with a ceiling generous enough to hold the full retained set (system + the <=4
    distinct-tool groups + recent turns), the most-recent usable result of every
    tool present before pruning is retained. (When a tiny ceiling forces a choice,
    the hard bound wins — that tradeoff is exercised by the bounded/subsequence
    facet, not here.)

    Validates: Requirements 7.2
    """
    result = prune_messages(msgs, _GENEROUS_CFG)
    assert len(result) <= _GENEROUS_CFG.prune_max_messages

    # Latest result content per tool in the input.
    latest_content = {}
    for m in msgs:
        if m.type == "tool" and m.name is not None:
            latest_content[m.name] = m.content
    kept_contents = {m.content for m in result if m.type == "tool"}
    for name, content in latest_content.items():
        assert content in kept_contents, f"latest result for {name} was not preserved"


# ─────────────────────────────────────────────────────────────────────────────
# Property 14, facet 5 — totality on degraded input
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 14: prune_messages never raises on a None / non-list / empty history.
@settings(max_examples=50, deadline=None)
@given(cfg=configs(), degraded=st.sampled_from([None, "nope", 123, {}, ()]))
def test_property_14_total_on_degraded_input(cfg, degraded):
    """Feature: adaptive-opportunity-engine, Property 14 (totality): a ``None`` /
    non-list / empty history yields an empty list without raising.

    Validates: Requirements 7.1
    """
    assert prune_messages(degraded, cfg) == []
    assert prune_messages([], cfg) == []
