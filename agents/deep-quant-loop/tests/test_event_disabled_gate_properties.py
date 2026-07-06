# Feature: earnings-event-risk-gate, Property 14: A disabled gate returns a gate-disabled marker and performs no retrieval
"""Property-based test for the disabled-gate marker and no source retrieval.

Feature: earnings-event-risk-gate

This module implements design **Property 14: A disabled gate returns a
gate-disabled marker and performs no retrieval**:

    When the master enable flag is DISABLED (``EVENT_GATE_ENABLED`` set to a
    recognized disabling spelling — ``0`` / ``false`` / ``no``, any case), the
    ``get_event_risk`` tool short-circuits: it returns a gate-disabled
    Unavailable_Marker (``{"unavailable": true, "reason": ...}`` whose reason
    indicates the gate is disabled), omits ``event_risk`` /
    ``event_recommendation`` entirely, never raises, and performs NO source
    retrieval — ``tools._load_event_candidates`` is never called.

Validates: Requirements 5.4, 11.5.

The strategies generate arbitrary symbols and holding horizons alongside every
recognized disabling spelling of ``EVENT_GATE_ENABLED`` (with case variants).
The environment is isolated and restored exactly on exit so Hypothesis re-runs
never leak state. The sys.path / import pattern and the ``os.environ`` isolation
context mirror ``tests/test_event_config_deterministic_properties.py``.
"""

import os
import sys
from contextlib import contextmanager
from unittest.mock import Mock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py / events.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
import tools  # noqa: E402
from tools import get_event_risk  # noqa: E402

# Every EVENT_* env var the resolver reads. We clear all of them inside the
# isolation context so only the value under test influences the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_EVENT_ENV_VARS = (
    events.ENV_EVENT_GATE_ENABLED,
    events.ENV_EVENT_MARKET_TIMEZONE,
    events.ENV_EVENT_DEFAULT_HOLDING_HORIZON,
    events.ENV_EVENT_IMMINENT_WINDOW_DAYS,
    events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS,
    events.ENV_EVENT_SOURCE_TIMEOUT_S,
    events.ENV_EVENT_CALENDAR_API_URL,
    events.ENV_EVENT_CALENDAR_FILE,
)


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


@contextmanager
def _event_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every EVENT_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_EVENT_ENV_VARS}
    try:
        for name in _ALL_EVENT_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# ── Value strategies ──────────────────────────────────────────────────────────

# Every recognized DISABLING spelling of the master enable flag, with case
# variants. _resolve_bool lower-cases the value, so any case of 0/false/no maps
# to a disabled gate.
_disabling_flag = st.sampled_from(
    ["0", "false", "no", "False", "FALSE", "No", "NO", "nO", "fAlSe"]
)

# Arbitrary symbols: realistic tickers plus arbitrary text (the disabled gate
# must short-circuit BEFORE symbol validation, so even blank/odd symbols yield
# the gate-disabled marker and never trigger retrieval).
_symbol = st.one_of(
    st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]),
    st.text(max_size=12),
)

# Arbitrary holding horizons: recognized values plus arbitrary text / empty.
_horizon = st.one_of(
    st.sampled_from(["intraday", "multi_session", "", "swing", "unknown"]),
    st.text(max_size=12),
)


@settings(max_examples=200, deadline=None)
@given(flag=_disabling_flag, symbol=_symbol, holding_horizon=_horizon)
def test_disabled_gate_returns_marker_and_skips_retrieval(flag, symbol, holding_horizon):
    """A disabled gate yields a gate-disabled marker and never retrieves.

    Validates: Requirements 5.4, 11.5.
    """
    with _event_env({events.ENV_EVENT_GATE_ENABLED: flag}):
        # Sanity: the resolver reads the disabled flag from the environment.
        assert events.resolve_event_config().enabled is False

        # Patch the source reader so ANY retrieval attempt is observable. The
        # disabled gate must short-circuit before this is ever consulted.
        with patch.object(tools, "_load_event_candidates", Mock()) as mock_loader:
            result = _raw(get_event_risk)(symbol, holding_horizon)

            # No source retrieval whatsoever (Requirement 11.5).
            mock_loader.assert_not_called()

    # Honest gate-disabled Unavailable_Marker (Requirement 5.4).
    assert isinstance(result, dict)
    assert result.get("unavailable") is True

    # The reason indicates the gate is disabled.
    reason = result.get("reason")
    assert isinstance(reason, str) and reason
    assert "disabl" in reason.lower()

    # A missing input, never a fabricated label: no risk / recommendation.
    assert "event_risk" not in result
    assert "event_recommendation" not in result
