# Feature: earnings-event-risk-gate, Property 13: The tool degrades to an Unavailable_Marker on any source-retrieval or processing failure
"""Property-based test for graceful degradation of the get_event_risk tool to an
Unavailable_Marker (tools.py, task 4.6).

Feature: earnings-event-risk-gate

This Hypothesis property exercises the ``get_event_risk`` tool in ``tools.py``
with the Event_Source retrieval / processing FORCED to fail in every distinct
way the tool can observe a failure. It covers design **Property 13**: the tool
degrades to an Unavailable_Marker on any source-retrieval or processing failure,
and NEVER propagates an exception into the Deep_Quant_Agent loop.

The tool resolves its config (gate ENABLED), reads the process clock for the
reference "now", gathers candidate Scheduled_Event timestamps via
``tools._load_event_candidates(symbol, config)`` (the only I/O in the gate),
selects the nearest upcoming event via ``events.select_next_event`` and
classifies it. To exercise the failure paths we patch
``tools._load_event_candidates`` (a VALID symbol so argument validation passes)
and force, per Hypothesis example, one of the structured source results the
loader can return, plus the loader itself raising:

  (a) ``source_configured=False`` — NEITHER a file nor an API is configured
      (-> "no event source configured", Requirement 1.2);
  (b) ``source_configured=True, retrieval_failed=True`` with a ``failure_reason``
      — a configured source that was missing / unreadable / malformed /
      unreachable / timed out / non-2xx / unparseable (-> retrieval-cause marker,
      Requirement 1.4);
  (c) ``source_configured=True, retrieval_failed=False`` with an EMPTY candidate
      list — a source that read cleanly but has no upcoming event for the symbol
      (-> no-upcoming-event marker, Requirement 1.3);
  (d) ``source_configured=True, retrieval_failed=False`` with candidates that are
      ALL at/before the reference "now" — ``events.select_next_event`` returns
      ``None`` (-> no-upcoming-event marker, Requirement 1.3);
  (e) the loader itself RAISING an exception — a processing error that the
      tool's catch-all must degrade to an Unavailable_Marker (Requirement 5.3).

For every failure mode the result MUST be an Unavailable_Marker — a dict with
``unavailable is True`` and a non-empty ``reason`` string that OMITS
``event_risk`` and ``event_recommendation`` — and the tool must never raise.

The mock helpers follow the same pattern as
``test_session_tool_degradation_properties.py`` /
``test_rs_tool_degradation_properties.py``.
"""

import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / events.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_event_risk  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


_OMITTED_FIELDS = ("event_risk", "event_recommendation")

# A reference well after any candidate we generate below, so "past" candidates
# are unambiguously at/before the process clock the tool reads for "now".
_PAST_MAX_MS = 1_500_000_000_000  # 2017-07-14 — safely before the real clock.


# ── failure-mode strategy ────────────────────────────────────────────────────
# Each example names a distinct way source retrieval / processing can fail.
_failure_modes = st.sampled_from([
    # (a) no source configured -> "no event source configured".
    "no_source_configured",
    # (b) configured source that failed retrieval -> retrieval-cause marker.
    "retrieval_failed",
    # (c) configured source, clean read, no candidates -> no upcoming event.
    "no_candidates",
    # (d) configured source, clean read, ONLY past/at-reference candidates ->
    #     select_next_event returns None -> no upcoming event.
    "only_past_candidates",
    # (e) the loader itself raises -> catch-all degrades (processing error).
    "loader_raises_runtime",
    "loader_raises_value",
    "loader_raises_key",
    "loader_raises_type",
])

_valid_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"])
# Absent/empty/unrecognized horizons are all valid tool inputs (normalized).
_horizons = st.sampled_from(["", "intraday", "multi_session", "swing", "   ", "GARBAGE"])

_failure_reasons = st.sampled_from([
    "calendar file not found: /tmp/events.json",
    "calendar file malformed: expecting value: line 1 column 1",
    "calendar api timeout after 5.0s",
    "calendar api returned non-2xx: 503",
    "calendar api body unparseable",
])

# A non-empty list of strictly-past epoch-ms candidates (all <= real "now").
_past_candidate_lists = st.lists(
    st.integers(min_value=1, max_value=_PAST_MAX_MS).map(float),
    min_size=1,
    max_size=6,
)


