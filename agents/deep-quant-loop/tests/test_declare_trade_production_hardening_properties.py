"""Production-hardening regression tests for the declare_trade commit path.

Two deployment-only defects made a shipped run behave differently from dev, both
ending in a non-executable ("HOLD-looking") card or — worse — an unvalidated one:

  1. ``tools.declare_trade`` wrapped the POST to the authoritative Rust
     Trade_Validator in a bare ``except`` that only printed, then returned
     "Trade declared successfully". With the tool server unreachable (wrong
     ``RUST_TOOL_SERVER_URL``, container down, network policy) EVERY directional
     trade "passed" with no direction-consistency, R:R or stop-vs-ATR check.
     A transport fault must now REJECT a BUY/SELL (fail closed) while still
     letting a risk-free HOLD commit.

  2. ``graph._decision_from_declare`` read the PRE-COERCION tool args. Pydantic
     coerces ``"24500.5"`` -> ``24500.5`` for the real invocation (so the Rust
     validator passes), but the decision dict kept the string, and
     ``stream_events._is_finite_num`` rejects strings — so ``execution_levels``
     was silently dropped from a genuinely validated trade.

Validates: the declared decision record always agrees with what the
authoritative validator actually saw, and an unreachable validator never yields
a committed directional trade.
"""

import math

import pytest
from hypothesis import given, settings, strategies as st

import graph
import tools
import stream_events


# ── Defect 1: fail closed when the validator is unreachable ──────────────────


def _call_declare(monkeypatch, action, raise_exc=True, **kwargs):
    """Invoke the declare_trade tool with the Rust POST forced to fail."""

    def _boom(*_a, **_kw):
        raise ConnectionError("connection refused")

    if raise_exc:
        monkeypatch.setattr(tools.httpx, "post", _boom)

    payload = {
        "action": action,
        "conviction_score": 70,
        "setup_validation": "Reclaim above VWAP.",
        "execution_plan": "Scale out at R1.",
        "entry": 24112.85,
        "stop_loss": 24078.0,
        "take_profit": 24175.0,
        "atr_14": 20.0,
    }
    payload.update(kwargs)
    # `.func` reaches the undecorated callable so no LangChain plumbing is needed.
    return tools.declare_trade.func(**payload)


@pytest.mark.parametrize("action", ["BUY", "SELL", "buy", "sell", " Buy "])
def test_unreachable_validator_rejects_directional_trade(monkeypatch, action):
    result = _call_declare(monkeypatch, action)
    assert "TRADE_REJECTED" in result
    # The agent must not be told to retry with different numbers.
    assert "infrastructure fault" in result
    assert "declared successfully" not in result


@pytest.mark.parametrize("action", ["HOLD", "hold"])
def test_unreachable_validator_still_commits_a_hold(monkeypatch, action):
    # A HOLD risks no capital; an outage must not also suppress a stand-aside.
    result = _call_declare(monkeypatch, action, entry=None, stop_loss=None, take_profit=None)
    assert "TRADE_REJECTED" not in result
    assert "declared successfully" in result


def test_rejection_marker_is_the_form_the_graph_detects(monkeypatch):
    """The fail-closed marker must be recognized by _declare_was_rejected."""

    class _ToolMsg:
        name = "declare_trade"

        def __init__(self, content):
            self.content = content

    result = _call_declare(monkeypatch, "BUY")
    monkeypatch.setattr(graph, "_is_tool_message", lambda m: isinstance(m, _ToolMsg))
    assert graph._declare_was_rejected([_ToolMsg(result)]) is True


def test_is_directional_action_totality():
    assert tools._is_directional_action("BUY") is True
    assert tools._is_directional_action("sell") is True
    assert tools._is_directional_action(" Buy ") is True
    for bad in ("HOLD", "", "  ", None, 1, 0, True, [], {}, object()):
        assert tools._is_directional_action(bad) is False


# ── Defect 2: price coercion so execution_levels survives ────────────────────


def test_coerce_price_accepts_numeric_strings():
    assert graph._coerce_price("24500.5") == 24500.5
    assert graph._coerce_price(" 24500.5 ") == 24500.5
    assert graph._coerce_price(24500.5) == 24500.5
    assert graph._coerce_price(24500) == 24500.0


def test_coerce_price_rejects_junk_rather_than_fabricating():
    for bad in (None, True, False, "", "  ", "abc", "1,2", [], {}, object()):
        assert graph._coerce_price(bad) is None
    for bad in ("nan", "inf", "-inf", float("nan"), float("inf")):
        assert graph._coerce_price(bad) is None


@given(
    entry=st.floats(min_value=100, max_value=50_000, allow_nan=False, allow_infinity=False),
    as_string=st.booleans(),
    action=st.sampled_from(["BUY", "SELL", "buy", "sell", "LONG", "short"]),
)
@settings(max_examples=60, deadline=None)
def test_string_or_float_levels_both_yield_execution_levels(entry, as_string, action):
    """A validated directional trade always reaches the UI carrying levels.

    This is the end-to-end property the bug violated: the same declaration must
    produce execution_levels whether the model emitted numbers or numeric
    strings, since pydantic coerces the latter before the validator sees them.
    """
    stop = entry * 0.99
    target = entry * 1.02
    fmt = (lambda v: str(v)) if as_string else (lambda v: v)

    ok_calls = [{
        "name": "declare_trade",
        "args": {
            "action": action,
            "conviction_score": 72,
            "setup_validation": "Reclaim.",
            "execution_plan": "Scale out.",
            "entry": fmt(entry),
            "stop_loss": fmt(stop),
            "take_profit": fmt(target),
            "atr_14": fmt(5.0),
        },
    }]

    decision = graph._decision_from_declare(ok_calls)
    assert decision is not None
    # Action is normalized, so LONG/short and casing cannot defeat the gate.
    assert decision["action"] in ("BUY", "SELL")
    for key in ("entry", "stop_loss", "take_profit", "atr_14"):
        assert isinstance(decision[key], float)
        assert math.isfinite(decision[key])

    payload = stream_events.build_decision_event(decision)
    data = payload.get("data", payload)
    levels = data.get("execution_levels")
    assert levels is not None, "validated directional trade lost its execution_levels"
    assert levels["entry"] == pytest.approx(float(entry))
    assert levels["stop_loss"] == pytest.approx(float(stop))
    assert levels["take_profit"] == pytest.approx(float(target))


def test_hold_still_carries_no_levels():
    decision = graph._decision_from_declare([{
        "name": "declare_trade",
        "args": {"action": "HOLD", "conviction_score": 10, "setup_validation": "Chop."},
    }])
    assert decision["action"] == "HOLD"
    payload = stream_events.build_decision_event(decision)
    data = payload.get("data", payload)
    assert "execution_levels" not in data


def test_unusable_levels_degrade_to_absent_not_fabricated():
    decision = graph._decision_from_declare([{
        "name": "declare_trade",
        "args": {
            "action": "BUY",
            "conviction_score": 50,
            "entry": "not-a-price",
            "stop_loss": None,
            "take_profit": "abc",
        },
    }])
    assert decision["entry"] is None
    assert decision["take_profit"] is None
    payload = stream_events.build_decision_event(decision)
    data = payload.get("data", payload)
    assert "execution_levels" not in data
