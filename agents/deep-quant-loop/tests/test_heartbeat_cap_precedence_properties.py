"""R6/R3 preservation property test — bounded-hunt caps retain precedence.

Feature: deep-quant-runtime-hardening

Property 16 (Preservation), Python ``graph.tool_node`` finalize seam +
``graph.force_terminal``:

    For any loop state where a Watch_Cap OR Session_Budget bound holds, the R6
    heartbeat gate is INERT and can never produce an unbounded loop:

      * ``opportunity.termination_reason`` is UNCHANGED by the presence of the
        heartbeat gate — the gate never mutates the state consulted by
        ``termination_reason`` (``watch_cycles`` / ``session_turns`` /
        ``session_started_at``), so a capped state still reports its termination
        reason after a turn through ``tool_node``.

      * When a cap holds, the heartbeat gate does NOT block a terminal HOLD: on a
        heartbeat wake with an unresolved core AND a fired cap, ``tool_node``
        STILL commits the terminal decision (caps retain absolute precedence over
        the gate).

      * ``force_terminal(state)`` still commits a terminal stand-aside decision
        citing the ``termination_reason`` — the safety net that closes the
        analyze -> watch -> invalidate -> re-watch loop is unaffected by the gate.

    Validates: Requirements 6.2, 7.4.

This test reuses the sibling R6 tests' (``test_heartbeat_gate_properties.py`` /
``test_heartbeat_gate_bug_properties.py``) stub message classes, ``FakeToolNode``
(no network), state-builder conventions, ``sys.path`` setup, and the no-op
journal patch, and drives ``graph.tool_node`` over ``graph._base_tool_node``.
"""

import json
import os
import sys
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
from graph import tool_node, force_terminal  # noqa: E402


# ── The CORE data tools whose first-pass acquisition R6 gates ─────────────────
CORE_TOOLS = tuple(sorted(graph.CORE_DATA_TOOL_NAMES))

# The resolved engine configuration the graph consults (default Watch_Cap 3,
# Session_Budget 40 turns / 3600 wall-secs unless env-overridden).
_CFG = graph._OPPORTUNITY_CFG
_WATCH_CAP = _CFG.watch_cap
_SESSION_MAX_TURNS = _CFG.session_max_turns

# A fixed, far-future clock so the wall-clock arm of the Session_Budget fires
# deterministically for a state stamped at the epoch (elapsed >> any budget) and
# is irrelevant for the watch-cap / turn-budget arms.
_FIXED_NOW = 10_000_000_000.0


# ── Lightweight stub message objects (mirror the sibling R6 tests) ────────────
class StubAIMessage:
    """Assistant message carrying classified tool calls."""

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
    """In-memory replacement for the real ToolNode (no network I/O)."""

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {
            "messages": [
                StubToolMessage(content="ok", name=c.get("name")) for c in calls
            ]
        }


# ── Core-tool result builders (usable / failing) ──────────────────────────────
def _usable_core(name):
    """A usable core-tool result — real data, no error/unavailable marker."""
    return StubToolMessage(
        content=json.dumps(
            {"tool": name, "trend_score": 20, "current_price": 24250.0, "atr_14": 60.0}
        ),
        name=name,
    )


def _failing_core(name):
    """A core tool still FAILING (hard error → not usable, not resolved)."""
    return StubToolMessage(
        content=json.dumps({"error": f"{name}: upstream candle store fault"}),
        name=name,
    )


def _declare_hold(conviction, call_id="declare_hb"):
    """A model-declared stand-aside HOLD (conviction > 0)."""
    args = {
        "action": "HOLD",
        "conviction_score": conviction,
        "setup_validation": "Tier: stand_aside. Chop, no edge — standing aside for now.",
        "execution_plan": "HOLD — no trade taken; reassess on the next pulse.",
    }
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "declare_trade", "args": args}],
        extraction_status={call_id: "ok"},
    )


def _pending_watch(call_id="watch_hb"):
    """A pending watch_price_condition re-arm (the loop-forming call force_terminal
    answers and supersedes with a terminal stand-aside)."""
    return StubAIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": "watch_price_condition",
                "args": {
                    "symbol": "NIFTY",
                    "timeframe": "15m",
                    "price_level": 24250.0,
                    "direction": "above",
                },
            }
        ],
        extraction_status={call_id: "ok"},
    )