def _exc_for(mode):
    """The exception object a 'loader raises' mode should raise."""
    return {
        "loader_raises_runtime": RuntimeError("unexpected boom in source reader"),
        "loader_raises_value": ValueError("bad value while parsing calendar"),
        "loader_raises_key": KeyError("candidates"),
        "loader_raises_type": TypeError("NoneType is not iterable"),
    }[mode]


def _make_loader(mode, failure_reason, past_candidates):
    """Build a ``_load_event_candidates`` replacement implementing ``mode``.

    Returns a ``mock.Mock`` whose ``return_value`` is the structured source
    result for the retrieval-outcome modes, or whose ``side_effect`` raises for
    the processing-error modes.
    """
    if mode == "no_source_configured":
        return mock.Mock(return_value={
            "candidates": [],
            "source_configured": False,
            "retrieval_failed": False,
            "failure_reason": None,
        })
    if mode == "retrieval_failed":
        return mock.Mock(return_value={
            "candidates": [],
            "source_configured": True,
            "retrieval_failed": True,
            "failure_reason": failure_reason,
        })
    if mode == "no_candidates":
        return mock.Mock(return_value={
            "candidates": [],
            "source_configured": True,
            "retrieval_failed": False,
            "failure_reason": None,
        })
    if mode == "only_past_candidates":
        return mock.Mock(return_value={
            "candidates": list(past_candidates),
            "source_configured": True,
            "retrieval_failed": False,
            "failure_reason": None,
        })
    if mode in ("loader_raises_runtime", "loader_raises_value",
                "loader_raises_key", "loader_raises_type"):
        return mock.Mock(side_effect=_exc_for(mode))

    raise AssertionError(f"unhandled failure mode {mode!r}")  # pragma: no cover


def _assert_unavailable_marker(result, mode):
    """Assert ``result`` is a well-formed Unavailable_Marker with no label fields."""
    assert isinstance(result, dict), f"[{mode}] result is not a dict: {result!r}"
    assert result.get("unavailable") is True, (
        f"[{mode}] result is not an Unavailable_Marker (unavailable!=True): {result!r}"
    )
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"[{mode}] Unavailable_Marker carries no non-empty reason: {result!r}"
    )
    # event_risk / event_recommendation MUST be omitted, never fabricated (R5.3).
    for field in _OMITTED_FIELDS:
        assert field not in result, (
            f"[{mode}] Unavailable_Marker fabricated field "
            f"'{field}'={result.get(field)!r}: {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: The tool degrades to an Unavailable_Marker on any source-retrieval
#              or processing failure
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None)
@given(
    symbol=_valid_symbols,
    holding_horizon=_horizons,
    mode=_failure_modes,
    failure_reason=_failure_reasons,
    past_candidates=_past_candidate_lists,
)
def test_property_13_degrades_to_unavailable_marker(
    symbol, holding_horizon, mode, failure_reason, past_candidates
):
    """Feature: earnings-event-risk-gate, Property 13: The tool degrades to an
    Unavailable_Marker on any source-retrieval or processing failure.

    With the gate ENABLED and a VALID symbol (so the gate proceeds and argument
    validation passes), every way source retrieval / processing can fail — no
    source configured, a configured source whose retrieval failed, a clean read
    with no candidates, a clean read with only past/at-reference candidates
    (``select_next_event`` -> ``None``), or the source reader itself raising —
    must make ``get_event_risk`` return an Unavailable_Marker
    (``unavailable: True`` with a non-empty ``reason``) that OMITS event_risk /
    event_recommendation, never raising or propagating an exception into the
    agent loop.

    Validates: Requirements 1.2, 1.3, 1.4, 5.3
    """
    loader = _make_loader(mode, failure_reason, past_candidates)

    # Force the gate ENABLED regardless of the ambient environment (default is
    # enabled) so the tool proceeds to consult the (mocked) source.
    with mock.patch.dict(os.environ, {"EVENT_GATE_ENABLED": "true"}, clear=False), \
            mock.patch.object(tools, "_load_event_candidates", loader):
        # The tool must NOT raise — any escape of an exception fails the property.
        try:
            result = _raw(get_event_risk)(symbol=symbol, holding_horizon=holding_horizon)
        except Exception as exc:  # pragma: no cover - property failure path
            raise AssertionError(
                f"get_event_risk propagated an exception on failure mode "
                f"{mode!r}: {exc!r}"
            )

    _assert_unavailable_marker(result, mode)
