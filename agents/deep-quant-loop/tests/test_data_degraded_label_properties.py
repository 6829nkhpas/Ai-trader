"""R6 verification property test — degraded terminal decisions are labeled.

Feature: deep-quant-runtime-hardening (bugfix)

Property 15 (Expected Behavior), Python ``graph._finalize_decision`` chokepoint —
"When a terminal decision is reached on degraded data, the agent labels it
data-degraded":

    Every terminal decision funnels through ``_finalize_decision`` (the single
    finalize chokepoint for the validated declare, the data-gating HOLD, the
    forced HOLD, and the bounded-hunt force_terminal). When a decision is
    committed WHILE the CORE data acquisition (regime, consensus,
    relative-strength, session, order-flow) is still unresolved — at least one
    core tool has only ever produced a hard error or was never called —
    ``_finalize_decision`` stamps ``decision["data_degraded"] = True`` AND the
    defensibility record carries a degraded note naming exactly the still-
    unresolved core tools (``data_degraded`` / ``data_degraded_note`` on the
    record, plus the note appended to the record summary).

    Conversely, a decision committed once EVERY core tool has resolved (returned
    usable data OR an explicit Unavailable_Marker) is NOT labeled: neither the
    decision nor the record carries ``data_degraded``, and no degraded note is
    appended.

    Validates: Requirements 6.3.

This is a focused Property 15 test: it drives ``graph._finalize_decision(state,
decision)`` directly (the label seam) with ``graph.journal.record_decision``
patched to a no-op so the property stays hermetic (journaling is best-effort).
It reuses the sibling tests' ``sys.path`` setup, stub tool-message convention,
and core-tool result builders (usable / explicitly-unavailable / failing).
"""

import json
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves as every sibling test expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402


# ── The five CORE data tools whose first-pass acquisition R6 gates ───────────
CORE_TOOLS = tuple(sorted(graph.CORE_DATA_TOOL_NAMES))

# A core tool is "resolved" when usable OR explicitly unavailable; "unresolved"
# when still failing (hard error) or absent (never called this run).
RESOLVED_STATUSES = ("usable", "unavailable")
UNRESOLVED_STATUSES = ("failing", "absent")
ALL_STATUSES = RESOLVED_STATUSES + UNRESOLVED_STATUSES


# ── Lightweight stub message object (mirrors the sibling routing/defensibility
# tests). ``_is_tool_message`` matches ``type == 'tool'``; the resolution
# predicates read ``.name`` / ``.content``. ─────────────────────────────────
class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _usable_core(name):
    """A usable core-tool result — real data, no error/unavailable marker."""
    return StubToolMessage(
        content=json.dumps(
            {"tool": name, "trend_score": 20, "current_price": 24250.0, "atr_14": 60.0}
        ),
        name=name,
    )


def _unavailable_core(name):
    """A core tool that RESOLVED as explicitly unavailable (graceful degradation)."""
    return StubToolMessage(
        content=json.dumps({"tool": name, "status": "unavailable"}),
        name=name,
    )


def _failing_core(name):
    """A core tool still FAILING (hard error → not usable, not resolved)."""
    return StubToolMessage(
        content=json.dumps({"error": f"{name}: upstream candle store fault"}),
        name=name,
    )


_CORE_MSG_BUILDERS = {
    "usable": _usable_core,
    "unavailable": _unavailable_core,
    "failing": _failing_core,
    # "absent" contributes no message for that tool.
}


def _messages_for(core_statuses):
    """Synthesize the tool-message history: one result per core tool per its
    status (an ``absent`` tool contributes no message at all)."""
    msgs = []
    for name in CORE_TOOLS:
        status = core_statuses[name]
        if status == "absent":
            continue
        msgs.append(_CORE_MSG_BUILDERS[status](name))
    return msgs


def _decision(action, conviction):
    """A terminal decision dict as the finalize chokepoint receives it."""
    decision = {
        "action": action,
        "source": "declare_trade",
        "conviction_score": conviction,
        "setup_validation": "Tier context for finalize.",
        "execution_plan": "Terminal decision under test.",
    }
    if action in ("BUY", "SELL"):
        decision.update(
            {"entry": 24250.0, "stop_loss": 24150.0, "take_profit": 24450.0, "atr_14": 60.0}
        )
    return decision


def _state(messages):
    """A finalize state — only ``messages`` (plus optional metadata) is read by
    ``_finalize_decision`` / ``build_defensibility_record``."""
    return {
        "messages": messages,
        "mode": "FIND",
        "manual_trade": None,
        "symbol": "NIFTY",
        "timeframe": "10m",
    }


def _finalize(state, decision):
    """Drive ``_finalize_decision`` with a no-op journal so the label seam runs
    hermetically (journaling is best-effort and irrelevant to Property 15)."""
    with mock.patch.object(graph.journal, "record_decision", lambda *a, **k: None):
        graph._finalize_decision(state, decision)
    return decision


def _expected_unresolved(core_statuses):
    """The sorted core-tool names the note must name — every tool NOT resolved."""
    resolved = {n for n in CORE_TOOLS if core_statuses[n] in RESOLVED_STATUSES}
    return sorted(set(CORE_TOOLS) - resolved)


