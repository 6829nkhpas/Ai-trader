"""R6 verification property test — the heartbeat gate blocks a premature stand-aside.

Feature: deep-quant-runtime-hardening (bugfix)

Property 14 (Expected Behavior), Python ``graph.tool_node`` finalize seam —
"Heartbeat must not precipitate a premature stand-aside":

    On a heartbeat resume (``last_resume_kind == RESUME_HEARTBEAT``), a
    model-declared non-directional HOLD / stand-aside must NOT be committed as
    the terminal decision WHILE the CORE data acquisition (regime, consensus,
    relative-strength, session, order-flow) is still unresolved (at least one
    core tool has only ever produced a hard error) AND no bounded-hunt cap has
    fired. In that window the loop must keep gathering the core tools rather
    than commit an artifact stand-aside.

    Conversely, once the core acquisition IS resolved (every core tool has
    returned usable data OR an explicit Unavailable_Marker) OR a bounded-hunt
    cap HAS fired (Watch_Cap / Session_Budget), the terminal HOLD IS permitted
    (the decision commits). A directional BUY / SELL is NEVER gated — it commits
    even on a heartbeat wake with the core still unresolved.

    Validates: Requirements 6.1, 6.2.

This is the FIXED-behavior counterpart to task 13's bug-condition exploration
test (``test_heartbeat_gate_bug_properties.py``). It reuses that test's stub
message classes, ``FakeToolNode`` (no network), state-builder conventions, and
``sys.path`` setup, and drives ``graph.tool_node`` over ``graph._base_tool_node``
patched with the in-memory ``FakeToolNode``.
"""

