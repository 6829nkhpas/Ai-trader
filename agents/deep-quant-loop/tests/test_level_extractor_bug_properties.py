"""R4 bug-condition exploration test — Best-Current-Read level extraction.

Feature: deep-quant-runtime-hardening (bugfix)

Property 5 (Bug Condition), Python ``graph._parse_levels_from_text`` seam —
"Trustworthy Best-Current-Read key levels":

    When the prose fallback runs over rule/ordinal text such as
    "stop >= 1.5x ATR" or "Target 1: <price>", the extractor must NOT capture
    the ATR *multiplier* (``1.5``) as a stop-loss price, nor the target
    *ordinal* (``1`` in "Target 1") as a take-profit price. A number that is a
    multiplier ("1.5x ATR") or an ordinal label ("Target 1") is not a defensible
    price and must be rejected — the field is omitted rather than filled with a
    spurious value (design Property 5 / 6).

    Validates: Requirements 4.2, 4.3.

*** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***

The unfixed regexes (``graph.py`` ~1481-1483) are:

    _SL_RE  = (?:stop[\\s\\-]?loss|stop|sl)\\b[^0-9\\-]*([0-9]+(?:\\.[0-9]+)?)
    _TP_RE  = (?:take[\\s\\-]?profit|target|tp)\\b[^0-9\\-]*([0-9]+(?:\\.[0-9]+)?)

On "stop >= 1.5x ATR" the ``[^0-9\\-]*`` gap swallows " >= " and ``_SL_RE``
captures ``1.5`` — the ATR multiplier, not a price. On "Target 1: 24300" the
gap swallows the single space and ``_TP_RE`` captures ``1`` — the ordinal, not
the ``24300`` price that follows it. So ``_parse_levels_from_text`` returns
``{"stop_loss": 1.5, "take_profit": 1.0}`` and the UI renders a nonsense
"stop: 1.5 · target: 1" in Best_Current_Read (the R4 misparse defect).

The assertions below encode the CORRECT (fixed) behavior, so they FAIL on the
unfixed regexes. That failure is the informative, expected outcome — it proves
the misparse. DO NOT fix the code here; task 11.1 tightens the regexes and task
11.3 re-runs THIS SAME test to confirm the fix.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import _parse_levels_from_text  # noqa: E402


# ── Documented concrete counterexample (the real Best_Current_Read misparse) ───


def test_concrete_rule_and_ordinal_prose_yields_no_spurious_levels():
    """Concrete counterexample documenting the defect: the exact rule/ordinal
    prose the agent emits on a stand-aside HOLD renders "stop: 1.5 · target: 1".

    EXPECTED FAIL on unfixed code: ``_parse_levels_from_text`` returns
    ``stop_loss == 1.5`` (the ATR multiplier) and ``take_profit == 1`` (the
    "Target 1" ordinal).
    """
    text = (
        "Standing aside — chop, no edge. Rule: stop >= 1.5x ATR below structure. "
        "Target 1: reassess on a clean break above value."
    )

    levels = _parse_levels_from_text(text) or {}

    # 1.5 is the ATR multiplier from "1.5x ATR", not a stop-loss price.
    assert levels.get("stop_loss") != 1.5, (
        "counterexample: 'stop >= 1.5x ATR' captured stop_loss=1.5 — the ATR "
        "multiplier is rendered as a price in Best_Current_Read"
    )
    # 1 is the ordinal from "Target 1", not a take-profit price.
    assert levels.get("take_profit") != 1.0, (
        "counterexample: 'Target 1' captured take_profit=1 — the target ordinal "
        "is rendered as a price in Best_Current_Read"
    )


def test_target_ordinal_with_following_price_captures_the_price_not_ordinal():
    """"Target 1: 24300" must yield 24300 (the price), never 1 (the ordinal).

    EXPECTED FAIL on unfixed code: ``_TP_RE`` captures ``1`` and stops.
    """
    text = "Entry near value. Target 1: 24300 on continuation."

    levels = _parse_levels_from_text(text) or {}

    tp = levels.get("take_profit")
    assert tp != 1.0, "captured the ordinal '1' from 'Target 1' instead of the price"
    if tp is not None:
        assert tp == 24300.0


# ── Property 5 (bug condition): ATR multipliers are never captured as a stop ───

# Sanely-bounded ATR multipliers, as they appear in "stop >= <k>x ATR" prose.
_multiplier = st.sampled_from([1.0, 1.5, 2.0, 2.5, 3.0])
_mult_token = st.sampled_from(["x", "X", "×"])


@settings(max_examples=150)
@given(mult=_multiplier, token=_mult_token)
def test_atr_multiplier_never_captured_as_stop_loss(mult, token):
    """For any "stop >= <mult><x> ATR" rule text, the extractor must NOT return
    the multiplier as ``stop_loss``.

    EXPECTED FAIL on unfixed code: ``_SL_RE`` captures ``mult``.
    """
    mult_str = f"{mult:g}"
    text = f"Standing aside. Rule: stop >= {mult_str}{token} ATR below the low."

    levels = _parse_levels_from_text(text) or {}

    assert levels.get("stop_loss") != mult, (
        f"'{mult_str}{token} ATR' multiplier captured as stop_loss={mult}"
    )


# ── Property 6 (bug condition): target ordinals are never captured as a target ─


@settings(max_examples=150)
@given(ordinal=st.integers(min_value=1, max_value=9))
def test_target_ordinal_never_captured_as_take_profit(ordinal):
    """For any "Target N" ordinal label (no following price), the extractor must
    NOT return the ordinal N as ``take_profit``.

    EXPECTED FAIL on unfixed code: ``_TP_RE`` captures ``N``.
    """
    text = f"No trade. Target {ordinal}: reassess after the open."

    levels = _parse_levels_from_text(text) or {}

    assert levels.get("take_profit") != float(ordinal), (
        f"'Target {ordinal}' ordinal captured as take_profit={ordinal}"
    )
