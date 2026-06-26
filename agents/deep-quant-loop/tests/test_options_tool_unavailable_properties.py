"""Property-based test that an unavailable `get_options_analytics` result omits
the bias fields (tools.py, task 4.7).

Feature: options-agent-integration

This module implements design **Property 10: Unavailable results omit the bias
fields**:

    Whenever the F2 Options_Analytics_Engine cannot produce a usable
    Options_Analytics_Result — it returns an Unavailable_Marker
    (``{"unavailable": True, "reason": ...}``), returns a non-dict, OR raises —
    the ``get_options_analytics`` tool returns an HONEST Unavailable_Marker that
    OMITS ``options_bias_state`` and ``alignment`` entirely (never a fabricated
    bias), carries ``unavailable: True`` and a non-empty ``reason``, and records
    the resolved chain context (``symbol`` / ``underlying`` / ``chain_context``).
    The tool NEVER raises.

Validates: Requirements 3.2, 3.4.

The tool delegates ALL option-chain / spot / future I/O to
``options.compute_options_analytics`` (the F2 engine), which itself never
raises. To exercise the tool's unavailable gate (Requirements 3.1, 3.2) and its
defensive catch-all (Requirement 3.4) without a live QuestDB, we monkeypatch
``tools.options.compute_options_analytics`` per Hypothesis example to either:

  (a) return an arbitrary Unavailable_Marker (``{"unavailable": True,
      "reason": ...}``, optionally with extra keys) — exercising the gate; or
  (b) return a non-dict (``None`` / a list / a bare string / an int) — also
      caught by the gate (``not isinstance(analytics, dict)``); or
  (c) raise an arbitrary exception — exercising the defensive catch-all.

For every case the tool result MUST be an honest Unavailable_Marker that omits
the two bias state fields, carries ``unavailable: True`` and a ``reason``, and
records ``symbol`` / ``underlying`` / ``chain_context``; and the tool must never
propagate an exception.

The sys.path / ``_raw`` / restore-in-``finally`` patterns mirror the sibling
``test_rs_*`` / ``test_options_*`` property modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / options.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_options_analytics, INDEX_UNDERLYINGS  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


# The two categorical bias fields an Unavailable_Marker must NEVER carry
# (Requirements 3.2): they are omitted, never fabricated.
_BIAS_FIELDS = ("options_bias_state", "alignment")


# A mix of index underlyings (own-chain) and non-index symbols (broad-market),
# so chain resolution + labelling runs both branches.
_symbols = st.sampled_from([
    "NIFTY 50", "NIFTY", "BANKNIFTY",          # index underlyings -> own-chain
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN",  # non-index -> broad-market
])

_expiries = st.sampled_from(["", "  ", "2024-01-25", "weekly", "2024-12-26"])

_directions = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "  ", "weird"]),
)

# Distinct ways the F2 engine can fail to produce a usable analytics result.
_failure_modes = st.sampled_from([
    # (a) honest Unavailable_Markers of varying shape.
    "marker_plain",
    "marker_with_extra_keys",
    "marker_empty_reason",
    "marker_no_reason",
    # (b) non-dict returns — also caught by the gate.
    "non_dict_none",
    "non_dict_list",
    "non_dict_string",
    "non_dict_int",
    # (c) the engine raises -> defensive catch-all.
    "raises_runtime",
    "raises_value",
    "raises_key",
    "raises_type",
])

_reasons = st.sampled_from([
    "no chain snapshot",
    "outside market hours",
    "unsubscribed underlying",
    "spot unavailable",
    "no expiry matched",
])


def _make_fake_compute(mode, reason):
    """Build a ``compute_options_analytics`` stand-in implementing failure ``mode``."""
    def _fake(underlying, expiry):
        if mode == "marker_plain":
            return {"unavailable": True, "reason": reason}
        if mode == "marker_with_extra_keys":
            return {
                "unavailable": True,
                "reason": reason,
                "underlying": underlying,
                "expiry": expiry,
                "spot": 123.45,
                "pcr_oi": None,
            }
        if mode == "marker_empty_reason":
            return {"unavailable": True, "reason": ""}
        if mode == "marker_no_reason":
            return {"unavailable": True}
        if mode == "non_dict_none":
            return None
        if mode == "non_dict_list":
            return [{"unavailable": True, "reason": reason}]
        if mode == "non_dict_string":
            return "totally not a dict"
        if mode == "non_dict_int":
            return 0
        if mode == "raises_runtime":
            raise RuntimeError("engine boom")
        if mode == "raises_value":
            raise ValueError("bad value in engine")
        if mode == "raises_key":
            raise KeyError("missing key")
        if mode == "raises_type":
            raise TypeError("type error in engine")
        raise AssertionError(f"unhandled failure mode {mode!r}")  # pragma: no cover

    return _fake


# ─────────────────────────────────────────────────────────────────────────────
# Feature: options-agent-integration, Property 10: Unavailable results omit the bias fields
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(
    symbol=_symbols,
    expiry=_expiries,
    proposed_direction=_directions,
    mode=_failure_modes,
    reason=_reasons,
)
def test_property_10_unavailable_results_omit_bias_fields(
    symbol, expiry, proposed_direction, mode, reason
):
    """Feature: options-agent-integration, Property 10: Unavailable results omit
    the bias fields.

    For every way the F2 engine can fail to produce a usable
    Options_Analytics_Result — returning an Unavailable_Marker, returning a
    non-dict, or raising — ``get_options_analytics`` returns an honest
    Unavailable_Marker that OMITS ``options_bias_state`` and ``alignment``,
    carries ``unavailable: True`` and a non-empty ``reason``, and records the
    resolved ``symbol`` / ``underlying`` / ``chain_context``. The tool never
    raises.

    Validates: Requirements 3.2, 3.4
    """
    original_compute = tools.options.compute_options_analytics
    tools.options.compute_options_analytics = _make_fake_compute(mode, reason)
    try:
        # The tool must NOT raise — any escape of an exception fails the property.
        try:
            result = _raw(get_options_analytics)(
                symbol=symbol,
                expiry=expiry,
                proposed_direction=proposed_direction or "",
            )
        except Exception as exc:  # pragma: no cover - property failure path
            raise AssertionError(
                f"get_options_analytics propagated an exception on failure mode "
                f"{mode!r}: {exc!r}"
            )
    finally:
        tools.options.compute_options_analytics = original_compute

    # Always a dict.
    assert isinstance(result, dict), f"[{mode}] result is not a dict: {result!r}"

    # It is an honest Unavailable_Marker.
    assert result.get("unavailable") is True, (
        f"[{mode}] result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )

    # It carries a non-empty reason — the tool supplies a fallback reason even
    # when the engine's marker omitted/blanked one.
    reason_out = result.get("reason")
    assert isinstance(reason_out, str) and reason_out.strip(), (
        f"[{mode}] Unavailable_Marker carries no non-empty reason: {result!r}"
    )

    # The core assertion (Requirement 3.2): the two bias state fields MUST be
    # OMITTED entirely — never a fabricated bias.
    for field in _BIAS_FIELDS:
        assert field not in result, (
            f"[{mode}] Unavailable_Marker fabricated bias field "
            f"'{field}'={result.get(field)!r} (must be omitted): {result!r}"
        )

    # The marker records the chain context it resolved before the failure.
    assert result.get("symbol") == symbol, (
        f"[{mode}] marker did not echo the original symbol: {result!r}"
    )

    expected_context = (
        "own-chain" if symbol.strip().upper() in INDEX_UNDERLYINGS else "broad-market"
    )
    assert result.get("chain_context") == expected_context, (
        f"[{mode}] marker chain_context {result.get('chain_context')!r} != "
        f"expected {expected_context!r}: {result!r}"
    )

    # The resolved underlying is recorded (an index uses its own chain; a
    # non-index symbol resolves to a benchmark index) — never None on a
    # gate-driven unavailable, and present in every case.
    assert "underlying" in result, (
        f"[{mode}] marker omitted the resolved underlying: {result!r}"
    )
    assert isinstance(result.get("underlying"), str) and result["underlying"].strip(), (
        f"[{mode}] marker carries no resolved underlying string: {result!r}"
    )