import json
import os
import sys
import time
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` / ``import opportunity`` resolve as every sibling test expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import opportunity  # noqa: E402
from graph import tool_node  # noqa: E402


# ── The five CORE data tools whose first-pass acquisition R6 gates ───────────
CORE_TOOLS = tuple(sorted(graph.CORE_DATA_TOOL_NAMES))

# Non-heartbeat resume kinds (plus ``None``) — the gate is inert for all of these.
NON_HEARTBEAT_RESUMES = (opportunity.RESUME_TARGET, opportunity.RESUME_INVALIDATION, None)

# A watch_cycles value guaranteed to exceed any sane Watch_Cap (default 3) so a
# bounded-hunt cap has definitively fired.
_CAP_FIRED_WATCH_CYCLES = 999


# ── Lightweight stub message objects (mirror the task-13 bug test) ────────────
class StubAIMessage:
    """Assistant message carrying classified tool calls.

    ``tool_node`` reads ``.tool_calls`` and ``.additional_kwargs``
    (``_extraction_status`` / ``_synthetic_results``) and ``.content``;
    ``.type == "ai"`` keeps it out of the tool-message scanners.
    """

    def __init__(self, content="", tool_calls=None, extraction_status=None, synthetic=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {
            "_extraction_status": extraction_status or {},
            "_synthetic_results": synthetic or {},
        }


class StubToolMessage:
    """Tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class FakeToolNode:
    """In-memory replacement for the real ToolNode (no network I/O).

    Returns one synthetic ToolMessage per pending call so ``tool_node`` can
    finalize without dispatching a real ``declare_trade`` HTTP call.
    """

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {
            "messages": [
                StubToolMessage(content="ok", name=c.get("name")) for c in calls
            ]
        }


# ── Core-tool result builders (usable / explicitly-unavailable / failing) ─────
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
}


def _declare(action, conviction, call_id="declare_hb"):
    """A model-declared trade. ``action`` is BUY / SELL (directional) or HOLD.

    Conviction is > 0 (a model-declared decision, NOT the conviction-0
    force_terminal path).
    """
    args = {
        "action": action,
        "conviction_score": conviction,
        "setup_validation": (
            "Tier: stand_aside. Chop, no edge — standing aside for now."
            if action == "HOLD"
            else f"Tier: b_continuation. Directional {action} setup with edge."
        ),
        "execution_plan": (
            "HOLD — no trade taken; reassess on the next pulse."
            if action == "HOLD"
            else f"{action} at market; stop and target attached."
        ),
    }
    if action in ("BUY", "SELL"):
        # Directional trades carry structured execution levels.
        args.update({"entry": 24250.0, "stop_loss": 24150.0, "take_profit": 24450.0, "atr_14": 60.0})
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "declare_trade", "args": args}],
        extraction_status={call_id: "ok"},
    )


def _build_messages(core_statuses, action, conviction):
    """Synthesize a message history: one result per core tool (per its status)
    followed by the model's declare_trade. The declare must be the LAST message
    because ``tool_node`` reads ``state["messages"][-1]``."""
    msgs = [_CORE_MSG_BUILDERS[core_statuses[name]](name) for name in CORE_TOOLS]
    msgs.append(_declare(action, conviction))
    return msgs


def _state(messages, resume_kind, cap_fired):
    """A loop state on ``resume_kind`` with market_data_seen already latched and
    the bounded-hunt caps either fired (``watch_cycles`` huge) or fresh."""
    return {
        "messages": messages,
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": True,                    # latched by a prior usable read
        "last_resume_kind": resume_kind,
        "watch_cycles": _CAP_FIRED_WATCH_CYCLES if cap_fired else 0,
        "heartbeat_count": 2,
        "session_turns": 0,
        "session_started_at": None,
    }


def _run_tool_node(state):
    """Drive ``tool_node`` with the network-free FakeToolNode and a no-op journal
    (finalize is best-effort journaled; keep the property test hermetic)."""
    with mock.patch.object(graph, "_base_tool_node", FakeToolNode()), \
            mock.patch.object(graph.journal, "record_decision", lambda *a, **k: None):
        return tool_node(state)


# ── Sanity: the fixture models the gate's inputs honestly ─────────────────────
def test_core_predicate_matches_synthesized_statuses():
    """``_core_acquisition_resolved`` is True exactly when no core tool is failing."""
    all_usable = _build_messages({n: "usable" for n in CORE_TOOLS}, "HOLD", 60)
    assert graph._core_acquisition_resolved(all_usable) is True

    mixed_resolved = _build_messages(
        {CORE_TOOLS[0]: "unavailable", **{n: "usable" for n in CORE_TOOLS[1:]}}, "HOLD", 60
    )
    assert graph._core_acquisition_resolved(mixed_resolved) is True

    one_failing = _build_messages(
        {CORE_TOOLS[0]: "failing", **{n: "usable" for n in CORE_TOOLS[1:]}}, "HOLD", 60
    )
    assert graph._core_acquisition_resolved(one_failing) is False


# ══════════════════════════════════════════════════════════════════════════════
# Property 14 (expected behavior)
# Feature: deep-quant-runtime-hardening, Property 14: heartbeat gate blocks a
# premature stand-aside — HOLD on a heartbeat with unresolved core and no cap →
# loop continues (no decision); core resolved OR cap fired → terminal decision
# permitted; a directional BUY/SELL is never gated.
# Validates: Requirements 6.1, 6.2.
# ══════════════════════════════════════════════════════════════════════════════
@settings(max_examples=200, deadline=None)
@given(
    resume_kind=st.sampled_from([opportunity.RESUME_HEARTBEAT, *NON_HEARTBEAT_RESUMES]),
    core_statuses=st.fixed_dictionaries(
        {name: st.sampled_from(("usable", "unavailable", "failing")) for name in CORE_TOOLS}
    ),
    cap_fired=st.booleans(),
    action=st.sampled_from(("HOLD", "BUY", "SELL")),
    conviction=st.integers(min_value=1, max_value=100),
)
def test_heartbeat_gate_blocks_premature_standaside(
    resume_kind, core_statuses, cap_fired, action, conviction
):
    messages = _build_messages(core_statuses, action, conviction)
    state = _state(messages, resume_kind, cap_fired)
    update = _run_tool_node(state)
    committed = update.get("decision") is not None

    core_resolved = all(status != "failing" for status in core_statuses.values())
    is_directional = action in ("BUY", "SELL")
    is_heartbeat = resume_kind == opportunity.RESUME_HEARTBEAT

    # The gate fires ONLY for a non-directional HOLD, on a heartbeat wake, while
    # the core acquisition is unresolved and no bounded-hunt cap has fired.
    gated = is_heartbeat and (not is_directional) and (not core_resolved) and (not cap_fired)

    if gated:
        assert not committed, (
            "premature stand-aside committed on a heartbeat wake while core "
            f"acquisition was unresolved and no cap had fired "
            f"(core_statuses={core_statuses}, conviction={conviction})"
        )
    else:
        assert committed, (
            "terminal decision was NOT permitted although the gate should be "
            f"inert (resume_kind={resume_kind!r}, directional={is_directional}, "
            f"core_resolved={core_resolved}, cap_fired={cap_fired}, "
            f"core_statuses={core_statuses})"
        )


# ── Focused sub-properties (make each clause of the property explicit) ────────
@settings(max_examples=100, deadline=None)
@given(
    conviction=st.integers(min_value=1, max_value=100),
    failing=st.lists(st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True),
)
def test_heartbeat_hold_unresolved_core_no_cap_is_not_committed(conviction, failing):
    """HOLD on a heartbeat with >=1 core tool still failing and no cap → NOT committed."""
    statuses = {n: ("failing" if n in failing else "usable") for n in CORE_TOOLS}
    state = _state(_build_messages(statuses, "HOLD", conviction), opportunity.RESUME_HEARTBEAT, cap_fired=False)
    assert _run_tool_node(state).get("decision") is None


@settings(max_examples=100, deadline=None)
@given(
    conviction=st.integers(min_value=1, max_value=100),
    resolved_kinds=st.fixed_dictionaries(
        {name: st.sampled_from(("usable", "unavailable")) for name in CORE_TOOLS}
    ),
)
def test_heartbeat_hold_core_resolved_is_committed(conviction, resolved_kinds):
    """HOLD on a heartbeat once every core tool has resolved (usable OR explicitly
    unavailable) → the terminal decision IS permitted."""
    state = _state(_build_messages(resolved_kinds, "HOLD", conviction), opportunity.RESUME_HEARTBEAT, cap_fired=False)
    assert _run_tool_node(state).get("decision") is not None


@settings(max_examples=100, deadline=None)
@given(
    conviction=st.integers(min_value=1, max_value=100),
    failing=st.lists(st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True),
)
def test_heartbeat_hold_unresolved_core_but_cap_fired_is_committed(conviction, failing):
    """HOLD on a heartbeat with unresolved core BUT a fired bounded-hunt cap →
    the terminal decision IS permitted (caps retain absolute precedence)."""
    statuses = {n: ("failing" if n in failing else "usable") for n in CORE_TOOLS}
    state = _state(_build_messages(statuses, "HOLD", conviction), opportunity.RESUME_HEARTBEAT, cap_fired=True)
    assert _run_tool_node(state).get("decision") is not None


@settings(max_examples=100, deadline=None)
@given(
    conviction=st.integers(min_value=1, max_value=100),
    action=st.sampled_from(("BUY", "SELL")),
    failing=st.lists(st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True),
)
def test_heartbeat_directional_is_never_gated(conviction, action, failing):
    """A directional BUY/SELL is NEVER gated — it commits even on a heartbeat
    wake with the core still unresolved and no cap fired."""
    statuses = {n: ("failing" if n in failing else "usable") for n in CORE_TOOLS}
    state = _state(_build_messages(statuses, action, conviction), opportunity.RESUME_HEARTBEAT, cap_fired=False)
    decision = _run_tool_node(state).get("decision")
    assert decision is not None
    assert decision.get("action") == action