def _build_hold_messages(failing_tools, conviction):
    """Message history: one result per core tool (failing ones error, the rest
    usable) followed by the model's declare_trade HOLD (the last message)."""
    msgs = [
        (_failing_core(n) if n in failing_tools else _usable_core(n))
        for n in CORE_TOOLS
    ]
    msgs.append(_declare_hold(conviction))
    return msgs


# ── Cap-firing state builders ─────────────────────────────────────────────────
# Three independent ways a bounded-hunt cap can hold. Each yields a state for
# which ``opportunity.termination_reason`` is non-None.
def _apply_cap(state, cap_kind, over):
    """Stamp ``state`` so exactly the requested bounded-hunt cap holds.

    ``over`` (>= 0) pushes the relevant counter strictly past its bound.
      * "watch"  -> watch_cycles >= watch_cap        (reason: watch-cap-reached)
      * "turns"  -> session_turns >= session_max_turns, watch fresh
                    (reason: session-budget-exhausted)
      * "wall"   -> session_started_at at the epoch so elapsed >> budget, watch
                    and turns fresh (reason: session-budget-exhausted)
    """
    if cap_kind == "watch":
        state["watch_cycles"] = _WATCH_CAP + over
    elif cap_kind == "turns":
        state["watch_cycles"] = 0
        state["session_turns"] = _SESSION_MAX_TURNS + over
    else:  # "wall"
        state["watch_cycles"] = 0
        state["session_turns"] = 0
        state["session_started_at"] = 0.0  # epoch → elapsed vs any sane now >> budget
    return state


def _state(messages, cap_kind, over):
    """A heartbeat-resume loop state with market_data_seen latched and exactly one
    bounded-hunt cap fired."""
    state = {
        "messages": messages,
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": True,                       # latched by a prior usable read
        "last_resume_kind": opportunity.RESUME_HEARTBEAT,
        "watch_cycles": 0,
        "heartbeat_count": 2,
        "session_turns": 0,
        "session_started_at": None,
    }
    return _apply_cap(state, cap_kind, over)


def _reason(state):
    """``termination_reason`` at the fixed clock (deterministic across arms)."""
    return opportunity.termination_reason(state, _CFG, _FIXED_NOW)


def _cap_relevant(state):
    """The subset of state consulted by ``termination_reason`` — the fields the
    heartbeat gate must never mutate."""
    return {
        "watch_cycles": state.get("watch_cycles"),
        "session_turns": state.get("session_turns"),
        "session_started_at": state.get("session_started_at"),
    }


def _run_tool_node(state):
    """Drive ``tool_node`` with the network-free FakeToolNode and a no-op journal."""
    with mock.patch.object(graph, "_base_tool_node", FakeToolNode()), \
            mock.patch.object(graph.journal, "record_decision", lambda *a, **k: None):
        return tool_node(state)


def _run_force_terminal(state):
    """Drive ``force_terminal`` with a no-op journal (finalize is best-effort
    journaled; keep the property test hermetic)."""
    with mock.patch.object(graph.journal, "record_decision", lambda *a, **k: None):
        return force_terminal(state)


# ── Sanity: each cap builder actually fires a distinct/expected reason ────────
def test_cap_builders_fire_expected_reason():
    watch_state = _state(_build_hold_messages(set(CORE_TOOLS), 60), "watch", 0)
    assert _reason(watch_state) == "watch-cap-reached"

    turns_state = _state(_build_hold_messages(set(CORE_TOOLS), 60), "turns", 0)
    assert _reason(turns_state) == "session-budget-exhausted"

    wall_state = _state(_build_hold_messages(set(CORE_TOOLS), 60), "wall", 0)
    assert _reason(wall_state) == "session-budget-exhausted"