# ── Sanity: the fixture models the label's input honestly ─────────────────────
def test_core_predicate_matches_synthesized_statuses():
    """``_core_acquisition_resolved`` is True exactly when no core tool is
    failing/absent — the precise condition the label keys on."""
    all_usable = _messages_for({n: "usable" for n in CORE_TOOLS})
    assert graph._core_acquisition_resolved(all_usable) is True

    mixed_resolved = _messages_for(
        {CORE_TOOLS[0]: "unavailable", **{n: "usable" for n in CORE_TOOLS[1:]}}
    )
    assert graph._core_acquisition_resolved(mixed_resolved) is True

    one_failing = _messages_for(
        {CORE_TOOLS[0]: "failing", **{n: "usable" for n in CORE_TOOLS[1:]}}
    )
    assert graph._core_acquisition_resolved(one_failing) is False

    one_absent = _messages_for(
        {CORE_TOOLS[0]: "absent", **{n: "usable" for n in CORE_TOOLS[1:]}}
    )
    assert graph._core_acquisition_resolved(one_absent) is False


# ══════════════════════════════════════════════════════════════════════════════
# Property 15
# Feature: deep-quant-runtime-hardening, Property 15: degraded terminal decisions
# are labeled — a decision committed while the core acquisition is unresolved is
# stamped ``data_degraded: True`` with a defensibility note naming the unresolved
# core tools; a decision committed with every core tool resolved is not labeled.
# Validates: Requirements 6.3.
# ══════════════════════════════════════════════════════════════════════════════
@settings(max_examples=200, deadline=None)
@given(
    core_statuses=st.fixed_dictionaries(
        {name: st.sampled_from(ALL_STATUSES) for name in CORE_TOOLS}
    ),
    action=st.sampled_from(("HOLD", "BUY", "SELL")),
    conviction=st.integers(min_value=0, max_value=100),
)
def test_degraded_label_iff_core_unresolved(core_statuses, action, conviction):
    messages = _messages_for(core_statuses)
    decision = _finalize(_state(messages), _decision(action, conviction))

    core_resolved = all(status in RESOLVED_STATUSES for status in core_statuses.values())
    record = decision.get("defensibility")
    assert isinstance(record, dict), "finalize must attach a defensibility record"

    if core_resolved:
        # Not labeled: absent/False on both the decision and the record; no note.
        assert not decision.get("data_degraded"), (
            "a decision committed with all core tools resolved must NOT be "
            f"labeled data_degraded (core_statuses={core_statuses})"
        )
        assert not record.get("data_degraded")
        assert "data_degraded_note" not in record
    else:
        # Labeled: True on the decision AND the record, with a note naming the
        # unresolved core tools.
        assert decision.get("data_degraded") is True, (
            "a decision committed while core acquisition was unresolved must be "
            f"labeled data_degraded (core_statuses={core_statuses})"
        )
        assert record.get("data_degraded") is True
        unresolved = _expected_unresolved(core_statuses)
        expected_note = (
            "decision reached on degraded data (core tools unresolved: "
            f"{', '.join(unresolved)})"
        )
        assert record.get("data_degraded_note") == expected_note
        # Every unresolved core tool is named; every resolved one is not.
        note = record["data_degraded_note"]
        for name in unresolved:
            assert name in note
        summary = record.get("summary")
        if isinstance(summary, str):
            assert summary.endswith(expected_note + ".")


# ── Focused sub-properties (make each clause explicit) ────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    unresolved=st.lists(
        st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True
    ),
    unresolved_kind=st.sampled_from(UNRESOLVED_STATUSES),
    action=st.sampled_from(("HOLD", "BUY", "SELL")),
    conviction=st.integers(min_value=0, max_value=100),
)
def test_unresolved_core_is_labeled_with_note(unresolved, unresolved_kind, action, conviction):
    """>=1 core tool unresolved (failing or absent), rest usable → the decision
    and record are labeled data_degraded and the note names the unresolved set."""
    statuses = {n: (unresolved_kind if n in unresolved else "usable") for n in CORE_TOOLS}
    decision = _finalize(_state(_messages_for(statuses)), _decision(action, conviction))

    assert decision.get("data_degraded") is True
    record = decision["defensibility"]
    assert record.get("data_degraded") is True
    expected = sorted(unresolved)
    expected_note = (
        "decision reached on degraded data (core tools unresolved: "
        f"{', '.join(expected)})"
    )
    assert record.get("data_degraded_note") == expected_note


@settings(max_examples=100, deadline=None)
@given(
    resolved_kinds=st.fixed_dictionaries(
        {name: st.sampled_from(RESOLVED_STATUSES) for name in CORE_TOOLS}
    ),
    action=st.sampled_from(("HOLD", "BUY", "SELL")),
    conviction=st.integers(min_value=0, max_value=100),
)
def test_all_core_resolved_is_not_labeled(resolved_kinds, action, conviction):
    """Every core tool resolved (usable OR explicitly unavailable) → the decision
    is NOT labeled and no degraded note is attached."""
    decision = _finalize(_state(_messages_for(resolved_kinds)), _decision(action, conviction))

    assert not decision.get("data_degraded")
    record = decision["defensibility"]
    assert not record.get("data_degraded")
    assert "data_degraded_note" not in record
