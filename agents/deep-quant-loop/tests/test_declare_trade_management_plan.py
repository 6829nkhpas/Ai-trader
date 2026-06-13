"""Unit tests for ``declare_trade`` management-plan wiring (task 7.2).

Feature: trade-management

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis) covering Requirements 4.1, 4.2, 4.3, 4.4, and 8.5 for the
``declare_trade`` tool in ``tools.py``:

  * 4.1 — ``declare_trade`` accepts an optional ``management_plan`` argument in
    addition to the existing entry / stop-loss / take-profit / atr_14 arguments.
  * 4.2 / 8.5 — when no ``management_plan`` is supplied the tool behaves exactly
    as today (a Single_Target_Trade is committed and forwarded) so management is
    recommended, never forced.
  * 4.3 — when a *valid* ``management_plan`` is supplied the tool forwards the
    plan (with ``management_plan`` in the posted body) and commits.
  * 4.4 — when a *failing* ``management_plan`` is supplied (a risk-violating plan
    or a malformed/unparseable plan) the tool returns a ``TRADE_REJECTED`` reason
    and does NOT forward / commit the trade (``httpx.post`` is never called).

The authoritative Rust Tool_Server is mocked by monkeypatching
``tools.httpx.post`` with a recording fake that returns a 200-like response
(``.raise_for_status()`` is a no-op, ``.json()`` -> ``{"status": "committed"}``),
so no real server is required. The paired passing/failing examples are scored by
the *real* pure-Python ``validator.validate_trade`` inside ``declare_trade`` — the
test does not stub validation.

The sys.path / import pattern mirrors ``tests/test_of_tool_shape.py`` and the
other tool-level unit modules.
"""

import os
import sys

import pytest

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import declare_trade  # noqa: E402

from langchain_core.tools import BaseTool  # noqa: E402


# ── Rust Tool_Server fake ────────────────────────────────────────────────────
class _FakeResponse:
    """A minimal stand-in for an ``httpx.Response`` from a committed declare_trade.

    ``raise_for_status`` is a no-op (a 200-like response) and ``json`` returns a
    committed body, so ``declare_trade`` treats the forward as a successful
    commit rather than a rejection.
    """

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _PostRecorder:
    """A callable that records every ``httpx.post`` call and returns a committed
    fake response, so a test can assert both *whether* the trade was forwarded
    and *what* body was posted."""

    def __init__(self, payload=None):
        self.calls = []
        self._payload = payload if payload is not None else {"status": "committed"}

    def __call__(self, url, json=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(self._payload)

    @property
    def called(self):
        return len(self.calls) > 0


@pytest.fixture
def post_recorder(monkeypatch):
    """Monkeypatch ``tools.httpx.post`` with a recording fake (no real server)."""
    recorder = _PostRecorder()
    monkeypatch.setattr(tools.httpx, "post", recorder)
    return recorder


def _invoke(**kwargs):
    """Call the underlying ``declare_trade`` function past the ``@tool`` wrapper.

    Common required prose args default here so each test only states the levels /
    plan it cares about.
    """
    base = {
        "action": "BUY",
        "conviction_score": 80,
        "setup_validation": "Trend and momentum align; volume confirms the breakout.",
        "execution_plan": "Enter at 100, stop 90, scale out at 120 and 140.",
    }
    base.update(kwargs)
    return declare_trade.func(**base)


# A valid two-leg plan: entry 100, stop 90 (risk = 10), legs at 120 (0.5) and
# 140 (0.5), breakeven at 110, atr unknown. Blended R:R =
# 0.5*(20/10) + 0.5*(40/10) = 1.0 + 2.0 = 3.0 >= the 2.0 default minimum, so this
# plan PASSES the real Trade_Validator.
_VALID_PLAN = {
    "legs": [
        {"target": 120.0, "fraction": 0.5},
        {"target": 140.0, "fraction": 0.5},
    ],
    "breakeven": {"price": 110.0},
}


# ── Requirement 4.1: optional management_plan argument is exposed ─────────────
def test_declare_trade_accepts_optional_management_plan_arg():
    """Validates: Requirements 4.1

    ``declare_trade`` is an ``@tool`` whose argument schema exposes
    ``management_plan`` alongside the existing entry / stop-loss / take-profit /
    atr_14 levels, and ``management_plan`` is optional (omittable).
    """
    assert isinstance(declare_trade, BaseTool)

    args = declare_trade.args
    for field in ("entry", "stop_loss", "take_profit", "atr_14", "management_plan"):
        assert field in args, f"declare_trade should expose '{field}'"

    schema = declare_trade.args_schema
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})
    plan_field = fields["management_plan"]
    is_required = getattr(plan_field, "is_required", None)
    if callable(is_required):
        assert is_required() is False
    else:
        assert getattr(plan_field, "required", False) is False


