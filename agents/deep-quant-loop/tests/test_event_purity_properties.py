# Feature: earnings-event-risk-gate, Property 2: Classifier functions are pure (no input mutation, no I/O)
"""Property-based test for Event_Classifier purity (events.py, task 2.4).

Feature: earnings-event-risk-gate

This module implements design **Property 2: Classifier functions are pure (no
input mutation, no I/O)**:

    Every pure ``Event_Classifier`` function — ``normalize_holding_horizon``,
    ``select_next_event``, ``compute_days_until_event``, ``classify_event_risk``,
    ``derive_event_recommendation`` and the top-level ``assess_event_risk`` —
    produces NO observable change to its inputs and performs NO I/O.

    * No input mutation (Requirement 2.9): after a call, every mutable argument
      (in particular the ``candidate_ms`` list handed to ``select_next_event``)
      must remain deep-equal to a snapshot taken before the call, and the frozen
      ``EventConfig`` must remain equal to its pre-call snapshot.
    * No I/O (Requirement 2.1): the classifier reads no network socket, opens no
      file, and never touches the host wall clock (``datetime.now`` /
      ``time.time``). All such I/O lives in the ``get_event_risk`` tool, never in
      these pure helpers. Reading the process clock for the reference "now" and
      reading the configured Event_Source both happen outside this module.

Validates: Requirements 2.1, 2.9.

The generators produce a deliberately broad, adversarial mix of inputs —
in-range and out-of-range epoch-millisecond timestamps, non-finite / non-numeric
/ ``None`` / boolean / string candidates, empty and long candidate lists, and
recognized / unrecognized / non-string Holding_Horizon values — so the purity
guarantee is stressed across every branch (the future-selection path, the
invalid-timestamp path, the exclusion path, and the ordinary classification
path) where a careless implementation might sort-in-place, normalize, or cache
against its inputs.

The sys.path / import pattern mirrors the sibling ``test_event_*_properties.py``
modules; the input-immutability + no-I/O structure mirrors
``test_forecaster_purity_properties.py`` and ``test_of_purity_properties.py``.
"""

import builtins
import copy
import os
import socket
import sys
import time
from datetime import datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
from events import (  # noqa: E402
    EventConfig,
    assess_event_risk,
    classify_event_risk,
    compute_days_until_event,
    derive_event_recommendation,
    normalize_holding_horizon,
    resolve_event_config,
    select_next_event,
)


# ─────────────────────────────────────────────────────────────────────────────
# Input strategies: an adversarial mix so purity is exercised on every branch.
# ─────────────────────────────────────────────────────────────────────────────

# Epoch-millisecond values: in-range (1970..2100), floats, extreme integers, and
# the invalid spellings (None / non-finite / non-numeric / bool) the helpers must
# tolerate without mutating anything or raising.
_EPOCH_MS = st.one_of(
    st.integers(min_value=0, max_value=4_102_444_800_000),  # ~1970..2100
    st.floats(min_value=0.0, max_value=4.102e12, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-(10**18), max_value=10**18),      # out-of-range extremes
    st.sampled_from(
        [None, float("nan"), float("inf"), float("-inf"), "x", "", True, False, []]
    ),
)

# A single candidate event timestamp (same broad mix; lists of these are the
# primary mutable input under test).
_CANDIDATE = st.one_of(
    _EPOCH_MS,
    st.none(),
    st.text(max_size=6),
    st.booleans(),
)

_CANDIDATES = st.lists(_CANDIDATE, min_size=0, max_size=25)

# Holding_Horizon: recognized, unrecognized, empty, and non-string values.
_HORIZON = st.one_of(
    st.none(),
    st.sampled_from(
        ["intraday", "multi_session", "swing", "positional", "", "INTRADAY", "scalp"]
    ),
    st.integers(min_value=-5, max_value=5),
    st.booleans(),
)

# Whole-day counts fed to classify_event_risk (valid non-negative, negative, and
# invalid spellings).
_DAYS = st.one_of(
    st.integers(min_value=-50, max_value=400),
    st.floats(min_value=-10.0, max_value=400.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([None, float("nan"), float("inf"), "3", True]),
)

# Event_Risk labels handed to derive_event_recommendation (valid + garbage).
_EVENT_RISK = st.sampled_from(
    ["clear", "imminent", "through_event", "unknown", "", "CLEAR", "bogus"]
)

# Optional context strings.
_OPT_STR = st.one_of(st.none(), st.text(max_size=8))


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Classifier functions are pure (no input mutation, no I/O)
#   Part A — no input mutation, across many generated inputs.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 2: Classifier functions are pure (no input mutation, no I/O)
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    candidates=_CANDIDATES,
    reference_ms=_EPOCH_MS,
    event_ms=_EPOCH_MS,
    holding_horizon=_HORIZON,
    days_until_event=_DAYS,
    event_risk=_EVENT_RISK,
    symbol=_OPT_STR,
    event_date=_OPT_STR,
)
def test_property_2_classifier_functions_do_not_mutate_inputs(
    candidates,
    reference_ms,
    event_ms,
    holding_horizon,
    days_until_event,
    event_risk,
    symbol,
    event_date,
):
    """Validates: Requirements 2.9

    Every pure Event_Classifier function leaves its inputs deep-equal to their
    pre-call snapshots. The ``candidate_ms`` list (and its elements) is
    snapshotted with a deep copy before the calls and asserted deep-equal
    afterward; the frozen ``EventConfig`` is compared by equality. No helper may
    sort-in-place, normalize, or otherwise observably change any argument.
    """
    config = resolve_event_config()
    assert isinstance(config, EventConfig)
    config_snapshot = config  # frozen dataclass -> compare by equality

    candidates_snapshot = copy.deepcopy(candidates)

    # Exercise every pure helper on the same inputs. None may mutate an argument.
    normalize_holding_horizon(holding_horizon, config)
    select_next_event(candidates, reference_ms, config)
    compute_days_until_event(reference_ms, event_ms, config)
    classify_event_risk(days_until_event, holding_horizon, config)
    derive_event_recommendation(event_risk, holding_horizon)

    # The top-level entry point, across both call shapes.
    assess_event_risk(
        reference_ms,
        event_ms,
        holding_horizon,
        config,
        symbol=symbol,
        event_date=event_date,
    )
    assess_event_risk(reference_ms, event_ms, holding_horizon, config)

    assert candidates == candidates_snapshot, (
        "classifier mutated its candidate list input: "
        f"{candidates!r} != {candidates_snapshot!r}"
    )
    assert config == config_snapshot, "classifier mutated its config input"


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Classifier functions are pure (no input mutation, no I/O)
#   Part B — no I/O: no host clock, no network socket, no file open.
# ─────────────────────────────────────────────────────────────────────────────