# ══════════════════════════════════════════════════════════════════════════════
# Property 16 (preservation)
# Feature: deep-quant-runtime-hardening, Property 16: bounded-hunt caps retain
# precedence — for any capped state, termination_reason is unchanged by the
# heartbeat gate and both tool_node and force_terminal still commit a terminal
# decision (the gate can never produce an unbounded loop).
# Validates: Requirements 6.2, 7.4.
# ══════════════════════════════════════════════════════════════════════════════
@settings(max_examples=200, deadline=None)
@given(
    cap_kind=st.sampled_from(("watch", "turns", "wall")),
    over=st.integers(min_value=0, max_value=997),
    conviction=st.integers(min_value=1, max_value=100),
    # >= 1 core tool still failing → the core acquisition is unresolved, so the
    # heartbeat gate WOULD fire were it not for the fired cap.
    failing=st.lists(
        st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True
    ),
)
def test_bounded_hunt_caps_retain_precedence(cap_kind, over, conviction, failing):
    failing_set = set(failing)
    state = _state(_build_hold_messages(failing_set, conviction), cap_kind, over)

    # Precondition: a bounded-hunt cap genuinely holds for this state.
    reason_before = _reason(state)
    assert reason_before is not None, (
        f"fixture did not fire a cap (cap_kind={cap_kind}, over={over})"
    )
    cap_fields_before = _cap_relevant(state)

    # Drive a heartbeat-woken finalize turn (unresolved core + HOLD).
    update = _run_tool_node(state)

    # (1) The gate never mutated the state consulted by termination_reason, and
    #     the returned update never overrides those fields to un-fire the cap:
    #     the reason is UNCHANGED by the presence of the gate.
    assert _cap_relevant(state) == cap_fields_before, (
        "heartbeat gate mutated cap-relevant state in place "
        f"(cap_kind={cap_kind}): {cap_fields_before} -> {_cap_relevant(state)}"
    )
    merged = {**state, **update}
    assert _reason(merged) == reason_before, (
        "termination_reason changed across the gated turn "
        f"(cap_kind={cap_kind}): {reason_before} -> {_reason(merged)}"
    )

    # (2) With a cap fired, the heartbeat gate does NOT block the terminal HOLD —
    #     tool_node STILL commits the decision (caps retain absolute precedence).
    assert update.get("decision") is not None, (
        "terminal HOLD was blocked by the heartbeat gate despite a fired "
        f"bounded-hunt cap ({reason_before}, cap_kind={cap_kind}, "
        f"failing={sorted(failing_set)})"
    )

    # (3) force_terminal still commits a terminal stand-aside citing the reason —
    #     the loop-closing safety net is unaffected by the gate (no unbounded loop).
    ft_state = _state([*_build_hold_messages(failing_set, conviction)[:-1], _pending_watch()], cap_kind, over)
    ft_update = _run_force_terminal(ft_state)
    decision = ft_update.get("decision")
    assert decision is not None, "force_terminal failed to commit a terminal decision"
    assert decision.get("action") == "HOLD"
    assert decision.get("reason") == reason_before, (
        "force_terminal committed a decision that does not cite the bounded-hunt "
        f"reason (expected {reason_before}, got {decision.get('reason')!r})"
    )


# ── Focused sub-properties (make each clause explicit) ────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    cap_kind=st.sampled_from(("watch", "turns", "wall")),
    over=st.integers(min_value=0, max_value=997),
    conviction=st.integers(min_value=1, max_value=100),
    failing=st.lists(
        st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True
    ),
)
def test_termination_reason_unchanged_by_gate(cap_kind, over, conviction, failing):
    """The heartbeat gate never mutates the cap-relevant state, so a capped state
    still reports the SAME termination reason after a turn through tool_node."""
    state = _state(_build_hold_messages(set(failing), conviction), cap_kind, over)
    reason_before = _reason(state)
    assert reason_before is not None
    update = _run_tool_node(state)
    assert _reason({**state, **update}) == reason_before


@settings(max_examples=100, deadline=None)
@given(
    cap_kind=st.sampled_from(("watch", "turns", "wall")),
    over=st.integers(min_value=0, max_value=997),
    conviction=st.integers(min_value=1, max_value=100),
    failing=st.lists(
        st.sampled_from(CORE_TOOLS), min_size=1, max_size=len(CORE_TOOLS), unique=True
    ),
)
def test_force_terminal_still_commits_under_cap(cap_kind, over, conviction, failing):
    """force_terminal commits a terminal stand-aside HOLD citing the bounded-hunt
    reason for any capped state — the loop-closing safety net is preserved."""
    messages = [*_build_hold_messages(set(failing), conviction)[:-1], _pending_watch()]
    state = _state(messages, cap_kind, over)
    expected_reason = _reason(state)
    update = _run_force_terminal(state)
    decision = update.get("decision")
    assert decision is not None
    assert decision.get("action") == "HOLD"
    assert decision.get("conviction_score") == 0
    assert decision.get("reason") == expected_reason
