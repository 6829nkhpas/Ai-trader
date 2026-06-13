"""Property-based test for the trade-management verification step (task 13.2).

Feature: trade-management

This module implements design **Property 21: Trade-management verification step
outcome**:

    For ANY defensibility record's management entry (present / absent, available
    True / False, every style in ``trade_manager.MANAGEMENT_STYLE_TAGS``, and the
    explicitly-invalid simulated status), ``_trade_management_step(record)``
    returns EXACTLY ONE step carrying the stable check id ``trade-management``
    and an outcome whose leading token is one of ``pass`` / ``fail`` /
    ``informational`` / ``not-evaluable``, matching the documented mapping:

        available True, active multi-leg style (not "single")  -> pass
        available True, style == "single"                      -> informational
        absent / non-dict / available False                    -> not-evaluable
        simulated status == "invalid"                          -> fail

Validates: Requirements 10.1, 10.2, 10.3, 10.4.

The implementation under test lives in ``stream_events.py``:
  - ``_trade_management_step(record)`` — maps the defensibility ``management``
    entry to a single step under the fixed check id ``trade-management``.
  - ``build_verification_steps(decision)`` — FIND-mode records (no
    ``validator_checks``) route to ``_derive_find_mode_steps`` which appends
    EXACTLY ONE ``_trade_management_step(record)``; this test asserts the
    single-step guarantee through it too.

The real LLM / graph is never invoked. The defensibility ``management`` entry is
built directly in the shape ``graph._management_entry`` produces: a usable label
``{"available": True, "style": <tm-style>, "action": ..., "entry": ...,
"initial_stop": ..., "legs": [...], ...}`` (optionally carrying a simulated
``status``), or it is absent / a non-dict / ``{"available": False, ...}``.

The sys.path / import pattern mirrors
``tests/test_forecast_verification_step_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``stream_events`` /
``trade_manager`` are importable when pytest is run from anywhere.
"""

import os
import re
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py / trade_manager.py
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import trade_manager  # noqa: E402
from stream_events import (  # noqa: E402
    _trade_management_step,
    build_verification_steps,
)

TM_CHECK = "trade-management"

# The four primary outcome tokens the step may ever report (R10.1).
_ALLOWED_TOKENS = {"pass", "fail", "informational", "not-evaluable"}


def _leading_token(outcome: str) -> str:
    """Return the primary outcome token.

    The implementation formats outcomes like ``"pass — managed plan (scale)"``
    or ``"not-evaluable — no management plan"``; the leading token is recovered
    by splitting on whitespace and the em-dash separator and taking the first
    non-empty piece. ``not-evaluable`` survives intact (its only separator is a
    hyphen, not whitespace or an em-dash).
    """
    parts = [p for p in re.split(r"[\s\u2014]+", outcome.strip()) if p]
    return parts[0] if parts else ""


def _expected_token(management) -> str:
    """The documented mapping, mirroring ``_trade_management_step`` precedence.

    Precedence matches the implementation exactly:
      1. absent / non-dict / available falsy        -> not-evaluable (R10.4)
      2. simulated status == "invalid"              -> fail
      3. style == "single"                          -> informational (R10.3)
      4. missing / "unknown" style on an available
         entry (defensive)                          -> not-evaluable
      5. any other active multi-leg style           -> pass (R10.2)
    """
    if not isinstance(management, dict) or not management.get("available"):
        return "not-evaluable"
    if management.get("status") == "invalid":
        return "fail"
    style = management.get("style")
    if style == "single":
        return "informational"
    if not style or style == "unknown":
        return "not-evaluable"
    return "pass"


# ── Strategies ───────────────────────────────────────────────────────────────
_action = st.sampled_from(["BUY", "SELL"])
_price = st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False)
_fraction = st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def _legs(draw):
    """A list of 1-3 leg dicts (target + fraction); contents are outcome-agnostic."""
    n = draw(st.integers(min_value=1, max_value=3))
    return [{"target": draw(_price), "fraction": draw(_fraction)} for _ in range(n)]