class _NoClockDatetime:
    """Stand-in for ``events.datetime`` that forwards the deterministic
    ``fromtimestamp`` (needed for date math on the *provided* timestamps) but
    fails loudly if the wall clock is read via ``now`` / ``today`` / ``utcnow``.
    """

    @staticmethod
    def fromtimestamp(*args, **kwargs):
        return datetime.fromtimestamp(*args, **kwargs)

    @staticmethod
    def now(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("classifier read the host clock via datetime.now()")

    @staticmethod
    def utcnow(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("classifier read the host clock via datetime.utcnow()")

    @staticmethod
    def today(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("classifier read the host clock via datetime.today()")


def _run_all_pure_helpers(config):
    """Invoke every pure helper once with representative valid inputs."""
    reference_ms = 1_700_000_000_000  # 2023-11-14, well within range
    event_ms = reference_ms + 3 * 86_400_000  # three days later
    candidates = [
        reference_ms + 86_400_000,
        reference_ms + 5 * 86_400_000,
        reference_ms - 86_400_000,  # a past candidate (must be discarded)
    ]

    normalize_holding_horizon("multi_session", config)
    select_next_event(candidates, reference_ms, config)
    compute_days_until_event(reference_ms, event_ms, config)
    classify_event_risk(3, "multi_session", config)
    derive_event_recommendation("through_event", "intraday")
    assess_event_risk(
        reference_ms, event_ms, "multi_session", config,
        symbol="RELIANCE", event_date="2023-11-17",
    )


# Feature: earnings-event-risk-gate, Property 2: Classifier functions are pure (no input mutation, no I/O)
def test_property_2_classifier_performs_no_io():
    """Validates: Requirements 2.1

    The pure classifier reads no host wall clock (``datetime.now`` /
    ``time.time``), opens no network socket, and opens no file. We install
    spies that fail if any of those I/O primitives is touched while the helpers
    run, then invoke every helper on representative valid inputs.

    ``zoneinfo`` timezone data is pre-warmed before the file-open spy is armed
    (the resolved market timezone is loaded once and cached), so the only file
    activity that could reach the spy would be genuine, undesired classifier
    I/O — keeping the assertion robust and non-flaky.
    """
    config = resolve_event_config()

    # Pre-warm the zoneinfo cache for the resolved timezone so timezone-data
    # file reads (a one-time zoneinfo concern, not a classifier concern) cannot
    # trip the open() spy below.
    _run_all_pure_helpers(config)

    real_time = time.time
    real_socket = socket.socket
    real_open = builtins.open
    opened_paths = []

    def _no_time(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("classifier read the host clock via time.time()")

    def _no_socket(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("classifier opened a network socket")

    def _spy_open(*args, **kwargs):
        opened_paths.append(args[0] if args else kwargs.get("file"))
        return real_open(*args, **kwargs)

    events.datetime = _NoClockDatetime
    time.time = _no_time
    socket.socket = _no_socket
    builtins.open = _spy_open
    try:
        _run_all_pure_helpers(config)
    finally:
        events.datetime = datetime
        time.time = real_time
        socket.socket = real_socket
        builtins.open = real_open

    assert opened_paths == [], (
        f"classifier opened files during pure classification: {opened_paths!r}"
    )


# Feature: earnings-event-risk-gate, Property 2: Classifier functions are pure (no input mutation, no I/O)
def test_events_module_imports_no_network_client():
    """Validates: Requirements 2.1

    ``events`` is pure Python: it imports no HTTP / network client, so there is
    no client object through which the classifier could reach the network. All
    Event_Source I/O lives in the ``get_event_risk`` tool, never in this module.
    """
    for forbidden in ("httpx", "requests", "urllib3", "aiohttp"):
        assert not hasattr(events, forbidden), (
            f"events must not import a network client ({forbidden})"
        )
    # No module-level attribute should reference an HTTP client module either.
    for name in dir(events):
        attr = getattr(events, name)
        mod = getattr(attr, "__module__", "") or ""
        assert not str(mod).startswith(("httpx", "requests", "aiohttp", "urllib3")), (
            f"events.{name} pulls in a network client: {mod}"
        )
