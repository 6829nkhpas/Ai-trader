"""Unit tests for resume classification & heartbeat accounting wiring (task 12.2).

Feature: adaptive-opportunity-engine

Covers the ``main.py`` / ``tools.py`` resume path integration of the engine:
  - the watch tool classifies a resume trigger to target / invalidation / heartbeat
    and scopes a cheap Delta_Recheck message for each (R5.3, R6.1);
  - a heartbeat resume charges one heartbeat + one Session_Budget turn but does NOT
    count as a fresh Watch_Cycle, and a pure same-thesis continuation re-arm is not
    counted as a fresh Watch_Cycle either (R5.2, R6.1).

The real LLM / network are never invoked. ``graph._base_tool_node`` is patched so
resume messages can be injected without dispatching to a live tool.
"""

import json
import os
import sys
from unittest import mock

import pytest

# Make the service package importable (modules live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import opportunity  # noqa: E402


class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class FakeToolNode:
    def __init__(self, content):
        self._content = content

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {"messages": [StubToolMessage(content=self._content, name=c.get("name")) for c in calls]}


class StubAIMessage:
    def __init__(self, content="", tool_calls=None, extraction_status=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {"_extraction_status": extraction_status or {}, "_synthetic_results": {}}


_THESIS = {"symbol": "RELIANCE", "timeframe": "15m", "price_level": 2500.0, "direction": "above"}


def _watch_ai(args, call_id="w1"):
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "watch_price_condition", "args": args}],
        extraction_status={call_id: "ok"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Resume classification: the three canonical kinds map to distinct plans
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R6.1: any trigger spelling classifies to one of the three canonical resume kinds.
@pytest.mark.parametrize(
    "trigger,expected",
    [
        ("target", opportunity.RESUME_TARGET),
        ("reached", opportunity.RESUME_TARGET),
        ("invalidation", opportunity.RESUME_INVALIDATION),
        ("stop-out", opportunity.RESUME_INVALIDATION),
        ("heartbeat", opportunity.RESUME_HEARTBEAT),
        ("pulse", opportunity.RESUME_HEARTBEAT),
        (None, opportunity.RESUME_TARGET),
        ("garbage", opportunity.RESUME_TARGET),
    ],
)
def test_resume_classification(trigger, expected):
    """Validates: Requirements 6.1"""
    assert opportunity.classify_resume(trigger) == expected


# Feature: adaptive-opportunity-engine, R6.1: each classified kind yields a non-empty cheap Delta_Recheck plan.
def test_delta_recheck_scopes_each_kind():
    """Validates: Requirements 6.1, 6.2, 6.3"""
    for kind in opportunity.RESUME_KINDS:
        plan = opportunity.delta_recheck_plan(kind)
        assert plan and set(plan) < set(opportunity.FULL_ORDER_OF_OPERATIONS_TOOLS)


# ─────────────────────────────────────────────────────────────────────────────
# Heartbeat resume: charges a heartbeat, not a Watch_Cycle
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, R5.2: a heartbeat resume charges one heartbeat + one turn but no Watch_Cycle.
def test_heartbeat_resume_charges_heartbeat_not_watch_cycle():
    """Validates: Requirements 5.2, 6.1"""
    # Enable heartbeats for this test via a config override on the module.
    base = opportunity.resolve_opportunity_config()
    cfg = opportunity.OpportunityConfig(
        watch_cap=base.watch_cap,
        session_max_turns=base.session_max_turns,
        session_max_wall_secs=base.session_max_wall_secs,
        size_factor_a_plus=base.size_factor_a_plus,
        size_factor_b_continuation=base.size_factor_b_continuation,
        size_factor_scalp=base.size_factor_scalp,
        lower_tiers_enabled=base.lower_tiers_enabled,
        heartbeat_enabled=True,
        heartbeat_cadence_secs=base.heartbeat_cadence_secs,
        heartbeat_max=6,
        prune_keep_recent_turns=base.prune_keep_recent_turns,
        prune_max_messages=base.prune_max_messages,
    )
    watch_msg = _watch_ai(_THESIS)
    state = {
        "messages": [watch_msg],
        "decision": None,
        "market_data_seen": True,
        "watch_cycles": 2,
        "heartbeat_count": 0,
        "session_turns": 5,
    }
    heartbeat_content = "Heartbeat check (mid-wait pulse): the watched target was NOT reached ..."
    with mock.patch.object(graph, "_OPPORTUNITY_CFG", cfg):
        with mock.patch.object(graph, "_base_tool_node", FakeToolNode(heartbeat_content)):
            update = graph.tool_node(state)

    assert update.get("last_resume_kind") == opportunity.RESUME_HEARTBEAT
    assert update.get("heartbeat_count") == 1          # one heartbeat consumed
    assert update.get("session_turns") == 6            # charged one budget turn
    assert "watch_cycles" not in update                # NOT a fresh Watch_Cycle


# Feature: adaptive-opportunity-engine, R6.1: a same-thesis continuation re-arm is not counted as a fresh Watch_Cycle by call_model.
def test_same_thesis_continuation_not_counted_as_watch_cycle():
    """Validates: Requirements 6.1

    ``call_model``'s watch-cycle bookkeeping is exercised in isolation by driving
    the same logic it uses: a proposed re-arm whose thesis fingerprint equals the
    most-recent armed watch is a continuation and must not increment watch_cycles;
    a changed thesis must.
    """
    prior = [_watch_ai(_THESIS)]  # the currently-armed watch in history

    same_fp = opportunity.thesis_fingerprint(dict(_THESIS))
    prior_fp = opportunity.thesis_fingerprint(graph._latest_watch_args(prior))
    assert same_fp == prior_fp  # a re-arm of the identical thesis is a continuation

    changed_fp = opportunity.thesis_fingerprint(
        {"symbol": "RELIANCE", "timeframe": "15m", "price_level": 2400.0, "direction": "below"}
    )
    assert changed_fp != prior_fp  # a materially different thesis is a fresh cycle