@st.composite
def _available_entry(draw):
    """An available management entry across EVERY fixed management style.

    Draws ``style`` from the full ``trade_manager.MANAGEMENT_STYLE_TAGS``
    enumeration (including ``single`` and ``unknown``) plus the degenerate
    missing/empty styles, and optionally stamps a simulated ``status`` (sometimes
    ``"invalid"``) so the fail branch is exercised.
    """
    style = draw(st.sampled_from(list(trade_manager.MANAGEMENT_STYLE_TAGS) + [None, ""]))
    entry = {
        "available": True,
        "style": style,
        "action": draw(_action),
        "entry": draw(_price),
        "initial_stop": draw(_price),
        "legs": draw(_legs()),
        "breakeven": draw(st.one_of(st.none(), st.fixed_dictionaries({"r_multiple": st.just(1.0)}))),
        "trailing": draw(st.one_of(st.none(), st.fixed_dictionaries({"atr_multiple": st.just(1.5)}))),
    }
    status = draw(st.sampled_from([None, "resolved", "open", "invalid"]))
    if status is not None:
        entry["status"] = status
    return entry


# An Unavailable_Marker entry: available False (optionally with a reason).
_unavailable_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    st.one_of(st.none(), st.sampled_from(["HOLD decision", "no usable levels"])),
)

# A non-dict / degenerate management value (drives the not-evaluable path).
_non_dict_entry = st.one_of(
    st.none(),
    st.text(max_size=8),
    st.integers(),
    st.lists(st.integers(), max_size=3),
    st.just({}),                       # dict without ``available`` -> falsy
    st.just({"available": False}),
)

_management_value = st.one_of(
    _available_entry(),
    _unavailable_entry,
    _non_dict_entry,
)

# Optional FIND-mode record fields the sibling checks read. Their presence must
# not change the single trade-management step. Crucially NO ``validator_checks``
# so the record routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_tm_step(steps):
    """Return the single trade-management step, asserting exactly one (R10.1)."""
    tm_steps = [s for s in steps if s.get("check") == TM_CHECK]
    assert len(tm_steps) == 1, (
        f"expected exactly one '{TM_CHECK}' step, got {len(tm_steps)}"
    )
    return tm_steps[0]


# ─────────────────────────────────────────────────────────────────────────────
# Property 21 (task 13.2): Trade-management verification step outcome
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 21: Trade-management verification step outcome
@settings(max_examples=50, deadline=None)
@given(
    management=_management_value,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
    present=st.booleans(),
)
def test_property_21_trade_management_verification_step_outcome(management, extras, action, present):
    """Validates: Requirements 10.1, 10.2, 10.3, 10.4

    For any management-entry shape — absent, a non-dict, an Unavailable_Marker,
    or an available entry across every fixed management style (with or without an
    invalid simulated status) — ``_trade_management_step`` returns exactly one
    step under the stable check id ``trade-management`` whose leading outcome
    token is one of {pass, fail, informational, not-evaluable} and matches the
    documented mapping:

        available True, active multi-leg style (not "single") -> pass   (R10.2)
        available True, style == "single"                     -> informational (R10.3)
        absent / non-dict / available False                   -> not-evaluable (R10.4)
        simulated status == "invalid"                         -> fail
    """
    # Build the FIND-mode defensibility record. ``present`` toggles whether the
    # ``management`` key exists at all, exercising the absent path (R10.4).
    record = dict(extras)
    if present:
        record["management"] = management
    effective = management if present else None

    # ── Direct call: _trade_management_step returns ONE step (R10.1) ─────────
    step = _trade_management_step(record)
    assert step["check"] == TM_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    token = _leading_token(outcome)
    assert token in _ALLOWED_TOKENS, (
        f"outcome leading token {token!r} (from {outcome!r}) not in {_ALLOWED_TOKENS}"
    )

    expected = _expected_token(effective)
    assert token == expected, (
        f"management={effective!r} -> token {token!r} (outcome {outcome!r}), "
        f"expected {expected!r}"
    )

    # ── Via build_verification_steps: still EXACTLY ONE tm step (R10.1) ───────
    decision = {"action": action, "defensibility": record}
    steps = build_verification_steps(decision)
    via_steps = _only_tm_step(steps)
    assert via_steps["check"] == TM_CHECK
    assert _leading_token(via_steps["outcome"]) == expected