# ── Requirement 4.2 / 8.5: absent plan behaves as a single-target trade ──────
def test_absent_management_plan_forwards_single_target(post_recorder):
    """Validates: Requirements 4.2, 8.5

    With no ``management_plan`` the tool behaves exactly as today: it forwards a
    single-target bracket to the Rust server (``httpx.post`` IS called), the
    posted body carries the levels but NO ``management_plan`` key, and the tool
    reports a successful commit.
    """
    result = _invoke(entry=100.0, stop_loss=90.0, take_profit=120.0, atr_14=None)

    assert post_recorder.called, "an absent plan must still forward to the server"
    assert len(post_recorder.calls) == 1
    posted = post_recorder.calls[0]["json"]
    assert posted["action"] == "BUY"
    assert posted["entry"] == 100.0
    assert posted["stop_loss"] == 90.0
    assert posted["take_profit"] == 120.0
    # A Single_Target_Trade posts no management_plan field (unchanged behavior).
    assert "management_plan" not in posted

    assert not result.startswith("TRADE_REJECTED")
    assert "declared successfully" in result


# ── Requirement 4.3: valid plan forwards with the plan and commits ───────────
def test_valid_management_plan_forwards_with_plan_and_commits(post_recorder):
    """Validates: Requirements 4.3

    A valid multi-leg ``management_plan`` passes the real Trade_Validator, so the
    tool forwards the trade (``httpx.post`` IS called) with ``management_plan`` in
    the posted body and reports a successful commit.
    """
    result = _invoke(
        entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        atr_14=None,
        management_plan=_VALID_PLAN,
    )

    assert post_recorder.called, "a validation-passing plan must be forwarded"
    assert len(post_recorder.calls) == 1
    posted = post_recorder.calls[0]["json"]
    # The declared plan is forwarded alongside the base bracket (R4.3).
    assert "management_plan" in posted
    assert posted["management_plan"] == _VALID_PLAN

    assert not result.startswith("TRADE_REJECTED")
    assert "declared successfully" in result


# ── Requirement 4.4: a risk-violating plan is rejected, never forwarded ──────
def test_invalid_management_plan_is_rejected_and_not_forwarded(post_recorder):
    """Validates: Requirements 4.4

    A plan with a leg fraction outside ``(0.0, 1.0]`` (here 1.5) fails the
    Trade_Validator. The tool returns a ``TRADE_REJECTED`` reason and never
    forwards/commits the trade (``httpx.post`` is NOT called).
    """
    bad_plan = {
        "legs": [{"target": 120.0, "fraction": 1.5}],
    }
    result = _invoke(
        entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        atr_14=None,
        management_plan=bad_plan,
    )

    assert result.startswith("TRADE_REJECTED")
    assert not post_recorder.called, "a rejected plan must NOT be forwarded"


def test_blended_rr_too_low_plan_is_rejected_and_not_forwarded(post_recorder):
    """Validates: Requirements 4.4

    A plan whose fraction-weighted blended reward-to-risk falls below the
    configured minimum (here a single leg at 105 over a risk of 10 -> blended
    R:R = 0.5, well under the 2.0 default) is rejected, and the trade is not
    forwarded.
    """
    low_rr_plan = {
        "legs": [{"target": 105.0, "fraction": 1.0}],
    }
    result = _invoke(
        entry=100.0,
        stop_loss=90.0,
        take_profit=105.0,
        atr_14=None,
        management_plan=low_rr_plan,
    )

    assert result.startswith("TRADE_REJECTED")
    assert not post_recorder.called


# ── Requirement 4.4: a malformed / unparseable plan is rejected ──────────────
def test_malformed_management_plan_is_rejected_and_not_forwarded(post_recorder):
    """Validates: Requirements 4.4

    A plan dict that cannot be parsed into a well-formed multi-leg plan (here it
    carries no ``legs``) is treated as an invalid plan: the tool returns a
    ``TRADE_REJECTED`` reason and never forwards/commits the trade.
    """
    malformed_plan = {"breakeven": {"price": 110.0}}  # no legs at all
    result = _invoke(
        entry=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        atr_14=None,
        management_plan=malformed_plan,
    )

    assert result.startswith("TRADE_REJECTED")
    assert not post_recorder.called, "an unparseable plan must NOT be forwarded"
