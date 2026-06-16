"""Property-based test for the debate entry being absent on non-DEBATE runs.

Feature: multi-agent-debate

This module implements design **Property 18: No debate entry on non-DEBATE
runs**:

    For ANY non-DEBATE run (the resolved ``mode`` is not ``"DEBATE"``),
    ``build_defensibility_record`` adds NO ``debate`` key to the assembled
    record — even when the committed decision defensively carries a private
    ``_debate`` carrier that should never be present off the DEBATE path.

Validates: Requirements 7.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder. It normalizes ``mode = (mode or "FIND").upper()``
    and attaches a ``debate`` key only when ``_debate_entry`` returns non-None.
  - ``_debate_entry(decision, mode, action)`` — returns ``None`` whenever
    ``mode != "DEBATE"`` (or no ``_debate`` carrier is threaded), so no ``debate``
    key is added.

Because the builder upper-cases the mode, a raw mode whose UPPERCASE equals
``"DEBATE"`` (e.g. lowercase ``"debate"``) WOULD count as a DEBATE run. The
strategy therefore generates arbitrary modes — ``None``, the known non-DEBATE
modes, edge strings, and free text — filtered so their normalized form is NOT
``"DEBATE"``. A positive control confirms the test is meaningful: a true DEBATE
run carrying a ``_debate`` carrier DOES add the key.

The real LLM / tool server are never invoked. A minimal ToolMessage-like stub
(``.type == "tool"``, ``.name``, ``.content``) carries optional JSON tool
results so the builder reads message history exactly as it would live.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up). Mirrors the
# sibling defensibility-record test modules.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import build_defensibility_record  # noqa: E402

_FINITE = {"allow_nan": False, "allow_infinity": False}


class StubToolMessage:
    """Stand-in for a LangChain ToolMessage tool result.

    ``build_defensibility_record`` reads only ``.type`` (must be ``"tool"``),
    ``.name`` (the originating Analysis_Tool) and ``.content`` (serialized JSON
    tool result), so this minimal stub is sufficient.
    """

    def __init__(self, name, payload):
        self.type = "tool"
        self.name = name
        self.content = json.dumps(payload)


# A few realistic tool-result messages so the builder has something to read on
# some examples (and an empty list on others) — none of which is debate data.
_tool_messages = st.lists(
    st.one_of(
        st.fixed_dictionaries(
            {"name": st.just("get_multi_tf_trend"),
             "payload": st.fixed_dictionaries({"1D": st.sampled_from(["Bullish", "Bearish", "Neutral"])})}
        ),
        st.fixed_dictionaries(
            {"name": st.just("get_consensus_report"),
             "payload": st.fixed_dictionaries({"atr_14": st.floats(min_value=0.01, max_value=50.0, **_FINITE)})}
        ),
    ),
    max_size=3,
).map(lambda specs: [StubToolMessage(s["name"], s["payload"]) for s in specs])


# Modes whose normalized form ((mode or "FIND").upper()) is NOT "DEBATE". Spans
# None, the known non-DEBATE modes, lowercase/whitespace edge cases, and free
# text; the filter mirrors the builder's exact normalization so any generated
# value that WOULD normalize to "DEBATE" (e.g. "debate", "Debate") is excluded.
_non_debate_modes = st.one_of(
    st.none(),
    st.sampled_from(
        ["FIND", "VERIFY", "QA", "find", "verify", "qa", "", "   ",
         " DEBATE", "DEBATE ", "DEBATES", "DEBAT", "FOO", "research"]
    ),
    st.text(max_size=12),
).filter(lambda m: (m or "FIND").upper() != "DEBATE")


def _debate_carrier(draw):
    """A realistic ``_debate`` carrier as the Judge would thread it (used here
    defensively — it must NOT leak a debate key on a non-DEBATE run)."""
    return {
        "bull_stance": {"available": True, "strength": draw(st.integers(min_value=0, max_value=10))},
        "bear_stance": {"available": True, "strength": draw(st.integers(min_value=0, max_value=10))},
        "consensus": draw(st.sampled_from(["strong", "lean", "contested", "weak"])),
        "conviction": draw(st.integers(min_value=0, max_value=10)),
    }


@st.composite
def _decision(draw, force_debate_carrier=False):
    """An arbitrary committed decision, optionally carrying ``_debate``."""
    dec = {}
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD", None]))
    if action is not None:
        dec["action"] = action
    if draw(st.booleans()):
        dec["entry"] = draw(st.floats(min_value=1.0, max_value=1000.0, **_FINITE))
        dec["stop_loss"] = draw(st.floats(min_value=0.5, max_value=999.0, **_FINITE))
        dec["take_profit"] = draw(st.floats(min_value=1.0, max_value=2000.0, **_FINITE))
    # Defensively attach a debate carrier on some non-DEBATE examples too: even
    # then, the record must carry no debate key (R7.3).
    if force_debate_carrier or draw(st.booleans()):
        dec["_debate"] = _debate_carrier(draw)
    return dec


# ─────────────────────────────────────────────────────────────────────────────
# Property 18: No debate entry on non-DEBATE runs
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 18: No debate entry on non-DEBATE runs
@settings(max_examples=100, deadline=None)
@given(
    data=st.data(),
    mode=_non_debate_modes,
    messages=_tool_messages,
)
def test_property_18_no_debate_entry_on_non_debate_runs(data, mode, messages):
    """Validates: Requirements 7.3

    For any non-DEBATE mode and any decision (including one that defensively
    carries a ``_debate`` carrier), ``build_defensibility_record`` adds NO
    ``debate`` key to the assembled record.
    """
    decision = data.draw(_decision())

    record = build_defensibility_record(messages, decision, mode=mode)

    assert (mode or "FIND").upper() != "DEBATE"  # generator invariant
    assert "debate" not in record, (
        f"non-DEBATE run (mode={mode!r}) must not add a debate key; "
        f"record keys: {sorted(record.keys())}"
    )


# Feature: multi-agent-debate, Property 18 (positive control): DEBATE adds the key
@settings(max_examples=100, deadline=None)
@given(data=st.data(), messages=_tool_messages)
def test_property_18_positive_control_debate_run_adds_key(data, messages):
    """Sanity check that the property is meaningful: a true DEBATE run carrying a
    ``_debate`` carrier DOES add the ``debate`` key (so the negative assertion
    above is not vacuously true)."""
    decision = data.draw(_decision(force_debate_carrier=True))

    record = build_defensibility_record(messages, decision, mode="DEBATE")

    assert "debate" in record, (
        "a DEBATE run carrying a _debate carrier must add the debate key"
    )
